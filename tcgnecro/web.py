"""Web app: the Proxies page (this module), the deck builder (mounted from
tcgnecro.deckbuilder.routes under /decks), and sign-in (tcgnecro.auth).
Every page except /login requires a signed-in profile. Everything happens
server-side.

Set TCGNECRO_SECURE_COOKIES=1 when serving over HTTPS, so the session cookie
is never sent over plain HTTP.

While running, the server also keeps linked decks in sync with their source
site (deckbuilder/sync.py) every TCGNECRO_SYNC_MINUTES (default 60; 0 = off)."""

import asyncio
import contextlib
import logging
import os

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from fastapi import Request
from PIL import Image
from pydantic import BaseModel, Field

from tcgnecro import auth, collection_page, db, users
from tcgnecro.deckbuilder import sync
from tcgnecro.deckbuilder.routes import router as deckbuilder_router
from tcgnecro.decklist import (CardRequest, DecklistError, Printing, item_requests, pack_cards, pack_requests,
                               parse_decklist, pick_printings, resolve)
from tcgnecro.games import card_backs
from tcgnecro.pdf import CUT_LINES, LAYOUTS, PAGE_SIZES, PdfOptions, generate_pdf
from tcgnecro.templating import templates

THUMB_WIDTH = 240

SYNC_MINUTES = int(os.environ.get("TCGNECRO_SYNC_MINUTES", "60"))
SYNC_FIRST_DELAY = 120   # seconds after start-up before the first check

log = logging.getLogger("tcgnecro.sync")


async def _sync_loop(minutes: int) -> None:
    await asyncio.sleep(SYNC_FIRST_DELAY)
    while True:
        try:
            results = await run_in_threadpool(sync.sync_all)
            updated = sum(1 for _, r in results if r.changed)
            log.info("deck sync: %d linked, %d updated", len(results), updated)
        except Exception:
            log.exception("deck sync failed")
        await asyncio.sleep(minutes * 60)


@contextlib.asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_sync_loop(SYNC_MINUTES)) if SYNC_MINUTES > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="TCG Necropolis", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(auth.router)
app.include_router(collection_page.router)
app.include_router(deckbuilder_router)
auth.install(app, https_only=os.environ.get("TCGNECRO_SECURE_COOKIES") == "1")


class ListItem(BaseModel):
    """One row of the Proxies page's list: an exact printing and how many."""
    card_id: str
    pack: str | None = None
    position: int | None = None
    qty: int = Field(1, ge=0, le=99)


class DeckRequest(BaseModel):
    game: str
    items: list[ListItem] = []
    decklist: str = ""
    pack: str | None = None


class PdfRequest(DeckRequest):
    page_size: str = "letter"
    layout: str = "edge"
    cut_lines: str = "margins"
    double_sided: bool = False
    back_label: str | None = None
    compact: bool = True


def _resolve(req: DeckRequest):
    """The list's items, then any decklist text, then a whole pack."""
    conn = db.connect()
    try:
        requests, not_found = item_requests(
            conn, req.game, [(i.card_id, i.pack, i.position, i.qty) for i in req.items])
        if req.decklist.strip():
            more, missing = parse_decklist(conn, req.game, req.decklist)
            requests += more
            not_found += [m for m in missing if m not in not_found]
        if req.pack:
            requests += pack_requests(conn, req.game, req.pack)
        return resolve(conn, req.game, requests, not_found)
    except DecklistError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


def _list_item(request: CardRequest, printing: Printing | None, qty: int) -> dict:
    """A proxy-list row: the printing asked for, plus what the page shows."""
    front = printing.front.best(False) if printing else None
    return {
        "card_id": request.card_id, "pack": request.printing, "position": request.position, "qty": qty,
        "title": request.title,
        "has_image": printing is not None,
        "thumb": db.thumb_url(front[0], printing.front.rotate) if front else None,
        "landscape": bool(printing) and printing.front.rotate in (90, 270),
        # When the pack's own printing has no image, another pack's art stands in.
        "art_from": printing.label if printing and printing.pack_id != request.printing else None,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    conn = db.connect()
    games = conn.execute(
        "SELECT g.id, g.name FROM games g WHERE EXISTS (SELECT 1 FROM images i WHERE i.game_id = g.id)"
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(request, "index.html", {
        "games": games,
        "page_sizes": list(PAGE_SIZES),
        "layouts": LAYOUTS,
        "cut_lines": CUT_LINES,
    })


@app.get("/api/games/{game}")
def game_info(game: str, request: Request):
    conn = db.connect()
    packs = conn.execute(
        """SELECT p.id, p.name, COUNT(DISTINCT i.card_id) AS printable
           FROM packs p LEFT JOIN images i ON i.game_id = p.game_id AND i.pack_id = p.id
           WHERE p.game_id = ? GROUP BY p.id ORDER BY p.sort_key""",
        (game,),
    ).fetchall()
    names = [r[0] for r in conn.execute(
        """SELECT DISTINCT c.title FROM cards c JOIN images i ON i.game_id = c.game_id AND i.card_id = c.id
           WHERE c.game_id = ? ORDER BY c.title""",
        (game,),
    )]
    conn.close()
    owned = users.owned_packs(request.state.user, game)   # None: no collection set up for this game
    return {
        "packs": [dict(p) | {"owned": owned is not None and p["id"] in owned} for p in packs],
        "has_collection": owned is not None,
        "names": names,
        "back_labels": sorted({b.label for b in card_backs(game)}),
    }


@app.get("/api/games/{game}/packs/{pack}/cards")
def pack_card_list(game: str, pack: str):
    """A pack's cards for the picker, in pack order, each with its quantity in
    the pack and the image that would print."""
    conn = db.connect()
    try:
        cards = pack_cards(conn, game, pack)
        printings = pick_printings(conn, game, [r for r, _ in cards])
    finally:
        conn.close()
    if not cards:
        raise HTTPException(404, "No such pack")
    return {"cards": [_list_item(r, p, qty) for (r, qty), p in zip(cards, printings)]}


class ImportRequest(BaseModel):
    game: str
    decklist: str


@app.post("/api/parse")
def parse_into_list(req: ImportRequest):
    """Decklist text -> proxy-list rows, one per printing chosen (quantities
    added up), so pasted lists land in the same list as picked cards."""
    conn = db.connect()
    try:
        requests, not_found = parse_decklist(conn, req.game, req.decklist)
        printings = pick_printings(conn, req.game, requests)
    except DecklistError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()
    rows, no_image = {}, []
    for request, printing in zip(requests, printings):
        if printing is None:
            if request.title not in no_image:
                no_image.append(request.title)
            continue
        exact = CardRequest(printing.title, printing.card_id, printing.pack_id, printing.position)
        key = (exact.card_id, exact.printing, exact.position)
        if key in rows:
            rows[key]["qty"] += 1
        else:
            rows[key] = _list_item(exact, printing, 1)
    return {"items": list(rows.values()), "not_found": not_found, "no_image": no_image}


@app.post("/api/resolve")
def resolve_deck(req: DeckRequest):
    res = _resolve(req)
    return {
        "cards": [
            {
                "title": p.title,
                "printing": p.label,
                "collection": p.collection,
                "thumb": db.thumb_url(p.front.best(False)[0], p.front.rotate) if p.front.best(False) else None,
                "landscape": p.front.rotate in (90, 270),
                # Every side, for the full-size viewer: front first, then backs.
                "sides": [
                    {"label": "Front" if i == 0 else ("Back" if len(p.backs) == 1 else f"Back {i}"),
                     "thumb": db.thumb_url(best[0], side.rotate), "full": db.image_url(best[0], side.rotate)}
                    for i, side in enumerate([p.front, *p.backs])
                    if (best := side.best(False))
                ],
                "backs": len(p.backs),
            }
            for p in res.printings
        ],
        "not_found": res.not_found,
        "no_image": res.no_image,
    }


@app.post("/api/pdf")
def make_pdf(req: PdfRequest):
    res = _resolve(req)
    if not res.printings:
        raise HTTPException(400, "Nothing to print")
    options = PdfOptions(
        page_size=req.page_size, layout=req.layout, cut_lines=req.cut_lines,
        double_sided=req.double_sided, back_label=req.back_label or None, compact=req.compact,
    )
    pdf = generate_pdf(res.printings, options, req.game)
    name = f"{req.game}-{req.pack or 'proxies'}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _collection_file(image_id: str) -> Path:
    """A file inside the collections folder, or 404. Guards against paths
    like ../../index.db escaping it."""
    root = db.collections_dir().resolve()
    src = (root / image_id).resolve()
    if not src.is_relative_to(root) or not src.is_file():
        raise HTTPException(404)
    return src


# Clockwise rotation -> the lossless PIL transpose that does it.
ROTATIONS = {
    90: Image.Transpose.ROTATE_270,    # PIL's names are counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def _rotation(r: int) -> int:
    if r not in (0, *ROTATIONS):
        raise HTTPException(400, "r must be 0, 90, 180 or 270")
    return r


def _cache_path(src: Path, kind: str, r: int) -> Path:
    """Derived images live in data/thumbs/, keyed by source path, its
    modification time (so replaced scans regenerate) and the rotation."""
    key = f"{kind}:{src}:{src.stat().st_mtime}:{r}"
    return db.data_dir() / "thumbs" / (hashlib.sha1(key.encode()).hexdigest() + ".jpg")


def _open(src: Path, r: int) -> Image.Image:
    """The scan turned r degrees clockwise. A quarter turn is for showing a
    landscape card stored sideways in a portrait slot, so a scan that's
    already landscape (a few collections store some that way) is left as is."""
    img = Image.open(src)
    if r in (90, 270) and img.width > img.height:
        return img
    return img.transpose(ROTATIONS[r]) if r else img


@app.get("/image/{image_id:path}")
def full_image(image_id: str, r: int = 0):
    """The scan at full size, for the card viewer and card details. `r` turns
    it clockwise so landscape cards read upright; rotated copies are cached
    as JPEGs on first view. FileResponse sends ETag/Last-Modified, so browsers
    revalidate cheaply and pick up replaced files."""
    src, r = _collection_file(image_id), _rotation(r)
    if not r:
        return FileResponse(src)
    cache = _cache_path(src, "full", r)
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _open(src, r).convert("RGB").save(cache, "JPEG", quality=92)
    return FileResponse(cache, media_type="image/jpeg")


@app.get("/thumb/{image_id:path}")
def thumb(image_id: str, r: int = 0):
    """A small JPEG of the scan, turned `r` degrees clockwise. The short side
    is THUMB_WIDTH, so landscape thumbnails are as sharp as portrait ones."""
    src, r = _collection_file(image_id), _rotation(r)
    cache = _cache_path(src, "thumb", r)
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        img = _open(src, r)
        box = (THUMB_WIDTH * 2, THUMB_WIDTH) if img.width > img.height else (THUMB_WIDTH, THUMB_WIDTH * 2)
        img.thumbnail(box)
        img.convert("RGB").save(cache, "JPEG", quality=85)
    return FileResponse(cache, media_type="image/jpeg")
