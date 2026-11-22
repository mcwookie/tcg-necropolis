"""Arkham Horror LCG deck simulator (Monte Carlo).

Plays a deck solo, thousands of times, with a fixed "policy" for what to do,
and reports how often each kind of skill test would pass, how the hand and
resources hold up, when assets come into play and when weaknesses show up.
docs/SIMULATOR.md explains the model for players; this docstring is the map
for developers.

What is modelled, per run:

- Setup: 5 resources, a 5-card opening hand (weaknesses set aside and
  replaced), an optional mulligan, permanents in play.
- Each round: mythos (from round 2: an optional treachery test), the
  investigation phase (3 actions, chosen by the policy in Run.take_action),
  upkeep (ready, draw 1, gain 1 resource, hand limit 8).
- Skill tests: base skill + bonuses from cards in play + committed icons
  (matching and wild) + resources spent on "pump" assets + a chaos token
  against the difficulty. Auto-fail always fails; the elder sign uses the
  investigator's printed "+N"; skull/cultist/tablet/elder thing count as a
  value the player chooses.
- Card text: cards listed in sim_effects.json (plus the server's own
  data/custom/ahlcg/sim_effects.json) get their effects; every other card
  still counts for its icons, cost and slots ("tier 1").

At the start of every turn, before acting, the run is "probed": for each
challenge (investigate, fight, evade, plain skill tests) and difficulty 1-6,
the exact chance of passing right now is worked out from the chaos bag, with
the cards in play, the commit policy and the hand as it is, without changing
anything. Averaging those gives the chance-to-pass table with far less noise
than counting passes and fails.

Not modelled: the encounter deck beyond one treachery test a round, enemies
attacking, damage and horror, locations and movement, investigator abilities
(other than the elder sign), multiplayer, taboo lists, customizable upgrades.
"""

import functools
import json
import random
import re
import time
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tcgnecro.deckbuilder.model import Deck, DeckCard, Pool
from tcgnecro.deckbuilder.simulate import (Cell, SimChart, SimNotes, SimReport, SimSection, SimSetting,
                                          SimStats, SimTable, band, pct)
from tcgnecro.games import custom

HERE = Path(__file__).parent
SKILLS = ("willpower", "intellect", "combat", "agility")
ACTION_SKILL = {"investigate": "intellect", "fight": "combat", "evade": "agility"}
SYMBOLS = ("skull", "cultist", "tablet", "elder_thing")
TOKEN_ALIASES = {
    "elderthing": "elder_thing", "elder-thing": "elder_thing", "autofail": "auto_fail",
    "auto-fail": "auto_fail", "tentacle": "auto_fail", "eldersign": "elder_sign",
    "elder-sign": "elder_sign", "star": "elder_sign",
}
DIFFICULTIES = (1, 2, 3, 4, 5, 6)
BASE_SLOTS = {"hand": 2, "arcane": 2, "accessory": 1, "body": 1, "ally": 1, "tarot": 1}
HAND_LIMIT = 8
# Challenge key -> (label, action or None for a plain skill test, skill)
CHALLENGES = {
    "investigate": ("Investigate", "investigate", "intellect"),
    "fight": ("Fight", "fight", "combat"),
    "evade": ("Evade", "evade", "agility"),
    "willpower": ("Willpower test", None, "willpower"),
    "agility": ("Agility test", None, "agility"),
    "combat": ("Combat test", None, "combat"),
    "intellect": ("Intellect test", None, "intellect"),
}
UNIT = {"investigate": ("clue", "clues"), "fight": ("damage", "damage"), "evade": ("evade", "evades")}
# Commit policy -> the chance of passing to commit up to (None: never commit;
# 1.0: as sure as the bag allows, since auto-fail can't be beaten).
COMMIT_GOAL = {"p50": 0.5, "p75": 0.75, "max": 1.0, "none": None}
COMMIT_LABELS = {"p50": "Until a 50% chance of passing", "p75": "Until a 75% chance of passing",
                 "max": "Until passing is as sure as it gets", "none": "Never"}
# How much the policy values each kind of reward when choosing how to act.
WEIGHTS = {"clues": 1.0, "damage": 1.0, "evades": 1.0, "resources": 0.3, "draw": 0.5, "actions": 0.8}

# ----- effects data ------------------------------------------------------------------

RESULT_KEYS = {"clues", "damage", "draw", "resources", "actions"}
EFFECT_KEYS = {
    "name", "note", "commit", "commit_only", "skill_bonus", "action_bonus", "uses", "abilities", "pump",
    "reactions", "turn_start", "extra_actions", "enter", "hand_size", "slots", "opening_play", "if_controls",
    "play", "test", "trigger", "rescue",
}
ABILITY_KEYS = {
    "action", "skill", "base", "add_skill", "bonus", "actions", "use", "exhaust", "discard", "discard_on",
    "success", "better", "instead", "damage_by_margin", "retry", "end_turn_on", "return_by",
}
PUMP_KEYS = {"skills", "when", "cost", "value", "value_when", "min_hand", "free", "use", "max"}
REACTION_KEYS = {"after", "when", "by", "exhaust"} | RESULT_KEYS
COMMIT_KEYS = {"when", "success", "better", "fail", "return_by", "return_on_fail"}
WHEN = {"investigate", "fight", "evade", "treachery", "any"}
TOKENS = set(SYMBOLS) | {"auto_fail", "elder_sign"}


def _check_result(where: str, value, better: bool = False) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where} should be an object"]
    allowed = RESULT_KEYS | ({"by"} if better else set())
    out = [f"{where}: unknown key '{k}'" for k in value if k not in allowed]
    out += [f"{where}.{k} should be a number" for k, v in value.items()
            if k in allowed and not isinstance(v, (int, float))]
    if better and "by" not in value:
        out.append(f"{where} needs 'by'")
    return out


def _check_ability(where: str, a) -> list[str]:
    if not isinstance(a, dict):
        return [f"{where} should be an object"]
    out = [f"{where}: unknown key '{k}'" for k in a if k not in ABILITY_KEYS]
    if a.get("action") not in ("investigate", "fight", "evade", "resources"):
        out.append(f"{where}: 'action' should be investigate, fight, evade or resources")
    skills = a.get("skill", [])
    for s in skills if isinstance(skills, list) else [skills]:
        if s not in SKILLS:
            out.append(f"{where}: unknown skill '{s}'")
    if a.get("add_skill") is not None and a["add_skill"] not in SKILLS:
        out.append(f"{where}: unknown skill '{a['add_skill']}'")
    for key in ("success",):
        if key in a:
            out += _check_result(f"{where}.{key}", a[key])
    if "better" in a:
        out += _check_result(f"{where}.better", a["better"], better=True)
    for t in a.get("discard_on", []) + a.get("end_turn_on", []):
        if t not in TOKENS:
            out.append(f"{where}: unknown token '{t}'")
    return out


def check_effects(effects: dict) -> list[str]:
    """Problems with an effects table, one message per problem (empty: fine)."""
    return [m for code, e in effects.items() for m in check_entry(code, e)]


def check_entry(code: str, e) -> list[str]:
    """Problems with one card's effects entry."""
    problems = []
    where = f"{code} ({e.get('name', '?')})" if isinstance(e, dict) else code
    if not isinstance(e, dict):
        return [f"{where}: should be an object"]
    problems += [f"{where}: unknown key '{k}'" for k in e if k not in EFFECT_KEYS]
    for skill_map_key in ("skill_bonus",):
        for s in e.get(skill_map_key, {}):
            if s not in SKILLS:
                problems.append(f"{where}: unknown skill '{s}' in {skill_map_key}")
    for when, bonus in e.get("action_bonus", {}).items():
        if when not in WHEN:
            problems.append(f"{where}: action_bonus for unknown '{when}'")
        problems += [f"{where}: unknown skill '{s}' in action_bonus" for s in bonus if s not in SKILLS]
    for i, a in enumerate(e.get("abilities", [])):
        problems += _check_ability(f"{where} ability {i + 1}", a)
    if "test" in e:
        problems += _check_ability(f"{where} test", e["test"])
    if "pump" in e:
        p = e["pump"]
        problems += [f"{where} pump: unknown key '{k}'" for k in p if k not in PUMP_KEYS]
        problems += [f"{where} pump: unknown skill '{s}'" for s in p.get("skills", []) if s not in SKILLS]
    for i, r in enumerate(e.get("reactions", [])):
        problems += [f"{where} reaction {i + 1}: unknown key '{k}'" for k in r if k not in REACTION_KEYS]
        if r.get("after") not in ("success", "fail"):
            problems.append(f"{where} reaction {i + 1}: 'after' should be success or fail")
        if r.get("when", "any") not in WHEN:
            problems.append(f"{where} reaction {i + 1}: unknown 'when'")
    if "commit" in e:
        c = e["commit"]
        problems += [f"{where} commit: unknown key '{k}'" for k in c if k not in COMMIT_KEYS]
        for key in ("success", "fail"):
            if key in c:
                problems += _check_result(f"{where} commit.{key}", c[key])
        if "better" in c:
            problems += _check_result(f"{where} commit.better", c["better"], better=True)
        if c.get("when", "any") not in WHEN:
            problems.append(f"{where} commit: unknown 'when'")
    for key in ("play", "enter", "turn_start"):
        if key in e:
            problems += _check_result(f"{where} {key}", e[key])
    if e.get("trigger") not in (None, "defeat", "fail"):
        problems.append(f"{where}: 'trigger' should be defeat or fail")
    if e.get("commit_only", "any") not in WHEN:
        problems.append(f"{where}: unknown commit_only")
    return problems


def load_effects() -> tuple[dict[str, dict], list[str]]:
    """The effects table: the bundled sim_effects.json with the server's
    data/custom/ahlcg/sim_effects.json laid over it (an entry there replaces
    the bundled one; null removes it). Returns (effects, warnings); entries
    with problems are left out and reported."""
    warnings = []
    effects = {k: v for k, v in json.loads((HERE / "sim_effects.json").read_text()).items()
               if not k.startswith("_")}
    path = custom.custom_dir("ahlcg") / "sim_effects.json"
    if path.exists():
        try:
            extra = json.loads(path.read_text())
            if not isinstance(extra, dict):
                raise ValueError("should be a JSON object keyed by card code")
        except (OSError, ValueError) as e:
            warnings.append(f"{path.name}: not used ({e})")
            extra = {}
        for code, entry in extra.items():
            if code.startswith("_"):
                continue
            if entry is None:
                effects.pop(code, None)
            else:
                effects[code] = entry
    for code in list(effects):
        bad = check_entry(code, effects[code])
        if bad:
            del effects[code]
            warnings += [f"Card effect skipped: {m}" for m in bad]
    return effects, warnings


@functools.cache
def load_bags() -> list[dict]:
    """The bags in chaos_bags.toml (read once; it ships with the code)."""
    with open(HERE / "chaos_bags.toml", "rb") as f:
        return tomllib.load(f)["bags"]


def parse_tokens(text: str) -> tuple[list, list[str]]:
    """'+1 0 -1 skull auto_fail' -> (tokens, problems). A token is a number or
    one of the symbol names."""
    out, problems = [], []
    for word in re.split(r"[\s,]+", text.strip().lower()):
        if not word:
            continue
        word = TOKEN_ALIASES.get(word, word).replace("−", "-")
        if re.fullmatch(r"[+-]?\d+", word):
            out.append(int(word))
        elif word in TOKENS:
            out.append(word)
        else:
            problems.append(f"Unknown chaos token '{word}'")
    return out, problems


# ----- cards ---------------------------------------------------------------------------


@dataclass(eq=False)
class SimCard:
    id: str
    name: str            # with level, as the deck builder shows it
    title: str           # printed name (uniqueness)
    type: str            # asset, event, skill, weakness or other
    cost: int
    icons: dict          # skill -> icons that count for it (matching + wild)
    slots: list
    traits: set
    fast: bool
    max_one: bool
    unique: bool
    permanent: bool
    effect: dict
    modelled: bool       # has an effects entry, or no text beyond its icons

    def relevant(self, action: str) -> float:
        """How much the policy wants this asset in play when its spare
        actions go to `action` (higher first)."""
        e = self.effect
        if e.get("extra_actions"):
            return 4
        skill = ACTION_SKILL[action]
        if any(a.get("action") == action for a in e.get("abilities", [])) \
                or e.get("skill_bonus", {}).get(skill) or e.get("action_bonus", {}).get(action) \
                or skill in e.get("pump", {}).get("skills", []):
            return 3
        if e.get("turn_start") or e.get("reactions") or e.get("enter") \
                or any(a.get("action") == "resources" for a in e.get("abilities", [])):
            return 2
        return 1 if e else 0.5


_TRIVIAL = re.compile(r"(Max 1 committed per skill test\.|Fast\.|<[^>]+>|\s)+")


def _slots(raw: dict) -> list[str]:
    out = []
    for part in (raw.get("real_slot") or "").split("."):
        part = part.strip().lower()
        if not part:
            continue
        m = re.fullmatch(r"(\w+) x(\d)", part)
        out += [m.group(1)] * int(m.group(2)) if m else [part]
    return out


def sim_card(card: DeckCard, effects: dict) -> SimCard:
    raw = card.raw
    text = raw.get("text") or ""
    effect = effects.get(card.id, {})
    kind = card.get("kind")
    ctype = "weakness" if kind == "weakness" else raw.get("type_code", "other")
    if ctype not in ("asset", "event", "skill", "weakness"):
        ctype = "other"
    wild = raw.get("skill_wild") or 0
    icons = {s: (raw.get(f"skill_{s}") or 0) + wild for s in SKILLS}
    cost = raw.get("cost")
    return SimCard(
        id=card.id, name=card.name, title=raw.get("name", card.name), type=ctype,
        cost=cost if isinstance(cost, int) and cost > 0 else 0,
        icons=icons, slots=_slots(raw),
        traits={t.strip() for t in (raw.get("traits") or "").split(".") if t.strip()},
        fast=bool(re.search(r"\bFast\.", text)),
        max_one="Max 1 committed per skill test" in text,
        unique=bool(raw.get("is_unique")), permanent=bool(raw.get("permanent")),
        effect=effect, modelled=card.id in effects or not _TRIVIAL.sub("", text),
    )


@dataclass
class Investigator:
    name: str
    skills: dict
    elder: int


def investigator(card: DeckCard) -> Investigator:
    raw = card.raw
    m = re.search(r"\[elder_sign\] effect: ?\+(\d+)", raw.get("text") or "")
    return Investigator(raw.get("name", card.name), {s: raw.get(f"skill_{s}") or 0 for s in SKILLS},
                        int(m.group(1)) if m else 0)


# ----- the simulation ------------------------------------------------------------------


@dataclass
class Setup:
    """Everything fixed for one simulation: the deck, the rules chosen on the
    form, and the chaos bag turned into a pass-chance table."""

    inv: Investigator
    cards: list               # one SimCard per copy in the deck
    action: str               # what spare actions go to
    difficulty: int
    treachery: str | None     # skill of the round's treachery test
    treachery_difficulty: int
    goal: float | None        # commit until this chance of passing (None: never)
    mulligan: bool
    enemy_health: int
    weakness_actions: int
    rounds: int
    challenges: list
    bag: list                 # (name, modifier, auto_fail)
    bag_label: str = ""
    pass_table: list = field(default_factory=list)

    OFFSET = 40

    def __post_init__(self):
        n = len(self.bag)
        self.pass_table = []
        for gap in range(-self.OFFSET, self.OFFSET + 1):
            ok = sum(1 for _, mod, fail in self.bag if not fail and gap + mod >= 0)
            self.pass_table.append(ok / n if n else 0.0)
        # The best chance the bag allows (auto-fail always fails); a goal
        # above it means "as sure as it gets".
        if self.goal is not None:
            self.goal = min(self.goal, max(self.pass_table))

    def pass_chance(self, gap: int) -> float:
        """Chance a test passes when skill - difficulty = gap, before the token."""
        return self.pass_table[max(0, min(len(self.pass_table) - 1, gap + self.OFFSET))]


class InPlay:
    __slots__ = ("card", "uses", "exhausted", "free")

    def __init__(self, card: SimCard):
        self.card = card
        self.uses = card.effect.get("uses", 0)
        self.exhausted = False
        self.free = card.effect.get("pump", {}).get("free", 0)


class Stats:
    """Totals gathered over all runs, turned into the report."""

    def __init__(self, setup: Setup, runs: int):
        r = setup.rounds
        self.runs = runs
        self.probe = {c: {d: 0.0 for d in DIFFICULTIES} for c in setup.challenges}
        self.probe_round = {c: [0.0] * r for c in setup.challenges}
        self.hand = [[] for _ in range(r)]
        self.resources = [[] for _ in range(r)]
        self.progress = [[] for _ in range(r)]
        self.defeats = []
        self.actions = Counter()
        self.tests = self.passes = 0
        self.treachery_tests = self.treachery_passes = 0
        self.short = 0             # tests below target with nothing to commit
        self.low_hand = 0          # turns starting with 0-1 cards
        self.stuck = 0             # turns with an asset you can't afford
        self.reshuffles = 0        # runs that drew through the deck
        self.assets: dict[str, list] = {}
        self.weaknesses: dict[str, list] = {}


class Run:
    """One game. Methods follow the order of a round."""

    def __init__(self, setup: Setup, rng: random.Random, stats: Stats):
        self.s, self.rng, self.stats = setup, rng, stats
        self.deck = [c for c in setup.cards if not c.permanent]
        rng.shuffle(self.deck)
        self.hand: list[SimCard] = []
        self.discard: list[SimCard] = []
        self.play = [InPlay(c) for c in setup.cards if c.permanent]
        self.resources = 5
        self.round = 0
        self.pending = 0           # actions owed to weaknesses
        self.bonus_actions = 0
        self.end_turn = False
        self.enemy_damage = 0
        self.progress = 0          # clues, damage or evades so far
        self.defeats = 0
        self.reshuffled = False
        self.first_asset: dict[str, int] = {}
        self.first_weakness: dict[str, int] = {}
        self._slots = None         # _slot_state cache

    # ----- setup --------------------------------------------------------------

    def opening(self):
        aside = []
        self._draw_opening(5, aside)
        if self.s.mulligan:
            out = [c for c in self.hand if not self._keep(c)]
            for c in out:
                self.hand.remove(c)
            self._draw_opening(len(out), aside)
            self.deck += out
        self.deck += aside
        self.rng.shuffle(self.deck)
        for c in list(self.hand):
            if c.effect.get("opening_play") and c.type == "asset" and self._placeable(c) is not None:
                self.hand.remove(c)
                self._enter(c)

    def _draw_opening(self, n: int, aside: list):
        while n > 0 and self.deck:
            c = self.deck.pop()
            if c.type == "weakness":
                aside.append(c)
            else:
                self.hand.append(c)
                n -= 1

    def _keep(self, c: SimCard) -> bool:
        if c.type == "asset":
            return c.cost <= 4
        if c.type == "event" and ("play" in c.effect or "test" in c.effect):
            return True
        return c.icons[ACTION_SKILL[self.s.action]] > 0

    # ----- drawing and resources ---------------------------------------------

    def draw(self, n: int = 1):
        for _ in range(n):
            if not self.deck:
                if not self.discard:
                    return
                # Rules: shuffle the discard pile back in (and take 1 horror).
                self.deck, self.discard = self.discard, []
                self.rng.shuffle(self.deck)
                self.reshuffled = True
            c = self.deck.pop()
            if c.type == "weakness":
                self.pending += self.s.weakness_actions
                self.first_weakness.setdefault(c.name, self.round)
                self.discard.append(c)
            else:
                self.hand.append(c)

    def gain(self, result: dict, margin: int | None = None):
        """Apply a result {clues, damage, draw, resources, actions}."""
        if not result:
            return
        if result.get("resources"):
            self.resources += result["resources"]
        if result.get("draw"):
            self.draw(result["draw"])
        if result.get("clues") and self.s.action == "investigate":
            self.progress += result["clues"]
        if result.get("damage"):
            self.deal(result["damage"])
        if result.get("actions"):
            self.bonus_actions += result["actions"]

    def deal(self, n: int):
        if self.s.action == "fight":
            self.progress += n
        self.enemy_damage += n
        if self.enemy_damage >= self.s.enemy_health:
            self.enemy_damage = 0
            self.defeats += 1
            for c in self.hand:   # Evidence! and friends
                if c.effect.get("trigger") == "defeat" and c.cost <= self.resources:
                    self._play_event(c, free_action=True)
                    break

    # ----- cards in play ------------------------------------------------------

    def _slot_state(self) -> tuple[dict, dict]:
        """(capacity, used) per slot, cached until the cards in play change."""
        if self._slots is None:
            cap, used = dict(BASE_SLOTS), {}
            for ip in self.play:
                for slot, n in ip.card.effect.get("slots", {}).items():
                    cap[slot] = cap.get(slot, 0) + n
                for slot in ip.card.slots:
                    used[slot] = used.get(slot, 0) + 1
            self._slots = (cap, used)
        return self._slots

    def _placeable(self, c: SimCard) -> list | None:
        """In-play cards to discard to make room for c ([] if there's room),
        or None if it can't be played (no room, or a unique copy in play)."""
        if c.unique and any(ip.card.title == c.title for ip in self.play):
            return None
        if not c.slots:
            return []
        cap, used = self._slot_state()
        drop = []
        for slot in set(c.slots):
            short = used.get(slot, 0) + c.slots.count(slot) - cap.get(slot, 0)
            if short <= 0:
                continue
            # Make room by discarding assets that have run out of uses.
            empty = [ip for ip in self.play if slot in ip.card.slots and ip.card.effect.get("uses")
                     and ip.uses <= 0 and ip not in drop]
            for ip in empty:
                if short <= 0:
                    break
                drop.append(ip)
                short -= ip.card.slots.count(slot)
            if short > 0:
                return None
        return drop

    def _enter(self, c: SimCard):
        self.play.append(InPlay(c))
        self._slots = None
        self.first_asset.setdefault(c.name, self.round)
        self.gain(c.effect.get("enter", {}))

    def _controls(self, traits: list, besides: InPlay) -> bool:
        return any(ip is not besides and ip.card.traits & set(traits) for ip in self.play)

    def skill_value(self, skill: str, action: str | None) -> int:
        """Base skill plus constant bonuses from cards in play."""
        v = self.s.inv.skills[skill]
        key = action or "treachery"
        for ip in self.play:
            e = ip.card.effect
            if not e:
                continue
            if e.get("if_controls") and not self._controls(e["if_controls"], ip):
                continue
            v += e.get("skill_bonus", {}).get(skill, 0)
            v += e.get("action_bonus", {}).get(key, {}).get(skill, 0)
        return v

    # ----- choosing how to take an action ----------------------------------------

    def methods(self, action: str, actions_left: int = 3) -> list[dict]:
        """Ways to take a test action now: the basic action, abilities on cards
        in play, and test events in hand. Each is a dict with the skill used,
        its value before commits, and what it costs and gives."""
        skill = ACTION_SKILL[action]
        out = [{"value": self.skill_value(skill, action), "skill": skill, "ability": {}}]
        for ip in self.play:
            for a in ip.card.effect.get("abilities", []):
                if a["action"] != action or a.get("actions", 1) > actions_left:
                    continue
                if a.get("exhaust") and ip.exhausted:
                    continue
                if a.get("use") and ip.uses < a["use"]:
                    continue
                out.append(self._method(a, action, source=ip))
        for c in self.hand:
            t = c.effect.get("test")
            if t and t["action"] == action and c.cost <= self.resources + c.effect.get("play", {}).get("resources", 0):
                out.append(self._method(t, action, event=c))
        return out

    def _method(self, a: dict, action: str, source: InPlay | None = None, event: SimCard | None = None) -> dict:
        skills = a.get("skill", ACTION_SKILL[action])
        best = None
        for skill in skills if isinstance(skills, list) else [skills]:
            v = self.skill_value(skill, action)
            if "base" in a:
                v += a["base"] - self.s.inv.skills[skill]
            if best is None or v > best[0]:
                best = (v, skill)
        value, skill = best
        if a.get("add_skill"):
            value += self.s.inv.skills[a["add_skill"]]
        value += a.get("bonus", 0)
        return {"value": value, "skill": skill, "ability": a, "source": source, "event": event}

    def _score(self, m: dict, action: str, difficulty: int) -> float:
        a = m["ability"]
        p = self.s.pass_chance(m["value"] - difficulty)
        reward = 0.0 if a.get("instead") or a.get("damage_by_margin") else 1.0
        if a.get("damage_by_margin"):
            reward += 2
        for k, v in a.get("success", {}).items():
            if k == "clues" and action != "investigate" or k == "damage" and action != "fight":
                continue
            w = WEIGHTS.get(k, 0) * (2 if k == "resources" and self.resources < 2 else 1)
            reward += w * v
        if "better" in a:
            reward += 0.3 * sum(WEIGHTS.get(k, 0) * v for k, v in a["better"].items() if k != "by")
        cost = 0.05 * a.get("use", 0)
        if m.get("event"):
            cost += 0.3 + 0.1 * m["event"].cost
        return (p * reward - cost) / a.get("actions", 1)

    def best_method(self, action: str, difficulty: int, actions_left: int = 3) -> dict:
        return max(self.methods(action, actions_left), key=lambda m: self._score(m, action, difficulty))

    # ----- committing ---------------------------------------------------------------

    def _commit_candidates(self, skill: str, action: str | None) -> list[SimCard]:
        """Cards in hand the policy is willing to commit, best first: skills,
        then events with nothing better to do, then assets that can't be
        played anyway."""
        key = action or "treachery"
        skills, events, assets = [], [], []
        for c in self.hand:
            n = c.icons[skill]
            if n <= 0:
                continue
            only = c.effect.get("commit_only")
            if only and only != key:
                continue
            if c.type == "skill":
                skills.append(c)
            elif c.type == "event" and not ({"play", "test", "trigger"} & c.effect.keys()):
                events.append(c)
            elif c.type == "asset" and (self._placeable(c) is None
                                        or any(ip.card.title == c.title for ip in self.play)):
                assets.append(c)
        by_icons = lambda c: -c.icons[skill]  # noqa: E731
        return sorted(skills, key=by_icons) + sorted(events, key=by_icons) + sorted(assets, key=by_icons)

    def _pumps(self, skill: str, action: str | None) -> list[tuple[InPlay, int, int, int]]:
        """(card, value per activation, resource cost, max activations) for
        assets that turn resources (or their own uses) into skill."""
        key = action or "treachery"
        out = []
        for ip in self.play:
            p = ip.card.effect.get("pump")
            if not p or skill not in p.get("skills", []):
                continue
            if p.get("when") and key not in p["when"]:
                continue
            if p.get("min_hand") and len(self.hand) < p["min_hand"]:
                continue
            times = p.get("max", 99)
            if p.get("use"):
                times = min(times, ip.uses // p["use"])
            out.append((ip, p.get("value_when", {}).get(key, p.get("value", 1)), p.get("cost", 1), times))
        return out

    def plan(self, skill: str, action: str | None, value: int, difficulty: int,
             prepared: tuple | None = None) -> tuple[int, list, list, bool]:
        """What the policy commits and spends to reach its chance of passing:
        (new value, cards to commit, pump activations [(InPlay, from_pool)],
        short) where short means it wanted to commit but had nothing useful.
        Changes nothing. `prepared` is (candidates, pumps) when the caller
        plans several difficulties for the same skill and hand."""
        goal = self.s.goal
        if goal is None:
            return value, [], [], False
        chance = self.s.pass_chance

        def enough(v: int) -> bool:
            return chance(v - difficulty) >= goal - 1e-9

        if enough(value):
            return value, [], [], False
        cards, pumps = prepared or (self._commit_candidates(skill, action), self._pumps(skill, action))
        if not cards and not pumps:
            return value, [], [], True
        reserve = 0 if goal >= max(self.s.pass_table) - 1e-9 else 2
        # Hopeless even with everything? Keep the cards.
        spendable = max(0, self.resources - reserve)
        best = value + sum(c.icons[skill] for c in cards)
        for ip, per, cost, times in pumps:
            free = ip.free if cost else 0
            n = times if not cost else min(times, free + spendable // cost)
            best += per * max(0, n)
        if best < difficulty:
            return value, [], [], False
        chosen, used_titles, spent = [], set(), []
        skills_first = [c for c in cards if c.type == "skill"]
        others = [c for c in cards if c.type != "skill"]

        def take(group):
            nonlocal value
            for c in group:
                if enough(value):
                    return
                if c.max_one and c.title in used_titles:
                    continue
                chosen.append(c)
                used_titles.add(c.title)
                value += c.icons[skill]

        take(skills_first)
        pool = self.resources
        for ip, per, cost, times in pumps:
            free = ip.free if cost else 0
            n = 0
            while not enough(value) and n < times:
                if cost and free > 0:
                    free -= 1
                    spent.append((ip, False))
                elif not cost or pool - cost >= reserve:
                    pool -= cost
                    spent.append((ip, True))
                else:
                    break
                value += per
                n += 1
        take(others)
        return value, chosen, spent, False

    # ----- tests --------------------------------------------------------------------

    def test(self, skill: str, action: str | None, value: int, difficulty: int,
             method: dict | None = None) -> tuple[bool, int]:
        """Commit, reveal a token, resolve. Returns (success, margin)."""
        a = method["ability"] if method else {}
        new_value, cards, pumps, short = self.plan(skill, action, value, difficulty)
        if short:
            self.stats.short += 1
        for c in cards:
            self.hand.remove(c)
        for ip, from_pool in pumps:
            p = ip.card.effect["pump"]
            if p.get("use"):
                ip.uses -= p["use"]
            if from_pool:
                self.resources -= p.get("cost", 1)
            elif p.get("cost"):
                ip.free -= 1
        name, mod, auto_fail = self.rng.choice(self.s.bag)
        total = max(0, new_value + mod)
        success = not auto_fail and total >= difficulty
        if not success and not auto_fail and a.get("retry") and method.get("source") \
                and not method["source"].exhausted:
            method["source"].exhausted = True
            name, mod, auto_fail = self.rng.choice(self.s.bag)
            total = max(0, new_value - a.get("bonus", 0) + a["retry"].get("bonus", 0) + mod)
            success = not auto_fail and total >= difficulty
        if not success and not auto_fail and self.s.goal is not None:
            for c in self.hand:   # Lucky!
                if c.effect.get("trigger") == "fail" and c.cost <= self.resources \
                        and total + c.effect.get("rescue", 0) >= difficulty:
                    total += c.effect.get("rescue", 0)
                    success = True
                    self._play_event(c, free_action=True)
                    break
        margin = total - difficulty
        key = action or "treachery"

        if success:
            if action and not a.get("instead"):
                if a.get("damage_by_margin"):
                    lo, hi = a["damage_by_margin"]
                    self.deal(max(lo, min(hi, margin)))
                elif action == "investigate":
                    self.gain({"clues": 1})
                elif action == "fight":
                    self.deal(1)
                else:
                    self.progress += 1 if self.s.action == "evade" else 0
            better = a.get("better")
            self.gain(better if better and margin >= better["by"] else a.get("success", {}))
        for c in cards:
            commit = c.effect.get("commit", {})
            if commit.get("when", "any") in ("any", key):
                if success:
                    better = commit.get("better")
                    self.gain(better if better and margin >= better["by"] else commit.get("success", {}))
                else:
                    self.gain(commit.get("fail", {}))
            back = (success and commit.get("return_by") is not None and margin >= commit["return_by"]) \
                or (not success and commit.get("return_on_fail"))
            (self.hand if back else self.discard).append(c)
        for ip in self.play:
            for r in ip.card.effect.get("reactions", []):
                if r["after"] != ("success" if success else "fail") or r.get("when", "any") not in ("any", key):
                    continue
                if success and margin < r.get("by", 0):
                    continue
                if r.get("exhaust"):
                    if ip.exhausted:
                        continue
                    ip.exhausted = True
                self.gain(r)
        if method and method.get("source") is not None:
            src = method["source"]
            if a.get("use"):
                src.uses -= a["use"]
            if a.get("exhaust"):
                src.exhausted = True
            if a.get("discard") or name in a.get("discard_on", []):
                self.play.remove(src)
                self.discard.append(src.card)
                self._slots = None
        if name in a.get("end_turn_on", []):
            self.end_turn = True
        return success, margin

    def _play_event(self, c: SimCard, free_action: bool = False):
        self.hand.remove(c)
        self.resources -= c.cost
        self.discard.append(c)
        self.gain(c.effect.get("play", {}))

    # ----- the round ------------------------------------------------------------------

    def round_(self, r: int):
        self.round = r
        s, st = self.s, self.stats
        if r > 1 and s.treachery:   # mythos: one treachery test
            st.treachery_tests += 1
            if self.test(s.treachery, None, self.skill_value(s.treachery, None), s.treachery_difficulty)[0]:
                st.treachery_passes += 1
        for ip in self.play:   # "when your turn begins"
            self.gain(ip.card.effect.get("turn_start", {}))
        self.probe(r)
        actions = 3 + sum(ip.card.effect.get("extra_actions", 0) for ip in self.play)
        self.bonus_actions = 0
        self.end_turn = False
        for _ in range(30):   # a turn is never this long; this only guards against loops
            if actions <= 0:   # Quick Thinking and the like
                actions, self.bonus_actions = self.bonus_actions, 0
            if actions <= 0 or self.end_turn:
                break
            if self.pending:
                lost = min(self.pending, actions)
                self.pending -= lost
                actions -= lost
                st.actions["weakness"] += lost
                continue
            self.free_plays()
            actions -= self.take_action(actions)
        self.free_plays()
        # upkeep
        for ip in self.play:
            ip.exhausted = False
            pump = ip.card.effect.get("pump", {})
            ip.free = pump.get("free", 0)
        self.draw(1)
        self.resources += 1
        limit = HAND_LIMIT + sum(ip.card.effect.get("hand_size", 0) for ip in self.play)
        while len(self.hand) > limit:
            worst = min(self.hand, key=lambda c: (c.type == "asset", c.icons[ACTION_SKILL[s.action]]))
            self.hand.remove(worst)
            self.discard.append(worst)
        self.stats.progress[r - 1].append(self.progress)

    def _wanted_assets(self) -> list[SimCard]:
        out = [c for c in self.hand if c.type == "asset" and self._placeable(c) is not None]
        return sorted(out, key=lambda c: (-c.relevant(self.s.action), c.cost))

    def _useful_event(self, c: SimCard) -> bool:
        play = c.effect.get("play")
        if c.type != "event" or not play or "test" in c.effect or "trigger" in c.effect or c.cost > self.resources:
            return False
        if play.get("clues") and self.s.action == "investigate" or play.get("damage") and self.s.action == "fight":
            return True
        if play.get("draw") and len(self.hand) <= 5:
            return True
        if play.get("resources"):
            need = max((a.cost for a in self._wanted_assets()), default=0)
            return self.resources <= 3 or need > self.resources
        return False

    def _play_asset(self, c: SimCard):
        for ip in self._placeable(c) or []:
            self.play.remove(ip)
            self.discard.append(ip.card)
        self._slots = None
        self.hand.remove(c)
        self.resources -= c.cost
        self._enter(c)

    def free_plays(self):
        """Fast cards cost no action: play them whenever the policy would."""
        for _ in range(10):
            c = next((c for c in self._wanted_assets() if c.fast and c.cost <= self.resources), None)
            if c:
                self._play_asset(c)
                continue
            c = next((c for c in self.hand if c.fast and self._useful_event(c)), None)
            if c:
                self._play_event(c)
                continue
            return

    def take_action(self, actions_left: int) -> int:
        """Take one action (or a multi-action ability). Returns actions used."""
        s, st = self.s, self.stats
        assets = self._wanted_assets()
        c = next((c for c in assets if c.cost <= self.resources), None)
        if c:
            self._play_asset(c)
            st.actions["play"] += 1
            return 1
        c = next((c for c in self.hand if self._useful_event(c)), None)
        if c:
            self._play_event(c)
            st.actions["play"] += 1
            return 1
        if len(self.hand) <= 1 and (self.deck or self.discard):
            self.draw(1)
            st.actions["draw"] += 1
            return 1
        if assets and min(a.cost for a in assets) - self.resources <= 2:
            for ip in self.play:   # Dario El-Amin and the like
                for a in ip.card.effect.get("abilities", []):
                    if a["action"] == "resources" and not (a.get("exhaust") and ip.exhausted):
                        ip.exhausted = ip.exhausted or bool(a.get("exhaust"))
                        self.gain(a.get("success", {}))
                        st.actions["resource"] += 1
                        return 1
            self.resources += 1
            st.actions["resource"] += 1
            return 1
        m = self.best_method(s.action, s.difficulty, actions_left)
        cost = m["ability"].get("actions", 1)
        if m.get("event"):
            ev = m["event"]
            self.hand.remove(ev)
            self.resources -= ev.cost
            self.gain(ev.effect.get("play", {}))
        st.tests += 1
        st.actions["test"] += cost
        success, margin = self.test(m["skill"], s.action, m["value"], s.difficulty, m)
        st.passes += success
        if m.get("event"):
            rb = m["ability"].get("return_by")
            (self.hand if success and rb is not None and margin >= rb else self.discard).append(m["event"])
        return cost

    # ----- probing ------------------------------------------------------------------

    def probe(self, r: int):
        s, st = self.s, self.stats
        st.hand[r - 1].append(len(self.hand))
        st.resources[r - 1].append(self.resources)
        if len(self.hand) <= 1:
            st.low_hand += 1
        if any(c.type == "asset" and c.cost > self.resources and self._placeable(c) is not None for c in self.hand):
            st.stuck += 1
        rescue = max((c.effect.get("rescue", 0) for c in self.hand
                      if c.effect.get("trigger") == "fail" and c.cost <= self.resources), default=0)
        for key in s.challenges:
            _, action, skill = CHALLENGES[key]
            if action:
                m = max(self.methods(action), key=lambda m: m["value"])
                value, skill = m["value"], m["skill"]
            else:
                value = self.skill_value(skill, None)
            row = st.probe[key]
            prepared = (self._commit_candidates(skill, action), self._pumps(skill, action)) \
                if s.goal is not None else None
            for d in DIFFICULTIES:
                v, _, _, _ = self.plan(skill, action, value, d, prepared)
                p = s.pass_chance(v + (rescue if s.goal is not None else 0) - d)
                row[d] += p
                if d == s.difficulty:
                    st.probe_round[key][r - 1] += p
            if s.difficulty not in DIFFICULTIES:
                v, _, _, _ = self.plan(skill, action, value, s.difficulty, prepared)
                st.probe_round[key][r - 1] += s.pass_chance(v - s.difficulty)

    def finish(self):
        st = self.stats
        st.defeats.append(self.defeats)
        st.reshuffles += self.reshuffled
        for name, r in self.first_asset.items():
            st.assets.setdefault(name, []).append(r)
        for name, r in self.first_weakness.items():
            st.weaknesses.setdefault(name, []).append(r)


# ----- settings, entry point, report -------------------------------------------------


def settings() -> list[SimSetting]:
    bags = load_bags()
    tests = [(k, v[0]) for k, v in CHALLENGES.items()]
    return [
        SimSetting("action", "Spend spare actions on", "select", "investigate",
                   [("investigate", "Investigating"), ("fight", "Fighting"), ("evade", "Evading")],
                   help="After playing cards, drawing and gaining resources as needed"),
        SimSetting("difficulty", "Shroud / enemy fight / evade", "number", 3, min=1, max=8),
        SimSetting("treachery", "Treachery test each round", "select", "willpower",
                   [("none", "None"), ("willpower", "Willpower"), ("agility", "Agility"),
                    ("combat", "Combat"), ("intellect", "Intellect")]),
        SimSetting("treachery_difficulty", "Treachery difficulty", "number", 3, min=1, max=8),
        SimSetting("bag", "Chaos bag", "select", bags[1]["key"] if len(bags) > 1 else bags[0]["key"],
                   [(b["key"], b["label"]) for b in bags] + [("custom", "Type my own")]),
        SimSetting("custom_bag", "Tokens", "text", "",
                   help="e.g. +1 0 0 -1 -1 -2 -3 skull cultist auto_fail elder_sign", show_if=("bag", "custom")),
        SimSetting("symbols", "Skull, cultist, tablet and elder thing count as", "number", -2, min=-8, max=2),
        SimSetting("commit", "Commit cards", "select", "p75",
                   list(COMMIT_LABELS.items())),
        SimSetting("mulligan", "Mulligan the opening hand", "toggle", True),
        SimSetting("enemy_health", "Enemy health", "number", 3, min=1, max=10,
                   help="Damage to defeat an enemy, when fighting"),
        SimSetting("weakness_actions", "Actions lost per weakness drawn", "number", 1, min=0, max=3),
        SimSetting("challenges", "Show the chance to pass", "multiselect",
                   ["investigate", "fight", "evade", "willpower", "agility"], tests),
        SimSetting("rounds", "Rounds per game", "number", 10, min=1, max=25),
        SimSetting("runs", "Games to play", "number", 1000, min=100, max=5000, step=100),
        SimSetting("seed", "Random seed", "text", "", help="Same seed, same results; leave empty for new ones"),
    ]


def build_setup(deck: Deck, pool: Pool, values: dict, effects: dict) -> tuple[Setup, list[str]]:
    problems = []
    identity = pool.get(deck.identity)
    inv = investigator(identity)
    cards = []
    for card_id, qty in deck.slots.items():
        card = pool.get(card_id)
        if card is None or card.get("kind") == "investigator":
            continue
        sc = sim_card(card, effects)
        cards += [sc] * qty
    if values["bag"] == "custom":
        tokens, problems = parse_tokens(values["custom_bag"])
        label = "your bag"
    else:
        bag = next(b for b in load_bags() if b["key"] == values["bag"])
        tokens, problems = parse_tokens(bag["tokens"])
        label = bag["label"]
    if not tokens:
        problems.append("The chaos bag is empty; using the standard Night of the Zealot bag")
        tokens, _ = parse_tokens(load_bags()[1]["tokens"])
        label = load_bags()[1]["label"]
    bag = []
    for t in tokens:
        if isinstance(t, int):
            bag.append((str(t), t, False))
        elif t == "auto_fail":
            bag.append((t, 0, True))
        elif t == "elder_sign":
            bag.append((t, inv.elder, False))
        else:
            bag.append((t, int(values["symbols"]), False))
    setup = Setup(
        inv=inv, cards=cards, action=values["action"], difficulty=int(values["difficulty"]),
        treachery=None if values["treachery"] == "none" else values["treachery"],
        treachery_difficulty=int(values["treachery_difficulty"]), goal=COMMIT_GOAL[values["commit"]],
        mulligan=values["mulligan"], enemy_health=int(values["enemy_health"]),
        weakness_actions=int(values["weakness_actions"]), rounds=int(values["rounds"]),
        challenges=values["challenges"] or ["investigate"], bag=bag,
    )
    setup.bag_label = label
    return setup, problems


def run(deck: Deck, pool: Pool, values: dict) -> SimReport:
    started = time.perf_counter()
    effects, warnings = load_effects()
    setup, problems = build_setup(deck, pool, values, effects)
    runs = int(values["runs"])
    seed = values.get("seed", "").strip()
    rng = random.Random(int(seed) if seed.lstrip("-").isdigit() else (seed or None))
    stats = Stats(setup, runs)
    for _ in range(runs):
        game = Run(setup, rng, stats)
        game.opening()
        for r in range(1, setup.rounds + 1):
            game.round_(r)
        game.finish()
    report = build_report(setup, stats, values)
    report.warnings = problems + warnings
    report.seconds = time.perf_counter() - started
    return report


def _checkpoints(rounds: int) -> list[int]:
    return sorted({r for r in (1, 3, 5, rounds) if r <= rounds})


def build_report(setup: Setup, st: Stats, values: dict) -> SimReport:
    runs, R = st.runs, setup.rounds
    turns = runs * R
    rounds = [str(r) for r in range(1, R + 1)]
    checks = _checkpoints(R)
    unit_one, unit_many = UNIT[setup.action]
    action_label = {"investigate": "investigating", "fight": "fighting", "evade": "evading"}[setup.action]
    sections = []

    # Chance to pass
    grid = SimTable(
        ["Test"] + [str(d) for d in DIFFICULTIES],
        [[Cell(CHALLENGES[k][0])] + [pct(st.probe[k][d] / turns) for d in DIFFICULTIES] for k in setup.challenges],
        caption="Columns are the difficulty: shroud, enemy fight or evade value, or the treachery's test.")
    by_round = SimTable(
        ["Test"] + [f"Round {r}" for r in checks],
        [[Cell(CHALLENGES[k][0])] + [pct(st.probe_round[k][r - 1] / runs) for r in checks] for k in setup.challenges],
        caption=f"At difficulty {setup.difficulty}, as the game goes on and assets come into play.")
    sections.append(SimSection(
        "Chance to pass", [grid, by_round],
        intro="The chance of passing each kind of test at the start of a turn, using the cards in play, "
              "what the commit policy would commit from the hand, and the chaos bag. Averaged over every "
              "turn of every game."))

    # Main action
    finals = sorted(st.progress[-1]) if st.progress[-1] else [0]
    items = [
        (f"{unit_many.capitalize()} per game", f"{sum(finals) / len(finals):.1f}",
         f"Average over {runs:,} games of {R} rounds; the middle 80% got "
         f"{finals[len(finals) // 10]}–{finals[min(len(finals) - 1, len(finals) * 9 // 10)]}"),
        ("Tests passed", f"{st.passes / st.tests * 100:.0f}%" if st.tests else "–",
         f"{st.tests / runs:.1f} {action_label} tests per game"),
    ]
    if setup.action == "fight":
        items.append(("Enemies defeated", f"{sum(st.defeats) / runs:.1f}",
                      f"Per game, at {setup.enemy_health} health each"))
    if setup.treachery:
        items.append((f"{setup.treachery.capitalize()} treacheries passed",
                      f"{st.treachery_passes / st.treachery_tests * 100:.0f}%" if st.treachery_tests else "–",
                      f"One test at difficulty {setup.treachery_difficulty} each round from round 2"))
    sections.append(SimSection(
        f"Spare actions: {action_label}", [
            SimStats(items),
            SimChart(f"{unit_many.capitalize()} so far", rounds, [band(unit_many.capitalize(), st.progress)],
                     y_label=unit_many, caption="Middle line: the typical game. Band: the middle 80% of games."),
        ],
        intro=f"Each spare action goes to {action_label} at difficulty {setup.difficulty}, using the best "
              f"card for it."))

    # Hand
    sections.append(SimSection("Hand", [
        SimStats([
            ("Turns starting with 0–1 cards", f"{st.low_hand / turns * 100:.0f}%", None),
            ("Tests with nothing to commit", f"{st.short / max(1, st.tests + st.treachery_tests) * 100:.0f}%",
             "Tests the policy wanted to commit to, where the hand had no card worth committing"),
            ("Games that drew the whole deck", f"{st.reshuffles / runs * 100:.0f}%",
             "Reshuffling the discard pile costs 1 horror in the real game"),
        ]),
        SimChart("Cards in hand at the start of each turn", rounds, [band("Hand", st.hand)], y_label="cards",
                 caption="Middle line: the typical game. Band: the middle 80% of games."),
    ]))

    # Resources and actions
    names = [("play", "Playing cards"), ("draw", "Drawing"), ("resource", "Gaining resources"),
             ("test", f"Tests ({action_label})"), ("weakness", "Lost to weaknesses")]
    sections.append(SimSection("Resources and actions", [
        SimStats([("Turns with an asset you can't afford", f"{st.stuck / turns * 100:.0f}%", None)]),
        SimChart("Resources at the start of each turn", rounds, [band("Resources", st.resources)],
                 y_label="resources"),
        SimTable(["Action", "Per turn", "Share"],
                 [[Cell(label), Cell(f"{st.actions[k] / turns:.2f}"),
                   pct(st.actions[k] / max(1, sum(st.actions.values())))] for k, label in names],
                 caption="Where the investigation phase's actions go."),
    ]))

    # Assets
    if st.assets:
        def by_round(samples: list, r: int) -> float:
            return sum(1 for x in samples if x <= r) / runs
        rows = sorted(st.assets.items(), key=lambda kv: -by_round(kv[1], checks[min(1, len(checks) - 1)]))
        sections.append(SimSection("Assets in play", [SimTable(
            ["Asset"] + [f"By round {r}" for r in checks],
            [[Cell(name)] + [pct(by_round(samples, r)) for r in checks] for name, samples in rows[:20]],
            caption="Share of games where at least one copy had been played by then (round 1 includes cards put into play from the opening hand).")]))

    # Weaknesses
    weak_names = sorted({c.name for c in setup.cards if c.type == "weakness"})
    if weak_names:
        rows = []
        for name in weak_names:
            samples = st.weaknesses.get(name, [])
            typical = sorted(samples)[len(samples) // 2] if samples else None
            rows.append([Cell(name)] + [pct(sum(1 for x in samples if x <= r) / runs) for r in checks]
                        + [Cell(str(typical) if typical is not None else "–")])
        sections.append(SimSection("Weaknesses", [SimTable(
            ["Weakness"] + [f"Drawn by round {r}" for r in checks] + ["Typical round"], rows,
            caption=f"Each one costs {setup.weakness_actions} action{'s' if setup.weakness_actions != 1 else ''} "
                    f"when drawn (change this above); their own effects aren't simulated.")]))

    # Coverage
    distinct = {c.id: c for c in setup.cards if c.type not in ("weakness",)}
    partial = sorted(c.name for c in distinct.values() if not c.modelled)
    notes = sorted({f"{c.name}: {c.effect['note']}" for c in distinct.values() if c.effect.get("note")})
    lines = [f"{len(distinct) - len(partial)} of the deck's {len(distinct)} different cards are fully modelled."]
    if partial:
        lines.append("Counted only for their icons, cost and slots: " + ", ".join(partial) + ".")
    lines += notes
    lines += [
        f"{setup.inv.name}: skills {', '.join(f'{setup.inv.skills[s]} {s}' for s in SKILLS)}; elder sign "
        f"+{setup.inv.elder}. Investigator abilities aren't simulated.",
        "Not simulated: enemies attacking, damage and horror, movement, other encounter cards, other players.",
        "The simulated player follows fixed rules, so these figures are best for comparing versions of a deck "
        "rather than predicting a real game.",
    ]
    sections.append(SimSection("How this deck was modelled", [SimNotes(lines)]))

    summary = (f"{runs:,} games of {R} rounds · {setup.inv.name} · {setup.bag_label} "
               f"(symbols {int(values['symbols']):+d}) · commit: {COMMIT_LABELS[values['commit']].lower()}")
    return SimReport(sections, summary=summary)
