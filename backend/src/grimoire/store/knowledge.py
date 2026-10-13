"""Authoritative knowledge overrides: who knows, suspects or never heard what
happened in a scene (roadmap 11, §7). Stored at ``<campaign>/knowledge.json``.

Epistemic retrieval (11) derives, for each actor, whether they may see a piece
of recalled history: they were recorded present, they wrote it, or nothing
shows they have it. A derivation is wrong in exactly the cases a person can
see and a structure cannot -- "Winifred told her on the walk back", "a
whispered aside nobody else heard". This file is where the user says so, and
what it says beats every derived signal.

    {"version": 1,
     "entries": {"k-<8 hex>": {
        "audience": "characters:<id>" | "groups:<id>" | "*",
        "audience_name": "<display name at write time>",     # "" for "*"
        "status": "knows" | "suspects" | "unaware",
        "subject": {"kind": "posts", "scene": "<identity>",
                    "posts": [{"key", "part", "variant", "digest"}, ...]}
                 | {"kind": "scene", "scene": "<identity>", "digest": "<hash>"},
        "note": "...", "source": "manual" | "accepted",
        "created": "<iso>", "updated": "<iso>"}}}

**Addressed by identity and fingerprinted content, never by sid or index.** A
sid moves on rename and is reissued after a delete; an index moves on every
cut. A post is named by its key (`post_key`: ``r-<response_id>`` with its
``response_part``, else ``p-<post_id>``), the response's active variant and a
digest of its raw text, all at write time; a whole scene by
`knowledge_scene.scene_digest`: the transcript hash (which covers the hidden
flag) with the title and chronicle summary a whole-scene grant exposes.

**A grant fails closed, a withholding does not.** A ``knows`` or ``suspects``
entry matches only while its fingerprint holds (`holds`): a swipe, Keep
writing, an edit or a hide makes it stop, because the user judged *that*
text and not whatever replaced it. An ``unaware`` entry keeps matching through
all of it, since withholding is the safe direction. The same asymmetry runs
through the audience: an actor or group audience whose current display name is
not the one recorded no longer grants (a reissued slug is somebody else), and
a group that no longer resolves expands to nobody.

**Precedence** (`decide`) is subject, then audience, then recency, then the
withholding tie-break -- so a post-level ``unaware`` on a whisper is not
overridden by a broad "the Watch knows that scene" (spec §7.2). ``"*"`` is
every actor except the post's author, so "nobody else heard" does not silence
the whisperer about their own line.

**Nothing derived writes here.** The note is user prose and never reaches a
prompt. The writers are the ledger routes (a hand edit, journalled as
``manual``), the delete hooks that prune a deleted actor's or group's entries,
the branch copy, and undo -- `test_knowledge_writer_guard.py` holds that list.

This module may not import ``overlay``, ``membership``, ``responses`` or the
scene reader: ``overlay`` calls the delete hook here and ``undo`` restores
through it, and each of those leads back to ``overlay``. Everything that has
to read a scene or the group rosters -- `knowledge_scene.scene_facts`,
`current` and `audiences_for` -- lives in `store/knowledge_scene.py`, and this
module takes their results as arguments.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import atomic, group_refs, journal, locks
from .campaigns import paths as campaigns_paths
from .paths import now_iso, safe_id

log = logging.getLogger(__name__)

VERSION = 1

KNOWS, SUSPECTS, UNAWARE = "knows", "suspects", "unaware"
STATUSES = (KNOWS, SUSPECTS, UNAWARE)
GRANTS = (KNOWS, SUSPECTS)
SOURCES = ("manual", "accepted")
EVERYONE = "*"
AUDIENCE_KINDS = ("characters", "groups")

#: The tie-break on an exact tie, withholding first (spec §7.2 rule 4, the
#: direction #116's filter breaks ties in).
_WITHHOLDING = {UNAWARE: 0, SUSPECTS: 1, KNOWS: 2}

#: What a `Match` says decided it (spec §3.5), by the audience that matched.
BASIS = {0: "override", 1: "override_group", 2: "override_all"}

_IDENTITY = re.compile(r"\A[0-9a-f]{32}\Z")
_KID = re.compile(r"\Ak-[0-9a-f]{8}\Z")
_KEY = re.compile(r"\A[rp]-[0-9A-Za-z_-]{1,128}\Z")
_POST_DIGEST = re.compile(r"\A[0-9a-f]{16}\Z")
_SCENE_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")

#: Why a pruned entry's journal row offers no Undo. Restoring a statement about
#: an actor whose id has since been handed to somebody new would grant the
#: newcomer knowledge -- the leak the prune is there to stop.
FORGOTTEN_WHY = ("the actor or group this was about was deleted; add the "
                 "statement again if it still holds")


class KnowledgeError(ValueError):
    """A knowledge entry, or the file holding them, is not of the right shape."""


# ------------------------------------------------------------------- the shape


def _text(value, what: str) -> str:
    if not isinstance(value, str):
        raise KnowledgeError(f"{what} must be text")
    return value


def valid_audience(value) -> bool:
    if value == EVERYONE:
        return True
    if not isinstance(value, str):
        return False
    kind, sep, rid = value.partition(":")
    return bool(sep) and kind in AUDIENCE_KINDS and safe_id(rid)


def valid_identity(value) -> bool:
    return isinstance(value, str) and bool(_IDENTITY.match(value))


def valid_key(value) -> bool:
    return isinstance(value, str) and bool(_KEY.match(value))


def _subject(value) -> dict:
    if not isinstance(value, dict):
        raise KnowledgeError("a subject must be an object")
    kind = value.get("kind")
    scene = value.get("scene")
    if not valid_identity(scene):
        raise KnowledgeError("a subject names its scene by identity")
    if kind == "scene":
        digest = value.get("digest")
        if not isinstance(digest, str) or not _SCENE_DIGEST.match(digest):
            raise KnowledgeError("a scene subject carries its transcript hash")
        return {"kind": "scene", "scene": scene, "digest": digest}
    if kind != "posts":
        raise KnowledgeError("a subject is posts or a scene")
    posts = value.get("posts")
    if not isinstance(posts, list) or not posts:
        raise KnowledgeError("a posts subject lists at least one post")
    # A dict, not a set: this module defines `add`, and the lock-domain guard
    # resolves a bare-name method call against the module's own functions.
    out, seen = [], {}
    for post in posts:
        one = _subject_post(post)
        if (one["key"], one["part"]) in seen:
            raise KnowledgeError("a subject names each post once")
        seen[(one["key"], one["part"])] = True
        out.append(one)
    return {"kind": "posts", "scene": scene, "posts": out}


def _subject_post(post) -> dict:
    if not isinstance(post, dict) or not valid_key(post.get("key")):
        raise KnowledgeError("a subject post needs an r- or p- key")
    digest = post.get("digest")
    if not isinstance(digest, str) or not _POST_DIGEST.match(digest):
        raise KnowledgeError("a subject post carries its text digest")
    return {"key": post["key"], "part": _text(post.get("part", ""), "a post's part"),
            "variant": _text(post.get("variant", ""), "a post's variant"), "digest": digest}


def normalize(entry) -> dict:
    """`entry` as it is stored, or `KnowledgeError`. Unknown keys are dropped;
    `created` and `updated` are kept when they are text."""
    if not isinstance(entry, dict):
        raise KnowledgeError("an entry must be an object")
    audience = entry.get("audience")
    if not valid_audience(audience):
        raise KnowledgeError("an audience is characters:<id>, groups:<id> or *")
    status = entry.get("status")
    if status not in STATUSES:
        raise KnowledgeError("a status is knows, suspects or unaware")
    source = entry.get("source", "manual")
    if source not in SOURCES:
        raise KnowledgeError("a source is manual or accepted")
    name = "" if audience == EVERYONE else _text(entry.get("audience_name", ""),
                                                   "an audience name").strip()
    if audience != EVERYONE and not name:
        # The name is the backstop against a reissued slug (§7.5): a grant
        # with none would match whoever holds the id next.
        raise KnowledgeError("an actor or group audience records its display name")
    out = {"audience": audience,
           "audience_name": name,
           "status": status,
           "subject": _subject(entry.get("subject")),
           "note": _text(entry.get("note", ""), "a note"),
           "source": source}
    for stamp in ("created", "updated"):
        if isinstance(entry.get(stamp), str) and entry[stamp]:
            out[stamp] = entry[stamp]
    return out


# ----------------------------------------------------------------------- reads


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "knowledge.json"


def _entries_of(doc) -> dict | None:
    """The raw entries map of a parsed document, or None for the wrong shape --
    a version this build does not know included, since a newer build's
    entries may carry fields that change what they mean."""
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), dict):
        return None
    if doc.get("version", VERSION) != VERSION:
        return None
    return doc["entries"]


def read_checked(cid: str) -> tuple[dict[str, dict], bool]:
    """Every entry that reads, and whether nothing was lost on the way.

    A missing file is intact and empty. A file that will not parse, has the
    wrong shape, or holds an entry that does not normalize is NOT intact --
    the lost entry might have been an ``unaware``, so a reader deciding what
    an actor may see must not treat what is left as the whole story.
    """
    try:
        raw = _path(cid).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, True
    except (OSError, UnicodeDecodeError):
        return {}, False
    try:
        entries = _entries_of(json.loads(raw))
    except ValueError:
        return {}, False
    if entries is None:
        return {}, False
    out: dict[str, dict] = {}
    intact = True
    for kid, entry in entries.items():
        try:
            if not isinstance(kid, str) or not _KID.match(kid):
                raise KnowledgeError("an entry id is k- and 8 hex digits")
            out[kid] = normalize(entry)
        except KnowledgeError:
            intact = False
    return out, intact


def read(cid: str) -> dict[str, dict]:
    """Every entry that reads: an unparseable file reads as empty, and a
    malformed entry is skipped."""
    return read_checked(cid)[0]


def get(cid: str, kid: str) -> dict | None:
    return read(cid).get(kid)


def get_strict(cid: str, kid: str) -> dict | None:
    """One entry for undo's compare-and-swap, raising `KnowledgeError` when
    the file or the entry will not read -- a None there would compare equal to
    a genuinely absent entry and let a reversal write over a file it could not
    see."""
    doc = _load_strict(cid)
    if kid not in doc["entries"]:
        return None
    return normalize(doc["entries"][kid])


def require_readable(cid: str, *, intact: bool = False) -> None:
    """Preflight for a write that depends on the file: refuse, before anything
    happens, when it cannot be read -- rather than delete an actor and leave
    its grants for the next holder of the id. `intact` also refuses a file
    holding an entry that does not read, which a branch needs: the entry it
    could not copy might be an `unaware`."""
    try:
        _load_strict(cid)
        if intact and not read_checked(cid)[1]:
            raise KnowledgeError("an entry cannot be read")
    except KnowledgeError as exc:
        raise group_refs.GroupIdentityConflict(
            "knowledge.json cannot be read; repair it before deleting, reclassifying "
            "or branching what it names") from exc


# -------------------------------------------------------------------- mutators


def _load_strict(cid: str) -> dict:
    """The whole document for a read-modify-write, refusing one it cannot
    represent rather than publishing an empty ledger over it (the
    `facts._read_ledger` rule). Only a missing file is an empty ledger; one
    that exists and cannot be opened is refused like one that will not parse. Entries it does not touch are written back
    verbatim, so a malformed one a person can still repair survives an
    unrelated write."""
    try:
        doc = json.loads(_path(cid).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": VERSION, "entries": {}}
    except (OSError, ValueError) as exc:
        raise KnowledgeError("knowledge.json cannot be read") from exc
    if _entries_of(doc) is None:
        raise KnowledgeError("knowledge.json does not hold a knowledge ledger")
    return doc


def _write(cid: str, doc: dict) -> None:
    doc["version"] = VERSION
    atomic.write_text(_path(cid), json.dumps(doc, indent=2, sort_keys=True,
                                             ensure_ascii=False) + "\n")


def _new_id(taken) -> str:
    while True:
        kid = f"k-{uuid.uuid4().hex[:8]}"
        if kid not in taken:
            return kid


def mint_id(cid: str) -> str:
    """A free id, for a caller that journals a create against it and so has
    to know it before `add`. Free only while the caller holds the campaign
    lock across both calls, which the ledger route does."""
    return _new_id(_load_strict(cid)["entries"])


def add(cid: str, entry: dict, kid: str | None = None) -> str:
    """Store a new entry and return its id (`kid` when given, which must be
    free). `created` is stamped now unless the entry carries one."""
    record = normalize(entry)
    record.setdefault("created", now_iso())
    record.pop("updated", None)
    if kid is not None and not _KID.match(kid):
        raise KnowledgeError(f"not a knowledge id: {kid!r}")
    with locks.campaign_lock(cid):
        doc = _load_strict(cid)
        if kid is None:
            kid = _new_id(doc["entries"])
        elif kid in doc["entries"]:
            raise KnowledgeError(f"{kid} is already taken")
        doc["entries"][kid] = record
        _write(cid, doc)
    return kid


def update(cid: str, kid: str, entry: dict, *, renew: bool = False) -> dict:
    """Replace an entry's contents and stamp `updated`. When it was first
    written is kept -- that is what recency ranks on -- unless `renew`: a
    statement pointed at a new subject or audience is a new statement, and
    ranks as one. `KeyError` for an id the file does not hold."""
    record = normalize(entry)
    with locks.campaign_lock(cid):
        doc = _load_strict(cid)
        if kid not in doc["entries"]:
            raise KeyError(kid)
        stored = doc["entries"][kid]
        created = None if renew else (stored.get("created") if isinstance(stored, dict)
                                      else None)
        record["created"] = created if isinstance(created, str) and created else now_iso()
        record["updated"] = now_iso()
        doc["entries"][kid] = record
        _write(cid, doc)
    return copy.deepcopy(record)


def remove(cid: str, kid: str) -> dict | None:
    """Delete one entry; what it held, or None when there was none."""
    with locks.campaign_lock(cid):
        doc = _load_strict(cid)
        if kid not in doc["entries"]:
            return None
        gone = doc["entries"].pop(kid)
        _write(cid, doc)
    return gone if isinstance(gone, dict) else None


def restore(cid: str, kid: str, value) -> None:
    """Put one entry back exactly as `value` (None removes it): undo's door."""
    if not isinstance(kid, str) or not _KID.match(kid):
        raise KnowledgeError(f"not a knowledge id: {kid!r}")
    record = None if value is None else normalize(value)
    with locks.campaign_lock(cid):
        doc = _load_strict(cid)
        if record is None:
            if doc["entries"].pop(kid, None) is None:
                return
        else:
            doc["entries"][kid] = record
        _write(cid, doc)


def _restores(ref: str):
    """Whether a journalled reversal would put back an entry about `ref`."""
    def matches(plan: dict) -> bool:
        target, value = plan.get("target"), plan.get("restore")
        return (isinstance(target, dict) and target.get("w") == "knowledge"
                and isinstance(value, dict) and value.get("audience") == ref)
    return matches


class Forgotten(dict):
    """What `forget_audience` pruned, plus how many journalled Undo offers it
    withdrew. True when either was written -- a caller outside the activity
    middleware bumps the campaign's revision on that."""
    retired: int = 0

    def __bool__(self) -> bool:
        return len(self) > 0 or self.retired > 0


def forget_audience(cid: str, ref: str) -> Forgotten:
    """Remove every entry whose audience is `ref`, a deleted actor or group,
    and return what went.

    Called beside the delete, under its hold, so a record later created under
    the same id inherits nothing (#225's rule, which `overlay` already applies
    to the appearance record). Two things go:

    - the entries, each journalled as a ``manual`` row with no reversal
      (`FORGOTTEN_WHY`);
    - every journalled Undo that would put an entry about `ref` back -- an
      earlier hand removal or edit -- which is retired first. Restoring one
      after a same-named actor took the id would grant the newcomer what the
      user said of somebody else.

    Raises `KnowledgeError` (or the journal's conflict) for a file it cannot
    read; the deletes that call this preflight with `require_readable` so
    nothing is removed before that is known.
    """
    if not valid_audience(ref) or ref == EVERYONE:
        return Forgotten()
    with locks.campaign_lock(cid):
        doc = _load_strict(cid)
        gone = Forgotten({kid: e for kid, e in doc["entries"].items()
                          if isinstance(e, dict) and e.get("audience") == ref})
        gone.retired = journal.retire_offers(cid, _restores(ref), FORGOTTEN_WHY)
        if not len(gone):
            return gone
        for kid in gone:
            del doc["entries"][kid]
        _write(cid, doc)
    try:
        journal.append(cid, [{
            "scene": "", "source": "manual", "kind": "knowledge",
            "ref": {"kind": "knowledge", "id": kid}, "field": "status",
            "label": f"{entry.get('audience_name') or ref} — knowledge removed",
            "before": str(entry.get("status") or ""), "after": "",
            "undo": None, "why": FORGOTTEN_WHY} for kid, entry in gone.items()])
    except Exception:  # the prune landed; only its history did not
        log.warning("could not journal the knowledge pruned with %s in %s", ref, cid,
                    exc_info=True)
    return gone


def _branch_copy(record: dict, dst: str, rid_map: Mapping[str, str],
                 kept: set[tuple[str, str]]) -> dict | None:
    """What `record` becomes in the branch `dst`, or None when it does not
    travel. A grant travels only whole; a withholding travels as much of
    itself as the branch keeps."""
    subject = record["subject"]
    withholds = record["status"] == UNAWARE
    if subject["kind"] == "scene":
        # An `unaware` never consults the hash, so the shorter transcript is
        # no reason to drop it; a grant's hash would never hold there.
        return {**record, "subject": {**subject, "scene": dst}} if withholds else None
    posts = []
    for p in subject["posts"]:
        key = p["key"]
        new = rid_map.get(key[2:]) if key.startswith("r-") else key[2:]
        if (p["key"], p["part"]) not in kept or not new:
            if not withholds:
                return None
            continue
        posts.append({**p, "key": f"{key[:2]}{new}"})
    if not posts:
        return None
    return {**record, "subject": {**subject, "scene": dst, "posts": posts}}


def copy_for_branch(cid: str, src: str, dst: str, rid_map: Mapping[str, str],
                    copied: Iterable[tuple[str, str]]) -> int:
    """Copy the scene `src`'s statements into its branch `dst`, re-keying
    ``r-`` ids through `rid_map` and keeping variant and digest (the cloned
    responses keep their variant ids, and the text is the same). `copied` is
    the ``(key, part)`` pairs of the posts the branch keeps.

    Asymmetric on purpose, as matching is (§7.1): a ``knows``/``suspects``
    about posts travels only when every post it names is kept (the user
    judged them together) and a whole-scene one never does (the branch's
    hash differs by construction); an ``unaware`` travels narrowed to the kept
    posts, and a whole-scene ``unaware`` travels as it is -- dropping one
    would show the branch's reader a post the user said nobody heard. Each
    copy gets a fresh id. Returns how many were copied.
    """
    kept = set(copied)
    with locks.campaign_lock(cid):
        doc = _load_strict(cid)
        fresh: dict[str, dict] = {}
        for entry in doc["entries"].values():
            # Fail closed: an entry that does not read cannot be told apart
            # from an `unaware` the branch must carry.
            record = normalize(entry)
            if record["subject"]["scene"] != src:
                continue
            copy_ = _branch_copy(record, dst, rid_map, kept)
            if copy_ is not None:
                fresh[_new_id({**doc["entries"], **fresh})] = copy_
        if not fresh:
            return 0
        doc["entries"].update(fresh)
        _write(cid, doc)
    return len(fresh)


def drop_scene(cid: str, identity: str) -> int:
    """Remove every entry about the scene `identity`. Only a discarded branch
    sibling calls this: a scene the user deleted keeps its entries, listed by
    the ledger as pointing at a deleted scene, because a hand-written
    statement is not removed behind the user's back (spec §7.5)."""
    with locks.campaign_lock(cid):
        try:
            doc = _load_strict(cid)
        except KnowledgeError:
            return 0
        gone = [kid for kid, e in doc["entries"].items()
                if isinstance(e, dict) and isinstance(e.get("subject"), dict)
                and e["subject"].get("scene") == identity]
        if not gone:
            return 0
        for kid in gone:
            del doc["entries"][kid]
        _write(cid, doc)
    return len(gone)


# ---------------------------------------------------------------- fingerprints


def post_key(message: dict) -> str:
    """A post's key in 08's spelling: ``r-<response_id>``, else
    ``p-<post_id>``, else "" (a legacy post or a synthetic line)."""
    if message.get("response_id"):
        return f"r-{message['response_id']}"
    if message.get("post_id"):
        return f"p-{message['post_id']}"
    return ""


def post_part(message: dict) -> str:
    """The part of a reply split around a roll; "" for a first part or a post."""
    part = message.get("response_part") or ""
    return part if isinstance(part, str) else str(part)


def digest(text: str) -> str:
    """The raw-text digest a post fingerprint carries."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _variant(message: dict, variants: Mapping) -> str:
    rid = message.get("response_id")
    if not rid:
        return ""
    active = (variants.get(rid) or (None, []))[0]
    return active if isinstance(active, str) else ""


def fingerprint(message: dict, variants: Mapping) -> dict:
    """The subject post a statement about `message` stores. `variants` is
    `responses.variants_by_response` for the scene."""
    return {"key": post_key(message), "part": post_part(message),
            "variant": _variant(message, variants),
            "digest": digest(str(message.get("content") or ""))}


def shown_fingerprint(message: dict, view: dict, variants: Mapping) -> dict:
    """`fingerprint`, plus `shown`: the digest of the post's prompt-view text
    (`view`, its `regex.view` copy), which is what a grant agrees to. The one
    spelling of what a client sends back as `expect`, for the Knowledge
    ledger's scene read and for a capture's accept (11-S3) alike."""
    return {**fingerprint(message, variants), "shown": digest(str(view.get("content") or ""))}


@dataclass(frozen=True)
class SceneFacts:
    """What a scene holds now, for checking fingerprints against it: each
    keyed post's ``(variant, digest, hidden)`` and the transcript hash. Built
    by `knowledge_scene.scene_facts`. A post whose key appears twice is
    recorded as `AMBIGUOUS`, which no fingerprint matches."""
    posts: Mapping[tuple[str, str], tuple[str, str, bool]] = field(default_factory=dict)
    digest: str = ""


#: What `SceneFacts` records for a key two posts share: hidden, so it holds
#: nothing -- a statement cannot be about one of them without saying which.
AMBIGUOUS = ("", "", True)


def holds(entry: dict, facts: SceneFacts | None) -> bool:
    """Whether the text `entry` was written about is still the text there.

    A posts subject holds while every post it names is present, not hidden,
    on the same variant and with the same digest; a scene subject while the
    transcript hash is unchanged (it covers the hidden flag). With no facts
    (the scene could not be read) nothing holds.
    """
    if facts is None:
        return False
    subject = entry["subject"]
    if subject["kind"] == "scene":
        return subject["digest"] == facts.digest
    for p in subject["posts"]:
        now = facts.posts.get((p["key"], p["part"]))
        if now is None or now[2] or now[0] != p["variant"] or now[1] != p["digest"]:
            return False
    return True


# ------------------------------------------------------------------ precedence


@dataclass(frozen=True)
class Audiences:
    """Who an audience reaches now: each actor's current display name, and
    each group's current name with its affiliated actors -- 07's
    `membership.affiliated`, leader first. Built by
    `knowledge_scene.audiences_for`."""
    names: Mapping[str, str] = field(default_factory=dict)
    groups: Mapping[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)


@dataclass(frozen=True)
class Match:
    kid: str
    status: str
    basis: str


def _audience_rank(entry: dict, actor: str, author: str | None,
                   aud: Audiences) -> int | None:
    who, grants = entry["audience"], entry["status"] in GRANTS
    if who == actor:
        if grants and (aud.names.get(actor) or None) != entry["audience_name"]:
            return None
        return 0
    if who == EVERYONE:
        return None if actor == author else 2
    if who.startswith("groups:"):
        group = aud.groups.get(who)
        if group is None or actor not in group[1]:
            return None
        if grants and group[0] != entry["audience_name"]:
            return None
        return 1
    return None


def _subject_rank(entry: dict, scene: str, post: tuple[str, str] | None) -> int | None:
    subject = entry["subject"]
    if subject["scene"] != scene:
        return None
    if subject["kind"] == "scene":
        return 1
    if post is None or not post[0]:
        return None
    return 0 if any((p["key"], p["part"]) == post for p in subject["posts"]) else None


def decide(entries: Mapping[str, dict], *, actor: str, scene: str,
           post: tuple[str, str] | None, author: str | None,
           facts: SceneFacts | None, audiences: Audiences) -> Match | None:
    """The entry that settles what `actor` has of one part, or None.

    `post` is the part's ``(key, part)``; None for a scene's summary or title,
    which only a whole-scene entry reaches. A keyless post likewise matches
    only whole-scene entries. `author` is the actor that wrote the post, whom
    ``"*"`` never covers. `facts` is the scene as it reads now.
    """
    best: tuple | None = None
    for kid, entry in entries.items():
        s = _subject_rank(entry, scene, post)
        if s is None:
            continue
        a = _audience_rank(entry, actor, author, audiences)
        if a is None:
            continue
        if entry["status"] in GRANTS and not holds(entry, facts):
            continue
        # Lower ranks first; within them the newest `created`; then the
        # withholding status. `created` is ISO text, so it sorts as time.
        key = (s, a, _Desc(entry.get("created", "")), _WITHHOLDING[entry["status"]], kid)
        if best is None or key < best[0]:
            best = (key, kid, entry, a)
    if best is None:
        return None
    _key, kid, entry, a = best
    return Match(kid=kid, status=entry["status"], basis=BASIS[a])


@dataclass(frozen=True, order=False)
class _Desc:
    """A string that sorts descending, for one slot of a sort key."""
    value: str

    def __lt__(self, other: _Desc) -> bool:
        return self.value > other.value

    def __gt__(self, other: _Desc) -> bool:
        return self.value < other.value
