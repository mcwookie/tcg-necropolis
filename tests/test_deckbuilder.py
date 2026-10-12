"""Deck builder tests. Synthetic AHLCG cards stand in for ArkhamDB data, so
these run offline and pin down the rules engine's behavior."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from tcgnecro import db
from tcgnecro.deckbuilder import filters, markup, registry, store
from tcgnecro.deckbuilder.config import IconConfig
from tcgnecro.deckbuilder.model import Deck
from tcgnecro.games import Card, CardVersion, Catalog, Pack
from tcgnecro.games.ahlcg import card_titles
from tcgnecro.games.ahlcg.deckbuilder import PLUGIN as AH


def card(code, name, type_code="asset", faction="guardian", xp=0, **extra):
    return {"code": code, "name": name, "real_name": name, "type_code": type_code,
            "faction_code": faction, "xp": xp, "pack_code": "core", "position": int(code[-3:]),
            "quantity": 2, "deck_limit": 2, "text": extra.pop("text", ""), **extra}


ROLAND = card("01001", "Roland Banks", "investigator", xp=None, traits="Agency. Detective.", deck_limit=1,
              deck_requirements={"size": 30, "card": {"01006": {"01006": "01006"}},
                                 "random": [{"target": "subtype", "value": "basicweakness"}]},
              deck_options=[{"faction": ["guardian", "neutral"], "level": {"min": 0, "max": 5}},
                            {"faction": ["seeker"], "level": {"min": 0, "max": 2}}])
ZOEY = card("02001", "Zoey Samaras", "investigator", xp=None, deck_limit=1,
            deck_requirements={"size": 30, "card": {}},
            deck_options=[{"faction": ["guardian", "neutral"], "level": {"min": 0, "max": 5}},
                          {"level": {"min": 0, "max": 0}, "limit": 2, "error": "Max 2 off-class"}])
TONY = card("03001", "Tony Morgan", "investigator", xp=None, deck_limit=1,
            deck_requirements={"size": 30, "card": {}},
            # First match wins, so a "not" option only bans what earlier options
            # haven't already allowed. Real data lists them first (Rex Murphy).
            deck_options=[{"not": True, "trait": ["fortune"]},
                          {"faction": ["rogue", "neutral"], "level": {"min": 0, "max": 5}},
                          {"name": "Secondary Class", "faction_select": ["guardian", "seeker"],
                           "level": {"min": 0, "max": 1}, "type": ["event", "skill"], "limit": 10}])
CARDS = [
    ROLAND, ZOEY, TONY,
    card("01006", "Roland's .38 Special", deck_limit=1, restrictions={"investigator": {"01001": "01001"}}),
    card("01016", "Machete", traits="Item. Weapon. Melee.", cost=3, real_slot="Hand"),
    card("01516", "Machete", traits="Item. Weapon. Melee.", cost=3, duplicate_of_code="01016"),
    card("01017", "Machete", xp=2, traits="Item. Weapon. Melee.", cost=3),
    card("01020", "Shrivelling", faction="mystic", type_code="asset", traits="Spell."),
    card("01030", "Deduction", faction="seeker", type_code="skill", skill_intellect=1),
    card("01031", "Deduction", faction="seeker", type_code="skill", xp=3),
    card("01040", "Emergency Cache", faction="neutral", type_code="event", traits="Supply."),
    card("01041", "Lucky Break", faction="guardian", type_code="event", traits="Fortune."),
    card("01042", "Big Gun", faction="guardian", xp=4, exceptional=True),
    card("01043", "Charm", faction="neutral", permanent=True),
    card("01044", "Underworld Support", faction="rogue", permanent=True, deck_limit=1),
    card("01050", "Amnesia", faction="neutral", type_code="treachery", xp=None, subtype_code="basicweakness", deck_limit=1),
    card("01060", "Scenario Enemy", faction="mythos", type_code="enemy", encounter_code="x"),
    card("03059", "The Man in the Pallid Mask", faction="mythos", type_code="enemy", xp=None,
         subtype_code="weakness", encounter_code="curtain_call", deck_limit=1),
    card("03070", "Lita Chantler", faction="neutral", encounter_code="x", deck_limit=1),
]


@pytest.fixture
def pool():
    return AH.build_pool([(c["code"], c["name"], c) for c in CARDS])


def deck(identity="01001", **slots):
    return Deck(id="t", game="ahlcg", name="t", identity=identity,
                slots={k.lstrip("_"): v for k, v in slots.items()})


def messages(problems):
    return [p.message for p in problems]


# ----- pool --------------------------------------------------------------------

def test_pool_keeps_player_cards_and_aliases_reprints(pool):
    assert "01060" not in pool.cards          # scenario card
    assert pool.get("03059").get("kind") == "weakness"   # story weakness from a campaign
    assert pool.get("03070").get("kind") == "story"      # story asset
    assert "01516" not in pool.cards          # reprint...
    assert pool.resolve("01516") == "01016"   # ...stands in for the original
    assert pool.get("01016").get("slots") == ["Hand"]
    assert [c.id for c in AH.identities(pool)] == ["01001", "03001", "02001"]


# ----- legality ----------------------------------------------------------------

def test_faction_and_level_options(pool):
    d = deck()
    legal = lambda cid: AH.is_legal(pool.get(cid), d, pool)
    assert legal("01016")          # guardian 0
    assert legal("01030")          # seeker 0
    assert not legal("01031")      # seeker 3 > 2
    assert not legal("01020")      # mystic
    assert legal("01006")          # his signature
    assert not AH.is_legal(pool.get("01006"), deck("02001"), pool)  # someone else's signature
    assert legal("01050")          # basic weakness


def test_start_deck_adds_signatures_and_default_choices(pool):
    d = deck("03001")
    AH.start_deck(d, pool)
    assert d.meta == {"faction_selected": "guardian"}
    d = deck()
    AH.start_deck(d, pool)
    assert d.slots == {"01006": 1}


def test_faction_select_and_not_options(pool):
    d = deck("03001")
    d.meta["faction_selected"] = "seeker"
    assert AH.is_legal(pool.get("01030"), d, pool)       # seeker skill, level 0
    assert not AH.is_legal(pool.get("01016"), d, pool)   # guardian asset: wrong class and type
    d.meta["faction_selected"] = "guardian"
    assert not AH.is_legal(pool.get("01030"), d, pool)
    assert not AH.is_legal(pool.get("01041"), d, pool)   # Fortune events are banned


def test_limit_options_fall_through_and_report(pool):
    # Zoey: guardian/neutral, plus at most 2 level-0 cards of other classes.
    d = deck("02001", _01030=2, _01020=1)
    assert "Max 2 off-class" in messages(AH.validate(d, pool))
    d = deck("02001", _01030=2)
    assert "Max 2 off-class" not in messages(AH.validate(d, pool))


# ----- deck checks -------------------------------------------------------------

def test_deck_size_ignores_signatures_weaknesses_and_permanents(pool):
    d = deck(_01006=1, _01050=1, _01043=1, _01016=2)
    assert AH._counted(d, pool, pool.get("01001")) == 2
    assert "Deck has 2 cards; Roland Banks needs 30" in messages(AH.validate(d, pool))


def test_underworld_support_changes_size_and_copy_rule(pool):
    d = deck("03001", _01044=1, _01016=2)
    assert AH._deck_size(d, pool.get("03001"), pool) == 25
    assert any("only 1 copy" in m for m in messages(AH.validate(d, pool)))


def test_campaign_cards_are_allowed_and_dont_count(pool):
    d = deck(_01006=1, _03059=1, _03070=1)
    assert AH.is_legal(pool.get("03070"), d, pool)
    assert AH._counted(d, pool, pool.get("01001")) == 0
    assert not [m for m in messages(AH.validate(d, pool)) if "isn't allowed" in m or "Unknown" in m]


def test_copy_limit_is_by_title_across_levels(pool):
    d = deck(_01016=2, _01017=1)
    assert "3 copies of Machete (limit 2)" in messages(AH.validate(d, pool))


def test_missing_signature_and_random_weakness(pool):
    problems = AH.validate(deck(), pool)
    assert "Missing required card: Roland's .38 Special" in messages(problems)
    assert any(p.severity == "warning" and "random basic weakness" in p.message for p in problems)
    d = deck()
    assert AH.run_action("random_weakness", d, pool) == "Drew Amnesia"
    assert d.slots == {"01050": 1}


def test_xp_counts_exceptional_double(pool):
    assert AH.xp(deck(_01017=2, _01042=1), pool) == 2 * 2 + 4 * 2


# ----- titles (ported from upstream's adapter tests) ------------------------------

def test_titles_spell_out_level_keep_reprints_and_separate_namesakes():
    titles = card_titles([
        card("01016", "Machete", duplicated_by=["01516"]),
        card("01516", "Machete", duplicate_of_code="01016"),   # reprint: same card
        card("01017", "Machete", xp=2),                        # upgrade: level in title
        card("01070", "Lucky"), card("02070", "Lucky", pack_code="dwl"),  # two different cards
    ])
    assert titles["01016"] == titles["01516"] == "Machete"
    assert titles["01017"] == "Machete (2)"
    assert titles["01070"] == "Lucky"                          # lowest code keeps it
    assert titles["02070"] == "Lucky (dwl 70)"                 # others get pack + position


# ----- filters, markup, storage -------------------------------------------------

def test_filters_use_config_defaults_until_submitted(pool):
    values = filters.parse(AH.config, {})
    assert values["kind"] == ["asset", "event", "skill"] and values["legal"] is True
    names = {c.name for c in filters.apply(AH, values, deck(), pool)}
    assert "Machete" in names and "Shrivelling" not in names and "Amnesia" not in names
    submitted = filters.parse(AH.config, {"f": ["1"], "q": ["machete"]})
    assert submitted["legal"] is False and submitted["kind"] == []
    assert {c.name for c in filters.apply(AH, submitted, deck(), pool)} == {"Machete", "Machete (2)"}


def test_markup_escapes_html_and_draws_icons():
    out = markup.render('<script>x</script> <b>Fast.</b> [action] [[Spell]] [unknown]',
                        {"action": IconConfig(label="A", title="Action")})
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert "<b>Fast.</b>" in out
    assert '<span class="icon icon-action" title="Action">A</span>' in out
    assert '<b class="trait"><i>Spell</i></b>' in out
    assert "[unknown]" in out


def test_store_roundtrip_and_newer_format_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    d = store.save(Deck(id=store.new_id(), game="ahlcg", name="x", slots={"01016": 2}))
    assert store.load(d.id).slots == {"01016": 2}
    store.update(d.id, lambda x: x.set_count("01016", 0))
    assert store.load(d.id).slots == {}
    with pytest.raises(store.DeckNotFound):
        store.load("../etc")
    with pytest.raises(ValueError):
        Deck.from_json({**d.to_json(), "format": 99})


def test_old_index_db_is_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    old = sqlite3.connect(tmp_path / "index.db")
    old.executescript("CREATE TABLE games (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
                      "CREATE TABLE cards (game_id TEXT, id TEXT, title TEXT, title_normalized TEXT, back_group TEXT);")
    old.close()
    conn = db.connect()
    assert {r[1] for r in conn.execute("PRAGMA table_info(cards)")} >= {"attrs"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


# ----- web ---------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    registry._pools.clear()
    catalog = Catalog("ahlcg", "Arkham Horror: The Card Game", packs=[Pack("core", "Core Set", "2016")])
    for c in CARDS:
        catalog.cards.append(Card(c["code"], c["name"] + (f" ({c['xp']})" if c.get("xp") else ""), "player", c))
        catalog.versions.append(CardVersion(c["code"], "core", c["position"]))
    db.store_catalog(db.connect(), catalog)
    from tcgnecro.web import app
    client = TestClient(app)
    client.post("/login/setup", data={"name": "Tester"})   # first profile, signed in
    yield client
    registry._pools.clear()


def test_web_create_edit_export(client):
    assert client.get("/decks").status_code == 200
    assert "Roland Banks" in client.get("/decks/identities?game=ahlcg").text
    r = client.post("/decks", data={"game": "ahlcg", "identity": "01001"}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    assert client.get(f"/decks/{deck_id}").status_code == 200

    r = client.get(f"/decks/{deck_id}/cards?f=1&legal=1&q=machete")
    assert "Machete" in r.text and r.headers["HX-Push-Url"].startswith(f"/decks/{deck_id}?")

    for _ in range(3):
        r = client.post(f"/decks/{deck_id}/slots/01016?delta=1")
    assert store.load(deck_id).slots["01016"] == 2           # capped at deck limit
    assert 'id="ctl-01016" hx-swap-oob="true"' in r.text     # list control refreshed

    client.post(f"/decks/{deck_id}/meta", data={"name": "Renamed"})
    assert store.load(deck_id).name == "Renamed"
    assert client.get(f"/decks/{deck_id}/export.txt").text.splitlines()[1:] == [
        "1x Roland Banks", "1x Roland's .38 Special", "2x Machete"]
    assert client.get(f"/decks/{deck_id}/card/01016").status_code == 200

    client.post(f"/decks/{deck_id}/delete")
    assert client.get(f"/decks/{deck_id}").status_code == 404


def test_pool_notices_reindex_even_when_image_count_is_unchanged(client, tmp_path):
    from PIL import Image
    from tcgnecro import collections

    conn = db.connect()
    folder = tmp_path / "collections" / "ahlcg" / "first"
    folder.mkdir(parents=True)
    Image.new("RGB", (6, 8)).save(folder / "01016@core.png")
    collections.reindex(conn, warn=lambda _: None)
    assert "/first/" in registry.pool(conn, "ahlcg").images["01016"]

    folder.rename(folder.with_name("second"))         # same number of images
    collections.reindex(conn, warn=lambda _: None)
    assert "/second/" in registry.pool(conn, "ahlcg").images["01016"]


def test_landscape_cards_show_upright_in_the_builder(client):
    from PIL import Image
    from tcgnecro import collections

    conn = db.connect()
    folder = db.collections_dir() / "ahlcg" / "scans"
    folder.mkdir(parents=True)
    for name in ("01001@core.png", "01016@core.png"):
        Image.new("RGB", (63, 88)).save(folder / name)
    collections.reindex(conn, warn=lambda _: None)
    assert registry.pool(conn, "ahlcg").get("01001").rotate == 270

    r = client.post("/decks", data={"game": "ahlcg", "identity": "01001"}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/decks/{deck_id}").text
    assert "/thumb/ahlcg/scans/01001@core.png?r=270" in page             # deck panel investigator
    assert "/thumb/ahlcg/scans/01016@core.png\"" in page                  # assets untouched
    detail = client.get(f"/decks/{deck_id}/card/01001").text
    assert "/image/ahlcg/scans/01001@core.png?r=270" in detail and 'class="detail landscape"' in detail
    picker = client.get("/decks/identities?game=ahlcg").text
    assert "01001@core.png?r=270" in picker and "data-landscape" in picker
