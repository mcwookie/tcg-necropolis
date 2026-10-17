# Updating a game

A game has three independent layers. A new expansion can touch all three:

| Layer | Where | Updated by |
|---|---|---|
| **Card data** (names, packs, rules text) | ArkhamDB for `ahlcg`; JSON in the repo for `swlcg` | `tcgnecro catalog <game>` |
| **Card images** | `data/collections/<game>/<collection>/` | dropping in files, then `tcgnecro reindex` |
| **Deck builder rules and config** | `tcgnecro/games/<game>/deckbuilder.py` / `.toml` | editing code (only when the expansion adds something new) |

Nothing needs a server restart: the running app notices a reloaded catalog or a reindex
on the next request.

---

## Arkham Horror: adding a new expansion

### 1. Card data

ArkhamDB usually lists new packs around release. Check that it's there:

```bash
curl -s https://arkhamdb.com/api/public/packs/ | python3 -m json.tool | grep -A2 '"name"' | tail -20
```

Then re-download and reload:

```bash
.venv/bin/tcgnecro catalog ahlcg --refresh
```

That replaces `data/cache/ahlcg/` and the `ahlcg` rows in `index.db`. Without
`--refresh`, `catalog` reuses the cached download. Use that form to reload after a code
change without hitting ArkhamDB.

**Collection page groups:** packs are grouped by ArkhamDB's cycle number. A new campaign
cycle is named after its first pack automatically. Non-campaign cycle numbers (starter
decks, standalone scenarios...) get their labels from `CYCLE_LABELS` in
`games/ahlcg/__init__.py`. If a new kind of product shows up with a number missing there,
add a label. New packs start unticked in everyone's collection.

**Repackaged expansions** (the "Investigator Expansion" / "Campaign Expansion" boxes)
need nothing extra. ArkhamDB marks them with `reprint_type`, and `load_catalog` gives
their reprinted cards versions under the new pack code automatically.

### 2. Card images

Images are named `{card code}@{pack code}.{jpg|png}`, with extra sides as `~back`,
`~back2`, … and bled scans as `.bleed` before the extension:

```
11001@tskc.jpg           front
11001@tskc~back.jpg      back (investigators, double-sided cards)
11001@tskc.bleed.jpg     the same front with bleed
```

Card and pack codes are ArkhamDB's: open the card on arkhamdb.com and the URL ends in
the card code. The pack code is in the pack's URL (`/set/tskc`).

Either add the files to an existing collection folder, or start a new one, which keeps
a set's scans together and makes them easy to share:

```bash
mkdir -p data/collections/ahlcg/ahlcg-tskc
cat > data/collections/ahlcg/ahlcg-tskc/manifest.toml <<'EOF'
game = "ahlcg"
version = "1.0.0"
language = "en"
EOF
cp ~/scans/tskc/*.jpg data/collections/ahlcg/ahlcg-tskc/
.venv/bin/tcgnecro reindex
```

`reindex` prints a line for any image whose sides don't add up (a back with no front,
`back2` without `back`). Check the result:

```bash
.venv/bin/tcgnecro list                         # image counts per collection
```

In the web app, the Proxies page's "Whole set" dropdown shows `(N)` printable cards per
pack, and in the deck builder, cards without images show no thumbnail on hover.

Images whose code doesn't match any card still index, but they can't be printed by
name. The usual cause is a typo in the code, or a card ArkhamDB marks hidden.

To share a collection, zip the folder with the images under `images/`:

```bash
cd data/collections/ahlcg/ahlcg-tskc && mkdir -p /tmp/pack/images && cp *.jpg /tmp/pack/images/ \
  && cp manifest.toml /tmp/pack/ && (cd /tmp/pack && zip -r ~/ahlcg-tskc.zip .)
```

`tcgnecro import ahlcg-tskc.zip` (or upstream Proxy Nexus, renamed to `.pnx`) reads it. A
plain zip of the folder works too, and so does a zip of loose images without a manifest:
`tcgnecro import scans.zip --game ahlcg`.

### 3. Deck builder: does the expansion add anything new?

Most expansions need **no code changes**: new investigators bring their own
`deck_options`, and new player cards are just data. Check these four things:

**a) New kinds of deckbuilding rules.** List any option keys the engine doesn't know:

```bash
.venv/bin/python - <<'EOF'
import json
known = {"faction", "faction_select", "option_select", "deck_size_select", "level", "type",
         "trait", "slot", "uses", "text", "tag", "permanent", "not", "limit", "atleast",
         "ignore_match", "error", "name", "id", "base_level", "size"}
for c in json.load(open("data/cache/ahlcg/cards.json")):
    for o in c.get("deck_options") or []:
        extra = set(o) - known
        if extra:
            print(c["code"], c["name"], sorted(extra))
EOF
```

Unknown keys are currently ignored, which makes the option more permissive than it
should be. To support one, add a check in `AhlcgPlugin.option_matches` (and to `known`
above), plus a test in `tests/test_deckbuilder.py`.

**b) New player cards that change deckbuilding in their text** (like "Your deck size is
increased by 5"). Search the new pack's cards for "deck size" or "deckbuilding". Add size
changes to `DECKBUILDING_CARDS` in `games/ahlcg/deckbuilder.py`. Other effects go in
`_special_problems` (checks) or `_options` (extra allowed cards). Key them by English
card name so reprints are covered.

**c) New text icons.** Card text uses `[token]` for icons. List the tokens the config
doesn't draw:

```bash
.venv/bin/python - <<'EOF'
import json, re, tomllib
icons = tomllib.load(open("tcgnecro/games/ahlcg/deckbuilder.toml", "rb"))["icons"]
seen = {t for c in json.load(open("data/cache/ahlcg/cards.json"))
        for t in re.findall(r"\[([a-z_]+)\]", (c.get("text") or "") + (c.get("back_text") or ""))}
print(sorted(seen - set(icons)))
EOF
```

Add any that matter to `[icons]` in `deckbuilder.toml`. Undrawn tokens just show as
`[token]`.

**d) New landscape card types.** If an expansion introduces a landscape card type, add it
to `LANDSCAPE_TYPES` in `games/ahlcg/__init__.py` so it displays upright.

**e) New filter values.** A new slot, card type or class needs adding to the matching
`[[filters]]` `options` list (and `[colors]` for a class) in `deckbuilder.toml`.
Otherwise the cards still appear, but can't be selected by that filter.

Finally:

```bash
.venv/bin/python -m pytest
```

### Errata and taboo updates

Errata arrive with the next `catalog ahlcg --refresh`. Taboo lists aren't applied yet (see
ROADMAP); imported decks keep their `taboo_id` in `meta` for when they are.

---

## Star Wars LCG: adding or correcting a pack

SWLCG's data is bundled in the repo because there is no maintained online source:

- `tcgnecro/games/swlcg/swlcg_packs.json`: `{"code", "name", "position"}`. `position` orders
  packs; newer is higher.
- `tcgnecro/games/swlcg/swlcg_cards.json`: one entry per card. See the docstring in
  `games/swlcg/__init__.py` (and upstream's `models.rs`) for the fields. `unique_id` is
  the card id used in image names, and `label` is the title used in decklists, so make it
  unique (add `(set.number)` when a name repeats).

After editing:

```bash
.venv/bin/tcgnecro catalog swlcg
# images: {unique_id}@{pack code}.png in data/collections/swlcg/<collection>/
.venv/bin/tcgnecro reindex
```

Commit the JSON changes; they are the source of truth for this game.

---

## Fixing or replacing a single image

Overwrite the file in the collection folder, keeping its name. If the name changed, run
`tcgnecro reindex`. Thumbnails regenerate automatically, since they are keyed by the file's
modification time.

## Checklist

- [ ] `tcgnecro catalog <game> --refresh` (Arkham) or edit JSON + `tcgnecro catalog <game>` (SWLCG)
- [ ] Images named `{code}@{pack}.ext`, in a collection folder; `tcgnecro reindex`; no warnings
- [ ] Arkham: no unknown `deck_options` keys; deckbuilding cards added to `DECKBUILDING_CARDS`
- [ ] New `[icons]`, filter options and colors in `deckbuilder.toml` if needed
- [ ] `pytest` passes; spot-check a new investigator's deck in the builder
