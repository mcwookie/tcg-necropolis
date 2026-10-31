"""Collections are plain folders: data/collections/{game}/{name}/ holding a
manifest.toml and the card images. A collection archive (a Proxy Nexus .pnx,
or any .zip) is the same thing zipped up, so importing one just unzips it."""

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


# Files that zip tools add and nobody wants imported.
JUNK = (".DS_Store", "Thumbs.db", "desktop.ini")


def _is_junk(path: str) -> bool:
    return path.startswith("__MACOSX/") or Path(path).name in JUNK


def _archive_root(names: list[str]) -> str:
    """The archive's top level, or the single folder everything sits in (what
    "Compress folder" in a file manager produces). Returned as a prefix."""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def collection_name(archive_path: Path) -> str:
    """'ahlcg.pnx' -> 'ahlcg', 'scans.zip' -> 'scans', 'scans.pnx.zip' -> 'scans'."""
    name = archive_path.name
    for suffix in (".zip", ".pnx"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name


def import_archive(archive_path: Path, name: str | None = None, progress=print,
                   game: str | None = None) -> Path:
    """Unzip a collection archive into data/collections/{game}/{name}/.

    Accepts Proxy Nexus .pnx files and ordinary .zip files, laid out as
    manifest.toml + images/, either at the top of the archive or inside one
    wrapper folder. Images may also sit at that top level with no images/
    folder. Without a manifest, `game` says which game the cards belong to
    and a manifest is written. Files that don't follow the image naming
    convention are skipped (and counted); an archive with no usable card
    images is refused rather than imported empty.
    """
    name = name or collection_name(archive_path)
    with zipfile.ZipFile(archive_path) as zf:
        files = [m for m in zf.infolist() if not m.is_dir() and not _is_junk(m.filename)]
        root = _archive_root([m.filename for m in files])

        manifest_name = root + MANIFEST
        if manifest_name in zf.namelist():
            manifest_bytes = zf.read(manifest_name)
            manifest = tomllib.loads(manifest_bytes.decode())
            if game and manifest.get("game") != game:
                raise CollectionError(
                    f"{archive_path.name}'s manifest says game '{manifest.get('game')}', not '{game}'")
        elif game:
            manifest = {"game": game, "version": "1", "language": "en"}
            manifest_bytes = (f'game = "{game}"\nversion = "1"\nlanguage = "en"\n').encode()
        else:
            raise CollectionError(
                f"{archive_path.name} has no {MANIFEST}. Say which game its cards are for with "
                f"--game (one of: {', '.join(GAME_MODULES)})")

        game_id = manifest["game"]
        if game_id not in GAME_MODULES:
            raise CollectionError(f"{archive_path.name} is for game '{game_id}', which isn't supported yet")

        # Card images live in images/ under the root, or directly in the root.
        image_dir = root + "images/"
        if any(m.filename.startswith(image_dir) for m in files):
            candidates = [m for m in files if m.filename.startswith(image_dir)]
        else:
            candidates = [m for m in files if "/" not in m.filename.removeprefix(root)
                          and m.filename != manifest_name]
        members, skipped = [], []
        for m in candidates:
            (members if parse_filename(Path(m.filename).name) else skipped).append(m)
        if not members:
            raise CollectionError(
                f"{archive_path.name} has no card images named like 0235@core-set.png "
                f"({len(skipped)} other file(s) found). See the naming convention in docs/UPDATING_GAMES.md")

        errors = validate([parse_filename(Path(m.filename).name) for m in members])
        if errors:
            raise CollectionError("\n".join([f"{archive_path.name} failed validation:"] + errors))

        target = db.collections_dir() / game_id / name
        if target.exists():
            raise CollectionError(f"Collection folder already exists: {target}")
        target.mkdir(parents=True)

        try:
            (target / MANIFEST).write_bytes(manifest_bytes)
            for i, member in enumerate(members, 1):
                with zf.open(member) as src, open(target / Path(member.filename).name, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                if i % 100 == 0 or i == len(members):
                    progress(f"  extracted {i}/{len(members)}")
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
    if skipped:
        shown = ", ".join(Path(m.filename).name for m in skipped[:5]) + (" ..." if len(skipped) > 5 else "")
        progress(f"  skipped {len(skipped)} file(s) not named like card images: {shown}")
    return target


def remove(name: str, game: str | None = None) -> None:
    """Delete a collection folder. `game` is needed only when more than one
    game has a collection with that name."""
    matches = [f for f in collection_folders() if f.name == name and (game is None or f.parent.name == game)]
    if not matches:
        raise CollectionError(f"No collection named '{name}'" + (f" for {game}" if game else ""))
    if len(matches) > 1:
        raise CollectionError(f"More than one game has a collection named '{name}' "
                              f"({', '.join(f.parent.name for f in matches)}); say which with --game")
    shutil.rmtree(matches[0])


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


def reindex(conn: sqlite3.Connection, warn=print) -> dict[tuple[str, str], int]:
    """Rebuild the image index from the collection folders on disk.
    Returns {(game id, collection name): image count}."""
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
                    p.side, int(p.has_bleed), str(f.relative_to(db.collections_dir())),
                ))
            conn.executemany("INSERT INTO images VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            conn.execute(
                "INSERT INTO collections VALUES (?, ?, ?, ?)",
                (folder.name, game_id, manifest.get("version"), manifest.get("language")),
            )
            counts[(game_id, folder.name)] = len(rows)
        db.mark(conn, "images")
    return counts
