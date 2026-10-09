"""Print-and-play PDF. Layout math is ported from upstream's pdf.rs, minus
the bleed layout and upscaling. Coordinates are points, origin top-left."""

import io
from dataclasses import dataclass
from math import floor

import pymupdf
from PIL import Image

from tcgnecro.decklist import Printing, Side
from tcgnecro.games import CardBack, card_backs

PT_PER_INCH = 72.0
MM = PT_PER_INCH / 25.4

PAGE_SIZES = {"letter": (8.5 * PT_PER_INCH, 11 * PT_PER_INCH), "a4": (8.27 * PT_PER_INCH, 11.69 * PT_PER_INCH)}

CARD_W = 178.54  # 63 x 88 mm
CARD_H = 249.09
MIN_MARGIN = 0.25 * PT_PER_INCH
LAYOUT_GAP = 0.125 * PT_PER_INCH
LAYOUT_INSET = 1.0 * MM

# Bled scans are cropped back to the cut line using upstream's reference sizes.
BLEED_W, BLEED_H = 816.0, 1110.0
CUT_W, CUT_H = 744.0, 1038.0

LAYOUTS = ("edge", "gap", "margin")
CUT_LINES = ("none", "margins", "full")

# "compact" re-encodes each card as a JPEG at this resolution, which takes a
# deck PDF from hundreds of MB down to a few MB.
COMPACT_DPI = 300
COMPACT_QUALITY = 88


@dataclass
class PdfOptions:
    page_size: str = "letter"
    layout: str = "edge"
    cut_lines: str = "margins"
    cut_line_thickness: float = 0.5
    double_sided: bool = False
    back_label: str | None = None  # which generic card back to use, e.g. "proxy"
    compact: bool = False

    @property
    def page(self) -> tuple[float, float]:
        return PAGE_SIZES[self.page_size]

    @property
    def gap(self) -> float:
        base = LAYOUT_GAP if self.layout == "gap" else 0.0
        return max(base, self.cut_line_thickness) if self.cut_lines == "full" else base

    def grid(self) -> tuple[int, int]:
        w, h = self.page
        cols = floor((w - 2 * MIN_MARGIN + self.gap) / (CARD_W + self.gap))
        rows = floor((h - 2 * MIN_MARGIN + self.gap) / (CARD_H + self.gap))
        return rows, cols

    def margins(self) -> tuple[float, float]:
        w, h = self.page
        rows, cols = self.grid()
        return (
            (w - (cols * CARD_W + (cols - 1) * self.gap)) / 2,
            (h - (rows * CARD_H + (rows - 1) * self.gap)) / 2,
        )

    def card_rect(self, index: int) -> pymupdf.Rect:
        _, cols = self.grid()
        left, top = self.margins()
        row, col = divmod(index, cols)
        x = left + col * (CARD_W + self.gap)
        y = top + row * (CARD_H + self.gap)
        inset = LAYOUT_INSET if self.layout == "margin" else 0.0
        return pymupdf.Rect(x + inset, y + inset, x + CARD_W - inset, y + CARD_H - inset)


# A slot is (image path, has_bleed), or None for a blank card position.
Slot = tuple[str, bool] | None


def _slot(side: Side | None) -> Slot:
    return side.best(want_bleed=False) if side else None


def _generic_back(printing: Printing, backs: list[CardBack], label: str | None) -> Slot:
    for back in backs:
        if back.back_group == printing.back_group and (label is None or back.label == label):
            return str(back.path), back.has_bleed
    return None


def build_pages(printings: list[Printing], options: PdfOptions, game_id: str) -> list[list[tuple[int, Slot]]]:
    rows, cols = options.grid()
    per_page = rows * cols
    if per_page == 0:
        return []

    # One printed card per back; a card with no back of its own is one card.
    cards = [(p, back) for p in printings for back in (p.backs or [None])]

    if not options.double_sided:
        slots = []
        for printing, back in cards:
            slots.append(_slot(printing.front))
            if back:
                slots.append(_slot(back))
        return [list(enumerate(slots[i:i + per_page])) for i in range(0, len(slots), per_page)]

    generic = card_backs(game_id)
    pages = []
    for i in range(0, len(cards), per_page):
        chunk = cards[i:i + per_page]
        pages.append([(n, _slot(p.front)) for n, (p, _) in enumerate(chunk)])
        # Backs are mirrored left-to-right so they line up when printed duplex.
        pages.append([
            ((n // cols) * cols + (cols - 1 - n % cols),
             _slot(back) if back else _generic_back(p, generic, options.back_label))
            for n, (p, back) in enumerate(chunk)
        ])
    return pages


def _prepare(path: str, has_bleed: bool, compact: bool, rect: pymupdf.Rect) -> dict:
    """insert_image() arguments for one card image."""
    if not has_bleed and not compact:
        return {"filename": path}

    img = Image.open(path)
    if has_bleed:
        w, h = img.size
        cx = round(w * (BLEED_W - CUT_W) / 2 / BLEED_W)
        cy = round(h * (BLEED_H - CUT_H) / 2 / BLEED_H)
        img = img.crop((cx, cy, w - cx, h - cy))
    if compact:
        target = (round(rect.width / PT_PER_INCH * COMPACT_DPI), round(rect.height / PT_PER_INCH * COMPACT_DPI))
        if img.width > target[0]:
            img = img.resize(target, Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=COMPACT_QUALITY)
    else:
        buf = io.BytesIO()
        img.save(buf, "PNG")
    return {"stream": buf.getvalue()}


def _cut_lines(page: pymupdf.Page, options: PdfOptions) -> None:
    if options.cut_lines == "none":
        return
    rows, cols = options.grid()
    left, top = options.margins()
    gap = options.gap
    w, h = options.page
    shape = page.new_shape()

    if options.cut_lines == "full":
        for i in range(cols + 1):
            x = left + i * CARD_W + (i - 0.5) * gap
            shape.draw_line((x, 0), (x, h))
        for i in range(rows + 1):
            y = top + i * CARD_H + (i - 0.5) * gap
            shape.draw_line((0, y), (w, y))
    else:
        # Short ticks in the page margin at every card edge.
        length = 15.0
        offset = max(3.0, options.cut_line_thickness / 2 + 1)
        right = left + cols * CARD_W + (cols - 1) * gap
        bottom = top + rows * CARD_H + (rows - 1) * gap

        def edges(start, size, count):
            out = []
            for i in range(count + 1):
                pos = start + i * size + max(i - 1, 0) * gap if i else start
                out.append(pos)
                if gap > 0 and 0 < i < count:
                    out.append(pos + gap)
            return out

        for x in edges(left, CARD_W, cols):
            shape.draw_line((x, top - offset - length), (x, top - offset))
            shape.draw_line((x, bottom + offset), (x, bottom + offset + length))
        for y in edges(top, CARD_H, rows):
            shape.draw_line((left - offset - length, y), (left - offset, y))
            shape.draw_line((right + offset, y), (right + offset + length, y))

    shape.finish(color=(16 / 255,) * 3, width=options.cut_line_thickness)
    shape.commit()


def generate_pdf(printings: list[Printing], options: PdfOptions, game_id: str, progress=None) -> bytes:
    doc = pymupdf.open()
    w, h = options.page
    xrefs: dict[str, int] = {}  # each distinct image is embedded once and reused
    pages = build_pages(printings, options, game_id)
    total = sum(1 for p in pages for _, s in p if s)
    done = 0

    for page_slots in pages:
        page = doc.new_page(width=w, height=h)
        for index, slot in page_slots:
            if slot is None:
                continue
            path, has_bleed = slot
            rect = options.card_rect(index)
            if path in xrefs:
                page.insert_image(rect, xref=xrefs[path], keep_proportion=False)
            else:
                xrefs[path] = page.insert_image(
                    rect, keep_proportion=False, **_prepare(path, has_bleed, options.compact, rect)
                )
            done += 1
            if progress:
                progress(done, total)
        _cut_lines(page, options)

    return doc.tobytes(garbage=1, deflate=True)
