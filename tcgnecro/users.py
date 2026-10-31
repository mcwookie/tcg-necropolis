"""User profiles, stored in data/users.json.

A profile is who is using the app: it owns decks and (later) holds personal
settings. How someone proves they are that profile is a separate concern,
handled in auth.py, so the sign-in method can change (profile picker today,
Google/GitHub or a login proxy later) without touching profiles or decks.

Profiles are user data, like decks, so they live in their own file rather
than in index.db (which is a rebuildable cache). The file is small and read
on every request, so reads are cached and refreshed when the file changes.
"""

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tcgnecro import db
from tcgnecro.deckbuilder.model import now

USERS_FORMAT = 1
_lock = threading.Lock()
_cache: tuple[float, dict[str, "User"]] | None = None


class UserError(ValueError):
    pass


@dataclass
class User:
    id: str
    name: str
    is_admin: bool = False
    email: str | None = None
    # Accounts at sign-in providers that map to this profile, e.g.
    # {"provider": "github", "subject": "12345"}. Unused until SSO is added.
    identities: list[dict] = field(default_factory=list)
    # Personal settings (owned packs, default filters...). Reserved; see ROADMAP.
    prefs: dict = field(default_factory=dict)
    created: str = field(default_factory=now)


def users_file() -> Path:
    return db.data_dir() / "users.json"


def _read() -> dict[str, User]:
    global _cache
    path = users_file()
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    if _cache and _cache[0] == mtime:
        return _cache[1]
    data = json.loads(path.read_text())
    known = set(User.__dataclass_fields__)
    users = {u["id"]: User(**{k: v for k, v in u.items() if k in known}) for u in data.get("users", [])}
    _cache = (mtime, users)
    return users


def _write(users: dict[str, User]) -> None:
    global _cache
    path = users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(
        {"format": USERS_FORMAT, "users": [asdict(u) for u in users.values()]}, indent=2))
    os.replace(tmp, path)
    _cache = None


def list_users() -> list[User]:
    return sorted(_read().values(), key=lambda u: u.name.lower())


def get(user_id: str | None) -> User | None:
    return _read().get(user_id) if user_id else None


def by_name(name: str) -> User | None:
    name = name.strip().lower()
    return next((u for u in _read().values() if u.name.lower() == name), None)


def normalize_email(email: str | None) -> str | None:
    email = (email or "").strip().lower()
    return email or None


def by_email(email: str | None) -> User | None:
    """The profile with this email (case doesn't matter), e.g. the address a
    sign-in provider vouched for (auth.py)."""
    email = normalize_email(email)
    return next((u for u in _read().values() if email and normalize_email(u.email) == email), None) if email else None


def _check_email(users: dict[str, User], email: str | None, user_id: str | None) -> str | None:
    """A normalized email, refusing one another profile already uses."""
    email = normalize_email(email)
    if email and "@" not in email:
        raise UserError(f"'{email}' doesn't look like an email address")
    if email and any(normalize_email(u.email) == email and u.id != user_id for u in users.values()):
        raise UserError(f"Another profile already uses {email}")
    return email


def find(name_or_id: str) -> User:
    user = get(name_or_id) or by_name(name_or_id)
    if user is None:
        raise UserError(f"No profile named '{name_or_id}'")
    return user


def create(name: str, email: str | None = None, is_admin: bool = False) -> User:
    """Add a profile. The very first profile is always an admin, and takes
    ownership of any decks made before profiles existed."""
    from tcgnecro.deckbuilder import store  # avoid an import cycle at load time

    name = name.strip()
    if not name:
        raise UserError("A profile needs a name")
    with _lock:
        users = dict(_read())
        if any(u.name.lower() == name.lower() for u in users.values()):
            raise UserError(f"There is already a profile named '{name}'")
        first = not users
        email = _check_email(users, email, None)
        user = User(id=uuid.uuid4().hex[:12], name=name, email=email, is_admin=is_admin or first)
        users[user.id] = user
        _write(users)
    if first:
        store.assign_unowned(user.id)
    return user


def update(user_id: str, **changes) -> User:
    with _lock:
        users = dict(_read())
        if user_id not in users:
            raise UserError("No such profile")
        if "name" in changes:
            name = changes["name"].strip()
            if not name:
                raise UserError("A profile needs a name")
            if any(u.name.lower() == name.lower() and u.id != user_id for u in users.values()):
                raise UserError(f"There is already a profile named '{name}'")
            changes["name"] = name
        if "email" in changes:
            changes["email"] = _check_email(users, changes["email"], user_id)
        user = User(**(asdict(users[user_id]) | changes))
        users[user_id] = user
        _write(users)
        return user


def remove(user_id: str, decks_to: str | None = None) -> int:
    """Delete a profile. Its decks must go to another profile (`decks_to`);
    a profile that owns decks can't just vanish. Returns decks moved."""
    from tcgnecro.deckbuilder import store

    with _lock:
        users = dict(_read())
        if user_id not in users:
            raise UserError("No such profile")
        owned = [d for d in store.list_decks() if d.owner == user_id]
        if owned and not decks_to:
            raise UserError(f"{users[user_id].name} owns {len(owned)} deck(s); say who gets them")
        if decks_to and decks_to not in users:
            raise UserError("The profile to receive the decks doesn't exist")
        if decks_to == user_id:
            raise UserError("Decks can't be handed to the profile being removed")
        if users[user_id].is_admin and sum(u.is_admin for u in users.values()) == 1:
            raise UserError("Can't remove the last admin")
        del users[user_id]
        _write(users)
    for deck in owned:
        store.update(deck.id, lambda d: setattr(d, "owner", decks_to))
    return len(owned)


# ----- per-profile settings ----------------------------------------------------
# Stored in User.prefs. Owned packs: prefs["owned_packs"] = {game_id: [pack ids]}.
# A game missing from the dict means "not set up", which is different from
# "owns nothing": filters only apply once a collection has been saved.

def owned_packs(user: User | None, game_id: str) -> set[str] | None:
    if user is None:
        return None
    packs = user.prefs.get("owned_packs", {}).get(game_id)
    return set(packs) if packs is not None else None


def set_owned_packs(user_id: str, game_id: str, packs: set[str] | None) -> User:
    """Save which packs a profile owns for one game (None forgets it)."""
    user = get(user_id)
    if user is None:
        raise UserError("No such profile")
    owned = dict(user.prefs.get("owned_packs", {}))
    if packs is None:
        owned.pop(game_id, None)
    else:
        owned[game_id] = sorted(packs)
    return update(user_id, prefs=user.prefs | {"owned_packs": owned})
