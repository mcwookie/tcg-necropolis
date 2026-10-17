"""Game registry.

Each game is a package under `tcgnecro/games/<id>/` exposing:

- `GAME_ID`, `NAME`
- `load_catalog(refresh: bool = False) -> Catalog`. `refresh` asks games that
  download their data to fetch it again instead of using the local cache.
- optionally a `backs/` folder of generic card backs, named
  `{back_group}_{label}[.bleed].{ext}` (same convention as upstream)
- optionally `display_rotation(attrs, side) -> int`: clockwise degrees
  (0/90/180/270) to turn a stored scan so it reads upright on screen. For
  landscape cards, which collections store sideways so they print in a
  portrait slot. Display only; PDFs always use the scan as stored.
- optionally a deck builder: `deckbuilder.py` defining `PLUGIN` plus a
  `deckbuilder.toml` config. See docs/DECKBUILDER.md.

See docs/ADDING_A_GAME.md for a walkthrough.
"""

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

GAME_MODULES = ["ahlcg", "meccg", "swlcg", "city_of_heroes"]


@dataclass
class Pack:
    id: str
    name: str
    sort_key: str  # upstream calls this date_release; newer sorts higher
    # Optional grouping for the Collection page ("The Dunwich Legacy",
    # "Standalone scenarios"); groups are shown in `group_order` order.
    group: str | None = None
    group_order: int = 0


@dataclass
class Card:
    id: str
    title: str  # unique per card within a game; decklists resolve against it
    back_group: str | None
    # Everything else the source knows about the card (text, cost, class...).
    # Only the deck builder reads this; proxy printing needs id + title alone.
    attrs: dict | None = None


@dataclass
class CardVersion:
    card_id: str
    pack_id: str
    position: int | None = None
    api_id: str | None = None  # a per-printing id, when the source has one


@dataclass
class Catalog:
    game_id: str
    name: str
    packs: list[Pack] = field(default_factory=list)
    cards: list[Card] = field(default_factory=list)
    versions: list[CardVersion] = field(default_factory=list)


@dataclass(frozen=True)
class CardBack:
    back_group: str
    label: str
    path: Path
    has_bleed: bool


def get_game(game_id: str):
    if game_id not in GAME_MODULES:
        raise KeyError(f"Unknown game '{game_id}'. Known: {', '.join(GAME_MODULES)}")
    return import_module(f"tcgnecro.games.{game_id}")


def display_rotation(game_id: str, attrs: dict | None, side: str = "front") -> int:
    """How far to turn a card's scan, clockwise, to show it the right way up.
    0 for games that don't declare a rule."""
    rule = getattr(get_game(game_id), "display_rotation", None)
    return int(rule(attrs, side)) % 360 if rule else 0


def card_backs(game_id: str) -> list[CardBack]:
    backs_dir = Path(get_game(game_id).__file__).parent / "backs"
    if not backs_dir.is_dir():
        return []
    backs = []
    for path in sorted(backs_dir.iterdir()):
        stem = path.stem
        has_bleed = stem.endswith(".bleed")
        if has_bleed:
            stem = stem[: -len(".bleed")]
        group, sep, label = stem.partition("_")
        if sep:
            backs.append(CardBack(group, label, path, has_bleed))
    return backs
