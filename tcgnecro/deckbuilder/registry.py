"""Finds game plugins and caches each game's card pool.

A game has a deck builder when tcgnecro/games/<id>/deckbuilder.py exists and
defines PLUGIN. Pools are built from the catalog in index.db and rebuilt
automatically when `tcgnecro catalog` reloads the game (games.loaded_at
changes) or `tcgnecro reindex` runs (index_state "images"), even while the
server is running.
"""

import json
import sqlite3
import threading
from importlib import import_module

from tcgnecro import db
from tcgnecro.deckbuilder.model import Pool
from tcgnecro.deckbuilder.plugin import DeckbuilderPlugin
from tcgnecro.games import GAME_MODULES, display_rotation

_plugins: dict[str, DeckbuilderPlugin] | None = None
_pools: dict[str, tuple[tuple, Pool]] = {}
_lock = threading.Lock()


def plugins() -> dict[str, DeckbuilderPlugin]:
    global _plugins
    if _plugins is None:
        found = {}
        for game_id in GAME_MODULES:
            try:
                module = import_module(f"tcgnecro.games.{game_id}.deckbuilder")
            except ModuleNotFoundError as e:
                if e.name != f"tcgnecro.games.{game_id}.deckbuilder":
                    raise  # a real import error inside the plugin
                continue
            found[game_id] = module.PLUGIN
        _plugins = found
    return _plugins


def plugin(game_id: str) -> DeckbuilderPlugin:
    try:
        return plugins()[game_id]
    except KeyError:
        raise KeyError(f"No deck builder for game '{game_id}'") from None


def pool(conn: sqlite3.Connection, game_id: str) -> Pool:
    loaded = conn.execute("SELECT loaded_at FROM games WHERE id = ?", (game_id,)).fetchone()
    version = (loaded[0] if loaded else None, db.marked(conn, "images"))
    with _lock:
        cached = _pools.get(game_id)
        if cached and cached[0] == version:
            return cached[1]

    rows = [
        (r["id"], r["title"], json.loads(r["attrs"]))
        for r in conn.execute(
            "SELECT id, title, attrs FROM cards WHERE game_id = ? AND attrs IS NOT NULL", (game_id,)
        )
    ]
    built = plugin(game_id).build_pool(rows)
    for card in built.cards.values():
        card.rotate = display_rotation(game_id, card.raw, "front")

    # A thumbnail per pool card: any front image of the card itself or of a
    # card that aliases to it (a reprint), preferring images without bleed.
    for r in conn.execute(
        "SELECT card_id, file_path FROM images WHERE game_id = ? AND side = 'front' ORDER BY has_bleed DESC",
        (game_id,),
    ):
        card_id = built.resolve(r["card_id"])
        if card_id in built.cards:
            built.images[card_id] = r["file_path"]

    with _lock:
        _pools[game_id] = (version, built)
    return built
