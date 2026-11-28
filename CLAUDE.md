# CLAUDE.md

Orientation for AI assistants working on TCG Necropolis. Read this first, then
the doc for whatever you're touching (table at the end). The code and docs are
the source of truth; this file says where things are and how the owner likes
work done.

## What it is

A self-hosted web app (and CLI) for out-of-print card games: **print proxies**
from your own card scans as PDFs, **build decks** with per-game rules, track
**which packs you own**, add **custom cards**. A Python rewrite of Proxy Nexus
(proxynexus-rs), keeping its image naming and collection layout. AGPL-3.0;
the owner knows Python well.

Games (`GAME_MODULES` in `tcgnecro/games/__init__.py`): `ahlcg` (Arkham Horror,
ArkhamDB data), `marvel_champions` (MarvelCDB data), `swlcg` (Star Wars LCG),
`meccg` (Middle-earth CCG), `city_of_heroes`. The last three use bundled JSON in
their game folder. Every game has proxies and a deck builder.

## Commands

```bash
/usr/bin/python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # plain python3 here may lack ssl
.venv/bin/python -m pytest -q                 # all tests; keep them green
.venv/bin/tcgnecro catalog [GAME...]          # load card data (+ custom cards) into index.db
.venv/bin/tcgnecro reindex                    # rescan image folders
.venv/bin/tcgnecro serve                      # web UI on http://127.0.0.1:8000
.venv/bin/tcgnecro user --help                # profiles, emails, admins
```

Data lives in `data/` (or `$TCGNECRO_DATA`), never in git:
`collections/<game>/<name>/` card images (the source of truth for images),
`decks/*.json` and `users.json` (user data: never delete), `custom/<game>/`
custom cards, `cache/` downloaded card data, `index.db` + `thumbs/` (rebuildable
caches). The owner's data folder is shared with the live server, so **for local
previews use a scratch copy**: copy `index.db`, `users.json`, `decks/`; symlink
`collections/` and `cache/`; run with `TCGNECRO_DATA=<copy>` and
`TCGNECRO_SYNC_MINUTES=0` (no deck syncing). Sign in to a preview with
`POST /login user_id=<profile id>`.

## Code map

| Path | What |
|---|---|
| `tcgnecro/web.py` | FastAPI app: proxy printer (`/proxies`), images/thumbs, PDF, `/healthz`; mounts the routers below |
| `tcgnecro/home.py` | `/` games landing grid and `/games/<id>` game pages |
| `tcgnecro/auth.py` | sessions; sign-in is a profile picker, or Cloudflare Access (verified JWT email → profile) when `TCGNECRO_CF_ACCESS_TEAM/_AUD` are set |
| `tcgnecro/users.py`, `ownership.py`, `collection_page.py` | profiles (`users.json`), owned packs, Collection page |
| `tcgnecro/db.py`, `collections.py`, `naming.py` | SQLite index, image scanning, `{card}@{pack}[~back][.bleed].ext` names |
| `tcgnecro/decklist.py`, `pdf.py` | decklist parsing/printing choice, PDF layout (PyMuPDF) |
| `tcgnecro/games/<id>/` | per game: catalog (`load_source` → `build_catalog`), `backs/`, `display_rotation`, `DESCRIPTION`, `LOGO_TILE`, deck builder (`deckbuilder.py` rules + `deckbuilder.toml` config) |
| `tcgnecro/games/custom.py` | merges `data/custom/<game>/` cards into catalogs, skipping bad records with warnings |
| `tcgnecro/deckbuilder/` | game-agnostic deck builder: plugin base, registry/pools, filters, routes, sync (ArkhamDB/MarvelCDB), `simulate.py` (deck simulation core), `buildql.py` (Query box language), templates |
| `tcgnecro/games/ahlcg/simulate.py` | Arkham deck simulator (Monte Carlo); card effects in `sim_effects.json` (servers add `data/custom/ahlcg/sim_effects.json`), bags in `chaos_bags.toml` |
| `tcgnecro/templates/`, `static/` | Jinja + HTMX pages; `app.css`; `static/logos/<id>.(svg\|png\|webp\|jpg)` replaces a game's text tile |
| `deploy/`, `compose.yaml`, `Dockerfile` | Docker image, compose (optional `https` Caddy and `tunnel` cloudflared profiles), update timer |

## Conventions and gotchas

- **Game rules are data-driven** where the source data allows (ArkhamDB/MarvelCDB
  `deck_options`), and were checked against real published decks. Keep changes
  faithful to the official rules and the source sites' validation.
- **New games:** follow `docs/ADDING_A_GAME.md`; split loading into
  `load_source` / `build_catalog` so custom cards work automatically.
- **Images:** landscape cards are stored sideways; `display_rotation` turns them
  upright on screen only (PDFs use scans as stored). `web._open` leaves scans that
  are already landscape alone.
- **Static URLs** go through `static_url()` (content fingerprint), so CSS/JS
  changes reach users through browser and Cloudflare caches.
- **Templates:** pass `crumb_game` to show the "/ Game" breadcrumb. In JS inside
  Jinja, don't put `${...}` template literals in single-quoted strings; concatenate.
- **Tests:** synthetic data per game in `tests/`; for Arkham pools reuse
  `ROLAND` / `card()` from `tests/test_deckbuilder.py` (cards need full attrs).
  Add tests with every behaviour change.
- **Layout:** pages must work from phone (390 px, no sideways scrolling) to wide
  desktop; check both, plus dark mode, for UI changes.
- **Docs:** every user-visible change updates `docs/` (and `README.md` when it's
  a headline feature). Plain, direct prose.
- **Data safety:** never delete or overwrite user data or card images without
  asking; back up first and log what changed.

## Working with the owner

- **Commit and push only when asked.** Show what changed and ask first. Commit
  messages: a short subject, then a body explaining what and why, ending with
  the `Co-Authored-By:` trailer your harness specifies. Work lands on `main`
  directly or via a PR.
- Pushing to `main` builds `ghcr.io/mcwookie/tcg-necropolis` in GitHub Actions
  (tests, then an old-x86-CPU start check, then multi-arch images). The live
  server pulls `:latest` about every 5 minutes, so a push to `main` deploys.
  Pull requests into `main` run the tests only (the `test` check).
  Don't act on the server itself without the owner's say-so.
- Report outcomes plainly: what was tested and how, what wasn't.
- The owner works from more than one Claude client (Claude Code, Claude
  desktop); they share only this repository. Check `git log` for recent work, and
  record anything other sessions should know in this file or in `docs/`.

## Hosting (public summary)

Runs in Docker on the owner's home server, reachable from the internet through
a Cloudflare Tunnel with Cloudflare Access in front (Google, GitHub and
emailed-code logins, configured in the Cloudflare dashboard, not in code). With
Access on, the app refuses requests that lack a valid Access token, including
direct LAN visits; people sign in as the profile whose email matches. Setup and
troubleshooting: `docs/DOCKER.md`, `docs/ACCOUNTS.md`.

## Open items

- New landing/game pages: not yet checked at phone width or in dark mode.
- No game logo images yet (`tcgnecro/static/logos/`).
- See `docs/ROADMAP.md` for the longer list.

## Docs

| Doc | For |
|---|---|
| `docs/USAGE.md` | using the app and CLI, where data lives |
| `docs/DECKBUILDER.md` | deck builder internals, plugin API, each game's rules |
| `docs/BUILDQL.md` | the deck builder's Query box (BuildQL): syntax, fields, `[query]` config |
| `docs/SIMULATOR.md` | the Arkham deck simulator: model, settings, effects format |
| `docs/ADDING_A_GAME.md` | supporting a new game |
| `docs/UPDATING_GAMES.md` | new expansions, images, rule data |
| `docs/CUSTOM_CARDS.md` | custom cards and packs |
| `docs/ACCOUNTS.md` | profiles, sign-in, Cloudflare Access |
| `docs/DOCKER.md`, `docs/DEPLOY_VPS.md` | deployment, tunnel, backups |
| `docs/ROADMAP.md` | what's left |
