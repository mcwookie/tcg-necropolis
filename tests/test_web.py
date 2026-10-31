"""Proxies page endpoints: preview data and image serving."""

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tcgnecro import collections, db, users
from tcgnecro.games import Card, CardVersion, Catalog, Pack


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    users._cache = None
    conn = db.connect()
    catalog = Catalog("ahlcg", "Arkham", packs=[Pack("core", "Core Set", "2016")])
    catalog.cards = [Card("01001", "Roland Banks", "player", {"type_code": "investigator"}),
                     Card("01016", "Machete", "player", {"type_code": "asset"})]
    catalog.versions = [CardVersion("01001", "core", 1), CardVersion("01016", "core", 16)]
    db.store_catalog(conn, catalog)
    folder = tmp_path / "collections" / "ahlcg" / "scans"
    folder.mkdir(parents=True)
    for name in ("01001@core.png", "01001@core~back.png", "01016@core.png"):
        Image.new("RGB", (630, 880), "gray").save(folder / name)
    collections.reindex(conn, warn=lambda _: None)
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Brian"})
    yield c
    users._cache = None


def test_preview_lists_every_side_with_full_size_urls(client):
    cards = client.post("/api/resolve", json={"game": "ahlcg", "decklist": "Roland Banks\nMachete"}).json()["cards"]
    roland, machete = cards
    assert [s["label"] for s in roland["sides"]] == ["Front", "Back"]
    # Investigators are landscape: every side is turned upright for display.
    assert roland["sides"][0]["full"] == "/image/ahlcg/scans/01001@core.png?r=270"
    assert roland["sides"][1]["thumb"].endswith("01001@core~back.png?r=270")
    assert roland["landscape"] and not machete["landscape"]
    assert [s["label"] for s in machete["sides"]] == ["Front"]
    assert machete["sides"][0]["full"] == "/image/ahlcg/scans/01016@core.png"


def test_full_image_is_the_original_and_stays_inside_collections(client):
    r = client.get("/image/ahlcg/scans/01016@core.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert Image.open(__import__("io").BytesIO(r.content)).size == (630, 880)   # not a thumbnail
    # Encoded so the client sends the dots to the route instead of normalizing them away.
    assert client.get("/image/ahlcg/..%2F..%2Findex.db").status_code == 404
    assert client.get("/image/%2e%2e/%2e%2e/index.db").status_code == 404
    assert client.get("/image/ahlcg/scans/missing.png").status_code == 404
    client.post("/logout")
    assert client.get("/image/ahlcg/scans/01016@core.png", follow_redirects=False).status_code == 303


def test_rotated_images_are_turned_and_cached(client, tmp_path):
    import io
    size = lambda r: Image.open(io.BytesIO(r.content)).size
    assert size(client.get("/image/ahlcg/scans/01001@core.png?r=270")) == (880, 630)
    assert size(client.get("/image/ahlcg/scans/01001@core.png?r=180")) == (630, 880)
    thumb = size(client.get("/thumb/ahlcg/scans/01001@core.png?r=90"))
    assert thumb[0] > thumb[1] and min(thumb) == 240        # short side stays sharp
    assert client.get("/thumb/ahlcg/scans/01001@core.png?r=45").status_code == 400
    assert len(list((tmp_path / "thumbs").glob("*.jpg"))) == 3


def test_rotation_rule_is_per_game():
    from tcgnecro.games import display_rotation
    assert [display_rotation("ahlcg", {"type_code": t}) for t in ("investigator", "act", "agenda", "asset", "location")] \
        == [270, 270, 270, 0, 0]
    assert display_rotation("ahlcg", {"type_code": "act"}, "back") == 270
    assert display_rotation("meccg", None) == display_rotation("swlcg", None) == 0


def test_index_is_portable_between_installs_sharing_a_data_folder(client, tmp_path, monkeypatch):
    """The Docker container and this PC share one data folder under different
    paths (/data vs /mnt/...). An index written by either must work for both."""
    import shutil
    conn = db.connect()
    stored = {r[0] for r in conn.execute("SELECT file_path FROM images")}
    assert stored == {"ahlcg/scans/01001@core.png", "ahlcg/scans/01001@core~back.png",
                      "ahlcg/scans/01016@core.png"}                    # relative, not absolute

    elsewhere = tmp_path / "other-mount"                              # same files, another path
    shutil.copytree(tmp_path / "collections", elsewhere / "collections")
    shutil.copy(tmp_path / "index.db", elsewhere / "index.db")
    shutil.copy(tmp_path / "users.json", elsewhere / "users.json")
    shutil.copy(tmp_path / "secret.key", elsewhere / "secret.key")
    monkeypatch.setenv("TCGNECRO_DATA", str(elsewhere))
    users._cache = None
    cards = client.post("/api/resolve", json={"game": "ahlcg", "decklist": "Machete"}).json()["cards"]
    assert client.get(cards[0]["thumb"]).status_code == 200


def test_old_absolute_paths_from_another_machine_are_re_rooted(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path / "data"))
    assert db.image_path("/data/collections/ahlcg/ahlcg/03020@ptc.jpg") == \
        tmp_path / "data" / "collections" / "ahlcg" / "ahlcg" / "03020@ptc.jpg"
    assert db.thumb_url("/mnt/elsewhere/data/collections/ahlcg/ahlcg/03020@ptc.jpg") == \
        "/thumb/ahlcg/ahlcg/03020@ptc.jpg"
    assert db.image_path("ahlcg/ahlcg/03020@ptc.jpg") == tmp_path / "data" / "collections" / "ahlcg" / "ahlcg" / "03020@ptc.jpg"


# ---- proxy list: pack picker, exact printings, imports ----------------------

@pytest.fixture
def two_packs(tmp_path, monkeypatch):
    """Machete in the Core Set and again in the Revised Core, each with its own
    scan; Knife in the Core Set with no scan at all."""
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    users._cache = None
    conn = db.connect()
    catalog = Catalog("ahlcg", "Arkham", packs=[Pack("core", "Core Set", "2016"), Pack("rcore", "Revised Core", "2021")])
    catalog.cards = [Card("01016", "Machete", "player", {"type_code": "asset", "quantity": 2}),
                     Card("01086", "Knife", "player", {"type_code": "asset", "quantity": 2}),
                     Card("01001", "Roland Banks", "player", {"type_code": "investigator", "quantity": 1})]
    catalog.versions = [CardVersion("01001", "core", 1), CardVersion("01016", "core", 16),
                        CardVersion("01086", "core", 86), CardVersion("01016", "rcore", 5)]
    db.store_catalog(conn, catalog)
    folder = tmp_path / "collections" / "ahlcg" / "scans"
    folder.mkdir(parents=True)
    for name in ("01001@core.png", "01016@core.png", "01016@rcore.png"):
        Image.new("RGB", (630, 880), "gray").save(folder / name)
    collections.reindex(conn, warn=lambda _: None)
    from tcgnecro.web import app
    c = TestClient(app)
    c.post("/login/setup", data={"name": "Brian"})
    yield c
    users._cache = None


def test_game_info_marks_owned_packs(two_packs):
    info = two_packs.get("/api/games/ahlcg").json()
    assert info["has_collection"] is False and not any(p["owned"] for p in info["packs"])
    users.set_owned_packs(users.by_name("Brian").id, "ahlcg", {"rcore"})
    info = two_packs.get("/api/games/ahlcg").json()
    assert info["has_collection"] is True
    assert {p["id"]: p["owned"] for p in info["packs"]} == {"core": False, "rcore": True}


def test_pack_cards_in_pack_order_with_quantities_and_images(two_packs):
    cards = two_packs.get("/api/games/ahlcg/packs/core/cards").json()["cards"]
    assert [(c["position"], c["title"], c["qty"], c["has_image"]) for c in cards] == [
        (1, "Roland Banks", 1, True), (16, "Machete", 2, True), (86, "Knife", 2, False)]
    machete = cards[1]
    assert machete["pack"] == "core" and machete["thumb"].endswith("01016@core.png") and machete["art_from"] is None
    assert cards[0]["landscape"] and not cards[1]["landscape"]
    assert two_packs.get("/api/games/ahlcg/packs/nope/cards").status_code == 404


def test_list_items_print_the_exact_printing_and_quantity(two_packs):
    body = {"game": "ahlcg", "items": [{"card_id": "01016", "pack": "rcore", "position": 5, "qty": 2},
                                       {"card_id": "01016", "pack": "core", "position": 16, "qty": 1},
                                       {"card_id": "01086", "pack": "core", "position": 86, "qty": 1},
                                       {"card_id": "99999", "pack": "core", "qty": 1}]}
    res = two_packs.post("/api/resolve", json=body).json()
    assert [c["printing"] for c in res["cards"]] == ["Revised Core", "Revised Core", "Core Set"]
    assert res["no_image"] == ["Knife"] and res["not_found"] == ["99999"]
    assert two_packs.post("/api/resolve", json={"game": "ahlcg", "items": [{"card_id": "01016", "qty": 100}]}).status_code == 422
    pdf = two_packs.post("/api/pdf", json=body)
    assert pdf.status_code == 200 and pdf.headers["content-disposition"].endswith('ahlcg-proxies.pdf"')


def test_pasted_decklist_becomes_list_rows(two_packs):
    res = two_packs.post("/api/parse", json={"game": "ahlcg",
                                             "decklist": "2x Machete [core]\nMachete [core]\nMachete\nKnife\nNobody"}).json()
    rows = {(r["card_id"], r["pack"]): r["qty"] for r in res["items"]}
    assert rows == {("01016", "core"): 3, ("01016", "rcore"): 1}     # unpinned Machete takes the newest printing
    assert res["no_image"] == ["Knife"] and res["not_found"] == ["Nobody"]
    assert two_packs.post("/api/parse", json={"game": "ahlcg", "decklist": "Machete [core"}).status_code == 400
