"""City of Heroes CCG.

The catalog is static JSON bundled alongside this module,
since there is no maintained online source. Edit the JSON to add or correct
packs, then run `tcgnecro catalog city_of_heroes` (see docs/UPDATING_GAMES.md).

coh_packs.json, one object per pack:
    code          pack id, used in image names ("the-wizards")
    name          display name
    position      release order; newer packs have higher numbers
    available     release date ("1995-12")

coh_cards.json, one object per card:
    id            set abbreviation and card number ("AS-1")
    name          printed name
    pack_code     the pack's code
    number        card position in the pack
    type          edge, enhancement, hero, mission, sig power, power, sidekick
    rarity        rarity (common, uncommon, rare, battle pack)
    image         scan filename on the Homecoming wiki
  For the deck builder (from the Homecoming wiki's card pages; null/[] when
  a card has none):
    text, inspiration, archetype, travel, accuracy, defense, health, recharge
    powersets         a super's powersets, or a power's own powerset
    starting_powers   a hero's two starting powers, by name
    prereq            printed requirement ("Requires 2 Archery Powers.")
    signature_hero    the hero a signature power requires, by name
"""

import json
from pathlib import Path

from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "city_of_heroes"
NAME = "City of Heroes CCG"

HERE = Path(__file__).parent

def load_catalog(refresh: bool = False) -> Catalog:
    # Static data bundled with the code, so there is nothing to refresh.
    packs = json.loads((HERE / "coh_packs.json").read_text())
    cards = json.loads((HERE / "coh_cards.json").read_text())

    catalog = Catalog(GAME_ID, NAME)
    catalog.packs = [Pack(p["code"], p["name"], f"{p['position']:02}") for p in packs]
    for c in cards:
        catalog.cards.append(Card(
            c["id"],
            c["name"],
            "back",  # all cards have the same back
            c,       # for the deck builder
        ))
        catalog.versions.append(CardVersion(c["id"], c["pack_code"], c["number"]))
    return catalog

