"""Arkham taboo lists: loading ArkhamDB's format and applying a list to a deck."""

import json

import pytest

from tcgnecro.deckbuilder import store
from tcgnecro.games import ahlcg
from tcgnecro.games.ahlcg.deckbuilder import PLUGIN as AH
from tcgnecro.games.ahlcg.deckbuilder import TabooPool
from tests.test_deckbuilder import CARDS, client, deck, messages, pool  # noqa: F401 (fixtures)

# As ArkhamDB's API sends them: `cards` is a JSON string.
API_TABOOS = [
    {"id": 1, "code": "001", "name": "Taboo List", "active": 0, "date_start": "2019-04-20",
     "cards": json.dumps([{"code": "01016", "xp": 1}])},
    {"id": 2, "code": "002", "name": "Taboo List", "active": 1, "date_start": "2020-10-15",
     "cards": json.dumps([
         {"code": "01516", "xp": 1},                                   # reprint code of Machete
         {"code": "01042", "xp": -1},                                  # Big Gun (4, exceptional) unchained
         {"code": "01040", "deck_limit": 0, "text": "Forbidden."},     # Emergency Cache
         {"code": "01030", "exceptional": True, "text": "This card gains the Exceptional keyword."},
         {"code": "01001", "text": "Roland can take Seeker cards up to level 4.",
          "deck_options": [{"faction": ["guardian", "neutral"], "level": {"min": 0, "max": 5}},
                           {"faction": ["seeker"], "level": {"min": 0, "max": 4}}]},
     ])},
]


@pytest.fixture
def taboos(monkeypatch):
    lists = ahlcg.normalize_taboos(API_TABOOS)
    monkeypatch.setattr(AH, "taboos", lambda: lists)
    return lists


def test_normalize_reads_api_strings_and_repo_lists():
    lists = ahlcg.normalize_taboos(API_TABOOS)
    assert sorted(lists) == [1, 2] and lists[2]["cards"]["01040"]["deck_limit"] == 0
    # arkhamdb-json-data: cards as a list, no id on older lists.
    repo = ahlcg.normalize_taboos([{"code": "003", "name": "Taboo List", "cards": [{"code": "01016", "xp": 2}]}])
    assert repo[3]["cards"]["01016"]["xp"] == 2 and repo[3]["code"] == "003"


def test_load_taboos_reads_the_cache_and_is_empty_without_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    assert ahlcg.load_taboos() == {}
    ahlcg.taboos_file().parent.mkdir(parents=True)
    ahlcg.taboos_file().write_text(json.dumps(API_TABOOS))
    assert sorted(ahlcg.load_taboos()) == [1, 2]


def test_no_taboo_leaves_the_pool_alone(pool, taboos):
    assert AH.deck_pool(deck(), pool) is pool


def test_view_is_cached_and_idempotent(pool, taboos):
    d = deck()
    d.meta["taboo_id"] = 2
    view = AH.deck_pool(d, pool)
    assert isinstance(view, TabooPool) and AH.deck_pool(d, pool) is view and AH.deck_pool(d, view) is view
    assert pool.get("01040").raw["deck_limit"] == 2          # the game's pool is untouched
    d.meta["taboo_id"] = ""                                   # picker's "None"
    assert AH.deck_pool(d, view) is pool


def test_chains_add_xp_but_keep_the_level(pool, taboos):
    d = deck(_01016=2, _01017=0, _01031=1)
    d.meta["taboo_id"] = "2"                                  # as the picker stores it
    assert AH.xp(d, pool) == 2 + 3                           # Machete (0) chained via its reprint code
    assert AH.is_legal(pool.get("01016"), d, pool)


def test_unchain_is_taken_before_exceptional_doubling(pool, taboos):
    d = deck(_01042=1)
    assert AH.xp(d, pool) == 8
    d.meta["taboo_id"] = 2
    assert AH.xp(d, pool) == 6                                # (4 - 1) * 2


def test_forbidden_cards_are_illegal_and_reported(pool, taboos):
    d = deck(_01006=1, _01040=1)
    d.meta["taboo_id"] = 2
    card = pool.get("01040")
    assert not AH.is_legal(card, d, pool) and AH.max_copies(card, d) == 0
    found = messages(AH.validate(d, pool))
    assert "Emergency Cache is forbidden by the taboo list" in found
    assert not any("isn't allowed" in m or "copies of" in m for m in found)


def test_gaining_exceptional_limits_copies(pool, taboos):
    d = deck(_01030=2)
    d.meta["taboo_id"] = 2
    assert AH.max_copies(pool.get("01030"), d) == 1
    assert "2 copies of Deduction (limit 2)" not in messages(AH.validate(d, pool))
    assert any(m.startswith("2 copies of Deduction (limit 1)") for m in messages(AH.validate(d, pool)))


def test_investigator_deck_options_are_replaced(pool, taboos):
    d = deck()
    deduction3 = pool.get("01031")
    assert not AH.is_legal(deduction3, d, pool)               # printed Roland: Seeker 0-2
    d.meta["taboo_id"] = 2
    assert AH.is_legal(deduction3, d, pool)                   # taboo: Seeker 0-4


def test_unknown_taboo_is_a_warning(pool, taboos):
    d = deck()
    d.meta["taboo_id"] = 9
    assert AH.deck_pool(d, pool) is pool
    problems = AH.validate(d, pool)
    assert any("Taboo list 9 isn't downloaded" in p.message and p.severity == "warning" for p in problems)


def test_picker_lists_newest_first(pool, taboos):
    d = deck()
    picker = next(s for s in AH.selections(d, pool) if s.key == "taboo_id")
    assert [v for v, _ in picker.options] == ["", "2", "1"] and picker.value == ""
    assert picker.options[1][1] == "Taboo List 002 (2020-10-15)"


def test_no_picker_without_downloaded_lists(pool, monkeypatch):
    monkeypatch.setattr(AH, "taboos", lambda: {})
    assert not any(s.key == "taboo_id" for s in AH.selections(deck(), pool))


def test_web_picker_badges_and_details(client, taboos):  # noqa: F811
    r = client.post("/decks", data={"game": "ahlcg", "identity": "01001"}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    assert "Taboo list" in client.get(f"/decks/{deck_id}").text

    client.post(f"/decks/{deck_id}/meta", data={"taboo_id": "2"})
    assert store.load(deck_id).meta["taboo_id"] == "2"
    rows = client.get(f"/decks/{deck_id}/cards?f=1&q=machete").text
    assert "+1 XP" in rows
    # Forbidden cards drop out of the "legal" list, and + is refused.
    assert "Emergency Cache" not in client.get(f"/decks/{deck_id}/cards?f=1&legal=1&q=cache").text
    client.post(f"/decks/{deck_id}/slots/01040?delta=1")
    assert "01040" not in store.load(deck_id).slots
    detail = client.get(f"/decks/{deck_id}/card/01016").text
    assert "Chained: +1 experience per copy." in detail
