"""Deck builder web pages, mounted under /decks.

Pages are server-rendered with Jinja; HTMX swaps in fragments as you work:

- the filter form re-fetches the card list (GET /decks/{id}/cards)
- +/- buttons POST to /decks/{id}/slots/{card} and get back the deck panel,
  plus an out-of-band update of that card's +/- control in the card list
- changing a deck choice (e.g. secondary class) re-renders the deck panel and
  fires a `filtersChanged` event so the card list refreshes its legality

Every handler is a plain `def`, so FastAPI runs it in a worker thread.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from tcgnecro import db
from tcgnecro.decklist import CardRequest, resolve
from tcgnecro.deckbuilder import filters, registry, store
from tcgnecro.deckbuilder.model import Deck, DeckCard
from tcgnecro.games import card_backs
from tcgnecro.pdf import CUT_LINES, LAYOUTS, PAGE_SIZES, PdfOptions, generate_pdf

router = APIRouter(prefix="/decks")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.globals["thumb_url"] = db.thumb_url


# ----- helpers -----------------------------------------------------------------

def _load(deck_id: str) -> Deck:
    try:
        return store.load(deck_id)
    except store.DeckNotFound:
        raise HTTPException(404, "No such deck")


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


def _deck_context(deck: Deck, plugin, pool, message: str | None = None) -> dict:
    """Everything the deck panel template needs."""
    problems = plugin.validate(deck, pool)
    flagged = {cid for p in problems for cid in p.card_ids}
    groups: dict[str, list[tuple[DeckCard, int]]] = {}
    for card_id, qty in deck.slots.items():
        card = pool.get(card_id)
        if card:
            groups.setdefault(plugin.group_of(card), []).append((card, qty))
    ordered = [
        (plugin.config.group_label(g), sum(q for _, q in rows),
         sorted(rows, key=lambda r: filters._sort_value(r[0].name)))
        for g, rows in sorted(groups.items(), key=lambda kv: plugin.config.group_order(kv[0]))
    ]
    return {
        "deck": deck,
        "plugin": plugin,
        "pool": pool,
        "identity": pool.get(deck.identity) if deck.identity else None,
        "groups": ordered,
        "problems": problems,
        "flagged": flagged,
        "stats": plugin.stats(deck, pool),
        "selections": plugin.selections(deck, pool),
        "actions": plugin.actions(deck, pool),
        "missing_images": sum(1 for cid, _ in plugin.print_list(deck) if pool.resolve(cid) not in pool.images),
        "message": message,
    }


def _card_rows(request: Request, deck: Deck, plugin, pool, offset: int = 0) -> dict:
    """Context for the card list fragment."""
    values = filters.parse(plugin.config, request.query_params)
    sort_key = request.query_params.get("sort")
    cards = filters.sort(filters.apply(plugin, values, deck, pool), plugin.config, sort_key)
    size = plugin.config.page_size
    qs = filters.query_string(plugin.config, values) + ([("sort", sort_key)] if sort_key else [])
    return {
        "deck": deck,
        "plugin": plugin,
        "pool": pool,
        "cards": cards[offset:offset + size],
        "total": len(cards),
        "offset": offset,
        "next_offset": offset + size if offset + size < len(cards) else None,
        "query": urlencode(qs),
        "values": values,
        "sort_key": sort_key or (plugin.config.sorts[0].key if plugin.config.sorts else None),
    }


def _deck_fragment(request: Request, deck: Deck, plugin, pool, changed: list[str] = (),
                   message: str | None = None, refresh_list: bool = False) -> HTMLResponse:
    """The deck panel, plus fresh +/- controls for `changed` cards in the list."""
    ctx = _deck_context(deck, plugin, pool, message) | {"changed": [pool.get(c) for c in changed]}
    response = templates.TemplateResponse(request, "_deck.html", ctx)
    if refresh_list:
        response.headers["HX-Trigger"] = "filtersChanged"
    return response


# ----- deck list, create, import -----------------------------------------------

@router.get("", response_class=HTMLResponse)
def deck_index(request: Request, error: str | None = None):
    conn = db.connect()
    loaded = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM games")}
    conn.close()
    games = [(gid, loaded[gid]) for gid in registry.plugins() if gid in loaded]
    decks = []
    for deck in store.list_decks():
        if deck.game not in loaded or deck.game not in registry.plugins():
            decks.append((deck, None, None))
            continue
        plugin, pool = _game(deck.game)
        identity = pool.get(deck.identity) if deck.identity else None
        errors = sum(1 for p in plugin.validate(deck, pool) if p.severity == "error")
        decks.append((deck, identity, errors))
    return templates.TemplateResponse(request, "decks.html", {
        "games": games, "game_names": loaded, "decks": decks, "error": error,
    })


@router.get("/identities", response_class=HTMLResponse)
def identities(request: Request, game: str, q: str = ""):
    plugin, pool = _game(game)
    words = [w for w in db.normalize_title(q).split("_") if w]
    found = [c for c in plugin.identities(pool)
             if all(w in db.normalize_title(c.name + " " + str(c.get("traits"))) for w in words)]
    return templates.TemplateResponse(request, "_identities.html", {
        "plugin": plugin, "pool": pool, "identities": found,
    })


@router.post("")
def create_deck(game: str = Form(...), identity: str = Form(""), name: str = Form("")):
    plugin, pool = _game(game)
    card = pool.get(identity) if identity else None
    if plugin.config.identity_required and card is None:
        return RedirectResponse(f"/decks?error=Choose+an+{plugin.config.identity_label.lower()}", 303)
    deck = Deck(id=store.new_id(), game=game, name=name.strip() or (card.raw.get("name") if card else "New deck"),
                identity=card.id if card else None)
    plugin.start_deck(deck, pool)
    store.save(deck)
    return RedirectResponse(f"/decks/{deck.id}", 303)


@router.post("/import")
def import_deck(game: str = Form(...), source: str = Form(...)):
    plugin, pool = _game(game)
    try:
        deck = plugin.import_deck(source, pool)
    except Exception as e:  # network errors, bad URLs, private decks
        return RedirectResponse("/decks?" + urlencode({"error": f"Import failed: {e}"}), 303)
    deck.id = store.new_id()
    store.save(deck)
    return RedirectResponse(f"/decks/{deck.id}", 303)


# ----- the builder ----------------------------------------------------------------

@router.get("/{deck_id}", response_class=HTMLResponse)
def builder(request: Request, deck_id: str):
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    ctx = _deck_context(deck, plugin, pool) | _card_rows(request, deck, plugin, pool)
    ctx |= {
        "config": plugin.config,
        "page_sizes": list(PAGE_SIZES), "layouts": LAYOUTS, "cut_lines": CUT_LINES,
        "back_labels": sorted({b.label for b in card_backs(deck.game)}),
        "changed": [],
    }
    return templates.TemplateResponse(request, "builder.html", ctx)


@router.get("/{deck_id}/cards", response_class=HTMLResponse)
def card_list(request: Request, deck_id: str, offset: int = 0):
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    ctx = _card_rows(request, deck, plugin, pool, offset)
    response = templates.TemplateResponse(request, "_cards.html", ctx)
    if offset == 0:
        # Keep the filters in the address bar, so reload and back work.
        response.headers["HX-Push-Url"] = f"/decks/{deck_id}?{ctx['query']}"
    return response


@router.get("/{deck_id}/card/{card_id}", response_class=HTMLResponse)
def card_detail(request: Request, deck_id: str, card_id: str):
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    card = pool.get(card_id)
    if card is None:
        raise HTTPException(404, "No such card")
    return templates.TemplateResponse(request, "_detail.html", {
        "deck": deck, "card": card, "plugin": plugin, "pool": pool,
        "legal": plugin.is_legal(card, deck, pool),
    })


@router.post("/{deck_id}/slots/{card_id}", response_class=HTMLResponse)
def change_count(request: Request, deck_id: str, card_id: str, delta: int = 0, to: int | None = None):
    """Add/remove copies: ?delta=1 / ?delta=-1, or ?to=N. Clamped to 0..max copies."""
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    card = pool.get(card_id)
    if card is None:
        raise HTTPException(404, "No such card")

    def change(d: Deck):
        current = d.count(card.id)
        wanted = to if to is not None else current + delta
        d.set_count(card.id, max(0, min(wanted, plugin.max_copies(card, d))))

    deck = store.update(deck_id, change)
    return _deck_fragment(request, deck, plugin, pool, changed=[card.id])


@router.post("/{deck_id}/meta", response_class=HTMLResponse)
async def change_meta(request: Request, deck_id: str):
    form = await request.form()
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    selection_keys = {s.key: {v for v, _ in s.options} for s in plugin.selections(deck, pool)}
    refresh = False

    def change(d: Deck):
        nonlocal refresh
        if "name" in form and str(form["name"]).strip():
            d.name = str(form["name"]).strip()
        if "notes" in form:
            d.notes = str(form["notes"])
        for key, allowed in selection_keys.items():
            if key in form and form[key] in allowed:
                d.meta[key] = form[key]
                refresh = True

    deck = await _run(lambda: store.update(deck_id, change))
    return _deck_fragment(request, deck, plugin, pool, refresh_list=refresh)


async def _run(fn):
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(fn)


@router.post("/{deck_id}/action/{key}", response_class=HTMLResponse)
def run_action(request: Request, deck_id: str, key: str):
    deck = _load(deck_id)
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


# ----- output -------------------------------------------------------------------

@router.get("/{deck_id}/export.txt")
def export_text(deck_id: str):
    """A plain decklist using catalog titles, so it pastes straight into the
    Proxies page or `tcgnecro pdf --deck`."""
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    lines = [f"# {deck.name}"]
    for card_id, qty in plugin.print_list(deck):
        card = pool.get(card_id)
        lines.append(f"{qty}x {card.title if card else card_id}")
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


@router.get("/{deck_id}/export.json")
def export_json(deck_id: str):
    deck = _load(deck_id)
    filename = re.sub(r"[^\w.-]+", "_", deck.name) or "deck"
    return Response(json.dumps(deck.to_json(), indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.json"'})


@router.post("/{deck_id}/pdf")
def deck_pdf(deck_id: str, page_size: str = Form("letter"), layout: str = Form("edge"),
             cut_lines: str = Form("margins"), double_sided: bool = Form(False),
             compact: bool = Form(False), back_label: str = Form("")):
    deck = _load(deck_id)
    plugin, pool = _game(deck.game)
    requests = []
    for card_id, qty in plugin.print_list(deck):
        card = pool.get(card_id)
        if card:
            requests += [CardRequest(card.title, card.id)] * qty
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
def copy_deck(deck_id: str):
    deck = _load(deck_id)
    copy = Deck.from_json(deck.to_json() | {"id": store.new_id(), "name": f"{deck.name} (copy)", "source": None})
    store.save(copy)
    return RedirectResponse(f"/decks/{copy.id}", 303)


@router.post("/{deck_id}/delete")
def delete_deck(deck_id: str):
    _load(deck_id)
    store.delete(deck_id)
    return RedirectResponse("/decks", 303)
