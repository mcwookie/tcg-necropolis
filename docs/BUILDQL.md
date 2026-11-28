# Card queries (BuildQL)

The **Query** box in the deck builder's filter panel takes a short expression that
narrows the card list further than the checkboxes can:

```
trait = "spell" & level >= 2
health + sanity >= 5 & trait == "ally"
slot ? ["hand", "arcane"] & cost <= 2
(class = "rogue" | class = "survivor") & text = "evade"
```

The language is a Python port of [arkham.build's BuildQL](https://github.com/arkham-build/arkham.build/blob/main/frontend/src/store/lib/buildql/buildql.md),
so Arkham queries written for that site mostly work here unchanged. It works in every
game's deck builder; only the field names differ.

The query runs **after** the other filters, over the cards that already pass them. If the
list looks too short, check that "Only cards my investigator can take" or a Type box isn't
hiding what you want. A query that doesn't parse leaves the list unfiltered and shows the
error above it. Queries stay in the page address, so you can bookmark or share one.

## Syntax

A query is one or more **conditions** joined with `&` (and) and `|` (or). `&` binds tighter
than `|`, so `a | b & c` means `a | (b & c)`; use parentheses to group.

A condition compares two sides: `field operator value`. Either side can be a field, a
number, quoted text (`"..."` or `'...'`), a `/regex/`, `null`, `true` or `false`.
**Unquoted words are field names**: write `trait = "spell"`, not `trait = spell`.

A field on its own is a condition too: `unique` means `unique = true`.

| Operator | Meaning |
|---|---|
| `=` `!=` | Text: every word on the right appears in the value, in any order and any case (`name = "breaking entering"` finds Breaking and Entering). Numbers and true/false: equal. |
| `==` `!==` | Exact. Text: the whole value, ignoring case (`trait == "ally"`). Rules-text fields: this exact phrase, punctuation and markup included (`text == "<b>Fight.</b>"`). |
| `?` `!?` | `=` against any item of a list: `trait ? ["tactic", "supply"]` |
| `??` `!??` | `==` against any item of a list |
| `>` `<` `>=` `<=` | Numbers only |
| `+` `-` `*` `/` `%` | Arithmetic: `health + sanity < 6`, `cost % 2 = 0` |

- **Several values:** some fields hold several values: Arkham traits, slots and classes;
  Marvel resource icons; City of Heroes powersets. A positive operator matches if *any*
  value matches. A negated one (`!=`, `!?`...) matches only if *none* does, so
  `trait != "item"` drops every card with the Item trait.
- **Missing values:** `field = null` finds cards with no value (`health = null`);
  `field != null` finds cards that have one. A missing value compared with `true`/`false`
  counts as false, and it never satisfies `>` or `<`.
- **Regular expressions:** `/.../`, case-insensitive, matched anywhere in the value unless
  anchored: `name = /^the/`, `trait = /^(spell|ritual)$/`, `text = /\d+ damage/`.
- **Number placeholders** (arkham.build's): `"x"` is X, `"*"` and `"?"` are * and ?, so
  `cost = "x"` finds X-cost cards. They need quotes, because a bare `x` is a field.
- Two fields can be compared: `health > sanity`.

## Fields

Every game has:

| Field | |
|---|---|
| `in_deck` | Copies of the card in this deck, all sections together: `in_deck > 0` |
| `owned` | In a pack you own. Empty (`null`) until you've set up your collection. |
| `pack` | Pack code or pack name: `pack = "dunwich"` |
| `id`, `code` | The card's id |

Each game's own fields, with descriptions and aliases, are listed under **How to write
queries** below the Query box. Any other key of a card's data also works by name, for
example Arkham's `skill_intellect`, Marvel's `card_set_name` or MECCG's `home_site`. To be
explicit about where a value comes from, prefix it: `raw.` reads the source data
(ArkhamDB, MarvelCDB, the bundled JSON) and `attrs.` reads the deck builder's own fields.
A name the game's cards never have is an error, which catches typos and unquoted text.

### Arkham Horror

Field names and aliases follow arkham.build, so `wp`, `cls`, `lvl`, `tr`, `sl` and the
one-letter legacy aliases work too. Supported: `name`, `subname`, `type`, `subtype`,
`faction`/`class` (all of a multiclass card's classes), `level`/`xp`, `cost`, `trait`,
`slot`, `text`, `flavor`, `willpower`, `intellect`, `combat`, `agility`, `wild`, `health`,
`sanity`, `unique`, `exceptional`, `myriad`, `permanent`, `customizable`, `multiclass`,
`heals_damage`, `heals_horror`, `tag`, `deck_limit`, `quantity`, `encounter_set`, `pack`.

Not supported, because the deck builder doesn't have the data or the feature:
card backs (`back:` fields), `taboo_set`, `cycle`, `illustrator`, `errata`, `bonded`,
`in_side_deck`, `favorite`, and enemy/location stats. `xp` is the printed level; it
doesn't account for chain or exceptional.

Text fields hold ArkhamDB's markup: `[action]`, `[reaction]`, `<b>Fight.</b>`. Use `==`
to match it exactly: `text == "[fast]"`.

### Other games

- **Marvel Champions:** `name`, `type`, `aspect`, `cost`, `trait`, `resources`
  (one value per icon: `resources = "energy"`), `energy`/`mental`/`physical`/`wild` (icon
  counts), `attack`/`atk`, `thwart`/`thw`, `defense`/`def`, `recover`/`rec`, `health`/`hp`,
  `unique`, `set` (card set code or name), `deck_limit`.
- **Star Wars LCG:** the list shows objective sets, so fields describe the whole set:
  `side`, `affiliation`, `set` (set number), `units`, `force`, `resources`, `type` (the card
  types in the set), `trait` (the objective's), `limited`, and `text` (every card's text).
- **Middle-earth CCG:** `type`, `kind`, `alignment`, `subtype`, `race`, `mind`, `di`,
  `prowess`, `body`, `mp`, `keyword`, `unique`, `rarity`, `text`.
- **City of Heroes:** `kind`, `powerset`, `archetype`, `inspiration`, `recharge`,
  `accuracy`, `defense`, `health`, `rarity`, `text`.

## For maintainers

The interpreter is `tcgnecro/deckbuilder/buildql.py` (lexer, recursive-descent parser,
evaluator, no dependencies). `routes._run_query` applies it after `filters.apply`, and
`registry.query_schema` caches each game's known field names and pack names alongside the
pool.

A game's field names live in the `[query]` table of its `deckbuilder.toml`:

```toml
[query]
text_fields = ["text", "flavor"]   # data keys where == is an exact substring match

[query.split]                      # data key -> separator, for multi-value strings
traits = "."                       # "Item. Weapon." -> ["Item", "Weapon"]

[query.fields]                     # query name -> where the value comes from
trait = "raw.traits"               # "raw.key", "attrs.key", or "key" (attrs, then raw)
tr = "raw.traits"                  # an alias is just another name for the same path
set = ["raw.card_set_code", "raw.card_set_name"]   # a list combines several values

[query.help]                       # what the in-page help lists, in order
trait = "Traits, one at a time"
```

The help shows each `[query.help]` entry with every other `[query.fields]` name that reads
the same data as its aliases. A value that needs computing, like Arkham's `multiclass`, goes
in the card's `attrs` in the game's `build_card`.
