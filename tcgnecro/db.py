"""SQLite index. Everything in here can be rebuilt: the catalog from the game
modules, and the image index by scanning the collection folders. User data
(decks) deliberately lives elsewhere, in data/decks/, so deleting index.db is
always safe: run `tcgnecro catalog` and `tcgnecro reindex` to rebuild it."""

import json
import os
from datetime import datetime, timezone
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


def thumb_url(path: str) -> str:
    """URL of the thumbnail for a collection image. Images are addressed by
    their path relative to the collections folder, so the thumbnail route
    can't be pointed at arbitrary files."""
    return "/thumb/" + str(Path(path).relative_to(collections_dir()))


# Schema changes after the first release, applied in order to existing
# databases. PRAGMA user_version records how many have run. Add new steps to
# the end; never edit or reorder old ones.
MIGRATIONS = [
    # 1: the deck builder needs each card's full data, and a way to notice
    # that a catalog was reloaded while the server was running.
    """
    ALTER TABLE cards ADD COLUMN attrs TEXT;
    ALTER TABLE games ADD COLUMN loaded_at TEXT;
    """,
]


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir() / "index.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {number}")
    conn.commit()


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
        conn.execute(
            "INSERT OR REPLACE INTO games (id, name, loaded_at) VALUES (?, ?, ?)",
            (g, catalog.name, datetime.now(timezone.utc).isoformat()),
        )
        conn.executemany(
            "INSERT INTO packs VALUES (?, ?, ?, ?)",
            [(g, p.id, p.name, p.sort_key) for p in catalog.packs],
        )
        conn.executemany(
            "INSERT INTO cards (game_id, id, title, title_normalized, back_group, attrs)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (g, c.id, c.title, normalize_title(c.title), c.back_group,
                 json.dumps(c.attrs) if c.attrs is not None else None)
                for c in catalog.cards
            ],
        )
        conn.executemany(
            "INSERT INTO card_versions VALUES (?, ?, ?, ?, ?)",
            [(g, v.card_id, v.pack_id, v.position, v.api_id) for v in catalog.versions],
        )
