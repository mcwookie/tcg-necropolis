"""Keeping decks in sync with the site they were imported from.

One-way: the source site (ArkhamDB for Arkham) is the master copy, and
TCG Necropolis follows it. A synced deck carries a `sync` record (Deck.sync):

    provider, provider_name   "arkhamdb", "ArkhamDB"
    kind, remote_id           what to fetch; remote_id moves to the newest
                              version when the site has upgraded the deck
    url, version, remote_updated
    fingerprint               hash of the cards as last synced, to notice
                              edits made here since
    synced_name               the name as last synced, to notice a local rename
    status, message           "ok" | "local-changes" | "error", and why
    last_checked, last_synced timestamps

Rules:
- Cards (identity, slots, side deck, choices) come from the site.
- If the cards were changed here since the last sync, an automatic sync
  leaves the deck alone and records status "local-changes". `force=True`
  ("Sync and replace") overwrites them.
- The name follows the site unless it was renamed here. Notes, owner and
  sharing are never touched.

Plugins provide sync_link() and fetch_remote(); this module does the rest.
Syncs happen from the deck page, `tcgnecro decks sync`, and the web server's
background loop (sync_all, every TCGNECRO_SYNC_MINUTES).
"""

import hashlib
import json
import logging
from dataclasses import dataclass

from tcgnecro import db
from tcgnecro.deckbuilder import registry, store
from tcgnecro.deckbuilder.model import Deck, RemoteDeck, now

log = logging.getLogger("tcgnecro.sync")


@dataclass
class SyncResult:
    deck: Deck
    changed: bool        # the deck's cards or name were updated
    message: str


def fingerprint(identity, slots, side_slots, meta) -> str:
    content = {"identity": identity, "slots": slots, "side_slots": side_slots, "meta": meta}
    return hashlib.sha1(json.dumps(content, sort_keys=True).encode()).hexdigest()


def deck_fingerprint(deck: Deck) -> str:
    return fingerprint(deck.identity, deck.slots, deck.side_slots, deck.meta)


def remote_fingerprint(remote: RemoteDeck) -> str:
    return fingerprint(remote.identity, remote.slots, remote.side_slots, remote.meta)


def has_local_changes(deck: Deck) -> bool:
    return bool(deck.sync) and deck_fingerprint(deck) != deck.sync.get("fingerprint")


def can_link(plugin, deck: Deck) -> bool:
    """A deck imported before syncing existed, from a source that can sync."""
    return deck.sync is None and bool(deck.source) and plugin.sync_link(deck.source) is not None


def _record(link: dict, remote: RemoteDeck, name: str) -> dict:
    stamp = now()
    return link | {
        "kind": remote.kind, "remote_id": remote.remote_id, "url": remote.url,
        "version": remote.version, "remote_updated": remote.updated,
        "fingerprint": remote_fingerprint(remote), "synced_name": name,
        "status": "ok", "message": "", "last_checked": stamp, "last_synced": stamp,
    }


def _apply(deck: Deck, remote: RemoteDeck, link: dict) -> None:
    """Copy the site's version onto the deck and record the sync."""
    renamed_here = deck.sync is not None and deck.name != deck.sync.get("synced_name")
    deck.identity, deck.slots, deck.side_slots, deck.meta = (
        remote.identity, dict(remote.slots), dict(remote.side_slots), dict(remote.meta))
    if not renamed_here:
        deck.name = remote.name
    deck.sync = _record(link, remote, deck.name if renamed_here else remote.name)


def import_and_link(plugin, source: str, pool) -> Deck:
    """A new deck from an import URL, linked for syncing when the plugin can.
    The caller assigns the id and owner and saves it."""
    link = plugin.sync_link(source)
    if link is None:
        return plugin.import_deck(source, pool)
    remote = plugin.fetch_remote(link, pool)
    deck = Deck(id="", game=plugin.game_id, name=remote.name, notes=remote.notes, source=source.strip())
    _apply(deck, remote, link)
    return deck


def sync_deck(deck_id: str, force: bool = False) -> SyncResult:
    """Bring one deck up to date with its source. Never raises for remote
    problems: they're recorded on the deck (status "error") and returned."""
    deck = store.load(deck_id)
    plugin = registry.plugin(deck.game)
    conn = db.connect()
    try:
        pool = registry.pool(conn, deck.game)
    finally:
        conn.close()

    link = deck.sync or (plugin.sync_link(deck.source) if deck.source else None)
    if link is None:
        return SyncResult(deck, False, "This deck isn't linked to a site it can sync from")
    first_link = deck.sync is None

    try:
        remote = plugin.fetch_remote(link, pool)
    except Exception as e:   # network trouble, private or deleted decks
        message = f"Couldn't sync from {link.get('provider_name', 'the source')}: {e}"
        if not first_link:
            deck = store.update(deck_id, lambda d: d.sync and d.sync.update(
                status="error", message=message, last_checked=now()))
        return SyncResult(deck, False, message)

    result = {}

    def change(d: Deck):
        # Re-checked under the store lock, against the deck as saved now.
        if d.sync and has_local_changes(d) and not force:
            d.sync.update(status="local-changes", last_checked=now(),
                          message="Changed here since the last sync; syncing would replace those changes")
            result.update(changed=False, message=d.sync["message"])
            return
        current = d.sync is not None and not has_local_changes(d) and (
            d.sync.get("fingerprint") == remote_fingerprint(remote)
            and (d.name == remote.name or d.name != d.sync.get("synced_name")))
        if current:
            d.sync.update(status="ok", message="", last_checked=now(), remote_id=remote.remote_id,
                          url=remote.url, version=remote.version, remote_updated=remote.updated)
            result.update(changed=False, message="Already up to date")
            return
        old_id = (d.sync or link).get("remote_id")
        _apply(d, remote, d.sync or link)
        what = f"version {remote.version}" if remote.version else "the latest version"
        moved = f" (followed the upgrade to deck {remote.remote_id})" if str(old_id) != remote.remote_id else ""
        result.update(changed=True, message=f"Updated to {what}{moved}")

    deck = store.update(deck_id, change)
    return SyncResult(deck, result["changed"], result["message"])


def unlink(deck_id: str) -> Deck:
    return store.update(deck_id, lambda d: setattr(d, "sync", None))


def sync_all() -> list[tuple[Deck, SyncResult]]:
    """Sync every linked deck (without forcing). Used by the background loop
    and `tcgnecro decks sync`. Decks of games without a deck builder are skipped."""
    results = []
    for deck in store.list_decks():
        if not deck.sync or deck.game not in registry.plugins():
            continue
        result = sync_deck(deck.id)
        if result.changed or result.deck.sync.get("status") != "ok":
            log.info("sync %s (%s): %s", deck.id, deck.name, result.message)
        results.append((deck, result))
    return results
