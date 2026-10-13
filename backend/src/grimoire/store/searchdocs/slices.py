"""A campaign's inputs to its scene SearchDocuments, and one scene's slices of
them (roadmap 08-C1, spec section 4.2).

`CampaignInputs.load(cid)` parses each ledger once per stat signature (this
package's own `statcache` pool), tolerantly: a file that will not parse, or
parses to the wrong shape, contributes nothing and is named in `degraded`.
`scene_slices` then cuts what concerns one scene. Every slice is a plain JSON
value whose strings are whitespace-collapsed and byte-clipped to the item caps
`scene.py` renders them at, and whose lists are sorted as they render, so its
digest moves only when the text or metadata it can contribute moves.

**The absorbed gate.** A row keyed by a `sid` that the scene's own head does
not mark absorbed is, in the common case, one a deleted scene left behind for
whichever scene inherited its number. So every untimed sid join -- the
chronicle record, thread and commitment beats, facts, relationship rows and the
chronicle's cast -- contributes only to a scene whose head says `done: true`
(`routes.scenes._already_absorbed`'s rule). A continuity link carries a
timestamp and is admitted on its own merits: one created no earlier than the
head's `created` cannot predate this scene. The cost, stated in the spec: a
beat the user files against the open scene is invisible until absorb, and a
recycled sid that is later absorbed still inherits a dead scene's rows (every
sid join's residual today; fixing it belongs to scene deletion).

Read-only: nothing here writes, takes a lock or mints an identity.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import (
    characters,
    chronicle,
    commitments,
    facts,
    membership,
    overlay,
    paths,
    pcs,
    plot,
    relationship_history,
    statcache,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..continuity import canon, effective
from ..continuity import doc as continuity_doc
from ..frontmatter import parse_frontmatter_head
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from . import rolling

log = logging.getLogger(__name__)

#: This package's memo pool, sized like the scene-head pool for its reason: a
#: campaign-wide read touches every scene head, and the shared FIFO would evict
#: every card summary in it.
POOL_ENTRIES = scenes_read.POOL_ENTRIES
_POOL: dict = {}

#: Item caps, in UTF-8 bytes (spec section 4.3). A slice string is clipped to
#: the cap of the item it renders into, so a change past what can render
#: cannot move a digest. `scene.py` renders against the same table.
CAPS = {
    "title": 160, "date": 48, "moment": 64, "location": 64, "name": 48,
    "one_line": 240, "summary": 1500, "keyword": 32, "record": 144,
    "fact": 144, "relationship": 112, "ref": 160,
}

#: What `scene.py` renders after a player's name; its bytes count in the cap.
PLAYER_SUFFIX = " (player)"

#: How many entries of a list `scene.py` shows: in the text (the line's item
#: count) and in the metadata (`META_REFS`, past which a list is marked
#: `truncated`). A slice keeps exactly that much, so an entry no part of the
#: document can show does not move its digest. `scene.LINES` reads these.
ITEMS = {"threads": 5, "commitments": 3, "facts.recorded": 4, "facts.retired": 2,
         "relationships": 3}
META_REFS = 64

_TOKEN = re.compile(r"\A[0-9a-f]{32}\Z")
#: RecursionError: valid JSON nested past the parser's depth (as `continuity`).
_JSON_ERRORS = (OSError, ValueError, UnicodeDecodeError, RecursionError)

#: Degraded inputs already logged, by kind, so each says so once per process.
_logged: set[str] = set()


def _inaccessible(path: Path) -> bool:
    """Whether `path` is there but cannot be reached (an access error, not
    absence)."""
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False     # ValueError: a name no filesystem can hold (a lone surrogate)
    except OSError:
        return True
    return False


def _present(path: Path) -> bool:
    """Whether `path` is there, an access failure counting as there: a record
    that cannot be reached is unreadable, never missing (`Path.exists`
    suppresses that failure on some versions)."""
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False     # ValueError: a name no filesystem can hold (a lone surrogate)
    except OSError:
        return True
    return True


# --- text normalization ---------------------------------------------------------

def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def clip(text: str, cap: int) -> str:
    """`text` within `cap` UTF-8 bytes, cut at a character boundary and marked
    with `…` (three bytes, inside the cap) when anything was dropped. A lone
    surrogate (a hand-edited escape) is replaced first, so this never raises."""
    if not _encodable(text):
        text = text.encode("utf-8", "replace").decode("utf-8")
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text
    return raw[:max(cap - 3, 0)].decode("utf-8", "ignore") + "…"


@dataclass
class _Damage:
    """Inputs that would not read, or held a lone surrogate."""
    names: set[str] = field(default_factory=set)

    def text(self, value: object, cap: int, source: str) -> str:
        """`value` as slice text: a string, whitespace collapsed, a lone
        surrogate replaced (and `source` named), clipped to `cap`; anything
        else is ""."""
        if not isinstance(value, str):
            return ""
        out = " ".join(value.split())
        if not _encodable(out):
            out = out.encode("utf-8", "replace").decode("utf-8")
            self.add(source)
        return clip(out, cap)

    def clean(self, value: str, source: str) -> str:
        """`value` with a lone surrogate replaced (and `source` named), whole:
        for a ref, which is capped where it is emitted, not where it is kept."""
        if _encodable(value):
            return value
        self.add(source)
        return value.encode("utf-8", "replace").decode("utf-8")

    def add(self, name: str) -> None:
        # The name is often the damaged value itself: escaped, so neither the
        # log line nor `meta.degraded` carries a string that cannot be encoded.
        name = name.encode("utf-8", "backslashreplace").decode("utf-8")
        self.names.add(name)
        if name not in _logged:
            _logged.add(name)
            log.warning("searchdocs: input %s is degraded; it contributes nothing it "
                        "could not read", name)


def digest(value: object) -> str:
    """One slice's digest: canonical JSON, surrogates passed through so a
    hand-edited value can never raise."""
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8", "surrogatepass")).hexdigest()


# --- memoized ledger parses -----------------------------------------------------

#: Counted per parse, so a test can see a warm call parse nothing.
parse_count: dict[str, int] = {}


def _opt(value: object, kind: type) -> bool:
    return value is None or isinstance(value, kind)


def _strings(value: object) -> bool:
    return value is None or (isinstance(value, list) and all(isinstance(x, str) for x in value))


def _each(ledger: object, record: Callable[[dict], bool]) -> bool:
    return all(isinstance(r, dict) and record(r) for r in ledger.values()) \
        if isinstance(ledger, dict) else True


#: Each ledger's nested shape, checked once per parse: a record or a section
#: of the wrong shape is skipped where it is read (the rest still renders),
#: and its ledger is named in `degraded` -- never silently read as empty.
_SHAPES: dict[str, Callable[[object], bool]] = {
    "chronicle.json": lambda d: _each(d, lambda r: (
        _strings(r.get("keywords")) and _strings(r.get("cast"))
        and all(_opt(r.get(k), str) for k in ("one_line", "summary", "date", "location")))),
    "appearances.json": lambda d: _each(d, lambda r: (
        _strings(r.get("scenes")) and _opt(r.get("presence"), dict)
        and all(_opt(r.get(k), str) for k in ("role", "version")))),
    "facts.json": lambda d: _each(d, lambda r: _opt(r.get("text"), str)),
    "continuity.json": lambda d: not isinstance(d, dict) or (
        _opt(d.get("links"), dict) and _each(d.get("links") or {}, lambda _r: True)),
    "relationship_history.json": lambda d: not isinstance(d, dict) or (
        _opt(d.get("entries"), list)
        and all(isinstance(e, dict) for e in d.get("entries") or ())),
}


def _record_shape(ledger: object) -> bool:
    """A thread or commitment ledger: records, each with a list of beats."""
    return _each(ledger, lambda r: _opt(r.get("beats"), list)
                 and all(isinstance(b, dict) and _opt(b.get("text"), str)
                         and _opt(b.get("scene"), str) for b in r.get("beats") or ())
                 and all(_opt(r.get(k), str) for k in ("title", "status")))


def _parse_json(name: str, path: Path, shape: type) -> tuple[object, bool]:
    """(value, degraded). Absent is the empty value and not degraded; a parse
    failure or the wrong top-level shape is the empty value and degraded; a
    nested part of the wrong shape (`_SHAPES`) keeps the value and is
    degraded."""
    def compute() -> tuple[object, bool]:
        parse_count[name] = parse_count.get(name, 0) + 1
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return shape(), False
        except _JSON_ERRORS:
            return shape(), True
        try:
            value = json.loads(raw)
        except _JSON_ERRORS:
            return shape(), True
        if not isinstance(value, shape):
            return shape(), True
        return value, not _SHAPES.get(name, lambda _v: True)(value)
    return statcache.memo(f"searchdocs.parse:{name}", statcache.signature(path, absent_ok=True),
                          compute, pool=_POOL, max_entries=POOL_ENTRIES)


def _records(cid: str) -> tuple[dict, dict, set[str]]:
    """The alias-resolved effective threads and commitments, memoized on the
    three files the alias walk reads together."""
    files = (plot._path(cid), commitments._path(cid), continuity_doc._path(cid))

    def compute() -> tuple[dict, dict, set[str]]:
        parse_count["records"] = parse_count.get("records", 0) + 1
        out: list[dict] = []
        bad: set[str] = set()
        for kind, name, ledger in (("thread", "plot.json", plot),
                                   ("commitment", "commitments.json", commitments)):
            try:
                got = effective.records(cid, kind)
                # The effective view normalizes a malformed record away; the
                # file as written is what says whether one was.
                malformed = not _record_shape(ledger.read(cid))
            except Exception:  # noqa: BLE001 -- one garbled ledger empties its slice only
                got, malformed = None, True
            if not isinstance(got, dict):
                bad.add(name)
                got = {}
            elif malformed:
                bad.add(name)
            out.append(got)
        return out[0], out[1], bad
    return statcache.memo("searchdocs.records", statcache.signature(*files, absent_ok=True),
                          compute, pool=_POOL, max_entries=POOL_ENTRIES)


@dataclass(frozen=True)
class Name:
    """A display name, the store file it was read from (None for a fallback to
    the id), and whether that fallback was forced by a card that would not
    read."""
    text: str
    source: Path | None
    unreadable: bool = False


class CampaignInputs:
    """One campaign's parsed ledgers and name tables, for one read sweep.

    Built per call: each parse is memoized by its file's stat signature, so a
    warm build stats the files and parses nothing that has not changed. Never
    raises for store damage; raises `CampaignNotFound` for a campaign that is
    not there."""

    def __init__(self, cid: str) -> None:
        self.cid = cid
        self.root = campaigns_paths.campaign_root(cid)
        if not campaigns_paths.campaign_meta_path(cid).exists():
            raise campaigns_paths.CampaignNotFound(cid)
        self.damage = _Damage()
        # What the overlay reads to resolve a location's layer and a group's
        # membership: the world (campaign.md), the tombstones and detachments.
        self.control_paths: tuple[Path, ...] = (campaigns_paths.campaign_meta_path(cid),
                                                overlay._deleted_path(cid),
                                                overlay._detached_path(cid))
        self.ledger_paths: dict[str, Path] = {
            "chronicle": chronicle._chronicle_path(cid),
            "appearances": appearances_paths._path(cid),
            "facts": facts._path(cid),
            "relationships": relationship_history._path(cid),
            "continuity": continuity_doc._path(cid),
            "plot": plot._path(cid),
            "commitments": commitments._path(cid),
        }
        loaded: dict[str, object] = {}
        for key, name in (("chronicle", "chronicle.json"), ("appearances", "appearances.json"),
                          ("facts", "facts.json"), ("continuity", "continuity.json")):
            value, bad = _parse_json(name, self.ledger_paths[key], dict)
            loaded[key] = value
            if bad:
                self.damage.add(name)
        rels, bad = _parse_json("relationship_history.json", self.ledger_paths["relationships"],
                                dict)
        if bad:
            self.damage.add("relationship_history.json")
        entries = rels.get("entries") if isinstance(rels, dict) else None
        self.relationships: list[dict] = [e for e in entries if isinstance(e, dict)] \
            if isinstance(entries, list) else []
        self.chronicle: dict = loaded["chronicle"]  # type: ignore[assignment]
        self.appearances: dict = loaded["appearances"]  # type: ignore[assignment]
        self.facts: dict = loaded["facts"]  # type: ignore[assignment]
        links = loaded["continuity"].get("links")  # type: ignore[attr-defined]
        self.links: dict = links if isinstance(links, dict) else {}
        self.threads, self.commitments, bad_records = _records(cid)
        for name in sorted(bad_records):
            self.damage.add(name)
        self._view = overlay.view(cid)
        self._by_ref: dict[str, tuple[str, dict]] | None = None
        self.groups_index, self.group_paths = self._groups(cid)

    def _groups(self, cid: str) -> tuple[membership.Index | None, dict[str, Path]]:
        """07's effective inverse (detached rule applied) and each group's
        source file, from one capture. A capture that fails is no groups at
        all, and `groups` is named in `degraded`."""
        try:
            capture = overlay.group_capture(cid)
        except Exception:  # noqa: BLE001 -- groups are metadata; the document still renders
            self.damage.add("groups")
            return None, {}
        sources = {f"groups:{src.id}": src.path for src in capture.sources}
        try:
            snapshot = membership.campaign_snapshot(cid, capture=capture)
        except Exception:  # noqa: BLE001 -- as above
            # The capture listed the files even though one would not parse:
            # they stay inputs, so repairing that file finds the documents.
            self.damage.add("groups")
            return None, sources
        return snapshot.index, sources

    # --- names --------------------------------------------------------------

    def actor_name(self, kind: str, aid: str, version: object) -> Name:
        """The actor's name as `scene_cast` resolves it -- `overlay.actor_root`
        at the record's locked version -- memoized on that card file's stat
        signature; the id when the card will not read."""
        if kind not in ("characters", "pcs") or not paths.safe_id(aid) \
                or not isinstance(version, str) or not paths.safe_id(version):
            return Name(aid, None)
        files = self.actor_files(kind, aid, version)
        try:
            root = overlay.actor_root(self.cid, kind, aid, v=self._view)
            module = characters if kind == "characters" else pcs
            card = module.require_version(root, aid, version)
        except (characters.CharacterNotFound, characters.VersionNotFound,
                pcs.PCNotFound, pcs.PCVersionNotFound, OSError):
            # Not found -- or not reachable: `exists` can swallow an access
            # error, and a card that is there is unreadable, never absent.
            return Name(aid, None, unreadable=any(_inaccessible(p) for p in files))
        if root != self._view.croot and any(_inaccessible(p) for p in files[:2]):
            # Resolved to the world only because the campaign's own copy
            # could not be reached: that copy is the authority, unread.
            return Name(aid, None, unreadable=True)

        def compute() -> str | None:
            try:
                return appearances_cast.display_name(root, kind, aid, version)
            except (*_JSON_ERRORS, AttributeError, TypeError):
                return None
        got = statcache.memo("searchdocs.actor", statcache.signature(card), compute,
                             pool=_POOL, max_entries=POOL_ENTRIES)
        if not got:
            # The card is there and will not read: still the file whose repair
            # changes this name, so it stays an input.
            return Name(aid, card, unreadable=True)
        return Name(got, card)

    def appearance_refs(self) -> dict[str, tuple[str, dict]]:
        """Each appearance record by its actor's canonical ref (the form a
        chronicle cast names), built once per load."""
        if self._by_ref is None:
            self._by_ref = {canon.actor_ref(token): (token, rec)
                            for token, rec in self.appearances.items()
                            if isinstance(token, str) and isinstance(rec, dict)}
        return self._by_ref

    def actor_files(self, kind: str, aid: str, version: object) -> tuple[Path, ...]:
        """Both layers' meta and locked-version files for an actor, present
        or not: what a name read consults, so a write that creates or restores
        the card finds the documents that named the actor by its id meanwhile
        (as `location_files`)."""
        if kind not in ("characters", "pcs") or not paths.safe_id(aid) \
                or not isinstance(version, str) or not paths.safe_id(version) \
                or not _encodable(aid + version):
            return ()     # no file can be at a name that will not encode
        module = characters if kind == "characters" else pcs
        card = characters._card_path if kind == "characters" else pcs._version_path
        return tuple(p for root in (self._view.croot, self._view.wroot)
                     for p in (module._meta_path(root, aid), card(root, aid, version)))

    def location_files(self, lid: str) -> tuple[Path, ...]:
        """Both layers' files for a location, present or not: what a name read
        consults, so a write that creates or edits either one finds the
        documents that named it."""
        if not paths.safe_id(lid):
            return ()
        return (self._view.croot / "locations" / f"{lid}.md",
                self._view.wroot / "locations" / f"{lid}.md")

    def location_name(self, lid: str) -> Name:
        """The location's name through the overlay, memoized on both layers'
        files and on whether the campaign tombstoned it; the id otherwise. A
        record that is there but will not read is `unreadable`."""
        if not paths.safe_id(lid):
            return Name(lid, None)
        gone = f"locations/{lid}" in self._view.gone
        layers = (self._view.croot / "locations" / f"{lid}.md",
                  self._view.wroot / "locations" / f"{lid}.md")

        def compute() -> tuple[str, int] | None:
            """(name, layer), (``""``, -1) for a record that is there and will
            not read, None for one that is not there or names nothing."""
            try:
                meta = overlay.read_entity(self.cid, "locations", lid, v=self._view)["meta"]
            except Exception:  # noqa: BLE001 -- a missing or unreadable record is its id
                return None if gone or not any(_present(p) for p in layers) else ("", -1)
            name = meta.get("name") if isinstance(meta, dict) else None
            source = 0 if layers[0].exists() else 1
            if source == 1 and _inaccessible(layers[0]):
                # The world's copy answered only because the campaign's own
                # could not be reached: that one is the authority, unread.
                return ("", -1)
            return (name, source) if isinstance(name, str) and name else None
        sig = statcache.signature(*layers, absent_ok=True)
        got = statcache.memo("searchdocs.location", None if sig is None else (*sig, ("gone", gone)),
                             compute, pool=_POOL, max_entries=POOL_ENTRIES)
        if got is None:
            return Name(lid, None)
        if got[1] < 0:
            return Name(lid, None, unreadable=True)
        return Name(got[0], layers[got[1]])

    @property
    def degraded(self) -> tuple[str, ...]:
        return tuple(sorted(self.damage.names))

    @staticmethod
    def load(cid: str) -> CampaignInputs:
        return CampaignInputs(cid)


# --- one scene ------------------------------------------------------------------

@dataclass(frozen=True)
class Head:
    """A scene's frontmatter head, read without its transcript."""
    sid: str
    path: Path
    meta: dict

    @property
    def absorbed(self) -> bool:
        return str(self.meta.get("done", "")).lower() == "true"

    @property
    def identity(self) -> str:
        token = str(self.meta.get("identity", "") or "")
        return token if _TOKEN.match(token) else ""


def read_head(cid: str, sid: str) -> Head:
    """The scene's head, memoized by the file's stat signature in the scene-head
    pool's spirit. Raises `SceneNotFound` and `OSError`."""
    p = scenes_paths._scene_path(cid, sid)
    if not paths.safe_id(sid) or not p.exists():
        raise scenes_paths.SceneNotFound(sid)
    sig = statcache.signature(p)
    meta = statcache.memo("searchdocs.head", sig, lambda: parse_frontmatter_head(p),
                          pool=_POOL, max_entries=POOL_ENTRIES)
    return Head(sid, p, dict(meta))


@dataclass(frozen=True)
class Slices:
    """One scene's slices (`values`, by name), their digests, the store files
    they read (for 03's `materialized` rows), and what would not read."""
    sid: str
    identity: str
    values: dict
    digests: dict[str, str]
    paths: tuple[Path, ...]
    degraded: tuple[str, ...]


def _beats_here(record: dict, sid: str, damage: _Damage, source: str) -> list[str]:
    beats = record.get("beats")
    out = []
    for b in beats if isinstance(beats, list) else ():
        text = b.get("text") if isinstance(b, dict) else None
        if isinstance(text, str) and text.strip() and b.get("scene") == sid:
            out.append(damage.text(text, CAPS["record"], source))
    return out


def _records_here(records: dict, sid: str, damage: _Damage, source: str
                  ) -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    status: dict[str, str] = {}
    for ref in sorted(records):
        rec = records[ref]
        if not isinstance(rec, dict):
            continue
        beats = _beats_here(rec, sid, damage, source)
        if not beats:
            continue
        clean = damage.text(ref, CAPS["ref"], source)
        title = damage.text(rec.get("title"), CAPS["record"], source)
        # The slice holds the item as it renders, capped whole: a beat past
        # what the cap shows cannot move the digest.
        rows.append({"ref": clean,
                     "item": clip(f"{title or clean}: {' / '.join(beats)}", CAPS["record"])})
        status[clean] = damage.text(rec.get("status"), CAPS["ref"], source)
    return rows, status


def _relationships(entries: list[dict], sid: str, damage: _Damage) -> list[dict]:
    """Absorb rows filed under `sid` that no later undo row reversed: an undo on
    the same scene, kind and pair (as a set) with `before` and `after` swapped
    cancels the latest such absorb row still standing."""
    kept: list[dict] = []
    for e in entries:
        if e.get("scene") != sid or e.get("scene_gone"):
            continue
        pair = frozenset(x for x in (e.get("a"), e.get("b")) if isinstance(x, str))
        if e.get("source") == "absorb":
            kept.append({**e, "_pair": pair})
        elif e.get("source") == "undo":
            for i in range(len(kept) - 1, -1, -1):
                k = kept[i]
                if (k["_pair"] == pair and k.get("kind") == e.get("kind")
                        and k.get("before") == e.get("after") and k.get("after") == e.get("before")):
                    del kept[i]
                    break
    src = "relationship_history.json"

    def item(k: dict) -> str:
        """The row as it renders, capped whole (as `_records_here`'s item)."""
        label, before, after = (damage.text(k.get(f), CAPS["relationship"], src)
                                for f in ("label", "before", "after"))
        return clip(f"{label}: {before} -> {after}", CAPS["relationship"])
    return [{"item": item(k),
             "pair": sorted(damage.text(x, CAPS["ref"], src) for x in k["_pair"])}
            for k in kept]


def _facts(ledger: dict, sid: str, damage: _Damage) -> dict:
    """Ordered `(natural_key(id), id)` on the ledger's own ids, before any is
    clipped: two long ids that clip alike must not fall back on the file's
    key order."""
    recorded, retired = [], []
    ids = sorted((f for f in ledger if isinstance(f, str)),
                 key=lambda f: (paths.natural_key(f), f))
    for fid in ids:
        rec = ledger[fid]
        if not isinstance(rec, dict):
            continue
        text = damage.text(rec.get("text"), CAPS["fact"], "facts.json")
        if not text:
            continue
        clean_id = damage.text(fid, CAPS["ref"], "facts.json")
        if rec.get("scene") == sid:
            recorded.append({"id": clean_id, "text": text})
        if rec.get("retired_scene") == sid:
            retired.append({"id": clean_id, "text": text})
    return {"recorded": recorded, "retired": retired}


def _chronicle(inputs: CampaignInputs, head: Head, damage: _Damage) -> dict | None:
    src = "chronicle.json"
    rec = inputs.chronicle.get(head.sid)
    if isinstance(rec, dict):
        keywords = rec.get("keywords")
        cast = rec.get("cast")
        # Per field: a chronicle record whose summary (or one-liner) is blank
        # falls back to the head's, which the priority puts next.
        one_line = damage.text(rec.get("one_line"), CAPS["one_line"], src) \
            or damage.text(head.meta.get("one_line"), CAPS["one_line"], "head")
        summary = damage.text(rec.get("summary"), CAPS["summary"], src)
        source = "chronicle"
        if not summary:
            summary = damage.text(head.meta.get("summary"), CAPS["summary"], "head")
            source = "frontmatter"
        return {
            "one_line": one_line,
            "summary": summary,
            "keywords": [damage.text(k, CAPS["keyword"], src)
                         for k in (keywords if isinstance(keywords, list) else ())
                         if isinstance(k, str) and k.strip()],
            "date": damage.text(rec.get("date"), CAPS["date"], src),
            "location": damage.text(rec.get("location"), CAPS["location"], src),
            "cast": sorted({canon.actor_ref(t) for t in (cast if isinstance(cast, list) else ())
                            if isinstance(t, str) and t}),
            "source": source,
        }
    one_line = damage.text(head.meta.get("one_line"), CAPS["one_line"], "head")
    summary = damage.text(head.meta.get("summary"), CAPS["summary"], "head")
    if one_line or summary:
        return {"one_line": one_line, "summary": summary, "keywords": [], "date": "",
                "location": "", "cast": [], "source": "frontmatter"}
    return None


def _cast(inputs: CampaignInputs, head: Head, chron: dict | None,
          used: list[Path], damage: _Damage) -> list[dict]:
    """Every actor whose appearance record lists this scene or holds a presence
    interval for it, plus (absorbed only, through `chron`) the chronicle's cast.
    Players first, then the rest, each by `(kind, id)`."""
    out: dict[str, dict] = {}
    for token, rec in inputs.appearances.items():
        if not isinstance(token, str) or not isinstance(rec, dict):
            continue
        raw_scenes, raw_presence = rec.get("scenes"), rec.get("presence")
        scenes = raw_scenes if isinstance(raw_scenes, list) else []
        presence = raw_presence if isinstance(raw_presence, dict) else {}
        listed = head.sid in scenes
        if not listed and not presence.get(head.sid):
            continue
        raw_role = rec.get("role")
        role = raw_role if isinstance(raw_role, str) else ""
        entry = _actor(inputs, token, rec, role, used, damage)
        out[entry["ref"]] = {**entry, "present": listed}
    by_ref = inputs.appearance_refs() if chron and chron.get("cast") else {}
    for ref in (chron or {}).get("cast", ()):
        if ref in out:
            continue
        if ref in by_ref:
            # The chronicle names an actor whose appearance record no longer
            # lists this scene: still named, and read, through that record.
            token, rec = by_ref[ref]
            out[ref] = {**_actor(inputs, token, rec, "", used, damage), "present": False}
            continue
        kind, _, aid = ref.partition(":")
        out[ref] = {"ref": damage.clean(ref, "chronicle.json"), "join": ref, "kind": kind,
                    "id": aid, "name": damage.text(aid, CAPS["name"], "chronicle.json"),
                    "role": "", "present": False}
    return sorted(out.values(), key=lambda a: (a["role"] != "player", a["kind"], a["id"]))


def _actor(inputs: CampaignInputs, token: str, rec: dict, role: str,
           used: list[Path], damage: _Damage) -> dict:
    """One cast entry from its appearance record: the name at the locked
    version, and the files that name was (or would be) read from."""
    kind, aid = appearances_paths._split(token)
    name = inputs.actor_name(kind, aid, rec.get("version"))
    if name.source is not None:
        used.append(name.source)
    used += inputs.actor_files(kind, aid, rec.get("version"))
    if name.unreadable:
        damage.add(token)
    join = canon.actor_ref(token)     # full and raw: the membership join needs it
    # A player's name renders with its suffix inside the item cap, so the
    # slice keeps only what can show.
    cap = CAPS["name"] - (len(PLAYER_SUFFIX.encode()) if role == "player" else 0)
    return {"ref": damage.clean(join, token), "join": join, "kind": kind, "id": aid,
            "name": damage.text(name.text, cap, token), "role": role}


def _groups(inputs: CampaignInputs, cast: list[dict], used: list[Path]) -> list[dict]:
    """07-C3c's `scene_groups` over the FULL cast (not the 64-ref metadata
    list): every group one of whose affiliated actors -- members or leader --
    is in the cast, uncapped and unfiltered, gm-only rows included and marked by
    their `secrecy`. Current affiliation, never history: metadata only, never
    rendered, so a membership edit moves the key and `meta` and embeds
    nothing. A renderer that puts a group name into a prompt applies
    `membership.prompt_visible` and `SCENE_GROUP_LIMIT` itself."""
    # Every group file the snapshot read is an input, not only the groups
    # that list a cast member today: an edit that adds one to a group is a
    # write to a file this document never named otherwise. So is every file a
    # snapshot that failed had captured.
    used += inputs.group_paths.values()
    if inputs.groups_index is None:
        return []
    if not cast:
        return []
    rows = membership.scene_groups(inputs.groups_index, [a["join"] for a in cast],
                                   visible=None, limit=None)
    return [{"group": row.group, "name": clip(" ".join(row.name.split()), CAPS["ref"]),
             "secrecy": row.secrecy, "present": list(row.present),
             "leader_present": bool(row.leader_present)} for row in rows]


def _capped(rows: list, marker: object) -> list:
    """The first `META_REFS` entries, and `marker` after them when there were
    more: the metadata keeps as many and says it was cut, and no entry past
    them can move the digest."""
    return [*rows[:META_REFS], marker] if len(rows) > META_REFS else list(rows)


def _shown(rows: list[dict], visible: int, field: str, marker: dict) -> list[dict]:
    """`_capped`, with `field` (the rendered text) blanked past the `visible`
    entries the line shows: their refs reach the metadata, their text does
    not reach the document."""
    return _capped([r if i < visible else {**r, field: ""} for i, r in enumerate(rows)],
                   marker)


def _visible(values: dict) -> dict:
    """`values` cut to what the document can show (`ITEMS`, `META_REFS`), so
    each slice's digest moves only when the text or the metadata could. A
    thread's status is kept for the refs the metadata keeps. The cast, the
    places are kept whole: a scene's own short lists, and the groups are
    derived over the full cast by design."""
    head = values["head"]
    moments = head["moments"]
    threads = _shown(values["threads"], ITEMS["threads"], "item", {"ref": "", "item": ""})
    commits = _shown(values["commitments"], ITEMS["commitments"], "item",
                     {"ref": "", "item": ""})
    kept = {r["ref"] for r in threads + commits}
    facts = values["facts"]
    rels = values["relationships"]
    chron = values["chronicle"]
    if chron:
        # Deduplicated by case fold as both readers do, and capped like a ref
        # list; the cut is a flag, since a marker would read as a keyword.
        seen: set[str] = set()
        keywords = []
        for k in chron["keywords"]:
            if k.casefold() not in seen:
                seen.add(k.casefold())
                keywords.append(k)
        chron = {**chron, "keywords": keywords[:META_REFS],
                 "keywords_cut": len(keywords) > META_REFS}
    pairs = sorted({tuple(r["pair"]) for r in rels if len(r["pair"]) == 2})
    return {
        **values,
        "chronicle": chron,
        "head": {**head, "moments": moments if len(moments) <= 2 else [moments[0], moments[-1]],
                 "locations": _capped(head["locations"], "")},
        "threads": threads,
        "commitments": commits,
        "status": {r: st for r, st in values["status"].items() if r in kept},
        "facts": {name: _shown(facts[name], ITEMS[f"facts.{name}"], "text",
                               {"id": "", "text": ""})
                  for name in ("recorded", "retired")},
        "relationships": [{"item": r["item"]} for r in rels[:ITEMS["relationships"]]],
        "relationship_pairs": _capped([list(p) for p in pairs], ["", ""]),
        "links": _capped(values["links"], ""),
    }


def scene_slices(inputs: CampaignInputs, head: Head) -> Slices:
    """One scene's slices of `inputs`, gated as the module docstring says."""
    sid = head.sid
    # This scene's own damage: a lone surrogate or an unreadable card it read.
    # A ledger that would not parse is the campaign's, and joins it below.
    damage = _Damage()
    absorbed = head.absorbed
    hist = scenes_read.histories(head.meta)
    # Every store file the campaign's inputs read, for every document, whether
    # or not it contributed to this one: a ledger that holds nothing for this
    # scene today -- or that the absorbed gate keeps out until absorb, or that
    # would not parse and is named in `degraded` -- is still the file whose
    # next write can change the document, and 03-C3's rows are how 05's sync
    # finds it. So are the overlay's control files.
    used: list[Path] = [head.path, *inputs.ledger_paths.values(), *inputs.control_paths]
    chron = _chronicle(inputs, head, damage) if absorbed else None
    locations = []
    for lid in hist["locations"]:
        name = inputs.location_name(lid)
        used += inputs.location_files(lid)
        if name.unreadable:
            damage.add(f"locations/{lid}")
        locations.append({"id": damage.text(lid, CAPS["ref"], "head"),
                          "name": damage.text(name.text, CAPS["location"], f"locations/{lid}")})
    cast = _cast(inputs, head, chron, used, damage)
    threads, t_status = _records_here(inputs.threads, sid, damage, "plot.json") \
        if absorbed else ([], {})
    commits, c_status = _records_here(inputs.commitments, sid, damage, "commitments.json") \
        if absorbed else ([], {})
    fact_slice = _facts(inputs.facts, sid, damage) if absorbed else {"recorded": [], "retired": []}
    rels = _relationships(inputs.relationships, sid, damage) if absorbed else []
    created = str(head.meta.get("created", "") or "")
    links = sorted(lid for lid, link in inputs.links.items()
                   if isinstance(lid, str) and isinstance(link, dict)
                   and link.get("scene") == sid and created
                   and isinstance(link.get("created"), str) and link["created"] >= created)
    running = rolling.intact_rolling(inputs.cid, head) if not absorbed \
        else rolling.Running("", ())
    used += running.paths
    values = {
        "head": {
            "title": damage.text(head.meta.get("title", sid), CAPS["title"], "head"),
            "identity": head.identity,
            "done": absorbed,
            "pcless": head.meta.get("pcless") == "true",
            "branch_group": damage.text(head.meta.get("branch_group"), CAPS["ref"], "head"),
            "created": damage.text(created, CAPS["moment"], "head"),
            "locations": [damage.text(x, CAPS["ref"], "head") for x in hist["locations"]],
            "moments": [damage.text(x, CAPS["moment"], "head") for x in hist["times"]],
        },
        "rolling": damage.text(running.text, CAPS["summary"], "rolling_summary"),
        "chronicle": chron,
        "cast": cast,
        "locations": locations,
        "threads": threads,
        "commitments": commits,
        "status": {**t_status, **c_status},
        "facts": fact_slice,
        "relationships": rels,
        "links": [damage.text(x, CAPS["ref"], "continuity.json") for x in links],
        "groups": _groups(inputs, cast, used),
    }
    values = _visible(values)
    mine = tuple(_capped(sorted(damage.names | set(inputs.degraded)), ""))
    return Slices(sid=sid, identity=head.identity, values=values,
                  digests={name: digest(value) for name, value in values.items()},
                  paths=tuple(dict.fromkeys(used)), degraded=mine)
