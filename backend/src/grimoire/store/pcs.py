"""Player-character containers: one folder per PC, one markdown persona per version.

Mirrors characters.py but with a simpler payload:
  <root>/pcs/<pid>/pc.md          # frontmatter: name, tags (comma-joined), default_version
  <root>/pcs/<pid>/<vid>.md       # frontmatter: PERSONA_FIELDS ; body: description
  <root>/pcs/<pid>/assets/<vid>/  # optional per-version images (#219)
  <root>/pcs/<pid>/history/<vid>/<rid>.md  # earlier texts of <vid>.md (#67)

Images live in the same per-version asset folder characters use, keyed on
``ASSET_BASE`` instead of "characters" -- `store.assets` was already
base-parameterised for the entity kinds, so PCs needed no new primitive. As for
characters, images are outside `dir_hash`/`snapshot`, so an avatar edit is
invisible to sync and to `overlay.materialize_actor`; assets overlay per file.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from . import assets, atomic, image_descriptions, statcache
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import safe_id, slugify, uniquify

#: Frontmatter scalars; the description is the body. `goals` and
#: `player_notes` (#65) are profile fields the narrator reads -- see `pc_block`
#: in templates/scene/_persona_blocks.j2. A file written before a field existed
#: simply lacks the key and `_load_persona` reads it as "", so adding one here
#: needs no migration.
PERSONA_FIELDS = ("name", "pronouns", "summary", "birthdate", "goals", "player_notes")

#: The `store.assets` base a PC's images are stored under -- the literal name of
#: the directory PCs already live in, so `<root>/pcs/<pid>/assets/<vid>/` sits
#: beside the persona files exactly as a character's does.
ASSET_BASE = "pcs"

#: The container's own file. Versions are `<vid>.md` in that same directory, so
#: this name is the one slug a version may not have -- see `_new_version_id`.
_META_NAME = "pc.md"


class PCNotFound(Exception):
    pass


class PCVersionNotFound(Exception):
    pass


class PCRevisionNotFoundError(Exception):
    pass


#: How many earlier texts each version keeps (#67). A structural bound, so a
#: PC edited every session for years does not grow without limit; it says
#: nothing about how anybody edits, and is cheap to raise.
HISTORY_KEEP = 20

#: The directory, beside the version files, that history lives under.
#: Deliberately a SUBDIRECTORY: `_version_ids` globs `*.md` non-recursively and
#: `snapshot`/`dir_hash` name their files explicitly, so nothing in here is a
#: version, is hashed for sync, or is copied by `overlay.materialize_actor`.
_HISTORY_DIR = "history"


def _pcs_dir(root: Path) -> Path:
    return root / "pcs"


def _pc_dir(root: Path, pid: str) -> Path:
    """The PC's directory. Raises PCNotFound for an id that doesn't name a
    child of the pcs dir -- same guard as characters._char_dir (#240)."""
    if not safe_id(pid):
        raise PCNotFound(pid)
    return _pcs_dir(root) / pid


def _meta_path(root: Path, pid: str) -> Path:
    return _pc_dir(root, pid) / _META_NAME


def _version_path(root: Path, pid: str, vid: str) -> Path:
    return _pc_dir(root, pid) / f"{vid}.md"


def _new_version_id(root: Path, pid: str, version_name: str) -> str:
    """A version id nothing under `pid` is using -- the meta file included.

    A version's file and the container's meta share one directory and one
    extension, so `version_name="PC"` slugs onto `pc.md`: `create_pc` wrote the
    persona there and then wrote the meta over it, leaving a PC that answered
    200 and 404 in the same breath (`_version_ids` skips `pc.md`, so `read_pc`
    saw no versions) while holding the name's slug against every later PC.
    `create_version` never had the bug -- by then `pc.md` exists, so its own
    existence check already stepped around it -- and now both allocate ids
    through the one function that knows why (#14)."""
    def taken(vid: str) -> bool:
        p = _version_path(root, pid, vid)
        return p == _meta_path(root, pid) or p.exists()
    return uniquify(slugify(version_name), taken)


def blank_persona(name: str) -> dict:
    return {**dict.fromkeys(PERSONA_FIELDS, ""), "name": name, "description": ""}


def _one_line(value: object) -> str:
    """A frontmatter scalar as the single line `dump_frontmatter` can store.

    `frontmatter` writes `key: value` lines, so a newline inside a value would
    be read back as a second, bogus key and the rest of the value lost. Every
    whitespace run folds to one space instead -- the summary has always been
    exposed to this, and the profile fields (#65) are where a pasted list of
    goals would hit it first."""
    return " ".join(str(value or "").split())


def _dump_persona(persona: dict) -> str:
    meta = {f: _one_line(persona.get(f, "")) for f in PERSONA_FIELDS}
    return dump_frontmatter(meta, persona.get("description", ""))


def _load_persona(text: str) -> dict:
    meta, body = parse_frontmatter(text)
    return {**{f: meta.get(f, "") for f in PERSONA_FIELDS}, "description": body.strip()}


def _require_pc(root: Path, pid: str) -> Path:
    d = _pc_dir(root, pid)   # raises PCNotFound for an unsafe id
    if not _meta_path(root, pid).exists():
        raise PCNotFound(pid)
    return d


def _read_meta(root: Path, pid: str) -> dict:
    meta, _ = parse_frontmatter(_meta_path(root, pid).read_text(encoding="utf-8"))
    return meta


def _write_meta(root: Path, pid: str, name: str, tags: list[str], default_version: str) -> None:
    atomic.write_text(_meta_path(root, pid), dump_frontmatter(
        {"name": name, "tags": ",".join(tags), "default_version": default_version}, ""))


def _tags_of(meta: dict) -> list[str]:
    # Stripped, matching `greetings._tags_list`: a PC's tags are compared to a
    # greeting's `requires_tags` verbatim, so a space on either side of the
    # comma silently stops every gated greeting from being offered.
    return [t for t in (x.strip() for x in meta.get("tags", "").split(",")) if t]


def create_pc(root: Path, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None, taken=None) -> tuple[str, str]:
    _pcs_dir(root).mkdir(parents=True, exist_ok=True)
    pid = uniquify(slugify(name), lambda c: _pc_dir(root, c).exists() or (taken and taken(c)))
    _pc_dir(root, pid).mkdir(parents=True)
    vid = _new_version_id(root, pid, version_name)
    atomic.write_text(_version_path(root, pid, vid), _dump_persona(persona or blank_persona(name)))
    _write_meta(root, pid, name, tags, vid)
    return pid, vid


def create_version(root: Path, pid: str, version_name: str, persona: dict) -> str:
    _require_pc(root, pid)
    vid = _new_version_id(root, pid, version_name)
    atomic.write_text(_version_path(root, pid, vid), _dump_persona(persona))
    return vid


def update_version(root: Path, pid: str, vid: str, persona: dict) -> None:
    """Overwrite a version's persona, keeping the text it replaces (#67).

    The earlier text is copied into the version's history first, and only when
    the write changes something: a Save with no edits must not push a real
    revision out of the retention window to record a duplicate. Snapshot then
    write, in that order, so a crash between the two leaves an extra history
    entry rather than an edit with no record of what it replaced."""
    p = require_version(root, pid, vid)
    text = _dump_persona(persona)
    keep_before_overwrite(root, pid, vid, text)
    atomic.write_text(p, text)


# ---- revision history (#67) ------------------------------------------------
#
# Versions are authored VARIANTS a campaign can lock (and locking purges the
# siblings), so they cannot double as an edit trail. History is the edit trail:
# every text `update_version` replaced, per version, newest kept. It records
# what went through the editor -- a sync accept or an import-version replaces a
# version file through its own reviewed path and is not an edit of this kind.
#
# Scope is whichever root was written: a world edit keeps world history, a
# campaign edit keeps the campaign copy's. Campaign history therefore starts
# with the first campaign edit, and its first entry is the inherited text that
# edit replaced.

def _history_dir(root: Path, pid: str, vid: str) -> Path:
    return _pc_dir(root, pid) / _HISTORY_DIR / vid


#: A revision id: the UTC timestamp `_record_revision` mints, plus the
#: `uniquify` suffix two snapshots in one microsecond would need. Anything else
#: in a history folder -- a sync client's conflict copy, a hand-placed file --
#: is not a revision and is not listed.
_REVISION_ID = re.compile(r"\d{8}T\d{12}Z(-\d+)?")


def _revision_ids(root: Path, pid: str, vid: str) -> list[str]:
    """Oldest first. Ids are UTC timestamps, so name order is time order --
    on one machine. A store shared between devices orders by each device's
    clock, which is the same trust every other stamp in the store extends."""
    d = _history_dir(root, pid, vid)
    if not d.is_dir():
        return []
    return sorted((p.stem for p in d.glob("*.md") if _REVISION_ID.fullmatch(p.stem)),
                  key=_revision_order)


def _revision_order(rid: str) -> tuple[str, int]:
    """Timestamp, then `uniquify`'s numeric suffix -- so `-10` follows `-9`."""
    stamp, _, n = rid.partition("-")
    return stamp, int(n or 1)


def keep_before_overwrite(root: Path, pid: str, vid: str, new_text: str) -> None:
    """Keep the version's current text in its history if `new_text` replaces it.

    The one door into history. `update_version` calls it for editor saves, and
    the library operations that overwrite a CAMPAIGN copy's version file in
    place -- a sync accept that copies the world's text over a diverged copy, an
    import of a locked version -- call it too, so the edit they clobber is the
    newest entry rather than silently gone. A missing file, or an identical one,
    records nothing."""
    p = _version_path(root, pid, vid)
    try:
        before = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    # Compared as the store would WRITE it, not byte for byte: a file from
    # before a field existed (no `goals:` line, say) re-dumps differently with
    # nothing changed, and a byte compare would spend a history slot on a Save
    # that edited nothing.
    if _dump_persona(_load_persona(before)) != new_text:
        _record_revision(root, pid, vid, before)


def forget_history(root: Path, pid: str, vid: str | None = None) -> None:
    """Drop one version's history, or (`vid=None`) the whole PC's.

    For every path that removes version files without `delete_version` --
    a lock purging sibling versions, an import replacing the locked one, a
    campaign copy reverting to inherited -- so a history never outlives the
    text it is the history of, and a later version reusing the id starts
    clean."""
    if not safe_id(pid) or (vid is not None and not safe_id(vid)):
        return
    target = _pc_dir(root, pid) / _HISTORY_DIR
    shutil.rmtree(target / vid if vid is not None else target, ignore_errors=True)


def _record_revision(root: Path, pid: str, vid: str, text: str) -> None:
    d = _history_dir(root, pid, vid)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    rid = uniquify(stamp, lambda c: (d / f"{c}.md").exists())
    atomic.write_text(d / f"{rid}.md", text)
    for old in _revision_ids(root, pid, vid)[:-HISTORY_KEEP]:
        (d / f"{old}.md").unlink(missing_ok=True)


def _saved_at(rid: str) -> str:
    """The ISO time a revision id names -- when that text was replaced."""
    try:
        when = datetime.strptime(rid[:22], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)
    except ValueError:
        return ""
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def list_revisions(root: Path, pid: str, vid: str) -> list[dict]:
    """The earlier texts of `vid`, newest first: `{id, saved, name}`.

    `name` is the persona name the text held, so a row can say whose text it
    was when a rename is among the edits."""
    require_version(root, pid, vid)
    d = _history_dir(root, pid, vid)
    out = []
    for rid in reversed(_revision_ids(root, pid, vid)):
        try:
            name = _load_persona((d / f"{rid}.md").read_text(encoding="utf-8"))["name"]
        except (OSError, UnicodeDecodeError):
            continue   # pruned or unreadable between the listing and the read
        out.append({"id": rid, "saved": _saved_at(rid), "name": name})
    return out


def read_revision(root: Path, pid: str, vid: str, rid: str) -> dict:
    require_version(root, pid, vid)
    p = _history_dir(root, pid, vid) / f"{rid}.md"
    if not safe_id(rid) or not p.is_file():
        raise PCRevisionNotFoundError(rid)
    return _load_persona(p.read_text(encoding="utf-8"))


def restore_revision(root: Path, pid: str, vid: str, rid: str) -> dict:
    """Put an earlier text back as the version's current one; return it.

    A restore is an ordinary `update_version`, so the text it replaces goes
    into history too and the restore can itself be undone."""
    persona = read_revision(root, pid, vid, rid)
    update_version(root, pid, vid, persona)
    return persona


def set_default_version(root: Path, pid: str, vid: str) -> None:
    require_version(root, pid, vid)
    meta = _read_meta(root, pid)
    _write_meta(root, pid, meta.get("name", pid), _tags_of(meta), vid)


def set_tags(root: Path, pid: str, tags: list[str]) -> None:
    _require_pc(root, pid)
    meta = _read_meta(root, pid)
    _write_meta(root, pid, meta.get("name", pid), tags, meta.get("default_version", ""))


def _version_ids(root: Path, pid: str) -> list[str]:
    # see characters._version_ids
    return sorted(p.stem for p in _pc_dir(root, pid).glob("*.md")
                  if p.name != _META_NAME and safe_id(p.stem))


def require_version(root: Path, pid: str, vid: str) -> Path:
    """Assert `pid`/`vid` name a real PC version; return the persona's path.

    Two stats and no read, which is the point: the image routes gate on this
    per request, and `GET .../images/avatar` is hit once per portrait per
    rendered grid. Doing it with `read_persona` -- as the first cut of that
    gate did -- put a file read and a frontmatter parse on the hottest route in
    the feature to answer a question `Path.exists` answers.

    Raises the same two exceptions the mutators here already raised inline;
    this is the guard pair they were each repeating, named once."""
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not safe_id(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    return p


def read_persona(root: Path, pid: str, vid: str) -> dict:
    return _load_persona(require_version(root, pid, vid).read_text(encoding="utf-8"))


def read_pc(root: Path, pid: str) -> dict:
    _require_pc(root, pid)
    meta = _read_meta(root, pid)
    version_ids = _version_ids(root, pid)
    if not version_ids:
        raise PCNotFound(pid)   # see characters.read_character
    # One read per version: this used to call `read_persona` twice for each one
    # (name and persona), re-reading and re-parsing the same file to fill two
    # keys of one dict.
    versions = []
    for v in version_ids:
        persona = read_persona(root, pid, v)
        versions.append({
            "id": v, "name": persona["name"], "persona": persona,
            # Same shape characters.read_character returns, so the editor's
            # image handling is one code path for both actor kinds (#219).
            "images": [i["name"] for i in assets.list_images(root, pid, v, ASSET_BASE)],
            "avatar_focus": assets.read_focus(root, pid, v, ASSET_BASE),
            "image_descriptions": image_descriptions.read_all(root, pid, v, ASSET_BASE),
        })
    default = meta.get("default_version", "")
    return {"meta": {"id": pid, "name": meta.get("name", pid), "tags": _tags_of(meta),
                     "default_version": default if default in version_ids else version_ids[0]},
            "versions": versions}


def _listed_dirs(root: Path) -> list[Path]:
    """The PC directories `list_pcs` walks, in its order -- see
    `characters._listed_dirs`."""
    d = _pcs_dir(root)
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_dir() and (p / _META_NAME).exists() and safe_id(p.name))


def listed_ids(root: Path) -> list[str]:
    """The ids `list_pcs` lists, reading no file: the same walk and the same
    at-least-one-addressable-version filter, without the meta read, the asset
    scan and the persona read per version the row carries. For a caller that
    counts PCs -- `overlay.cast_ids`, and through it the sheet tally."""
    return [pd.name for pd in _listed_dirs(root) if _version_ids(root, pd.name)]


def list_pcs(root: Path) -> list[dict]:
    out: list[dict] = []
    # the same walk `listed_ids` takes, so the two cannot disagree
    for pd in _listed_dirs(root):
        pid = pd.name
        meta = _read_meta(root, pid)
        version_ids = _version_ids(root, pid)
        if not version_ids:
            continue   # see read_pc: no addressable version, nothing to show
        default = meta.get("default_version", "")
        default = default if default in version_ids else version_ids[0]
        # The rail renders a portrait per row, so the summary carries the
        # same derived image fields characters.list_characters does. No
        # `localized_count`: only a character card's text is localized, so a
        # PC has no `embed-` images to count.
        #
        # This adds two stats per art-less PC and nothing more: both
        # `list_images` and `read_focus` return early on a directory or
        # sidecar that is not there, so the scan and the parse are only
        # paid by PCs that actually have images.
        images = assets.list_images(root, pid, default, ASSET_BASE)
        names = [i["name"] for i in images]
        out.append({"id": pid, "name": meta.get("name", pid), "tags": _tags_of(meta),
                    "default_version": default,
                    "has_avatar": assets.AVATAR in names,
                    # The token `list_images` already computed, for the rail's
                    # `?v=`: a portrait URL without one is never served
                    # immutable, so every visit revalidated every PC's.
                    "avatar_v": next((i["v"] for i in images if i["name"] == assets.AVATAR),
                                     None),
                    "avatar_focus": assets.read_focus(root, pid, default, ASSET_BASE),
                    "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
                    "versions": [{"id": v, "name": read_persona(root, pid, v)["name"]}
                                 for v in version_ids]})
    return out


def delete_version(root: Path, pid: str, vid: str) -> None:
    p = require_version(root, pid, vid)
    if len(_version_ids(root, pid)) == 1:
        raise ValueError("cannot delete the last version of a PC")
    p.unlink()
    # the persona was the only thing that made this version's art addressable;
    # after the unlink for the reason characters.delete_version gives (#360)
    assets.delete_version_images(root, pid, vid, ASSET_BASE)
    # ...and its history, so a later version reusing the id does not inherit a
    # dead version's edit trail (#67).
    forget_history(root, pid, vid)
    meta = _read_meta(root, pid)
    if meta.get("default_version") == vid:
        _write_meta(root, pid, meta.get("name", pid), _tags_of(meta), _version_ids(root, pid)[0])


def delete_pc(root: Path, pid: str) -> None:
    _require_pc(root, pid)
    shutil.rmtree(_pc_dir(root, pid))


def version_hash(root: Path, pid: str, vid: str) -> str | None:
    if not safe_id(pid) or not safe_id(vid):
        return None
    p = _version_path(root, pid, vid)
    sig = statcache.signature(p)
    if sig is None:
        return None
    return statcache.memo(
        "pc_version_hash", sig,
        lambda: hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest())


def dir_content_hash(files: list[tuple[str, str]]) -> str:
    """`dir_hash` over (name, text) pairs you are holding rather than files on
    disk — see `snapshot`."""
    h = hashlib.sha256()
    for name, text in files:
        h.update(name.encode("utf-8"))
        h.update(text.encode("utf-8"))
    return h.hexdigest()


def snapshot(root: Path, pid: str) -> tuple[str, list[tuple[str, str]]] | None:
    """One read of the whole PC: its `dir_hash` and the (name, text) pairs that
    hash covers, meta first. See `characters.snapshot` (#247)."""
    if not safe_id(pid) or not _meta_path(root, pid).exists():
        return None
    version_ids = _version_ids(root, pid)
    if not version_ids:
        # read_character/read_pc refuse an actor with no addressable version,
        # so reporting a hash makes sync see a changed record it cannot then
        # read. `snapshot` and `dir_hash` have to agree (#247), so both say
        # absent -- the same answer as for an actor that isn't there (#259 review)
        return None
    files = [_meta_path(root, pid)] + [_version_path(root, pid, v) for v in version_ids]
    pairs = [(p.name, p.read_text(encoding="utf-8")) for p in files]
    return dir_content_hash(pairs), pairs


def _dir_hash_compute(files: list[Path]) -> str:
    return dir_content_hash([(p.name, p.read_text(encoding="utf-8")) for p in files])


def dir_hash(root: Path, pid: str) -> str | None:
    """Whole-actor content hash: pc.md plus every version persona, name-tagged.
    Only these files feed the hash, so nothing else in the dir can surface in sync."""
    if not safe_id(pid) or not _meta_path(root, pid).exists():
        return None
    version_ids = _version_ids(root, pid)
    if not version_ids:
        # read_character/read_pc refuse an actor with no addressable version,
        # so reporting a hash makes sync see a changed record it cannot then
        # read. `snapshot` and `dir_hash` have to agree (#247), so both say
        # absent -- the same answer as for an actor that isn't there (#259 review)
        return None
    files = [_meta_path(root, pid)] + [_version_path(root, pid, v) for v in version_ids]
    return statcache.memo("pc_dir_hash", statcache.signature(*files),
                          lambda: _dir_hash_compute(files))


def pc_count(root: Path) -> int:
    d = _pcs_dir(root)
    return sum(1 for p in d.iterdir()
               if p.is_dir() and (p / _META_NAME).exists() and safe_id(p.name)) if d.exists() else 0


def pc_exists(root: Path, pid: str) -> bool:
    """Whether `root` holds a PC under this id -- see `characters.character_exists`."""
    return safe_id(pid) and _meta_path(root, pid).exists()


def pc_refs(root: Path) -> list[str]:
    d = _pcs_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and (p / _META_NAME).exists() and safe_id(p.name))
