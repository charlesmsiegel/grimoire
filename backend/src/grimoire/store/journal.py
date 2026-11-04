"""The commit journal: which staged files a transaction publishes, and whether it aborted.

Replacing ``.txn/journal.json`` is the commit point. Recovery trusts nothing in
it: targets must be canonical record or manifest paths (or, by entry kind, a
kept-version slot or a ``.sync`` state file), temps bare staging
names in ``.txn/``. Entries are typed so later record families can commit
other kinds atomically with publication.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .errors import RecoveryFailed
from .jsonfmt import JsonError, is_etag, parse_strict, render
from .layout import Parts, classify, is_kept_target, is_sync_state

JOURNAL_FORMAT = "grimoire.journal/1"
STAGING_SUFFIX = ".new"
_TEMP = re.compile(r"[A-Za-z0-9][A-Za-z0-9_~-]*\.new")

State = Literal["commit", "abort"]
#: ``publish`` a canonical record; ``replica`` a canonical record holding another library's exact
#: bytes; ``keep`` a kept copy of another library's version under ``.conflicts``; ``hold`` a kept
#: version whose bytes exist nowhere else; ``state`` a device-local ``.sync`` file. Replica, keep
#: and state entries are never preserved: dropped instead (their bytes are regenerable).
Kind = Literal["publish", "replica", "keep", "hold", "state"]
KINDS = ("publish", "replica", "keep", "hold", "state")
#: Entries whose bytes exist elsewhere: dropped, never preserved, when they cannot publish.
REGENERABLE_KINDS = frozenset({"replica", "keep", "state"})


def valid_target(kind: str, target: Parts) -> bool:
    if kind in ("publish", "replica"):
        return classify(target) is not None
    if kind in ("keep", "hold"):
        return is_kept_target(target)
    return kind == "state" and is_sync_state(target)


@dataclass(frozen=True)
class Entry:
    target: Parts
    temp: str
    old: str | None
    new: str
    kind: Kind = "publish"


@dataclass(frozen=True)
class Journal:
    state: State
    entries: tuple[Entry, ...]

    def render(self) -> bytes:
        return render({
            "format": JOURNAL_FORMAT,
            "state": self.state,
            "entries": [{"kind": entry.kind, "target": "/".join(entry.target), "temp": entry.temp,
                         "old": entry.old, "new": entry.new} for entry in self.entries],
        })

    def aborted(self) -> Journal:
        return Journal("abort", self.entries)


def is_staging_name(name: str) -> bool:
    return _TEMP.fullmatch(name) is not None


def parse(data: bytes) -> Journal:
    """A journal whose every path the store itself could have written, or ``RecoveryFailed``."""
    try:
        document = parse_strict(data)
    except JsonError as error:
        raise RecoveryFailed(f"the journal is not valid JSON: {error}") from None
    if document.get("format") != JOURNAL_FORMAT:
        raise RecoveryFailed(f"unknown journal format {document.get('format')!r}")
    state = document.get("state")
    if state not in ("commit", "abort"):
        raise RecoveryFailed(f"unknown journal state {state!r}")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, tuple) or not raw_entries:
        raise RecoveryFailed("the journal lists no entries")
    entries: list[Entry] = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping) or set(raw) != {"kind", "target", "temp", "old", "new"}:
            raise RecoveryFailed("a journal entry has unexpected fields")
        kind = raw["kind"]
        if kind not in KINDS:
            raise RecoveryFailed(f"unknown journal entry kind {kind!r}")
        target, temp = raw["target"], raw["temp"]
        if type(target) is not str or not valid_target(kind, tuple(target.split("/"))):
            raise RecoveryFailed(f"journal target is not a {kind} path: {target!r}")
        if type(temp) is not str or not is_staging_name(temp):
            raise RecoveryFailed(f"journal temp is not a staging name: {temp!r}")
        if not (raw["old"] is None or is_etag(raw["old"])) or not is_etag(raw["new"]):
            raise RecoveryFailed("journal etags are malformed")
        entries.append(Entry(tuple(target.split("/")), temp, raw["old"], raw["new"], kind))
    if len({entry.target for entry in entries}) != len(entries) or len({e.temp for e in entries}) != len(entries):
        raise RecoveryFailed("the journal names a target or temp twice")
    return Journal(state, tuple(entries))  # type: ignore[arg-type]
