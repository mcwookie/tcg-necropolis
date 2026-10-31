"""City of Heroes CCG deck builder: one hero, its starting powers, 40+ cards."""

import pytest

from tcgnecro.deckbuilder.model import Deck
from tcgnecro.games.city_of_heroes.deckbuilder import PLUGIN as COH


def card(cid, name, type_, powersets=(), **extra):
    return {"id": cid, "name": name, "pack_code": "arena", "number": int(cid[-3:]), "type": type_,
            "rarity": "common", "powersets": list(powersets), "starting_powers": [], "inspiration": "insight",
            "signature_hero": None, "prereq": None, "text": "", **extra}


RAW = [
    card("arena_001", "Aesir", "hero", ["Ice Armor", "Battle Axe"], archetype="Tanker",
         starting_powers=["Frozen Armor", "Chop"]),
    card("arena_002", "Infernal", "hero", ["Fiery Aura", "Fire Blast"], starting_powers=["Fire Bolt", "Blazing Aura"]),
    card("arena_010", "Frozen Armor", "power", ["Ice Armor"]),
    card("arena_011", "Chop", "power", ["Battle Axe"]),
    card("arena_012", "Fire Bolt", "power", ["Fire Blast"]),
    card("arena_013", "Blazing Aura", "power", ["Fiery Aura"]),
    card("arena_014", "Brawl", "power", ["Common Pool"]),
    card("arena_015", "Axe Cyclone", "sig power", ["Battle Axe"], signature_hero="Aesir"),
    card("arena_016", "Bind Demon", "sig power", ["Fiery Aura"], signature_hero="Infernal"),
    card("arena_020", "Mainline", "sidekick", ["Electrical Blast", "Devices"]),
    card("arena_021", "Charged Bolts", "power", ["Electrical Blast"]),
    card("arena_030", "Level Up!", "edge"),
    card("arena_031", "Damage Enhancement", "enhancement"),
    card("arena_040", "Bank Job", "mission"),
]


@pytest.fixture
def pool():
    return COH.build_pool([(c["id"], c["name"], c) for c in RAW])


def started(pool, hero="arena_001"):
    d = Deck(id="t", game="city_of_heroes", name="t", identity=hero)
    COH.start_deck(d, pool)
    return d


def messages(problems, severity="error"):
    return [p.message for p in problems if p.severity == severity]


def test_new_deck_gets_the_starting_powers(pool):
    assert [c.name for c in COH.identities(pool)] == ["Aesir", "Infernal"]
    assert started(pool).slots == {"arena_010": 1, "arena_011": 1}


def test_legal_hides_other_heroes_and_their_signature_powers(pool):
    d = started(pool)
    legal = {pool.get(c).name for c in pool.cards if COH.is_legal(pool.get(c), d, pool)}
    assert "Axe Cyclone" in legal and "Bind Demon" not in legal and "Infernal" not in legal
    usable = {c.name for c in pool.cards.values() if COH.toggle_filter("usable", c, d, pool)}
    assert {"Chop", "Brawl", "Level Up!"} <= usable and "Fire Bolt" not in usable and "Charged Bolts" not in usable
    d.slots["arena_020"] = 1                                        # a sidekick brings its powersets
    assert COH.toggle_filter("usable", pool.get("arena_021"), d, pool)


def test_forty_cards_besides_starting_powers_and_three_copies(pool):
    d = started(pool)
    d.slots.update({"arena_030": 3, "arena_031": 3, "arena_040": 3, "arena_014": 3, "arena_015": 3})
    d.slots["arena_011"] = 3                                       # 2 extra Chops count, the starting one doesn't
    errors = messages(COH.validate(d, pool))
    assert errors == ["Deck has 17 cards besides Aesir's starting powers; it needs at least 40"]
    d.slots["arena_030"] = 4
    assert "4 copies of Level Up! (limit 3)" in messages(COH.validate(d, pool))
    del d.slots["arena_010"]
    assert "Missing starting powers: Frozen Armor" in messages(COH.validate(d, pool))


def test_warnings_for_cards_no_super_can_use(pool):
    d = started(pool)
    d.slots.update({"arena_016": 1, "arena_012": 1, "arena_001": 1})
    warnings = messages(COH.validate(d, pool), "warning")
    assert "Bind Demon is Infernal's signature power" in warnings
    assert "No super in this deck has Fire Bolt's powerset (Fire Blast)" in warnings
    assert "Aesir: a deck has only one hero" in messages(COH.validate(d, pool))
