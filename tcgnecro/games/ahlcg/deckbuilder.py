"""Arkham Horror LCG deck builder rules.

Deckbuilding in Arkham is driven by data on each investigator card, which
ArkhamDB publishes as `deck_options` and `deck_requirements`. This module is
an interpreter for that data, following ArkhamDB's semantics, so new
investigators work without code changes as long as they only use option
kinds handled here.

How a deck is checked (validate):

1. Exempt cards are set aside: weaknesses, story cards a campaign adds
   (kind "story"), the investigator's required
   (signature) cards, and cards restricted to this investigator. They are
   always allowed and don't count toward deck size.
2. Every other copy is offered to the investigator's deck options in order.
   The first option whose criteria the card meets claims it, unless that
   option has a `limit` that is already used up, in which case the next
   matching option is tried. An option with `not: true` that matches makes
   the card illegal outright. A copy that nothing claims is illegal.
3. Deck size (permanents don't count), copy limits by title, required cards,
   "at least N cards from M classes" rules and random basic weaknesses are
   checked separately.

Some player cards change deckbuilding too. Those that ArkhamDB encodes as
`deck_options` on the card (On Your Own) are applied generically, ahead of
the investigator's options. The rest only say so in their text, so they are
listed in DECKBUILDING_CARDS below.

Taboo lists (deck.meta["taboo_id"], ArkhamDB's list id) are applied by
giving the deck its own view of the pool (deck_pool): the listed cards are
copied with the list's changes merged into `raw`, so every rule above reads
the changed card. See "Taboo lists" below.

Option criteria supported: faction, faction_select, option_select,
deck_size_select, level, type, trait, slot, uses, text, tag, permanent, not,
limit, atleast, ignore_match. Not yet: customizable upgrades (and taboo
changes to them), parallel investigator front/back choices, base_level.
See docs/ROADMAP.md.
"""

import json
import random
import re
import urllib.error
from dataclasses import dataclass, replace
from pathlib import Path

from tcgnecro.deckbuilder.model import Action, Deck, DeckCard, Pool, Problem, RemoteDeck, Selection, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin
from tcgnecro.games import ahlcg
from tcgnecro.games.ahlcg import fetch_json

API = "https://arkhamdb.com/api/public"
PLAYER_TYPES = {"investigator", "asset", "event", "skill"}
WEAKNESS_SUBTYPES = {"weakness", "basicweakness"}
DEFAULT_DECK_SIZE = 30
DEFAULT_COPIES = 2

# deck.meta keys for each kind of choice, matching ArkhamDB's deck meta so
# imported decks keep their choices. An option's own "id" overrides these
# (Charlie Kane's two class choices are "faction_1" and "faction_2").
SELECT_KEYS = {
    "faction_select": "faction_selected",
    "option_select": "option_selected",
    "deck_size_select": "deck_size_selected",
}

# ArkhamDB's "Random Basic Weakness" placeholder. Not a real card: decks list
# it to mean "draw one later", so import drops it and validation asks for a
# weakness to be drawn instead.
RANDOM_WEAKNESS_PLACEHOLDER = "01000"

CLASSES = ["guardian", "seeker", "rogue", "mystic", "survivor"]

# Player cards whose effect on deckbuilding is only in their text, keyed by
# English name (so reprints are covered). "size" adjusts the required deck
# size; other effects are checked in validate()/_options().
DECKBUILDING_CARDS = {
    "Versatile": {"size": 5},              # + one other level 0 card from any class
    "Ancestral Knowledge": {"size": 5},    # + at least 10 skills
    "Underworld Support": {"size": -5},    # + max 1 copy of each card by title
    "Forced Learning": {"size": 15},
    "Underworld Market": {"size": 10},     # + at least 10 Illicit cards
}
VERSATILE_OPTION = {
    "faction": CLASSES + ["neutral"], "level": {"min": 0, "max": 0}, "limit": 1,
    "error": "Versatile allows only one level 0 card from another class",
}

FACTION_NAMES = {
    "guardian": "Guardian", "seeker": "Seeker", "rogue": "Rogue",
    "mystic": "Mystic", "survivor": "Survivor", "neutral": "Neutral", "mythos": "Mythos",
}


def _lower_set(values) -> set[str]:
    return {str(v).lower() for v in values or []}


def _traits(raw: dict) -> set[str]:
    return {t.strip().lower() for t in (raw.get("traits") or "").split(".") if t.strip()}


def _factions(raw: dict) -> list[str]:
    return [raw[k] for k in ("faction_code", "faction2_code", "faction3_code") if raw.get(k)]


def _select_key(option: dict) -> str | None:
    for kind, key in SELECT_KEYS.items():
        if kind in option:
            return option.get("id") or key
    return None


# ----- Taboo lists ---------------------------------------------------------------
#
# Each entry of a list (games/ahlcg/__init__.py loads them) names a card code
# and any of:
#   xp            extra experience per copy: positive is "chained", negative
#                 "unchained". The card's level is unchanged, so deck options
#                 that check level still see the printed level; only the XP
#                 cost changes.
#   deck_limit    new copy limit; 0 means forbidden.
#   exceptional   gains (true) or loses (false) the Exceptional keyword.
#   deck_options, deck_requirements   replace an investigator's.
#   text          the list's own description of the change (shown).
#   replacement_text  the card's new text, used as its text.
#   customization_*   changes to customizable cards; not applied (customizable
#                 upgrades aren't supported yet), only described by `text`.

TABOO_KEY = "taboo_id"
# Fields of a taboo entry copied over the card's own.
TABOO_REPLACES = ("deck_limit", "exceptional", "deck_options", "deck_requirements")


@dataclass
class TabooPool(Pool):
    """A pool with one taboo list applied (AhlcgPlugin.deck_pool)."""

    taboo_id: int = 0
    base: Pool | None = None


def taboo_id(deck: Deck) -> int | None:
    """The deck's taboo list id, or None. ArkhamDB imports store an int; the
    picker stores the form's string ("" for none)."""
    value = deck.meta.get(TABOO_KEY)
    try:
        return int(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def taboo_label(taboo: dict) -> str:
    date = taboo.get("date_start") or ""
    return f"{taboo['name']} {taboo['code']}" + (f" ({date})" if date else "")


def _taboo_summary(entry: dict) -> tuple[str, str]:
    """(badge, description) for one taboo entry."""
    parts, badge = [], "Taboo"
    xp = entry.get("xp")
    if entry.get("deck_limit") == 0:
        badge = "Forbidden"
    elif xp:
        badge = f"{'+' if xp > 0 else '−'}{abs(xp)} XP"
        parts.append(f"{'Chained' if xp > 0 else 'Unchained'}: {xp:+d} experience per copy.")
    if entry.get("text"):
        parts.append(entry["text"])
    elif entry.get("deck_limit") == 0:
        parts.append("Forbidden.")
    return badge, " ".join(parts)


def apply_taboo(card: DeckCard, entry: dict) -> DeckCard:
    """A copy of `card` with one taboo entry applied."""
    raw = dict(card.raw)
    for key in TABOO_REPLACES:
        if key in entry:
            raw[key] = entry[key]
    # Exceptional cards are limited to 1 copy by the rules; ArkhamDB's data
    # bakes that into deck_limit, so a list that only adds the keyword
    # (Seeker's Insight, 03315) needs it applied here. (Our reading of the
    # rules; the entry doesn't say so itself.)
    if entry.get("exceptional") and "deck_limit" not in entry:
        raw["deck_limit"] = min(int(raw.get("deck_limit") or DEFAULT_COPIES), 1)
    if entry.get("replacement_text"):
        raw["text"] = entry["replacement_text"]
    badge, summary = _taboo_summary(entry)
    raw["taboo_xp"] = int(entry.get("xp") or 0)
    raw["taboo_text"] = summary
    attrs = card.attrs | {"taboo": badge, "taboo_xp": raw["taboo_xp"], "text": raw.get("text") or "",
                          "deck_limit": raw.get("deck_limit"),
                          "forbidden": raw.get("deck_limit") == 0}
    return replace(card, attrs=attrs, raw=raw)


class AhlcgPlugin(DeckbuilderPlugin):

    # ----- card pool --------------------------------------------------------

    def build_card(self, card_id: str, title: str, raw: dict) -> DeckCard | None:
        if raw.get("bonded_to") or raw.get("duplicate_of_code"):
            return None  # bonded cards, reprints (see build_pool)
        subtype = raw.get("subtype_code")
        # Cards from an encounter set are scenario cards, except the ones a
        # campaign puts in your deck: story weaknesses (The Man in the Pallid
        # Mask) and story assets/events/skills (Lita Chantler). Those are kept
        # as kind "weakness" / "story"; like weaknesses, story cards sit
        # outside deckbuilding rules and deck size.
        story = bool(raw.get("encounter_code"))
        if subtype in WEAKNESS_SUBTYPES:
            kind = "weakness"
        elif raw["type_code"] in PLAYER_TYPES and not (story and raw["type_code"] == "investigator"):
            kind = "story" if story else raw["type_code"]
        else:
            return None
        xp = raw.get("xp")
        name = raw["name"] + (f" ({xp})" if xp else "")
        if raw.get("subname") and kind == "investigator":
            name += f" — {raw['subname']}"
        skills = [s for s in ("willpower", "intellect", "combat", "agility", "wild")
                  for _ in range(raw.get(f"skill_{s}") or 0)] if kind != "investigator" else []
        slots = [s.strip() for s in (raw.get("real_slot") or "").split(".") if s.strip()]

        attrs = {
            "name": name,
            "kind": kind,
            "subtype": subtype,
            "story": story,
            "faction": raw["faction_code"],
            "factions": _factions(raw),
            "xp": xp,
            "pips": "●" * xp if xp else None,
            "cost": raw.get("cost"),
            "traits": raw.get("traits") or "",
            "slots": slots,
            "skill_icons": sorted(set(skills)),
            "skills": skills,
            "text": raw.get("text") or "",
            "pack": raw.get("pack_code"),
            "deck_limit": raw.get("deck_limit"),
            # For queries (deckbuilder/buildql.py, [query] in deckbuilder.toml).
            "multiclass": len(_factions(raw)) > 1,
            "heals_damage": "hd" in (raw.get("tags") or "").split("."),
            "heals_horror": "hh" in (raw.get("tags") or "").split("."),
        }
        return DeckCard(card_id, title, name, attrs, raw)

    def build_pool(self, rows):
        pool = super().build_pool(rows)
        # Reprints (e.g. Revised Core's 01520 Machete) stand in for the
        # original card, so decks that list either code land on one card.
        for card_id, _, raw in rows:
            original = raw.get("duplicate_of_code")
            if original and original in pool.cards:
                pool.aliases[card_id] = original
        return pool

    def identities(self, pool: Pool) -> list[DeckCard]:
        return sorted(
            (c for c in pool.cards.values() if c.get("kind") == "investigator" and c.raw.get("deck_options")),
            key=lambda c: (c.raw.get("faction_code"), c.name),
        )

    # ----- taboo lists ------------------------------------------------------

    def taboos(self) -> dict[int, dict]:
        return ahlcg.load_taboos()

    def deck_pool(self, deck: Deck, pool: Pool) -> Pool:
        """The pool with the deck's taboo list applied (cached per list)."""
        wanted = taboo_id(deck)
        if isinstance(pool, TabooPool):
            if pool.taboo_id == wanted:
                return pool
            pool = pool.base
        taboo = self.taboos().get(wanted) if wanted is not None else None
        if taboo is None:
            return pool
        # taboo id -> (the list it was built from, TabooPool); rebuilt when the
        # game's pool is rebuilt or the taboo lists are downloaded again.
        cache = self.__dict__.setdefault("_taboo_pools", {})
        built_from, cached = cache.get(wanted, (None, None))
        if cached is not None and cached.base is pool and built_from is taboo:
            return cached
        cards = dict(pool.cards)
        for code, entry in taboo["cards"].items():
            card_id = pool.resolve(code)
            if card_id in pool.cards:
                cards[card_id] = apply_taboo(pool.cards[card_id], entry)
        view = TabooPool(cards, pool.aliases, pool.images, taboo_id=wanted, base=pool)
        cache[wanted] = (taboo, view)
        return view

    def _taboo_entry(self, deck: Deck, card: DeckCard) -> dict | None:
        wanted = taboo_id(deck)
        taboo = self.taboos().get(wanted) if wanted is not None else None
        return taboo["cards"].get(card.id) if taboo else None

    # ----- helpers ----------------------------------------------------------

    def _identity(self, deck: Deck, pool: Pool) -> DeckCard | None:
        return pool.get(deck.identity) if deck.identity else None

    def _required(self, identity: DeckCard | None, pool: Pool) -> list[set[str]]:
        """Each signature requirement as the set of pool ids that satisfy it."""
        if identity is None:
            return []
        reqs = (identity.raw.get("deck_requirements") or {}).get("card") or {}
        return [{pool.resolve(code) for code in alts.values()} | {pool.resolve(key)}
                for key, alts in reqs.items()]

    def _restriction_ok(self, card: DeckCard, identity: DeckCard | None) -> bool | None:
        """None: card has no restrictions. Otherwise whether identity meets them."""
        restrictions = card.raw.get("restrictions")
        if not restrictions:
            return None
        if identity is None:
            return False
        ids = {identity.id, identity.raw.get("alternate_of_code")}
        if "investigator" in restrictions and not ids & set(restrictions["investigator"]):
            return False
        if "trait" in restrictions and not _traits(identity.raw) & _lower_set(restrictions["trait"]):
            return False
        if "faction" in restrictions and identity.raw.get("faction_code") not in restrictions["faction"]:
            return False
        return True

    def _exempt(self, card: DeckCard, identity: DeckCard | None, pool: Pool) -> bool:
        """Cards outside deckbuilding options and deck size."""
        if card.get("kind") in ("weakness", "story"):
            return True
        if any(card.id in req for req in self._required(identity, pool)):
            return True
        restrictions = card.raw.get("restrictions") or {}
        return "investigator" in restrictions and self._restriction_ok(card, identity) is True

    def _chosen(self, option: dict, deck: Deck, kind: str):
        """The player's choice for a *_select option, defaulting to the first."""
        value = deck.meta.get(_select_key(option))
        choices = option[kind]
        if kind == "option_select":
            return next((o for o in choices if o.get("id") == value), choices[0])
        return value if value in choices else choices[0]

    def option_matches(self, option: dict, card: DeckCard, deck: Deck) -> bool:
        """Does `card` meet every criterion in one deck option?"""
        raw = card.raw
        if "option_select" in option:
            chosen = self._chosen(option, deck, "option_select")
            merged = {k: v for k, v in option.items() if k != "option_select"} | chosen
            return self.option_matches(merged, card, deck)
        if "deck_size_select" in option and "faction" not in option:
            return False  # a choice, not a rule about cards
        factions = set(card.get("factions"))
        if "faction" in option and not factions & set(option["faction"]):
            return False
        if "faction_select" in option and self._chosen(option, deck, "faction_select") not in factions:
            return False
        if "level" in option:
            xp = raw.get("xp") or 0
            if not option["level"]["min"] <= xp <= option["level"]["max"]:
                return False
        if "type" in option and raw.get("type_code") not in option["type"]:
            return False
        if "trait" in option and not _traits(raw) & _lower_set(option["trait"]):
            return False
        if "slot" in option:
            slot = (raw.get("real_slot") or "").lower()
            if not any(s.lower() in slot for s in option["slot"]):
                return False
        if "uses" in option:
            pattern = r"Uses \(\d+ (" + "|".join(map(re.escape, option["uses"])) + r")\)"
            if not re.search(pattern, raw.get("text") or ""):
                return False
        if "text" in option and not any(re.search(p, raw.get("text") or "") for p in option["text"]):
            return False
        if "tag" in option:
            tags = set((raw.get("tags") or "").split("."))
            if not tags & set(option["tag"]):
                return False
        if "permanent" in option and bool(raw.get("permanent")) != option["permanent"]:
            return False
        return True

    def _special(self, deck: Deck, pool: Pool) -> set[str]:
        """Names of DECKBUILDING_CARDS present in the deck."""
        names = {c.raw.get("real_name") or c.raw["name"]
                 for cid in deck.slots if (c := pool.get(cid))}
        return names & DECKBUILDING_CARDS.keys()

    def _options(self, identity: DeckCard | None, deck: Deck | None = None,
                 pool: Pool | None = None) -> list[dict]:
        """The deck options in force: those carried by cards in the deck
        (e.g. On Your Own's restriction), then the investigator's, then
        Versatile's extra slot."""
        options = []
        if deck is not None and pool is not None:
            for card_id in deck.slots:
                card = pool.get(card_id)
                if card and card.get("kind") != "investigator" and card.raw.get("deck_options"):
                    options += card.raw["deck_options"]
        options += (identity.raw.get("deck_options") or []) if identity else []
        if deck is not None and pool is not None and "Versatile" in self._special(deck, pool):
            options.append(VERSATILE_OPTION)
        return options

    def _deck_size(self, deck: Deck, identity: DeckCard | None, pool: Pool | None = None) -> int:
        size = None
        for option in self._options(identity):
            if "deck_size_select" in option:
                size = int(self._chosen(option, deck, "deck_size_select"))
        if size is None:
            reqs = (identity.raw.get("deck_requirements") or {}) if identity else {}
            size = int(reqs.get("size") or DEFAULT_DECK_SIZE)
        if pool is not None:
            size += sum(DECKBUILDING_CARDS[n].get("size", 0) for n in self._special(deck, pool))
        return size

    # ----- building ---------------------------------------------------------

    def start_deck(self, deck: Deck, pool: Pool) -> None:
        pool = self.deck_pool(deck, pool)
        identity = self._identity(deck, pool)
        if identity is None:
            return
        for option in self._options(identity):
            key = _select_key(option)
            if key and key not in deck.meta:
                kind = next(k for k in SELECT_KEYS if k in option)
                first = option[kind][0]
                deck.meta[key] = first["id"] if kind == "option_select" else first
        reqs = (identity.raw.get("deck_requirements") or {}).get("card") or {}
        for code in reqs:
            card = pool.get(code)
            if card and not deck.count(card.id):
                deck.set_count(card.id, int(card.raw.get("deck_limit") or card.raw.get("quantity") or 1))

    def selections(self, deck: Deck, pool: Pool) -> list[Selection]:
        pool = self.deck_pool(deck, pool)
        out = []
        for option in self._options(self._identity(deck, pool)):
            key = _select_key(option)
            if not key:
                continue
            if "faction_select" in option:
                choices = [(f, FACTION_NAMES.get(f, f.title())) for f in option["faction_select"]]
            elif "option_select" in option:
                choices = [(o["id"], o["name"]) for o in option["option_select"]]
            else:
                choices = [(str(s), f"{s} cards") for s in option["deck_size_select"]]
            value = deck.meta.get(key, choices[0][0])
            out.append(Selection(key, option.get("name") or "Choice", choices, str(value)))
        taboos = self.taboos()
        if taboos and deck.identity:
            choices = [("", "None")] + [(str(t["id"]), taboo_label(t))
                                        for t in sorted(taboos.values(), key=lambda t: t["id"], reverse=True)]
            current = taboo_id(deck)
            out.append(Selection(TABOO_KEY, "Taboo list", choices, str(current) if current else ""))
        return out

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        pool = self.deck_pool(deck, pool)
        card = pool.get(card.id) or card
        identity = self._identity(deck, pool)
        if card.get("kind") == "investigator" or card.raw.get("deck_limit") == 0:
            return False
        restricted = self._restriction_ok(card, identity)
        if restricted is not None:
            return restricted
        if card.get("kind") in ("weakness", "story") or self._exempt(card, identity, pool):
            return True
        for option in self._options(identity, deck, pool):
            if option.get("ignore_match") or not self.option_matches(option, card, deck):
                continue
            return not option.get("not")
        return False

    def max_copies(self, card: DeckCard, deck: Deck) -> int:
        limit = card.raw.get("deck_limit")
        if "taboo_xp" not in card.raw and (entry := self._taboo_entry(deck, card)):
            limit = apply_taboo(card, entry).raw.get("deck_limit")   # called with an untabooed card
        return 0 if limit == 0 else int(limit or DEFAULT_COPIES)

    def _assign(self, deck: Deck, pool: Pool, identity: DeckCard) -> tuple[list[str], list[str]]:
        """Offer each non-exempt copy to the deck options (see module doc).
        Returns (ids of illegal cards, limit error messages)."""
        options = self._options(identity, deck, pool)
        used = [0] * len(options)
        illegal, limit_errors = [], []
        for card_id, qty in sorted(deck.slots.items()):
            card = pool.get(card_id)
            if card is None or self._exempt(card, identity, pool):
                continue
            restricted = self._restriction_ok(card, identity)
            if restricted is False:
                illegal.append(card.id)
                continue
            for _ in range(qty):
                blocked_by = None
                placed = False
                for i, option in enumerate(options):
                    if option.get("ignore_match") or not self.option_matches(option, card, deck):
                        continue
                    if option.get("not"):
                        break
                    limit = option.get("limit")
                    if limit is not None and used[i] >= limit:
                        blocked_by = blocked_by or option
                        continue
                    used[i] += 1
                    placed = True
                    break
                if placed:
                    continue
                if blocked_by is not None:
                    message = blocked_by.get("error") or (
                        f"Too many cards under '{blocked_by.get('name', 'a limited option')}'"
                        f" (limit {blocked_by['limit']})")
                    if message not in limit_errors:
                        limit_errors.append(message)
                elif card.id not in illegal:
                    illegal.append(card.id)
        return illegal, limit_errors

    def validate(self, deck: Deck, pool: Pool) -> list[Problem]:
        pool = self.deck_pool(deck, pool)
        identity = self._identity(deck, pool)
        if identity is None:
            return [Problem("Choose an investigator")]
        problems = []

        wanted = taboo_id(deck)
        if wanted is not None and not isinstance(pool, TabooPool):
            problems.append(Problem(
                f"Taboo list {wanted} isn't downloaded, so it isn't applied"
                " (run `tcgnecro catalog ahlcg --refresh`)", severity="warning"))
        forbidden = sorted(cid for cid in deck.slots
                           if (c := pool.get(cid)) and c.raw.get("deck_limit") == 0 and "taboo_xp" in c.raw)
        for card_id in forbidden:
            problems.append(Problem(f"{pool.get(card_id).name} is forbidden by the taboo list",
                                    card_ids=[card_id]))

        unknown = [cid for cid in deck.slots if pool.get(cid) is None]
        if unknown:
            problems.append(Problem(f"Unknown card codes: {', '.join(unknown)}", card_ids=unknown))

        size = self._deck_size(deck, identity, pool)
        counted = self._counted(deck, pool, identity)
        if counted != size:
            problems.append(Problem(f"Deck has {counted} cards; {identity.raw['name']} needs {size}"))

        illegal, limit_errors = self._assign(deck, pool, identity)
        for card_id in illegal:
            if card_id in forbidden:
                continue
            problems.append(Problem(f"{pool.get(card_id).name} isn't allowed for this investigator",
                                    card_ids=[card_id]))
        problems += [Problem(m) for m in limit_errors]

        # Copy limits are by title: all levels of a card share one limit.
        by_title: dict[str, list[DeckCard]] = {}
        for card_id in deck.slots:
            card = pool.get(card_id)
            if card:
                by_title.setdefault(card.raw.get("real_name") or card.raw["name"], []).append(card)
        for title, cards in sorted(by_title.items()):
            copies = sum(deck.count(c.id) for c in cards)
            limit = max(self.max_copies(c, deck) for c in cards)
            if copies > limit and not all(c.id in forbidden for c in cards):
                problems.append(Problem(f"{copies} copies of {title} (limit {limit})",
                                        card_ids=[c.id for c in cards]))

        for req in self._required(identity, pool):
            if not any(deck.count(cid) for cid in req):
                names = sorted({pool.get(cid).name for cid in req if pool.get(cid)})
                problems.append(Problem(f"Missing required card: {' or '.join(names) or '?'}"))

        problems += self._atleast_problems(deck, pool, identity)
        problems += self._special_problems(deck, pool, identity)

        wanted = sum(1 for r in (identity.raw.get("deck_requirements") or {}).get("random") or []
                     if r.get("value") == "basicweakness")
        have = sum(qty for cid, qty in deck.slots.items()
                   if (c := pool.get(cid)) and c.get("subtype") == "basicweakness")
        if have < wanted:
            problems.append(Problem(
                f"Add {wanted - have} random basic weakness{'es' if wanted - have > 1 else ''}"
                " (use the button below)", severity="warning"))
        return problems

    def _counted(self, deck: Deck, pool: Pool, identity: DeckCard | None) -> int:
        """Cards that count toward deck size."""
        total = 0
        for card_id, qty in deck.slots.items():
            card = pool.get(card_id)
            if card and not self._exempt(card, identity, pool) and not card.raw.get("permanent"):
                total += qty
        return total

    def _atleast_problems(self, deck: Deck, pool: Pool, identity: DeckCard) -> list[Problem]:
        problems = []
        for option in self._options(identity):
            rule = option.get("atleast")
            if not rule:
                continue
            per_faction: dict[str, int] = {}
            for card_id, qty in deck.slots.items():
                card = pool.get(card_id)
                if card is None or self._exempt(card, identity, pool):
                    continue
                for faction in card.get("factions"):
                    if faction in option.get("faction", []):
                        per_faction[faction] = per_faction.get(faction, 0) + qty
            met = sum(1 for n in per_faction.values() if n >= rule.get("min", 0))
            if met < rule.get("factions", 0):
                problems.append(Problem(option.get("error") or
                                        f"Needs at least {rule['min']} cards from {rule['factions']} classes"))
        return problems

    def _special_problems(self, deck: Deck, pool: Pool, identity: DeckCard) -> list[Problem]:
        """Rules from DECKBUILDING_CARDS beyond deck size."""
        special = self._special(deck, pool)
        problems = []
        counted = [(c, q) for cid, q in deck.slots.items()
                   if (c := pool.get(cid)) and not self._exempt(c, identity, pool)]
        if "Underworld Support" in special:
            by_title: dict[str, int] = {}
            for card, qty in counted:
                title = card.raw.get("real_name") or card.raw["name"]
                by_title[title] = by_title.get(title, 0) + qty
            doubled = sorted(t for t, n in by_title.items() if n > 1)
            if doubled:
                problems.append(Problem("Underworld Support: only 1 copy of each card by title ("
                                        + ", ".join(doubled) + ")"))
        if "Ancestral Knowledge" in special:
            skills = sum(q for c, q in counted if c.get("kind") == "skill")
            if skills < 10:
                problems.append(Problem(f"Ancestral Knowledge needs at least 10 skills (has {skills})"))
        if "Underworld Market" in special:
            illicit = sum(q for c, q in counted if "illicit" in _traits(c.raw))
            if illicit < 10:
                problems.append(Problem(f"Underworld Market needs at least 10 Illicit cards (has {illicit})"))
        return problems

    def xp(self, deck: Deck, pool: Pool) -> int:
        """Experience the deck costs: exceptional cards cost double, myriad
        cards are paid for once per title. A taboo list's chain (unchain) is
        added to (taken from) the level before doubling, never below 0."""
        pool = self.deck_pool(deck, pool)
        total, myriad_seen = 0, set()
        for card_id, qty in deck.slots.items():
            card = pool.get(card_id)
            if card is None:
                continue
            level = max(0, (card.raw.get("xp") or 0) + (card.raw.get("taboo_xp") or 0))
            if not level:
                continue
            xp = level * (2 if card.raw.get("exceptional") else 1)
            if card.raw.get("myriad"):
                if card.raw["name"] not in myriad_seen:
                    myriad_seen.add(card.raw["name"])
                    total += xp
            else:
                total += xp * qty
        return total

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        pool = self.deck_pool(deck, pool)
        identity = self._identity(deck, pool)
        size = self._deck_size(deck, identity, pool)
        counted = self._counted(deck, pool, identity)
        weaknesses = sum(q for cid, q in deck.slots.items() if (c := pool.get(cid)) and c.get("kind") == "weakness")
        stats = [
            Stat("Cards", f"{counted} / {size}", counted == size),
            Stat("XP in deck", str(self.xp(deck, pool)),
                 title="Total experience cost of the cards in the deck (exceptional cards count double"
                       + (", taboo list chains included)" if isinstance(pool, TabooPool) else ")")),
        ]
        exp = deck.meta.get("experience")
        if exp:
            available = exp["earned"] + exp["adjustment"] - exp["spent"]
            source = " (from ArkhamDB, as of the last sync)" if deck.sync else ""
            stats.append(Stat(
                "XP to spend", str(available), ok=False if available < 0 else None,
                title=f"Earned {exp['earned']} for this upgrade, adjustment {exp['adjustment']:+d}, "
                      f"spent {exp['spent']}{source}"))
        stats += [
            Stat("Weaknesses", str(weaknesses)),
            Stat("Total incl. signatures", str(deck.total())),
        ]
        return stats

    def actions(self, deck: Deck, pool: Pool) -> list[Action]:
        return [Action("random_weakness", "Draw random basic weakness")] if deck.identity else []

    def run_action(self, key: str, deck: Deck, pool: Pool) -> str | None:
        if key != "random_weakness":
            raise KeyError(key)
        pool = self.deck_pool(deck, pool)
        identity = self._identity(deck, pool)
        candidates = [
            c for c in pool.cards.values()
            if c.get("subtype") == "basicweakness" and not c.get("story")
            and self._restriction_ok(c, identity) is not False
            and deck.count(c.id) < self.max_copies(c, deck)
        ]
        if not candidates:
            return "No basic weaknesses left to draw"
        card = random.choice(candidates)
        deck.set_count(card.id, deck.count(card.id) + 1)
        return f"Drew {card.name}"

    # ----- simulation (simulate.py) -------------------------------------------

    def sim_settings(self, deck: Deck, pool: Pool) -> list:
        if not self._identity(deck, pool):
            return []
        from tcgnecro.games.ahlcg import simulate
        return simulate.settings()

    def simulate(self, deck: Deck, pool: Pool, values: dict):
        from tcgnecro.games.ahlcg import simulate
        return simulate.run(deck, pool, values)

    # ----- display ----------------------------------------------------------

    def detail_fields(self, card: DeckCard) -> list[tuple[str, str]]:
        raw = card.raw
        fields = [
            ("Type", raw.get("type_code", "").title() + (" (weakness)" if card.get("kind") == "weakness" else "")),
            ("Class", " / ".join(FACTION_NAMES.get(f, f) for f in card.get("factions"))),
            ("Level", str(raw["xp"]) if raw.get("xp") is not None else ""),
            ("Cost", "X" if raw.get("cost") is None and raw.get("type_code") in ("asset", "event") else str(raw.get("cost") or "")),
            ("Traits", raw.get("traits") or ""),
            ("Slot", raw.get("real_slot") or ""),
            ("Skill icons", " ".join(s.title() for s in card.get("skills"))),
            ("Health / Sanity", f"{raw.get('health', '–')} / {raw.get('sanity', '–')}"
             if raw.get("health") is not None or raw.get("sanity") is not None else ""),
            ("Deck limit", "Forbidden" if raw.get("deck_limit") == 0 else str(raw.get("deck_limit") or "")),
            ("Taboo", raw.get("taboo_text") or ""),
            ("Pack", f"{raw.get('pack_code')} #{raw.get('position')}"),
            ("Code", card.id),
        ]
        return [(k, v) for k, v in fields if v]

    # ----- import / export --------------------------------------------------

    # ArkhamDB has two kinds of deck: published *decklists* (/decklist/view/N),
    # which don't change, and players' *decks* (/deck/view/N), readable when
    # shared. Upgrading a deck with experience creates a new deck and links
    # the versions with previous_deck / next_deck; sync follows next_deck to
    # the newest one. Private decks need ArkhamDB's login API (not supported).

    def sync_link(self, source: str) -> dict | None:
        source = (source or "").strip()
        if m := re.search(r"arkhamdb\.com/decklist/(?:view/)?(\d+)", source):
            kind, remote_id = "decklist", m.group(1)
        elif m := re.search(r"arkhamdb\.com/deck/(?:view/)?(\d+)", source):
            kind, remote_id = "deck", m.group(1)
        elif source.isdigit():
            kind, remote_id = "auto", source     # resolved on first fetch
        else:
            return None
        return {"provider": "arkhamdb", "provider_name": "ArkhamDB", "kind": kind, "remote_id": remote_id}

    def fetch_remote(self, link: dict, pool: Pool) -> RemoteDeck:
        kind, remote_id = link["kind"], str(link["remote_id"])
        if kind == "auto":
            try:
                data, kind = self._fetch("decklist", remote_id), "decklist"
            except ValueError:
                data, kind = self._fetch("deck", remote_id), "deck"
        else:
            data = self._fetch(kind, remote_id)
        # Follow upgrades to the newest version (bounded, in case of a loop).
        for _ in range(100):
            nxt = data.get("next_deck")
            if kind != "deck" or not nxt:
                break
            data = self._fetch("deck", str(nxt))
        return self._remote_from(data, kind, pool)

    def _fetch(self, kind: str, remote_id: str) -> dict:
        try:
            data = fetch_json(f"{API}/{kind}/{remote_id}")
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ValueError(f"ArkhamDB {kind} {remote_id} isn't readable (private or deleted?)") from None
            raise
        if not isinstance(data, dict) or "slots" not in data:
            raise ValueError(f"ArkhamDB {kind} {remote_id} isn't readable (private or deleted?)")
        return data

    def _remote_from(self, data: dict, kind: str, pool: Pool) -> RemoteDeck:
        investigator = data.get("investigator_code")

        def normalize(slots: dict | list | None) -> dict[str, int]:
            out: dict[str, int] = {}
            for code, qty in (slots or {}).items() if isinstance(slots, dict) else []:
                if code == RANDOM_WEAKNESS_PLACEHOLDER:
                    continue  # validation will ask for a weakness to be drawn
                cid = pool.resolve(code)
                if cid != investigator:
                    out[cid] = out.get(cid, 0) + int(qty)
            return out

        meta = data.get("meta") or {}
        if isinstance(meta, str):
            meta = json.loads(meta) if meta.strip() else {}
        if data.get("taboo_id"):
            meta["taboo_id"] = data["taboo_id"]
        # Campaign experience as ArkhamDB tracks it: earned for this upgrade
        # (xp), spent on it (xp_spent, worked out by ArkhamDB against the
        # previous version), and a manual adjustment.
        if any(data.get(k) is not None for k in ("xp", "xp_spent", "xp_adjustment")):
            meta["experience"] = {
                "earned": int(data.get("xp") or 0),
                "spent": int(data.get("xp_spent") or 0),
                "adjustment": int(data.get("xp_adjustment") or 0),
            }

        return RemoteDeck(
            name=data.get("name") or "Imported deck",
            identity=pool.resolve(investigator) if investigator else None,
            slots=normalize(data.get("slots")),
            side_slots=normalize(data.get("sideSlots")),
            meta=meta,
            remote_id=str(data["id"]),
            url=f"https://arkhamdb.com/{kind}/view/{data['id']}",
            kind=kind,
            version=str(data["version"]) if data.get("version") else None,
            updated=data.get("date_update"),
            notes=data.get("description_md") or "",
        )

    def import_deck(self, source: str, pool: Pool) -> Deck:
        """ArkhamDB decklist or deck URL (or bare id), as a one-off copy.
        The web app links imports for syncing instead (deckbuilder/sync.py)."""
        link = self.sync_link(source)
        if link is None:
            raise ValueError("Paste an ArkhamDB deck or decklist URL")
        remote = self.fetch_remote(link, pool)
        return Deck(id="", game=self.game_id, name=remote.name, identity=remote.identity,
                    slots=remote.slots, side_slots=remote.side_slots, meta=remote.meta,
                    notes=remote.notes, source=source.strip())


PLUGIN = AhlcgPlugin("ahlcg", Path(__file__).with_name("deckbuilder.toml"))
