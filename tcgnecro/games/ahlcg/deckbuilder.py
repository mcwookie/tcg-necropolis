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

Option criteria supported: faction, faction_select, option_select,
deck_size_select, level, type, trait, slot, uses, text, tag, permanent, not,
limit, atleast, ignore_match. Not yet: taboo lists, customizable upgrades,
parallel investigator front/back choices, base_level. See docs/ROADMAP.md.
"""

import json
import random
import re
import urllib.error
from pathlib import Path

from tcgnecro.deckbuilder.model import Action, Deck, DeckCard, Pool, Problem, RemoteDeck, Selection, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin
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
        return out

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        identity = self._identity(deck, pool)
        if card.get("kind") == "investigator":
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
        return int(card.raw.get("deck_limit") or DEFAULT_COPIES)

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
        identity = self._identity(deck, pool)
        if identity is None:
            return [Problem("Choose an investigator")]
        problems = []

        unknown = [cid for cid in deck.slots if pool.get(cid) is None]
        if unknown:
            problems.append(Problem(f"Unknown card codes: {', '.join(unknown)}", card_ids=unknown))

        size = self._deck_size(deck, identity, pool)
        counted = self._counted(deck, pool, identity)
        if counted != size:
            problems.append(Problem(f"Deck has {counted} cards; {identity.raw['name']} needs {size}"))

        illegal, limit_errors = self._assign(deck, pool, identity)
        for card_id in illegal:
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
            if copies > limit:
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
        cards are paid for once per title."""
        total, myriad_seen = 0, set()
        for card_id, qty in deck.slots.items():
            card = pool.get(card_id)
            if card is None or not card.raw.get("xp"):
                continue
            xp = card.raw["xp"] * (2 if card.raw.get("exceptional") else 1)
            if card.raw.get("myriad"):
                if card.raw["name"] not in myriad_seen:
                    myriad_seen.add(card.raw["name"])
                    total += xp
            else:
                total += xp * qty
        return total

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        identity = self._identity(deck, pool)
        size = self._deck_size(deck, identity, pool)
        counted = self._counted(deck, pool, identity)
        weaknesses = sum(q for cid, q in deck.slots.items() if (c := pool.get(cid)) and c.get("kind") == "weakness")
        return [
            Stat("Cards", f"{counted} / {size}", counted == size),
            Stat("XP", str(self.xp(deck, pool))),
            Stat("Weaknesses", str(weaknesses)),
            Stat("Total incl. signatures", str(deck.total())),
        ]

    def actions(self, deck: Deck, pool: Pool) -> list[Action]:
        return [Action("random_weakness", "Draw random basic weakness")] if deck.identity else []

    def run_action(self, key: str, deck: Deck, pool: Pool) -> str | None:
        if key != "random_weakness":
            raise KeyError(key)
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
            ("Deck limit", str(raw.get("deck_limit") or "")),
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
