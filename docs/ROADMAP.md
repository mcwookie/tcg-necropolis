# Roadmap and known limitations

What exists today is a usable first version. This list is roughly in priority order
within each section. "Size" is a rough guess: S = an hour or two, M = a session,
L = several sessions.

## Deck builder: Arkham rules

| Item | Size | Notes |
|---|---|---|
| Taboo lists | M | ArkhamDB publishes them through its API: XP changes, chains, unchained cards, bans. Imported decks already keep `meta.taboo_id`. Needs a taboo picker per deck plus applying changes to the pool view |
| Customizable cards | M | `customization_options` exist on 16 cards. XP comes from checked boxes, stored in deck meta (check how ArkhamDB encodes them before choosing keys). Currently they count as their printed XP (0) |
| Parallel investigators | M | ArkhamDB's `alternate_front` / `alternate_back` meta picks which side's deckbuilding applies. Today parallel versions are separate investigators |
| Side deck / upgrades / campaign XP | M | `side_slots` is stored but not shown. Deck versions and "XP available" are the core of campaign play |
| `base_level` (Subject 5U-21) | S | Read, but not applied |
| Bonded cards | S | Excluded from the pool. Could be listed under their parent and printed with it |
| Pack ownership filter | S | "Only packs I own", stored once, applied as a filter |

## Deck builder: features

| Item | Size | Notes |
|---|---|---|
| Deck sharing between people | M | Today everyone on the server sees every deck. Options: simple per-browser owner tags, or HTTP basic auth per user |
| Export to ArkhamDB format | S | ArkhamDB has no public upload API, but its text/JSON import formats are documented |
| Deck charts | S | Cost curve, class split, skill icons; see arkham.build |
| Card list virtualization | M | "Show more" pages of 100 are fine for ~1,500 cards; a bigger game might want infinite scroll |
| Live updates across tabs | M | Edits are safe across tabs, but a second tab only shows them after reload. Server-sent events would fix it |
| Undo | S | Keep the last N `slots` states in deck meta |
| Icon font / SVG icons | S | Icons are text badges today, so the repo carries no third-party font. Undrawn tokens are scenario-only (seals, runes, day/night). Swapping to SVGs means a new `IconConfig` field and a template change |

## More games

| Game | Size | Notes |
|---|---|---|
| Marvel Champions (proxy + deck builder) | M | MarvelCDB API is close to ArkhamDB's; upstream has an adapter to port |
| LotR LCG, AGoT 1st/2nd, Netrunner, L5R, Warhammer, Call of Cthulhu | S each for proxies | Port upstream's adapters. Deck builders are M each, since each game builds decks differently (see docs/DECKBUILDER.md) |
| SWLCG deck builder | M | Decks are built from objective sets of 6, so `+` adds a set, not a card. The plugin API supports this via `run_action` / a custom `start_deck`, but the card list would want to show sets |
| MECCG | L | Card data exists in this workspace (separate project); rules are complex |

## Proxies

| Item | Size | Notes |
|---|---|---|
| MakePlayingCards (MPC) zip export | M | Upstream's `mpc.rs`: bleed generation by edge extension, MPC's pixel sizes |
| Bleed print layout | S–M | Port `print_prep.rs` (`add_uniform_bleed_border`) |
| Upscaling | M | Deferred by choice. onnxruntime + the same Real-ESRGAN model; slow on CPU, so offline pre-upscaling may be better |
| Background PDF jobs with progress | S | Big sets take ~30 s in a single request today |
| Landscape cards | S | Rotated to fit portrait slots; upstream doesn't either |

## Data and housekeeping

- **Unmatched images in the AHLCG collection.** 313 images (174 card codes) in
  `ahlcg.pnx` don't match a catalog card. Most look like cards ArkhamDB marks `hidden`,
  which upstream also drops. Worth a look to see if any are real cards worth surfacing.
- **Title normalization** uses NFKD + ASCII instead of upstream's `deunicode`. Rare
  characters (ß, æ) can normalize differently.
- **Card back artwork** in `games/*/backs/` is publisher art. Decide whether it belongs in a
  public repo; the app runs without it (blank backs).
- **Docker packaging** to replace proxynexus-package: a small Python image plus a volume
  for `data/`. S–M.
- **ArkhamDB etiquette:** card data is fetched only on `catalog --refresh` and cached;
  imports hit the API once per deck. Keep it that way. Don't add per-request API calls.

## Design decisions worth revisiting

- **HTMX over a JavaScript framework.** Kept the code in Python. Every filter change is a
  round trip (fine on a LAN, ~15 ms server time). If it ever feels slow, send the pool to
  the browser as JSON and filter there, keeping validation server-side.
- **Rules in Python, look-and-feel in TOML.** Moving more into TOML (e.g. simple deck size
  / copy limits) would let simple games skip Python entirely. That's worth doing when the
  second deck builder game arrives and shows which rules are truly common.
- **Decks as JSON files.** Simple and portable. Revisit if multi-user features need queries
  across decks.
