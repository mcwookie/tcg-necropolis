# Deck builder: how it works

The deck builder is a game-agnostic core (`tcgnecro/deckbuilder/`) plus one plugin per
game (`tcgnecro/games/<id>/deckbuilder.py` + `deckbuilder.toml`). The core handles
everything that is the same for every game. Each game describes:

- **in TOML:** what can be filtered and the filter defaults, colors, groupings,
  sort orders, and how card-text icons look
- **in Python:** which cards can be in a deck, what makes a deck legal, how the deck
  is started, and how decks are imported

Arkham Horror (`ahlcg`) is the first and so far only plugin.

## Data flow

```
ArkhamDB API ──► data/cache/ahlcg/*.json ──► games/ahlcg load_catalog()
                                                   │  Card(id, title, back_group, attrs=raw card)
                                                   ▼
                                          index.db  cards.attrs (JSON)
                                                   │
                         registry.pool() ◄─────────┘  rebuilt when the catalog or images change
                                │  plugin.build_pool(): DeckCard per deckable card + aliases
                                ▼                     + a thumbnail per card from `images`
            filters.apply(plugin, values, deck, pool) ──► card list
            plugin.validate(deck, pool)               ──► problems
            store.update(deck_id, change)             ──► data/decks/{id}.json
```

- **Card data** comes from the same catalog the proxy printer uses. Each `Card` carries
  `attrs`, the game's full card record, stored as JSON in `index.db`. Proxy printing
  ignores it.
- **The pool** is the set of cards that can go in decks, built by the plugin and cached
  in memory per game. It is rebuilt automatically when `tcgnecro catalog` reloads the
  game or `tcgnecro reindex` changes its images, even while the server is running.
- **Filtering runs in Python** over the pool (about 1,750 cards for Arkham, roughly 15 ms).
  That lets the "legal for my investigator" filter call the rules engine directly,
  instead of translating rules into SQL.
- **Decks are JSON files**, not database rows. They're user data: they survive deleting
  `index.db`, they're easy to back up, and they're readable without the app.

## Code map

| File | Role |
|---|---|
| `deckbuilder/model.py` | `Deck`, `DeckCard`, `Pool`, `Problem`, `Stat`, `Selection`, `Action` |
| `deckbuilder/config.py` | Pydantic models for `deckbuilder.toml` |
| `deckbuilder/plugin.py` | `DeckbuilderPlugin` base class and its default behavior |
| `deckbuilder/registry.py` | Discovers plugins, caches pools |
| `deckbuilder/filters.py` | Parses filter query params, applies filters and sorts |
| `deckbuilder/markup.py` | Card text → safe HTML (escape, allow-list, `[icons]`, `[[Traits]]`) |
| `deckbuilder/store.py` | Deck JSON files: load/save/update/list/delete |
| `deckbuilder/routes.py` | FastAPI routes under `/decks` |
| `deckbuilder/templates/` | Jinja templates; `_*.html` are HTMX fragments |
| `static/app.css`, `static/htmx.min.js` | Styles; htmx 2.0.4 vendored, so no CDN is needed |

## The plugin API

Subclass `DeckbuilderPlugin` and create one instance named `PLUGIN`:

```python
PLUGIN = MyGamePlugin("mygame", Path(__file__).with_name("deckbuilder.toml"))
```

Only `build_card` is required. Every other hook has a default that gives a plain
"any cards, up to N copies" builder, so a game can start minimal.

| Hook | Default | Purpose |
|---|---|---|
| `build_card(id, title, raw) -> DeckCard \| None` | **required** | Map one catalog card to a DeckCard, or None if it can never be in a deck. Fill `attrs` with the keys your TOML refers to |
| `build_pool(rows) -> Pool` | calls `build_card` for each row | Override to add `pool.aliases` (e.g. reprint code → original) |
| `identities(pool)` | `[]` | Cards a deck is built around (investigators, heroes...) |
| `start_deck(deck, pool)` | nothing | Called once on creation: add required cards, default choices |
| `selections(deck, pool) -> [Selection]` | `[]` | Choices shown as dropdowns; stored in `deck.meta[key]` |
| `is_legal(card, deck, pool)` | `True` | Drives the "legal" toggle filter; ignore per-option limits here |
| `toggle_filter(key, card, deck, pool)` | `"legal"` → `is_legal` | Meaning of each `type = "toggle"` filter |
| `max_copies(card, deck)` | `attrs.deck_limit` or `default_copies` | Caps the + button |
| `validate(deck, pool) -> [Problem]` | unknown cards, copy limits | Everything wrong with the deck |
| `stats(deck, pool) -> [Stat]` | card count | Summary figures in the deck panel |
| `actions(deck, pool)` / `run_action(key, deck, pool)` | none | Extra buttons, e.g. "Draw random basic weakness" |
| `group_of(card)` | `attrs[group_field]` | Deck list heading |
| `row_color`, `subtitle`, `badges` | from TOML | Card row appearance |
| `render_text(text)` | `markup.render` with TOML icons | Card text HTML |
| `detail_fields(card)` | `[]` | (label, value) rows in the card dialog |
| `import_deck(source, pool) -> Deck` | not supported | Build a deck from a URL or text |
| `print_list(deck)` | identity + slots | What "Print proxies" prints |

Rules for plugins:

- Keep no per-deck state on the plugin. Everything comes in through `deck` and `pool`.
- Mutate decks only inside `start_deck` / `run_action` (the routes save the result), or
  in import.
- `DeckCard.raw` is the untouched source record. `DeckCard.attrs` is the normalized view
  that filters, sorts and the templates read.

## Config reference (`deckbuilder.toml`)

Top-level keys:

| Key | Default | Meaning |
|---|---|---|
| `identity_label` | `"Identity"` | What the deck is built around: "Investigator", "Hero"... |
| `identity_required` | `true` | Whether a deck must have one |
| `default_copies` | `3` | Copy limit when a card has no `deck_limit` |
| `color_field` | none | `attrs` key whose value picks a row color from `[colors]` (lists use their first value) |
| `subtitle_field` | none | `attrs` key shown under each card name |
| `group_field` | `"kind"` | `attrs` key the deck list is grouped by |
| `page_size` | `100` | Card rows per "Show more" |

Tables:

```toml
[colors]                 # value of color_field -> CSS color
guardian = "#2b80c5"

[[badges]]               # small figures at the right of each row, in order
field = "cost"           # attrs key; hidden when empty
title = "Cost"           # tooltip
format = "{}"            # Python format string

[[groups]]               # deck list headings, in this order; unlisted values go last
value = "asset"
label = "Assets"

[[filters]]              # the filter panel, top to bottom
key = "faction"          # query parameter name; must be unique
label = "Class"
type = "multiselect"     # multiselect | range | text | toggle
field = "factions"       # attrs key (value or list)
default = []             # used until the user changes a filter
options = [ { value = "guardian", label = "Guardian", color = "#2b80c5" } ]

[[filters]]
key = "level"
type = "range"           # card passes if min <= attrs[field] <= max
field = "xp"
min = 0
max = 5
default = [0, 5]
none_passes = true       # cards with no value pass

[[filters]]
key = "q"
type = "text"            # every word must appear in one of `fields`
fields = ["name", "traits", "text"]
help = "placeholder text"

[[filters]]
key = "legal"
type = "toggle"          # meaning comes from plugin.toggle_filter(key, ...)
default = true

[[sorts]]                # the first is the default
key = "class"
label = "Class, name"
fields = ["faction", "name"]   # prefix "-" for descending

[icons]                  # [token] in card text -> badge
action = { label = "➤", title = "Action", css = "extra-class" }
```

**Ownership:** a toggle filter with `key = "owned"` is handled by the core, not the plugin.
It keeps only cards the signed-in profile owns (from the Collection page; see
`tcgnecro/ownership.py`). It does nothing until a collection has been saved for the game,
and then it starts switched on regardless of `default`. Add it to any game's TOML to get
"Only cards I own". Ownership is computed from card *versions*, so cards reprinted in a
pack the profile owns count, and reprint codes resolve through `pool.aliases`.

How filters reach the server: the form sends each filter as query parameters (`faction=a&faction=b`,
`level_min=0&level_max=2`, `q=text`, `legal=1`) plus `f=1`. Without `f=1` (first load,
"Reset filters") the TOML defaults apply. With it, an unchecked box means off.

## Deck file format

`data/decks/{id}.json`:

```json
{
  "format": 2,
  "id": "0d9269855fcb",
  "game": "ahlcg",
  "name": "Zoey - Dunwich",
  "identity": "02001",
  "slots": {"01020": 2, "02006": 1},
  "side_slots": {},
  "meta": {"faction_selected": "seeker", "taboo_id": 8},
  "notes": "",
  "source": "https://arkhamdb.com/decklist/view/55482/x",
  "owner": "3f2a9c41d0be",
  "visibility": "private",
  "created": "2026-10-11T01:50:00+00:00",
  "updated": "2026-10-11T01:52:13+00:00"
}
```

`owner` is a profile id from `data/users.json`, and `visibility` is `private` or `shared`
(see [ACCOUNTS.md](ACCOUNTS.md)). Format-1 decks, saved before profiles existed, have
neither field and load as unowned and private.

Card ids are the game's catalog ids (ArkhamDB codes for Arkham). `meta` keys for Arkham
match ArkhamDB's (`faction_selected`, `faction_1`/`faction_2`, `option_selected`,
`deck_size_selected`), so imported choices carry over.

If the layout ever changes, bump `DECK_FORMAT` in `model.py` and teach `Deck.from_json` to
read the old layout. The app refuses to load decks written by a newer version, rather
than silently dropping fields.

## How the page updates (HTMX)

| User action | Request | Response |
|---|---|---|
| Change any filter | `GET /decks/{id}/cards?...` | the card list; `HX-Push-Url` puts the filters in the address bar |
| +/− | `POST /decks/{id}/slots/{card}?delta=±1` | the deck panel, plus that card's list control (out-of-band) |
| Rename, notes, choices | `POST /decks/{id}/meta` | the deck panel; a choice change also sends `HX-Trigger: filtersChanged`, so the list re-checks legality |
| Action button | `POST /decks/{id}/action/{key}` | the deck panel with a message |
| Click a card name | `GET /decks/{id}/card/{card}` | card detail, opened in a `<dialog>` |

Every route loads its deck through `_load(request, deck_id, edit=...)`, which applies
the ownership rules. A deck you can't see returns 404, and editing one you can only view
returns 403. When viewing someone else's shared deck, the builder renders a read-only
layout (the template variable `can_edit`).

All edits go through `store.update()`, which loads, modifies and saves under a lock. Each
edit is small and applied to the latest saved state, so two tabs on the same deck don't
overwrite each other's work. The page doesn't live-update across tabs, though; reload to
see changes made elsewhere.

## The Arkham rules engine (`games/ahlcg/deckbuilder.py`)

Arkham's deckbuilding rules are data on each investigator (`deck_options`,
`deck_requirements`). The plugin interprets that data the way ArkhamDB does, so new
investigators work without code changes.

1. **Exempt cards** are always allowed and don't count toward deck size: weaknesses,
   story cards from campaigns, the investigator's required signature cards (and their alternates), and cards
   restricted to this investigator.
2. **Each other copy** is offered to the deck options in order. The first option the
   card meets claims it. If that option's `limit` is used up, the next matching option
   is tried. A matching `not: true` option makes the card illegal. A copy that nothing
   claims is illegal. Order matters, and real data relies on it: parallel Rex Murphy
   allows Cursed cards before banning Fortune/Blessed.
3. **Separately checked:**
   - deck size, where permanents don't count, plus or minus the cards that change it
   - copy limits by English title across all levels (`deck_limit`, default 2)
   - required cards present
   - `atleast` rules ("7 cards from 3 classes")
   - random basic weaknesses (a warning, with a button to draw one)

Option criteria supported: `faction`, `faction_select`, `option_select`,
`deck_size_select`, `level`, `type`, `trait`, `slot`, `uses`, `text` (regex), `tag`,
`permanent`, `not`, `limit`, `atleast`, `ignore_match`.

**Cards in the deck that change deckbuilding:**

- Encoded as data: On Your Own carries its own `deck_options`, which are applied ahead
  of the investigator's.
- Text only, listed in `DECKBUILDING_CARDS`:

  | Card | Effect handled |
  |---|---|
  | Versatile | +5 deck size, plus one off-class level 0 card |
  | Ancestral Knowledge | +5 deck size, needs 10 skills |
  | Underworld Support | −5 deck size, 1 copy per title |
  | Forced Learning | +15 deck size |
  | Underworld Market | +10 deck size, needs 10 Illicit cards |

**Pool rules:**

- Scenario cards (encounter sets), bonded cards and reprints are excluded. The exception
  is encounter-set cards that a campaign puts into your deck: story weaknesses (The Man in
  the Pallid Mask) become kind `weakness`, and story assets, events and skills (Lita
  Chantler) become kind `story`. Both sit outside deckbuilding rules and deck size, and
  random weakness draws skip them.
- Reprint codes become aliases of the original (`duplicate_of_code`), so a deck listing
  `01520` (Revised Core Machete) lands on `01020`.
- ArkhamDB's `01000` "Random Basic Weakness" placeholder is dropped on import.

**XP:** exceptional cards cost double. Myriad cards are paid for once per title.

**Checked against real data:** five published ArkhamDB decklists import and validate
legal (Zoey, Ursula, Lucius, Jenny with Underworld Support, Joe Diamond with 40 cards).
Not yet handled: taboo lists, customizable upgrades, parallel investigator front/back
choices. See [ROADMAP.md](ROADMAP.md).

## Tests

`tests/test_deckbuilder.py` uses small synthetic Arkham cards, so it runs offline. It
covers:

- pool building and aliases
- legality: class/level, signatures, faction choice, `not`, limits
- deck size with permanents and Underworld Support
- copy limits by title, XP
- title disambiguation
- filter defaults vs. submitted values
- markup escaping
- deck storage, and migration of an old `index.db`
- a full create/edit/export/delete run through the web routes

Run everything with `.venv/bin/python -m pytest`.
