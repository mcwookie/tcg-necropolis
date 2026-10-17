# Using TCG Necropolis

## Starting the app

```bash
cd /path/to/tcgnecro
.venv/bin/tcgnecro serve                 # http://127.0.0.1:8000, this machine only
.venv/bin/tcgnecro serve --host 0.0.0.0  # reachable from your network
.venv/bin/tcgnecro serve --reload        # restart on code changes (development)
```

The top bar switches between **Proxies** and **Decks**. Your profile name (top right)
opens your profile page; **Sign out** is next to it.

### Signing in

The first time anyone opens the app, it asks for a name and creates the **admin**
profile. After that, the sign-in page lists everyone's profiles: click yours. Admins add
profiles for friends on the Profile page. Details, and why this is LAN-only for now:
[ACCOUNTS.md](ACCOUNTS.md).

First-time setup, if `data/` is empty:

```bash
.venv/bin/tcgnecro catalog                     # card data for every supported game
.venv/bin/tcgnecro import /path/to/swlcg.pnx   # one or more image collections
.venv/bin/tcgnecro import /path/to/ahlcg.pnx
```

## Proxies page

1. Pick the game in the top bar.
2. Either pick a **whole set**, or type a **decklist** (see below). The "Add a card" box
   autocompletes card names and appends a `1x` line.
3. **Preview** shows what will print and which names weren't found. Click a card to see
   it full size: **Front**/**Back** switch sides, ←/→ (or the arrows) step through the
   preview, and Esc or a click outside closes it. **Open original** opens the scan itself
   in a new tab.

   Landscape cards (Arkham investigators, acts and agendas) are stored sideways in
   collections so they print in a portrait slot. Everywhere on screen (here and in the
   deck builder) they're shown turned upright. PDFs keep them as stored.
4. Choose layout options and **Download PDF**.

PDF options:

| Option | Meaning |
|---|---|
| Page | letter or A4 |
| Layout | `edge`: cards touch. `gap`: 1/8" between cards. `margin`: each card shrunk 1 mm per side |
| Cut lines | `margins`: ticks outside the grid. `full`: lines across the page. `none` |
| Double-sided | Backs go on alternating pages, mirrored for duplex printing. Cards with no back image of their own get the game's generic back |
| Compact images | Re-encodes cards as 300 DPI JPEGs. Much smaller files; turn off for maximum quality |

### Decklist syntax

```
3x Machete
2 Deduction                     # "3x" and "3" both work; comments start with #
Shrivelling (5)                 # upgraded Arkham cards include their level
Machete [core]                  # pick a printing (pack code or variant name)
Machete [core:16:ahlcg]         # [printing:position:collection]
```

Names match ignoring case and punctuation. When several cards share a name, the
catalog title includes something to tell them apart, e.g. `Darth Vader (234.2)` in SWLCG
or `Daisy Walker (rod 1)` in Arkham. Use the autocomplete to find the exact title.

## Collection page

**Collection** (top bar) records which packs you own, separately for each game (tabs at
the top). Packs are grouped by cycle; tick a cycle's name to select all of it. Save when
done.

- Owning a repackaged box, like *The Dunwich Legacy Investigator Expansion*, counts as
  owning the player cards from the original Dunwich packs. Owning the Revised Core
  counts for its reprints of Core cards.
- Until you save a collection for a game, nothing in the deck builder filters by
  ownership. **Clear** returns a game to that "not set up" state. Owning nothing is
  different: that is a saved, empty collection.
- New packs added to the catalog (e.g. after `tcgnecro catalog ahlcg --refresh`) start
  unticked.

## Decks page

### Starting a deck

Your decks are listed first, followed by **Shared with you**: decks others have shared.

- **New deck:** pick the game, then the investigator (filterable by name or trait, or
  "Only … in my collection"; hover a name to see the card), and optionally a name. The investigator's required
  signature cards are added for you.
- **Import:** paste an ArkhamDB URL. Published decklists (`/decklist/view/…`) and shared
  decks (`/deck/view/…`) both work. Private decks can't be read through ArkhamDB's API.

### The builder

Three columns: **filters | cards | your deck**.

- **Filters** come from the game's config. For Arkham, "Only cards my investigator can
  take" is on by default and applies the investigator's deckbuilding rules. **Only cards
  I own** starts switched on once you've saved a collection for the game. With it off,
  cards outside your collection carry a "not owned" tag. Weaknesses
  and campaign cards are hidden by default; tick *Weakness* or *Story / campaign* under
  Type to add a story weakness or a story asset your campaign gave you. Neither counts
  toward deck size.
- **Cards:** `+`/`−` change copies (capped at the card's deck limit). Hover a name for
  its image; click it for full text and stats. "Show more" loads the next 100.
- **Your deck:** live counts (cards toward deck size, XP, weaknesses), problems in red
  (errors) or orange (warnings), and cards grouped by type. Problem cards are highlighted.
  - Investigator choices (secondary class, deck size, trait choice) are dropdowns under
    the investigator. Changing one re-checks the card list.
  - **Draw random basic weakness** adds one at random, as the rules require.
  - **Print proxies** opens the PDF options and prints the whole deck, including the
    investigator, signatures and weaknesses. It warns if some cards have no image. With a
    collection saved, **Only cards I don't own** prints just the proxies you need. The deck
    summary's **Not owned** figure tells you how many that is.
  - **Text list** is a plain decklist that pastes into the Proxies page or
    `tcgnecro pdf --deck`.
  - **JSON** downloads the deck file. **Copy** duplicates the deck. **Delete** asks first.
  - **Who can see this deck** (under the name): *Only me*, or *Everyone on this server*.
    Shared decks are read-only for others. They can print, export or **Copy to my
    decks**, but not change yours.
  - The deck name (top) and **Notes** (bottom) save when you leave the field.

### Keeping decks in sync with ArkhamDB

Decks imported from ArkhamDB stay **linked**: when the deck changes there, the change
comes here.

- **What syncs:** the cards, side deck and investigator choices. The name follows too,
  unless you've renamed the deck here. Your notes, who can see the deck, and the deck
  itself (its address here) are never touched.
- **Upgrades are followed.** When you upgrade a deck with experience, ArkhamDB makes a
  new deck for the new version and links the two. Sync follows that chain to the newest
  version and updates your deck in place.
- **When:** automatically about every hour while the server runs, whenever you click
  **Sync now** on the deck page, or with `tcgnecro decks sync`.
- **Changes made here win until you decide otherwise.** If you change the cards here,
  automatic syncing pauses for that deck and the deck page explains why. **Sync and
  replace my changes** takes ArkhamDB's version; **Stop syncing** keeps yours and
  unlinks the deck.
- **Decks imported before syncing existed** show **Keep in sync with the source**,
  which links them (replacing their cards with ArkhamDB's current version).
- **Only decks ArkhamDB shows publicly** can be synced, the same as importing: published
  decklists, and decks you've made public or shared. If a linked deck becomes private,
  the deck page shows the error and keeps the last synced version.
- **Copying a synced deck** gives you an unlinked copy, yours to change freely.
- **One-way:** changes made here never go back to ArkhamDB.

The deck list marks linked decks "synced" (with ⚠ if the last sync had a problem).

Filters are kept in the address bar, so reloading or bookmarking a builder URL keeps them.
"Reset filters" goes back to the game's defaults.

Every change is saved immediately; there is no save button.

## CLI reference

```bash
tcgnecro catalog [GAME...] [--refresh]   # load card data; --refresh re-downloads (ahlcg)
tcgnecro import FILE [--name NAME] [--game GAME]   # unzip a collection archive (.pnx/.zip) and index it
tcgnecro reindex                         # rescan collection folders after adding/fixing images
tcgnecro list                            # collections and image counts
tcgnecro remove NAME [--game GAME]       # delete a collection folder (--game if two games share the name)
tcgnecro pdf --game G (--deck FILE | --pack PACK) [-o out.pdf] [--page letter|a4]
             [--layout edge|gap|margin] [--cut-lines none|margins|full]
             [--double-sided] [--back-label LABEL] [--compact]
tcgnecro serve [--host H] [--port P] [--reload]
tcgnecro user list | add NAME | rename NAME NEW | admin NAME [--off] | remove NAME --decks-to NAME
tcgnecro decks sync [DECK_ID...] [--force]   # sync linked decks now; --force replaces local changes
```

`--deck -` reads the decklist from stdin.

**What `import` accepts:** a Proxy Nexus `.pnx` or any `.zip`. Inside, it looks for
`manifest.toml` and the card images either at the top of the archive or in a single
folder that holds everything (what "Compress folder" produces). Images can be in an
`images/` folder or loose next to the manifest. With no `manifest.toml`, pass `--game`
(e.g. `--game ahlcg`) and one is written for you. Files not named like card images
(`0235@core-set.png`) are skipped and listed. An archive with no card images at all is
refused rather than imported empty. The collection is named after the file, without
`.zip`/`.pnx`.

## Where your data lives

Everything is under `data/` (or the folder in `TCGNECRO_DATA`):

| Path | What | Back up? |
|---|---|---|
| `data/decks/*.json` | everyone's decks | **yes**, this is the only copy |
| `data/users.json` | profiles | **yes** |
| `data/secret.key` | session signing key | optional; losing it just signs everyone out |
| `data/collections/` | card images | yes, unless you still have the archives |
| `data/cache/` | downloaded card data | no, re-downloads |
| `data/index.db` | search index | no, rebuild with `catalog` + `reindex` |

Deck files are plain JSON (format documented in docs/DECKBUILDER.md), so copying
`data/decks/` to another machine moves your decks.
