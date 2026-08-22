"""World create/rename/fork/delete."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

from .. import atomic
from ..campaigns import read as campaigns_read
from ..frontmatter import dump_frontmatter, parse_frontmatter
from ..paths import ensure_home, now_iso, slugify, uniquify
from . import paths, staging


class WorldInUse(Exception):
    def __init__(self, wid: str, names: list[str]):
        self.names = names
        super().__init__(f"world is used by campaigns: {', '.join(names)}")


def create_world(name: str) -> str:
    """Make an empty world and return its id.

    Staged and published by one rename, like the fork and the bundle import --
    and here that is not about crash-safety (there is almost nothing to write)
    but about never publishing an EMPTY directory.

    This used to `mkdir` the world and then write `world.md` into it, which
    left a window in which `worlds/<id>/` existed and held nothing. POSIX
    `rename` REPLACES an empty destination directory, so a fork or an import
    that had just checked the id was free could rename its finished tree over
    that directory -- and this call would then write its `world.md` into
    somebody else's copied world. Both callers returned the same id, and the
    world that resulted was two worlds mixed together (Codex review, P1).

    Publishing a directory that is never empty is what closes it: `rename`
    refuses a non-empty destination (ENOTEMPTY), so `staging.publish` can only
    lose the race, which it already handles by picking another id and retrying.
    """
    ensure_home()
    base = slugify(name)
    now = now_iso()
    with staging.staging_tree() as tree:
        atomic.write_text(tree / "world.md",
                          dump_frontmatter({"name": name, "created": now, "updated": now}, ""))
        return staging.publish(tree, base,
                               uniquify(base, lambda c: paths.world_root(c).exists()))


def fork_world(wid: str, name: str) -> str:
    """Copy the world at `wid` into a new world called `name`; return its id.

    A world directory is the world. `world.md`, the entity kind-folders,
    `characters/` and `pcs/` with their per-version assets, `greetings/`,
    `sheets/`, `plotmap.json`, `tags.md`, `calendar.json` — and nothing inside
    it names its own id except the serving URLs `store/localize.py` writes into
    card and greeting text, which `worlds.staging.repoint_urls` moves onto the
    fork. Campaigns are the only records that point *at* a world, and a
    brand-new fork has none. So the copy is referentially complete by
    construction, and a part added to the layout next month is forked without
    anybody remembering this function exists — the trap `create_campaign`'s
    hand-written walk keeps falling into.

    Copied whole, then fixed up: `copytree` first, and the only things rewritten
    afterwards are the three that are *identity* — the name, and both
    timestamps. `created` is stamped now rather than carried because the fork
    started existing now; `updated` because `list_worlds` sorts on it, and a
    fork of a world last touched two years ago would otherwise land at the
    bottom of the shelf the user is looking at for it. Everything else in the
    frontmatter travels as it stands, `module` included: a mechanics binding
    names a pack installed in this store, which is the same store the fork
    lives in.

    Built in `worlds.staging` and published by a single rename, which is what
    makes a failure invisible rather than half-visible: `list_worlds` calls any
    directory with a `world.md` in it a world, and a `copytree` straight into
    the library would publish that file partway through a copy that can still
    fail — leaving a phantom world holding some fraction of a real one, under a
    name that does not say it is a copy. Nothing reaches the library until the
    whole tree is there.

    Raises `WorldNotFound` for a source that is not a world, and
    `staging.WorldIdConflictError` if no id could be claimed for the copy.

    Not locked, because worlds have no lock — a world edited while it is being
    forked can be copied half-old and half-new, exactly as `write_bundle`
    documents for an export. The source is never written to either way.
    """
    ensure_home()
    # Canonical first: on Windows and macOS `worlds/REALM` opens `worlds/realm`,
    # so a fork asked for under the wrong case would copy the right directory
    # and then look for `/api/worlds/REALM/` in records that carry
    # `/api/worlds/realm/` -- finding nothing, and publishing a fork whose
    # images all still serve out of the world it was forked from.
    wid = paths.canonical_id(wid)
    root = paths.world_root(wid)
    if not paths.world_meta_path(wid).exists():
        raise paths.WorldNotFound(wid)
    base = slugify(name)
    new_wid = uniquify(base, lambda c: paths.world_root(c).exists())
    with staging.staging_tree() as dest:
        # Symlinks are FOLLOWED (`symlinks` left False), the same call
        # `store/fork.py` documents for campaigns: copied as links, every file
        # in the fork would still be the source's file and the first edit to
        # the copy would land in the original. A store is plain files the user
        # owns and syncs, so a symlink in one is not hypothetical. Following
        # costs the bytes and can loop or dangle; both fail the fork loudly,
        # which is the trade `store/fork.py` also takes -- and unlike an export
        # (which skips links, because a bundle goes to somebody else) a fork
        # that skipped them would produce a world missing files it appears to
        # have.
        #
        # Following does materialize the target, so a fork-then-export packs
        # content the export of the SOURCE would have skipped. Accepted: the
        # copy stays in the same store under the same user, the world already
        # serves that link's content over the API today, and planting the
        # symlink at all needs write access to the store -- with which one can
        # simply put the bytes in a record and skip the laundering. What the
        # export's skip actually buys is that no ordinary export silently
        # dereferences a link; a fork is not ordinary and is not silent.
        #
        # `store.atomic`'s in-flight temps are skipped: they are not part of the
        # world, and the writer that owns one renames or unlinks it out from
        # under the walk -- so copying one is both wrong and racy.
        #
        # `copyfile`, not the default `copy2`: CONTENT is copied and metadata
        # is not, and that is a correctness requirement rather than a
        # preference. `copy2` carries the mode bits, so a record the user had
        # chmod'ed `0444` -- a protection `store.atomic` deliberately honours
        # (`_assert_target_writable`) -- arrives read-only in the staging tree,
        # and `repoint_urls` then cannot rewrite the world id inside it: the
        # whole fork fails with a PermissionError, and only for worlds holding
        # a read-only localized record. Nothing is lost by dropping the
        # metadata, because the store keeps every timestamp it cares about in
        # its own frontmatter and has no use for a mode bit; the fork's files
        # are new files, made now, under the process umask.
        #
        # `dirs_exist_ok`, because `staging_tree` has already made `dest`: the
        # work directory it will remove has to exist before anything is put in
        # it, which is the whole point of it owning both halves.
        shutil.copytree(root, dest, ignore=_skip_write_temps,
                        copy_function=shutil.copyfile, dirs_exist_ok=True)
        _make_writable(dest)
        mp = dest / "world.md"
        meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
        now = now_iso()
        meta["name"] = name
        meta["created"] = now
        meta["updated"] = now
        atomic.write_text(mp, dump_frontmatter(meta, body))
        staging.repoint_urls(dest, wid, new_wid)
        return staging.publish(dest, base, new_wid)


def _skip_write_temps(directory: str | Path, names: list[str]) -> set[str]:
    """`copytree`'s `ignore` callback: the `store.atomic` temps in `names`.

    `directory` is whatever `copytree` was handed -- a `Path` here, but the
    signature says both because the callback protocol does not promise which.

    A temp is a FILE. Matching on the name alone would let a directory called
    `.notes.abcdefgh.tmp` -- a user's, or a layout this code has not met yet --
    take its whole subtree out of a copy that calls itself deep, silently
    (Codex review). `atomic` only ever produces files here, so requiring one
    costs nothing and closes that.
    """
    return {n for n in names
            if atomic.is_write_temp(Path(directory) / n)
            and (Path(directory) / n).is_file()}


def _make_writable(root: Path) -> None:
    """Give the owner write access to every directory in the staged copy.

    `copytree` applies `copystat` to each destination DIRECTORY whatever
    `copy_function` is, so a `0555` directory in the source arrives `0555` here
    -- and then `repoint_urls` cannot create the sibling temp `store.atomic`
    publishes through, the fork fails, and the cleanup cannot unlink out of
    that directory either, so the whole staged copy is left behind (Codex
    review). Dropping the file metadata was only half the fix.

    Owner-write is added rather than the mode being replaced: nothing here
    needs to widen a directory for anyone else, and the published fork keeps
    whatever the source said about group and other.
    """
    for d in [root, *(p for p in root.rglob("*") if p.is_dir())]:
        mode = d.stat().st_mode
        if not mode & stat.S_IWUSR:
            d.chmod(stat.S_IMODE(mode) | stat.S_IRWXU)


def rename_world(wid: str, name: str) -> None:
    mp = paths.world_meta_path(wid)
    if not mp.exists():
        raise paths.WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    atomic.write_text(mp, dump_frontmatter(meta, body))


def delete_world(wid: str) -> None:
    root = paths.world_root(wid)
    if not paths.world_meta_path(wid).exists() or not paths.names_its_directory(root):
        raise paths.WorldNotFound(wid)
    # world_refs, not list_campaigns: the in-use check has to see campaigns the
    # public listing hides, or hiding one makes its world deletable. A campaign
    # whose reference could not be read (w is None) counts as a user too --
    # deletion is irreversible, so "we could not tell" has to block it.
    used_by = [name for _cid, name, w in campaigns_read.world_refs()
               if w is None or paths.references_world(w, root)]
    if used_by:
        raise WorldInUse(wid, used_by)
    shutil.rmtree(root)
