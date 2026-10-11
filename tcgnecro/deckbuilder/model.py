"""Plain data types shared by the deck builder core and game plugins."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Bump when the deck JSON layout changes, and teach Deck.from_json to read
# the older layout. Every saved deck records the version it was written with.
DECK_FORMAT = 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DeckCard:
    """A card as the deck builder sees it.

    `attrs` holds the normalized fields that filters, sorting and grouping
    read (the game's deckbuilder.toml refers to these keys). `raw` is the
    card's full source data, for game logic that needs more.
    """

    id: str
    title: str   # catalog title, unique within the game (used for printing)
    name: str    # what to show in lists
    attrs: dict
    raw: dict

    def get(self, key, default=None):
        return self.attrs.get(key, default)


@dataclass
class Pool:
    """Every card that can go in a deck for one game, plus lookups."""

    cards: dict[str, DeckCard]
    # Alternate ids -> the pool id that stands for them (e.g. a reprint's
    # code -> the original's). Imported decks are normalized through this.
    aliases: dict[str, str] = field(default_factory=dict)
    # card id -> path of a front image in some collection, for thumbnails
    images: dict[str, str] = field(default_factory=dict)

    def resolve(self, card_id: str) -> str:
        return self.aliases.get(card_id, card_id)

    def get(self, card_id: str) -> DeckCard | None:
        return self.cards.get(self.resolve(card_id))


@dataclass
class Deck:
    """A saved deck. Stored as JSON; see store.py.

    `identity` is the card the deck is built around (an Arkham investigator,
    a Marvel hero, a Netrunner identity), or None for games without one.
    `meta` holds game-specific choices made while building (e.g. an Arkham
    investigator's secondary class), keyed however the plugin likes.
    """

    id: str
    game: str
    name: str
    identity: str | None = None
    slots: dict[str, int] = field(default_factory=dict)
    side_slots: dict[str, int] = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    notes: str = ""
    source: str | None = None   # where it was imported from, if anywhere
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    format: int = DECK_FORMAT

    def count(self, card_id: str) -> int:
        return self.slots.get(card_id, 0)

    def set_count(self, card_id: str, qty: int) -> None:
        if qty > 0:
            self.slots[card_id] = qty
        else:
            self.slots.pop(card_id, None)

    def total(self) -> int:
        return sum(self.slots.values())

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Deck":
        version = data.get("format", 1)
        if version > DECK_FORMAT:
            raise ValueError(f"Deck {data.get('id')} was saved by a newer version (format {version})")
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Problem:
    """Something wrong with a deck. `severity` is "error" (the deck is not
    legal) or "warning" (legal, but probably not what you want)."""

    message: str
    severity: str = "error"
    card_ids: list[str] = field(default_factory=list)


@dataclass
class Stat:
    """One figure in the deck summary, e.g. ("Cards", "30 / 30", ok=True)."""

    label: str
    value: str
    ok: bool | None = None  # None: neutral, True: good, False: needs attention


@dataclass
class Selection:
    """A choice the player makes for a deck (e.g. a secondary class). Shown
    as a dropdown; the chosen value is stored in deck.meta[key]."""

    key: str
    label: str
    options: list[tuple[str, str]]  # (value, label)
    value: str | None = None


@dataclass
class Action:
    """A game-specific button on the deck page (e.g. "Draw random basic
    weakness"). The plugin's run_action() handles it."""

    key: str
    label: str
