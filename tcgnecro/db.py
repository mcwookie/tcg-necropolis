"""SQLite index. Everything in here can be rebuilt: the catalog from the game
modules, and the image index by scanning the collection folders."""

import os
import sqlite3
import unicodedata
from pathlib import Path

from tcgnecro.games import Catalog

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packs (
    game_id TEXT NOT NULL,
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    sort_key TEXT,
    PRIMARY KEY (game_id, id)
);
CREATE TABLE IF NOT EXISTS cards (
    game_id TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    title_normalized TEXT NOT NULL,
    back_group TEXT,
    PRIMARY KEY (game_id, id)
);
CREATE INDEX IF NOT EXISTS cards_by_title ON cards (game_id, title_normalized);
CREATE TABLE IF NOT EXISTS card_versions (
    game_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    position INTEGER,
    api_id TEXT
);
CREATE INDEX IF NOT EXISTS versions_by_card ON card_versions (game_id, card_id);
CREATE TABLE IF NOT EXISTS collections (
    name TEXT PRIMARY KEY,
    game_id TEXT NOT NULL,
    version TEXT,
    language TEXT
);
-- One row per image file. A printing that matched a catalog version has
-- pack_id set; anything else is a free-form variant (an alt art, a fan card).
CREATE TABLE IF NOT EXISTS images (
    collection TEXT NOT NULL,
    game_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    pack_id TEXT,
    variant TEXT,
    position INTEGER,
    side TEXT NOT NULL,
    has_bleed INTEGER NOT NULL,
    file_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS images_by_card ON images (game_id, card_id);
"""


def data_dir() -> Path:
    return Path(os.environ.get("TCGNECRO_DATA", Path.cwd() / "data"))


def collections_dir() -> Path:
    return data_dir() / "collections"


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir() / "index.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def normalize_title(title: str) -> str:
    """Upstream uses the `deunicode` crate here; NFKD + ASCII is close but not
    identical for a few characters (e.g. ß, æ)."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "_" for c in ascii_title.lower())


def store_catalog(conn: sqlite3.Connection, catalog: Catalog) -> None:
    g = catalog.game_id
    with conn:
        for table in ("packs", "cards", "card_versions"):
            conn.execute(f"DELETE FROM {table} WHERE game_id = ?", (g,))
        conn.execute("INSERT OR REPLACE INTO games VALUES (?, ?)", (g, catalog.name))
        conn.executemany(
            "INSERT INTO packs VALUES (?, ?, ?, ?)",
            [(g, p.id, p.name, p.sort_key) for p in catalog.packs],
        )
        conn.executemany(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?)",
            [(g, c.id, c.title, normalize_title(c.title), c.back_group) for c in catalog.cards],
        )
        conn.executemany(
            "INSERT INTO card_versions VALUES (?, ?, ?, ?, ?)",
            [(g, v.card_id, v.pack_id, v.position, v.api_id) for v in catalog.versions],
        )
