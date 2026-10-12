"""Middle-earth CCG catalog: card backs and title uniqueness."""

from tcgnecro.db import normalize_title
from tcgnecro.games.meccg import back_group_for, load_catalog


def test_meccg_backs():
    assert [back_group_for(t) for t in ("Character", "Resource", "Hazard", "Site", "Region")] == \
           ["eye", "eye", "eye", "map", "map"]


def test_meccg_catalog_titles_are_unique_and_positions_are_card_numbers():
    catalog = load_catalog()
    titles = [normalize_title(c.title) for c in catalog.cards]
    assert len(titles) == len(set(titles))   # decklists resolve by title
    angmarim = next(v for v in catalog.versions if v.card_id == "05058")
    assert (angmarim.pack_id, angmarim.position) == ("against-the-shadow", 58)
