"""The Collection page (/collection): which packs you own, per game.

Saved on the signed-in profile (users.set_owned_packs). The deck builder
uses it for "Only cards I own" and "Print only cards I don't own"; see
ownership.py for how packs turn into cards.
"""

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from tcgnecro import db, ownership, users
from tcgnecro.auth import current_user
from tcgnecro.templating import templates

router = APIRouter(prefix="/collection")


def _games(conn) -> list[tuple[str, str]]:
    return [(r["id"], r["name"]) for r in conn.execute("SELECT id, name FROM games ORDER BY name")]


@router.get("", response_class=HTMLResponse)
def collection_index(request: Request):
    conn = db.connect()
    games = _games(conn)
    conn.close()
    if not games:
        raise HTTPException(404, "No games loaded; run `tcgnecro catalog`")
    return RedirectResponse(f"/collection/{games[0][0]}", 303)


@router.get("/{game_id}", response_class=HTMLResponse)
def collection(request: Request, game_id: str, message: str | None = None):
    user = current_user(request)
    conn = db.connect()
    try:
        games = _games(conn)
        if game_id not in dict(games):
            raise HTTPException(404, "No such game")
        groups = ownership.pack_groups(conn, game_id)
    finally:
        conn.close()
    owned = users.owned_packs(user, game_id)
    return templates.TemplateResponse(request, "collection.html", {
        "nav": "collection", "games": games, "game_id": game_id, "groups": groups,
        "owned": owned or set(), "configured": owned is not None,
        "total": sum(len(packs) for _, packs in groups), "message": message,
    })


@router.post("/{game_id}")
async def save_collection(request: Request, game_id: str):
    user = current_user(request)
    form = await request.form()
    conn = db.connect()
    try:
        known = {r[0] for r in conn.execute("SELECT id FROM packs WHERE game_id = ?", (game_id,))}
    finally:
        conn.close()
    if not known:
        raise HTTPException(404, "No such game")
    if form.get("forget"):
        users.set_owned_packs(user.id, game_id, None)
        message = "Collection cleared; ownership filters are off for this game"
    else:
        chosen = {p for p in form.getlist("pack") if p in known}
        users.set_owned_packs(user.id, game_id, chosen)
        message = f"Saved: you own {len(chosen)} pack{'s' if len(chosen) != 1 else ''}"
    return RedirectResponse(f"/collection/{game_id}?" + urlencode({"message": message}), 303)
