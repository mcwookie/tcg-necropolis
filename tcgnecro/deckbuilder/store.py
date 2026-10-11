"""Deck storage: one JSON file per deck in data/decks/.

Files rather than a database table because decks are user data: they should
survive deleting index.db, be easy to back up or copy between machines, and
be readable without the app. Writes go to a temp file then rename, so a crash
mid-save never leaves a half-written deck.

Every change goes through `update()`, which loads, modifies and saves under a
lock. Edits are small (one card +1, one field) and applied to the latest
saved state, so two browser tabs editing one deck don't overwrite each
other's changes wholesale.
"""

import json
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from tcgnecro import db
from tcgnecro.deckbuilder.model import Deck, now

_lock = threading.Lock()


class DeckNotFound(KeyError):
    pass


def decks_dir() -> Path:
    return db.data_dir() / "decks"


def _path(deck_id: str) -> Path:
    if not deck_id.isalnum():
        raise DeckNotFound(deck_id)
    return decks_dir() / f"{deck_id}.json"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def load(deck_id: str) -> Deck:
    path = _path(deck_id)
    if not path.exists():
        raise DeckNotFound(deck_id)
    return Deck.from_json(json.loads(path.read_text()))


def save(deck: Deck) -> Deck:
    with _lock:
        return _write(deck)


def _write(deck: Deck) -> Deck:
    deck.updated = now()
    path = _path(deck.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(deck.to_json(), indent=2, sort_keys=True))
    os.replace(tmp, path)
    return deck


def update(deck_id: str, change: Callable[[Deck], None]) -> Deck:
    with _lock:
        deck = load(deck_id)
        change(deck)
        return _write(deck)


def delete(deck_id: str) -> None:
    _path(deck_id).unlink(missing_ok=True)


def assign_unowned(user_id: str) -> int:
    """Give every deck without an owner to `user_id`. Returns how many."""
    count = 0
    for deck in list_decks():
        if deck.owner is None:
            update(deck.id, lambda d: setattr(d, "owner", user_id))
            count += 1
    return count


def list_decks(game: str | None = None) -> list[Deck]:
    if not decks_dir().is_dir():
        return []
    decks = []
    for path in decks_dir().glob("*.json"):
        try:
            deck = Deck.from_json(json.loads(path.read_text()))
        except (ValueError, TypeError, json.JSONDecodeError):
            continue  # skip unreadable files rather than break the page
        if game is None or deck.game == game:
            decks.append(deck)
    return sorted(decks, key=lambda d: d.updated, reverse=True)
