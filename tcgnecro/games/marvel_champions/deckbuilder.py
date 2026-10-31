"""Marvel Champions deck builder rules.

Follows MarvelCDB's deck validation (marvelcdb.com, app.deck.js), so decks
that are legal there are legal here:

- A deck is built around a hero. The hero's own cards (its set) go in at their
  printed quantities and are always allowed.
- Cards from any other set (other heroes' cards, campaign cards, leaders) and
  encounter cards are never allowed. Basic cards always are.
- The player picks an aspect (Aggression, Justice, Leadership, Protection or
  'Pool); its cards are allowed. Until one is picked, every aspect is.
- Some heroes change that, as data on the hero card:
    deck_requirements {"aspects": 2}        Spider-Woman: two aspects, equal counts
    deck_requirements {"aspects": 4,        Adam Warlock: all four aspects, equal
                       "limit": 1}          counts, exactly one copy of each card
    deck_options [{type, trait, resource,   extra cards from any aspect, e.g.
                   limit, name_limit,       Gamora's 6 attack/thwart events or
                   use_deck_limit}]         Cyclops's X-Men allies
- 40 to 50 cards, hero cards included, permanents not counted. Each card up to
  its deck_limit (usually 3, or 1 for "Max 1 per deck"); reprints are the same
  card.

Not yet: MarvelCDB's "current" format (legacy packs excluded) and campaign-mode
cards. See docs/ROADMAP.md.
"""

import json
import re
import urllib.error
from pathlib import Path

from tcgnecro.deckbuilder.model import Deck, DeckCard, Pool, Problem, RemoteDeck, Selection, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin
from tcgnecro.games.marvel_champions import API, fetch_json

DECK_TYPES = {"ally", "event", "resource", "support", "upgrade", "player_side_scheme"}
ASPECTS = ["aggression", "justice", "leadership", "protection", "pool"]
ASPECT_NAMES = {"aggression": "Aggression", "justice": "Justice", "leadership": "Leadership",
                "protection": "Protection", "pool": "'Pool", "basic": "Basic", "hero": "Hero"}
RESOURCES = ["energy", "mental", "physical", "wild"]
MIN_DECK, MAX_DECK = 40, 50
DEFAULT_COPIES = 3
# Set types whose cards belong to a hero but aren't deck cards (set aside).
NOT_IN_DECK_SETS = {"hero_special"}
# What a deck's PDF includes besides the deck (deck.meta["print"]).
PRINT_CHOICES = [("deck", "Hero and deck"),
                 ("encounter", "Hero, deck, obligation and nemesis set")]


def _traits(raw: dict) -> str:
    return (raw.get("traits") or "").upper()


class MarvelChampionsPlugin(DeckbuilderPlugin):

    # ----- card pool --------------------------------------------------------

    def build_card(self, card_id: str, title: str, raw: dict) -> DeckCard | None:
        type_code, faction = raw.get("type_code"), raw.get("faction_code")
        if type_code == "hero":
            if card_id.endswith("c"):
                return None   # a hero's third form (Ant-Man giant), not an identity
            kind = "hero"
        elif faction == "encounter" and type_code in ("obligation", "minion", "treachery", "attachment",
                                                      "side_scheme", "environment", "upgrade", "support",
                                                      "ally", "event"):
            # Encounter cards are never deck cards, but a hero's obligation and
            # nemesis set are kept so a deck can print them (see print_list).
            set_code = raw.get("card_set_code") or ""
            if not (type_code == "obligation" or set_code.endswith("_nemesis")):
                return None
            kind = "encounter"
        elif type_code in DECK_TYPES and faction in ("basic", "hero", "pool", *ASPECTS):
            if raw.get("card_set_type_name_code") in NOT_IN_DECK_SETS:
                return None
            if raw.get("card_set_type_name_code") == "leader":
                return None   # Civil War's competitive-mode leaders
            kind = type_code
        else:
            return None   # villains, schemes, campaign cards, alter egos

        resources = [r for r in RESOURCES for _ in range(raw.get(f"resource_{r}") or 0)]
        name = raw["name"]
        if kind == "hero" and raw.get("back_name") and raw["back_name"] != name:
            name += f" ({raw['back_name']})"
        attrs = {
            "name": name,
            "kind": kind,
            "aspect": faction,
            "cost": raw.get("cost"),
            "traits": raw.get("traits") or "",
            "text": raw.get("text") or "",
            "resources": resources,
            "resource_icons": sorted(set(resources)),
            "set": raw.get("card_set_code"),
            "set_type": raw.get("card_set_type_name_code"),
            "unique": bool(raw.get("is_unique")),
            "pack": raw.get("pack_code"),
            "deck_limit": raw.get("deck_limit"),
            "quantity": raw.get("quantity") or 1,
        }
        return DeckCard(card_id, title, name, attrs, raw)

    def build_pool(self, rows):
        pool = super().build_pool(rows)
        # MarvelCDB decks may list a reprint's own code, or the code of a
        # two-sided card's back (34002b); both stand for the card here.
        for card_id, _, raw in rows:
            if card_id in pool.cards:
                for code in [*(raw.get("reprints") or []), raw.get("linked_to_code")]:
                    if code and code not in pool.cards:
                        pool.aliases[code] = card_id
        return pool

    def identities(self, pool: Pool) -> list[DeckCard]:
        return sorted((c for c in pool.cards.values() if c.get("kind") == "hero"), key=lambda c: c.name)

    # ----- helpers ----------------------------------------------------------

    def _identity(self, deck: Deck, pool: Pool) -> DeckCard | None:
        return pool.get(deck.identity) if deck.identity else None

    def _requirements(self, identity: DeckCard | None) -> dict:
        reqs = (identity.raw.get("deck_requirements") or []) if identity else []
        if isinstance(reqs, dict):
            reqs = [reqs]
        merged = {}
        for req in reqs:
            merged |= req
        return merged

    def _aspect_count(self, identity: DeckCard | None) -> int:
        return int(self._requirements(identity).get("aspects") or 1)

    def _chosen_aspects(self, deck: Deck, identity: DeckCard | None) -> list[str]:
        """The aspects picked for this deck (none: not picked yet)."""
        wanted = self._aspect_count(identity)
        if wanted >= len(ASPECTS) - 1:
            return []      # Adam Warlock: every aspect, nothing to pick
        keys = ["aspect", "aspect2"][:wanted]
        return [deck.meta[k] for k in keys if deck.meta.get(k) in ASPECTS]

    def _hero_set(self, identity: DeckCard | None, pool: Pool) -> list[DeckCard]:
        """The hero's own deck cards, which every deck for it includes."""
        if identity is None:
            return []
        return [c for c in pool.cards.values()
                if c.get("set") == identity.get("set") and c.get("kind") in DECK_TYPES
                and c.get("set_type") == "hero"]

    def _own(self, card: DeckCard, identity: DeckCard | None) -> bool:
        return identity is not None and card.get("set") is not None and card.get("set") == identity.get("set")

    def option_matches(self, option: dict, card: DeckCard) -> bool:
        """Does a card meet one of the hero's deck_options (ignoring limits)?"""
        raw = card.raw
        if "type" in option and raw.get("type_code") not in option["type"]:
            return False
        if "trait" in option and not any(t.upper() in _traits(raw) for t in option["trait"]):
            return False
        if "resource" in option and not any((raw.get(f"resource_{r.lower()}") or 0) > 0 for r in option["resource"]):
            return False
        return True

    def _base_legal(self, card: DeckCard, deck: Deck, identity: DeckCard | None) -> bool | None:
        """True/False when the card's set, faction or aspect settles it; None
        when only the hero's deck_options could allow it."""
        if card.get("kind") in ("hero", "encounter"):
            return False
        if self._own(card, identity):
            return True
        if card.get("set"):
            return False   # another hero's cards
        aspect = card.get("aspect")
        if aspect == "basic":
            return True
        if aspect == "hero":
            return False
        if self._aspect_count(identity) >= len(ASPECTS) - 1:
            return aspect in ASPECTS
        chosen = self._chosen_aspects(deck, identity)
        if not chosen or aspect in chosen:
            return True    # nothing picked yet: every aspect is open
        return None

    # ----- building ---------------------------------------------------------

    def start_deck(self, deck: Deck, pool: Pool) -> None:
        deck.meta.setdefault("print", "deck")
        for card in self._hero_set(self._identity(deck, pool), pool):
            if not deck.count(card.id):
                deck.set_count(card.id, int(card.get("quantity") or 1))

    def selections(self, deck: Deck, pool: Pool) -> list[Selection]:
        identity = self._identity(deck, pool)
        out = []
        wanted = self._aspect_count(identity)
        if identity is not None and wanted < len(ASPECTS) - 1:
            choices = [("", "— choose —")] + [(a, ASPECT_NAMES[a]) for a in ASPECTS]
            for i, key in enumerate(["aspect", "aspect2"][:wanted]):
                out.append(Selection(key, "Aspect" if wanted == 1 else f"Aspect {i + 1}",
                                     choices, deck.meta.get(key) or ""))
        out.append(Selection("print", "Print", PRINT_CHOICES, deck.meta.get("print") or "deck"))
        return out

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        identity = self._identity(deck, pool)
        base = self._base_legal(card, deck, identity)
        if base is not None:
            return base
        options = (identity.raw.get("deck_options") or []) if identity else []
        return any(self.option_matches(o, card) for o in options)

    def max_copies(self, card: DeckCard, deck: Deck) -> int:
        """Hero cards come in their printed quantity; everything else up to
        its deck_limit (Adam Warlock's one-copy rule is reported by validate)."""
        if card.get("aspect") == "hero":
            return int(card.get("quantity") or 1)
        return int(card.get("deck_limit") or DEFAULT_COPIES)

    # ----- validation -------------------------------------------------------

    def _deck_cards(self, deck: Deck, pool: Pool) -> list[tuple[DeckCard, int]]:
        return [(c, q) for cid, q in deck.slots.items() if (c := pool.get(cid)) and c.get("kind") in DECK_TYPES]

    def _illegal(self, deck: Deck, pool: Pool, identity: DeckCard) -> tuple[list[str], list[Problem]]:
        """Cards not allowed, after offering the rest to the hero's
        deck_options in order, with their limits (as MarvelCDB does). Returns
        (ids of cards nothing allows, problems for cards over an option's limit)."""
        options = identity.raw.get("deck_options") or []
        used = [0] * len(options)
        illegal, over = [], []
        for card, qty in sorted(self._deck_cards(deck, pool), key=lambda t: t[0].id):
            base = self._base_legal(card, deck, identity)
            if base is True:
                continue
            if base is False:
                illegal.append(card.id)
                continue
            placed, blocked = False, None
            for i, option in enumerate(options):
                if not self.option_matches(option, card):
                    continue
                if option.get("use_deck_limit") and option.get("name_limit"):
                    # Counts different cards, not copies (Maria Hill's S.H.I.E.L.D. supports).
                    limit, need = option["name_limit"], 1
                else:
                    limit, need = option.get("limit"), qty
                if limit is not None and used[i] + need > limit:
                    blocked = blocked or limit
                    continue
                used[i] += need
                placed = True
                break
            if placed:
                continue
            if blocked is not None:
                over.append(Problem(f"{card.name} goes over {identity.raw['name']}'s limit of {blocked} "
                                    "cards from outside the aspect", card_ids=[card.id]))
            else:
                illegal.append(card.id)
        return illegal, over

    def validate(self, deck: Deck, pool: Pool) -> list[Problem]:
        identity = self._identity(deck, pool)
        if identity is None:
            return [Problem("Choose a hero")]
        problems = []
        unknown = [cid for cid in deck.slots if pool.get(cid) is None]
        if unknown:
            problems.append(Problem(f"Unknown card codes: {', '.join(unknown)}", card_ids=unknown))

        size = self._size(deck, pool)
        if not MIN_DECK <= size <= MAX_DECK:
            problems.append(Problem(f"Deck has {size} cards; it needs {MIN_DECK} to {MAX_DECK}"))

        missing = [c for c in self._hero_set(identity, pool) if deck.count(c.id) < int(c.get("quantity") or 1)]
        if missing:
            problems.append(Problem("Missing hero cards: " + ", ".join(sorted(c.name for c in missing)),
                                    card_ids=[c.id for c in missing]))

        illegal, over = self._illegal(deck, pool, identity)
        for card_id in illegal:
            problems.append(Problem(f"{pool.get(card_id).name} isn't allowed for this deck", card_ids=[card_id]))
        problems += over

        # Copy limits per card (reprints are one card here). Different cards
        # that share a name, like the Peter Parker and Miles Morales
        # Spider-Man allies, each have their own limit, as on MarvelCDB.
        for card, qty in self._deck_cards(deck, pool):
            if not self._own(card, identity) and qty > self.max_copies(card, deck):
                problems.append(Problem(f"{qty} copies of {card.name} (limit {self.max_copies(card, deck)})",
                                        card_ids=[card.id]))

        reqs = self._requirements(identity)
        wanted = self._aspect_count(identity)
        counts = self._aspect_counts(deck, pool)
        if wanted == 1 and not self._chosen_aspects(deck, identity):
            problems.append(Problem("Choose an aspect", severity="warning"))
        elif wanted == 2:
            chosen = self._chosen_aspects(deck, identity)
            if len(chosen) < 2 or chosen[0] == chosen[1]:
                problems.append(Problem(f"Choose two different aspects for {identity.raw['name']}"))
            elif counts.get(chosen[0], 0) != counts.get(chosen[1], 0):
                problems.append(Problem(
                    f"{identity.raw['name']} needs equal numbers of each aspect "
                    f"({ASPECT_NAMES[chosen[0]]} {counts.get(chosen[0], 0)}, "
                    f"{ASPECT_NAMES[chosen[1]]} {counts.get(chosen[1], 0)})"))
        elif wanted > 2:
            present = {a: n for a, n in counts.items() if n}
            if present and (len(present) != wanted or len(set(present.values())) != 1):
                problems.append(Problem(f"{identity.raw['name']} needs cards from {wanted} aspects in equal numbers ("
                                        + ", ".join(f"{ASPECT_NAMES[a]} {n}" for a, n in present.items()) + ")"))
        if reqs.get("limit"):
            off = [c for c, q in self._deck_cards(deck, pool) if not self._own(c, identity) and q != reqs["limit"]]
            if off:
                problems.append(Problem(f"{identity.raw['name']} needs exactly {reqs['limit']} "
                                        f"cop{'y' if reqs['limit'] == 1 else 'ies'} of each card",
                                        card_ids=[c.id for c in off]))
        return problems

    def _size(self, deck: Deck, pool: Pool) -> int:
        """Cards that count toward 40-50: permanents don't (MarvelCDB)."""
        return sum(q for c, q in self._deck_cards(deck, pool) if not c.raw.get("permanent"))

    def _aspect_counts(self, deck: Deck, pool: Pool) -> dict[str, int]:
        counts = {a: 0 for a in ASPECTS}
        for card, qty in self._deck_cards(deck, pool):
            if card.get("aspect") in counts:
                counts[card.get("aspect")] += qty
        return counts

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        size = self._size(deck, pool)
        cards = self._deck_cards(deck, pool)
        identity = self._identity(deck, pool)
        hero = sum(q for c, q in cards if self._own(c, identity))
        resources = {r: 0 for r in RESOURCES}
        costs = []
        for card, qty in cards:
            for r in card.get("resources"):
                resources[r] += qty
            if card.get("kind") != "resource" and isinstance(card.get("cost"), int):
                costs += [card.get("cost")] * qty
        aspects = {a: n for a, n in self._aspect_counts(deck, pool).items() if n}
        basic = sum(q for c, q in cards if c.get("aspect") == "basic")
        return [
            Stat("Cards", f"{size} ({MIN_DECK}–{MAX_DECK})", MIN_DECK <= size <= MAX_DECK),
            Stat("Hero", str(hero), title="Cards from the hero's own set"),
            Stat("Aspect", " / ".join(f"{ASPECT_NAMES[a]} {n}" for a, n in aspects.items()) or "0"),
            Stat("Basic", str(basic)),
            Stat("Avg cost", f"{sum(costs) / len(costs):.1f}" if costs else "–",
                 title="Average printed cost of non-resource cards"),
            Stat("Resources", " ".join(f"{r[0].upper()}{n}" for r, n in resources.items() if n) or "0",
                 title="Resource icons: " + ", ".join(f"{r} {n}" for r, n in resources.items())),
        ]

    # ----- display ----------------------------------------------------------

    def detail_fields(self, card: DeckCard) -> list[tuple[str, str]]:
        raw = card.raw
        stats = " / ".join(f"{k.upper()} {raw[k]}" for k in ("thwart", "attack", "defense", "recover")
                           if raw.get(k) is not None)
        fields = [
            ("Type", (raw.get("type_code") or "").replace("_", " ").title()),
            ("Aspect", ASPECT_NAMES.get(card.get("aspect"), (card.get("aspect") or "").title())),
            ("Cost", "" if raw.get("cost") is None else str(raw["cost"])),
            ("Traits", raw.get("traits") or ""),
            ("Resources", " ".join(r.title() for r in card.get("resources"))),
            ("Stats", stats),
            ("Health", str(raw["health"]) if raw.get("health") is not None else ""),
            ("Hand size", str(raw["hand_size"]) if raw.get("hand_size") is not None else ""),
            ("Deck limit", str(raw.get("deck_limit") or "")),
            ("Pack", f"{raw.get('pack_name') or raw.get('pack_code')} #{raw.get('position')}"),
            ("Code", card.id),
        ]
        return [(k, v) for k, v in fields if v]

    def subtitle(self, card: DeckCard) -> str:
        return card.get("traits") or ""

    def group_of(self, card: DeckCard) -> str:
        """The hero's own cards sit together, above the aspect and basic cards."""
        return "hero" if card.get("aspect") == "hero" else super().group_of(card)

    # ----- printing -----------------------------------------------------------

    def print_list(self, deck: Deck) -> list[tuple[str, int]]:
        """The hero and the deck, plus the obligation and nemesis set when
        deck.meta["print"] asks for them (they're needed to play)."""
        out = super().print_list(deck)
        if deck.meta.get("print") == "encounter" and deck.identity:
            from tcgnecro.deckbuilder import registry   # the pool, for the hero's sets
            from tcgnecro import db
            conn = db.connect()
            try:
                pool = registry.pool(conn, self.game_id)
            finally:
                conn.close()
            identity = pool.get(deck.identity)
            if identity is not None:
                hero_set = identity.get("set")
                for card in sorted(pool.cards.values(), key=lambda c: c.id):
                    if card.get("kind") == "encounter" and card.get("set") in (hero_set, f"{hero_set}_nemesis"):
                        out.append((card.id, int(card.get("quantity") or 1)))
        return out

    # ----- import / sync (MarvelCDB, same API as ArkhamDB) ---------------------

    def sync_link(self, source: str) -> dict | None:
        source = (source or "").strip()
        if m := re.search(r"marvelcdb\.com/decklist/(?:view/)?(\d+)", source):
            kind, remote_id = "decklist", m.group(1)
        elif m := re.search(r"marvelcdb\.com/deck/(?:view/)?(\d+)", source):
            kind, remote_id = "deck", m.group(1)
        elif source.isdigit():
            kind, remote_id = "auto", source
        else:
            return None
        return {"provider": "marvelcdb", "provider_name": "MarvelCDB", "kind": kind, "remote_id": remote_id}

    def fetch_remote(self, link: dict, pool: Pool) -> RemoteDeck:
        kind, remote_id = link["kind"], str(link["remote_id"])
        if kind == "auto":
            try:
                data, kind = self._fetch("decklist", remote_id), "decklist"
            except ValueError:
                data, kind = self._fetch("deck", remote_id), "deck"
        else:
            data = self._fetch(kind, remote_id)
        for _ in range(100):             # follow newer versions, if the site links any
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
                raise ValueError(f"MarvelCDB {kind} {remote_id} isn't readable (private or deleted?)") from None
            raise
        if not isinstance(data, dict) or "slots" not in data:
            raise ValueError(f"MarvelCDB {kind} {remote_id} isn't readable (private or deleted?)")
        return data

    def _remote_from(self, data: dict, kind: str, pool: Pool) -> RemoteDeck:
        hero = data.get("hero_code")

        def normalize(slots) -> dict[str, int]:
            out: dict[str, int] = {}
            for code, qty in (slots or {}).items() if isinstance(slots, dict) else []:
                cid = pool.resolve(code)
                if cid != hero:
                    out[cid] = out.get(cid, 0) + int(qty)
            return out

        meta = data.get("meta") or {}
        if isinstance(meta, str):
            meta = json.loads(meta) if meta.strip() else {}
        meta = {k: v for k, v in meta.items() if k in ("aspect", "aspect2")}
        return RemoteDeck(
            name=data.get("name") or "Imported deck",
            identity=pool.resolve(hero) if hero else None,
            slots=normalize(data.get("slots")),
            side_slots=normalize(data.get("sideSlots")),
            meta=meta,
            remote_id=str(data["id"]),
            url=f"https://marvelcdb.com/{kind}/view/{data['id']}",
            kind=kind,
            version=str(data["version"]) if data.get("version") else None,
            updated=data.get("date_update"),
            notes=data.get("description_md") or "",
        )

    def import_deck(self, source: str, pool: Pool) -> Deck:
        """MarvelCDB decklist or deck URL (or bare id), as a one-off copy."""
        link = self.sync_link(source)
        if link is None:
            raise ValueError("Paste a MarvelCDB deck or decklist URL")
        remote = self.fetch_remote(link, pool)
        return Deck(id="", game=self.game_id, name=remote.name, identity=remote.identity,
                    slots=remote.slots, side_slots=remote.side_slots, meta=remote.meta | {"print": "deck"},
                    notes=remote.notes, source=source.strip())


PLUGIN = MarvelChampionsPlugin("marvel_champions", Path(__file__).with_name("deckbuilder.toml"))
