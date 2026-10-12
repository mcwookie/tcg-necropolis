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
    catalog.cards = [Card("01001", "Roland Banks", "player"), Card("01016", "Machete", "player")]
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
    assert roland["sides"][0]["full"] == "/image/ahlcg/scans/01001@core.png"
    assert roland["sides"][1]["thumb"].endswith("01001@core~back.png")
    assert [s["label"] for s in machete["sides"]] == ["Front"]


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
