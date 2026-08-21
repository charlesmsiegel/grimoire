"""Generic entity CRUD + content hashing over an arbitrary container root.

A "container root" is a world dir or a campaign dir; entities live at
`<root>/<kind>/<id>.md`. Entity ids are stable for life (rename changes only the
`name` frontmatter) so sync refs `(kind, id)` line up across world and campaign.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from . import atomic, statcache, tokens
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import safe_id, slugify, uniquify

log = logging.getLogger(__name__)

ENTITY_KINDS: tuple[str, ...] = ("locations", "lore", "items", "groups", "creatures")

# Everything copy-on-create / sync tracks as a flat `<kind>/<id>.md` file:
# generic entities plus greetings (which keep their own CRUD module).
SYNCED_KINDS: tuple[str, ...] = ENTITY_KINDS + ("greetings",)

# ---- secrecy (#49): who an activated entry is *for*, orthogonal to `owners` ----
# `owners` says what puts an entry in the prompt; `secrecy` says how the prompt
# is allowed to use it once there:
#   public  — the default, and the way every entry has always behaved
#   secret  — activates exactly as before, but renders under a heading telling
#             the model not to let uninvolved characters voice or act on it
#   gm-only — its BODY never reaches a prompt: `context.activate` drops it,
#             `_assemble` suppresses it as the current setting, and
#             `absorb.snapshots.group_snapshot` skips it
#
# Secrecy gates the body, not the record's existence. A gm-only location still
# has a NAME, and the app still uses it where it must refer to the place at all
# -- a mechanics sheet label for a location with a sheet
# (`context.mechanics`), the scene-suggestion picker (`suggest`). Suppressing
# those would break mechanics and make the location unpickable, which is a
# worse failure than naming a room the scene is already set in.
PUBLIC, SECRET, GM_ONLY = "public", "secret", "gm-only"
SECRECY_LEVELS: tuple[str, ...] = (PUBLIC, SECRET, GM_ONLY)


def normalize_secrecy(value: str | None) -> str:
    """One of `SECRECY_LEVELS` for a stored or incoming value; `PUBLIC` for
    anything else — a missing key, a blank, a hand-typo in the frontmatter.

    Lenient here and strict at the save boundary, the same split
    `entity_schema.invalid_values` documents: frontmatter is hand-editable and
    a typo in it must not take a turn down, but a typo arriving over HTTP is
    reported (`routes.entities._check_secrecy`) rather than silently downgraded
    to public — silently downgrading *secrecy* is the one direction that leaks.
    """
    v = (value or "").strip().lower()
    return v if v in SECRECY_LEVELS else PUBLIC


class EntityNotFound(Exception):
    pass


class UnknownKind(Exception):
    pass


class SameKindError(Exception):
    """`reclassify` was asked to move a record to the kind it already has.

    Its own type rather than a silent no-op: every caller of reclassify rewrites
    a pile of refs from an old key to a new one, and a no-op that returned the
    id unchanged would run all of that against `old == new` -- which is a
    delete-then-write of the same value in some ledgers and a dropped entry in
    others. A request that cannot mean anything is refused where it is made.
    """


def _check_kind(kind: str) -> None:
    if kind not in ENTITY_KINDS:
        raise UnknownKind(kind)


def _kind_dir(root: Path, kind: str) -> Path:
    return root / kind


def _entity_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


def _record_dir(root: Path, kind: str, eid: str) -> Path:
    """`<root>/<kind>/<eid>/` — what is filed *beside* a record rather than in
    it: its `assets/`, its descriptions sidecar, a group's state.md. Named here
    as well as in `overlay` because reclassify has to carry it, and a record
    that arrived under a new kind without its art would look like data loss."""
    return root / kind / eid


def list_entities(root: Path, kind: str) -> list[dict]:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    out: list[dict] = []
    if d.exists():
        for p in sorted(d.glob("*.md")):
            if not safe_id(p.stem):   # enumeration agrees with the resolvers
                continue
            meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
            # `tokens` LAST, after **meta: it is a measurement of the record,
            # not a field of it, so a file that happens to carry a `tokens:`
            # line in its frontmatter does not get to report its own cost.
            #
            # Unconditional rather than opt-in, and that is what makes the
            # memo load-bearing: this listing is on the turn loop's path too
            # (`context.world_state._world_info` sweeps all five kinds through
            # the overlay before deciding what activates), so an encode per
            # record per turn is the cost being avoided. Keyed on the same
            # signature `read_entity` uses, so the pair costs one encode
            # between them, not two.
            out.append({"id": p.stem, "name": meta.get("name", p.stem), **meta,
                        "tokens": tokens.record_tokens(p, body)})
    return out


def require_entity(root: Path, kind: str, eid: str) -> Path:
    """Assert `kind`/`eid` names a real entity here; return the record's path.

    One stat and no read, which is the point: the entity image routes gate on
    this per request (#373), and reading the record to answer a question
    `Path.exists` answers would put a file read, a frontmatter parse and a
    token encode on every upload. Raises the same two exceptions the readers
    here already raised inline -- the guard pair they were repeating, named
    once, the same shape `characters.require_version` is for the actor surface.
    """
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    return p


def _read_record(root: Path, kind: str, eid: str) -> tuple[dict, str]:
    p = require_entity(root, kind, eid)
    text = p.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    # `tokens` beside `meta`, not inside it: `meta` is the frontmatter this
    # record would be written back with, and every writer here round-trips it.
    # The `text` returned alongside is the same read, for callers that need to
    # hash the record rather than describe it.
    return {"meta": {"id": eid, **meta}, "body": body,
            "tokens": tokens.record_tokens(p, body)}, text


def read_entity(root: Path, kind: str, eid: str) -> dict:
    return _read_record(root, kind, eid)[0]


def read_entity_rev(root: Path, kind: str, eid: str) -> dict:
    """`read_entity` plus the `rev` of the very bytes it parsed (#35).

    The rev is what an editor echoes back on save so a write that would land on
    top of somebody else's can be refused. Hashing the text this call already
    read is the whole point: re-hashing the file afterwards would sample it a
    second time, and an external write landing in that gap hands the caller a
    rev describing content it was never shown -- which is precisely the write
    the precondition exists to stop, laundered into an approval.
    """
    record, text = _read_record(root, kind, eid)
    return {**record, "rev": content_hash(text)}


def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "", owners: str = "",
                  sd_prompt: str = "", taken=None, fields: dict[str, str] | None = None,
                  secrecy: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # `taken` widens the id namespace (overlay: world files + tombstones).
        # The record DIRECTORY counts as taken too, exactly as it does for an
        # actor (`characters.create_character` keys on `_char_dir`): it holds
        # the previous record-of-that-slug's assets and campaign-local sidecars,
        # so handing the id out again adopts them (#225). It also keeps a failed
        # `instantiate` rollback honest -- that rollback deletes the record
        # directory, and it must never be one that predates the create (Codex
        # review).
        return (_entity_path(root, kind, c).exists() or _record_dir(root, kind, c).is_dir()
                or (taken is not None and taken(c)))

    eid = uniquify(slugify(name), exists)
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    # Public is stored as the ABSENCE of the key, so an unmarked record is
    # byte-identical to one written before secrecy existed -- which keeps the
    # world->campaign sync (which hashes whole files) from seeing every entity
    # as edited the first time anyone saves one.
    level = normalize_secrecy(secrecy)
    if level != PUBLIC:
        meta["secrecy"] = level
    if sd_prompt:
        meta["sd_prompt"] = sd_prompt
    for k, v in (fields or {}).items():
        if v:
            meta[k] = v
    atomic.write_text(_entity_path(root, kind, eid), dump_frontmatter(meta, body))
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None, owners: str | None = None,
    sd_prompt: str | None = None, fields: dict[str, str] | None = None,
    secrecy: str | None = None,
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if keys is not None:
        meta["keys"] = keys
    if owners is not None:
        meta["owners"] = owners
    if secrecy is not None:
        level = normalize_secrecy(secrecy)
        if level == PUBLIC:
            meta.pop("secrecy", None)   # "back to public" removes the key
        else:
            meta["secrecy"] = level
    if sd_prompt is not None:
        meta["sd_prompt"] = sd_prompt
    for k, v in (fields or {}).items():
        if v:
            meta[k] = v
        else:
            meta.pop(k, None)
    new_body = cur_body if body is None else body
    atomic.write_text(p, dump_frontmatter(meta, new_body))


def delete_entity(root: Path, kind: str, eid: str) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    p.unlink()


def _occupied(root: Path, kind: str, eid: str) -> bool:
    """Is this slug spoken for in `kind`, by a record or by the orphaned asset
    directory of one (#225)? The half of the id namespace that is on disk here;
    a caller's `taken` adds the rest."""
    return _entity_path(root, kind, eid).exists() or _record_dir(root, kind, eid).is_dir()


def reclassify(root: Path, kind: str, eid: str, new_kind: str, taken=None,
               prefer: str | None = None) -> str:
    """Move `kind`/`eid` to `new_kind` in this root; return the id it landed on.

    The id is preserved -- that is the whole point, and the invariant every
    caller's ref rewrite is built on: a reclassify changes the kind directory
    and nothing else, so `(kind, id)` refs can be repointed key-for-key instead
    of re-resolved. It is preserved *where it can be*: a destination that
    already holds that slug (a record, or the orphaned asset directory of one,
    or whatever `taken` adds) gets the same `-2` suffix a create would take,
    and the id this returns is the one to rewrite refs to.

    `prefer` overrides the id to try FIRST, and it is what lets a world-side
    move bring a campaign's copy along: the world record has already landed on
    an id, and a copy that took a different one would stop being a copy of it.
    It is checked against the destination alone -- `taken` is the campaign's
    view of what its WORLD holds, and by then the world holds the very record
    being followed. Occupied, it falls back to the ordinary suffix walk, and
    the caller learns that from the id it gets back.

    Two moves, record first and record directory second, and the order is the
    one that fails safely. Crash between them and the record is readable under
    its new kind with its art stranded under the old -- visible on disk, nothing
    lost. The other order strands the art under the NEW kind, where a retry
    reads it as a taken slug and forks the record away from its own images.

    Which is also why a directory that will not move is logged rather than
    raised: the record has already moved by then, and answering with a 500 for
    art that is still on disk would tell the caller the reclassify did not
    happen when it did.
    """
    _check_kind(new_kind)
    src = require_entity(root, kind, eid)   # checks `kind`, `eid` and the file
    if kind == new_kind:
        raise SameKindError(f"{kind}/{eid}")
    _kind_dir(root, new_kind).mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # The same three questions `create_entity` asks, for the same reasons:
        # a record there, a record DIRECTORY there (#225's orphaned assets), or
        # a caller-supplied namespace (overlay: the world's files, tombstones).
        return _occupied(root, new_kind, c) or (taken is not None and taken(c))

    new_eid = (prefer if prefer is not None and safe_id(prefer)
               and not _occupied(root, new_kind, prefer)
               else uniquify(eid, exists))
    src.replace(_entity_path(root, new_kind, new_eid))
    old_dir = _record_dir(root, kind, eid)
    if old_dir.is_dir():
        try:
            old_dir.replace(_record_dir(root, new_kind, new_eid))
        except OSError as exc:
            log.warning("reclassified %s/%s to %s/%s but could not move %s (%s) -- "
                        "its images stay there, reachable only on disk",
                        kind, eid, new_kind, new_eid, old_dir, exc)
    return new_eid


def owner_refs(value: str) -> list[str]:
    """The `owners:` frontmatter line as the list of refs it names.

    One parse, here, rather than the split-strip-filter that
    `context.world_state._world_info` and every rewriter would each carry: an
    owner is a `<kind>:<id>` ref, and reclassify has to be able to find one.
    """
    return [o.strip() for o in (value or "").split(",") if o.strip()]


def rewrite_owner_refs(root: Path, old: str, new: str) -> list[tuple[str, str]]:
    """Repoint every `owners:` entry naming `old` at `new`, across every kind in
    this root. Returns the `(kind, id)` of each record it rewrote.

    An owner ref is `<kind>:<id>` and it is matched against the present-set
    `context.assemble` builds, so a location that stops being a location can
    never be present again -- by either spelling. Rewriting anyway is not about
    keeping the gate working; it is about not leaving a live ref pointing at a
    slug the reclassify just freed. Leave `locations:tidewatch` behind and the
    next location named Tidewatch inherits an owner gate written about a record
    it has never met, which is #225 through the one door that is plain text.

    Order-preserving and duplicate-collapsing: a record that already owned both
    spellings ends with one, in the position the old one held.
    """
    touched: list[tuple[str, str]] = []
    for kind in ENTITY_KINDS:
        for meta in list_entities(root, kind):
            refs = owner_refs(meta.get("owners", ""))
            if old not in refs:
                continue
            rewritten: list[str] = []
            for ref in refs:
                candidate = new if ref == old else ref
                if candidate not in rewritten:
                    rewritten.append(candidate)
            update_entity(root, kind, meta["id"], owners=", ".join(rewritten))
            touched.append((kind, meta["id"]))
    return touched


def content_hash(text: str) -> str:
    """`entity_hash` of a record you are holding rather than one on disk. A
    copier can then record a sync base that provably describes the bytes it
    copied, instead of re-reading a source that may have moved (#247)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def entity_hash(root: Path, kind: str, eid: str) -> str | None:
    if not safe_id(eid):
        return None
    p = _entity_path(root, kind, eid)
    sig = statcache.signature(p)
    if sig is None:
        return None
    return statcache.memo(
        "entity_hash", sig,
        lambda: content_hash(p.read_text(encoding="utf-8")))


def all_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            for p in sorted(d.glob("*.md")):
                if safe_id(p.stem):
                    refs.append((kind, p.stem))
    return refs


def synced_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in SYNCED_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            refs.extend((kind, p.stem) for p in sorted(d.glob("*.md")) if safe_id(p.stem))
    return refs


def entity_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        counts[kind] = sum(1 for p in d.glob("*.md") if safe_id(p.stem)) if d.exists() else 0
    return counts
