"""Applies a game's configured filters and sorts to its card pool.

Filtering runs in Python over the in-memory pool. A few thousand cards is
milliseconds, and it lets filters call into game logic (legality) without
translating rules into SQL.

Filter values arrive as query parameters named after each filter's key:
`faction=guardian&faction=seeker&level_min=0&level_max=2&q=machete&legal=1`.
The web form always sends `f=1`. Without it (first page load) the defaults
from deckbuilder.toml apply; with it, an absent checkbox means "off".

One toggle is handled here rather than by the game plugin: `owned` ("Only
cards I own"), since pack ownership is the same for every game. It does
nothing until the profile has saved a collection for the game (owned=None).
"""

from collections.abc import Mapping

from tcgnecro.db import normalize_title
from tcgnecro.deckbuilder.config import FilterConfig, GameConfig
from tcgnecro.deckbuilder.model import Deck, DeckCard, Pool


def parse(config: GameConfig, params, overrides: dict | None = None) -> dict:
    """Query params (a starlette QueryParams/MultiDict, or a plain dict of
    lists) -> {filter key: value}. Values: list for multiselect, (lo, hi) for
    range, str for text, bool for toggle. `overrides` replace the TOML
    defaults on first load (e.g. turn on "owned" once a collection exists)."""
    getlist = params.getlist if hasattr(params, "getlist") else (lambda k: params.get(k, []))
    submitted = bool(getlist("f"))
    values = {}
    for f in config.filters:
        if f.type == "multiselect":
            values[f.key] = getlist(f.key) if submitted else list(f.default or [])
        elif f.type == "range":
            lo, hi = (f.default or [f.min, f.max]) if not submitted else (None, None)
            lo = _int(getlist(f"{f.key}_min"), lo if lo is not None else f.min)
            hi = _int(getlist(f"{f.key}_max"), hi if hi is not None else f.max)
            values[f.key] = (lo, hi)
        elif f.type == "text":
            values[f.key] = (getlist(f.key) or [""])[0] if submitted else (f.default or "")
        elif f.type == "toggle":
            values[f.key] = bool(getlist(f.key)) if submitted else bool(f.default)
    if not submitted:
        values.update({k: v for k, v in (overrides or {}).items() if k in values})
    return values


def _int(raw: list, fallback):
    try:
        return int(raw[0]) if raw and raw[0] != "" else fallback
    except ValueError:
        return fallback


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _passes(f: FilterConfig, value, card: DeckCard) -> bool:
    if f.type == "multiselect":
        if not value:
            return True
        return bool(set(_as_list(card.get(f.field))) & set(value))
    if f.type == "range":
        lo, hi = value
        v = card.get(f.field)
        if v is None:
            return f.none_passes
        return (lo is None or v >= lo) and (hi is None or v <= hi)
    if f.type == "text":
        words = normalize_title(value).split("_")
        words = [w for w in words if w]
        if not words:
            return True
        haystack = normalize_title(" ".join(str(card.get(k) or "") for k in f.fields))
        return all(w in haystack for w in words)
    return True


def toggle_passes(plugin, key: str, card: DeckCard, deck: Deck, pool: Pool,
                  owned: set[str] | None) -> bool:
    if key == "owned":
        return owned is None or card.id in owned
    return plugin.toggle_filter(key, card, deck, pool)


def apply(plugin, values: dict, deck: Deck, pool: Pool, owned: set[str] | None = None) -> list[DeckCard]:
    """Cards in the pool that pass every filter. `owned`: pool ids the
    profile owns, or None if they haven't set up a collection."""
    config = plugin.config
    active = [(f, values.get(f.key)) for f in config.filters if f.type != "toggle"]
    toggles = [f.key for f in config.filters if f.type == "toggle" and values.get(f.key)]
    out = []
    for card in pool.cards.values():
        if all(_passes(f, v, card) for f, v in active) and all(
            toggle_passes(plugin, key, card, deck, pool, owned) for key in toggles
        ):
            out.append(card)
    return out


def sort(cards: list[DeckCard], config: GameConfig, key: str | None) -> list[DeckCard]:
    choice = next((s for s in config.sorts if s.key == key), config.sorts[0] if config.sorts else None)
    fields = choice.fields if choice else ["name"]
    # Python's sort is stable, so sorting by each field from last to first
    # gives a multi-key sort with per-field direction.
    out = list(cards)
    for field in reversed(fields):
        desc = field.startswith("-")
        name = field.lstrip("-")
        out.sort(key=lambda c: _sort_value(c.get(name)), reverse=desc)
    return out


def _sort_value(v):
    # Missing values sort last; lists sort by their first element.
    if isinstance(v, list):
        v = v[0] if v else None
    if v is None:
        return (2, "")
    if isinstance(v, (int, float)):
        return (0, v)
    # Compare text the way search does, so '"Eat lead!"' sorts under E.
    return (1, normalize_title(str(v)).strip("_"))


def query_string(config: GameConfig, values: Mapping) -> list[tuple[str, str]]:
    """Inverse of parse(), for building "load more" links."""
    out = [("f", "1")]
    for f in config.filters:
        v = values.get(f.key)
        if f.type == "multiselect":
            out += [(f.key, x) for x in v or []]
        elif f.type == "range":
            lo, hi = v
            out += [(f"{f.key}_min", "" if lo is None else str(lo)), (f"{f.key}_max", "" if hi is None else str(hi))]
        elif f.type == "text" and v:
            out.append((f.key, v))
        elif f.type == "toggle" and v:
            out.append((f.key, "1"))
    return out
