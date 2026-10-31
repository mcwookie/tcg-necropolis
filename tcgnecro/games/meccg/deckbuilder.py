"""Middle-earth CCG deck builder rules.

Follows the Council of Elrond's Official Game Rules (councilofelrond.org/rules,
deck construction, sections 1.3-1.7), with card data from the Council's card
database. A MECCG deck has four parts, which this builder keeps as the main
deck plus three sections (plugin.sections()):

    play deck (main)   30-50 resources, as many hazards, up to 10 non-avatar
                       characters, up to 3 avatars (not three different ones),
                       at least 12 creatures among the hazards
    location deck      sites only: any number of havens, one of each other site
    sideboard          up to 30/35/40 cards by game length
    pool               up to 10 non-avatar characters and up to 2 non-unique,
                       non-hoard minor items (Fallen-wizard: plus Stage
                       resources totalling exactly 3 stage points)

Across play deck, sideboard and pool: one copy of each unique non-avatar card,
three of each non-unique card (havens exempt), and agents' total mind at most
36. Avatar-specific cards need that avatar in the deck.

The player's alignment ("Playing as": Wizard, Ringwraith, Fallen-wizard or
Balrog) decides which characters, resources and sites are allowed; see
_allowed(). Agents count as hazards for Wizards and Balrogs and as characters
otherwise. The Fallen-wizard and Balrog banned lists are in BANNED.

Not checked: the extra 10 sideboard cards against Fallen-wizard opponents,
the Fallen-wizard "two copies of a hazard playable as a resource" split, and
the starting-company draft (a game-time choice). See docs/ROADMAP.md.
"""

import re
from pathlib import Path

from tcgnecro.deckbuilder.model import MAIN, Deck, DeckCard, Pool, Problem, Section, Selection, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin

LOCATION, SIDEBOARD, POOL = "location", "sideboard", "pool"
ALIGNMENTS = [("wizard", "Wizard (hero)"), ("ringwraith", "Ringwraith (minion)"),
              ("fallen", "Fallen-wizard"), ("balrog", "Balrog")]
GAME_LENGTHS = [("short", "Starter or Short game (sideboard 30)"), ("long", "Long game (sideboard 35)"),
                ("campaign", "Campaign game (sideboard 40)")]
SIDEBOARD_SIZE = {"short": 30, "long": 35, "campaign": 40}
AVATAR_RACE = {"wizard": ("Hero", "Wizard"), "ringwraith": ("Minion", "Ringwraith"),
               "fallen": ("Fallen-wizard", "Fallen-wizard"), "balrog": ("Balrog", "Balrog")}

# Balrog sites any player's location deck may include (1.4.B1).
SHARED_BALROG_SITES = {"ancientdeephold", "thewinddeeps", "thedrowningdeeps", "therusteddeeps",
                       "remainsofthangorodrim"}
# Minion sites a Balrog player needs the Balrog version of (1.4.B2), besides
# Under-deeps sites and Dark-holds.
BALROG_REPLACED = {"moria", "carndum", "dolguldur", "minasmorgul"}
BALROG_FACTIONS = {"Orc", "Troll", "Wolf", "Animal", "Dragon"}

BANNED = {
    "fallen": {"badetorule", "thebalrog", "cracksofdoom", "favorofthevalar", "gollumsfate", "hourofneed",
               "killallbutnotthehalflings", "thelidlesseye", "glamourofsurpassingexcellence",
               "messengerofmordor", "newsmustgetthrough", "newsoftheshire", "oldroad", "thesununveiled",
               "useyourlegs", "thewindlordfoundme", "wizarduncloaked"},
    "balrog": {"abovetheabyss", "badetorule", "thebalrog", "balrogofmoria", "theblackcouncil", "blackhorse",
               "blackrider", "bytheringwraithsword", "creatureofanolderworld", "durinsbane", "fellrider",
               "thefieryblade", "helmoffear", "heraldedlord", "killallbutnotthehalflings", "thelidlesseye",
               "morgulblade", "newsoftheshire", "opentothesummons", "ordersfromlugburz", "paddingfeet",
               "theringleavesitsmark", "sauron", "theyridetogether", "useyourlegs",
               "whiletheyellowfacesleeps"},
}


def _key(name: str) -> str:
    """Names compared ignoring case, accents' base letters aside, spaces and punctuation."""
    import unicodedata
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def _banned(card: DeckCard, alignment: str) -> bool:
    key = _key(card.raw["name"])
    if key in BANNED.get(alignment, ()):
        # "The Balrog (Ally)" is the resource; the Balrog avatar is a character.
        return not (key == "thebalrog" and card.get("type") == "character")
    return alignment == "balrog" and card.get("type") == "resource" and key.endswith("unleashed")


class MeccgPlugin(DeckbuilderPlugin):

    # ----- card pool --------------------------------------------------------

    def build_card(self, card_id: str, title: str, raw: dict) -> DeckCard | None:
        card_type = (raw.get("card_type") or "").lower()
        if card_type not in ("character", "resource", "hazard", "site"):
            return None   # regions: replaced by a map in tournament play (1.4)
        subtype = raw.get("subtype") or ""
        if card_type == "character":
            kind = "avatar" if raw.get("avatar") else ("agent" if raw.get("agent") else "character")
        elif card_type == "hazard":
            kind = "creature" if "Creature" in subtype else "hazard"
        elif card_type == "site":
            kind = "haven" if raw.get("site_type") == "{H}" else "site"
        else:
            kind = "item" if "Item" in subtype else ("faction" if subtype == "Faction" else
                                                     ("ally" if subtype == "Ally" else "resource"))
        name = raw["name"] + (" ✦" if raw.get("unique") else "")
        attrs = {
            "name": name,
            "type": card_type,
            "kind": kind,
            "alignment": (raw.get("alignment") or "").lower(),
            "subtype": subtype,
            "unique": bool(raw.get("unique")),
            "race": raw.get("race"),
            "mind": raw.get("mind"),
            "mp": raw.get("marshalling_points"),
            "text": raw.get("text") or "",
            "keywords": ", ".join(raw.get("keywords") or []),
            "specific": raw.get("specific"),
            "pack": raw.get("pack_code"),
        }
        return DeckCard(card_id, title, name, attrs, raw)

    # ----- deck parts and choices ---------------------------------------------

    def sections(self) -> list[Section]:
        return [Section(LOCATION, "Location deck", "Sites"),
                Section(SIDEBOARD, "Sideboard"),
                Section(POOL, "Pool", "Starting characters and minor items")]

    def main_label(self) -> str:
        return "Play deck"

    def _alignment(self, deck: Deck) -> str:
        value = deck.meta.get("alignment")
        return value if value in dict(ALIGNMENTS) else "wizard"

    def start_deck(self, deck: Deck, pool: Pool) -> None:
        deck.meta.setdefault("alignment", "wizard")
        deck.meta.setdefault("length", "short")

    def selections(self, deck: Deck, pool: Pool) -> list[Selection]:
        return [Selection("alignment", "Playing as", ALIGNMENTS, self._alignment(deck)),
                Selection("length", "Game length", GAME_LENGTHS, deck.meta.get("length") or "short")]

    def allowed_in(self, card: DeckCard, section: str) -> bool:
        if section == LOCATION:
            return card.get("type") == "site"
        if section == POOL:
            return card.get("type") == "character" or card.get("type") == "resource"
        return card.get("type") != "site"

    def _agent_is_hazard(self, deck: Deck) -> bool:
        return self._alignment(deck) in ("wizard", "balrog")

    def _copy_limit(self, card: DeckCard, deck: Deck) -> int:
        """Copies allowed across play deck, sideboard and pool."""
        if card.get("kind") == "avatar":
            return 3
        if card.get("unique"):
            return 1
        if self._alignment(deck) == "fallen":
            if card.get("alignment") == "stage":
                return 3
            if card.get("type") == "character" or (card.get("type") == "resource"
                                                   and card.get("alignment") in ("hero", "minion")):
                return 2
        return 3

    def _location_limit(self, card: DeckCard, deck: Deck) -> int:
        alignment = self._alignment(deck)
        if alignment == "fallen" and card.get("alignment") == "fallen-wizard":
            return 99
        if card.get("kind") == "haven" and alignment != "fallen":
            return 99
        return 1

    def max_copies(self, card: DeckCard, deck: Deck) -> int:
        if card.get("type") == "site":
            return self._location_limit(card, deck)
        return self._copy_limit(card, deck)

    def section_limit(self, card: DeckCard, deck: Deck, section: str) -> int:
        if section == LOCATION:
            return self._location_limit(card, deck)
        others = sum(deck.count(card.id, s) for s in (MAIN, SIDEBOARD, POOL) if s != section)
        return max(0, self._copy_limit(card, deck) - others)

    # ----- what each alignment may use ----------------------------------------

    def _allowed(self, card: DeckCard, deck: Deck) -> str | None:
        """None if the player's alignment may include this card, else why not."""
        alignment = self._alignment(deck)
        a, t, kind = card.get("alignment"), card.get("type"), card.get("kind")
        if _banned(card, alignment):
            return f"banned for {dict(ALIGNMENTS)[alignment]} players"
        specific = card.get("specific")
        if specific == "Balrog" and alignment != "balrog":
            return "Balrog-specific"
        if specific and specific != "Balrog" and alignment not in ("wizard", "fallen"):
            return f"{specific}-specific"
        if t == "hazard":
            return None
        if t == "character":
            if kind == "avatar":
                want_alignment, want_race = AVATAR_RACE[alignment]
                ok = card.get("race") == want_race and card.raw.get("alignment") == want_alignment
                return None if ok else f"not a {dict(ALIGNMENTS)[alignment].split(' ')[0]} avatar"
            if kind == "agent" and alignment in ("wizard", "balrog"):
                return None   # counted as a hazard
            if alignment == "wizard":
                return None if a == "hero" else "a Wizard player's characters are hero characters"
            if alignment == "ringwraith":
                return None if a == "minion" else "a Ringwraith player's characters are minion characters"
            if alignment == "fallen":
                return None if a in ("hero", "minion") else "not a hero or minion character"
            # Balrog
            if a != "minion" or card.get("race") not in ("Orc", "Troll"):
                return "a Balrog player's characters are minion Orcs and Trolls"
            if (card.get("mind") or 0) >= 9 and specific != "Balrog":
                return "a Balrog player's characters have mind under 9"
            return None
        if t == "resource":
            items = "Item" in (card.get("subtype") or "")
            if alignment == "wizard":
                ok = a in ("hero", "dual") or (a == "minion" and items)
            elif alignment == "ringwraith":
                ok = a in ("minion", "dual") or (a == "hero" and items)
            elif alignment == "fallen":
                ok = a in ("hero", "minion", "stage", "dual")
            else:
                ok = a in ("minion", "dual") and (kind != "faction" or card.get("race") in BALROG_FACTIONS)
            return None if ok else f"not a resource {dict(ALIGNMENTS)[alignment].split(' ')[0]} players use"
        # sites
        key = _key(card.raw["name"])
        if a == "balrog" and key in SHARED_BALROG_SITES:
            return None
        if alignment == "wizard":
            ok = a == "hero"
        elif alignment == "ringwraith":
            ok = a == "minion"
        elif alignment == "fallen":
            ok = a in ("hero", "minion", "fallen-wizard")
        else:
            ok = a == "balrog" or (a == "minion" and key not in BALROG_REPLACED
                                    and not card.raw.get("under_deeps") and card.raw.get("site_type") != "{D}")
        return None if ok else f"not a site {dict(ALIGNMENTS)[alignment].split(' ')[0]} players use"

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        return self._allowed(card, deck) is None

    # ----- validation ---------------------------------------------------------

    def _cards(self, deck: Deck, pool: Pool, section: str) -> list[tuple[DeckCard, int]]:
        return [(c, q) for cid, q in deck.section(section).items() if (c := pool.get(cid))]

    def _play_counts(self, deck: Deck, pool: Pool) -> dict:
        agent_hazard = self._agent_is_hazard(deck)
        out = {"resources": 0, "hazards": 0, "flex": 0, "characters": 0, "avatars": {}, "creatures": 0, "halves": 0}
        for card, qty in self._cards(deck, pool, MAIN):
            t, kind, raw = card.get("type"), card.get("kind"), card.raw
            if kind == "avatar":
                out["avatars"][card.raw["name"]] = out["avatars"].get(card.raw["name"], 0) + qty
            elif kind == "agent" and agent_hazard:
                out["hazards"] += qty
                out["halves"] += qty
            elif t == "character":
                out["characters"] += qty
            elif t == "resource":
                out["flex" if raw.get("playable_as_hazard") else "resources"] += qty
            elif t == "hazard":
                out["flex" if raw.get("playable_as_resource") else "hazards"] += qty
                subtype = card.get("subtype") or ""
                name = raw["name"]
                half = ("/" in subtype and "Creature" in subtype) or name.endswith(" Ahunt") or \
                       (name.endswith(" at Home") and name != "Not at Home") or \
                       (raw.get("spawn") and "Permanent-event" in subtype and "Creature" not in subtype)
                if half:
                    out["halves"] += qty
                elif subtype == "Creature":
                    out["creatures"] += qty
        return out

    def validate(self, deck: Deck, pool: Pool) -> list[Problem]:
        problems = []
        alignment = self._alignment(deck)
        unknown = [cid for s in (MAIN, LOCATION, SIDEBOARD, POOL) for cid in deck.section(s) if pool.get(cid) is None]
        if unknown:
            problems.append(Problem(f"Unknown cards: {', '.join(unknown)}", card_ids=unknown))

        # Play deck (1.5)
        c = self._play_counts(deck, pool)
        r, h, flex = c["resources"], c["hazards"], c["flex"]
        # Cards playable as either may count as either (1.3): is there a split
        # with 30-50 resources and as many hazards?
        balanced = (h + flex - r) % 2 == 0 and 0 <= (h + flex - r) // 2 <= flex and \
            30 <= r + (h + flex - r) // 2 <= 50
        if not balanced:
            extra = f" (+{flex} that may count as either)" if flex else ""
            problems.append(Problem(f"Play deck has {r} resources and {h} hazards{extra}; "
                                    "it needs 30-50 resources and as many hazards"))
        if c["characters"] > 10:
            problems.append(Problem(f"Play deck has {c['characters']} non-avatar characters (at most 10)"))
        avatars = c["avatars"]
        if sum(avatars.values()) > 3:
            problems.append(Problem(f"Play deck has {sum(avatars.values())} avatars (at most 3)"))
        elif len(avatars) >= 3:
            problems.append(Problem("Three different avatars aren't allowed"))
        creatures = c["creatures"] + c["halves"] // 2
        if creatures < 12:
            problems.append(Problem(f"Play deck has {creatures} creatures among its hazards (at least 12)"))

        # Every card the alignment can't use, wherever it is.
        for s in (MAIN, LOCATION, SIDEBOARD, POOL):
            for card, _ in self._cards(deck, pool, s):
                why = self._allowed(card, deck)
                if why:
                    problems.append(Problem(f"{card.raw['name']}: {why}", card_ids=[card.id]))
                elif not self.allowed_in(card, s):
                    problems.append(Problem(f"{card.raw['name']} can't go in the "
                                            f"{dict((x.key, x.label) for x in self.sections()).get(s, 'play deck')}",
                                            card_ids=[card.id]))

        # Copies across play deck, sideboard and pool (1.3)
        counts: dict[str, int] = {}
        for s in (MAIN, SIDEBOARD, POOL):
            for cid, q in deck.section(s).items():
                counts[cid] = counts.get(cid, 0) + q
        for cid, qty in sorted(counts.items()):
            card = pool.get(cid)
            if card and card.get("kind") != "haven" and qty > self._copy_limit(card, deck):
                problems.append(Problem(f"{qty} copies of {card.raw['name']} (limit {self._copy_limit(card, deck)})",
                                        card_ids=[cid]))
        agents_mind = sum((pool.get(cid).get("mind") or 0) * q for cid, q in counts.items()
                          if pool.get(cid) and pool.get(cid).get("kind") == "agent")
        if agents_mind > 36:
            problems.append(Problem(f"Agents' total mind is {agents_mind} (at most 36)"))
        avatar_names = {pool.get(cid).raw["name"] for cid in counts if pool.get(cid) and pool.get(cid).get("kind") == "avatar"}
        for cid in counts:
            card = pool.get(cid)
            specific = card.get("specific") if card else None
            if specific and specific != "Balrog" and specific not in avatar_names and not self._allowed(card, deck):
                problems.append(Problem(f"{card.raw['name']} is {specific}-specific; include {specific} to play it",
                                        card_ids=[cid]))

        # Location deck (1.4)
        for card, qty in self._cards(deck, pool, LOCATION):
            if card.get("type") == "site" and qty > self._location_limit(card, deck):
                problems.append(Problem(f"{qty} copies of {card.raw['name']} in the location deck (limit 1)",
                                        card_ids=[card.id]))

        # Sideboard (1.6)
        size = deck.total(SIDEBOARD)
        limit = SIDEBOARD_SIZE.get(deck.meta.get("length") or "short", 30)
        if size > limit:
            problems.append(Problem(f"Sideboard has {size} cards (at most {limit} for this game length)"))

        # Pool (1.7)
        pool_cards = self._cards(deck, pool, POOL)
        chars = sum(q for card, q in pool_cards if card.get("type") == "character" and card.get("kind") != "avatar")
        if chars > 10:
            problems.append(Problem(f"Pool has {chars} characters (at most 10)"))
        items = [(card, q) for card, q in pool_cards if card.get("type") == "resource" and card.get("kind") == "item"]
        if sum(q for _, q in items) > 2:
            problems.append(Problem("Pool may hold at most 2 minor items"))
        for card, _ in items:
            if card.get("unique") or "Minor" not in (card.get("subtype") or "") or "hoard" in card.get("text").lower()[:40]:
                problems.append(Problem(f"{card.raw['name']}: the pool's items must be non-unique, non-hoard minor items",
                                        card_ids=[card.id]))
        stage = [(card, q) for card, q in pool_cards if card.get("alignment") == "stage"]
        others = [(card, q) for card, q in pool_cards if card.get("type") == "resource"
                  and card.get("kind") != "item" and card.get("alignment") != "stage"]
        for card, _ in others + (stage if alignment != "fallen" else []):
            problems.append(Problem(f"{card.raw['name']} can't go in the pool", card_ids=[card.id]))
        if alignment == "fallen":
            points = sum((card.raw.get("stage_points") or 0) * q for card, q in stage)
            if points != 3 or not any(not card.get("unique") for card, _ in stage):
                problems.append(Problem(f"A Fallen-wizard's pool needs Stage resources worth exactly 3 stage points, "
                                        f"at least one non-unique (has {points})"))
        return problems

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        c = self._play_counts(deck, pool)
        creatures = c["creatures"] + c["halves"] // 2
        side_limit = SIDEBOARD_SIZE.get(deck.meta.get("length") or "short", 30)
        mp = sum((card.get("mp") or 0) * q for card, q in self._cards(deck, pool, MAIN) if card.get("type") != "hazard")
        return [
            Stat("Resources", str(c["resources"]) + (f" (+{c['flex']} either)" if c["flex"] else ""),
                 30 <= c["resources"] <= 50 or None),
            Stat("Hazards", str(c["hazards"]), c["hazards"] == c["resources"] or None),
            Stat("Creatures", f"{creatures} / 12+", creatures >= 12),
            Stat("Characters", f"{c['characters']} / 10", c["characters"] <= 10),
            Stat("Avatars", str(sum(c["avatars"].values()))),
            Stat("Sites", str(deck.total(LOCATION))),
            Stat("Sideboard", f"{deck.total(SIDEBOARD)} / {side_limit}", deck.total(SIDEBOARD) <= side_limit),
            Stat("Pool", str(deck.total(POOL))),
            Stat("MP", str(mp), title="Marshalling points on the play deck's resources and characters"),
        ]

    # ----- display ----------------------------------------------------------

    def subtitle(self, card: DeckCard) -> str:
        raw = card.raw
        bits = [raw.get("alignment") or ""]
        if card.get("type") == "character":
            bits += [raw.get("race") or "", raw.get("skills") or ""]
        elif card.get("type") == "site":
            bits.append(raw.get("site_type") or "")
        else:
            bits.append(card.get("subtype") or "")
        return " · ".join(b for b in bits if b)

    def detail_fields(self, card: DeckCard) -> list[tuple[str, str]]:
        raw = card.raw
        fields = [
            ("Type", " / ".join(x for x in (raw.get("card_type"), raw.get("subtype")) if x)),
            ("Alignment", raw.get("alignment") or ""),
            ("Unique", "yes" if raw.get("unique") else ""),
            ("Race", raw.get("race") or ""),
            ("Skills", raw.get("skills") or ""),
            ("Prowess / body", f"{raw.get('prowess')} / {raw.get('body')}" if raw.get("prowess") else ""),
            ("Mind / DI", f"{raw.get('mind')} / {raw.get('direct_influence')}" if raw.get("mind") is not None else ""),
            ("Marshalling points", f"{raw['marshalling_points']} {raw.get('marshalling_type') or ''}"
             if raw.get("marshalling_points") is not None else ""),
            ("Stage points", str(raw["stage_points"]) if raw.get("stage_points") else ""),
            ("Home site", raw.get("home_site") or ""),
            ("Specific to", raw.get("specific") or ""),
            ("Set", f"{raw.get('set')} ({raw.get('id')})"),
        ]
        return [(k, v) for k, v in fields if v]

    # ----- printing -----------------------------------------------------------

    def print_list(self, deck: Deck) -> list[tuple[str, int]]:
        """Every part: play deck, location deck, sideboard and pool."""
        out: dict[str, int] = {}
        for s in (MAIN, LOCATION, SIDEBOARD, POOL):
            for cid, q in deck.section(s).items():
                out[cid] = out.get(cid, 0) + q
        return list(out.items())


PLUGIN = MeccgPlugin("meccg", Path(__file__).with_name("deckbuilder.toml"))
