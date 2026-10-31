"""DeckbuilderPlugin: the base class a game subclasses to get a deck builder.

Only `build_card` must be implemented. Every other hook has a default that
gives a plain "pick any cards, up to N copies" builder, so a new game can
start minimal and grow rules over time. Hooks receive the deck and the pool
explicitly; plugins should keep no per-deck state of their own.
"""

from pathlib import Path

from markupsafe import Markup

from tcgnecro.deckbuilder import markup
from tcgnecro.deckbuilder.config import GameConfig
from tcgnecro.deckbuilder.model import (MAIN, Action, Deck, DeckCard, Pool, Problem, RemoteDeck, Section,
                                       Selection, Stat)


class DeckbuilderPlugin:
    def __init__(self, game_id: str, config_path: Path):
        self.game_id = game_id
        self.config = GameConfig.load(config_path)

    # ----- card pool --------------------------------------------------------

    def build_card(self, card_id: str, title: str, raw: dict) -> DeckCard | None:
        """Turn one catalog card (its `attrs` from the catalog, as `raw`) into a
        DeckCard, or return None if it can never go in a deck (e.g. encounter
        cards). Fill DeckCard.attrs with whatever keys deckbuilder.toml uses."""
        raise NotImplementedError

    def build_pool(self, rows: list[tuple[str, str, dict]]) -> Pool:
        """Build the pool from (card_id, title, raw) rows. Override to add
        aliases (e.g. reprints that should count as their original)."""
        cards = {}
        for card_id, title, raw in rows:
            card = self.build_card(card_id, title, raw)
            if card:
                cards[card.id] = card
        return Pool(cards)

    def identities(self, pool: Pool) -> list[DeckCard]:
        """Cards a deck can be built around. Default: none."""
        return []

    # ----- building ---------------------------------------------------------

    def start_deck(self, deck: Deck, pool: Pool) -> None:
        """Called once when a deck is created with its identity set: add any
        cards the identity requires, set default selections, etc."""

    def selections(self, deck: Deck, pool: Pool) -> list[Selection]:
        """Choices the player makes for this deck (shown as dropdowns)."""
        return []

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        """Could this card go in this deck at all? Drives the "legal cards
        only" filter. Copy limits and per-option limits are not considered
        here; validate() reports those."""
        return True

    def toggle_filter(self, key: str, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        """Interpret a `type = "toggle"` filter that is switched on. The key
        "legal" is understood by default; override for game-specific toggles."""
        if key == "legal":
            return self.is_legal(card, deck, pool)
        return True

    def max_copies(self, card: DeckCard, deck: Deck) -> int:
        """Upper bound for the + button."""
        return int(card.get("deck_limit") or self.config.default_copies)

    # ----- sections (games whose decks have several parts) -------------------

    def sections(self) -> list[Section]:
        """Parts of a deck besides the main one, e.g. MECCG's location deck,
        sideboard and pool. Default: none, so a deck is just its main part."""
        return []

    def main_label(self) -> str:
        """What the main part is called when a game has sections."""
        return "Deck"

    def allowed_in(self, card: DeckCard, section: str) -> bool:
        """Can this card go in this section at all (MAIN for the main part)?"""
        return True

    def section_limit(self, card: DeckCard, deck: Deck, section: str) -> int:
        """Upper bound for the + button in one section. Default: the card's
        max_copies, less any copies in the deck's other parts."""
        elsewhere = sum(q for cid, q in deck.all_counts().items() if cid == card.id) - deck.count(card.id, section)
        return max(0, self.max_copies(card, deck) - elsewhere)

    def validate(self, deck: Deck, pool: Pool) -> list[Problem]:
        problems = []
        for card_id, qty in deck.slots.items():
            card = pool.get(card_id)
            if card is None:
                problems.append(Problem(f"Unknown card {card_id}", card_ids=[card_id]))
            elif qty > self.max_copies(card, deck):
                problems.append(Problem(f"Too many copies of {card.name}", card_ids=[card_id]))
        return problems

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        return [Stat("Cards", str(deck.total()))]

    def actions(self, deck: Deck, pool: Pool) -> list[Action]:
        """Extra buttons on the deck page."""
        return []

    def run_action(self, key: str, deck: Deck, pool: Pool) -> str | None:
        """Handle an Action. Returns a message to show, or None."""
        raise KeyError(key)

    # ----- display ----------------------------------------------------------

    def group_of(self, card: DeckCard) -> str:
        """Which heading a card sits under in the deck list."""
        return str(card.get(self.config.group_field) or "other")

    def row_color(self, card: DeckCard) -> str | None:
        field = self.config.color_field
        value = card.get(field) if field else None
        if isinstance(value, list):
            value = value[0] if value else None
        return self.config.colors.get(value) if value else None

    def subtitle(self, card: DeckCard) -> str:
        field = self.config.subtitle_field
        return str(card.get(field) or "") if field else ""

    def badges(self, card: DeckCard) -> list[tuple[str, str]]:
        """(text, tooltip) pairs shown on a card row."""
        out = []
        for badge in self.config.badges:
            value = card.get(badge.field)
            if value is not None and value != "":
                out.append((badge.format.format(value), badge.title))
        return out

    def render_text(self, text: str | None) -> Markup:
        return markup.render(text or "", self.config.icons)

    def detail_fields(self, card: DeckCard) -> list[tuple[str, str]]:
        """(label, value) pairs for the card detail view."""
        return []

    # ----- import / export --------------------------------------------------

    def import_deck(self, source: str, pool: Pool) -> Deck:
        """Build a Deck from a URL or pasted text. The returned deck needs no
        id; the caller assigns one."""
        raise NotImplementedError(f"Importing decks isn't supported for {self.game_id} yet")

    # ----- sync with the source site (deckbuilder/sync.py) -------------------

    def sync_link(self, source: str) -> dict | None:
        """If `source` (an import URL) can be kept in sync, the link to store:
        at least {"provider", "provider_name", "kind", "remote_id"}. None
        means decks from this source can only be imported once."""
        return None

    def fetch_remote(self, link: dict, pool: Pool) -> RemoteDeck:
        """The newest version of a linked deck, following the site's upgrade
        chain. Raise ValueError with a readable message for decks that can't
        be read (private, deleted)."""
        raise NotImplementedError(f"Syncing decks isn't supported for {self.game_id}")

    def print_list(self, deck: Deck) -> list[tuple[str, int]]:
        """(card_id, copies) to print as proxies. Default: identity + slots."""
        out = [(deck.identity, 1)] if deck.identity else []
        return out + list(deck.slots.items())
