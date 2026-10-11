"""The per-game deck builder config, read from games/<id>/deckbuilder.toml.

Everything here is data a non-programmer could reasonably edit: which filters
exist and their defaults, sort orders, how the deck list is grouped, colors,
and how [tokens] in card text are drawn. Game *rules* live in Python, in the
game's deckbuilder.py.

docs/DECKBUILDER.md has an annotated reference for every field.
"""

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class FilterOption(BaseModel):
    value: str
    label: str
    color: str | None = None  # CSS color for a swatch next to the option


class FilterConfig(BaseModel):
    """One control in the filter panel.

    - multiselect: card passes if attrs[field] (a value or a list) contains any
      of the selected options. No selection means no filtering.
    - range: card passes if min <= attrs[field] <= max. Cards with no value
      pass only if `none_passes`.
    - text: card passes if every word typed appears in one of `fields`.
    - toggle: a checkbox the game's plugin interprets (plugin.toggle_filter).
    """

    key: str
    label: str
    type: Literal["multiselect", "range", "text", "toggle"]
    field: str | None = None
    fields: list[str] = Field(default_factory=list)
    options: list[FilterOption] = Field(default_factory=list)
    min: int | None = None
    max: int | None = None
    none_passes: bool = True
    default: Any = None
    help: str | None = None


class SortConfig(BaseModel):
    key: str
    label: str
    # attrs keys to sort by, in order. Prefix with "-" for descending.
    fields: list[str]


class GroupConfig(BaseModel):
    value: str
    label: str


class IconConfig(BaseModel):
    """How a [token] in card text is drawn: a short label in a styled badge."""

    label: str
    title: str | None = None  # tooltip; defaults to the token name
    css: str | None = None    # extra CSS class


class BadgeConfig(BaseModel):
    """A small figure shown on each card row, e.g. the cost."""

    field: str
    title: str
    format: str = "{}"


class GameConfig(BaseModel):
    identity_label: str = "Identity"   # "Investigator", "Hero", "Identity"...
    identity_required: bool = True
    default_copies: int = 3            # copy limit when a card doesn't say
    color_field: str | None = None     # attrs key whose value picks a row color
    colors: dict[str, str] = Field(default_factory=dict)
    subtitle_field: str | None = None  # attrs key shown under a card's name
    badges: list[BadgeConfig] = Field(default_factory=list)
    group_field: str = "kind"          # attrs key the deck list is grouped by
    groups: list[GroupConfig] = Field(default_factory=list)  # order + labels
    filters: list[FilterConfig] = Field(default_factory=list)
    sorts: list[SortConfig] = Field(default_factory=list)
    page_size: int = 100               # card rows per "load more"
    icons: dict[str, IconConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "GameConfig":
        return cls.model_validate(tomllib.loads(path.read_text()))

    def filter(self, key: str) -> FilterConfig | None:
        return next((f for f in self.filters if f.key == key), None)

    def group_label(self, value: str) -> str:
        return next((g.label for g in self.groups if g.value == value), value.title())

    def group_order(self, value: str) -> int:
        order = [g.value for g in self.groups]
        return order.index(value) if value in order else len(order)
