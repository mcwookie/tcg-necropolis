"""Custom cards and packs: content a game's official data doesn't have
(fan-made scenarios, homebrew investigators, promos, a missing card).

Put them in the data folder, one folder per game:

    data/custom/<game id>/packs.json   a JSON list of packs
    data/custom/<game id>/cards.json   a JSON list of cards

Either file may be left out (cards can go in an official pack). Records use the
game's own format, the same fields its official data has, so custom cards go
through exactly the same processing: titles, card backs, deck builder rules,
image orientation. Each game module says which fields are required
(CUSTOM_REQUIRED, CUSTOM_PACK_REQUIRED) and which identify a card
(CUSTOM_ID_FIELD, default "code"; the pack is CUSTOM_PACK_FIELD, default
"pack_code"). docs/CUSTOM_CARDS.md has a worked example for every game.

Images go in any collection for the game, named as usual:
{card id}@{pack code}[~back].ext.

A custom record that's malformed, reuses an official code, or names an unknown
pack is skipped with a warning; it never stops the official cards loading
(the Docker container loads catalogs on every start).

Games whose module doesn't split loading into load_source/build_catalog get a
generic format instead (merge_catalog): cards {"id", "name", "pack",
"position", "back_group", ...anything else becomes attrs}, packs {"code",
"name", "sort_key"}.
"""

import json
from pathlib import Path

from tcgnecro import db

def custom_dir(game_id: str) -> Path:
    return db.data_dir() / "custom" / game_id


def _read_list(path: Path, warn) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        warn(f"{path}: not readable JSON ({e}); skipped")
        return []
    if not isinstance(data, list):
        warn(f"{path}: should be a JSON list of records; skipped")
        return []
    out = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            out.append(item)
        else:
            warn(f"{path}: entry {i + 1} isn't a JSON object; skipped")
    return out


def read(game_id: str, warn) -> tuple[list[dict], list[dict]]:
    """(custom packs, custom cards) as written, unchecked."""
    folder = custom_dir(game_id)
    return _read_list(folder / "packs.json", warn), _read_list(folder / "cards.json", warn)


def _missing(record: dict, required) -> list[str]:
    return [f for f in required if record.get(f) in (None, "")]


def _describe(record: dict, id_field: str) -> str:
    return str(record.get(id_field) or record.get("name") or record)[:60]


def merge(game_id: str, module, packs: list[dict], cards: list[dict], warn) -> tuple[list[dict], list[dict], int]:
    """Official packs and cards plus the custom ones that pass the checks.
    Returns (packs, cards, number of custom cards added). The official lists
    aren't modified."""
    custom_packs, custom_cards = read(game_id, warn)
    if not custom_packs and not custom_cards:
        return packs, cards, 0
    id_field = getattr(module, "CUSTOM_ID_FIELD", "code")
    pack_field = getattr(module, "CUSTOM_PACK_FIELD", "pack_code")
    title_field = getattr(module, "CUSTOM_TITLE_FIELD", None)
    required = getattr(module, "CUSTOM_REQUIRED", (id_field, "name", pack_field))
    pack_required = getattr(module, "CUSTOM_PACK_REQUIRED", ("code", "name"))
    where = f"custom {game_id}"

    pack_codes = {p.get("code") for p in packs}
    new_packs = []
    for p in custom_packs:
        missing = _missing(p, pack_required)
        if missing:
            warn(f"{where} pack {_describe(p, 'code')}: missing {', '.join(missing)}; skipped")
        elif p["code"] in pack_codes:
            warn(f"{where} pack {p['code']}: an official pack has that code; skipped "
                 "(custom cards can use the official pack as it is)")
        else:
            pack_codes.add(p["code"])
            new_packs.append(dict(p))

    ids = {str(c.get(id_field)) for c in cards}
    titles = {db.normalize_title(str(c.get(title_field) or c.get("name") or "")) for c in cards} if title_field else set()
    new_cards = []
    for c in custom_cards:
        missing = _missing(c, required)
        if missing:
            warn(f"{where} card {_describe(c, id_field)}: missing {', '.join(missing)}; skipped")
            continue
        card_id = str(c[id_field])
        if card_id in ids:
            warn(f"{where} card {card_id}: that id is already used (by an official card or another custom one); "
                 "skipped. Give custom cards their own prefix, e.g. zz001")
            continue
        if c[pack_field] not in pack_codes:
            warn(f"{where} card {card_id}: no pack '{c[pack_field]}' (add it to packs.json); skipped")
            continue
        card = dict(c)
        card[id_field] = card_id
        if title_field:
            # Titles are unique per game (decklists resolve by them); a clash
            # with another card's title gets the card id added.
            title = str(card.get(title_field) or card["name"])
            if db.normalize_title(title) in titles:
                title = f"{title} ({card_id})"
            card[title_field] = title
            titles.add(db.normalize_title(title))
        ids.add(card_id)
        new_cards.append(card)
    return packs + new_packs, cards + new_cards, len(new_cards)


def merge_catalog(game_id: str, catalog, warn) -> int:
    """The generic format, for games without load_source/build_catalog: adds
    the custom packs and cards straight into a built Catalog. Returns the
    number of cards added."""
    from tcgnecro.games import Card, CardVersion, Pack

    custom_packs, custom_cards = read(game_id, warn)
    where = f"custom {game_id}"
    pack_codes = {p.id for p in catalog.packs}
    for p in custom_packs:
        missing = _missing(p, ("code", "name"))
        if missing:
            warn(f"{where} pack {_describe(p, 'code')}: missing {', '.join(missing)}; skipped")
        elif p["code"] in pack_codes:
            warn(f"{where} pack {p['code']}: an official pack has that code; skipped")
        else:
            pack_codes.add(p["code"])
            catalog.packs.append(Pack(p["code"], p["name"], str(p.get("sort_key") or "99")))
    ids = {c.id for c in catalog.cards}
    titles = {db.normalize_title(c.title) for c in catalog.cards}
    added = 0
    for c in custom_cards:
        missing = _missing(c, ("id", "name", "pack"))
        if missing:
            warn(f"{where} card {_describe(c, 'id')}: missing {', '.join(missing)}; skipped")
            continue
        card_id = str(c["id"])
        if card_id in ids:
            warn(f"{where} card {card_id}: that id is already used; skipped")
            continue
        if c["pack"] not in pack_codes:
            warn(f"{where} card {card_id}: no pack '{c['pack']}'; skipped")
            continue
        title = str(c["name"])
        if db.normalize_title(title) in titles:
            title = f"{title} ({card_id})"
        attrs = {k: v for k, v in c.items() if k not in ("id", "pack", "position", "back_group")}
        catalog.cards.append(Card(card_id, title, c.get("back_group"), attrs))
        position = c.get("position")
        catalog.versions.append(CardVersion(card_id, c["pack"], position if isinstance(position, int) else None))
        ids.add(card_id)
        titles.add(db.normalize_title(title))
        added += 1
    return added
