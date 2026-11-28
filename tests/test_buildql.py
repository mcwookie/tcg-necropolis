"""BuildQL queries (deckbuilder/buildql.py) over the synthetic Arkham pool from
test_deckbuilder, plus the query box in the deck builder's card list."""

import pytest

from tcgnecro.deckbuilder import buildql
from tcgnecro.deckbuilder.buildql import QueryError, QuerySchema
from tests.test_deckbuilder import AH, CARDS, card, client  # noqa: F401 (client is a fixture)

EXTRA = [
    card("05001", "Arcane Studies", faction="mystic", traits="Talent.", cost=2, xp=4,
         skill_willpower=1, skill_intellect=1, real_slot="Arcane", text="[action]: <b>Fight.</b> Deal 1 damage."),
    card("05002", "Beat Cop", traits="Ally. Police.", cost=4, health=2, sanity=2, real_slot="Ally", is_unique=False),
    card("05003", "Dr. Milan", faction="seeker", traits="Ally. Miskatonic.", cost=4, health=1, sanity=2,
         is_unique=True, real_slot="Ally", tags="hh."),
    card("05004", "Dual Wield", faction="guardian", faction2_code="rogue", type_code="event", cost=0, xp=0),
    card("05005", "Fight or Flight", faction="survivor", type_code="skill", text="Fight or flee."),
    card("05006", "Variable Cost", faction="neutral", type_code="event", cost=-2),
]


@pytest.fixture(scope="module")
def ah_pool():
    return AH.build_pool([(c["code"], c["name"], c) for c in CARDS + EXTRA])


def names(pool, text, counts=None, owned=None):
    schema = QuerySchema.build(AH.config.query, pool.cards.values())
    query = buildql.parse(text)
    query.check_fields(schema)
    ctx = buildql.Context(schema, counts or {}, owned, {"core": "Core Set"})
    return sorted({c.raw["name"] for c in pool.cards.values() if query.matches(c, ctx)})


def test_fuzzy_exact_and_lists(ah_pool):
    assert names(ah_pool, 'trait = "ally"') == ["Beat Cop", "Dr. Milan"]
    assert names(ah_pool, 'name = "dual"') == ["Dual Wield"]
    assert names(ah_pool, 'name = "arcane stud"') == ["Arcane Studies"]   # words in any part
    assert names(ah_pool, 'name == "machete"') == ["Machete"]
    assert names(ah_pool, 'name == "mach"') == []
    assert names(ah_pool, 'slot ? ["hand", "arcane"]') == ["Arcane Studies", "Machete"]
    assert names(ah_pool, 'trait ?? ["Police", "Spell"]') == ["Beat Cop", "Shrivelling"]


def test_text_fields_match_exact_substrings(ah_pool):
    assert names(ah_pool, 'text == "<b>Fight.</b>"') == ["Arcane Studies"]
    assert names(ah_pool, 'text = "fight"') == ["Arcane Studies", "Fight or Flight"]
    assert names(ah_pool, 'text ?? ["[action]", "flee."]') == ["Arcane Studies", "Fight or Flight"]


def test_negation_means_no_value_matches(ah_pool):
    # Beat Cop is Ally and Police: != "police" must reject it outright.
    assert names(ah_pool, 'trait = "ally" & trait != "police"') == ["Dr. Milan"]
    assert names(ah_pool, 'slot ? ["hand", "arcane", "ally"] & slot !? ["ally"]') == ["Arcane Studies", "Machete"]


def test_numbers_arithmetic_and_aliases(ah_pool):
    assert names(ah_pool, "health + sanity >= 4") == ["Beat Cop"]
    assert names(ah_pool, "hp < sa") == ["Dr. Milan"]
    assert names(ah_pool, "(health + sanity) * 2 = 6") == ["Dr. Milan"]
    assert names(ah_pool, "level >= 4 & willpower = 1") == ["Arcane Studies"]
    assert names(ah_pool, "lvl > 3 & book = 1 & f = mystic".replace("mystic", '"mystic"')) == ["Arcane Studies"]
    assert names(ah_pool, 'cost = "x"') == ["Variable Cost"]
    assert names(ah_pool, "cost % 2 = 1") == ["Machete"]
    assert names(ah_pool, "-cost = 2") == ["Variable Cost"]


def test_precedence_grouping_booleans_and_null(ah_pool):
    # & binds tighter than |
    assert names(ah_pool, 'cost = 0 | cost = 4 & unique') == ["Dr. Milan", "Dual Wield"]
    assert names(ah_pool, '(cost = 0 | cost = 4) & unique = false') == ["Beat Cop", "Dual Wield"]
    assert names(ah_pool, "multiclass") == ["Dual Wield"]
    assert names(ah_pool, "heals_horror = true") == ["Dr. Milan"]
    assert "Machete" in names(ah_pool, "health = null")
    assert names(ah_pool, 'type = "asset" & sanity != null') == ["Beat Cop", "Dr. Milan"]


def test_regex_pack_deck_and_owned(ah_pool):
    assert names(ah_pool, "name = /^d/") == ["Deduction", "Dr. Milan", "Dual Wield"]
    assert names(ah_pool, "trait = /^(spell|police)$/") == ["Beat Cop", "Shrivelling"]
    assert len(names(ah_pool, 'pack = "core set"')) == len(names(ah_pool, 'pack == "core"')) > 10
    assert names(ah_pool, "in_deck > 0", counts={"01016": 2}) == ["Machete"]
    assert names(ah_pool, "owned", owned={"05002"}) == ["Beat Cop"]
    assert len(names(ah_pool, "owned = null")) > 10      # no collection set up


def test_raw_and_attrs_keys_work_by_name(ah_pool):
    assert names(ah_pool, "skill_intellect = 1 & xp = 0") == ["Deduction"]
    assert names(ah_pool, 'raw.faction2_code = "rogue"') == ["Dual Wield"]


@pytest.mark.parametrize("text, message", [
    ("cost >", "Expected a field"),
    ('trait = "ally', "Unclosed quote"),
    ("trait = spell", "Unknown field 'spell'"),
    ("cost = 1 cost = 2", "Expected & or |"),
    ("(cost = 1", r"Expected '\)'"),
    ("name = /[/", "Bad regular expression"),
    ("cost = [1, 2]", None),   # a list after = is allowed: any of
])
def test_errors_point_at_the_problem(ah_pool, text, message):
    schema = QuerySchema.build(AH.config.query, ah_pool.cards.values())
    if message is None:
        buildql.parse(text).check_fields(schema)
        return
    with pytest.raises(QueryError, match=message) as e:
        buildql.parse(text).check_fields(schema)
    assert e.value.pos is not None


def test_blank_query_is_none():
    assert buildql.parse("  ") is None


def test_every_game_config_loads_with_help_fields():
    from tcgnecro.deckbuilder import registry
    for game_id, plugin in registry.plugins().items():
        q = plugin.config.query
        assert q.help, game_id
        assert set(q.field_names()) == set(q.help)


def test_card_list_applies_query_and_reports_errors(client):  # noqa: F811
    r = client.post("/decks", data={"game": "ahlcg", "identity": "01001"}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/decks/{deck_id}").text
    assert 'name="query"' in page and "How to write queries" in page

    r = client.get(f"/decks/{deck_id}/cards", params={"f": "1", "query": 'trait = "weapon" & level = 2'})
    assert "Machete" in r.text and "Lucky Break" not in r.text
    assert "query=trait" in r.headers["HX-Push-Url"]

    r = client.get(f"/decks/{deck_id}/cards", params={"f": "1", "query": "cost >"})
    assert "Query not applied" in r.text and "Lucky Break" in r.text   # list left unfiltered
