"""Collections are plain folders: data/collections/{game}/{name}/ holding a
manifest.toml and the card images. A collection archive is the same thing zipped up
(with the images under images/), so importing one just unzips it."""

import shutil
import sqlite3
import tomllib
import zipfile
from pathlib import Path

from tcgnecro import db
from tcgnecro.games import GAME_MODULES
from tcgnecro.naming import parse_filename, validate

MANIFEST = "manifest.toml"


class CollectionError(Exception):
    pass


def import_archive(archive_path: Path, name: str | None = None, progress=print) -> Path:
    name = name or archive_path.stem
    with zipfile.ZipFile(archive_path) as zf:
        manifest = tomllib.loads(zf.read(MANIFEST).decode())
        game_id = manifest["game"]
        if game_id not in GAME_MODULES:
            raise CollectionError(f"{archive_path.name} is for game '{game_id}', which isn't supported yet")

        members = [m for m in zf.infolist() if m.filename.startswith("images/") and not m.is_dir()]
        parsed = [parse_filename(Path(m.filename).name) for m in members]
        errors = validate([p for p in parsed if p])
        if errors:
            raise CollectionError("\n".join([f"{archive_path.name} failed validation:"] + errors))

        target = db.collections_dir() / game_id / name
        if target.exists():
            raise CollectionError(f"Collection folder already exists: {target}")
        target.mkdir(parents=True)

        try:
            (target / MANIFEST).write_bytes(zf.read(MANIFEST))
            for i, member in enumerate(members, 1):
                with zf.open(member) as src, open(target / Path(member.filename).name, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if i % 100 == 0 or i == len(members):
                    progress(f"  extracted {i}/{len(members)}")
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
    return target


def remove(name: str) -> None:
    for folder in collection_folders():
        if folder.name == name:
            shutil.rmtree(folder)
            return
    raise CollectionError(f"No collection named '{name}'")


def collection_folders() -> list[Path]:
    root = db.collections_dir()
    if not root.is_dir():
        return []
    return sorted(p for game in root.iterdir() if game.is_dir() for p in game.iterdir() if p.is_dir())


def read_manifest(folder: Path) -> dict:
    """A folder without a manifest is still usable; the game comes from the
    parent folder's name, as in upstream's extracted layout."""
    path = folder / MANIFEST
    manifest = tomllib.loads(path.read_text()) if path.exists() else {}
    manifest.setdefault("game", folder.parent.name)
    return manifest


def _printing_lookups(conn: sqlite3.Connection, game_id: str):
    by_card_pack, by_api_id = {}, {}
    for row in conn.execute(
        "SELECT card_id, pack_id, position, api_id FROM card_versions WHERE game_id = ?", (game_id,)
    ):
        by_card_pack.setdefault((row["card_id"], row["pack_id"]), row)
        if row["api_id"]:
            by_api_id.setdefault(row["api_id"], row)
    return by_card_pack, by_api_id


def _match_printing(card_id, printing, by_card_pack, by_api_id):
    """Port of upstream's resolve_card_and_version. The filename's card id may
    be a per-printing id; the printing part is a pack id when it matches the
    catalog, and a free-form variant otherwise. Returns (card_id, version row)."""
    hit = by_api_id.get(card_id)
    if hit and hit["pack_id"] == printing:
        return hit["card_id"], hit
    version = by_card_pack.get((card_id, printing))
    if version:
        return card_id, version
    if hit:
        return hit["card_id"], None
    return card_id, None


def reindex(conn: sqlite3.Connection, warn=print) -> dict[str, int]:
    """Rebuild the image index from the collection folders on disk."""
    counts = {}
    with conn:
        conn.execute("DELETE FROM images")
        conn.execute("DELETE FROM collections")
        lookups = {}
        for folder in collection_folders():
            manifest = read_manifest(folder)
            game_id = manifest["game"]
            if game_id not in GAME_MODULES:
                warn(f"Skipping {folder}: game '{game_id}' isn't supported")
                continue
            if game_id not in lookups:
                lookups[game_id] = _printing_lookups(conn, game_id)

            files = [(f, parse_filename(f.name)) for f in sorted(folder.iterdir())]
            files = [(f, p) for f, p in files if p]
            for error in validate([p for _, p in files]):
                warn(f"{folder.name}: {error}")

            rows = []
            for f, p in files:
                card_id, version = _match_printing(p.card_id, p.printing, *lookups[game_id])
                rows.append((
                    folder.name, game_id, card_id,
                    version["pack_id"] if version else None,
                    None if version else p.printing,
                    version["position"] if version else None,
                    p.side, int(p.has_bleed), str(f),
                ))
            conn.executemany("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.execute(
                "INSERT INTO collections VALUES (?, ?, ?, ?)",
                (folder.name, game_id, manifest.get("version"), manifest.get("language")),
            )
            counts[folder.name] = len(rows)
    return counts
