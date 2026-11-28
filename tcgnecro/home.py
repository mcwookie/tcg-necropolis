"""The landing page (/) and each game's page (/games/<id>).

The landing page is where everyone starts after signing in: a grid of game
logos. A game's page is the hub for that game: what it is, how much of it the
app knows, the signed-in profile's decks and collection, and the way into the
proxy printer, the deck builder and the Collection page, all scoped to the game.

Logos are image files in static/logos/<game id>.(svg|png|webp|jpg). A game
without one gets a text tile from its module's LOGO_TILE.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from tcgnecro import db, users
from tcgnecro.auth import current_user
from tcgnecro.deckbuilder import registry, store
from tcgnecro.games import GAME_MODULES, get_game
from tcgnecro.templating import static_url, templates

router = APIRouter()

LOGO_DIR = Path(__file__).parent / "static" / "logos"
LOGO_TYPES = ("svg", "png", "webp", "jpg")
RECENT_DECKS = 5


@dataclass
class Game:
    id: str
    name: str
    description: str
    logo_url: str | None          # an image in static/logos/, if there is one
    tile: tuple[str, str, str, str]   # title, subtitle, background, text color
    packs: int                    # expansions the catalog knows
    cards: int
    printable: int                # cards with at least one scan
    has_builder: bool


def logo_url(game_id: str) -> str | None:
    for ext in LOGO_TYPES:
        if (LOGO_DIR / f"{game_id}.{ext}").is_file():
            return static_url(f"logos/{game_id}.{ext}")
    return None


def loaded_games(conn) -> list[Game]:
    """Every game whose catalog is loaded, in the registry's order."""
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM games")}
    count = lambda sql: dict(conn.execute(sql).fetchall())
    packs = count("SELECT game_id, COUNT(*) FROM packs GROUP BY game_id")
    cards = count("SELECT game_id, COUNT(*) FROM cards GROUP BY game_id")
    printable = count("""SELECT c.game_id, COUNT(*) FROM cards c WHERE EXISTS
                         (SELECT 1 FROM images i WHERE i.game_id = c.game_id AND i.card_id = c.id)
                         GROUP BY c.game_id""")
    builders = registry.plugins()
    games = []
    for gid in GAME_MODULES:
        if gid not in names:
            continue
        module = get_game(gid)
        games.append(Game(
            id=gid, name=getattr(module, "NAME", names[gid]), description=getattr(module, "DESCRIPTION", ""),
            logo_url=logo_url(gid), tile=getattr(module, "LOGO_TILE", (names[gid], "", "#444441", "#F1EFE8")),
            packs=packs.get(gid, 0), cards=cards.get(gid, 0), printable=printable.get(gid, 0),
            has_builder=gid in builders,
        ))
    return games


def _my_decks(user) -> list:
    """The profile's own decks (not ones shared with them, not archived), newest first."""
    decks = [d for d in store.list_decks() if d.editable_by(user) and not d.archived]
    return sorted(decks, key=lambda d: d.updated or "", reverse=True)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    user = current_user(request)
    conn = db.connect()
    try:
        games = loaded_games(conn)
    finally:
        conn.close()
    decks = _my_decks(user)
    return templates.TemplateResponse(request, "home.html", {
        "games": games,
        "deck_counts": {g.id: sum(1 for d in decks if d.game == g.id) for g in games},
        "owned": {g.id: users.owned_packs(user, g.id) for g in games},
    })


@router.get("/games/{game_id}", response_class=HTMLResponse)
def game_page(request: Request, game_id: str):
    user = current_user(request)
    conn = db.connect()
    try:
        game = next((g for g in loaded_games(conn) if g.id == game_id), None)
        if game is None:
            raise HTTPException(404, "No such game")
        packs = conn.execute("SELECT id, name, group_name FROM packs WHERE game_id = ? ORDER BY group_order, sort_key",
                             (game_id,)).fetchall()
        pool = registry.pool(conn, game_id) if game.has_builder else None
    finally:
        conn.close()
    owned = users.owned_packs(user, game_id)
    # The pack groups (Arkham cycles...) the profile owns something from, in catalog
    # order; a pack outside any group stands for itself.
    owned_groups = list(dict.fromkeys(p["group_name"] or p["name"] for p in packs if owned and p["id"] in owned))
    decks = [d for d in _my_decks(user) if d.game == game_id]
    identity = lambda d: pool.get(d.identity) if pool and d.identity else None
    shared = sum(1 for d in store.list_decks() if d.game == game_id and not d.archived and d.viewable_by(user) and not d.editable_by(user))
    return templates.TemplateResponse(request, "game.html", {
        "game": game, "crumb_game": game.id,
        "decks": [(d, identity(d)) for d in decks[:RECENT_DECKS]], "deck_total": len(decks), "shared_total": shared,
        "owned": owned, "owned_groups": owned_groups,
    })
