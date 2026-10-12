"""Middle-earth CCG. The catalog is static JSON bundled alongside this module,
since there is no maintained online source. Edit the JSON to add or correct
packs, then run `tcgnecro catalog meccg` (see docs/UPDATING_GAMES.md).

meccg_packs.json, one object per pack:
    code          pack id, used in image names ("the-wizards")
    name          display name
    position      release order; newer packs have higher numbers
    total_cards   total number of cards in the expansion
    total_unique  total number of unique cards in the expansion
    available     release date ("1995-12")

meccg_cards.json, one object per card:
    id            set abbreviation and card number ("AS-1")
    unique_id     card id, used in image names ("05001")
    card_number   card position in the pack
    set           the pack's display name
    name          printed name
    pack_code     the pack's code
    card_type     Character, Hazard, Region, Resource, Site
    alignment     Balrog, Dual, Fallen-wizard, Hero, Minion, Neutral, Stage
    rarity        rarity (C, U, R) and the number of cards per sheet
    label         unique title used by decklists; when a name repeats, add
                  "(pack-code #number)": "Angmarim (against-the-shadow #58)".
                  Titles match ignoring case and punctuation, so names that
                  differ only in capitals still need a suffix.
"""

import json
from pathlib import Path

from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "meccg"
NAME = "Middle-earth CCG"

HERE = Path(__file__).parent

# Card types in the location deck (map back); everything else has the eye back.
LOCATION_TYPES = ("Site", "Region")


def back_group_for(card_type: str) -> str:
    """Which generic card back a card takes. MECCG has two:
    the map of north-western Middle-earth for Sites and Regions, and
    the burning Eye of Sauron for Characters, Resources and Hazards."""
    if card_type in LOCATION_TYPES:
        return "map"
    return "eye"


def load_catalog(refresh: bool = False) -> Catalog:
    # Static data bundled with the code, so there is nothing to refresh.
    packs = json.loads((HERE / "meccg_packs.json").read_text())
    cards = json.loads((HERE / "meccg_cards.json").read_text())

    catalog = Catalog(GAME_ID, NAME)
    catalog.packs = [Pack(p["code"], p["name"], f"{p['position']:02}") for p in packs]
    for c in cards:
        catalog.cards.append(Card(
            c["unique_id"],
            c["label"],
            back_group_for(c["card_type"]),
        ))
        catalog.versions.append(CardVersion(c["unique_id"], c["pack_code"], c["card_number"]))
    return catalog
