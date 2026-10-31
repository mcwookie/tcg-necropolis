"""Marvel Champions deck builder. Synthetic MarvelCDB cards, run through the
same catalog code as the real data (with_back_types, printed_cards, build),
so these run offline and pin down the rules MarvelCDB applies."""

import pytest
from fastapi.testclient import TestClient

from tcgnecro import db
from tcgnecro.deckbuilder import registry, store
from tcgnecro.deckbuilder.model import Deck
from tcgnecro.games import Catalog, Pack
from tcgnecro.games.marvel_champions import build, printed_cards, with_back_types
from tcgnecro.games.marvel_champions.deckbuilder import PLUGIN as MC


def card(code, name, type_code="event", faction="justice", **extra):
    return {"code": code, "name": name, "type_code": type_code, "faction_code": faction,
            "pack_code": extra.pop("pack_code", "core"), "position": int(code[2:5]), "quantity": 1,
            "deck_limit": 3, "card_set_code": None, "card_set_type_name_code": None, **extra}


def hero(code, name, alter, set_code, **extra):
    return [card(code + "a", name, "hero", "hero", card_set_code=set_code, card_set_type_name_code="hero",
                 linked_to_code=code + "b", deck_limit=1, **extra),
            card(code + "b", alter, "alter_ego", "hero", card_set_code=set_code, card_set_type_name_code="hero",
                 hidden=True, linked_to_code=code + "a", deck_limit=1)]


def hero_card(code, name, set_code, qty=1, type_code="event"):
    return card(code, name, type_code, "hero", card_set_code=set_code, card_set_type_name_code="hero",
                quantity=qty, deck_limit=qty)


RAW = [
    *hero("01001", "Spider-Man", "Peter Parker", "spider_man"),
    hero_card("01002", "Black Cat", "spider_man", type_code="ally"),
    hero_card("01003", "Backflip", "spider_man", qty=2),
    card("01165", "Eviction Notice", "obligation", "encounter", card_set_code="spider_man",
         card_set_type_name_code="hero"),
    card("01096", "Vulture", "minion", "encounter", card_set_code="spider_man_nemesis",
         card_set_type_name_code="nemesis"),
    *hero("01019", "She-Hulk", "Jennifer Walters", "she_hulk"),
    hero_card("01020", "Gamma Slam", "she_hulk"),
    *hero("04031", "Spider-Woman", "Jessica Drew", "spider_woman", deck_requirements=[{"aspects": 2}]),
    *hero("21031", "Adam Warlock", "Adam Warlock", "adam_warlock", deck_requirements=[{"aspects": 4, "limit": 1}]),
    *hero("18001", "Gamora", "Gamora", "gamora",
          deck_options=[{"limit": 2, "trait": ["attack", "thwart"], "type": ["event"]}]),
    card("12001c", "Ant-Man", "hero", "hero", card_set_code="ant", card_set_type_name_code="hero"),
    card("01088", "Energy", "resource", "basic", resource_energy=2),
    card("35024", "Energy", "resource", "basic", resource_energy=2, duplicate_of_code="01088", pack_code="wolv"),
    card("01087", "Avengers Mansion", "support", "basic", deck_limit=1, is_unique=True),
    card("01089", "Permanent Thing", "upgrade", "basic", permanent=True),
    card("01099", "Filler", "resource", "basic", deck_limit=99),        # pads test decks
    card("01040", "Get Over Here", faction="justice", traits="Thwart."),
    card("01041", "For Justice!", faction="justice", traits="Thwart."),
    card("01050", "Chase Them Down", faction="aggression", traits="Attack."),
    card("01051", "Relentless Assault", faction="aggression", traits="Attack."),
    card("01060", "Lead from the Front", faction="leadership"),
    card("01070", "Unflappable", "upgrade", "protection"),
    card("44016", "Lady Deadpool", "ally", "pool", deck_limit=1),
    card("04090", "Campaign Upgrade", "upgrade", "campaign", card_set_code="rrs_campaign"),
    card("56001", "Captain America", "leader", "basic", card_set_type_name_code="leader"),
]


def catalog_rows():
    """(id, title, attrs) as the catalog stores them, the way registry.pool reads them."""
    cards, _ = build(printed_cards(with_back_types(RAW)))
    return [(c.id, c.title, c.attrs) for c in cards]


@pytest.fixture
def pool():
    return MC.build_pool(catalog_rows())


def deck(identity="01001a", meta=None, **slots):
    return Deck(id="t", game="marvel_champions", name="t", identity=identity,
                slots={k.lstrip("_"): v for k, v in slots.items()}, meta=dict(meta or {}))


def started(pool, identity="01001a", meta=None):
    d = deck(identity, meta)
    MC.start_deck(d, pool)
    return d


def messages(problems, severity="error"):
    return [p.message for p in problems if p.severity == severity]


def fill(d, pool, n, code="01099"):
    """Pad a deck to n counted cards with copies of one card (limits aside)."""
    d.slots[code] = d.slots.get(code, 0) + n - MC._size(d, pool)


# ----- pool -----------------------------------------------------------------------

def test_pool_keeps_deck_cards_heroes_and_the_heros_encounter_cards(pool):
    assert set(pool.cards) == {"01001a", "01002", "01003", "01165", "01096", "01019a", "01020", "04031a",
                               "21031a", "18001a", "01088", "01087", "01089", "01040", "01041", "01050",
                               "01051", "01060", "01070", "44016", "01099"}
    # Alter egos, a hero's third form, campaign cards and leaders are out;
    # a reprint's code and a hero's back code stand for the card.
    assert pool.resolve("35024") == "01088" and pool.resolve("01001b") == "01001a"
    names = [c.name for c in MC.identities(pool)]
    assert "Spider-Man (Peter Parker)" in names and "Adam Warlock" in names and len(names) == 5
    assert pool.get("01165").get("kind") == "encounter"


# ----- building -------------------------------------------------------------------

def test_new_deck_gets_the_hero_set_and_asks_for_an_aspect(pool):
    d = started(pool)
    assert d.slots == {"01002": 1, "01003": 2} and d.meta["print"] == "deck"
    keys = [s.key for s in MC.selections(d, pool)]
    assert keys == ["aspect", "print"]
    assert "Choose an aspect" in messages(MC.validate(d, pool), "warning")
    assert [s.key for s in MC.selections(started(pool, "04031a"), pool)] == ["aspect", "aspect2", "print"]
    assert [s.key for s in MC.selections(started(pool, "21031a"), pool)] == ["print"]   # nothing to pick


def test_legal_cards_follow_the_aspect(pool):
    d = started(pool)
    legal = lambda code: MC.is_legal(pool.get(code), d, pool)
    assert all(legal(c) for c in ("01040", "01050", "01060", "01070", "44016"))   # no aspect yet: all open
    d.meta["aspect"] = "justice"
    assert legal("01040") and legal("01088") and legal("01003")              # aspect, basic, own hero card
    assert not legal("01050") and not legal("44016")                         # other aspects
    assert not legal("01020") and not legal("01165") and not legal("01019a")  # other hero, encounter, hero


def test_deck_size_hero_cards_and_copy_limits(pool):
    d = started(pool, meta={"aspect": "justice"})
    d.slots["01089"] = 1                                     # permanent: doesn't count
    fill(d, pool, 39, "01040")
    assert "Deck has 39 cards; it needs 40 to 50" in messages(MC.validate(d, pool))
    fill(d, pool, 51, "01040")
    assert "Deck has 51 cards; it needs 40 to 50" in messages(MC.validate(d, pool))

    d = started(pool, meta={"aspect": "justice"})
    d.slots.update({"01040": 3, "01041": 3, "01087": 2})
    fill(d, pool, 45)
    del d.slots["01002"]
    errors = messages(MC.validate(d, pool))
    assert "Missing hero cards: Black Cat" in errors
    assert "2 copies of Avengers Mansion (limit 1)" in errors
    d.slots["01088"] = 4
    assert "4 copies of Energy (limit 3)" in messages(MC.validate(d, pool))


def test_spider_woman_needs_two_aspects_in_equal_numbers(pool):
    d = started(pool, "04031a", {"aspect": "justice", "aspect2": "aggression"})
    d.slots.update({"01040": 2, "01050": 1})
    fill(d, pool, 40)
    assert MC.is_legal(pool.get("01050"), d, pool) and not MC.is_legal(pool.get("01060"), d, pool)
    assert any("equal numbers of each aspect (Justice 2, Aggression 1)" in m for m in messages(MC.validate(d, pool)))
    d.slots["01051"] = 1
    fill(d, pool, 40)
    assert messages(MC.validate(d, pool)) == []


def test_adam_warlock_takes_one_of_each_from_all_four_aspects(pool):
    d = started(pool, "21031a")
    d.slots.update({"01040": 1, "01050": 1, "01060": 1, "01070": 1})
    d.slots["01099"] = 36
    errors = messages(MC.validate(d, pool))
    assert errors == ["Adam Warlock needs exactly 1 copy of each card"]
    d.slots.pop("01099")
    d.slots.update({f"0108{i}": 1 for i in (7, 8)})
    d.slots["01050"] = 2
    assert any("4 aspects in equal numbers" in m for m in messages(MC.validate(d, pool)))


def test_gamoras_extra_events_have_a_limit(pool):
    d = started(pool, "18001a", {"aspect": "justice"})
    assert MC.is_legal(pool.get("01050"), d, pool)               # an Attack event from another aspect
    assert not MC.is_legal(pool.get("01070"), d, pool)           # not an event
    d.slots.update({"01050": 2})
    fill(d, pool, 40)
    assert messages(MC.validate(d, pool)) == []
    d.slots["01051"] = 1
    assert "Relentless Assault goes over Gamora's limit of 2 cards from outside the aspect" in messages(MC.validate(d, pool))


# ----- import / sync / printing --------------------------------------------------

def test_marvelcdb_decks_map_reprints_back_codes_and_aspect(pool):
    assert MC.sync_link("https://marvelcdb.com/decklist/view/68780/drax-1.0")["kind"] == "decklist"
    assert MC.sync_link("https://marvelcdb.com/deck/view/123")["remote_id"] == "123"
    assert MC.sync_link("https://arkhamdb.com/deck/view/1") is None
    data = {"id": 68780, "name": "Spidey", "hero_code": "01001a", "version": "1.0",
            "meta": '{"aspect":"justice","extra":1}',
            "slots": {"01001a": 1, "01002": 1, "01003": 2, "35024": 2, "01088": 1, "01040": 3}}
    remote = MC._remote_from(data, "decklist", pool)
    assert remote.identity == "01001a" and remote.meta == {"aspect": "justice"}
    assert remote.slots == {"01002": 1, "01003": 2, "01088": 3, "01040": 3}
    assert remote.url == "https://marvelcdb.com/decklist/view/68780"


def test_printing_adds_obligation_and_nemesis_on_request(pool, monkeypatch):
    monkeypatch.setattr(registry, "pool", lambda conn, game: pool)
    monkeypatch.setattr(db, "connect", lambda: type("C", (), {"close": lambda self: None})())
    d = started(pool)
    assert dict(MC.print_list(d)) == {"01001a": 1, "01002": 1, "01003": 2}
    d.meta["print"] = "encounter"
    assert dict(MC.print_list(d)) == {"01001a": 1, "01002": 1, "01003": 2, "01165": 1, "01096": 1}


# ----- the web page -----------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    registry._pools.clear()
    conn = db.connect()
    catalog = Catalog("marvel_champions", "Marvel Champions", packs=[Pack("core", "Core Set", "2019")])
    catalog.cards, catalog.versions = build(printed_cards(with_back_types(RAW)))
    db.store_catalog(conn, catalog)
    from tcgnecro import users
    users._cache = None
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Tester"})
    yield c
    registry._pools.clear()
    users._cache = None


def test_web_build_a_marvel_deck(client):
    assert "Spider-Man (Peter Parker)" in client.get("/decks/identities?game=marvel_champions").text
    r = client.post("/decks", data={"game": "marvel_champions", "identity": "01001a"}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/decks/{deck_id}").text
    assert "Hero cards" in page and "Choose an aspect" in page and "Black Cat" in page

    client.post(f"/decks/{deck_id}/meta", data={"aspect": "justice"})
    assert store.load(deck_id).meta["aspect"] == "justice"
    listing = client.get(f"/decks/{deck_id}/cards?f=1&legal=1").text
    assert "Get Over Here" in listing and "Chase Them Down" not in listing
    for _ in range(4):
        client.post(f"/decks/{deck_id}/slots/01040?delta=1")
    assert store.load(deck_id).slots["01040"] == 3                     # deck_limit
    client.post(f"/decks/{deck_id}/meta", data={"print": "encounter"})
    assert "1x Eviction Notice" in client.get(f"/decks/{deck_id}/export.txt").text
