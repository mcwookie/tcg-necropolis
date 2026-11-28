"""Deck builder web pages, mounted under /decks.

Pages are server-rendered with Jinja; HTMX swaps in fragments as you work:

- the filter form re-fetches the card list (GET /decks/{id}/cards)
- +/- buttons POST to /decks/{id}/slots/{card} and get back the deck panel,
  plus an out-of-band update of that card's +/- control in the card list
- changing a deck choice (e.g. secondary class) re-renders the deck panel and
  fires a `filtersChanged` event so the card list refreshes its legality

Who can do what: every deck has an owner and a visibility (model.Deck).
Routes load decks through `_load(request, id, edit=...)`: a deck you can't
see is a 404 (so its existence isn't revealed), one you can see but don't own
is read-only and edits get 403. New, imported and copied decks belong to the
signed-in profile (request.state.user, set by auth.AuthMiddleware).

Every handler is a plain `def`, so FastAPI runs it in a worker thread.
"""

import json
import logging
import re
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from tcgnecro import db, ownership, users
from tcgnecro.auth import current_user
from tcgnecro.decklist import CardRequest, resolve
from tcgnecro.deckbuilder import buildql, filters, registry, simulate, store, sync
from tcgnecro.deckbuilder.model import MAIN, VISIBILITIES, Deck, DeckCard
from tcgnecro.games import card_backs
from tcgnecro.pdf import CUT_LINES, LAYOUTS, PAGE_SIZES, PdfOptions, generate_pdf
from tcgnecro.templating import templates

router = APIRouter(prefix="/decks")
log = logging.getLogger(__name__)


# ----- helpers -----------------------------------------------------------------

def _load(request: Request, deck_id: str, edit: bool = False) -> Deck:
    """The deck, if the signed-in profile may see it (and edit it, if asked)."""
    user = current_user(request)
    try:
        deck = store.load(deck_id)
    except store.DeckNotFound:
        raise HTTPException(404, "No such deck")
    if not deck.viewable_by(user):
        raise HTTPException(404, "No such deck")
    if edit and not deck.editable_by(user):
        raise HTTPException(403, "This deck belongs to someone else; copy it to make changes")
    return deck


def _owned(request: Request, game_id: str, pool) -> set[str] | None:
    """Pool ids the signed-in profile owns in this game, or None if they
    haven't set up their collection for it."""
    conn = db.connect()
    try:
        return ownership.owned_in_pool(conn, current_user(request), game_id, pool)
    finally:
        conn.close()


def _owner_name(deck: Deck) -> str:
    owner = users.get(deck.owner)
    return owner.name if owner else "nobody"


def _game(game_id: str):
    """(plugin, pool) for a game, or 404."""
    try:
        plugin = registry.plugin(game_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    conn = db.connect()
    try:
        return plugin, registry.pool(conn, game_id)
    finally:
        conn.close()


def _deck_context(request: Request, deck: Deck, plugin, pool, message: str | None = None) -> dict:
    """Everything the deck panel template needs."""
    problems = plugin.validate(deck, pool)
    flagged = {cid for p in problems for cid in p.card_ids}
    owned = _owned(request, deck.game, pool)
    def grouped(slots: dict[str, int]):
        groups: dict[str, list[tuple[DeckCard, int]]] = {}
        for card_id, qty in slots.items():
            card = pool.get(card_id)
            if card:
                groups.setdefault(plugin.group_of(card), []).append((card, qty))
        return [
            (plugin.config.group_label(g), sum(q for _, q in rows),
             sorted(rows, key=lambda r: filters._sort_value(r[0].name)))
            for g, rows in sorted(groups.items(), key=lambda kv: plugin.config.group_order(kv[0]))
        ]

    ordered = grouped(deck.slots)
    # Games with more than one part to a deck (MECCG): each extra part listed
    # under its own heading, after the main one.
    parts = [(s.key, s.label, deck.total(s.key), grouped(deck.sections.get(s.key, {})))
             for s in plugin.sections()]
    return {
        "deck": deck,
        "plugin": plugin,
        "pool": pool,
        "identity": pool.get(deck.identity) if deck.identity else None,
        "groups": ordered,
        "parts": parts,
        "main_label": plugin.main_label() if parts else None,
        "problems": problems,
        "flagged": flagged,
        "stats": plugin.stats(deck, pool),
        "selections": plugin.selections(deck, pool),
        "actions": plugin.actions(deck, pool),
        "missing_images": sum(1 for cid, _ in plugin.print_list(deck) if pool.resolve(cid) not in pool.images),
        "message": message,
        "can_edit": deck.editable_by(current_user(request)),
        "owned": owned,
        "not_owned": None if owned is None else sum(
            qty for cid, qty in plugin.print_list(deck) if pool.resolve(cid) not in owned),
        "owner_name": _owner_name(deck),
        "can_link": sync.can_link(plugin, deck),
        "can_simulate": bool(plugin.sim_settings(deck, pool)),
        "local_changes": sync.has_local_changes(deck),
        "visibilities": VISIBILITIES,
    }


def _card_rows(request: Request, deck: Deck, plugin, pool, offset: int = 0) -> dict:
    """Context for the card list fragment."""
    owned = _owned(request, deck.game, pool)
    # Once you've recorded a collection, "Only cards I own" starts switched on.
    values = filters.parse(plugin.config, request.query_params,
                           overrides={"owned": True} if owned is not None else None)
    sort_key = request.query_params.get("sort")
    section = _section(plugin, request.query_params.get("section"))
    cards = filters.apply(plugin, values, deck, pool, owned)
    query_text = request.query_params.get("query", "").strip()
    cards, query_error = _run_query(query_text, cards, deck, pool, owned)
    cards = filters.sort(cards, plugin.config, sort_key)
    # Grouped like the deck (Assets, Events...): by group, in the config's
    # order, then the chosen sort within each group (the sort is stable).
    group_of = {c.id: plugin.group_of(c) for c in cards}
    cards.sort(key=lambda c: (plugin.config.group_order(group_of[c.id]), group_of[c.id]))
    group_counts: dict[str, int] = {}
    for c in cards:
        group_counts[group_of[c.id]] = group_counts.get(group_of[c.id], 0) + 1
    size = plugin.config.page_size or max(len(cards), 1)   # 0: the whole list at once
    qs = (filters.query_string(plugin.config, values) + ([("query", query_text)] if query_text else [])
          + ([("sort", sort_key)] if sort_key else [])
          + ([("section", section)] if section != MAIN else []))
    return {
        "deck": deck,
        "plugin": plugin,
        "pool": pool,
        "cards": cards[offset:offset + size],
        "page_groups": _page_groups(cards, offset, size, group_of, group_counts, plugin),
        "total": len(cards),
        "offset": offset,
        "next_offset": offset + size if offset + size < len(cards) else None,
        "query": urlencode(qs),
        "values": values,
        "query_text": query_text,
        "query_error": query_error,
        "section": section,
        "sections": [(MAIN, plugin.main_label())] + [(x.key, x.label) for x in plugin.sections()]
                    if plugin.sections() else [],
        "sort_key": sort_key or (plugin.config.sorts[0].key if plugin.config.sorts else None),
        "owned": owned,
        "can_edit": deck.editable_by(current_user(request)),
    }


def _run_query(text: str, cards: list[DeckCard], deck: Deck, pool, owned) -> tuple[list[DeckCard], str | None]:
    """Narrow the filtered cards with a BuildQL query. A query that doesn't
    parse leaves the list as it was and returns the error to show."""
    if not text:
        return cards, None
    conn = db.connect()
    try:
        schema, pack_names = registry.query_schema(conn, deck.game, pool)
    finally:
        conn.close()
    try:
        query = buildql.parse(text)
        query.check_fields(schema)
    except buildql.QueryError as e:
        where = f" (at character {e.pos + 1})" if e.pos is not None else ""
        return cards, f"{e}{where}"
    ctx = buildql.Context(schema, deck.all_counts(), owned, pack_names)
    return [c for c in cards if query.matches(c, ctx)], None


def _section(plugin, key: str | None) -> str:
    """A section key the game has, else MAIN."""
    return key if key and any(s.key == key for s in plugin.sections()) else MAIN


def _page_groups(cards, offset, size, group_of, group_counts, plugin) -> list[tuple[str | None, list]]:
    """One page of the card list as [(heading, cards)]. A page that carries on
    the previous page's group continues it without a heading (None)."""
    out: list[tuple[str | None, list]] = []
    previous = group_of[cards[offset - 1].id] if 0 < offset <= len(cards) else None
    for card in cards[offset:offset + size]:
        group = group_of[card.id]
        if not out or group != previous:
            heading = None if not out and group == previous else \
                f"{plugin.config.group_label(group)} ({group_counts[group]})"
            out.append((heading, []))
            previous = group
        out[-1][1].append(card)
    return out


def _deck_fragment(request: Request, deck: Deck, plugin, pool, changed: list[str] = (),
                   message: str | None = None, refresh_list: bool = False,
                   section: str = MAIN) -> HTMLResponse:
    """The deck panel, plus fresh +/- controls for `changed` cards in the list
    (which shows counts for `section`)."""
    ctx = _deck_context(request, deck, plugin, pool, message) | {
        "changed": [pool.get(c) for c in changed], "list_section": section}
    response = templates.TemplateResponse(request, "_deck.html", ctx)
    if refresh_list:
        response.headers["HX-Trigger"] = "filtersChanged"
    return response


# ----- deck list, create, import -----------------------------------------------

@router.get("", response_class=HTMLResponse)
def deck_index(request: Request, error: str | None = None, game: str | None = None):
    """Every deck you can see, or just one game's (?game=, from its game page)."""
    conn = db.connect()
    loaded = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM games")}
    conn.close()
    games = [(gid, loaded[gid]) for gid in registry.plugins() if gid in loaded]
    if game not in dict(games):
        game = None
    elif games[0][0] != game:   # the forms start on this game
        games.sort(key=lambda g: g[0] != game)
    user = current_user(request)
    mine, shared = [], []
    for deck in store.list_decks():
        if not deck.viewable_by(user) or (game and deck.game != game):
            continue
        if deck.game not in loaded or deck.game not in registry.plugins():
            row = (deck, None, None)
        else:
            plugin, pool = _game(deck.game)
            identity = pool.get(deck.identity) if deck.identity else None
            errors = sum(1 for p in plugin.validate(deck, pool) if p.severity == "error")
            row = (deck, identity, errors)
        (mine if deck.editable_by(user) else shared).append(row)
    return templates.TemplateResponse(request, "decks.html", {
        "games": games, "game_names": loaded, "decks": mine, "shared": shared,
        "owner_names": {u.id: u.name for u in users.list_users()}, "error": error, "crumb_game": game,
    })


@router.get("/identities", response_class=HTMLResponse)
def identities(request: Request, game: str, q: str = "", owned: str = ""):
    plugin, pool = _game(game)
    words = [w for w in db.normalize_title(q).split("_") if w]
    found = [c for c in plugin.identities(pool)
             if all(w in db.normalize_title(c.name + " " + str(c.get("traits"))) for w in words)]
    mine = _owned(request, game, pool) if owned else None
    if mine is not None:
        found = [c for c in found if c.id in mine]
    return templates.TemplateResponse(request, "_identities.html", {
        "plugin": plugin, "pool": pool, "identities": found,
    })


@router.post("")
def create_deck(request: Request, game: str = Form(...), identity: str = Form(""), name: str = Form("")):
    plugin, pool = _game(game)
    card = pool.get(identity) if identity else None
    if plugin.config.identity_required and card is None:
        return RedirectResponse("/decks?" + urlencode({"game": game, "error": f"Choose an {plugin.config.identity_label.lower()}"}) + "#new", 303)
    deck = Deck(id=store.new_id(), game=game, name=name.strip() or (card.raw.get("name") if card else "New deck"),
                identity=card.id if card else None, owner=current_user(request).id)
    plugin.start_deck(deck, pool)
    store.save(deck)
    return RedirectResponse(f"/decks/{deck.id}", 303)


@router.post("/import")
def import_deck(request: Request, game: str = Form(...), source: str = Form(...)):
    plugin, pool = _game(game)
    try:
        deck = sync.import_and_link(plugin, source, pool)   # linked for syncing when possible
    except Exception as e:  # network errors, bad URLs, private decks
        return RedirectResponse("/decks?" + urlencode({"game": game, "error": f"Import failed: {e}"}), 303)
    deck.id = store.new_id()
    deck.owner = current_user(request).id
    deck.visibility = "private"
    store.save(deck)
    return RedirectResponse(f"/decks/{deck.id}", 303)


# ----- the builder ----------------------------------------------------------------

@router.get("/{deck_id}", response_class=HTMLResponse)
def builder(request: Request, deck_id: str):
    deck = _load(request, deck_id)
    plugin, pool = _game(deck.game)
    ctx = _deck_context(request, deck, plugin, pool) | _card_rows(request, deck, plugin, pool)
    ctx |= {
        "config": plugin.config,
        "page_sizes": list(PAGE_SIZES), "layouts": LAYOUTS, "cut_lines": CUT_LINES,
        "back_labels": sorted({b.label for b in card_backs(deck.game)}),
        "changed": [],
        "crumb_game": deck.game,
    }
    return templates.TemplateResponse(request, "builder.html", ctx)


@router.get("/{deck_id}/cards", response_class=HTMLResponse)
def card_list(request: Request, deck_id: str, offset: int = 0):
    deck = _load(request, deck_id)
    plugin, pool = _game(deck.game)
    ctx = _card_rows(request, deck, plugin, pool, offset)
    response = templates.TemplateResponse(request, "_cards.html", ctx)
    if offset == 0:
        # Keep the filters in the address bar, so reload and back work.
        response.headers["HX-Push-Url"] = f"/decks/{deck_id}?{ctx['query']}"
    return response


@router.get("/{deck_id}/card/{card_id}", response_class=HTMLResponse)
def card_detail(request: Request, deck_id: str, card_id: str):
    deck = _load(request, deck_id)
    plugin, pool = _game(deck.game)
    card = pool.get(card_id)
    if card is None:
        raise HTTPException(404, "No such card")
    return templates.TemplateResponse(request, "_detail.html", {
        "deck": deck, "card": card, "plugin": plugin, "pool": pool,
        "legal": plugin.is_legal(card, deck, pool),
    })


@router.post("/{deck_id}/slots/{card_id}", response_class=HTMLResponse)
def change_count(request: Request, deck_id: str, card_id: str, delta: int = 0, to: int | None = None,
                 section: str = MAIN, list_section: str | None = None):
    """Add/remove copies: ?delta=1 / ?delta=-1, or ?to=N. Clamped to 0..max
    copies. ?section= picks the part of the deck for games with several
    (plugin.sections()); ?list_section= is the part the card list shows."""
    deck = _load(request, deck_id, edit=True)
    plugin, pool = _game(deck.game)
    card = pool.get(card_id)
    if card is None:
        raise HTTPException(404, "No such card")
    section = _section(plugin, section)

    def change(d: Deck):
        current = d.count(card.id, section)
        wanted = to if to is not None else current + delta
        if wanted > current:   # adding is capped; removing always works, even over the limit
            limit = plugin.section_limit(card, d, section) if plugin.allowed_in(card, section) else 0
            wanted = min(wanted, max(current, limit))
        d.set_count(card.id, max(0, wanted), section)

    deck = store.update(deck_id, change)
    return _deck_fragment(request, deck, plugin, pool, changed=[card.id],
                          section=_section(plugin, list_section if list_section is not None else section))


@router.post("/{deck_id}/meta", response_class=HTMLResponse)
async def change_meta(request: Request, deck_id: str):
    form = await request.form()
    deck = _load(request, deck_id, edit=True)
    plugin, pool = _game(deck.game)
    selection_keys = {s.key: {v for v, _ in s.options} for s in plugin.selections(deck, pool)}
    refresh = False

    def change(d: Deck):
        nonlocal refresh
        if "name" in form and str(form["name"]).strip():
            d.name = str(form["name"]).strip()
        if "notes" in form:
            d.notes = str(form["notes"])
        if form.get("visibility") in VISIBILITIES:
            d.visibility = form["visibility"]
        for key, allowed in selection_keys.items():
            if key in form and form[key] in allowed:
                d.meta[key] = form[key]
                refresh = True

    deck = await _run(lambda: store.update(deck_id, change))
    return _deck_fragment(request, deck, plugin, pool, refresh_list=refresh)


async def _run(fn):
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(fn)


@router.post("/{deck_id}/sync", response_class=HTMLResponse)
def sync_now(request: Request, deck_id: str, force: bool = False):
    """Sync from the source site. ?force=1 replaces changes made here. When
    the cards changed, the whole page reloads (the investigator may differ)."""
    _load(request, deck_id, edit=True)
    result = sync.sync_deck(deck_id, force=force)
    plugin, pool = _game(result.deck.game)
    response = _deck_fragment(request, result.deck, plugin, pool, message=result.message)
    if result.changed:
        response.headers["HX-Refresh"] = "true"
    return response


@router.post("/{deck_id}/unsync", response_class=HTMLResponse)
def stop_syncing(request: Request, deck_id: str):
    _load(request, deck_id, edit=True)
    deck = sync.unlink(deck_id)
    plugin, pool = _game(deck.game)
    return _deck_fragment(request, deck, plugin, pool, message="No longer syncing; this deck is now yours to edit")


@router.post("/{deck_id}/action/{key}", response_class=HTMLResponse)
def run_action(request: Request, deck_id: str, key: str):
    deck = _load(request, deck_id, edit=True)
    plugin, pool = _game(deck.game)
    message = None
    before = dict(deck.slots)

    def change(d: Deck):
        nonlocal message
        message = plugin.run_action(key, d, pool)

    try:
        deck = store.update(deck_id, change)
    except KeyError:
        raise HTTPException(404, "No such action")
    changed = [c for c in set(before) | set(deck.slots) if before.get(c) != deck.slots.get(c)]
    return _deck_fragment(request, deck, plugin, pool, changed=changed, message=message)


# ----- simulation (deckbuilder/simulate.py) ----------------------------------------

def _sim_settings(request: Request, deck_id: str):
    """(deck, plugin, pool, settings, values) for a deck that can be simulated."""
    deck = _load(request, deck_id)
    plugin, pool = _game(deck.game)
    settings = plugin.sim_settings(deck, pool)
    if not settings:
        raise HTTPException(404, "This deck can't be simulated")
    return deck, plugin, pool, settings, simulate.parse_settings(settings, request.query_params)


@router.get("/{deck_id}/simulate", response_class=HTMLResponse)
def simulate_page(request: Request, deck_id: str):
    """The Simulate page: settings on one side, results (fetched by HTMX when
    the page loads and on every run) on the other. Read-only: anyone who can
    see a deck can simulate it."""
    deck, plugin, pool, settings, values = _sim_settings(request, deck_id)
    return templates.TemplateResponse(request, "simulate.html", {
        "deck": deck, "plugin": plugin, "settings": settings, "values": values,
        "identity": pool.get(deck.identity) if deck.identity else None, "crumb_game": deck.game,
    })


@router.get("/{deck_id}/simulate/run", response_class=HTMLResponse)
def simulate_run(request: Request, deck_id: str):
    deck, plugin, pool, settings, values = _sim_settings(request, deck_id)
    errors = [p for p in plugin.validate(deck, pool) if p.severity == "error"]
    try:
        report, failure = plugin.simulate(deck, pool, values), None
    except Exception as e:   # a bad effects entry or an edge case shouldn't break the page
        log.exception("Simulating deck %s failed", deck_id)
        report, failure = None, str(e) or e.__class__.__name__
    response = templates.TemplateResponse(request, "_simulation.html", {
        "deck": deck, "report": report, "failure": failure, "illegal": len(errors),
    })
    # Keep the settings in the address bar, so reloading or sharing the link
    # gives the same simulation.
    query = urlencode([(k, v) for k, v in request.query_params.multi_items()])
    response.headers["HX-Replace-Url"] = f"/decks/{deck_id}/simulate?{query}"
    return response


# ----- output -------------------------------------------------------------------

@router.get("/{deck_id}/export.txt")
def export_text(request: Request, deck_id: str):
    """A plain decklist using catalog titles, so it pastes straight into the
    Proxies page or `tcgnecro pdf --deck`."""
    deck = _load(request, deck_id)
    plugin, pool = _game(deck.game)
    lines = [f"# {deck.name}"]
    for card_id, qty in plugin.print_list(deck):
        card = pool.get(card_id)
        lines.append(f"{qty}x {card.title if card else card_id}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


@router.get("/{deck_id}/export.json")
def export_json(request: Request, deck_id: str):
    deck = _load(request, deck_id)
    filename = re.sub(r"[^\w.-]+", "_", deck.name) or "deck"
    return Response(json.dumps(deck.to_json(), indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.json"'})


@router.post("/{deck_id}/pdf")
def deck_pdf(request: Request, deck_id: str, page_size: str = Form("letter"), layout: str = Form("edge"),
             cut_lines: str = Form("margins"), double_sided: bool = Form(False),
             compact: bool = Form(False), back_label: str = Form(""), unowned_only: bool = Form(False)):
    deck = _load(request, deck_id)
    plugin, pool = _game(deck.game)
    owned = _owned(request, deck.game, pool) if unowned_only else None
    requests = []
    for card_id, qty in plugin.print_list(deck):
        card = pool.get(card_id)
        if card and (owned is None or card.id not in owned):
            requests += [CardRequest(card.title, card.id)] * qty
    if not requests:
        raise HTTPException(400, "You own every card in this deck; nothing to print")
    conn = db.connect()
    try:
        res = resolve(conn, deck.game, requests)
    finally:
        conn.close()
    if not res.printings:
        raise HTTPException(400, "None of this deck's cards have images in your collections")
    options = PdfOptions(page_size=page_size, layout=layout, cut_lines=cut_lines,
                         double_sided=double_sided, back_label=back_label or None, compact=compact)
    pdf = generate_pdf(res.printings, options, deck.game)
    filename = re.sub(r"[^\w.-]+", "_", deck.name) or "deck"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})


@router.post("/{deck_id}/copy")
def copy_deck(request: Request, deck_id: str):
    """Copy any deck you can see into your own decks (private)."""
    deck = _load(request, deck_id)
    copy = Deck.from_json(deck.to_json() | {
        "id": store.new_id(), "name": f"{deck.name} (copy)", "source": None, "sync": None,
        "owner": current_user(request).id, "visibility": "private",
    })
    store.save(copy)
    return RedirectResponse(f"/decks/{copy.id}", 303)


@router.post("/{deck_id}/delete")
def delete_deck(request: Request, deck_id: str):
    _load(request, deck_id, edit=True)
    store.delete(deck_id)
    return RedirectResponse("/decks", 303)
