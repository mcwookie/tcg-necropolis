"""Star Wars LCG. The catalog is static JSON bundled alongside this module,
since there is no maintained online source. Edit the JSON to add or correct
packs, then run `tcgnecro catalog swlcg` (see docs/UPDATING_GAMES.md).

swlcg_packs.json, one object per pack:
    code       pack id, used in image names ("core-set")
    name       display name
    position   release order; newer packs have higher numbers

swlcg_cards.json, one object per card:
    unique_id               card id, used in image names ("0235")
    name                    printed name
    label                   unique title used by decklists; add "(set.number)"
                            when a name repeats: "Darth Vader (234.2)"
    pack_code               the pack's code
    card_type               Affiliation, Enhancement, Event, Fate, Mission, Objective, Unit
    side                    Light or Dark (picks the generic card back)
    affiliation             e.g. Jedi, Sith, Neutral
    objective_set_number    objective set the card belongs to, or null
    objective_set_sequence  position within the set (1-6), or null
    affiliation_lock        the affiliation an objective is locked to
                            ("[Jedi] affiliation only"), or null
    is_unique               unique card
    is_limited              objective says "Limit 1 per objective deck"
    cost, force_icons, resource_value, damage_capacity, edge_priority,
    combat_icons, edge_combat_icons ({unit, blast, tactics}), traits, text
                            printed values, for the deck builder; null when
                            the card has none

The rules fields came from the community swlcg-json data set.
"""

import json
from pathlib import Path

from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "swlcg"
NAME = "Star Wars LCG"

HERE = Path(__file__).parent


# Landscape card types. Collections store them turned so the title reads
# bottom to top, in a portrait slot.
LANDSCAPE_TYPES = {"Objective", "Mission"}


def display_rotation(attrs: dict | None, side: str) -> int:
    """Show landscape cards (objectives, missions) turned 90° clockwise
    (games.display_rotation). Scans already stored landscape are shown as is
    (web._open)."""
    if side == "front" and attrs and attrs.get("card_type") in LANDSCAPE_TYPES:
        return 90
    return 0


def back_group_for(pack_code: str, side: str, objective_set_number: int | None) -> str | None:
    # Balance of the Force cards outside objective sets have their own backs.
    botf = pack_code == "balance-of-the-force" and objective_set_number is None
    prefix = "botf-" if botf else ""
    if side in ("Light", "Dark"):
        return prefix + side.lower()
    return None


def load_catalog(refresh: bool = False) -> Catalog:
    # Static data bundled with the code, so there is nothing to refresh.
    packs = json.loads((HERE / "swlcg_packs.json").read_text())
    cards = json.loads((HERE / "swlcg_cards.json").read_text())

    catalog = Catalog(GAME_ID, NAME)
    catalog.packs = [Pack(p["code"], p["name"], f"{p['position']:02}") for p in packs]
    for c in cards:
        catalog.cards.append(Card(
            c["unique_id"],
            c["label"],
            back_group_for(c["pack_code"], c["side"], c["objective_set_number"]),
            {k: v for k, v in c.items() if k != "label"},   # for the deck builder
        ))
        position = int(c["unique_id"]) if c["unique_id"].isdigit() else None
        catalog.versions.append(CardVersion(c["unique_id"], c["pack_code"], position))
    return catalog
