"""Deck builder.

A game-agnostic core plus one plugin per game. The core owns everything that
is the same for every game:

- model.py     Deck, DeckCard, Problem, Stat, Selection, Pool
- config.py    the per-game TOML config (filters, colors, icons, groups...)
- plugin.py    DeckbuilderPlugin, the base class each game subclasses
- registry.py  finds plugins and caches each game's card pool
- filters.py   applies the configured filters and sorts to a card pool
- markup.py    turns card text into safe HTML with icons
- store.py     saves decks as JSON files under data/decks/
- routes.py    the web pages (FastAPI + HTMX)

A game opts in by adding `deckbuilder.py` (defining `PLUGIN`) and
`deckbuilder.toml` to its package under tcgnecro/games/<id>/. Everything that
differs between games -- what can be in a deck, what a legal deck looks like,
how cards are grouped, which filters exist and their defaults -- lives there.

See docs/DECKBUILDER.md for the design and docs/ADDING_A_GAME.md for a
walkthrough.
"""
