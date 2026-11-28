"""Arkham Horror: The Card Game.

Card and pack data comes from ArkhamDB's public API. The first
`tcgnecro catalog ahlcg` downloads it into data/cache/ahlcg/; later runs use
that copy unless you pass `--refresh` (do that after a new expansion is
released, see docs/UPDATING_GAMES.md).

Ported from upstream's games/ahlcg/adapter.rs. The parts that matter for
compatibility with upstream collections:

- card ids are ArkhamDB codes ("01001"), pack ids ArkhamDB pack codes ("core"),
  so collection images are named e.g. `01001@core.jpg`
- titles are made unique per card (see `card_titles`), because decklists and
  printing lookups resolve by title
- repackaged expansions ("The Dunwich Legacy Investigator Expansion") get
  versions for the cards they reprint, so images named after them resolve
"""

import json
import logging
import urllib.request
from pathlib import Path

from tcgnecro import db
from tcgnecro.games import Card, CardVersion, Catalog, Pack

log = logging.getLogger(__name__)

GAME_ID = "ahlcg"
NAME = "Arkham Horror: The Card Game"
# Shown on the game's page (/games/<id>).
DESCRIPTION = (
    "A cooperative Living Card Game of investigators facing cosmic horror in 1920s New England. Each "
    "player builds a deck around an investigator and plays linked scenarios through a campaign, "
    "carrying experience and trauma from one game to the next."
)
# Logo tile used until static/logos/<id>.(svg|png|webp|jpg) exists: title, subtitle, background, text.
LOGO_TILE = ('Arkham Horror', 'The Card Game', '#173404', '#C0DD97')

API = "https://arkhamdb.com/api/public"
USER_AGENT = "tcgnecro (+https://github.com/mcwookie/tcg-necropolis)"

PLAYER_TYPES = {"investigator", "asset", "event", "skill"}
ENCOUNTER_TYPES = {"enemy", "enemy_location", "treachery", "agenda", "act", "location",
                   "scenario", "story", "key"}

# The card fields kept for the deck builder. ArkhamDB sends more (URLs, OCTGN
# ids, image paths) that nothing here uses.
KEPT_FIELDS = {
    "code", "name", "real_name", "subname", "type_code", "subtype_code",
    "faction_code", "faction2_code", "faction3_code", "pack_code", "pack_name", "position",
    "quantity", "xp", "cost", "text", "back_name", "back_text", "flavor", "traits", "real_slot",
    "skill_willpower", "skill_intellect", "skill_combat", "skill_agility", "skill_wild",
    "health", "sanity", "deck_limit", "is_unique", "exceptional", "myriad", "permanent",
    "restrictions", "deck_options", "deck_requirements", "tags", "duplicate_of_code",
    "alternate_of_code", "bonded_to", "encounter_code", "double_sided",
    "customization_text",
}


def cache_dir() -> Path:
    return db.data_dir() / "cache" / GAME_ID


def fetch_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def load_source(refresh: bool = False) -> tuple[list[dict], list[dict]]:
    """(packs, cards) as ArkhamDB returns them, from the cache when present."""
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
    # Taboo lists ride along with the card data but are only used by the deck
    # builder, so failing to fetch them doesn't stop the catalog loading.
    if refresh or not taboos_file().exists():
        try:
            download_taboos()
        except Exception as e:  # network, ArkhamDB down
            log.warning("Couldn't download ArkhamDB taboo lists: %s", e)
    return packs, cards


# ----- taboo lists --------------------------------------------------------------
#
# FFG's taboo lists change player cards for balance: extra (chained) or fewer
# (unchained) experience, forbidden cards, and rewritten text or deckbuilding.
# ArkhamDB publishes them at /api/public/taboos/; a deck names the list it
# follows by its numeric id (`taboo_id`). The deck builder applies them
# (games/ahlcg/deckbuilder.py); this part only fetches and reads them.

def taboos_file() -> Path:
    return cache_dir() / "taboos.json"


def download_taboos() -> None:
    data = fetch_json(f"{API}/taboos/")
    cache_dir().mkdir(parents=True, exist_ok=True)
    taboos_file().write_text(json.dumps(data))


def normalize_taboos(data: list[dict]) -> dict[int, dict]:
    """id -> {"id", "code", "name", "date_start", "active", "cards": {code: entry}}.

    ArkhamDB's API sends each list's `cards` as a JSON string and always gives
    an `id`; the arkhamdb-json-data repository has `cards` as a list and ids
    only on recent lists, so the id falls back to the numeric code ("008" -> 8).
    """
    out = {}
    for taboo in data or []:
        cards = taboo.get("cards") or []
        if isinstance(cards, str):
            cards = json.loads(cards) if cards.strip() else []
        try:
            taboo_id = int(taboo.get("id") or taboo["code"])
        except (KeyError, TypeError, ValueError):
            continue
        out[taboo_id] = {
            "id": taboo_id,
            "code": taboo.get("code") or f"{taboo_id:03d}",
            "name": taboo.get("name") or "Taboo List",
            "date_start": taboo.get("date_start") or "",
            "active": bool(taboo.get("active", True)),
            "cards": {str(e["code"]): e for e in cards if isinstance(e, dict) and e.get("code")},
        }
    return out


_taboo_cache: tuple[float, dict[int, dict]] | None = None


def load_taboos() -> dict[int, dict]:
    """The downloaded taboo lists (see normalize_taboos), re-read when the
    cache file changes. Empty until `tcgnecro catalog ahlcg` has fetched them;
    never downloads from here, so page loads stay offline."""
    global _taboo_cache
    path = taboos_file()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _taboo_cache is None or _taboo_cache[0] != mtime:
        try:
            taboos = normalize_taboos(json.loads(path.read_text()))
        except (OSError, ValueError) as e:
            log.warning("Couldn't read %s: %s", path, e)
            taboos = {}
        _taboo_cache = (mtime, taboos)
    return _taboo_cache[1]


# Landscape card types. Collections store them sideways (turned 90° clockwise)
# so they fit a portrait print slot, on every side.
LANDSCAPE_TYPES = {"investigator", "act", "agenda"}


def display_rotation(attrs: dict | None, side: str) -> int:
    """Turn landscape cards 90° counter-clockwise (270° clockwise) on screen."""
    return 270 if attrs and attrs.get("type_code") in LANDSCAPE_TYPES else 0


def back_group_for(card: dict) -> str | None:
    """Which generic card back a card takes: player or encounter."""
    if card.get("subtype_code") in ("weakness", "basicweakness"):
        return "player"
    if card["type_code"] in PLAYER_TYPES:
        return "player"
    if card["type_code"] in ENCOUNTER_TYPES:
        return "encounter"
    return None


def display_title(card: dict) -> str:
    """`name` is shared by every level of an upgradeable card, so the level
    is spelled out: "Shrivelling (5)"."""
    xp = card.get("xp")
    return f"{card['name']} ({xp})" if xp else card["name"]


def same_card_groups(cards: list[dict]) -> dict[str, str]:
    """code -> lowest code of the reprint group it belongs to. ArkhamDB's
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


def reprint_versions(packs: list[dict], cards: list[dict]) -> list[CardVersion]:
    """ArkhamDB files every card under its original pack. The repackaged
    campaign/investigator expansions get versions here so their images resolve:
    encounter cards go to the campaign box, the rest to the investigator box."""
    versions = []
    for pack in packs:
        reprint_type = pack.get("reprint_type")
        if not reprint_type:
            continue
        campaign_half = reprint_type == "campaign"
        reprinted = set(pack.get("reprint_packs") or [])
        for card in cards:
            if card["pack_code"] in reprinted and bool(card.get("encounter_code")) == campaign_half:
                versions.append(CardVersion(card["code"], pack["code"], card["position"]))
    return versions


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


def cycle_labels(packs: list[dict]) -> dict[int, str]:
    """cycle_position -> a readable group name for the Collection page."""
    labels = {}
    for pack in sorted(packs, key=lambda p: (p.get("cycle_position", 0), p.get("position", 0))):
        cycle = pack.get("cycle_position", 0)
        if cycle not in labels:
            labels[cycle] = CYCLE_LABELS.get(cycle) or pack["name"].replace(" Investigator Expansion", "")
    return labels


# Custom cards (data/custom/ahlcg/, see games/custom.py) use ArkhamDB's fields.
CUSTOM_REQUIRED = ("code", "name", "pack_code", "position", "type_code")
CUSTOM_PACK_REQUIRED = ("code", "name")


def load_catalog(refresh: bool = False) -> Catalog:
    return build_catalog(*load_source(refresh))


def build_catalog(packs: list[dict], cards: list[dict]) -> Catalog:
    """The catalog from ArkhamDB-style packs and cards (official plus custom)."""
    titles = card_titles(cards)

    catalog = Catalog(GAME_ID, NAME)
    groups = cycle_labels(packs)
    catalog.packs = [
        Pack(p["code"], p["name"], p.get("available") or None,
             group=groups.get(p.get("cycle_position", 0)), group_order=p.get("cycle_position", 0))
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
    catalog.versions += reprint_versions(packs, cards)
    return catalog
