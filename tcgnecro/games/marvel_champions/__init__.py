"""Marvel Champions.

Card and pack data comes from marvelcdb's public API. The first
`tcgnecro catalog marvel_champions` downloads it into data/cache/marvel_champions/; later runs use
that copy unless you pass `--refresh` (do that after a new expansion is
released, see docs/UPDATING_GAMES.md).

Ported from upstream's games/marvel_champions/adapter.rs. The parts that matter for
compatibility with upstream collections:

- card ids are MarvelCDB codes ("01001"), pack ids MarvelCDB pack codes ("core"),
  so collection images are named e.g. `01001@core.jpg`
- titles are made unique per card (see `card_titles`), because decklists and
  printing lookups resolve by title
"""

import json
import urllib.request
from pathlib import Path

from tcgnecro import db
from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "marvel_champions"
NAME = "Marvel Champions"

API = "https://marvelcdb.com/api/public"
USER_AGENT = "tcgnecro (+https://github.com/mcwookie/tcg-necropolis)"

PLAYER_TYPES = {"hero",
        "alter_ego",
        "ally",
        "event",
        "upgrade",
        "support",
        "resource",
        "player_side_scheme"}

VILLAIN_TYPES = {"villain"}

ENCOUNTER_TYPES = {"minion",
        "main_scheme",
        "side_scheme",
        "attachment",
        "treachery",
        "obligation",
        "environment",
        "leader",
        "evidence_means",
        "evidence_motive",
        "evidence_opportunity"}

# The card fields kept for the deck builder. MarvelCDB sends more (URLs, OCTGN
# ids, image paths) that nothing here uses.
KEPT_FIELDS = {
    "pack_code", "pack_name", "pack_legacy", "type_code", "type_name", "faction_code", "faction_name", 
    "card_set_code", "card_set_name", "card_set_type_name_code", "position", "set_position", 
    "code", "name", "real_name", "cost", "cost_per_hero", "cost_star", "text", "real_text", 
    "quantity", "resource_energy", "health_per_group", "health_per_hero", "base_threat_fixed", 
    "base_threat_per_group", "base_threat_star", "escalation_threat_fixed", "threat_fixed", 
    "threat_per_group", "deck_limit", "flavor", "is_unique", "hidden", "permanent", "double_sided", 
    "attack_star", "thwart_star", "defense_star", "health_star", "recover_star", 
    "scheme_star", "boost_star", "threat_star", "escalation_threat_star"}

def cache_dir() -> Path:
    return db.data_dir() / "cache" / GAME_ID


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def load_source(refresh: bool = False) -> tuple[list[dict], list[dict]]:
    """(packs, cards) as MarvelCDB returns them, from the cache when present."""
    cache = cache_dir()
    packs_file, cards_file = cache / "packs.json", cache / "cards.json"
    if refresh or not (packs_file.exists() and cards_file.exists()):
        cache.mkdir(parents=True, exist_ok=True)
        packs = fetch_json(f"{API}/packs/")
        cards = fetch_json(f"{API}/cards/?encounter=1")
        packs_file.write_text(json.dumps(packs))
        cards_file.write_text(json.dumps(cards))
    packs = json.loads(packs_file.read_text())
    cards = [c for c in json.loads(cards_file.read_text()) if not c.get("hidden")]
    return packs, cards


# Landscape card types. Collections store them sideways (turned 90° clockwise)
# so they fit a portrait print slot, on every side.
LANDSCAPE_TYPES = {"side_scheme", "main_scheme"}


def display_rotation(attrs: dict | None, side: str) -> int:
    """Turn landscape cards 90° counter-clockwise (270° clockwise) on screen."""
    return 90 if attrs and attrs.get("type_code") in LANDSCAPE_TYPES else 0


def back_group_for(card: dict) -> str | None:
    """Which generic card back a card takes: player or encounter."""
    if card["type_code"] in PLAYER_TYPES:
        return "player"
    if card["type_code"] in VILLAIN_TYPES:
        return "villain"
    if card["type_code"] in ENCOUNTER_TYPES:
        return "encounter"
    return None


def display_title(card: dict) -> str:
    """Multiple villain card stages share the same `name`, so the stage
    is spelled out: "Klaw (II)"."""
    return f"{card['name']} ({card['set_position']})" if card["type_code"] in VILLAIN_TYPES else card["name"]


def same_card_groups(cards: list[dict]) -> dict[str, str]:
    """code -> lowest code of the reprint group it belongs to. MarvelCDB's
    `duplicated_by` says two codes are the same card printed twice."""
    adjacent: dict[str, list[str]] = {}
    for card in cards:
        for reprint in card.get("duplicated_by") or []:
            adjacent.setdefault(card["code"], []).append(reprint)
            adjacent.setdefault(reprint, []).append(card["code"])

    group_of: dict[str, str] = {}
    for card in cards:
        if card["code"] in group_of:
            continue
        group, seen, pending = [], {card["code"]}, [card["code"]]
        while pending:
            code = pending.pop()
            group.append(code)
            for nxt in adjacent.get(code, []):
                if nxt not in seen:
                    seen.add(nxt)
                    pending.append(nxt)
        label = min(group)
        for code in group:
            group_of[code] = label
    return group_of


def card_titles(cards: list[dict]) -> dict[str, str]:
    """One title per code, spelling apart titles that several different
    cards share. Reprints keep a shared title (they're one card). Where
    subtitles don't separate them, the lowest code keeps the plain title and
    the others get "(pack position)" or, failing that, "(code)" appended."""
    groups = same_card_groups(cards)
    sharing: dict[str, list[dict]] = {}
    for card in cards:
        sharing.setdefault(db.normalize_title(display_title(card)), []).append(card)

    titles = {}
    for group in sharing.values():
        distinct = {groups.get(c["code"], c["code"]) for c in group}
        if len(distinct) < 2:
            for card in group:
                titles[card["code"]] = display_title(card)
            continue

        subnames = {groups.get(c["code"], c["code"]): c.get("subname") or "" for c in group}
        by_subname = all(subnames.values()) and len(set(subnames.values())) == len(distinct)
        plain = min(distinct)
        for card in group:
            base = display_title(card)
            mine = groups.get(card["code"], card["code"])
            if not by_subname and mine == plain:
                titles[card["code"]] = base
                continue
            shares_position = any(
                groups.get(o["code"], o["code"]) != mine
                and o["pack_code"] == card["pack_code"] and o["position"] == card["position"]
                for o in group
            )
            if by_subname:
                suffix = card.get("subname") or ""
            elif shares_position:
                suffix = card["code"]
            else:
                suffix = f"{card['pack_code']} {card['position']}"
            titles[card["code"]] = f"{base} ({suffix})"
    return titles

# Labels for ArkhamDB's cycle numbers that aren't a campaign. Campaign cycles
# are named after their first pack, so new ones need no entry here.
CYCLE_LABELS = {
    50: "Return to…",
    60: "Investigator Starter Decks",
    61: "Investigator Starter Decks (2026)",
    70: "Standalone scenarios",
    80: "Novellas, books and promos",
    90: "Parallel investigators",
}

# def cycle_labels(packs: list[dict]) -> dict[int, str]:
#     """cycle_position -> a readable group name for the Collection page."""
#     labels = {}
#     for pack in sorted(packs, key=lambda p: (p.get(p.get("position", 0))):
#         cycle = pack.get("cycle_position", 0)
#         if cycle not in labels:
#             labels[cycle] = CYCLE_LABELS.get(cycle) or pack["name"].replace(" Investigator Expansion", "")
#     return labels


def load_catalog(refresh: bool = False) -> Catalog:
    packs, cards = load_source(refresh)
    titles = card_titles(cards)

    catalog = Catalog(GAME_ID, NAME)
    # groups = cycle_labels(packs)
    catalog.packs = [
        Pack(p["code"], p["name"], p.get("available") or None,
             ) # group=groups.get(p.get("cycle_position", 0)), group_order=p.get("cycle_position", 0))
        for p in packs
    ]
    for card in cards:
        catalog.cards.append(Card(
            card["code"],
            titles.get(card["code"], display_title(card)),
            back_group_for(card),
            {k: v for k, v in card.items() if k in KEPT_FIELDS},
        ))
        catalog.versions.append(CardVersion(card["code"], card["pack_code"], card["position"]))
    # catalog.versions += reprint_versions(packs, cards)
    return catalog
