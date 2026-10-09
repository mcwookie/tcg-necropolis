"""Star Wars LCG. The catalog is static JSON bundled alongside this module."""

import json
from pathlib import Path

from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "swlcg"
NAME = "Star Wars LCG"

HERE = Path(__file__).parent


def back_group_for(pack_code: str, side: str, objective_set_number: int | None) -> str | None:
    # Balance of the Force cards outside objective sets have their own backs.
    botf = pack_code == "balance-of-the-force" and objective_set_number is None
    prefix = "botf-" if botf else ""
    if side in ("Light", "Dark"):
        return prefix + side.lower()
    return None


def load_catalog() -> Catalog:
    packs = json.loads((HERE / "swlcg_packs.json").read_text())
    cards = json.loads((HERE / "swlcg_cards.json").read_text())

    catalog = Catalog(GAME_ID, NAME)
    catalog.packs = [Pack(p["code"], p["name"], f"{p['position']:02}") for p in packs]
    for c in cards:
        catalog.cards.append(Card(
            c["unique_id"],
            c["label"],
            back_group_for(c["pack_code"], c["side"], c["objective_set_number"]),
        ))
        position = int(c["unique_id"]) if c["unique_id"].isdigit() else None
        catalog.versions.append(CardVersion(c["unique_id"], c["pack_code"], position))
    return catalog
