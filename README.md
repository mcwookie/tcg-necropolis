# TCG Necropolis

Build decks and print proxies for out-of-print card games, using your own card scans.

- **Proxies:** turn a decklist (or a whole set) into a print-and-play PDF.
- **Decks:** a deck builder with per-game rules, filters and validation. It currently
  supports Arkham Horror: The Card Game, and any deck goes straight to the proxy printer.
- **Profiles:** each person signs in to their own profile and owns their decks, which
  they can keep private or share read-only with everyone on the server.

Based on [Proxy Nexus](https://github.com/axmccx/proxynexus-rs) by Alex McCulloch. TCG
Necropolis uses the same image naming convention and collection layout, and imports
Proxy Nexus `.pnx` collection files as-is.

| Game | Proxies | Deck builder | Card data |
|---|---|---|---|
| Arkham Horror: The Card Game (`ahlcg`) | yes | yes | ArkhamDB, downloaded and cached |
| Star Wars LCG (`swlcg`) | yes | not yet | bundled JSON |

## Quick start

```bash
/usr/bin/python3.12 -m venv .venv               # needs Python 3.11+
.venv/bin/pip install -e '.[dev]'
.venv/bin/tcgnecro catalog                      # load card data for every game
.venv/bin/tcgnecro import /path/to/ahlcg.pnx    # add a collection of card images
.venv/bin/tcgnecro serve                        # http://127.0.0.1:8000
```

The first person to open it creates the admin profile. Use `tcgnecro serve --host 0.0.0.0`
to reach it from other machines on your network.

**Sign-in is currently a profile picker with no passwords, so only run it on a network you
trust.** Real sign-in for internet access is planned; see
[docs/ACCOUNTS.md](docs/ACCOUNTS.md).

## Documentation

| Doc | What's in it |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | Using the web app and the CLI, decklist syntax, where data lives, backups |
| [docs/ACCOUNTS.md](docs/ACCOUNTS.md) | Profiles, sign-in, deck ownership and sharing, managing profiles |
| [docs/DECKBUILDER.md](docs/DECKBUILDER.md) | How the deck builder works: plugins, the TOML config reference, the Arkham rules engine |
| [docs/UPDATING_GAMES.md](docs/UPDATING_GAMES.md) | Keeping a game current: new expansions, new images, new rules |
| [docs/ADDING_A_GAME.md](docs/ADDING_A_GAME.md) | Supporting a new game, for proxies and optionally deck building |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What's left to do, known limitations, ideas |

## Where things live

```
data/                      (not in git; everything you own or download)
  collections/{game}/{name}/   card images, one folder per collection
  decks/{id}.json              your decks, one file each
  users.json                   profiles
  secret.key                   signs the session cookie
  cache/ahlcg/                 ArkhamDB card data
  index.db                     cache rebuilt from the above; safe to delete
tcgnecro/
  naming.py       image filename convention + validation
  db.py           SQLite index: schema, migrations, catalog storage
  collections.py  archive import, folder scan (reindex)
  decklist.py     decklist parsing, name resolution, printing selection
  pdf.py          page layout, cut lines, double-sided backs
  web.py          FastAPI app: Proxies page + mounts the deck builder and sign-in
  auth.py         sign-in, sessions, profile pages
  users.py        profile store
  templating.py   the shared Jinja environment
  cli.py          the `tcgnecro` command
  templates/      site layout, Proxies, sign-in and profile pages
  static/         shared CSS, vendored htmx
  deckbuilder/    game-agnostic deck builder core (see docs/DECKBUILDER.md)
  games/<id>/     one package per game: catalog, card backs, optional deck builder
tests/            pytest; run with `.venv/bin/python -m pytest`
```

## License

TCG Necropolis is free software, licensed under the **GNU Affero General Public License
v3.0** (AGPL-3.0). See [LICENSE](LICENSE).

```
Copyright (C) 2026 Brian Vogle
Portions copyright (C) Alex McCulloch and Proxy Nexus contributors
```

Parts of this program are ported from [Proxy Nexus](https://github.com/axmccx/proxynexus-rs)
by Alex McCulloch, which is licensed under AGPL-3.0. The ported parts are the
image naming convention and collection import (`naming.py`, `collections.py`),
decklist parsing and printing selection (`decklist.py`), the PDF page layout (`pdf.py`),
and the Arkham Horror catalog adapter (`games/ahlcg/__init__.py`).

Because of the AGPL, anyone you let use a running copy of this program over a network
must be able to get its source code. Pointing them to this repository satisfies that,
as long as what you run matches what's published here.

Third-party components:

- `tcgnecro/static/htmx.min.js`: [htmx](https://htmx.org) 2.0.4, BSD Zero-Clause License.

Card artwork, including the card backs under `tcgnecro/games/*/backs/` and any card
scans, and card text are the property of their publishers (Fantasy Flight Games /
Asmodee). They are not covered by this license. Card data for Arkham Horror is
downloaded from [ArkhamDB](https://arkhamdb.com) at run time and is not part of this
repository.
