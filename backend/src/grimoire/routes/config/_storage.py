"""config: storage responsibilities."""

from __future__ import annotations

from fastapi import HTTPException, Request

from ... import cache_warm, store
from ...store import compiled, group_refs
from ...store import paths as library_paths
from ...store.absorb import membership as absorb_membership
from ...store.inference import migrate as inference_migrate
from .. import runs
from ..models import DataDirUpdate
from ._router import router as router


@router.get("/config/data-dir")
def get_data_dir():
    return store.data_dir_info()


@router.put("/config/data-dir")
def put_data_dir(update: DataDirUpdate, request: Request):
    # Not under a campaign lock, deliberately: there is no campaign to lock --
    # the root itself is moving, and a lock taken in the old tree would not name
    # anything in the new one. The exclusion that IS available is the run
    # registry's own, and it is held across the move rather than consulted
    # before it: checking and then moving leaves a window for a send to reserve,
    # and that run's setup writes into the old tree while its terminal write
    # resolves against the new one. What remains outside any lock here is
    # another *process* sharing the store, which `store/locks/__init__.py` already places
    # outside what this can promise.
    #
    # The inference-settings migration is not a run, so the registry cannot
    # see it; its own lock is held across the move instead, TRIED rather than
    # waited on -- a migration begins with a backup of the whole library.
    with runs.store_held_still(request.app), inference_migrate.held_still() as free:
        if not free:
            raise HTTPException(status_code=409, detail={
                "kind": "busy",
                "detail": "the model settings are being upgraded; try again when "
                          "that has finished"})
        try:
            with compiled.root_change(), library_paths.library_guard():
                previous = library_paths.home()
                prospective = library_paths.prepare_data_dir(update.data_dir)
                moved = str(previous.resolve()) != str(prospective.resolve())
                if moved:
                    absorb_membership.retire_library_inherited(previous)
                store.set_data_dir(update.data_dir)
                if moved:
                    # Inside the hold, so no run reserves between the move and
                    # this: every retained run names the OLD tree's slugs.
                    runs.forget_root(request.app)
        except group_refs.GroupIdentityConflict as exc:
            raise HTTPException(status_code=409,
                                detail={"detail": str(exc), "kind": "group_identity"}) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=400,
                                detail={"detail": str(exc), "kind": "data_dir"}) from exc
    # The log's size cache is keyed by absolute path, so a root that moved
    # leaves byte counts charged against files the new tree does not have --
    # which would cap a fresh log at the old one's size (`logs.forget_file_sizes`).
    store.logs.forget_file_sizes()
    # Refs a continuity trigger left for a sweep name records in the OLD tree;
    # a move is refused only while a run is live, and these can wait with none.
    runs.drop_pending_touched(request.app)
    # Paths the write-through queue holds were collected under the old root,
    # and the queue never warms one against another (spec 05 section 5.4).
    cache_warm.clear(request.app)
    # And the threshold belongs to the store, not to the process: `log_level`
    # lives in the config of whichever library is open, so a root that moved
    # without this kept writing at the OLD tree's floor while `GET /config`
    # (reading the new tree) reported the new one -- two endpoints disagreeing
    # about one setting, with rows going to disk under the wrong one.
    store.logs.apply_level()
    # Opening the new library acts on any deletes other devices published to
    # it while this one was elsewhere (spec 03 section 12), as startup does.
    compiled.open_library()
    # The new root may be a legacy store: migrate it, in the background, as
    # startup would have (spec 11.2). After the move, outside every hold above.
    request.app.state.start_inference_migration()
    return store.data_dir_info()


# ---- backups (#32) ----
def _backups_body() -> dict:
    """Where the archives live and what is in there, newest first. The
    directory rides along because it is a *setting* — the answer to "why is
    this list empty" is often "you moved it"."""
    return {"dir": str(store.backups.backup_dir()),
            "backups": store.backups.list_backups(),
            "image_backups": store.backups.list_image_backups()}


@router.get("/backups")
def get_backups():
    try:
        return _backups_body()
    except OSError as exc:
        # Not an empty list: "no restore points" and "could not look" send a
        # reader in opposite directions, and this one is read right before
        # somebody decides whether they are covered.
        raise HTTPException(status_code=500, detail=f"could not list backups: {exc}") from exc


@router.post("/backups")
def post_backup(request: Request):
    """Back up now, then apply retention. Returns the refreshed listing, so the
    caller needs no second request to show what it just made.

    The two steps report separately on purpose. Under one `try` a failed sweep
    surfaced as "could not write a backup" — telling the user the opposite of
    what happened, about the half of the operation they care about, and
    throwing away the listing that would have shown them the archive sitting
    there. A backup that landed is a success with a retention problem attached.

    The archive is built holding the store against image-store maintenance
    (`maintenance_running` while a migration or collection is live), so it never
    zips a tree a pass is moving files out of.
    """
    try:
        with runs.maintenance_excluded(request.app):
            made = store.backups.create_backup()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not write a backup: {exc}") from exc
    swept: list[str] = []
    retention_error = None
    try:
        swept = store.backups.sweep()
    except OSError as exc:
        retention_error = f"backup written, but old archives could not be removed: {exc}"
    return {**_backups_body(), "created": made.name, "swept": swept,
            "retention_error": retention_error}


@router.post("/backups/images")
def post_image_backup(request: Request):
    """Create a separate image archive from the currently resolved store.

    Held against image-store maintenance while it is built, as `post_backup`
    is: a collection deleting objects under it would archive placements whose
    images are already gone."""
    try:
        with runs.maintenance_excluded(request.app):
            made = store.backups.create_image_backup()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not back up images: {exc}") from exc
    try:
        return {**_backups_body(), "created": made.name, "listing_error": None}
    except OSError as exc:
        # The archive has already landed. Tell the caller where it is without
        # claiming that the write failed or inventing an empty listing.
        return {"dir": str(made.parent), "created": made.name,
                "listing_error": f"image archive written, but backups could not be listed: {exc}"}


@router.get("/store/conflicts")
def get_store_conflicts():
    """Sync-tool conflict artifacts sitting unread in the store (#35).

    Its own route rather than a field on GET /config: this costs a directory
    walk of the whole library, and /config is read on nearly every page. The
    Storage section asks for it when it is shown, which is where the answer is
    actionable.

    A scan that could not run is a 500, deliberately -- `store.external.scan`
    already absorbs the per-directory failures a synced volume produces, so
    anything reaching here failed at the root, and reporting an empty list for
    that would tell the user their library is clean when nobody looked.
    """
    try:
        return store.external.scan()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not scan the store: {exc}") from exc
