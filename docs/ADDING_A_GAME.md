# Adding a game

Supporting a game happens in two independent steps: **proxies** (catalog + images) and,
optionally, a **deck builder**. Do the first, use it, then add the second.

## Step 1: proxy printing

### The game package

Create `tcgnecro/games/<id>/__init__.py`. The id is lowercase, with no spaces, and it
must match the `game` in the game's collection manifests (and upstream Proxy Nexus's
id, if the game exists there, so archives interchange).

```python
"""Marvel Champions. Card data from MarvelCDB."""

from tcgnecro.games import Card, CardVersion, Catalog, Pack

GAME_ID = "marvel_champions"
NAME = "Marvel Champions"


def load_catalog(refresh: bool = False) -> Catalog:
    catalog = Catalog(GAME_ID, NAME)
    # Pack(id, name, sort_key, group=None, group_order=0): sort_key orders packs;
    # newer must sort higher (a release date "2019-11-01" or zero-padded position
    # "07"). group/group_order arrange the Collection page (e.g. by cycle).
    # Card(id, title, back_group, attrs): title must be unique per card -- decklists
    #   resolve by it. back_group picks a generic back from backs/ (or None).
    #   attrs: the full card record if you'll add a deck builder, else None.
    # CardVersion(card_id, pack_id, position): one per printing of a card in a pack.
    return catalog
```

Look at `games/swlcg` (static JSON in the repo) and `games/ahlcg` (downloaded and cached
under `data/cache/<id>/`, honoring `refresh`) for the two common shapes. If upstream
Proxy Nexus already supports the game, port its `games/<id>/adapter.rs`: keeping the same
ids and titles means existing collections and decklists just work.

### Register it

Add the id to `GAME_MODULES` in `tcgnecro/games/__init__.py`.

### Optional: generic card backs

Put images in `tcgnecro/games/<id>/backs/`, named `{back_group}_{label}[.bleed].png`, e.g.
`player_original.bleed.png`. Double-sided PDFs use them for cards that have no back image
of their own. The label is what the "Generic card back" option lists.

### Try it

```bash
.venv/bin/tcgnecro catalog <id>
.venv/bin/tcgnecro import my-scans.zip      # or create data/collections/<id>/<name>/ by hand
.venv/bin/tcgnecro reindex
.venv/bin/tcgnecro serve
```

Images are named `{card id}@{pack id}.ext` (see docs/UPDATING_GAMES.md for the full
convention). After `reindex`, check that images matched a pack:

```bash
.venv/bin/python -c "
from tcgnecro import db; c = db.connect()
print(c.execute(\"select count(*), sum(pack_id is null) from images where game_id='<id>'\").fetchone())"
```

The second number counts images whose printing didn't match a catalog pack. They're
treated as variants (alt art, fan cards), which is fine if intended.

## Step 2: a deck builder

The catalog must fill `Card.attrs` with each card's full record; the deck builder reads
nothing else. Then add two files next to `__init__.py`.

### `deckbuilder.py`

```python
from pathlib import Path

from tcgnecro.deckbuilder.model import Deck, DeckCard, Pool, Problem, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin


class MarvelPlugin(DeckbuilderPlugin):

    def build_card(self, card_id, title, raw):
        if raw.get("type_code") in ("villain", "main_scheme"):    # never in a deck
            return None
        attrs = {
            "name": raw["name"],
            "kind": raw["type_code"],             # used by group_field / filters
            "aspect": raw.get("faction_code"),
            "cost": raw.get("cost"),
            "traits": raw.get("traits") or "",
            "text": raw.get("text") or "",
            "deck_limit": raw.get("deck_limit"),
        }
        return DeckCard(card_id, title, raw["name"], attrs, raw)

    def identities(self, pool):
        return [c for c in pool.cards.values() if c.get("kind") == "hero"]

    def is_legal(self, card, deck, pool):
        return card.get("aspect") in ("basic", deck.meta.get("aspect", "leadership"))

    def validate(self, deck, pool):
        problems = super().validate(deck, pool)   # unknown cards + copy limits
        if not 40 <= deck.total() <= 50:
            problems.append(Problem(f"Deck has {deck.total()} cards; needs 40-50"))
        return problems


PLUGIN = MarvelPlugin("marvel_champions", Path(__file__).with_name("deckbuilder.toml"))
```

The full list of hooks, with what each default does, is in docs/DECKBUILDER.md ("The
plugin API"). Start with `build_card` + `identities` + a basic `validate`, use it, and
add rules as you hit them. `games/ahlcg/deckbuilder.py` is the fully worked example.

### `deckbuilder.toml`

```toml
identity_label = "Hero"
default_copies = 3
color_field = "aspect"
subtitle_field = "traits"
group_field = "kind"

[colors]
aggression = "#c0392b"
justice = "#d4ac0d"
leadership = "#2e86c1"
protection = "#28b463"
basic = "#888888"

[[badges]]
field = "cost"
title = "Cost"

[[filters]]
key = "legal"
label = "Only cards this deck can use"
type = "toggle"
default = true

[[filters]]
key = "q"
label = "Search"
type = "text"
fields = ["name", "traits", "text"]

[[filters]]
key = "aspect"
label = "Aspect"
type = "multiselect"
field = "aspect"
options = [
  { value = "aggression", label = "Aggression", color = "#c0392b" },
  { value = "basic", label = "Basic" },
]

[[sorts]]
key = "name"
label = "Name"
fields = ["name"]
```

Every key is documented in docs/DECKBUILDER.md ("Config reference").

### Try it and test it

The game appears under **Decks → New deck** as soon as `deckbuilder.py` exists and its
catalog is loaded. Add tests modeled on `tests/test_deckbuilder.py`: build a pool from a
few synthetic cards and assert on `is_legal` / `validate`. They run offline and pin the
rules down.

### Checklist

- [ ] `games/<id>/__init__.py` with `GAME_ID`, `NAME`, `load_catalog(refresh=False)`
- [ ] id added to `GAME_MODULES`
- [ ] titles unique per card; pack `sort_key` orders newest highest
- [ ] backs in `games/<id>/backs/` (optional)
- [ ] images import and match packs
- [ ] (deck builder) `Card.attrs` populated; `deckbuilder.py` with `PLUGIN`; `deckbuilder.toml`
- [ ] tests; a row in the README's game table
