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
- **Card images are shown upright.** `registry.pool` sets each `DeckCard.rotate` from the
  game's `display_rotation` rule. Templates pass it to `thumb_url` / `image_url`, and the
  server returns turned images (cached in `data/thumbs/`).
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
| `related_cards(card, pool)` / `related_label()` | `[]` / "Comes with" | Cards shown with this one on hover and in its dialog (SWLCG: a set's command cards) |
| `import_deck(source, pool) -> Deck` | not supported | Build a deck from a URL or text (one-off copy) |
| `sync_link(source) -> dict \| None` | `None` | If an import URL can be kept in sync, the link to store (provider, kind, remote_id) |
| `fetch_remote(link, pool) -> RemoteDeck` | not supported | The newest version of a linked deck, following the site's upgrade chain |
| `print_list(deck)` | identity + slots | What "Print proxies" prints |
| `sections() -> [Section]` | `[]` | Parts of a deck besides the main one (MECCG's location deck, sideboard, pool). See "Deck sections" below |
| `main_label()` | `"Deck"` | Name of the main part when there are sections |
| `allowed_in(card, section)` | `True` | Whether a card can go in a part at all (`MAIN` is the main part) |
| `section_limit(card, deck, section)` | `max_copies` less copies in the other parts | Caps the + button in one part |

### Deck sections

Most games build one deck. A game whose decks have several parts lists the extra parts
in `sections()`; the main part stays `deck.slots` and is called `main_label()`, and each
extra part is `deck.sections[key]` (`Deck.count(id, section)`, `set_count(id, qty,
section)`, `total(section)`, `all_counts()`; `model.MAIN` names the main part).

On the page, an **Add to** choice appears above the filters; the card list's + and −
change that part, and the deck panel lists each part under its own heading with its own
controls. The +/− route takes `?section=` (and `?list_section=`, the part the list shows,
so its controls refresh correctly). The plugin decides what goes where (`allowed_in`),
the per-part limits (`section_limit`), and what prints (`print_list`).

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
  "format": 4,
  "id": "0d9269855fcb",
  "game": "ahlcg",
  "name": "Zoey - Dunwich",
  "identity": "02001",
  "slots": {"01020": 2, "02006": 1},
  "side_slots": {},
  "sections": {},
  "meta": {"faction_selected": "seeker", "taboo_id": 8},
  "notes": "",
  "source": "https://arkhamdb.com/decklist/view/55482/x",
  "owner": "3f2a9c41d0be",
  "visibility": "private",
  "sync": {"provider": "arkhamdb", "provider_name": "ArkhamDB", "kind": "deck",
           "remote_id": "6380828", "url": "https://arkhamdb.com/deck/view/6380828",
           "version": "0.1", "remote_updated": "2026-10-11T22:23:21+00:00",
           "fingerprint": "…", "synced_name": "Nathaniel Cho for Path to Carcosa",
           "status": "ok", "message": "", "last_checked": "…", "last_synced": "…"},
  "created": "2026-10-11T01:50:00+00:00",
  "updated": "2026-10-11T01:52:13+00:00"
}
```

`sections` holds the extra parts of a deck for games that have them (format 4; MECCG:
`{"location": {...}, "sideboard": {...}, "pool": {...}}`); older decks load with none.
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

## Deck sync (`deckbuilder/sync.py`)

Imports are linked to their source when the plugin's `sync_link` recognizes it, and
`sync_deck` keeps them up to date, one-way, from the site:

- **Rule 1: site changes are applied.** `fetch_remote` returns the newest version (a
  `RemoteDeck`). ArkhamDB makes a new deck per upgrade, linked by `next_deck`, and the
  plugin follows that chain. The local deck is updated in place: same id, owner, sharing
  and notes.
- **Rule 2: local edits are never silently overwritten.** `sync.fingerprint` is a hash of
  the deck's identity, slots, side slots and meta as last synced. If the deck no longer
  matches it, an unforced sync records `status: "local-changes"` and leaves the deck
  alone; `force=True` replaces it.
- **Rule 3: the name follows the site** unless the deck was renamed here
  (`synced_name`).
- **Errors are recorded, not raised:** private or deleted decks and network failures set
  `status: "error"` with a message, and the deck keeps its last synced version.

Syncs run from the deck page (`POST /decks/{id}/sync`, `?force=1`), `tcgnecro decks
sync`, and a background task in the web server (`sync_all` every
`TCGNECRO_SYNC_MINUTES`, default 60, first run two minutes after start-up). The
background task skips decks with local changes.

Format-2 decks load with `sync: null`. Decks imported before sync existed show a
"Keep in sync" button (`sync.can_link`), which links them on the first sync.

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

## The Marvel Champions rules (`games/marvel_champions/deckbuilder.py`)

Marvel's rules follow MarvelCDB's own deck validation (`app.deck.js` in its source), so a
deck that's legal there is legal here.

- **The hero is the identity.** Each hero is listed once, by its main code (`01001a`)
  and named with its alter ego ("Spider-Man (Peter Parker)"). Third forms (Ant-Man,
  Wasp and Archangel's `c` cards) aren't separate heroes.
- **The hero's own set** (its `card_set_code`, set type `hero`) is added at printed
  quantities when the deck is started, is always allowed, and is grouped as "Hero
  cards". "Hero special" set-aside cards aren't deck cards.
- **Never allowed:** encounter cards, other heroes' cards, and any other card that
  belongs to a set (campaign cards, Civil War leaders). **Always allowed:** basic cards.
- **Aspect** is a dropdown (`deck.meta["aspect"]`, as on MarvelCDB): Aggression,
  Justice, Leadership, Protection or 'Pool. Until one is picked every aspect is open,
  and validation warns.
- **Hero exceptions are data on the hero card:**

  | Data | Hero | Effect |
  |---|---|---|
  | `deck_requirements {"aspects": 2}` | Spider-Woman | two aspects (`aspect`, `aspect2`), equal counts |
  | `deck_requirements {"aspects": 4, "limit": 1}` | Adam Warlock | all four aspects in equal counts, exactly one copy of each card |
  | `deck_options [{type, trait, resource, limit}]` | Gamora, Cyclops, Cable, Wonder Man | extra cards from any aspect, offered in order with their limits |
  | `deck_options [{name_limit, use_deck_limit}]` | Maria Hill | up to N different S.H.I.E.L.D. supports |

- **Deck size** is 40–50, hero cards included and permanents not.
- **Copy limits** are per card: `deck_limit`, usually 3, or 1 for "Max 1 per deck".
  Reprints are one card, so the core *Energy* and a reprint share the limit. Different
  cards that share a name each have their own limit, like the Peter Parker and Miles
  Morales *Spider-Man* allies. MarvelCDB does the same.
- **Codes from MarvelCDB decks** are mapped: a reprint's code (`35024` → `01088`) and a
  two-sided card's back code (`34002b` → `34002a`) stand for the card. The catalog keeps
  each card's reprint codes as `attrs["reprints"]` for this.
- **Printing:** the deck's **Print** choice can add the hero's obligation and nemesis
  set (`<hero set>_nemesis`), which you need to play. They're in the pool as kind
  `encounter`, never legal in a deck.
- **Import and sync** use MarvelCDB's public API, which matches ArkhamDB's
  (`/api/public/decklist/N`, `/api/public/deck/N`; `hero_code`, `slots`, `meta` with the
  aspect). Sync works the same as for Arkham.

**Checked against real data:** 2,977 decklists published on MarvelCDB over 60 days.
2,947 validate legal, including every deck for the special heroes above. The other 30
break MarvelCDB's own rules too (32- to 92-card decks, off-aspect cards, a "Max 1" card
twice); MarvelCDB lets them be published anyway.

Not yet: MarvelCDB's "current" format (legacy packs excluded) and campaign-mode cards.

## The Star Wars LCG rules (`games/swlcg/deckbuilder.py`)

Decks are built from **objective sets**: an objective and the five command cards
printed with it, always together. So the builder's "cards" are sets: the pool holds one
entry per set (its objective, with the set's cards in `attrs["members"]`), and slots
count sets. Command cards are in the pool too (kind `command`, hidden from the list) only
so printing and image checks find them. **Print** expands each set into its six cards.

From the Core Set rulebook:

- The identity is an **affiliation** card (all three printings of each are offered);
  it fixes the side.
- **At least 10 sets**, all from that side. Any affiliation's sets are allowed unless the
  objective says "[affiliation] affiliation only" (`affiliation_lock`).
- **At most 2 copies** of a set, or 1 if it says "Limit 1 per objective deck"
  (`is_limited`).

Hovering a set shows the objective with its five cards; clicking it opens the set, every
card's image included (`plugin.related_cards`, a hook any game can use for cards that
come together). Objectives and missions are landscape cards, shown upright everywhere
(`display_rotation`); a scan already stored landscape is shown as is (`web._open`).

The sets are searchable by every card in them, and filterable by what they contain and
their Force icons. Balance of the Force's multiplayer objectives (outside numbered sets)
follow that expansion's own rules and aren't offered.

## The City of Heroes rules (`games/city_of_heroes/deckbuilder.py`)

From the Technical Rules (v0.545, rules 103.1 and 506.2):

- The identity is the **hero**. Its two **starting powers** are added when the deck is
  started.
- **At least 40 cards** besides the hero and one copy of each starting power; no maximum;
  **at most 3 copies** of a card. A deck has only one hero.
- **Warnings** (legal, but probably not wanted): another hero's **signature power**, and
  a power no super in the deck has the powerset for (the hero's and any sidekick's in the
  deck; "Common Pool" powers count as usable by anyone, which the rules don't spell out).

"Only cards my hero can take" hides other heroes and their signature powers; "Only powers
my supers can use" applies the powerset check.

## The Middle-earth CCG rules (`games/meccg/deckbuilder.py`)

From the Council of Elrond's Official Game Rules (deck construction, 1.3-1.7). A deck has
no identity card: **Playing as** (Wizard, Ringwraith, Fallen-wizard, Balrog) and **Game
length** are choices, and the deck has four parts (deck sections): the **play deck**
(main), **location deck**, **sideboard** and **pool**.

- **Play deck:** 30-50 resources and as many hazards (cards playable as either count as
  either), up to 10 non-avatar characters, up to 3 avatars (not three different ones), and
  at least 12 creatures, with agents-as-hazards, "Ahunt"/"at Home" Dragons, creatures also
  playable as events and Spawn permanent-events counting half.
- **Location deck:** sites only; any number of havens, one of each other site.
- **Sideboard:** at most 30, 35 or 40 cards by game length.
- **Pool:** up to 10 non-avatar characters and 2 non-unique, non-hoard minor items; for a
  Fallen-wizard, Stage resources worth exactly 3 stage points (at least one non-unique).
- **Across play deck, sideboard and pool:** one copy of a unique non-avatar card, three of
  a non-unique card (Fallen-wizard: two of characters and hero/minion resources), havens
  exempt; agents' total mind at most 36; avatar-specific cards need that avatar.
- **By alignment** (`_allowed`): which avatars, characters (Balrog: minion Orcs and Trolls,
  mind under 9), resources (hero/minion, the other side's items, Stage for Fallen-wizards,
  Balrog's faction races) and sites (including the five shared Balrog sites and the minion
  sites a Balrog needs its own version of) each may use, and the Fallen-wizard and Balrog
  banned lists. Agents count as hazards for Wizards and Balrogs, characters otherwise.

Not checked: the 10 extra sideboard cards against Fallen-wizard opponents, the
Fallen-wizard two-copies split for hazards played as resources, and the starting-company
draft (a game-time choice).

## Tests

`tests/test_swlcg_deckbuilder.py`, `tests/test_city_of_heroes_deckbuilder.py` and
`tests/test_meccg_deckbuilder.py` cover those games' rules the same way; the MECCG tests
also cover deck sections end to end through the web routes.

`tests/test_marvel_deckbuilder.py` does the same for Marvel Champions (hero sets,
aspects, Spider-Woman, Adam Warlock, Gamora's limit, MarvelCDB code mapping, printing
the obligation and nemesis, and the web page).

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
