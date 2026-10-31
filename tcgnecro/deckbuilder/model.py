"""Plain data types shared by the deck builder core and game plugins."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# Bump when the deck JSON layout changes, and teach Deck.from_json to read
# the older layout. Every saved deck records the version it was written with.
# 2: added `owner` and `visibility`. Format-1 decks load with no owner; the
#    first profile created claims them (users.create -> store.assign_unowned).
# 3: added `sync`, the link to the deck's source site (deckbuilder/sync.py).
# 4: added `sections`, extra parts of a deck beyond `slots` for games that
#    have them (MECCG's location deck, sideboard and pool). Older decks load
#    with none.
DECK_FORMAT = 4

# Who can see a deck besides its owner.
VISIBILITIES = {
    "private": "Only me",
    "shared": "Everyone on this server (read-only)",
}


# The key for a deck's main part (`slots`) wherever sections are named.
MAIN = "main"


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
    # Clockwise degrees to show the card's front image upright (landscape
    # cards are stored sideways). Set by registry.pool from the game's rule.
    rotate: int = 0

    @property
    def landscape(self) -> bool:
        return self.rotate in (90, 270)

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
    `owner` is a profile id (users.py); `visibility` is a key of VISIBILITIES.
    `slots` is the main deck. `sections` holds any other parts of the deck a
    game has (plugin.sections()), keyed by section: {"location": {id: qty}}.
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
    owner: str | None = None
    visibility: str = "private"
    # Link to the site the deck came from, kept up to date by deckbuilder/sync.py.
    # None when the deck isn't synced.
    sync: dict | None = None
    sections: dict[str, dict[str, int]] = field(default_factory=dict)
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    format: int = DECK_FORMAT

    def section(self, key: str | None = None) -> dict[str, int]:
        """A section's {card id: copies}, for reading; None or "main" is `slots`."""
        if key in (None, "", MAIN):
            return self.slots
        return self.sections.get(key, {})

    def count(self, card_id: str, section: str | None = None) -> int:
        if section in (None, "", MAIN):
            return self.slots.get(card_id, 0)
        return self.sections.get(section, {}).get(card_id, 0)

    def set_count(self, card_id: str, qty: int, section: str | None = None) -> None:
        slots = self.slots if section in (None, "", MAIN) else self.sections.setdefault(section, {})
        if qty > 0:
            slots[card_id] = qty
        else:
            slots.pop(card_id, None)
        if section not in (None, "", MAIN) and not slots:
            self.sections.pop(section, None)

    def total(self, section: str | None = None) -> int:
        return sum(self.section(section).values())

    def all_counts(self) -> dict[str, int]:
        """Copies of each card across the main deck and every section."""
        out = dict(self.slots)
        for slots in self.sections.values():
            for card_id, qty in slots.items():
                out[card_id] = out.get(card_id, 0) + qty
        return out

    # Access rules. A deck with no owner (made before profiles existed and not
    # yet claimed) belongs to the admins. Admins get no other special access:
    # private decks stay private.
    def editable_by(self, user) -> bool:
        if user is None:
            return False
        return self.owner == user.id or (self.owner is None and user.is_admin)

    def viewable_by(self, user) -> bool:
        return self.editable_by(user) or (user is not None and self.visibility == "shared")

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
class RemoteDeck:
    """A deck as its source site currently has it (plugin.fetch_remote).
    `remote_id` is the *newest* version's id: sites like ArkhamDB make a new
    deck for each upgrade and link the versions, and plugins follow that chain."""

    name: str
    identity: str | None
    slots: dict[str, int]
    side_slots: dict[str, int]
    meta: dict
    remote_id: str
    url: str
    kind: str = "deck"           # site-specific: "deck", "decklist"...
    version: str | None = None   # the site's version label, e.g. "1.2"
    updated: str | None = None   # when the site last changed it
    notes: str = ""


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
    title: str | None = None  # tooltip with more detail


@dataclass
class Selection:
    """A choice the player makes for a deck (e.g. a secondary class). Shown
    as a dropdown; the chosen value is stored in deck.meta[key]."""

    key: str
    label: str
    options: list[tuple[str, str]]  # (value, label)
    value: str | None = None


@dataclass
class Section:
    """A part of a deck besides the main one (plugin.sections()), e.g. MECCG's
    location deck. Its cards are in deck.sections[key]."""

    key: str
    label: str
    help: str | None = None


@dataclass
class Action:
    """A game-specific button on the deck page (e.g. "Draw random basic
    weakness"). The plugin's run_action() handles it."""

    key: str
    label: str
