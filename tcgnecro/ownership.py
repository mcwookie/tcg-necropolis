"""Which cards someone owns, from which packs they own.

Ownership is recorded per pack (users.owned_packs) and works out to cards
through the catalog's card *versions*, not each card's original pack. That
matters for Arkham's repackaged boxes: owning "The Dunwich Legacy
Investigator Expansion" owns the player cards ArkhamDB files under the
original Dunwich packs, because load_catalog gives those cards versions in the
repackaged box. Reprint codes (Revised Core's Machete) are mapped onto the
card the deck builder shows through the pool's aliases.

This is game-agnostic: any game whose catalog has packs gets a Collection page
and, if its deckbuilder.toml has an `owned` toggle filter, "Only cards I own".
"""

import sqlite3

from tcgnecro import users


def owned_card_ids(conn: sqlite3.Connection, game_id: str, packs: set[str]) -> set[str]:
    """Catalog card ids with at least one version in an owned pack."""
    if not packs:
        return set()
    marks = ",".join("?" * len(packs))
    rows = conn.execute(
        f"SELECT DISTINCT card_id FROM card_versions WHERE game_id = ? AND pack_id IN ({marks})",
        [game_id, *sorted(packs)],
    )
    return {r[0] for r in rows}


def owned_in_pool(conn: sqlite3.Connection, user, game_id: str, pool) -> set[str] | None:
    """Pool card ids `user` owns, or None if they haven't set up a collection
    for this game (so ownership filters should do nothing)."""
    packs = users.owned_packs(user, game_id)
    if packs is None:
        return None
    return {pool.resolve(cid) for cid in owned_card_ids(conn, game_id, packs)}


def pack_groups(conn: sqlite3.Connection, game_id: str) -> list[tuple[str, list[sqlite3.Row]]]:
    """[(group name, packs)] for the Collection page, each pack with a count
    of the distinct cards it holds."""
    rows = conn.execute(
        """SELECT p.id, p.name, p.sort_key, p.group_name, COALESCE(p.group_order, 0) AS group_order,
                  COUNT(DISTINCT v.card_id) AS cards
           FROM packs p LEFT JOIN card_versions v ON v.game_id = p.game_id AND v.pack_id = p.id
           WHERE p.game_id = ?
           GROUP BY p.id
           ORDER BY group_order, p.sort_key IS NULL, p.sort_key, p.name""",
        (game_id,),
    ).fetchall()
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row["group_name"] or "Packs", []).append(row)
    return list(groups.items())
