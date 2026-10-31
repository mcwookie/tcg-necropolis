"""Middle-earth CCG deck builder: alignments, the four parts of a deck, and
the deck builder's sections (play deck + location deck, sideboard, pool)."""

import pytest
from fastapi.testclient import TestClient

from tcgnecro import db
from tcgnecro.deckbuilder import registry, store
from tcgnecro.deckbuilder.model import MAIN, Deck
from tcgnecro.games import Catalog, Pack
from tcgnecro.games.meccg.deckbuilder import LOCATION, POOL, SIDEBOARD
from tcgnecro.games.meccg.deckbuilder import PLUGIN as ME

N = iter(range(1, 10_000))


def card(name, card_type, alignment="Hero", **extra):
    i = next(N)
    return {"unique_id": f"{i:05}", "id": f"TW-{i}", "card_number": i, "set": "The Wizards", "name": name,
            "pack_code": "the-wizards", "card_type": card_type, "alignment": alignment, "rarity": "C1",
            "unique": False, "avatar": False, "agent": False, "subtype": None, "keywords": [], "spawn": False,
            "race": None, "skills": None, "mind": None, "marshalling_points": None, "specific": None,
            "stage_points": None, "site_type": None, "playable_as_resource": False, "playable_as_hazard": False,
            "under_deeps": False, "text": "", **extra}


GANDALF = card("Gandalf", "Character", avatar=True, unique=True, race="Wizard")
SARUMAN = card("Saruman", "Character", avatar=True, unique=True, race="Wizard")
RADAGAST = card("Radagast", "Character", avatar=True, unique=True, race="Wizard")
WITCH_KING = card("The Witch-king", "Character", "Minion", avatar=True, unique=True, race="Ringwraith")
ARAGORN = card("Aragorn II", "Character", unique=True, race="Dúnadan", mind=9)
BARD = card("Bard Bowman", "Character", unique=True, race="Man", mind=2)
GUARD = card("Hobbit Guard", "Character", race="Hobbit", mind=1)            # non-unique
ORC = card("Orc Captain", "Character", "Minion", race="Orc", mind=4)
AGENT = card("Agent Spy", "Character", "Minion", agent=True, race="Man", mind=7)
RESOURCES = [card(f"Resource {i}", "Resource", subtype="Short-event") for i in range(30)]
DAGGER = card("Dagger of Westernesse", "Resource", subtype="Minor Item")
MINION_ITEM = card("Orc-draughts", "Resource", "Minion", subtype="Minor Item")
MINION_EVENT = card("Minion Event", "Resource", "Minion", subtype="Short-event")
GANDALF_ONLY = card("Wizard's Ring", "Resource", subtype="Permanent-event", specific="Gandalf")
CREATURES = [card(f"Creature {i}", "Hazard", "Neutral", subtype="Creature") for i in range(11)]
HALVES = [card("Smaug Ahunt", "Hazard", "Neutral", subtype="Long-event"),
          card("Wolves", "Hazard", "Neutral", subtype="Creature/Short-event")]
EVENTS = [card(f"Hazard {i}", "Hazard", "Neutral", subtype="Long-event") for i in range(30)]
RIVENDELL = card("Rivendell", "Site", site_type="{H}")
MORIA = card("Moria", "Site", unique=True, site_type="{S}")
MINION_SITE = card("Dol Guldur", "Site", "Minion", site_type="{H}")
REGION = card("Arthedain", "Region", "Neutral")
BANNED_FW = card("Cracks of Doom", "Resource", subtype="Short-event")
RAW = [GANDALF, SARUMAN, RADAGAST, WITCH_KING, ARAGORN, BARD, GUARD, ORC, AGENT, *RESOURCES, DAGGER, MINION_ITEM,
       MINION_EVENT, GANDALF_ONLY, *CREATURES, *HALVES, *EVENTS, RIVENDELL, MORIA, MINION_SITE, REGION, BANNED_FW]


def rows():
    return [(c["unique_id"], c["name"], {k: v for k, v in c.items() if k != "label"}) for c in RAW]


@pytest.fixture
def pool():
    return ME.build_pool(rows())


def deck(alignment="wizard", **meta):
    d = Deck(id="t", game="meccg", name="t", meta={"alignment": alignment, "length": "short", **meta})
    return d


def add(d, c, qty=1, section=MAIN):
    d.set_count(c["unique_id"], qty, section)


def legal_deck():
    """30 resources, 30 hazards with 12 creatures, Gandalf, two characters, two sites."""
    d = deck()
    for c in RESOURCES:
        add(d, c)
    for c in CREATURES + HALVES + EVENTS[:17]:   # 30 hazards; 11 creatures + two halves = 12
        add(d, c)
    for c in (GANDALF, ARAGORN, BARD):
        add(d, c)
    add(d, RIVENDELL, 2, LOCATION)
    add(d, MORIA, 1, LOCATION)
    return d


def messages(problems):
    return [p.message for p in problems if p.severity == "error"]


def test_pool_and_alignments(pool):
    assert pool.get(REGION["unique_id"]) is None                        # regions never go in a deck
    wiz, wraith = deck(), deck("ringwraith")
    legal = lambda c, d: ME.is_legal(pool.get(c["unique_id"]), d, pool)
    assert legal(GANDALF, wiz) and not legal(WITCH_KING, wiz) and legal(WITCH_KING, wraith)
    assert legal(MINION_ITEM, wiz) and not legal(MINION_EVENT, wiz)    # minion items only
    assert legal(AGENT, wiz) and not legal(ORC, wiz) and legal(ORC, wraith)
    assert legal(RIVENDELL, wiz) and not legal(MINION_SITE, wiz) and legal(MINION_SITE, wraith)
    assert not legal(BANNED_FW, deck("fallen")) and legal(BANNED_FW, wiz)
    assert not legal(ARAGORN, deck("balrog"))                           # Orcs and Trolls only


def test_a_legal_wizard_deck(pool):
    d = legal_deck()
    assert messages(ME.validate(d, pool)) == []
    stats = {s.label: s.value for s in ME.stats(d, pool)}
    assert stats["Resources"] == "30" and stats["Hazards"] == "30" and stats["Creatures"] == "12 / 12+"
    assert dict(ME.print_list(d))[RIVENDELL["unique_id"]] == 2 and len(ME.print_list(d)) == 30 + 30 + 3 + 2


def test_play_deck_balance_creatures_and_avatars(pool):
    d = legal_deck()
    d.set_count(EVENTS[0]["unique_id"], 0)
    assert "Play deck has 30 resources and 29 hazards; it needs 30-50 resources and as many hazards" \
        in messages(ME.validate(d, pool))
    d = legal_deck()
    d.set_count(CREATURES[0]["unique_id"], 0)
    add(d, EVENTS[20])
    assert "Play deck has 11 creatures among its hazards (at least 12)" in messages(ME.validate(d, pool))
    d = legal_deck()
    add(d, SARUMAN)
    add(d, RADAGAST)
    assert "Three different avatars aren't allowed" in messages(ME.validate(d, pool))


def test_copies_count_across_play_deck_sideboard_and_pool(pool):
    d = legal_deck()
    add(d, ARAGORN, 1, POOL)                       # unique: already in the play deck
    add(d, GUARD, 3, SIDEBOARD)
    add(d, GUARD, 1, POOL)
    errors = messages(ME.validate(d, pool))
    assert "2 copies of Aragorn II (limit 1)" in errors and "4 copies of Hobbit Guard (limit 3)" in errors
    assert ME.section_limit(pool.get(GUARD["unique_id"]), d, MAIN) == 0
    assert ME.section_limit(pool.get(RIVENDELL["unique_id"]), d, LOCATION) == 99    # havens: any number


def test_parts_hold_only_their_cards(pool):
    assert ME.allowed_in(pool.get(MORIA["unique_id"]), LOCATION)
    assert not ME.allowed_in(pool.get(MORIA["unique_id"]), MAIN)
    assert not ME.allowed_in(pool.get(EVENTS[0]["unique_id"]), POOL)
    d = legal_deck()
    add(d, MORIA, 2, LOCATION)
    add(d, DAGGER, 3, POOL)
    add(d, RESOURCES[0], 1, POOL)
    errors = messages(ME.validate(d, pool))
    assert "2 copies of Moria in the location deck (limit 1)" in errors
    assert "Pool may hold at most 2 minor items" in errors
    assert "Resource 0 can't go in the pool" in errors


def test_avatar_specific_cards_need_their_avatar(pool):
    d = legal_deck()
    add(d, GANDALF_ONLY)
    d.set_count(RESOURCES[0]["unique_id"], 0)
    assert messages(ME.validate(d, pool)) == []
    d.set_count(GANDALF["unique_id"], 0)
    add(d, SARUMAN)
    assert "Wizard's Ring is Gandalf-specific; include Gandalf to play it" in messages(ME.validate(d, pool))


def test_sideboard_size_follows_game_length(pool):
    d = legal_deck()
    for c in EVENTS[18:30] + RESOURCES[:19]:
        add(d, c, 1, SIDEBOARD)
    assert "Sideboard has 31 cards (at most 30 for this game length)" in messages(ME.validate(d, pool))
    d.meta["length"] = "long"
    assert not any("Sideboard" in m for m in messages(ME.validate(d, pool)))


# ----- the web page: sections ----------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    registry._pools.clear()
    from tcgnecro import users
    from tcgnecro.games import Card, CardVersion
    users._cache = None
    conn = db.connect()
    catalog = Catalog("meccg", "Middle-earth CCG", packs=[Pack("the-wizards", "The Wizards", "01")])
    for cid, title, attrs in rows():
        catalog.cards.append(Card(cid, title, "eye", attrs))
        catalog.versions.append(CardVersion(cid, "the-wizards", attrs["card_number"]))
    db.store_catalog(conn, catalog)
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Tester"})
    yield c
    registry._pools.clear()
    users._cache = None


def test_web_sections(client):
    r = client.post("/decks", data={"game": "meccg", "identity": ""}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/decks/{deck_id}").text
    assert 'id="add-to"' in page and "Location deck" in page and "Play deck" in page

    moria, rivendell = MORIA["unique_id"], RIVENDELL["unique_id"]
    client.post(f"/decks/{deck_id}/slots/{moria}?delta=1")                    # not allowed in the play deck
    client.post(f"/decks/{deck_id}/slots/{moria}?delta=1&section=location")
    client.post(f"/decks/{deck_id}/slots/{moria}?delta=1&section=location")   # one copy of a non-haven site
    for _ in range(3):
        r = client.post(f"/decks/{deck_id}/slots/{rivendell}?delta=1&section=location&list_section=location")
    saved = store.load(deck_id)
    assert saved.slots == {} and saved.sections == {"location": {moria: 1, rivendell: 3}}
    assert f'id="ctl-{rivendell}" hx-swap-oob="true"' in r.text
    listing = client.get(f"/decks/{deck_id}/cards?f=1&section=location&q=moria").text
    assert "section=location" in listing
    assert "1x Moria" in client.get(f"/decks/{deck_id}/export.txt").text
