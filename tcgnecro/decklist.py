"""Decklist text -> card requests -> chosen printings (images to print).

Ported from upstream's card_store.rs. Decklist lines look like:

    3x Force Lightning
    2 Gotal Outcast (48.2)   # comments are ignored
    Darth Vader [core-set]
    Darth Vader [core-set:12:swlcg]   # [printing:position:collection]

The Proxies page's list sends exact printings instead (item_requests).
"""

import json
import sqlite3
from dataclasses import dataclass, field

from tcgnecro.db import image_path, normalize_title
from tcgnecro.games import display_rotation
from tcgnecro.naming import side_sort_key


class DecklistError(Exception):
    pass


@dataclass(frozen=True)
class CardRequest:
    title: str
    card_id: str
    printing: str | None = None
    position: int | None = None
    collection: str | None = None


@dataclass
class Side:
    image: str | None = None        # path to the image without bleed
    bleed_image: str | None = None  # path to the image with bleed
    rotate: int = 0                 # clockwise degrees to show it upright (display only)

    def best(self, want_bleed: bool) -> tuple[str, bool] | None:
        """(path, has_bleed), preferring the requested kind but taking either."""
        order = [(self.bleed_image, True), (self.image, False)]
        if not want_bleed:
            order.reverse()
        return next(((p, b) for p, b in order if p), None)


@dataclass
class Printing:
    card_id: str
    title: str
    collection: str
    pack_id: str | None
    pack_name: str | None
    variant: str | None
    position: int | None
    back_group: str | None
    sort_key: str | None
    front: Side = field(default_factory=Side)
    backs: list[Side] = field(default_factory=list)

    @property
    def is_official(self) -> bool:
        return self.pack_id is not None

    @property
    def label(self) -> str:
        return self.pack_name or self.variant or "?"


@dataclass
class Resolution:
    printings: list[Printing]
    not_found: list[str]   # names not in the catalog
    no_image: list[str]    # in the catalog, but no collection has an image


def clean_card_name(name: str) -> str:
    return name.rstrip(" \t●○•·-–—")


def parse_quantity(line: str) -> tuple[int, str]:
    qty, sep, rest = line.partition("x ")
    if sep and qty.isdigit():
        return int(qty), rest.strip()
    qty, sep, rest = line.partition(" ")
    if sep and qty.isdigit():
        return int(qty), rest.strip()
    return 1, line


def parse_overrides(text: str) -> tuple[str, str | None, int | None, str | None]:
    start = text.find("[")
    if start < 0:
        return text.strip(), None, None, None
    end = text.find("]")
    if end < 0:
        raise DecklistError(f"Unclosed bracket in '{text}'")
    inner = text[start + 1:end]
    if not inner.strip():
        raise DecklistError(f"Empty brackets in '{text}'")
    parts = [p.strip().lower() or None for p in inner.split(":")]
    if len(parts) > 3:
        raise DecklistError(f"'[{inner}]' has too many ':'-separated parts")
    parts += [None] * (3 - len(parts))
    printing, position, collection = parts
    position = int(position) if position and position.isdigit() else None
    return text[:start].strip(), printing, position, collection


def parse_decklist(conn: sqlite3.Connection, game_id: str, text: str) -> tuple[list[CardRequest], list[str]]:
    entries = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        qty, rest = parse_quantity(line)
        name, printing, position, collection = parse_overrides(rest)
        entries.append((clean_card_name(name), qty, printing, position, collection))

    resolved = _resolve_names(conn, game_id, {name for name, *_ in entries})
    requests, not_found = [], []
    for name, qty, printing, position, collection in entries:
        card = resolved.get(normalize_title(name))
        if card is None:
            if name not in not_found:
                not_found.append(name)
            continue
        request = CardRequest(card["title"], card["id"], printing or card["pack_id"], position, collection)
        requests.extend([request] * qty)
    return requests, not_found


def _resolve_names(conn, game_id, names) -> dict[str, sqlite3.Row]:
    """Normalized title -> the card in the newest pack with that title."""
    normalized = list({normalize_title(n) for n in names})
    if not normalized:
        return {}
    marks = ",".join("?" * len(normalized))
    rows = conn.execute(
        f"""SELECT c.id, c.title, c.title_normalized, v.pack_id
            FROM cards c
            JOIN card_versions v ON v.game_id = c.game_id AND v.card_id = c.id
            JOIN packs p ON p.game_id = v.game_id AND p.id = v.pack_id
            WHERE c.game_id = ? AND c.title_normalized IN ({marks})
            ORDER BY p.sort_key IS NULL, p.sort_key DESC, c.id""",
        [game_id, *normalized],
    )
    resolved = {}
    for row in rows:
        resolved.setdefault(row["title_normalized"], row)
    return resolved


def item_requests(conn: sqlite3.Connection, game_id: str,
                  items: list[tuple[str, str | None, int | None, int]]) -> tuple[list[CardRequest], list[str]]:
    """Proxy-list entries -> requests. Each entry is (card_id, pack_id,
    position, quantity) and pins that exact printing, so a card reprinted in
    several packs prints the art of the pack it was picked from."""
    ids = list({card_id for card_id, *_ in items})
    titles = {}
    if ids:
        marks = ",".join("?" * len(ids))
        titles = {r["id"]: r["title"] for r in conn.execute(
            f"SELECT id, title FROM cards WHERE game_id = ? AND id IN ({marks})", [game_id, *ids])}
    requests, not_found = [], []
    for card_id, pack_id, position, qty in items:
        if card_id not in titles:
            if card_id not in not_found:
                not_found.append(card_id)
            continue
        requests.extend([CardRequest(titles[card_id], card_id, pack_id, position)] * qty)
    return requests, not_found


def pack_cards(conn: sqlite3.Connection, game_id: str, pack_id: str) -> list[tuple[CardRequest, int]]:
    """(request, quantity in the pack) for each card in a pack, in pack order.
    The quantity is the card's own count where the catalog has one (Arkham and
    Marvel do), else 1."""
    rows = conn.execute(
        """SELECT c.id, c.title, c.attrs, v.position FROM card_versions v
           JOIN cards c ON c.game_id = v.game_id AND c.id = v.card_id
           WHERE v.game_id = ? AND v.pack_id = ?
           ORDER BY v.position, c.id""",
        (game_id, pack_id),
    )
    out = []
    for r in rows:
        qty = (json.loads(r["attrs"]) if r["attrs"] else {}).get("quantity")
        out.append((CardRequest(r["title"], r["id"], pack_id, r["position"]),
                    qty if isinstance(qty, int) and qty > 0 else 1))
    return out


def pack_requests(conn: sqlite3.Connection, game_id: str, pack_id: str) -> list[CardRequest]:
    return [request for request, _ in pack_cards(conn, game_id, pack_id)]


def available_printings(conn, game_id, titles) -> dict[str, list[Printing]]:
    """Normalized title -> every printing some collection has an image of."""
    normalized = list({normalize_title(t) for t in titles})
    if not normalized:
        return {}
    marks = ",".join("?" * len(normalized))
    rows = conn.execute(
        f"""SELECT i.*, c.title, c.title_normalized, c.back_group, c.attrs, p.name AS pack_name, p.sort_key
            FROM images i
            JOIN cards c ON c.game_id = i.game_id AND c.id = i.card_id
            LEFT JOIN packs p ON p.game_id = i.game_id AND p.id = i.pack_id
            WHERE i.game_id = ? AND c.title_normalized IN ({marks})""",
        [game_id, *normalized],
    )
    printings: dict[tuple, Printing] = {}
    sides: dict[tuple, dict[str, Side]] = {}
    attrs: dict[tuple, dict | None] = {}
    for r in rows:
        key = (r["title_normalized"], r["card_id"], r["pack_id"], r["variant"], r["collection"], r["position"])
        if key not in printings:
            printings[key] = Printing(
                r["card_id"], r["title"], r["collection"], r["pack_id"], r["pack_name"],
                r["variant"], r["position"], r["back_group"], r["sort_key"],
            )
            sides[key] = {}
            attrs[key] = json.loads(r["attrs"]) if r["attrs"] else None
        side = sides[key].setdefault(
            r["side"], Side(rotate=display_rotation(game_id, attrs[key], r["side"])))
        if r["has_bleed"]:
            side.bleed_image = str(image_path(r["file_path"]))
        else:
            side.image = str(image_path(r["file_path"]))

    by_title: dict[str, list[Printing]] = {}
    for key, printing in printings.items():
        found = sides[key]
        printing.front = found.get("front", Side())
        printing.backs = [found[s] for s in sorted(found, key=side_sort_key) if s != "front"]
        by_title.setdefault(key[0], []).append(printing)
    return by_title


def select_printing(request: CardRequest, printings: list[Printing]) -> Printing:
    """The printing with the fewest mismatches against what was asked for."""
    def rank(p: Printing):
        return (
            request.printing is not None and request.printing not in (p.pack_id, p.variant),
            request.collection is not None and request.collection != p.collection,
            request.position is not None and request.position != p.position,
            p.card_id != request.card_id,
            not p.is_official,
            p.sort_key is None,
            p.sort_key or "",
        )
    return min(printings, key=rank)


def pick_printings(conn: sqlite3.Connection, game_id: str, requests: list[CardRequest]) -> list[Printing | None]:
    """The printing to print for each request, or None where no collection
    has an image of the card."""
    available = available_printings(conn, game_id, {r.title for r in requests})
    return [select_printing(r, options) if (options := available.get(normalize_title(r.title))) else None
            for r in requests]


def resolve(conn: sqlite3.Connection, game_id: str, requests: list[CardRequest], not_found=()) -> Resolution:
    chosen, no_image = [], []
    for request, printing in zip(requests, pick_printings(conn, game_id, requests)):
        if printing:
            chosen.append(printing)
        elif request.title not in no_image:
            no_image.append(request.title)
    return Resolution(chosen, list(not_found), no_image)
