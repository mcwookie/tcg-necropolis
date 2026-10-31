"""Star Wars LCG deck builder: objective sets as the unit of deckbuilding."""

import pytest

from tcgnecro import db
from tcgnecro.deckbuilder import registry
from tcgnecro.deckbuilder.model import Deck
from tcgnecro.games.swlcg.deckbuilder import PLUGIN as SW


def card(uid, name, card_type, side="Light", affiliation="Jedi", set_no=None, seq=None, **extra):
    return {"unique_id": uid, "name": name, "pack_code": "core-set", "card_type": card_type, "side": side,
            "affiliation": affiliation, "affiliation_lock": None, "objective_set_number": set_no,
            "objective_set_sequence": seq, "is_unique": False, "is_limited": False, "cost": None,
            "force_icons": None, "resource_value": None, "damage_capacity": None, "edge_priority": None,
            "combat_icons": None, "edge_combat_icons": None, "traits": [], "text": "", **extra}


def objective_set(no, name, side="Light", affiliation="Jedi", **objective):
    """An objective and its five command cards."""
    cards = [card(f"{no:03}1", name, "Objective", side, affiliation, no, 1, resource_value=1, **objective)]
    for seq in range(2, 7):
        cards.append(card(f"{no:03}{seq}", f"{name} card {seq}", "Unit" if seq < 4 else "Event", side,
                          affiliation, no, seq, cost=2, force_icons=1))
    return cards


RAW = [
    card("2001", "Jedi", "Affiliation"),
    card("2002", "Guardians of Justice", "Affiliation", pack_code="allies-of-necessity"),
    card("2003", "Sith", "Affiliation", side="Dark", affiliation="Sith"),
    *objective_set(1, "A Hero's Journey"),
    *objective_set(2, "Rebel Assault", affiliation="Rebel Alliance"),
    *objective_set(3, "Jedi Only", affiliation_lock="Jedi"),
    *objective_set(4, "Rebels Only", affiliation="Rebel Alliance", affiliation_lock="Rebel Alliance"),
    *objective_set(5, "Unique Set", is_limited=True),
    *objective_set(6, "Dark Set", side="Dark", affiliation="Sith"),
    card("9001", "Multiplayer Objective", "Objective"),          # Balance of the Force: no set
]


@pytest.fixture
def pool():
    return SW.build_pool([(c["unique_id"], c["name"], c) for c in RAW])


def deck(**slots):
    return Deck(id="t", game="swlcg", name="t", identity="2001", slots={k.lstrip("_"): v for k, v in slots.items()})


def messages(problems):
    return [p.message for p in problems]


def test_pool_holds_affiliations_and_one_entry_per_set(pool):
    kinds = {c.id: c.get("kind") for c in pool.cards.values()}
    assert [k for k, v in kinds.items() if v == "objective"] == ["0011", "0021", "0031", "0041", "0051", "0061"]
    assert "9001" not in kinds and kinds["0012"] == "command"
    hero = pool.get("0011")
    assert [m["id"] for m in hero.get("members")] == ["0012", "0013", "0014", "0015", "0016"]
    assert hero.get("summary") == "2 Units · 3 Events" and hero.get("force") == 5
    assert "A Hero's Journey card 4" in hero.get("text")               # searchable by its cards
    assert [c.name for c in SW.identities(pool)] == ["Guardians of Justice (Jedi)", "Jedi", "Sith"]


def test_side_affiliation_locks_and_copy_limits(pool):
    d = deck()
    legal = {c.id for c in pool.cards.values() if SW.is_legal(c, d, pool)}
    assert legal == {"0011", "0021", "0031", "0051"}       # not: other side, Rebels only, command cards
    assert SW.max_copies(pool.get("0011"), d) == 2 and SW.max_copies(pool.get("0051"), d) == 1

    d.slots = {"0011": 2, "0021": 2, "0031": 2, "0041": 1, "0051": 2, "0061": 1, "0012": 1}
    problems = messages(SW.validate(d, pool))
    assert "Rebels Only (4) is Rebel Alliance affiliation only" in problems
    assert "2 copies of Unique Set (5) (limit 1 per objective deck)" in problems
    assert "Dark Set (6) is a dark side set; Jedi is light side" in problems
    assert "A Hero's Journey card 2 isn't an objective set; add its set instead" in problems


def test_at_least_ten_sets(pool):
    d = deck(_0011=2, _0021=2, _0031=2, _0051=1)
    assert messages(SW.validate(d, pool)) == ["Deck has 7 objective sets; it needs at least 10"]
    stats = {s.label: s.value for s in SW.stats(d, pool)}
    assert stats["Objective sets"] == "7 / 10+" and stats["Command cards"] == "35"
    assert messages(SW.validate(Deck(id="t", game="swlcg", name="t"), pool)) == ["Choose an affiliation"]


def test_printing_expands_each_set(pool, monkeypatch):
    monkeypatch.setattr(registry, "pool", lambda conn, game: pool)
    monkeypatch.setattr(db, "connect", lambda: type("C", (), {"close": lambda self: None})())
    rows = dict(SW.print_list(deck(_0011=2, _0051=1)))
    assert rows["2001"] == 1 and rows["0011"] == 2 and rows["0016"] == 2 and rows["0056"] == 1
    assert len(rows) == 1 + 12
