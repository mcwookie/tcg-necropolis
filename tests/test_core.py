import zipfile
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from tcgnecro import collections, db
from tcgnecro.decklist import (CardRequest, Printing, parse_decklist, parse_overrides,
                                 parse_quantity, resolve, select_printing)
from tcgnecro.games import get_game
from tcgnecro.naming import ImageName, back_index, parse_filename, validate
from tcgnecro.pdf import PdfOptions, build_pages, generate_pdf


def test_parse_filename():
    assert parse_filename("0235@core-set.png") == ImageName("0235", "core-set", "front", False)
    assert parse_filename("bfd01@balance-of-the-force~back.bleed.jpg") == \
        ImageName("bfd01", "balance-of-the-force", "back", True)
    assert parse_filename("no-at-sign.png") is None
    assert parse_filename("a@b@c.png") is None
    assert parse_filename("0235@core-set.txt") is None


def test_back_index():
    assert [back_index(s) for s in ("back", "back2", "back0", "front", "backx")] == [1, 2, None, None, None]


def test_validate_catches_missing_front_and_gaps():
    errors = validate([ImageName("1", "p", "back", False), ImageName("2", "p", "front", False),
                       ImageName("2", "p", "back2", False)])
    assert any("no front" in e for e in errors)
    assert any("missing back" in e for e in errors)


def test_parse_quantity_and_overrides():
    assert parse_quantity("3x Force Lightning") == (3, "Force Lightning")
    assert parse_quantity("2 Gotal Outcast") == (2, "Gotal Outcast")
    assert parse_quantity("Force Lightning") == (1, "Force Lightning")
    assert parse_overrides("Vader [Core-Set:12:mine]") == ("Vader", "core-set", 12, "mine")
    assert parse_overrides("Vader [alt]") == ("Vader", "alt", None, None)


def test_normalize_title():
    assert db.normalize_title("Vader's TIE (159.3)") == "vader_s_tie__159_3_"
    assert db.normalize_title("Éowyn") == "eowyn"


def _printing(**kw):
    base = dict(card_id="1", title="X", collection="c", pack_id="a", pack_name="A", variant=None,
                position=None, back_group=None, sort_key="01")
    return Printing(**{**base, **kw})


def test_select_printing_prefers_requested_pack_then_official():
    official_old = _printing(pack_id="a", sort_key="01")
    official_new = _printing(pack_id="b", sort_key="02")
    alt = _printing(pack_id=None, pack_name=None, variant="alt", sort_key=None)
    options = [alt, official_new, official_old]
    assert select_printing(CardRequest("X", "1", "b"), options) is official_new
    assert select_printing(CardRequest("X", "1", "alt"), options) is alt
    assert select_printing(CardRequest("X", "1"), options) is official_old


def test_double_sided_backs_are_mirrored():
    cards = [_printing(back_group="light", title=str(i)) for i in range(2)]
    options = PdfOptions(double_sided=True, back_label="proxy")
    fronts, backs = build_pages(cards, options, "swlcg")
    assert [i for i, _ in fronts] == [0, 1]
    assert [i for i, _ in backs] == [2, 1]  # 3 columns: col 0 -> 2, col 1 -> 1
    assert all(slot and "light_proxy" in slot[0] for _, slot in backs)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path / "data"))
    conn = db.connect()
    db.store_catalog(conn, get_game("swlcg").load_catalog())
    return tmp_path, conn


def _make_archive(path, names):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.toml", 'game = "swlcg"\nversion = "1"\nlanguage = "en"\n')
        for name in names:
            img = Image.new("RGB", (63, 88), "red")
            with zf.open(f"images/{name}", "w") as f:
                img.save(f, "PNG")


def test_import_resolve_and_pdf(data):
    tmp, conn = data
    archive = tmp / "mini.zip"
    _make_archive(archive, ["0235@core-set.png", "0235@alt-art.png"])
    collections.import_archive(archive, progress=lambda _: None)
    assert collections.reindex(conn) == {("swlcg", "mini"): 2}

    requests, not_found = parse_decklist(conn, "swlcg", "2x Force Lightning\nForce Lightning [alt-art]\nNope")
    res = resolve(conn, "swlcg", requests, not_found)
    assert [p.label for p in res.printings] == ["Core Set", "Core Set", "alt-art"]
    assert res.not_found == ["Nope"]

    pdf = generate_pdf(res.printings, PdfOptions(), "swlcg")
    assert pymupdf.open(stream=pdf).page_count == 1


def test_import_rejects_invalid_archive(data):
    tmp, _ = data
    archive = tmp / "bad.zip"
    _make_archive(archive, ["0235@core-set~back.png"])
    with pytest.raises(collections.CollectionError, match="no front"):
        collections.import_archive(archive)
    assert not (tmp / "data" / "collections" / "swlcg" / "bad").exists()


# ----- archive layouts -----------------------------------------------------------

MANIFEST = 'game = "swlcg"\nversion = "1"\nlanguage = "en"\n'


def _zip(path, files: dict):
    png = __import__("io").BytesIO()
    Image.new("RGB", (6, 8)).save(png, "PNG")
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, png.getvalue() if content is None else content)
    return path


@pytest.mark.parametrize("layout", [
    {"manifest.toml": MANIFEST, "images/0235@core-set.png": None},                  # .pnx layout
    {"cards/manifest.toml": MANIFEST, "cards/images/0235@core-set.png": None},      # zipped folder
    {"manifest.toml": MANIFEST, "0235@core-set.png": None},                          # images at the top
    {"cards/manifest.toml": MANIFEST, "cards/0235@core-set.png": None,
     "__MACOSX/cards/._0235@core-set.png": "junk", "cards/.DS_Store": "junk"},      # macOS zip
])
def test_common_zip_layouts_import(data, layout):
    tmp, conn = data
    target = collections.import_archive(_zip(tmp / "scans.zip", layout), progress=lambda _: None)
    assert target.name == "scans"
    assert sorted(p.name for p in target.iterdir()) == ["0235@core-set.png", "manifest.toml"]
    assert collections.reindex(conn) == {("swlcg", "scans"): 1}


def test_zip_without_manifest_needs_game(data):
    tmp, conn = data
    archive = _zip(tmp / "loose.zip", {"0235@core-set.png": None})
    with pytest.raises(collections.CollectionError, match="--game"):
        collections.import_archive(archive)
    target = collections.import_archive(archive, game="swlcg", progress=lambda _: None)
    assert 'game = "swlcg"' in (target / "manifest.toml").read_text()
    with pytest.raises(collections.CollectionError, match="manifest says game 'swlcg', not 'ahlcg'"):
        collections.import_archive(_zip(tmp / "other.zip", {"manifest.toml": MANIFEST,
                                                            "0236@core-set.png": None}), game="ahlcg")


def test_archive_without_card_images_is_refused(data):
    tmp, _ = data
    archive = _zip(tmp / "empty.zip", {"manifest.toml": MANIFEST, "readme.txt": "hi", "photo.png": None})
    with pytest.raises(collections.CollectionError, match="no card images"):
        collections.import_archive(archive)
    assert not (tmp / "data" / "collections" / "swlcg" / "empty").exists()


def test_badly_named_files_are_skipped_and_reported(data):
    tmp, _ = data
    messages = []
    archive = _zip(tmp / "mixed.zip", {"manifest.toml": MANIFEST, "images/0235@core-set.png": None,
                                       "images/scan001.png": None})
    target = collections.import_archive(archive, progress=messages.append)
    assert not (target / "scan001.png").exists()
    assert any("skipped 1 file(s)" in m and "scan001.png" in m for m in messages)


@pytest.mark.parametrize("filename, name", [
    ("ahlcg.pnx", "ahlcg"), ("scans.zip", "scans"), ("scans.pnx.zip", "scans"), ("Scans.ZIP", "Scans"),
])
def test_collection_name_drops_archive_extensions(filename, name):
    assert collections.collection_name(Path(filename)) == name


def test_two_games_may_have_collections_with_the_same_name(data):
    tmp, conn = data
    db.store_catalog(conn, get_game("meccg").load_catalog())
    for game, image in (("swlcg", "0235@core-set.png"), ("meccg", "05001@against-the-shadow.png")):
        folder = tmp / "data" / "collections" / game / "scans"
        folder.mkdir(parents=True)
        Image.new("RGB", (6, 8)).save(folder / image)
    assert collections.reindex(conn, warn=lambda _: None) == {("meccg", "scans"): 1, ("swlcg", "scans"): 1}

    with pytest.raises(collections.CollectionError, match="say which with --game"):
        collections.remove("scans")
    collections.remove("scans", game="meccg")
    assert collections.reindex(conn, warn=lambda _: None) == {("swlcg", "scans"): 1}
