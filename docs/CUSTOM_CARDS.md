# Custom cards and scenarios

Add cards and packs that a game's official data doesn't have: fan-made scenarios and
campaigns, homebrew investigators or heroes, promos, a card missing from a database.
Custom cards work like official ones everywhere: the Proxies page, decklists, the deck
builder's rules and filters, the Collection page, image orientation.

## Where they go

One folder per game in the data folder (`data/` here; `TCGNECRO_DATA_DIR` for Docker,
which is the same shared folder):

```
data/custom/<game id>/packs.json    your packs (optional: cards may use an official pack)
data/custom/<game id>/cards.json    your cards
```

Game ids: `ahlcg`, `marvel_champions`, `swlcg`, `meccg`, `city_of_heroes`.

Each file is a JSON **list** of records. Records use **the game's own format**: the same
fields as its official data (ArkhamDB's for Arkham, MarvelCDB's for Marvel, the bundled
JSON's for the others). Copy an official card that resembles yours and change it; the
deck builder reads exactly the same fields for custom cards as for official ones.

Then:

1. Put the card images in any collection folder for the game, named as usual:
   `{card id}@{pack code}.jpg`, plus `{card id}@{pack code}~back.jpg` for a second face
   (e.g. `zz001@zzfan.jpg`). A new folder works: `data/collections/ahlcg/homebrew/`.
2. Reload: `tcgnecro catalog ahlcg` then `tcgnecro reindex`. With Docker, restart the
   app (`docker compose restart app`), which does both.

`tcgnecro catalog` reports `(including N custom)` per game, and prints a warning for each
custom record it skipped and why.

## Rules

- **Give custom cards their own ids.** Use a prefix the official data never uses, e.g.
  `zz001`, so a future official card can't collide. A custom card that reuses an official
  id is skipped (it never replaces an official card), as is one whose pack doesn't exist,
  or one missing a required field.
- **A pack code must be new**, or it's skipped. To add a card to an official pack, just
  use that pack's code on the card.
- **Names may repeat** official ones. Titles (what decklists match) are made unique
  automatically: Arkham and Marvel spell out the pack and position as they do for
  official duplicates; the other games add the card id.
- Problems never stop the official cards loading. If custom cards break a game's
  catalog in a way the checks didn't catch, the warning says so and only the official
  cards load.

## Arkham Horror (`ahlcg`): ArkhamDB's fields

Required on a card: `code`, `name`, `pack_code`, `position`, `type_code`. Required on a
pack: `code`, `name`.

`packs.json`, a fan campaign in its own group on the Collection page (packs that share a
`cycle_position` are grouped together):

```json
[
  {"code": "zzfan", "name": "The Fan Campaign", "available": "2026-11-01",
   "cycle_position": 900, "position": 1}
]
```

`cards.json`, a scenario's encounter cards (enough for printing), a player card and an
investigator (enough for the deck builder):

```json
[
  {"code": "zz001", "name": "Ghoul Horde", "pack_code": "zzfan", "position": 1,
   "type_code": "enemy", "encounter_code": "zz_ghouls", "quantity": 3},
  {"code": "zz002", "name": "Silver Dagger", "pack_code": "zzfan", "position": 2,
   "type_code": "asset", "faction_code": "guardian", "xp": 0, "cost": 2,
   "traits": "Item. Weapon. Melee.", "real_slot": "Hand", "skill_combat": 1,
   "deck_limit": 2, "quantity": 2, "text": "[action]: Fight. You get +1 [combat]..."},
  {"code": "zz003", "name": "Ada Lovelace", "subname": "The Enchantress of Numbers",
   "pack_code": "zzfan", "position": 3, "type_code": "investigator",
   "faction_code": "seeker", "health": 6, "sanity": 8, "traits": "Scholar.",
   "skill_willpower": 3, "skill_intellect": 5, "skill_combat": 2, "skill_agility": 2,
   "deck_requirements": {"size": 30, "card": {"zz004": {"zz004": "zz004"}},
                         "random": [{"target": "subtype", "value": "basicweakness"}]},
   "deck_options": [{"faction": ["seeker", "neutral"], "level": {"min": 0, "max": 5}},
                    {"faction": ["mystic"], "level": {"min": 0, "max": 2}}]},
  {"code": "zz004", "name": "Analytical Engine", "pack_code": "zzfan", "position": 4,
   "type_code": "asset", "faction_code": "neutral", "xp": 0, "deck_limit": 1,
   "restrictions": {"investigator": {"zz003": "zz003"}}}
]
```

- Encounter cards (`enemy`, `treachery`, `location`, `act`, `agenda`, `scenario`...) print
  with the encounter back. Acts, agendas and investigators show upright on screen like
  official ones.
- An investigator is offered in the deck builder when it has `deck_options`, written as
  ArkhamDB writes them; see docs/DECKBUILDER.md for what the rules engine understands.
  `deck_requirements.card` lists its signature cards, `restrictions` ties a card to it.
- Campaign story assets or weaknesses: give them an `encounter_code`, like official ones.

## Marvel Champions (`marvel_champions`): MarvelCDB's fields

Required on a card: `code`, `name`, `pack_code`, `position`, `type_code`. Required on a
pack: `code`, `name`.

```json
[
  {"code": "zz100", "name": "Fan Favorite", "pack_code": "zzm", "position": 1,
   "type_code": "ally", "faction_code": "justice", "cost": 3, "deck_limit": 1,
   "is_unique": true, "traits": "Avenger.", "text": "..."}
]
```

A homebrew hero needs a `hero` card and its alter ego as a hidden second face (`"hidden":
true`, linked with `linked_to_code` both ways), plus its hero cards with the hero's
`card_set_code` and `"card_set_type_name_code": "hero"`, as MarvelCDB lays out an official
hero. Its obligation and nemesis set use the same `card_set_code` (nemesis:
`<set>_nemesis`).

## Star Wars LCG (`swlcg`)

Required on a card: `unique_id`, `name`, `pack_code`, `card_type`, `side`. Required on a
pack: `code`, `name`, `position` (orders packs; use something high, like 99).

An objective set is an objective and five command cards sharing `objective_set_number`
(pick a number official sets don't use, e.g. 900 and up), numbered 1 to 6 in
`objective_set_sequence`:

```json
[
  {"unique_id": "9001", "name": "A New Recruit", "pack_code": "zz-sets", "card_type": "Objective",
   "side": "Light", "affiliation": "Jedi", "objective_set_number": 900, "objective_set_sequence": 1,
   "resource_value": 1, "damage_capacity": 5, "is_limited": false, "affiliation_lock": null},
  {"unique_id": "9002", "name": "Young Padawan", "pack_code": "zz-sets", "card_type": "Unit",
   "side": "Light", "affiliation": "Jedi", "objective_set_number": 900, "objective_set_sequence": 2,
   "cost": 2, "force_icons": 1}
]
```

The other fields (`traits`, `text`, `combat_icons`...) are listed in `games/swlcg/__init__.py`.

## Middle-earth CCG (`meccg`)

Required on a card: `unique_id`, `name`, `pack_code`, `card_type` (Character, Resource,
Hazard, Site), `alignment`, `card_number`. Required on a pack: `code`, `name`,
`position`. The deck builder's fields (`unique`, `avatar`, `agent`, `mind`, `subtype`,
`site_type`...) are listed in `games/meccg/__init__.py`.

```json
[
  {"unique_id": "zz001", "name": "Fan Ranger", "pack_code": "the-wizards", "card_type": "Character",
   "alignment": "Hero", "card_number": 900, "unique": true, "race": "Dúnadan",
   "skills": "Warrior/Ranger", "mind": 4, "marshalling_points": 1}
]
```

## City of Heroes (`city_of_heroes`)

Required on a card: `id`, `name`, `pack_code`, `number`, `type` (hero, sidekick, power,
sig power, enhancement, edge, mission). Required on a pack: `code`, `name`, `position`.
Fields are listed in `games/city_of_heroes/__init__.py`.

```json
[
  {"id": "fan_001", "name": "Night Owl", "pack_code": "fan", "number": 1, "type": "hero",
   "archetype": "Scrapper", "powersets": ["Martial Arts", "Super Reflexes"],
   "starting_powers": ["Storm Kick", "Focused Fighting"], "accuracy": 4, "defense": 4, "health": 18},
  {"id": "fan_002", "name": "Owl's Talon", "pack_code": "fan", "number": 2, "type": "sig power",
   "powersets": ["Martial Arts"], "signature_hero": "Night Owl", "recharge": 1}
]
```

## Other games, and games added later

A game module that loads its data in two steps (`load_source` then `build_catalog`; see
[ADDING_A_GAME.md](ADDING_A_GAME.md)) takes custom cards in its own format automatically.
A module that only has `load_catalog` takes a generic format instead:

```json
packs.json  [{"code": "fan", "name": "Fan Pack", "sort_key": "99"}]
cards.json  [{"id": "f1", "name": "Card name", "pack": "fan", "position": 1,
              "back_group": "player", "any": "other field, kept for a deck builder"}]
```
