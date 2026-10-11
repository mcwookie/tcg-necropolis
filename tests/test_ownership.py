"""Pack ownership: the Collection page, owned cards, and the deck builder's
"Only cards I own" / "Print only cards I don't own"."""

import pytest
from fastapi.testclient import TestClient

from tcgnecro import db, ownership, users
from tcgnecro.deckbuilder import registry, store
from tcgnecro.games import Card, CardVersion, Catalog, Pack
from tests.test_deckbuilder import CARDS

# Packs mirror Arkham's shapes: the original Core, a Revised Core that reprints
# Machete under a new code, and a cycle with a repackaged investigator box.
PACKS = [
    Pack("core", "Core Set", "2016", group="Core Set", group_order=1),
    Pack("rcore", "Revised Core Set", "2020", group="Core Set", group_order=1),
    Pack("dwl", "The Dunwich Legacy", "2017", group="The Dunwich Legacy", group_order=2),
    Pack("dwlp", "The Dunwich Legacy Investigator Expansion", "2024", group="The Dunwich Legacy", group_order=2),
]
IN_DWL = {"02001", "03001"}   # Zoey and Tony live in the Dunwich pack here


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    users._cache = None
    registry._pools.clear()
    catalog = Catalog("ahlcg", "Arkham Horror: The Card Game", packs=PACKS)
    for c in CARDS:
        pack = "rcore" if c.get("duplicate_of_code") else "dwl" if c["code"] in IN_DWL else "core"
        catalog.cards.append(Card(c["code"], c["name"] + (f" ({c['xp']})" if c.get("xp") else ""), "player", c))
        catalog.versions.append(CardVersion(c["code"], pack, c["position"]))
        if pack == "dwl":   # the repackaged box reprints the cycle's player cards
            catalog.versions.append(CardVersion(c["code"], "dwlp", c["position"]))
    conn = db.connect()
    db.store_catalog(conn, catalog)
    yield conn
    registry._pools.clear()
    users._cache = None


@pytest.fixture
def client(setup):
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Brian"})
    return c


def me():
    return users.by_name("Brian")


# ----- model -------------------------------------------------------------------

def test_not_set_up_is_different_from_owning_nothing(setup):
    user = users.create("Brian")
    assert users.owned_packs(user, "ahlcg") is None
    users.set_owned_packs(user.id, "ahlcg", set())
    assert users.owned_packs(users.get(user.id), "ahlcg") == set()
    users.set_owned_packs(user.id, "ahlcg", None)
    assert users.owned_packs(users.get(user.id), "ahlcg") is None


def test_repackaged_box_owns_the_cycle_cards(setup):
    assert ownership.owned_card_ids(setup, "ahlcg", {"dwlp"}) == IN_DWL


def test_reprint_codes_count_for_the_original_card(setup):
    user = users.create("Brian")
    users.set_owned_packs(user.id, "ahlcg", {"rcore"})
    pool = registry.pool(setup, "ahlcg")
    owned = ownership.owned_in_pool(setup, users.get(user.id), "ahlcg", pool)
    assert owned == {"01016"}     # 01516 (Revised Core Machete) -> 01016


def test_packs_are_grouped_for_the_collection_page(setup):
    groups = ownership.pack_groups(setup, "ahlcg")
    assert [g for g, _ in groups] == ["Core Set", "The Dunwich Legacy"]
    assert [p["id"] for p in groups[1][1]] == ["dwl", "dwlp"]
    assert groups[1][1][1]["cards"] == len(IN_DWL)


# ----- web ---------------------------------------------------------------------

def test_collection_page_saves_per_game(client):
    page = client.get("/collection/ahlcg").text
    assert "Not set up yet" in page and "The Dunwich Legacy Investigator Expansion" in page
    client.post("/collection/ahlcg", data={"pack": ["core", "dwlp", "bogus"]})
    assert users.owned_packs(me(), "ahlcg") == {"core", "dwlp"}
    assert "You own <b id=\"owned-count\">2</b> of 4 packs" in client.get("/collection/ahlcg").text
    client.post("/collection/ahlcg", data={"forget": "1"})
    assert users.owned_packs(me(), "ahlcg") is None


def _new_deck(client, identity="01001"):
    r = client.post("/decks", data={"game": "ahlcg", "identity": identity}, follow_redirects=False)
    return r.headers["location"].rsplit("/", 1)[1]


def test_owned_filter_defaults_on_once_a_collection_exists(client):
    deck_id = _new_deck(client)
    page = client.get(f"/decks/{deck_id}").text
    assert "Set up your collection" in page                   # no collection: filter disabled
    assert "Machete" in client.get(f"/decks/{deck_id}/cards").text  # not filtered

    users.set_owned_packs(me().id, "ahlcg", {"dwl"})          # owns no Machete
    page = client.get(f"/decks/{deck_id}").text
    assert 'name="owned" value="1" checked' in page
    cards = client.get(f"/decks/{deck_id}/cards").text
    assert "Machete" not in cards

    # Switched off, unowned cards show but are marked.
    cards = client.get(f"/decks/{deck_id}/cards?f=1&q=machete").text
    assert "Machete" in cards and "not owned" in cards


def test_deck_panel_counts_unowned_and_print_skips_owned(client):
    deck_id = _new_deck(client)
    store.update(deck_id, lambda d: d.set_count("01016", 2))
    users.set_owned_packs(me().id, "ahlcg", {"core"})          # owns everything in this deck
    panel = client.post(f"/decks/{deck_id}/slots/01016?delta=0").text
    assert "Not owned</span> <b>0</b>" in panel
    r = client.post(f"/decks/{deck_id}/pdf", data={"unowned_only": "true"})
    assert r.status_code == 400 and "own every card" in r.text


def test_identity_picker_can_show_only_owned(client):
    users.set_owned_packs(me().id, "ahlcg", {"dwl"})
    everyone = client.get("/decks/identities?game=ahlcg").text
    mine = client.get("/decks/identities?game=ahlcg&owned=1").text
    assert "Roland Banks" in everyone and "Roland Banks" not in mine
    assert "Zoey Samaras" in mine
