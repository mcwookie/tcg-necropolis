# TCG Necropolis

Turn a decklist (or a whole set) into a print-and-play PDF using your own card scans.

Based on [Proxy Nexus](https://github.com/axmccx/proxynexus-rs) by Alex McCulloch. TCG
Necropolis uses the same image naming convention and collection layout, and imports
Proxy Nexus `.pnx` collection files as-is.

MVP scope: Star Wars LCG, PDF export, web UI + CLI. No MPC export, bleed layout, or
upscaling yet.

## Setup

```bash
/usr/bin/python3.12 -m venv .venv            # needs Python 3.11+
.venv/bin/pip install -e '.[dev]'
.venv/bin/tcgnecro catalog                   # load card catalogs
.venv/bin/tcgnecro import /path/to/swlcg.zip # unzip a collection + index it
.venv/bin/tcgnecro serve                     # http://127.0.0.1:8000
```

Use `tcgnecro serve --host 0.0.0.0` to reach it from other machines on your network.

## Collections

Collections are plain folders:

```
data/collections/{game}/{collection}/
    manifest.toml
    0235@core-set.png
    bfd01@balance-of-the-force~back.png
```

Images follow upstream's naming convention, `{card_id}@{printing}[~back|~back2…][.bleed].{ext}`,
and a collection archive is just one of these folders zipped (with the images under
`images/`), so collections stay interchangeable with upstream.

To fix or add a card, drop the file into the folder and run `tcgnecro reindex`. Nothing else
needs rebuilding: `data/index.db` is a cache built from the catalogs and the folders.

`TCGNECRO_DATA` sets the data folder (default `./data`).

## CLI

```bash
tcgnecro list
tcgnecro pdf --game swlcg --deck deck.txt -o deck.pdf
tcgnecro pdf --game swlcg --pack core-set --compact --double-sided --back-label proxy
tcgnecro remove swlcg
```

`--compact` re-encodes images as 300 DPI JPEGs; the full-resolution SWLCG scans are
~3 MB each, so a full-size PDF runs to a few MB per card.

## Decklists

```
3x Force Lightning
2 Gotal Outcast (48.2)          # comments are ignored
Force Lightning [core-set]      # pick a printing
Force Lightning [alt:0:mine]    # [printing:position:collection]
```

Names match case-insensitively, ignoring punctuation. SWLCG titles include the objective
set number when a name repeats, e.g. `Darth Vader (234.2)`.

## Layout

```
tcgnecro/
  naming.py       filename convention + validation
  db.py           SQLite schema, catalog storage, title normalization
  collections.py  archive import, folder scan (reindex)
  decklist.py     parsing, name resolution, printing selection
  pdf.py          page layout, cut lines, double-sided backs
  web.py          FastAPI app; templates/index.html is the whole UI
  cli.py          `tcgnecro` command
  games/          one package per game: load_catalog() + optional backs/
```

## Adding a game

Create `tcgnecro/games/<id>/__init__.py` with `GAME_ID`, `NAME` and `load_catalog()`
returning a `Catalog` (see `games/swlcg`), optionally add generic backs as
`backs/{back_group}_{label}.png`, add the id to `GAME_MODULES` in `games/__init__.py`, then
run `tcgnecro catalog <id>`.
