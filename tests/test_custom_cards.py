"""Custom cards and packs (data/custom/<game>/): merged into every game's
catalog in its own format, checked, and never able to stop the official cards
loading."""

import json
import types

import pytest

from tcgnecro import db, games
from tcgnecro.games import Card, CardVersion, Catalog, Pack, custom, load_catalog


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    return tmp_path


def write(data, game, packs=None, cards=None):
    folder = data / "custom" / game
    folder.mkdir(parents=True, exist_ok=True)
    if packs is not None:
        (folder / "packs.json").write_text(json.dumps(packs))
    if cards is not None:
        (folder / "cards.json").write_text(json.dumps(cards))


def collect():
    messages = []
    return messages, messages.append


def ids(catalog):
    return {c.id for c in catalog.cards}


def test_no_custom_folder_changes_nothing(data):
    catalog = load_catalog("swlcg")
    assert catalog.custom_cards == 0 and len(catalog.cards) == len(games.get_game("swlcg").load_catalog().cards)


@pytest.mark.parametrize("game, packs, cards, card_id", [
    ("swlcg", [{"code": "zz-set", "name": "Fan Objectives", "position": 99}],
     [{"unique_id": "9991", "name": "Fan Objective", "pack_code": "zz-set", "card_type": "Objective",
       "side": "Light", "affiliation": "Jedi", "objective_set_number": 999, "objective_set_sequence": 1}], "9991"),
    ("meccg", None,
     [{"unique_id": "zz001", "name": "Fan Hobbit", "pack_code": "the-wizards", "card_type": "Character",
       "alignment": "Hero", "card_number": 999}], "zz001"),
    ("city_of_heroes", [{"code": "fan", "name": "Fan Set", "position": 9}],
     [{"id": "fan_001", "name": "Fan Hero", "pack_code": "fan", "number": 1, "type": "hero",
       "powersets": ["Archery"], "starting_powers": []}], "fan_001"),
])
def test_bundled_games_take_custom_cards_in_their_own_format(data, game, packs, cards, card_id):
    write(data, game, packs, cards)
    catalog = load_catalog(game)
    assert card_id in ids(catalog) and catalog.custom_cards == 1
    card = next(c for c in catalog.cards if c.id == card_id)
    assert card.attrs["name"] == cards[0]["name"]                       # the deck builder sees every field
    if packs:
        assert packs[0]["code"] in {p.id for p in catalog.packs}


def test_downloaded_games_take_custom_cards_in_the_sites_format(data, monkeypatch):
    from tcgnecro.games import ahlcg
    official_packs = [{"code": "core", "name": "Core Set", "available": "2016", "cycle_position": 1, "position": 1}]
    official_cards = [{"code": "01016", "name": "Machete", "pack_code": "core", "position": 16,
                       "type_code": "asset", "faction_code": "guardian", "xp": 0}]
    monkeypatch.setattr(ahlcg, "load_source", lambda refresh=False: (official_packs, official_cards))
    write(data, "ahlcg",
          [{"code": "zzfan", "name": "Fan Campaign", "available": "2026", "cycle_position": 900}],
          [{"code": "zz001", "name": "Machete", "pack_code": "zzfan", "position": 1, "type_code": "asset",
            "faction_code": "guardian", "xp": 0},
           {"code": "zz002", "name": "Homebrew Hero", "pack_code": "zzfan", "position": 2,
            "type_code": "investigator", "faction_code": "seeker"}])
    catalog = load_catalog("ahlcg")
    titles = {c.id: c.title for c in catalog.cards}
    assert catalog.custom_cards == 2 and len(set(titles.values())) == 3       # the second Machete gets its own title
    assert titles["01016"] != titles["zz001"]
    assert any(v.card_id == "zz001" and v.pack_id == "zzfan" for v in catalog.versions)
    assert next(c for c in catalog.cards if c.id == "zz002").back_group == "player"
    assert official_cards == [official_cards[0]] and len(official_packs) == 1   # the official lists are untouched


def test_bad_custom_records_are_skipped_with_a_reason(data):
    write(data, "meccg",
          [{"code": "the-wizards", "name": "Clash", "position": 1}, {"name": "No code"}],
          [{"unique_id": "05001", "name": "Reused", "pack_code": "the-wizards", "card_type": "Character",
            "alignment": "Hero", "card_number": 1},
           {"unique_id": "zz1", "name": "Lost", "pack_code": "nope", "card_type": "Character",
            "alignment": "Hero", "card_number": 1},
           {"unique_id": "zz2", "pack_code": "the-wizards"},
           "not a card",
           {"unique_id": "zz3", "name": "Fine", "pack_code": "the-wizards", "card_type": "Hazard",
            "alignment": "Neutral", "card_number": 2},
           {"unique_id": "zz3", "name": "Twice", "pack_code": "the-wizards", "card_type": "Hazard",
            "alignment": "Neutral", "card_number": 3}])
    messages, warn = collect()
    catalog = load_catalog("meccg", warn=warn)
    assert catalog.custom_cards == 1 and "zz3" in ids(catalog)
    text = "\n".join(messages)
    for expected in ("pack the-wizards: an official pack has that code", "missing code",
                     "card 05001: that id is already used", "card zz1: no pack 'nope'",
                     "card zz2: missing name, card_type, alignment, card_number", "entry 4 isn't a JSON object",
                     "card zz3: that id is already used"):
        assert expected in text, expected


def test_unreadable_files_and_build_errors_fall_back_to_the_official_cards(data, monkeypatch):
    (data / "custom" / "swlcg").mkdir(parents=True)
    (data / "custom" / "swlcg" / "cards.json").write_text("{not json")
    messages, warn = collect()
    assert load_catalog("swlcg", warn=warn).custom_cards == 0 and "not readable JSON" in messages[0]

    # A record that passes the checks but that the game's own code can't build.
    write(data, "swlcg", None, [{"unique_id": "x1", "name": "Odd", "pack_code": "core-set",
                                 "card_type": "Unit", "side": "Light"}])
    module = games.get_game("swlcg")
    real = module.build_catalog
    def picky(packs, cards):
        if any(c["unique_id"] == "x1" for c in cards):
            raise KeyError("objective_set_number")
        return real(packs, cards)
    monkeypatch.setattr(module, "build_catalog", picky)
    messages, warn = collect()
    catalog = load_catalog("swlcg", warn=warn)
    assert "x1" not in ids(catalog) and len(catalog.cards) > 1000
    assert "loaded the official cards only" in messages[0]


def test_games_without_a_split_loader_use_the_generic_format(data, monkeypatch):
    def official(refresh=False):
        catalog = Catalog("fake", "Fake Game", packs=[Pack("base", "Base Set", "01")])
        catalog.cards = [Card("b1", "Hero", "back", {"type": "hero"})]
        catalog.versions = [CardVersion("b1", "base", 1)]
        return catalog
    module = types.SimpleNamespace(GAME_ID="fake", NAME="Fake Game", load_catalog=official)
    monkeypatch.setattr(games, "get_game", lambda game_id: module)
    write(data, "fake", [{"code": "fan", "name": "Fan Pack", "sort_key": "99"}],
          [{"id": "f1", "name": "Hero", "pack": "fan", "position": 1, "back_group": "back", "type": "hero"},
           {"id": "f2", "name": "Lost", "pack": "nowhere"}])
    messages, warn = collect()
    catalog = load_catalog("fake", warn=warn)
    card = next(c for c in catalog.cards if c.id == "f1")
    assert catalog.custom_cards == 1 and card.title == "Hero (f1)" and card.attrs == {"name": "Hero", "type": "hero"}
    assert CardVersion("f1", "fan", 1) in catalog.versions and "no pack 'nowhere'" in messages[0]


def test_custom_cards_are_stored_and_printable_by_title(data):
    write(data, "city_of_heroes", None, [{"id": "fan_002", "name": "Fan Edge", "pack_code": "arena",
                                          "number": 999, "type": "edge"}])
    conn = db.connect()
    db.store_catalog(conn, load_catalog("city_of_heroes"))
    row = conn.execute("SELECT title FROM cards WHERE game_id = 'city_of_heroes' AND id = 'fan_002'").fetchone()
    assert row["title"] == "Fan Edge"
    assert custom.custom_dir("city_of_heroes") == data / "custom" / "city_of_heroes"
