"""Star Wars LCG deck builder rules.

A Star Wars LCG deck is built from objective sets, not single cards: each set
is an objective and the five command cards printed with it, always together.
So the deck builder's "cards" are objective sets: the pool holds one entry per
set (its objective card, carrying the whole set in attrs["members"]), and the
deck's slots count sets. Printing expands each set back into its six cards.

Rules (Core Set rulebook, deck construction):

- Choose an affiliation card; it fixes the deck's side (light or dark).
- At least 10 objective sets, all from that side. Their affiliation needn't
  match, unless the objective says "[affiliation] affiliation only"
  (affiliation_lock).
- At most 2 copies of a set, or 1 if its objective says "Limit 1 per
  objective deck" (is_limited).

Balance of the Force's multiplayer objectives (the cards outside numbered sets)
follow that expansion's own rules and aren't offered here.
"""

from pathlib import Path

from tcgnecro.deckbuilder.model import Deck, DeckCard, Pool, Problem, Stat
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin

MIN_SETS = 10
MAX_COPIES = 2
AFFILIATION_KEYS = {
    "Jedi": "jedi", "Rebel Alliance": "rebel", "Smugglers and Spies": "smugglers",
    "Sith": "sith", "Imperial Navy": "navy", "Scum and Villainy": "scum", "Neutral": "neutral",
}


def _pack_name(code: str | None) -> str:
    """"darkness-and-light" -> "Darkness and Light" (pack names aren't in the card data)."""
    words = (code or "").split("-")
    small = {"and", "of", "the", "to", "or", "in", "from"}
    return " ".join(w if i and w in small else w.capitalize() for i, w in enumerate(words))


def _int(value) -> int:
    """A printed number, or 0 for none and for "X" (one resource and two damage values)."""
    return value if isinstance(value, int) else 0


def _icons(icons: dict | None) -> str:
    if not icons:
        return ""
    return " ".join(f"{label} {icons[k]}" for k, label in (("unit", "U"), ("blast", "B"), ("tactics", "T"))
                    if icons.get(k))


class SwlcgPlugin(DeckbuilderPlugin):

    # ----- card pool --------------------------------------------------------

    def build_card(self, card_id: str, title: str, raw: dict) -> DeckCard | None:
        kind = raw.get("card_type")
        if kind == "Affiliation":
            # Three printings per affiliation: the Core Set card ("Jedi") and
            # alternates named otherwise ("Guardians of Justice").
            name = raw["name"] if raw["name"] == raw.get("affiliation") else f"{raw['name']} ({raw.get('affiliation')})"
        elif kind == "Objective" and raw.get("objective_set_number"):
            name = f"{raw['name']} ({raw['objective_set_number']})"
        elif raw.get("objective_set_number"):
            # A command card: never chosen on its own, but in the pool so a
            # deck's PDF and image checks find it (see print_list).
            name, kind = raw["name"], "Command"
        else:
            return None   # Balance of the Force's multiplayer cards
        attrs = {
            "name": name,
            "kind": kind.lower(),
            "side": raw["side"].lower(),
            "affiliation": AFFILIATION_KEYS.get(raw.get("affiliation"), "neutral"),
            "set_number": raw.get("objective_set_number"),
            "locked": AFFILIATION_KEYS.get(raw.get("affiliation_lock") or "", None),
            "limited": bool(raw.get("is_limited")),
            "text": raw.get("text") or "",
            "traits": ", ".join(raw.get("traits") or []),
            "pack": raw.get("pack_code"),
            "members": [],
        }
        return DeckCard(card_id, title, name, attrs, raw)

    def build_pool(self, rows):
        pool = super().build_pool(rows)
        by_set = {c.get("set_number"): c for c in pool.cards.values() if c.get("kind") == "objective"}
        commands = sorted((c for c in pool.cards.values() if c.get("kind") == "command"),
                          key=lambda c: c.raw.get("objective_set_sequence") or 0)
        for card in commands:
            raw = card.raw
            objective = by_set.get(raw.get("objective_set_number"))
            if objective is None:
                continue
            objective.attrs["members"].append({
                "id": card.id, "title": card.title, "name": raw["name"], "type": raw.get("card_type"),
                "cost": raw.get("cost"), "force": _int(raw.get("force_icons")),
                "resources": _int(raw.get("resource_value")), "unique": bool(raw.get("is_unique")),
                "combat": _icons(raw.get("combat_icons")), "edge": raw.get("edge_priority"),
                "text": raw.get("text") or "",
            })
        for objective in by_set.values():
            members = objective.attrs["members"]
            # Searchable and filterable by what's in the set, not just the objective.
            objective.attrs["text"] = " ".join([objective.get("text")] + [m["name"] + " " + m["text"] for m in members])
            objective.attrs["traits"] = objective.get("traits")
            objective.attrs["types"] = sorted({m["type"].lower() for m in members})
            objective.attrs["units"] = sum(1 for m in members if m["type"] == "Unit")
            objective.attrs["force"] = sum(m["force"] for m in members)
            objective.attrs["resources"] = _int(objective.raw.get("resource_value")) + sum(m["resources"] for m in members)
            objective.attrs["summary"] = " · ".join(
                f"{n} {t}{'s' if n > 1 else ''}" for t in ("Unit", "Event", "Enhancement", "Fate", "Mission")
                if (n := sum(1 for m in members if m["type"] == t)))
        return pool

    def identities(self, pool: Pool) -> list[DeckCard]:
        return sorted((c for c in pool.cards.values() if c.get("kind") == "affiliation"),
                      key=lambda c: (c.get("side") != "light", c.name))

    # ----- building ---------------------------------------------------------

    def _identity(self, deck: Deck, pool: Pool) -> DeckCard | None:
        return pool.get(deck.identity) if deck.identity else None

    def is_legal(self, card: DeckCard, deck: Deck, pool: Pool) -> bool:
        identity = self._identity(deck, pool)
        if card.get("kind") != "objective" or identity is None:
            return False
        if card.get("side") != identity.get("side"):
            return False
        return card.get("locked") in (None, identity.get("affiliation"))

    def max_copies(self, card: DeckCard, deck: Deck) -> int:
        return 1 if card.get("limited") else MAX_COPIES

    def validate(self, deck: Deck, pool: Pool) -> list[Problem]:
        identity = self._identity(deck, pool)
        if identity is None:
            return [Problem("Choose an affiliation")]
        problems = []
        unknown = [cid for cid in deck.slots if pool.get(cid) is None]
        if unknown:
            problems.append(Problem(f"Unknown objective sets: {', '.join(unknown)}", card_ids=unknown))
        sets = sum(q for cid, q in deck.slots.items() if (c := pool.get(cid)) and c.get("kind") == "objective")
        if sets < MIN_SETS:
            problems.append(Problem(f"Deck has {sets} objective sets; it needs at least {MIN_SETS}"))
        for card_id, qty in sorted(deck.slots.items()):
            card = pool.get(card_id)
            if card is None:
                continue
            if card.get("kind") != "objective":
                problems.append(Problem(f"{card.name} isn't an objective set; add its set instead",
                                        card_ids=[card.id]))
                continue
            if card.get("side") != identity.get("side"):
                problems.append(Problem(f"{card.name} is a {card.get('side')} side set; "
                                        f"{identity.raw['name']} is {identity.get('side')} side", card_ids=[card.id]))
            elif card.get("locked") not in (None, identity.get("affiliation")):
                problems.append(Problem(f"{card.name} is {card.raw['affiliation_lock']} affiliation only",
                                        card_ids=[card.id]))
            if qty > self.max_copies(card, deck):
                why = " (limit 1 per objective deck)" if card.get("limited") else ""
                problems.append(Problem(f"{qty} copies of {card.name}{why}", card_ids=[card.id]))
        return problems

    def stats(self, deck: Deck, pool: Pool) -> list[Stat]:
        chosen = [(c, q) for cid, q in deck.slots.items() if (c := pool.get(cid)) and c.get("kind") == "objective"]
        sets = sum(q for _, q in chosen)
        members = [(m, q) for c, q in chosen for m in c.get("members")]
        costs = [m["cost"] for m, q in members if m["type"] != "Fate" and isinstance(m["cost"], int) for _ in range(q)]
        return [
            Stat("Objective sets", f"{sets} / {MIN_SETS}+", sets >= MIN_SETS),
            Stat("Command cards", str(sum(q for _, q in members))),
            Stat("Units", str(sum(q for m, q in members if m["type"] == "Unit"))),
            Stat("Force icons", str(sum(m["force"] * q for m, q in members)),
                 title="Force icons on the command cards"),
            Stat("Resources", str(sum(c.get("resources") * q for c, q in chosen)),
                 title="Resource value of the objectives and command cards"),
            Stat("Avg cost", f"{sum(costs) / len(costs):.1f}" if costs else "–"),
        ]

    # ----- display ----------------------------------------------------------

    def subtitle(self, card: DeckCard) -> str:
        if card.get("kind") == "affiliation":
            return f"{card.get('side').title()} side · {_pack_name(card.get('pack'))}"
        return card.get("summary") or ""

    def detail_fields(self, card: DeckCard) -> list[tuple[str, str]]:
        raw = card.raw
        fields = [
            ("Side", raw.get("side") or ""),
            ("Affiliation", raw.get("affiliation") or ""),
            ("Affiliation only", raw.get("affiliation_lock") or ""),
            ("Limit", "1 per objective deck" if card.get("limited") else ""),
            ("Objective", f"resources {raw.get('resource_value')}, damage {raw.get('damage_capacity')}"
             if card.get("kind") == "objective" else ""),
            ("Pack", _pack_name(raw.get("pack_code"))),
        ]
        for m in card.get("members"):
            bits = [m["type"]]
            if m["cost"] is not None:
                bits.append(f"cost {m['cost']}")
            if m["force"]:
                bits.append(f"{m['force']} force")
            if m["resources"]:
                bits.append(f"{m['resources']} resource")
            if m["combat"]:
                bits.append(m["combat"])
            if m["edge"] is not None:
                bits.append(f"edge {m['edge']}")
            fields.append((("✦ " if m["unique"] else "") + m["name"], " · ".join(bits)))
        return [(k, str(v)) for k, v in fields if v]

    def related_label(self) -> str:
        return "Cards in this set"

    def related_cards(self, card: DeckCard, pool: Pool) -> list[DeckCard]:
        """An objective set's five command cards, in set order."""
        return [m for member in card.get("members") if (m := pool.get(member["id"]))]

    # ----- printing ---------------------------------------------------------

    def print_list(self, deck: Deck) -> list[tuple[str, int]]:
        """The affiliation, then each set's six cards (objective first)."""
        from tcgnecro import db
        from tcgnecro.deckbuilder import registry
        conn = db.connect()
        try:
            pool = registry.pool(conn, self.game_id)
        finally:
            conn.close()
        out = [(deck.identity, 1)] if deck.identity else []
        for card_id, qty in deck.slots.items():
            card = pool.get(card_id)
            if card is None or card.get("kind") != "objective":
                continue
            out.append((card.id, qty))
            out += [(m["id"], qty) for m in card.get("members")]
        return out


PLUGIN = SwlcgPlugin("swlcg", Path(__file__).with_name("deckbuilder.toml"))
