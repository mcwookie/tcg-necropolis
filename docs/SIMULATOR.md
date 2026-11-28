# Deck simulator (Arkham Horror)

The simulator plays an Arkham deck solo thousands of times and tells you how often it
passes the tests you care about, how its hand and resources hold up, when its assets
come into play and when its weaknesses show up. It's a Monte Carlo simulation: one game
tells you little, but a thousand games played the same way show the deck's tendencies.

Open a deck and press **Simulate** (next to **Print proxies**). Anyone who can see a
deck can simulate it; nothing about the deck changes.

Use it to **compare decks**: pick another deck in **Compare with** and both run with
the same settings and seed, side by side (see [Comparing two decks](#comparing-two-decks)).
The simulated player follows fixed rules and doesn't face a real scenario, so treat the
figures as a measuring stick, not a forecast.

## Settings

| Setting | What it does |
|---|---|
| Compare with | Another deck of the same game you can see (yours or a shared one). Both are simulated with these settings and shown side by side. *Nothing* simulates this deck alone |
| Spend spare actions on | Investigating, fighting or evading. After playing cards and drawing or gaining resources when needed, every other action goes to this |
| Shroud / enemy fight / evade | The difficulty of those tests |
| Treachery test each round | From round 2, one test of this skill each round (the mythos phase), with its own difficulty. Committed cards are used up, as in a real game. Choose *None* to skip it |
| Chaos bag | A campaign's bag at a difficulty, or **Type my own**: tokens separated by spaces, e.g. `+1 0 0 -1 -2 -3 skull cultist auto_fail elder_sign` (`elder_thing`, `tablet` too; `tentacle` means auto-fail) |
| Skull, cultist, tablet and elder thing count as | Their effect depends on the scenario, so pick a modifier; −2 is a fair average on Standard |
| Commit cards | How hard the simulated player tries: commit cards (and spend resources on cards like Hyperawareness) until the chance of passing reaches 50% or 75%, until it's as sure as the bag allows, or never |
| Mulligan the opening hand | Redraw cards that aren't cheap assets or useful for your spare actions, once |
| Enemy health | When fighting: damage to defeat an enemy. Counts enemies defeated, and triggers cards like Evidence! |
| Actions lost per weakness drawn | A weakness's own effect isn't simulated; it costs this many actions instead |
| Show the chance to pass | Which rows the chance-to-pass table shows |
| Rounds per game, games to play | 10 rounds and 1,000 games take a second or two |
| Random seed | Any number or word. The same seed and settings give the same results, so you can compare decks fairly. Leave it empty for fresh results |

The settings are kept in the page address, so a bookmark or a shared link reruns the
same simulation.

## Reading the results

**Chance to pass.** For each kind of test and each difficulty from 1 to 6: the chance
of passing at the start of a turn, with the cards you have in play, what the commit
policy would commit from your hand at that moment, and the chaos bag. Averaged over
every turn of every game. The chance is worked out exactly from the bag rather than by
drawing one token, so it's precise even with few games. A second table shows how it
changes from round 1 to the last round as assets come into play and the hand changes.

Rows: *Investigate*, *Fight* and *Evade* use the best way you have to take that action
(your skill, a Flashlight, a .45 Automatic, Rite of Seeking...). The plain skill tests
(*Willpower test*, *Agility test*...) are treachery-style tests: only constant bonuses
apply.

**Spare actions.** Clues (or damage, or evades) per game, the share of tests passed,
and a chart of the running total by round: the line is the typical game (median) and
the shaded band the middle 80% of games. Enemies defeated and treacheries passed, when
they apply.

**Hand.** Cards in hand at the start of each turn; how often a turn starts with 0–1
cards; how often the player wanted to commit to a test but had nothing worth
committing; how often a game drew through its whole deck.

**Resources and actions.** Resources by round, how often an asset was stuck in hand for
lack of resources, and where the actions went (playing cards, drawing, gaining
resources, tests, weaknesses).

**Assets in play.** For each asset, the share of games where a copy had been played by
round 1, 3, 5 and the last round. A good way to see how reliably a key card turns up.

**Weaknesses.** The share of games where each weakness had been drawn by those rounds,
and the typical round.

**How this deck was modelled.** How many of the deck's cards are fully simulated, which
ones count only for their icons, cost and slots, and any approximations (see below).

## Comparing two decks

Choose a deck in **Compare with** and run. The deck you opened is **A**, the other is
**B**. To compare two versions of one deck, copy it (**Copy** on the deck page), change
the copy, and compare the two.

Both decks use the same settings and the same random seed. If the seed box is empty, a
fresh seed is picked for the run and shown above the results, so you can type it in to
repeat it. Settings that depend on the deck (the investigator's skills, elder sign) come
from each deck.

The results have the same sections as a single run:

- headline figures become a small table: A, B and the change (B minus A);
- tables show each row twice, *· A* then *· B*, with the change in small print after
  each of B's figures. Percentages change in points (40% → 48% is *+8*);
- charts draw both decks, A in blue and B in orange, with a legend;
- rows only one deck has (its own assets or weaknesses) appear once, and a section only
  one deck has (Weaknesses, say) says so;
- notes the decks share are listed once; the rest are marked *A:* or *B:*.

The same seed doesn't make the two decks draw the same cards (their decks differ), so
some of a difference is still luck. With 1,000 games a change of a point or two in a
percentage can be noise; run more games or try another seed before trusting it.

The comparison is game-agnostic (`compare_reports` in `deckbuilder/simulate.py`), so a
future simulator for another game gets it too.

## What the simulated player does

Setup: 5 resources, permanents in play, a 5-card opening hand (weaknesses set aside and
replaced), then the mulligan. Each round:

1. **Mythos** (from round 2): the treachery test, if you chose one.
2. **Investigation**: 3 actions (more with Leo De Luca). Weaknesses drawn take their
   actions first. Then, in order of preference:
   - play an asset that fits a free slot and is affordable. Assets that help the spare
     action come first, then economy cards, then the rest. A weapon or tool that has
     run out of uses is discarded to make room. Fast assets and events don't use an
     action.
   - play an event that helps now: resources when low or when an asset is
     unaffordable, card draw when the hand is small, clues or damage that match the
     spare action.
   - draw a card if the hand has 0–1 cards.
   - gain a resource when an asset is at most 2 resources out of reach (using Dario
     El-Amin and the like if in play).
   - otherwise take the spare action, in the way that scores best: chance of passing
     times what success gives, less what it uses up (ammo, an event card).
3. **Upkeep**: ready everything, draw 1, gain 1 resource, discard down to 8 cards.

**Skill tests**: skill + constant bonuses + the ability's bonus, then commits and
resource "pumps" by the commit policy (skills first, then pumps while keeping 2
resources, then events and assets that can't be played anyway; never more than one
"Max 1 committed" card of a name; nothing if even everything wouldn't reach the
difficulty). Then a token: auto-fail fails, the elder sign uses the investigator's
printed `+N`, symbols use your setting. A test that fails by 2 or less can be saved by
Lucky! in hand. Successful investigations give a clue, attacks 1 damage, evasions an
evade, plus whatever the cards add.

## What is and isn't simulated

Simulated: drawing, resources, actions, slots, uses (ammo, charges, supplies), skill
tests with commits and the chaos bag, the effects in the card table, weaknesses costing
actions, reshuffling when the deck runs out.

Not simulated: enemies attacking, damage and horror (and so healing and soak),
movement and locations, the encounter deck beyond one treachery test a round,
investigator abilities (other than the elder sign), weakness effects, other players,
taboo lists, customizable upgrades, bless and curse tokens.

## Card effects

Every card counts for its skill icons (wild icons count for every skill), its cost and
its slots. That's enough for skill cards with no text, like Unexpected Courage. Cards
whose text does more get their effects from a table,
`tcgnecro/games/ahlcg/sim_effects.json`, keyed by ArkhamDB code. It starts with about
180 common cards: weapons and tools, bonus assets, the "spend 1 resource: +1" assets,
draw and resource events, the commit-for-a-reward skills, and so on. Cards that aren't
in it are listed on the results page as counted only for their icons, cost and slots.

Reprints use the original card's code (Revised Core's Machete is `01020`, the Core Set
code), because the deck builder already maps them.

### Adding or changing card effects on your server

Put a JSON file at `data/custom/ahlcg/sim_effects.json` (next to custom cards; see
[CUSTOM_CARDS.md](CUSTOM_CARDS.md)). It's laid over the bundled table:

```json
{
  "05024": {"name": "Fingerprint Kit", "uses": 3,
            "abilities": [{"action": "investigate", "bonus": 1, "use": 1, "exhaust": true,
                           "success": {"clues": 1}}]},
  "90123": {"name": "My Homebrew Lantern", "action_bonus": {"investigate": {"intellect": 2}}},
  "01020": null
}
```

An entry replaces the bundled one for that code, `null` removes it (the card then counts
for its icons only), and codes of custom cards work too. An entry with a mistake is
skipped and named in the results page's warnings; the rest still load. Changes apply to
the next run, with no restart. Entries that are right for everyone belong in the bundled
file; send them upstream.

### Format

Each entry is an object. `name` (the card's name) and `note` (an approximation worth
mentioning; shown in the results) are for people. Everything else is optional:

**Assets in play**

| Key | Example | Meaning |
|---|---|---|
| `skill_bonus` | `{"willpower": 1}` | Always +1 willpower (Holy Rosary) |
| `action_bonus` | `{"investigate": {"intellect": 1}}` | Bonus during one kind of test: `investigate`, `fight`, `evade`, `treachery` (Magnifying Glass) |
| `if_controls` | `["Tome", "Relic"]` | The bonuses apply only while you control another asset with one of these traits (Whitton Greene) |
| `uses` | `4` | Starting ammo, charges, supplies... |
| `abilities` | `[{...}]` | Actions the card offers; see below |
| `pump` | `{"skills": ["intellect", "agility"], "cost": 1, "value": 1}` | Spend resources during a test for +value (Hyperawareness). Also: `when` (list of test kinds), `value_when` (`{"evade": 2}`), `min_hand` (Higher Education), `free` (free uses each round, level 4 versions), `use` (spend uses instead), `max` (per test) |
| `reactions` | `[{"after": "success", "when": "investigate", "resources": 1}]` | After a test passes or fails: gain a result. Also `by` (succeed by at least N) and `exhaust` (once per round) (Dr. Milan Christopher, Rabbit's Foot) |
| `turn_start` | `{"resources": 1}` | At the start of each turn (Lone Wolf) |
| `extra_actions` | `1` | Extra actions each turn (Leo De Luca) |
| `enter` | `{"draw": 2}` | When it enters play (Laboratory Assistant) |
| `hand_size` | `2` | Raises the hand limit |
| `slots` | `{"ally": 1}` | Extra slots (Charisma) |
| `opening_play` | `true` | Put into play from the opening hand (Death • XIII) |

**Abilities** (`abilities` on assets, `test` on events)

| Key | Meaning |
|---|---|
| `action` | `investigate`, `fight`, `evade`, or `resources` (an action that gains resources, like Dario El-Amin) |
| `skill` | Use this skill instead, e.g. `"willpower"`; a list means the better of them (Thieves' Kit) |
| `base` | Use this base skill value (Duke: 4) |
| `add_skill` | Add another skill's base value (Polished Cane: agility) |
| `bonus` | +N for this test |
| `actions` | Actions it costs (default 1; Sledgehammer's big swing is 2) |
| `use` | Uses it spends |
| `exhaust` | Once per round |
| `discard` | Discards the card (Knife's second ability) |
| `discard_on` | Discarded if one of these tokens is revealed: `skull`, `cultist`, `tablet`, `elder_thing`, `auto_fail` (Baseball Bat) |
| `end_turn_on` | The turn ends if one of these tokens is revealed (Rite of Seeking) |
| `success` | Result on success, on top of the normal clue or damage, e.g. `{"damage": 1}` |
| `better` | Result instead of `success` when succeeding by at least `by`: `{"by": 2, "damage": 1}` |
| `instead` | `true`: success gives only `success`, not the normal clue or damage (Burglary) |
| `damage_by_margin` | `[1, 5]`: damage equals the margin of success, within these bounds (Shotgun) |
| `retry` | `{"bonus": 1}`: on failure, test again once a round with this bonus instead (Old Compass) |
| `return_by` | Events: return to hand when succeeding by at least N (Pilfer level 3) |

**Events**

| Key | Meaning |
|---|---|
| `play` | Result when played: `{"resources": 3}` (Emergency Cache), `{"draw": 3}`, `{"clues": 1}`, `{"damage": 1}` |
| `test` | The event is a test action: an ability as above (Backstab: `{"action": "fight", "skill": "agility", "success": {"damage": 2}}`) |
| `trigger` | Held until something happens: `defeat` (after you defeat an enemy: Evidence!) or `fail` (when a test would fail: Lucky!) |
| `rescue` | With `trigger: fail`, +N to save a failing test |

**Skills**

| Key | Meaning |
|---|---|
| `commit` | What happens when committed: `success`, `better`, `fail` results; `when` limits them to one kind of test (Deduction: `investigate`); `return_by` (Opportunist); `return_on_fail` |
| `commit_only` | Only commit it to this kind of test (Inquiring Mind: `investigate`) |

A **result** is an object with any of `clues`, `damage`, `draw`, `resources` and
`actions` (an extra action this turn, as with Quick Thinking).

"Fast." in a card's text makes it free to play, and "Max 1 committed per skill test."
is read from the text too; neither needs an entry.

## Chaos bags

The **Chaos bag** list comes from `tcgnecro/games/ahlcg/chaos_bags.toml`: Night of the
Zealot and The Dunwich Legacy at each difficulty. Check them against your campaign
guide; to add a campaign for everyone, add a `[[bags]]` entry there (or type the bag
in on the page).

## For developers

| File | Role |
|---|---|
| `deckbuilder/simulate.py` | Game-agnostic: `SimSetting` (form fields), `parse_settings`, the report types (`SimReport` → `SimSection` → `SimStats` / `SimTable` / `SimChart` / `SimNotes`), percentile helpers, the SVG band chart, and `compare_reports` (two reports side by side, with `delta` for the changes) |
| `deckbuilder/plugin.py` | `sim_settings(deck, pool)` (empty: no simulator, no button) and `simulate(deck, pool, values)` hooks |
| `deckbuilder/routes.py` | `GET /decks/{id}/simulate` (the page) and `GET /decks/{id}/simulate/run` (the results fragment; sets `HX-Replace-Url` so the address keeps the settings; `?vs=<deck id>` compares) |
| `deckbuilder/templates/simulate.html`, `_simulation.html` | The page and the results, rendered block by block |
| `games/ahlcg/simulate.py` | The Arkham engine: effects loading and checking, cards, the chaos bag, `Run` (one game), `Stats`, the report |
| `games/ahlcg/sim_effects.json`, `chaos_bags.toml` | Data |

The engine's docstring is the map. In short: `Setup` holds everything fixed for one
simulation (including the bag as a table of pass chances by skill-minus-difficulty);
`Run` plays one game, round by round; `Run.plan` is the commit policy (used both for real
tests and for the probes, without changing anything); `Run.probe` records the exact pass
chances at the start of each turn; `Stats` accumulates; `build_report` turns it into
sections.

Speed: about 1.5 s for 1,000 ten-round games on a desktop CPU, in a worker thread. Runs
are capped at 5,000. Most of the time goes to the probes (each challenge × 6
difficulties × every turn).

Another game can add a simulator by implementing the two hooks and returning a
`SimReport`; the page, form and charts come for free.

Tests: `tests/test_ahlcg_simulate.py` (bag parsing and pass chances, the effects table
and server overrides, determinism, policy behaviour, comparing two decks, the web routes).
