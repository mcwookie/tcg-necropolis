"""Marvel Champions catalog: printed cards, reprints, titles, backs, rotation."""

from tcgnecro import collections, db
from tcgnecro.games import Catalog, Pack, display_rotation
from tcgnecro.games.marvel_champions import back_group_for, build, card_titles, printed_cards


def card(code, name, type_code="ally", pack="core", position=1, **extra):
    return {"code": code, "name": name, "type_code": type_code, "pack_code": pack,
            "position": position, **extra}


def test_printed_cards_drop_back_faces_and_plain_scheme_doubles():
    cards = [
        card("01001a", "Spider-Man", "hero", linked_to_code="01001b"),
        card("01001b", "Peter Parker", "alter_ego", hidden=True, linked_to_code="01001a"),
        card("01097", "The Break-In!", "main_scheme", position=97),        # plain double
        card("01097a", "The Break-In!", "main_scheme", position=97, linked_to_code="01097b"),
        card("01097b", "The Break-In!", "main_scheme", position=97, hidden=True, linked_to_code="01097a"),
        card("31002a", "Peni Parker", "alter_ego", hidden=True, linked_to_code="31002b"),  # nobody links to her
        card("09001", "Hidden promo", "ally", hidden=True),                              # hidden, unlinked
    ]
    assert [c["code"] for c in printed_cards(cards)] == ["01001a", "01097a", "31002a"]


def test_reprints_are_versions_of_the_original():
    cards = [
        card("01060", "Black Cat", position=60),
        card("40010", "Black Cat", pack="mut_gen", position=10, duplicate_of_code="01060"),
        card("50010", "Black Cat", pack="sm", position=10, duplicate_of_code="40010"),   # chained
    ]
    catalog_cards, versions = build(cards)
    assert [c.id for c in catalog_cards] == ["01060"]
    assert [(v.card_id, v.pack_id, v.api_id) for v in versions] == [
        ("01060", "core", "01060"), ("01060", "mut_gen", "40010"), ("01060", "sm", "50010")]


def test_titles_use_stage_then_pack_position_then_code():
    cards = [
        card("01094", "Rhino", "villain", position=94, stage="I"),
        card("01095", "Rhino", "villain", position=95, stage="II"),
        card("02001", "Rhino", "ally", pack="tgs", position=1),      # no stage: keeps plain title
        card("05023", "Endurance", "upgrade", pack="tgs", position=23),
        card("36026", "Endurance", "upgrade", pack="ant", position=26),
        card("60001a", "Twin", "hero", pack="x", position=1),
        card("60001b", "Twin", "hero", pack="x", position=1),        # same pack + position
        card("01134", "Ultron", "villain", position=134, stage="I"),  # unique name: no suffix
    ]
    titles = card_titles(cards)
    assert titles["01094"] == "Rhino (I)" and titles["01095"] == "Rhino (II)" and titles["02001"] == "Rhino"
    assert titles["05023"] == "Endurance" and titles["36026"] == "Endurance (ant 26)"
    assert {titles["60001a"], titles["60001b"]} == {"Twin", "Twin (60001b)"}
    assert titles["01134"] == "Ultron"
    assert len({db.normalize_title(t) for t in titles.values()}) == len(titles)


def test_backs_and_landscape_rotation():
    assert [back_group_for(card("x", "x", t)) for t in ("hero", "villain", "minion", "unknown")] == \
        ["player", "villain", "encounter", None]
    assert display_rotation("marvel_champions", {"type_code": "main_scheme"}) == 90
    assert display_rotation("marvel_champions", {"type_code": "side_scheme"}, "back") == 90
    assert display_rotation("marvel_champions", {"type_code": "ally"}) == 0


def test_images_named_by_reprint_code_match_the_original(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    conn = db.connect()
    cards = [card("01060", "Black Cat", position=60),
             card("40010", "Black Cat", pack="mut_gen", position=10, duplicate_of_code="01060")]
    catalog = Catalog("marvel_champions", "Marvel Champions",
                      packs=[Pack("core", "Core Set", "2019"), Pack("mut_gen", "Mutant Genesis", "2022")])
    catalog.cards, catalog.versions = build(cards)
    db.store_catalog(conn, catalog)
    folder = tmp_path / "collections" / "marvel_champions" / "scans"
    folder.mkdir(parents=True)
    for name in ("01060@core.jpg", "40010@mut_gen.jpg"):
        Image.new("RGB", (6, 8)).save(folder / name)
    collections.reindex(conn, warn=lambda _: None)
    rows = conn.execute("SELECT card_id, pack_id FROM images WHERE game_id = 'marvel_champions' ORDER BY pack_id").fetchall()
    assert [tuple(r) for r in rows] == [("01060", "core"), ("01060", "mut_gen")]
