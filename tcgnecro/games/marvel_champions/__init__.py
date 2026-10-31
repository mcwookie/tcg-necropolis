"""Marvel Champions: The Card Game.

Card and pack data comes from MarvelCDB's public API. The first
`tcgnecro catalog marvel_champions` downloads it into
data/cache/marvel_champions/; later runs use that copy unless you pass
`--refresh` (do that after a new expansion is released, see
docs/UPDATING_GAMES.md).

Ported from upstream's games/marvel_champions/adapter.rs, so collections and
decklists made for upstream work here:

- card ids are MarvelCDB codes ("01001a"), pack ids MarvelCDB pack codes
  ("core"), so collection images are named e.g. `01001a@core.jpg`, with the
  second face as `01001a@core~back.jpg`
- a reprint is a version of the card it reprints, not a card of its own; its
  own code is kept as the version's api_id, so images named by either code
  match (see `build`)
- only cards printed as cards of their own are kept (see `printed_cards`)
- titles are unique per card (see `card_titles`), because decklists and
  printing lookups resolve by title
"""

import json
import urllib.request
from collections import Counter
from pathlib import Path

from tcgnecro import db
from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "marvel_champions"
NAME = "Marvel Champions"

API = "https://marvelcdb.com/api/public"
USER_AGENT = "tcgnecro (+https://github.com/mcwookie/tcg-necropolis)"

# Which generic card back a card takes, by type_code.
PLAYER_TYPES = {"hero", "alter_ego", "ally", "event", "upgrade", "support", "resource",
                "player_side_scheme"}
VILLAIN_TYPES = {"villain"}
ENCOUNTER_TYPES = {"minion", "main_scheme", "side_scheme", "attachment", "treachery",
                   "obligation", "environment", "leader",
                   "evidence_means", "evidence_motive", "evidence_opportunity"}

# Landscape card types. Marvel Champions collections store them sideways,
# turned 90° counter-clockwise (the opposite of Arkham's). Rotation is decided
# per face: the back of a two-sided card can be another type entirely
# ("Open the Dungeons", a side scheme, has the attachment Jormungand on its back).
LANDSCAPE_TYPES = {"main_scheme", "side_scheme", "player_side_scheme"}

# The card fields kept for a future deck builder. MarvelCDB sends more (URLs,
# OCTGN ids, image paths) that nothing here uses.
KEPT_FIELDS = {
    "code", "name", "real_name", "subname", "traits", "text", "real_text", "flavor",
    "pack_code", "pack_name", "pack_legacy", "position", "set_position", "quantity",
    "type_code", "type_name", "faction_code", "faction_name", "stage",
    "card_set_code", "card_set_name", "card_set_type_name_code",
    "cost", "cost_per_hero", "cost_star", "resource_energy", "deck_limit", "is_unique",
    "permanent", "double_sided", "duplicate_of_code", "linked_to_code", "back_type_code",
    "health_per_group", "health_per_hero", "base_threat_fixed", "base_threat_per_group",
    "base_threat_star", "escalation_threat_fixed", "threat_fixed", "threat_per_group",
    "attack_star", "thwart_star", "defense_star", "health_star", "recover_star",
    "scheme_star", "boost_star", "threat_star", "escalation_threat_star",
}


def cache_dir() -> Path:
    return db.data_dir() / "cache" / GAME_ID


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def load_source(refresh: bool = False) -> tuple[list[dict], list[dict]]:
    """(packs, cards) as MarvelCDB returns them, hidden cards included (see
    printed_cards), from the cache when present."""
    cache = cache_dir()
    packs_file, cards_file = cache / "packs.json", cache / "cards.json"
    if refresh or not (packs_file.exists() and cards_file.exists()):
        cache.mkdir(parents=True, exist_ok=True)
        packs = fetch_json(f"{API}/packs/")
        cards = fetch_json(f"{API}/cards/?encounter=1")
        packs_file.write_text(json.dumps(packs))
        cards_file.write_text(json.dumps(cards))
    return json.loads(packs_file.read_text()), json.loads(cards_file.read_text())


def display_rotation(attrs: dict | None, side: str) -> int:
    """Turn landscape faces 90° clockwise on screen (games.display_rotation).
    A back face goes by its own type (back_type_code) when it has one."""
    if not attrs:
        return 0
    type_code = (attrs.get("back_type_code") if side != "front" else None) or attrs.get("type_code")
    return 90 if type_code in LANDSCAPE_TYPES else 0


def back_group_for(card: dict) -> str | None:
    """Which generic card back a card takes: player, villain or encounter."""
    type_code = card["type_code"]
    if type_code in PLAYER_TYPES:
        return "player"
    if type_code in VILLAIN_TYPES:
        return "villain"
    if type_code in ENCOUNTER_TYPES:
        return "encounter"
    return None


def printed_cards(cards: list[dict]) -> list[dict]:
    """The cards printed as cards of their own.

    A hidden card is the second face of the card linking to it, and its image
    is that card's `~back`. SP//dr's Peni Parker is the exception: both of her
    faces are hidden and no card links to her, so she is kept as the front.

    MarvelCDB also lists most two-sided main schemes a second time under a
    plain code (`01097` beside the linked `01097a` and `01097b`). The linked
    pair describes both faces, so the plain code is dropped.
    """
    backs = {c["linked_to_code"] for c in cards if c.get("linked_to_code")}
    paired = {c["code"][:-1] for c in cards if c.get("linked_to_code") and c["code"].endswith("a")}
    kept = []
    for card in cards:
        if card.get("hidden"):
            if card.get("linked_to_code") and card["code"] not in backs:
                kept.append(card)
        elif card["code"] not in paired:
            kept.append(card)
    return kept


def original(by_code: dict[str, dict], card: dict) -> dict:
    """The card a reprint reprints (following chains), or the card itself."""
    current = card
    for _ in range(len(by_code)):
        nxt = by_code.get(current.get("duplicate_of_code") or "")
        if nxt is None:
            break
        current = nxt
    return current


def card_titles(cards: list[dict]) -> dict[str, str]:
    """One title per code, spelling apart titles that different cards share.

    Reprints share their original's title. Within a shared title, a villain or
    scheme stage names the card when no other card there has the same stage
    ("Rhino (II)"). The lowest code without one keeps the plain title, and the
    rest are spelled out by pack and position ("Endurance (core 40)"), or by
    code where two faces share a position.
    """
    by_code = {c["code"]: c for c in cards}
    sharing: dict[str, dict[str, dict]] = {}
    for card in cards:
        first = original(by_code, card)
        sharing.setdefault(db.normalize_title(card["name"]), {})[first["code"]] = first

    suffixes: dict[str, str] = {}
    for group in sharing.values():
        if len(group) < 2:
            continue
        stages = Counter(c["stage"] for c in group.values() if c.get("stage"))
        positions = Counter((c["pack_code"], c["position"]) for c in group.values())
        plain_taken = False
        for code in sorted(group):
            card = group[code]
            stage = card.get("stage")
            if stage and stages[stage] == 1:
                suffixes[code] = stage
            elif not plain_taken:
                plain_taken = True
            elif positions[(card["pack_code"], card["position"])] == 1:
                suffixes[code] = f"{card['pack_code']} {card['position']}"
            else:
                suffixes[code] = code

    titles = {}
    for card in cards:
        suffix = suffixes.get(original(by_code, card)["code"])
        titles[card["code"]] = f"{card['name']} ({suffix})" if suffix else card["name"]
    return titles


def build(cards: list[dict]) -> tuple[list[Card], list[CardVersion]]:
    """Catalog cards and versions from printed cards. A reprint becomes a
    version of its original under the reprint's pack, with the reprint's own
    code as api_id, which is what collection filenames name it by."""
    titles = card_titles(cards)
    by_code = {c["code"]: c for c in cards}
    catalog_cards, versions = [], []
    for card in cards:
        first = original(by_code, card)
        if first["code"] == card["code"]:
            catalog_cards.append(Card(
                card["code"],
                titles[card["code"]],
                back_group_for(card),
                {k: v for k, v in card.items() if k in KEPT_FIELDS},
            ))
        versions.append(CardVersion(first["code"], card["pack_code"], card.get("position"),
                                    api_id=card["code"]))
    return catalog_cards, versions


def with_back_types(cards: list[dict]) -> list[dict]:
    """Copies of the cards with `back_type_code`: the type of the hidden face
    a two-sided card links to (its ~back image), for per-face rotation."""
    by_code = {c["code"]: c for c in cards}
    out = []
    for card in cards:
        back = by_code.get(card.get("linked_to_code") or "")
        out.append(card | {"back_type_code": back["type_code"]} if back else card)
    return out


def load_catalog(refresh: bool = False) -> Catalog:
    packs, cards = load_source(refresh)
    cards = with_back_types(cards)
    catalog = Catalog(GAME_ID, NAME)
    # MarvelCDB packs carry no cycle, so the Collection page lists them by release date.
    catalog.packs = [Pack(p["code"], p["name"], p.get("available") or None) for p in packs]
    catalog.cards, catalog.versions = build(printed_cards(cards))
    return catalog
