"""Record roots, ref vocabulary and cleanup of record-owned directories."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .. import characters, entities, pcs
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..paths import safe_id

log = logging.getLogger('grimoire.store.overlay')

#: Record kinds a campaign inherits from its world. A `<campaign>/<kind>/...`
#: read for one of these is only correct through this module (or after the
#: reader has materialized the record itself).
INHERITED_KINDS: tuple[str, ...] = entities.SYNCED_KINDS + appearances_paths.ACTOR_KINDS

#: Campaign-root files that resolve through to the world the same way.
INHERITED_FILES: tuple[str, ...] = ("plotmap.json",)

#: The sync base a materialization reserves before its copy lands, while the
#: slim migration is still pending — see `_recorded_base`. Deliberately the
#: value the pre-overlay fork wrote for anything it did not copy, so the
#: migration's existing rule for that (`lifecycle.ensure_campaign_slim`, which
#: attributes no deletion to a ref carrying it) covers this too.
RESERVED_BASE = ""


def croot_of(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid)


def wroot_of(cid: str) -> Path:
    """The campaign's world root. May not exist — the world was deleted
    before the guard against that existed. Or, if the campaign records no
    world, `campaigns.world_root_of` yields a path nothing can occupy.
    Either way, nothing here raises for it — every resolver below just reads
    a path that doesn't hold the expected records as holding none."""
    return campaigns_read.world_root_of(cid)


def library_ref(name: str) -> str:
    """``assets/library/<name>`` -- how a campaign records hiding one of its
    world's library images (`store.world_images`).

    A third ref shape beside the flat `<kind>/<id>` and the per-asset
    `assets/<base>/<aid>/<vid>/<name>`, and it cannot be confused with either:
    every asset ref has five segments and a base drawn from the kind roster,
    and `library` is not a kind. Public rather than private because
    `store.campaign_images` builds and tests it on every read, and a private
    name reached across a module boundary is what the import rule is about.
    """
    return f"assets/library/{name}"


# ---- flat records (locations / lore; greetings + plotmap join in Task 2) ----

def flat_ref(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _flat_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


def record_dir(cid: str, kind: str, rid: str) -> Path:
    """The campaign's `<kind>/<rid>/` -- assets and the record's sidecars."""
    return _record_dir(croot_of(cid), kind, rid)


def _identity_path(root: Path, kind: str, eid: str) -> Path:
    if kind == "plotmap":
        return root / "plotmap.json"
    if kind in appearances_paths.ACTOR_KINDS:
        return _record_dir(root, kind, eid)
    return _flat_path(root, kind, eid)


# ---- actors (characters / pcs): whole-dir resolution keyed on the container meta ----

def _actor_meta(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _actor_not_found(kind: str, aid: str) -> Exception:
    return characters.CharacterNotFound(aid) if kind == "characters" else pcs.PCNotFound(aid)


# ---- assets: per-file union, campaign wins ----

def asset_ref(base: str, aid: str, vid: str, name: str) -> str:
    return f"assets/{base}/{aid}/{vid}/{name}"


# ---- world-side deletes: nothing outlives the record it was filed beside ----

def _record_dir(root: Path, kind: str, rid: str) -> Path:
    """`<root>/<kind>/<rid>/` — the directory a record's *neighbours* live in.

    Flat records are a file (`<kind>/<rid>.md`) with a sibling directory; actors
    are the directory. Either way this holds everything filed under the record's
    id rather than inside it: an actor's campaign-local sidecars (state.md,
    dossier.md, voice_drift.md), a group's state.md, and any kind's `assets/`.
    """
    return root / kind / rid


def _drop_record_dir(root: Path, kind: str, rid: str) -> None:
    """Remove `_record_dir`, if there is one.

    The id is the only thing tying those files to their record, and ids are
    handed out by slug (`entities.create_entity` / `characters.create_character`
    uniquify against what exists *now*), so a record deleted and recreated under
    the same name gets the same id back. Anything left behind is then adopted by
    a new, unrelated record — for a group's state.md that means a dead group's
    Secrets in a live scene's context (#225).

    A failure to remove is logged rather than raised: the record itself is
    already gone by the time this runs, and answering a delete that succeeded
    with a 500 helps nobody. `rmtree` stops at its first error, so what survives
    is a *part* of the directory rather than all of it — still strictly less
    than the pre-#225 leftovers, but the warning says which path to look at
    because "some of the dead record's sidecars" is not a state to leave a user
    guessing about.
    """
    if kind not in INHERITED_KINDS or not safe_id(rid):
        return
    d = _record_dir(root, kind, rid)
    if not d.is_dir():
        return
    try:
        shutil.rmtree(d)
    except OSError as exc:
        log.warning("could not remove %s (%s) -- a record recreated under the id "
                    "%r will inherit what is still in it", d, exc, rid)


def _record_present(root: Path, kind: str, rid: str) -> bool:
    """Is there a record of this kind and id here *now*?

    Asked again after the delete, because the sweep drops a whole directory and
    these handlers hold no world-level lock: a concurrent create of the same
    name takes the id back the instant the delete frees it, and publishes its
    own `<kind>/<rid>/` for the sweep to remove -- reporting success for a
    record that no longer exists (Codex review).

    This narrows that window to the gap between the check and the `rmtree`
    rather than closing it; closing it needs a world-record lock, which this
    module does not have for anything (`locks.UNREVIEWED`). It costs nothing
    and removes the destructive outcome for every interleaving but one.
    """
    if kind in ("characters", "pcs"):
        return (root / kind / rid / _actor_meta(kind)).exists()
    return _flat_path(root, kind, rid).exists()
