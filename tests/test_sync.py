"""Deck sync with ArkhamDB, against a fake ArkhamDB API."""

import urllib.error

import pytest
from fastapi.testclient import TestClient

from tcgnecro import db, users
from tcgnecro.cli import main as cli
from tcgnecro.deckbuilder import registry, store, sync
from tcgnecro.deckbuilder.model import Deck
from tcgnecro.games import Card, CardVersion, Catalog, Pack
from tests.test_deckbuilder import CARDS

API = "https://arkhamdb.com/api/public"


def remote(deck_id, name="Roland", slots=None, version="1.0", updated="2026-01-01T00:00:00+00:00", **extra):
    return {"id": deck_id, "name": name, "investigator_code": "01001", "version": version,
            "date_update": updated, "slots": slots or {"01006": 1, "01016": 2},
            "sideSlots": [], "meta": "", "next_deck": None, **extra}


@pytest.fixture
def fake(monkeypatch):
    """ArkhamDB as a dict: {"deck/100": {...}, "decklist/7": {...}}."""
    site = {}

    def fetch_json(url):
        key = url.removeprefix(API + "/")
        if key not in site:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return site[key]

    monkeypatch.setattr("tcgnecro.games.ahlcg.deckbuilder.fetch_json", fetch_json)
    return site


@pytest.fixture
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    users._cache = None
    registry._pools.clear()
    catalog = Catalog("ahlcg", "Arkham Horror: The Card Game", packs=[Pack("core", "Core Set", "2016")])
    for c in CARDS:
        catalog.cards.append(Card(c["code"], c["name"], "player", c))
        catalog.versions.append(CardVersion(c["code"], "core", c["position"]))
    db.store_catalog(db.connect(), catalog)
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Brian"})
    yield c
    registry._pools.clear()
    users._cache = None


def import_deck(client, source):
    r = client.post("/decks/import", data={"game": "ahlcg", "source": source}, follow_redirects=False)
    assert r.headers["location"].startswith("/decks/") and "error" not in r.headers["location"], r.headers["location"]
    return r.headers["location"].rsplit("/", 1)[1]


def test_import_links_the_deck(client, fake):
    fake["deck/100"] = remote(100)
    deck = store.load(import_deck(client, "https://arkhamdb.com/deck/view/100?deck_name=x"))
    assert deck.sync["provider"] == "arkhamdb" and deck.sync["remote_id"] == "100"
    assert deck.sync["status"] == "ok" and deck.slots == {"01006": 1, "01016": 2}
    assert deck.format == 3 and not sync.has_local_changes(deck)


def test_up_to_date_then_remote_edit_is_applied(client, fake):
    fake["deck/100"] = remote(100)
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/100")
    result = sync.sync_deck(deck_id)
    assert not result.changed and result.message == "Already up to date"

    fake["deck/100"] = remote(100, slots={"01006": 1, "01016": 1, "01030": 2}, version="1.1",
                              updated="2026-02-01T00:00:00+00:00")
    result = sync.sync_deck(deck_id)
    assert result.changed and result.message == "Updated to version 1.1"
    assert store.load(deck_id).slots == {"01006": 1, "01016": 1, "01030": 2}


def test_follows_the_upgrade_chain(client, fake):
    fake["deck/100"] = remote(100)
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/100")
    fake["deck/100"] = remote(100, next_deck=101)
    fake["deck/101"] = remote(101, slots={"01006": 1, "01017": 2}, version="2.0", next_deck=102)
    fake["deck/102"] = remote(102, slots={"01006": 1, "01017": 2, "01042": 1}, version="3.0")
    result = sync.sync_deck(deck_id)
    assert result.changed and "followed the upgrade to deck 102" in result.message
    deck = store.load(deck_id)
    assert deck.sync["remote_id"] == "102" and deck.sync["url"].endswith("/deck/view/102")
    assert deck.slots == {"01006": 1, "01017": 2, "01042": 1}


def test_local_changes_pause_sync_until_forced(client, fake):
    fake["deck/100"] = remote(100)
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/100")
    client.post(f"/decks/{deck_id}/slots/01030?delta=1")            # edited here
    fake["deck/100"] = remote(100, slots={"01006": 1, "01016": 1}, updated="2026-03-01T00:00:00+00:00")

    result = sync.sync_deck(deck_id)
    assert not result.changed and store.load(deck_id).sync["status"] == "local-changes"
    assert store.load(deck_id).slots["01030"] == 1                  # untouched

    result = sync.sync_deck(deck_id, force=True)
    assert result.changed and store.load(deck_id).slots == {"01006": 1, "01016": 1}
    assert store.load(deck_id).sync["status"] == "ok"


def test_name_follows_the_site_unless_renamed_here(client, fake):
    fake["deck/100"] = remote(100, name="Roland v1")
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/100")
    fake["deck/100"] = remote(100, name="Roland v2")
    sync.sync_deck(deck_id)
    assert store.load(deck_id).name == "Roland v2"

    client.post(f"/decks/{deck_id}/meta", data={"name": "My Roland"})
    fake["deck/100"] = remote(100, name="Roland v3", slots={"01006": 1})
    result = sync.sync_deck(deck_id)
    assert result.changed and store.load(deck_id).name == "My Roland"   # rename kept, cards updated
    assert store.load(deck_id).slots == {"01006": 1}


def test_unreadable_remote_is_recorded_not_raised(client, fake):
    fake["deck/100"] = remote(100)
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/100")
    del fake["deck/100"]                                            # made private / deleted
    result = sync.sync_deck(deck_id)
    assert not result.changed and "isn't readable" in result.message
    deck = store.load(deck_id)
    assert deck.sync["status"] == "error" and deck.slots == {"01006": 1, "01016": 2}


def test_decks_imported_before_syncing_can_be_linked(client, fake):
    fake["decklist/7"] = remote(7, name="Published")
    old = store.save(Deck(id="old1", game="ahlcg", name="Old import", identity="01001", slots={"01016": 1},
                          source="https://arkhamdb.com/decklist/view/7/published-1.0", owner=users.by_name("Brian").id))
    plugin = registry.plugin("ahlcg")
    assert sync.can_link(plugin, old)
    page = client.get("/decks/old1").text
    assert "Keep in sync with the source" in page
    r = client.post("/decks/old1/sync")
    assert r.headers.get("HX-Refresh") == "true"
    deck = store.load("old1")
    assert deck.sync["kind"] == "decklist" and deck.slots == {"01006": 1, "01016": 2}


def test_bare_ids_resolve_to_decklist_or_deck(client, fake):
    fake["deck/555"] = remote(555)
    deck = store.load(import_deck(client, "555"))
    assert deck.sync["kind"] == "deck" and deck.sync["url"] == "https://arkhamdb.com/deck/view/555"


def test_routes_permissions_copy_and_unsync(client, fake):
    fake["deck/100"] = remote(100)
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/100")
    assert "Synced with" in client.get(f"/decks/{deck_id}").text

    users.create("Friend")
    store.update(deck_id, lambda d: setattr(d, "visibility", "shared"))
    friend = TestClient(client.app)
    friend.post("/login", data={"user_id": users.by_name("Friend").id})
    assert friend.post(f"/decks/{deck_id}/sync").status_code == 403
    r = friend.post(f"/decks/{deck_id}/copy", follow_redirects=False)
    assert store.load(r.headers["location"].rsplit("/", 1)[1]).sync is None   # a copy is your own

    client.post(f"/decks/{deck_id}/unsync")
    assert store.load(deck_id).sync is None


def test_sync_all_and_cli(client, fake, capsys):
    fake["deck/100"] = remote(100)
    fake["deck/200"] = remote(200)
    a = import_deck(client, "https://arkhamdb.com/deck/view/100")
    import_deck(client, "https://arkhamdb.com/deck/view/200")
    store.save(Deck(id="local1", game="ahlcg", name="Not linked", identity="01001"))
    fake["deck/100"] = remote(100, slots={"01006": 1}, updated="2026-05-01T00:00:00+00:00")

    results = sync.sync_all()
    assert len(results) == 2 and sum(r.changed for _, r in results) == 1

    fake["deck/100"] = remote(100, slots={"01006": 1, "01016": 1}, updated="2026-06-01T00:00:00+00:00")
    cli(["decks", "sync", a])
    assert "Updated to" in capsys.readouterr().out


def test_campaign_experience_comes_from_arkhamdb(client, fake):
    fake["deck/300"] = remote(300, xp=5, xp_spent=None, xp_adjustment=None)
    deck_id = import_deck(client, "https://arkhamdb.com/deck/view/300")
    deck = store.load(deck_id)
    assert deck.meta["experience"] == {"earned": 5, "spent": 0, "adjustment": 0}

    stats = {s.label: s for s in registry.plugin("ahlcg").stats(deck, registry.pool(db.connect(), "ahlcg"))}
    assert stats["XP to spend"].value == "5" and "Earned 5" in stats["XP to spend"].title
    assert "XP in deck" in stats and "XP" not in stats          # the two figures are told apart
    assert 'title="Earned 5 for this upgrade' in client.get(f"/decks/{deck_id}").text

    # Spending happens on ArkhamDB; the next sync brings the new figures.
    fake["deck/300"] = remote(300, xp=5, xp_spent=7, xp_adjustment=1, updated="2026-07-01T00:00:00+00:00")
    sync.sync_deck(deck_id)
    deck = store.load(deck_id)
    stats = {s.label: s for s in registry.plugin("ahlcg").stats(deck, registry.pool(db.connect(), "ahlcg"))}
    assert stats["XP to spend"].value == "-1" and stats["XP to spend"].ok is False   # overspent
