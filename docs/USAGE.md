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
3. **Preview** shows what will print and which names weren't found.
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

## Decks page

### Starting a deck

Your decks are listed first, followed by **Shared with you**: decks others have shared.

- **New deck:** pick the game, then the investigator (filterable by name or trait;
  hover a name to see the card), and optionally a name. The investigator's required
  signature cards are added for you.
- **Import:** paste an ArkhamDB URL. Published decklists (`/decklist/view/…`) and shared
  decks (`/deck/view/…`) both work. Private decks can't be read through ArkhamDB's API.

### The builder

Three columns: **filters | cards | your deck**.

- **Filters** come from the game's config. For Arkham, "Only cards my investigator can
  take" is on by default and applies the investigator's deckbuilding rules. Weaknesses
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
    investigator, signatures and weaknesses. It warns if some cards have no image.
  - **Text list** is a plain decklist that pastes into the Proxies page or
    `tcgnecro pdf --deck`.
  - **JSON** downloads the deck file. **Copy** duplicates the deck. **Delete** asks first.
  - **Who can see this deck** (under the name): *Only me*, or *Everyone on this server*.
    Shared decks are read-only for others. They can print, export or **Copy to my
    decks**, but not change yours.
  - The deck name (top) and **Notes** (bottom) save when you leave the field.

Filters are kept in the address bar, so reloading or bookmarking a builder URL keeps them.
"Reset filters" goes back to the game's defaults.

Every change is saved immediately; there is no save button.

## CLI reference

```bash
tcgnecro catalog [GAME...] [--refresh]   # load card data; --refresh re-downloads (ahlcg)
tcgnecro import FILE [--name NAME]       # unzip a collection archive (.pnx/.zip) and index it
tcgnecro reindex                         # rescan collection folders after adding/fixing images
tcgnecro list                            # collections and image counts
tcgnecro remove NAME                     # delete a collection folder
tcgnecro pdf --game G (--deck FILE | --pack PACK) [-o out.pdf] [--page letter|a4]
             [--layout edge|gap|margin] [--cut-lines none|margins|full]
             [--double-sided] [--back-label LABEL] [--compact]
tcgnecro serve [--host H] [--port P] [--reload]
tcgnecro user list | add NAME | rename NAME NEW | admin NAME [--off] | remove NAME --decks-to NAME
```

`--deck -` reads the decklist from stdin.

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
