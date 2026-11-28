"""Arkham deck simulator (games/ahlcg/simulate.py) and the shared simulation
pieces (deckbuilder/simulate.py)."""

import json
import random

import pytest

from tcgnecro.deckbuilder import simulate as core
from tcgnecro.deckbuilder.model import Deck
from tcgnecro.games.ahlcg import simulate as sim
from tcgnecro.games.ahlcg.deckbuilder import PLUGIN as AH
from tests.test_deckbuilder import client  # noqa: F401  (fixture)


def card(code, name, type_code="asset", **extra):
    return {"code": code, "name": name, "real_name": name, "type_code": type_code, "faction_code": "neutral",
            "xp": 0, "pack_code": "core", "position": int(code[-3:]), "quantity": 2, "deck_limit": 2,
            "text": extra.pop("text", ""), **extra}


# A plain investigator: 3 in everything, elder sign +2. Real codes for cards
# with entries in sim_effects.json, so their effects apply.
INVESTIGATOR = card("90001", "Test Investigator", "investigator", xp=None, deck_limit=1,
                    skill_willpower=3, skill_intellect=3, skill_combat=3, skill_agility=3,
                    text="[elder_sign] effect: +2.", deck_requirements={"size": 30, "card": {}},
                    deck_options=[{"faction": ["neutral"], "level": {"min": 0, "max": 5}}])
CARDS = [
    INVESTIGATOR,
    card("01030", "Magnifying Glass", cost=1, real_slot="Hand", skill_intellect=1,
         text="Fast. You get +1 [intellect] while investigating."),
    card("01089", "Guts", "skill", skill_willpower=2,
         text="Max 1 committed per skill test. If this test is successful, draw 1 card."),
    card("01039", "Deduction", "skill", skill_intellect=1,
         text="If this skill test is successful while investigating a location, discover 1 additional clue."),
    card("01088", "Emergency Cache", "event", cost=0, text="Gain 3 resources."),
    card("99001", "Mystery Gadget", cost=2, real_slot="Accessory", skill_agility=1,
         text="[action] Exhaust: Something no simulator understands."),
    card("01096", "Amnesia", "treachery", subtype_code="basicweakness", xp=None,
         text="<b>Revelation</b> - Discard all but 1 card from your hand."),
]


@pytest.fixture
def pool():
    return AH.build_pool([(c["code"], c["name"], c) for c in CARDS])


class Params(dict):
    """A query string as parse_settings sees it."""

    def getlist(self, key):
        value = self.get(key)
        return value if isinstance(value, list) else ([value] if value is not None else [])


def values(**chosen):
    v = core.parse_settings(sim.settings(), Params())
    v.update(runs=200, rounds=6, seed="7")
    v.update(chosen)
    return v


def deck(**slots):
    return Deck(id="t", game="ahlcg", name="t", identity="90001",
                slots={k.lstrip("_"): v for k, v in slots.items()})


def table(report, title):
    section = next(s for s in report.sections if s.title == title)
    return section


def chances(report, row="Investigate"):
    grid = table(report, "Chance to pass").blocks[0]
    cells = next(r for r in grid.rows if r[0].text == row)[1:]
    return [float(c.text.rstrip("%")) for c in cells]


# ----- chaos bag ---------------------------------------------------------------

def test_tokens_parse_with_aliases_and_report_unknown_words():
    tokens, problems = sim.parse_tokens("+1 0, -2 skull Elder-Thing tentacle star −3 banana")
    assert tokens == [1, 0, -2, "skull", "elder_thing", "auto_fail", "elder_sign", -3]
    assert problems == ["Unknown chaos token 'banana'"]


def test_pass_chance_counts_tokens_and_auto_fail_always_fails():
    setup = sim.Setup(inv=sim.Investigator("x", {s: 3 for s in sim.SKILLS}, 2), cards=[], action="investigate",
                      difficulty=3, treachery=None, treachery_difficulty=3, goal=None, mulligan=False,
                      enemy_health=3, weakness_actions=1, rounds=1, challenges=["investigate"],
                      bag=[("0", 0, False), ("-1", -1, False), ("auto_fail", 0, True), ("elder_sign", 2, False)])
    assert setup.pass_chance(0) == 0.5        # 0 and elder sign pass, -1 and auto-fail don't
    assert setup.pass_chance(1) == 0.75
    assert setup.pass_chance(99) == 0.75      # auto-fail still fails
    assert setup.pass_chance(-3) == 0


def test_bundled_bags_parse():
    for bag in sim.load_bags():
        tokens, problems = sim.parse_tokens(bag["tokens"])
        assert tokens and not problems, bag["key"]


# ----- effects table -----------------------------------------------------------

def test_bundled_effects_are_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    effects, warnings = sim.load_effects()
    assert warnings == []
    assert len(effects) > 150
    assert effects["01030"]["name"] == "Magnifying Glass"


def test_effect_checks_catch_mistakes():
    problems = sim.check_effects({
        "1": {"name": "A", "abilities": [{"action": "investigate", "skill": "wits"}]},
        "2": {"name": "B", "commit": {"success": {"clue": 1}}},
        "3": {"name": "C", "bonus": {"intellect": 1}},
        "4": {"name": "D", "commit": {"better": {"draw": 2}}},
        "5": {"name": "E", "skill_bonus": {"intellect": 1}},
    })
    assert any("unknown skill 'wits'" in p for p in problems)
    assert any("unknown key 'clue'" in p for p in problems)
    assert any("unknown key 'bonus'" in p for p in problems)
    assert any("needs 'by'" in p for p in problems)
    assert not any(p.startswith("5 ") for p in problems)


def test_server_effects_override_bundled_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    folder = tmp_path / "custom" / "ahlcg"
    folder.mkdir(parents=True)
    (folder / "sim_effects.json").write_text(json.dumps({
        "01030": None,                                                    # removed
        "99001": {"name": "Mystery Gadget", "skill_bonus": {"agility": 2}},  # added
        "99002": {"name": "Broken", "skill_bonus": {"luck": 1}},          # skipped
    }))
    effects, warnings = sim.load_effects()
    assert "01030" not in effects
    assert effects["99001"]["skill_bonus"] == {"agility": 2}
    assert "99002" not in effects and any("luck" in w for w in warnings)


# ----- the engine --------------------------------------------------------------

def test_same_seed_same_results(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    d = deck(_01030=2, _01089=2, _01039=2, _01088=2, _99001=2, _01096=1)
    a, b = sim.run(d, pool, values()), sim.run(d, pool, values())
    assert chances(a) == chances(b)
    assert a.summary.startswith("200 games of 6 rounds · Test Investigator")


def test_chance_falls_with_difficulty_without_commits(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    report = sim.run(deck(_01030=2, _01089=2, _99001=2), pool, values(commit="none"))
    row = chances(report)
    assert row == sorted(row, reverse=True)
    assert row[0] > row[-1]


def test_assets_and_commits_raise_the_chance(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    plain = chances(sim.run(deck(_99001=2), pool, values(commit="none")))
    glass = chances(sim.run(deck(_01030=2, _99001=2), pool, values(commit="none")))
    assert glass[3] > plain[3]          # Magnifying Glass: +1 intellect while investigating
    skills = deck(_01039=2, _99001=2)
    never = chances(sim.run(skills, pool, values(commit="none")))
    always = chances(sim.run(skills, pool, values(commit="max")))
    assert always[3] > never[3]         # committing Deduction helps


def test_report_lists_weaknesses_and_partly_modelled_cards(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    report = sim.run(deck(_01030=2, _99001=2, _01096=1), pool, values(rounds=10))
    weak = table(report, "Weaknesses").blocks[0]
    assert weak.rows[0][0].text == "Amnesia"
    notes = table(report, "How this deck was modelled").blocks[0].lines
    assert notes[0] == "1 of the deck's 2 different cards are fully modelled."
    assert "Mystery Gadget" in notes[1]
    actions = table(report, "Resources and actions").blocks[2]
    assert next(r for r in actions.rows if r[0].text == "Lost to weaknesses")[1].text != "0.00"


def test_typed_in_bag_and_its_problems(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    report = sim.run(deck(_99001=2), pool, values(bag="custom", custom_bag="+1 +1 0 nonsense", commit="none"))
    assert "your bag" in report.summary
    assert report.warnings == ["Unknown chaos token 'nonsense'"]
    # intellect 3 against difficulty 1-4 with only +1/0 tokens: always passes up to 3
    assert chances(report)[:3] == [100.0, 100.0, 100.0]


def test_pump_spends_resources_only_when_needed():
    inv = sim.Investigator("x", {s: 2 for s in sim.SKILLS}, 0)
    pump = sim.SimCard("01034", "Hyperawareness", "Hyperawareness", "asset", 2, {s: 0 for s in sim.SKILLS}, [],
                       set(), False, False, False, False,
                       {"pump": {"skills": ["intellect", "agility"], "cost": 1, "value": 1}}, True)
    setup = sim.Setup(inv=inv, cards=[], action="investigate", difficulty=3, treachery=None,
                      treachery_difficulty=3, goal=0.75, mulligan=False, enemy_health=3, weakness_actions=1,
                      rounds=1, challenges=["investigate"], bag=[("0", 0, False), ("-1", -1, False)])
    run = sim.Run(setup, random.Random(1), sim.Stats(setup, 1))
    run.play = [sim.InPlay(pump)]
    run.resources = 5
    value, cards, pumps, short = run.plan("intellect", "investigate", 2, 3)
    assert value == 4 and len(pumps) == 2 and not short   # +2 for a 100% chance
    assert run.plan("intellect", "investigate", 4, 3)[2] == []   # already sure
    run.resources = 2                                           # keeps 2 in reserve
    assert run.plan("intellect", "investigate", 2, 3)[2] == []


# ----- shared pieces -----------------------------------------------------------

def test_settings_parse_clamps_and_reads_checkboxes():
    settings = sim.settings()
    v = core.parse_settings(settings, Params(runs="999999", rounds="x", bag="nope"))
    assert v["runs"] == 5000 and v["rounds"] == 10 and v["bag"] == "notz-standard"
    assert v["mulligan"] is True and "investigate" in v["challenges"]   # defaults until submitted
    v = core.parse_settings(settings, Params(run="1", challenges=["fight", "bogus"]))
    assert v["mulligan"] is False and v["challenges"] == ["fight"]


def test_band_chart_draws_svg():
    chart = core.SimChart("Hand", ["1", "2", "3"], [core.band("Hand", [[5, 5], [3, 4], [2, 6]])])
    svg = str(chart.svg())
    assert svg.startswith('<svg class="band-chart"') and "<polyline" in svg and "<polygon" in svg


# ----- comparing two decks -----------------------------------------------------

def test_delta_reads_percentages_decimals_and_text():
    assert core.delta("42%", "50%") == "+8"
    assert core.delta("3.1", "2.7") == "−0.4"
    assert core.delta("1,000", "1,250") == "+250"
    assert core.delta("12%", "12%") == "±0"
    assert core.delta("5", "–") is None and core.delta("40%", "4.0") is None


def test_compare_reports_lines_up_two_decks(pool, tmp_path, monkeypatch):
    monkeypatch.setenv("TCGNECRO_DATA", str(tmp_path))
    plain = sim.run(deck(_99001=2, _01096=1), pool, values(commit="none"))
    glass = sim.run(deck(_01030=2, _99001=2), pool, values(commit="none"))
    report = core.compare_reports(plain, glass, "Plain", "With glass")
    assert [s.title for s in report.sections] == [s.title for s in plain.sections]
    grid = table(report, "Chance to pass").blocks[0]
    a_row = next(r for r in grid.rows if r[0].text == "Investigate · A")
    b_row = grid.rows[grid.rows.index(a_row) + 1]
    assert b_row[0].text == "Investigate · B"
    assert a_row[4].delta is None and b_row[4].delta.startswith("+")   # the glass helps at difficulty 4
    # Stats become a figure / A / B / change table
    stats = table(report, "Spare actions: investigating").blocks[0]
    assert stats.headers == ["", "A", "B", "Change"] and stats.rows[0][0].text == "Clues per game"
    # One chart with both decks, in different series
    chart = table(report, "Spare actions: investigating").blocks[1]
    assert [b.label for b in chart.bands] == ["A · Clues", "B · Clues"]
    assert 'class="mid s1"' in str(chart.svg()) and len(chart.legend()) == 2
    # Rows one deck lacks appear once; sections one deck lacks are labelled
    assets = table(report, "Assets in play").blocks[0]
    assert [r[0].text for r in assets.rows if "Magnifying" in r[0].text] == ["Magnifying Glass · B"]
    assert table(report, "Weaknesses").intro.startswith("Only for A (Plain)")
    notes = table(report, "How this deck was modelled").blocks[0].lines
    assert any(line.startswith("A: 0 of the deck's 1") for line in notes)
    assert any(line.startswith("Investigator abilities") or "Not simulated" in line for line in notes)


def test_games_without_a_simulator_offer_none():
    from tcgnecro.deckbuilder import registry
    assert registry.plugin("swlcg").sim_settings(Deck(id="t", game="swlcg", name="t"), None) == []


# ----- web ---------------------------------------------------------------------

def test_web_simulate_page_and_run(client):  # noqa: F811
    r = client.post("/decks", data={"game": "ahlcg", "identity": "01001"}, follow_redirects=False)
    deck_id = r.headers["location"].rsplit("/", 1)[1]
    client.post(f"/decks/{deck_id}/slots/01016?delta=2")
    assert f'href="/decks/{deck_id}/simulate"' in client.get(f"/decks/{deck_id}").text
    page = client.get(f"/decks/{deck_id}/simulate")
    assert page.status_code == 200 and 'name="custom_bag"' in page.text
    r = client.get(f"/decks/{deck_id}/simulate/run?run=1&runs=100&rounds=3&challenges=fight&action=fight")
    assert r.status_code == 200
    assert "Chance to pass" in r.text and "Spare actions: fighting" in r.text
    assert r.headers["HX-Replace-Url"].startswith(f"/decks/{deck_id}/simulate?run=1&runs=100")
    assert client.get("/decks/nosuchdeck/simulate").status_code == 404


def test_web_simulate_compares_two_decks(client):  # noqa: F811
    ids = []
    for qty in (1, 2):
        r = client.post("/decks", data={"game": "ahlcg", "identity": "01001", "name": f"Deck {qty}"},
                        follow_redirects=False)
        ids.append(r.headers["location"].rsplit("/", 1)[1])
        client.post(f"/decks/{ids[-1]}/slots/01016?delta={qty}")
    a, b = ids
    page = client.get(f"/decks/{a}/simulate").text
    assert 'name="vs"' in page and f'value="{b}"' in page and f'value="{a}"' not in page
    r = client.get(f"/decks/{a}/simulate/run?run=1&runs=100&rounds=3&vs={b}")
    assert r.status_code == 200
    assert "Deck 2" in r.text and "Investigate · B" in r.text and "for both decks" in r.text
    assert f"vs={b}" in r.headers["HX-Replace-Url"]
    # A fixed seed is used as given; the same link gives the same figures
    url = f"/decks/{a}/simulate/run?run=1&runs=100&rounds=3&seed=5&vs={b}"
    first, second = client.get(url).text, client.get(url).text
    assert "for both decks" not in first
    assert first.split("Changes are")[1] == second.split("Changes are")[1]   # after the timing line
    assert client.get(f"/decks/{a}/simulate/run?vs=nosuchdeck").status_code == 404
