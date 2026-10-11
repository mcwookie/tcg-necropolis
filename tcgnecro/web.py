"""Web app: the Proxies page (this module) and the deck builder (mounted from
tcgnecro.deckbuilder.routes under /decks). Everything happens server-side."""

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request
from PIL import Image
from pydantic import BaseModel

from tcgnecro import db
from tcgnecro.deckbuilder.routes import router as deckbuilder_router
from tcgnecro.decklist import DecklistError, pack_requests, parse_decklist, resolve
from tcgnecro.games import card_backs
from tcgnecro.pdf import CUT_LINES, LAYOUTS, PAGE_SIZES, PdfOptions, generate_pdf

THUMB_WIDTH = 240

app = FastAPI(title="TCG Necropolis")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(deckbuilder_router)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


class DeckRequest(BaseModel):
    game: str
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
    conn = db.connect()
    try:
        if req.pack:
            return resolve(conn, req.game, pack_requests(conn, req.game, req.pack))
        requests, not_found = parse_decklist(conn, req.game, req.decklist)
        return resolve(conn, req.game, requests, not_found)
    except DecklistError as e:
        raise HTTPException(400, str(e))
    finally:
        conn.close()


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
def game_info(game: str):
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
    return {
        "packs": [dict(p) for p in packs],
        "names": names,
        "back_labels": sorted({b.label for b in card_backs(game)}),
    }


@app.post("/api/resolve")
def resolve_deck(req: DeckRequest):
    res = _resolve(req)
    return {
        "cards": [
            {
                "title": p.title,
                "printing": p.label,
                "collection": p.collection,
                "thumb": db.thumb_url(p.front.best(False)[0]) if p.front.best(False) else None,
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
    name = f"{req.game}-{req.pack or 'deck'}.pdf"
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/thumb/{image_id:path}")
def thumb(image_id: str):
    root = db.collections_dir().resolve()
    src = (root / image_id).resolve()
    if not src.is_relative_to(root) or not src.is_file():
        raise HTTPException(404)
    cache = db.data_dir() / "thumbs" / (hashlib.sha1(f"{src}:{src.stat().st_mtime}".encode()).hexdigest() + ".jpg")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        img = Image.open(src)
        img.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
        img.convert("RGB").save(cache, "JPEG", quality=85)
    return FileResponse(cache, media_type="image/jpeg")
