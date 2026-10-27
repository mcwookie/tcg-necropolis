"""Profiles, sign-in and deck ownership."""

import re

import pytest
from fastapi.testclient import TestClient

from tcgnecro import users
from tcgnecro.cli import main as cli
from tcgnecro.deckbuilder import store
from tcgnecro.deckbuilder.model import Deck


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    users._cache = None
    yield tmp_path
    users._cache = None


@pytest.fixture
def app(data):
    from tcgnecro.web import app
    return app


def sign_in(app, name):
    client = TestClient(app)
    client.post("/login", data={"user_id": users.by_name(name).id})
    return client


# ----- profiles ----------------------------------------------------------------

def test_first_profile_is_admin_and_claims_old_decks(data):
    store.save(Deck(id="old1", game="ahlcg", name="Before profiles"))
    alice = users.create("Alice")
    bob = users.create("Bob")
    assert alice.is_admin and not bob.is_admin
    assert store.load("old1").owner == alice.id


def test_names_are_unique_and_last_admin_is_kept(data):
    alice = users.create("Alice")
    with pytest.raises(users.UserError):
        users.create("alice ")
    with pytest.raises(users.UserError):
        users.remove(alice.id)


def test_removing_a_profile_hands_its_decks_on(data):
    users.create("Alice")
    bob = users.create("Bob")
    store.save(Deck(id="d1", game="ahlcg", name="Bob's", owner=bob.id))
    with pytest.raises(users.UserError, match="owns 1 deck"):
        users.remove(bob.id)
    assert users.remove(bob.id, decks_to=users.by_name("Alice").id) == 1
    assert store.load("d1").owner == users.by_name("Alice").id


def test_cli_user_commands(data, capsys):
    cli(["user", "add", "Alice"])
    cli(["user", "add", "Bob", "--email", "bob@example.com"])
    cli(["user", "rename", "Bob", "Robert"])
    cli(["user", "admin", "Robert"])
    cli(["user", "list"])
    out = capsys.readouterr().out
    assert "Robert" in out and "bob@example.com" in out
    assert users.by_name("Robert").is_admin


def test_format_1_decks_load_without_owner(data):
    deck = Deck.from_json({"id": "x", "game": "ahlcg", "name": "old", "format": 1})
    assert deck.owner is None and deck.visibility == "private"


# ----- sign-in -----------------------------------------------------------------

def test_everything_requires_sign_in(app):
    client = TestClient(app)
    r = client.get("/decks", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fdecks"
    r = client.get("/decks", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.headers["HX-Redirect"].startswith("/login")
    assert client.get("/static/app.css").status_code == 200
    assert "Create the first profile" in client.get("/login").text


def test_setup_then_picker_then_sign_out(app):
    client = TestClient(app)
    client.post("/login/setup", data={"name": "Alice"})
    assert "Alice" in client.get("/decks").text
    assert client.post("/login/setup", data={"name": "Mallory"}).status_code == 403
    client.post("/logout")
    assert client.get("/decks", follow_redirects=False).status_code == 303
    page = client.get("/login").text
    assert "Who's using" in page and "Alice" in page


def test_next_redirect_stays_on_site(app):
    users.create("Alice")
    client = TestClient(app)
    r = client.post("/login", data={"user_id": users.by_name("Alice").id, "next": "//evil.example"},
                    follow_redirects=False)
    assert r.headers["location"] == "/"


def test_only_admins_add_profiles(app):
    users.create("Alice")
    users.create("Bob")
    bob = sign_in(app, "Bob")
    assert bob.post("/profile/users", data={"name": "Eve"}).status_code == 403
    alice = sign_in(app, "Alice")
    alice.post("/profile/users", data={"name": "Carol"})
    assert users.by_name("Carol") is not None


# ----- deck ownership ------------------------------------------------------------

def test_private_shared_and_copy(app):
    alice_id = users.create("Alice").id
    users.create("Bob")
    store.save(Deck(id="a1", game="ahlcg", name="Alice's deck", owner=alice_id, slots={"01016": 2}))
    alice, bob = sign_in(app, "Alice"), sign_in(app, "Bob")

    # Private: Bob can't see it at all, not even that it exists.
    assert bob.get("/decks/a1/export.json").status_code == 404
    assert bob.post("/decks/a1/copy").status_code == 404

    # Shared: Bob can read and copy, not change.
    store.update("a1", lambda d: setattr(d, "visibility", "shared"))
    assert bob.get("/decks/a1/export.json").status_code == 200
    assert bob.post("/decks/a1/slots/01016?delta=-1").status_code == 403
    assert bob.post("/decks/a1/meta", data={"name": "Mine now"}).status_code == 403
    assert bob.post("/decks/a1/delete").status_code == 403
    r = bob.post("/decks/a1/copy", follow_redirects=False)
    copy = store.load(r.headers["location"].rsplit("/", 1)[1])
    assert copy.owner == users.by_name("Bob").id and copy.visibility == "private"
    assert copy.slots == {"01016": 2}

    # The owner can change visibility; nonsense values are ignored.
    alice.post("/decks/a1/meta", data={"visibility": "public-ish"})
    assert store.load("a1").visibility == "shared"
    store.update("a1", lambda d: setattr(d, "visibility", "private"))
    assert bob.get("/decks/a1/export.json").status_code == 404


def test_admins_do_not_see_private_decks(app):
    users.create("Alice")                       # admin
    bob_id = users.create("Bob").id
    store.save(Deck(id="b1", game="ahlcg", name="Bob's", owner=bob_id))
    assert sign_in(app, "Alice").get("/decks/b1/export.json").status_code == 404


def test_time_zone_preference(app):
    users.create("Alice")
    alice = sign_in(app, "Alice")
    store.save(Deck(id="t1", game="ahlcg", name="Timed", owner=users.by_name("Alice").id))

    page = alice.get("/profile").text
    assert "Automatic: this device's time zone" in page and ">America/New_York<" in page
    assert alice.post("/profile", data={"name": "Alice", "timezone": "Mars/Olympus"},
                      follow_redirects=False).headers["location"].startswith("/profile?error=")
    alice.post("/profile", data={"name": "Alice", "timezone": "America/New_York"})
    assert users.by_name("Alice").prefs["timezone"] == "America/New_York"

    decks = alice.get("/decks").text
    assert 'data-tz="America/New_York"' in decks
    # Saving stamps the deck with the current time; it's rendered in New York time.
    assert re.search(r'<time datetime="[^"]+" data-fmt="datetime">\d{4}-\d\d-\d\d \d\d:\d\d E[DS]T</time>', decks)

    alice.post("/profile", data={"name": "Alice", "timezone": ""})     # back to automatic
    assert "timezone" not in users.by_name("Alice").prefs
    assert 'data-tz=""' in alice.get("/decks").text
