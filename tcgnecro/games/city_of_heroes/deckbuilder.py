"""City of Heroes CCG deck builder rules.

From the game's Technical Rules (v0.545, rules 101-103 and 506):

- A deck contains one hero (the deck's identity) and a copy of each of that
  hero's starting powers, which are revealed and put into play to start the
  game (103.1).
- Constructed format (506.2): any legal cards, no more than 3 copies of a card,
  and at least 40 cards left after the hero and starting powers come out.
  There is no maximum.

What the rules leave to play rather than deckbuilding is reported as
warnings, since such a deck is legal but probably not what's wanted:

- A signature power "requires a certain named hero" (glossary), so another
  hero's signature power can't be used.
- A super attaches powers from its own powersets (413.1 implies it), so a power
  no super in the deck (the hero, or a sidekick in the deck) has the powerset
  for is dead weight. "Common Pool" powers are treated as usable by anyone.
"""

from pathlib import Path

from tcgnecro.deckbuilder.model import Deck, DeckCard, Pool, Problem, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin

MIN_DECK = 40
MAX_COPIES = 3
COMMON = "Common Pool"
POWER_KINDS = ("power", "sig power")


class CityOfHeroesPlugin(DeckbuilderPlugin):

    # ----- card pool --------------------------------------------------------

    def build_card(self, card_id: str, title: str, raw: dict) -> DeckCard | None:
        kind = raw.get("type")
        if not kind:
            return None
        attrs = {
            "name": raw["name"],
            "kind": kind,
            "inspiration": raw.get("inspiration") if raw.get("inspiration") not in (None, "N/A") else None,
            "powersets": raw.get("powersets") or [],
            "archetype": raw.get("archetype"),
            "recharge": raw.get("recharge") if isinstance(raw.get("recharge"), int) else None,
            "text": raw.get("text") or "",
            "prereq": raw.get("prereq") or "",
            "signature_hero": raw.get("signature_hero"),
            "rarity": raw.get("rarity"),
            "pack": raw.get("pack_code"),
        }
        return DeckCard(card_id, title, raw["name"], attrs, raw)

    def identities(self, pool: Pool) -> list[DeckCard]:
        return sorted((c for c in pool.cards.values() if c.get("kind") == "hero"), key=lambda c: c.name)

    # ----- helpers ----------------------------------------------------------

    def _identity(self, deck: Deck, pool: Pool) -> DeckCard | None:
        return pool.get(deck.identity) if deck.identity else None

    def _starting(self, identity: DeckCard | None, pool: Pool) -> list[DeckCard]:
        if identity is None:
            return []
        by_name = {c.name: c for c in pool.cards.values() if c.get("kind") in POWER_KINDS}
        return [by_name[n] for n in identity.raw.get("starting_powers") or [] if n in by_name]

    def _powersets(self, deck: Deck, pool: Pool) -> set[str]:
        """Powersets some super in the deck can use: the hero's and every
        sidekick's in the deck."""
        sets = set((self._identity(deck, pool) or DeckCard("", "", "", {}, {})).get("powersets") or [])
        for card_id in deck.slots:
            card = pool.get(card_id)
            if card and card.get("kind") == "sidekick":
                sets |= set(card.get("powersets"))
        return sets | {COMMON}

    def _usable(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        if card.get("kind") not in POWER_KINDS:
            return True
        return bool(set(card.get("powersets")) & self._powersets(deck, pool))

    def _counted(self, deck: Deck, pool: Pool) -> int:
        """Cards toward the 40: everything but one copy of each starting power."""
        starting = {c.id for c in self._starting(self._identity(deck, pool), pool)}
        return sum(q - (1 if cid in starting else 0) for cid, q in deck.slots.items() if pool.get(cid))

    # ----- building ---------------------------------------------------------

    def start_deck(self, deck: Deck, pool: Pool) -> None:
        for card in self._starting(self._identity(deck, pool), pool):
            if not deck.count(card.id):
                deck.set_count(card.id, 1)

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        identity = self._identity(deck, pool)
        if card.get("kind") == "hero":
            return False   # one hero per deck: the identity
        if card.get("kind") == "sig power":
            return identity is not None and card.get("signature_hero") == identity.name
        return True

    def toggle_filter(self, key: str, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        if key == "usable":
            return self._usable(card, deck, pool)
        return super().toggle_filter(key, card, deck, pool)

    def max_copies(self, card: DeckCard, deck: Deck) -> int:
        return MAX_COPIES

    def validate(self, deck: Deck, pool: Pool) -> list[Problem]:
        identity = self._identity(deck, pool)
        if identity is None:
            return [Problem("Choose a hero")]
        problems = []
        unknown = [cid for cid in deck.slots if pool.get(cid) is None]
        if unknown:
            problems.append(Problem(f"Unknown cards: {', '.join(unknown)}", card_ids=unknown))

        counted = self._counted(deck, pool)
        if counted < MIN_DECK:
            problems.append(Problem(f"Deck has {counted} cards besides {identity.name}'s starting powers; "
                                    f"it needs at least {MIN_DECK}"))
        missing = [c for c in self._starting(identity, pool) if not deck.count(c.id)]
        if missing:
            problems.append(Problem("Missing starting powers: " + ", ".join(c.name for c in missing),
                                    card_ids=[c.id for c in missing]))

        cards = [(c, q) for cid, q in sorted(deck.slots.items()) if (c := pool.get(cid))]
        for card, qty in cards:
            if card.get("kind") == "hero":
                problems.append(Problem(f"{card.name}: a deck has only one hero", card_ids=[card.id]))
            elif qty > MAX_COPIES:
                problems.append(Problem(f"{qty} copies of {card.name} (limit {MAX_COPIES})", card_ids=[card.id]))
        for card, _ in cards:
            if card.get("kind") == "sig power" and card.get("signature_hero") != identity.name:
                problems.append(Problem(f"{card.name} is {card.get('signature_hero')}'s signature power",
                                        severity="warning", card_ids=[card.id]))
            elif not self._usable(card, deck, pool):
                problems.append(Problem(f"No super in this deck has {card.name}'s powerset "
                                        f"({', '.join(card.get('powersets'))})", severity="warning",
                                        card_ids=[card.id]))
        return problems

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        counted = self._counted(deck, pool)
        cards = [(c, q) for cid, q in deck.slots.items() if (c := pool.get(cid))]
        kinds = {}
        for card, qty in cards:
            kinds[card.get("kind")] = kinds.get(card.get("kind"), 0) + qty
        inspirations = {}
        for card, qty in cards:
            if card.get("inspiration"):
                inspirations[card.get("inspiration")] = inspirations.get(card.get("inspiration"), 0) + qty
        return [
            Stat("Cards", f"{counted} / {MIN_DECK}+", counted >= MIN_DECK,
                 title="Besides the hero and one copy of each starting power"),
            Stat("Powers", str(kinds.get("power", 0) + kinds.get("sig power", 0))),
            Stat("Enhancements", str(kinds.get("enhancement", 0))),
            Stat("Edges", str(kinds.get("edge", 0))),
            Stat("Missions", str(kinds.get("mission", 0))),
            Stat("Sidekicks", str(kinds.get("sidekick", 0))),
            Stat("Inspirations", ", ".join(f"{k} {n}" for k, n in sorted(inspirations.items())) or "0"),
        ]

    # ----- display ----------------------------------------------------------

    def subtitle(self, card: DeckCard) -> str:
        if card.get("kind") in ("hero", "sidekick"):
            return f"{card.get('archetype') or ''} · {' / '.join(card.get('powersets'))}"
        if card.get("kind") in POWER_KINDS:
            return " / ".join(card.get("powersets")) + (f" · {card.get('prereq')}" if card.get("prereq") else "")
        return card.get("prereq") or ""

    def detail_fields(self, card: DeckCard) -> list[tuple[str, str]]:
        raw = card.raw
        stats = " / ".join(f"{label} {raw[k]}" for k, label in (("accuracy", "ACC"), ("defense", "DEF"),
                                                                 ("health", "HP")) if raw.get(k) is not None)
        fields = [
            ("Type", (raw.get("type") or "").title()),
            ("Archetype", raw.get("archetype") or ""),
            ("Powersets", " / ".join(raw.get("powersets") or [])),
            ("Starting powers", ", ".join(raw.get("starting_powers") or [])),
            ("Travel", raw.get("travel") or ""),
            ("Stats", stats),
            ("Recharge", str(raw["recharge"]) if raw.get("recharge") is not None else ""),
            ("Requires", raw.get("prereq") or ""),
            ("Inspiration", (raw.get("inspiration") or "").title()),
            ("Pack", f"{(raw.get('pack_code') or '').replace('_', ' ').title()} #{raw.get('number')}"),
        ]
        return [(k, v) for k, v in fields if v]


PLUGIN = CityOfHeroesPlugin("city_of_heroes", Path(__file__).with_name("deckbuilder.toml"))
