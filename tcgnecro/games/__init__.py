"""Game registry. Each game is a module exposing GAME_ID, NAME and
load_catalog(), with optional generic card backs in a `backs/` folder
named `{back_group}_{label}[.bleed].{ext}` (same as upstream)."""

from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path

GAME_MODULES = ["swlcg"]


@dataclass
class Pack:
    id: str
    name: str
    sort_key: str  # upstream calls this date_release; newer sorts higher


@dataclass
class Card:
    id: str
    title: str
    back_group: str | None


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
