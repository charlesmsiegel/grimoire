"""characters: catalog responsibilities."""

from __future__ import annotations

import functools
import os
from pathlib import Path

from .. import assets, statcache, taglines, voice_anchors
from .. import frontmatter as _store_frontmatter
from .. import paths as _store_paths
from . import _memo, _paths, _reads


def list_characters(root: Path) -> list[dict]:
    """One row per character, in `_listed_dirs` order: what the grid draws.

    Each row is memoized on the stamps of everything it was built from
    (`_row`), so on an unchanged library this is a directory listing and a
    handful of stats per character -- no file is opened. The rows are copies:
    callers fold counts into them in place (`greetings.add_featuring_counts`),
    and the memo must not see that.
    """
    chars = _paths._chars_dir(root)
    out: list[dict] = []
    for cid in _listed_names(chars):
        row = _row(root, chars, cid)
        if row is not None:
            out.append({**row, "versions": [dict(v) for v in row["versions"]]})
    return out


def _listed_names(chars: Path) -> list[str]:
    """`_listed_dirs`' names, off one `os.scandir` and no stat per entry.

    The walk `roster` and `listed_ids` take, so the three cannot disagree about
    which directories are characters -- except that the `character.md` test is
    left to `_row`, which has to stat that file anyway and answers None for a
    directory without one. Sorted as `_listed_dirs` sorts its paths: siblings
    compare by name, case-folded where the platform's paths are
    (`os.path.normcase`), which is pathlib's ordering on both.
    """
    try:
        it = os.scandir(chars)
    except OSError:
        # `_listed_dirs` guards with `exists()`: a missing directory lists
        # nothing, one that exists and cannot be listed raises.
        if not chars.exists():
            return []
        raise
    with it:
        names = [e.name for e in it if _store_paths.safe_id(e.name) and e.is_dir()]
    return sorted(names, key=os.path.normcase)


def _row(root: Path, chars: Path, cid: str) -> dict | None:
    """The listing row for one character directory, or None when the listing
    skips it (no `character.md`, or no addressable card), memoized in
    `_ROW_POOL` on the stamps `_build_row` collected. Frozen: see
    `list_characters`."""
    return statcache.memo_stamped((os.fspath(chars), cid), lambda: _build_row(root, cid),
                                  pool=_memo._ROW_POOL, max_entries=_memo.POOL_ENTRIES)


def _build_row(root: Path, cid: str) -> tuple[dict | None, tuple | None]:
    """`_row`'s value, and the stamps that vouch for it.

    EVERY input the row is built from is covered, each stamped before it is
    read (`statcache.memo_stamped` says why that order is the whole game):

    - the character directory -- its listing names the cards, and whether
      `character.md`, `tagline.md` and `voice_anchor.md` exist at all;
    - `character.md` (name, default version);
    - every card file, not just the default's: each version's label is read
      off its own card, and a text editor saving one in place moves no
      directory;
    - the default version's art and the tagline, as `_build_version_facts`
      stamps them (through its memo entry, whose stamps it validated just now);
    - `voice_anchor.md` when present -- absent, the directory's stamp already
      covers its arriving.

    A directory stamp alone would not do: an in-place edit (a sync client, a
    text editor) changes a file's mtime and size and leaves its directory
    alone, so a memo keyed on directories would serve the old row forever.
    Which is also why this is per row rather than one memo over the list --
    the list's only directory is the one edits never touch.

    The values are the ones the pre-memo listing computed, through the same
    helpers in the same order, so the failures are its failures too: an
    unreadable `character.md` or a malformed card still raises, and a card
    that vanishes mid-listing still does whatever `_card_summary` does about
    it -- it is only never remembered.
    """
    d = _paths._char_dir(root, cid)
    here = statcache.stamp(d)
    if here is None:
        return None, None                  # gone since the listing
    stamps = [here]
    meta_path = d / "character.md"
    meta_stamp = statcache.stamp(meta_path)
    if meta_stamp is None:
        # Not a character (a campaign's dossier-only directory): skipped, and
        # vouched for by the directory, which `character.md` arriving moves.
        return None, tuple(stamps)
    stamps.append(meta_stamp)
    meta, _ = _store_frontmatter.parse_frontmatter(meta_path.read_text(encoding="utf-8"))
    version_ids = _paths._version_ids_in(d)
    if not version_ids:
        return None, tuple(stamps)         # see read_character: nothing to show
    default = _paths._addressable_default(meta.get("default_version", ""), version_ids)
    cacheable = True
    cards_at: dict[str, tuple[Path, statcache.Stamp | None]] = {}
    for v in version_ids:
        card = d / f"{v}.json"             # `_card_path`, without rebuilding `d`
        card_stamp = statcache.stamp(card)
        cards_at[v] = (card, card_stamp)
        if card_stamp is None:
            cacheable = False              # vanished since the glob
        else:
            stamps.append(card_stamp)
    facts, fact_stamps = _facts_entry(root, cid, default, crop=False, d=d)
    if fact_stamps is None:
        cacheable = False
    else:
        stamps.extend(fact_stamps)
    names = facts["names"]
    try:
        greeting_count = _reads._card_summary(root, cid, default, cards_at[default])["greeting_count"]
    except _paths.VersionNotFound:
        greeting_count = 0
    anchor_stamp = statcache.stamp(voice_anchors.anchor_path(root, cid))
    if anchor_stamp is not None:
        stamps.append(anchor_stamp)
    row = {
        "id": cid,
        "name": meta.get("name", cid),
        "default_version": default,
        "has_avatar": assets.AVATAR in names,
        # Names the avatar's current BYTES: the grid tile spends it as
        # `?v=`, which `routes.common._serve_image_file` serves
        # immutable. A token that outlives the bytes pins a stale tile.
        "avatar_v": facts["avatar_v"],
        "avatar_focus": facts["focus"],
        # Every picture that is neither the avatar nor a localized embed: the
        # Chub gallery's `gallery_<i>` and a CHARX import's named art alike.
        "gallery_count": assets.gallery_count(names),
        "localized_count": assets.localized_count(names),
        "greeting_count": greeting_count,
        "tagline": facts["tagline"],
        # A BOOLEAN, not the body: the listing has no use for the text.
        #
        # At world level a tombstone and an absence are the same state
        # -- `voice_anchors.read_record` says so, and there is nothing
        # beneath a world to inherit from -- so `read` covering both is
        # correct rather than a simplification. Tombstones only carry
        # meaning in a campaign, which this world-scoped listing is not.
        "has_voice_anchor": bool(voice_anchors.read(root, cid)),
        "versions": [{"id": v, "name": _reads._card_summary(root, cid, v, cards_at[v])["label"]}
                     for v in version_ids],
    }
    # Deduplicated exactly: the facts stamp this directory again, and an
    # absent art folder is vouched for by the nearest directory that exists,
    # which is often this one. Two DIFFERENT stamps of one path stay, and can
    # never both match again -- which is right, since the path moved mid-build.
    return row, (tuple(dict.fromkeys(stamps)) if cacheable else None)


def version_facts(root: Path, cid: str, vid: str, *, crop: bool = False) -> dict:
    """What a listing row reads off ONE root about one version's art and the
    character's tagline, memoized in `_FACTS_POOL` -- see `_build_version_facts`.

    Per root on purpose. The campaign listing unions two roots' answers under
    the campaign's tombstones and detachments (`overlay._patch_char_item`), and
    a memo of that merged row would need a key covering all four; memoizing
    each root's facts on that root's own stamps, and merging per request, is
    exact with no such key. Frozen: callers read it and build their own rows.
    """
    return _facts_entry(root, cid, vid, crop=crop)[0]


def _facts_entry(root: Path, cid: str, vid: str, *, crop: bool,
                 d: Path | None = None) -> tuple[dict, tuple | None]:
    """`version_facts`' memo entry: the facts AND the stamps that vouch for
    them, so a listing row built from the facts can carry those stamps as its
    own (`_build_row`). One entry serves both readers -- the campaign listing's
    world side asks for exactly the facts the world row was built from, and a
    row rebuilt because only a card moved reuses the art it did not re-scan."""
    def compute() -> tuple[tuple[dict, tuple | None], tuple | None]:
        facts, stamps = _build_version_facts(root, cid, vid, crop=crop, d=d)
        return (facts, stamps), stamps

    return statcache.memo_stamped(("facts", os.fspath(root), cid, vid, crop), compute,
                                  pool=_memo._FACTS_POOL, max_entries=_memo.POOL_ENTRIES)


_NO_FACTS = {"names": (), "avatar_v": None, "focus": None,
             "avatar_file": False, "focus_file": False, "tagline": ""}


def _build_version_facts(root: Path, cid: str, vid: str, *, crop: bool = False,
                         d: Path | None = None) -> tuple[dict, tuple | None]:
    """`version_facts`' value and its stamps. The value:

    - `names`: the image names of `assets.list_images(root, cid, vid)`, in its
      order, stranded promotion healed -- and `avatar_v`, the `v` it gives
      `AVATAR`, or None. Only those: a row counts names and spends one token,
      and a memo holding every image's entry for every character in a large
      library is memory a phone pays for;
    - `focus`: `assets.read_focus(root, cid, vid)`;
    - `tagline`: `taglines.read(root, cid)`;
    - with `crop`, `avatar_file` -- whether `assets.image_path` finds any
      `avatar.*` file, of ANY extension, which is `overlay.read_focus`'s test
      for "this root owns the crop" and not the same question as an `avatar`
      among the names -- and `focus_file`, whether the folder owns a crop
      record (`assets.owns_focus`: an avatar placement, the image-less
      occurrence override included, or `focus.json`), the other half of that
      test. Only the campaign side of a merge asks, so only it pays the glob.

    Stamped as `assets.version_art` stamps the art, plus the character
    directory, whose listing covers `tagline.md` arriving, and `tagline.md`
    itself when present. `avatar_file` and `focus_file` are both a question
    about the art folder's LISTING, which its stamp covers -- and about
    ``image-refs/avatar.json``, which `version_art` stamps, with the
    placements folder whose listing covers it arriving.

    A root that has no directory for this character at all -- a campaign that
    holds nothing of an inherited one, the common case -- answers every
    question emptily, and says so without asking: the nearest ancestor that
    exists vouches for the directory's absence, one stat.
    """
    if d is None:
        d = _paths._char_dir(root, cid)       # raises CharacterNotFound for an unsafe id
    here = statcache.stamp(d)
    if here is None:
        up = statcache.stamp(d.parent) or statcache.stamp(root)
        return dict(_NO_FACTS), (None if up is None else (up,))
    stamps = [here]
    cacheable = True
    tagline_stamp = statcache.stamp(taglines.tagline_path(root, cid))
    if tagline_stamp is not None:
        stamps.append(tagline_stamp)
    images, focus, art = assets.version_art(root, cid, vid)
    if art is None:
        cacheable = False
    else:
        stamps.extend(art)
    facts = {
        "names": tuple(i["name"] for i in images),
        "avatar_v": next((i["v"] for i in images if i["name"] == assets.AVATAR), None),
        "focus": focus,
        "avatar_file": (crop and assets.image_path(root, cid, vid, assets.AVATAR) is not None),
        "focus_file": crop and assets.owns_focus(root, cid, vid),
        "tagline": taglines.read(root, cid),
    }
    return facts, (tuple(dict.fromkeys(stamps)) if cacheable else None)


def _listed_dirs(root: Path) -> list[Path]:
    """The character directories `list_characters` walks, in its order: a
    child dir holding `character.md` under an id the resolvers accept."""
    d = _paths._chars_dir(root)
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_dir() and (p / "character.md").exists() and _store_paths.safe_id(p.name))


def roster(root: Path) -> list[dict]:
    """`list_characters`, cut to `id`, `name` and `default_version`.

    For the callers that ask who is here and what they are called -- the
    turn's off-scene directory, cast-change detection, suggestions, the
    to-do list -- which paid the full row for it: an asset scan and a focus
    read per character, a card summary per version, a tagline and a voice
    anchor. What is left is the meta read and the version glob, and both are
    load-bearing: the glob is the row FILTER (a character with no addressable
    card is not listed, see `read_character`) and the meta is the name.

    Same walk, same filter, same order, same values as `list_characters`, and
    held to it by test -- a roster that listed one character the full listing
    did not would put a name in a prompt that no other surface can open. The
    meta goes through `parse_frontmatter` on the whole file, as the listing
    does, rather than the head parser: `character.md` has no body to skip, and
    the two parsers split a line differently on the rarer Unicode separators.
    A caller that needs another field of the row should read the full listing
    for it rather than widen this one piecemeal.

    Each row is memoized in-process on two stamps, the character directory
    (whose listing is the version glob, the row filter) and `character.md`
    (the name), the way `name_and_versions` is: the global to-do page asks for
    every world's roster on every read (spec 04 section 4.5).
    """
    out: list[dict] = []
    for cd in _listed_dirs(root):
        row = statcache.memo_stamped(
            ("roster_row", os.fspath(cd)), functools.partial(_build_roster_row, root, cd.name),
            pool=_memo._IDENTITY_POOL, max_entries=_memo.POOL_ENTRIES)
        if row is not None:
            out.append(dict(row))
    return out


def _build_roster_row(root: Path, cid: str) -> tuple[dict | None, tuple | None]:
    """One `roster` row (None for a character with no addressable card),
    vouched for by the character directory and `character.md`, each stamped
    before it is read. A meta that cannot be read raises and is not
    remembered -- here exactly where the listing raises."""
    here = statcache.stamp(_paths._char_dir(root, cid))
    meta_stamp = statcache.stamp(_paths._meta_path(root, cid))
    # Meta first, as the listing reads it, so an unreadable meta fails
    # here exactly where it fails there.
    meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
    version_ids = _paths._version_ids(root, cid)
    row = None if not version_ids else {
        "id": cid, "name": meta.get("name", cid),
        "default_version": _paths._addressable_default(meta.get("default_version", ""), version_ids)}
    if here is None or meta_stamp is None:
        return row, None
    return row, (here, meta_stamp)


def listed_ids(root: Path) -> list[str]:
    """The ids `list_characters` lists, reading no file at all.

    Not `character_refs`, which is every character dir holding a meta file:
    that one also names a character whose every card has gone, which the
    listing -- and so every count derived from it -- leaves out. The one way
    the two can differ is failure: a meta file the listing cannot read raises
    there and is simply counted here, which is the direction a count wants."""
    return [cd.name for cd in _listed_dirs(root) if _paths._version_ids(root, cd.name)]


def character_count(root: Path) -> int:
    # `_listed_names` for the directories -- their type comes off the listing,
    # so only `character.md` costs a stat -- then that stat as the filter.
    d = _paths._chars_dir(root)
    return sum(1 for cid in _listed_names(d) if (d / cid / "character.md").exists())


def character_exists(root: Path, cid: str) -> bool:
    """Whether `root` holds a character under this id.

    The container meta is the test, which is what `character_refs` and
    `character_count` enumerate by -- deliberately not `read_character`, which
    additionally refuses an actor whose every version file has gone. The
    question here is whether the id names a record in this root at all, not
    whether that record can be opened: a world character with no addressable
    version is still the library's claim on that slug, and a campaign copy of
    her is still the library's character (`appearances.actor_source`), not one
    the campaign invented.
    """
    return _store_paths.safe_id(cid) and _paths._meta_path(root, cid).exists()


def character_refs(root: Path) -> list[str]:
    d = _paths._chars_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir()
                  if p.is_dir() and (p / "character.md").exists() and _store_paths.safe_id(p.name))


def find_unlinked_versions(root: Path) -> list[dict]:
    """Every (character, version) pair with no chub.ai link, across every
    character in root -- for surfacing versions worth linking."""
    out: list[dict] = []
    for cid in character_refs(root):
        meta, _ = _store_frontmatter.parse_frontmatter(_paths._meta_path(root, cid).read_text(encoding="utf-8"))
        default = meta.get("default_version", "")
        legacy_chub_source = meta.get("chub_source", "")  # same fallback as read_character
        for vid in _paths._version_ids(root, cid):
            summary = _reads._card_summary(root, cid, vid)
            chub_source = summary["chub_source"] or (legacy_chub_source if vid == default else "")
            if not chub_source:
                out.append({
                    "character": cid, "character_name": meta.get("name", cid),
                    "version": vid, "version_name": summary["label"],
                })
    return out
