"""Move a store from the legacy settings layout to format 2 (spec 11.2).

Global, once, idempotent and resumable. `ensure` runs the steps in order:
`llm_connections.ensure_migrated()` (today's format-1 seeding, not a migration
write), then

1. **backup** -- `backups.create_backup(prefix=SAFETY_PREFIX)`, which
   retention never prunes. If it fails nothing is written, the status says
   why, and the next start retries. Taken once per root: a run resuming after
   a later step failed reuses the archive the status note names while it is
   still there, so retries do not pile up never-pruned copies of the library;
2. **providers** -- each connection gains `preset` and `billing`, both
   rev-neutral, so catalogs, verified tests and vector caches survive. A
   connection that already has a preset is skipped: a no-op update would
   still rewrite it;
3. **model facts** -- each connection's legacy `vision`, `prefill` and
   `post_process`, where they differ from the defaults, become the facts of its
   model (an unset Claude model is `opus`; any other empty model is keyed "").
   Copied in the `llm_connections.LOCK` hold that writes the marker
   (`_switch`), so a legacy edit made before it is not switched past;
4. *(derived reasoning presets: slice I, ruling 3)*;
5. **roles**, 6. **routes**, 7. **split routes**, 9. **marker** -- ONE
   `config.md` write, the last, derived from a read taken inside the
   `config_lock` hold that writes it: `translate.global_view` of the legacy
   settings, persisted **verbatim** (padded ids, dangling references and
   `PRESET_CLEAR` exactly as it gives them; the split routes are already in
   it, read from their parents) with two enrichments -- an unset Claude model
   is written as `opus` wherever it is a selection's model, and the Embedding
   role is set only when the legacy configuration actually embeds
   (`embed_space.resolve`, ruling 5) -- plus `inference_format: "2"`.

   Read late because the app serves at format 1 all through the backup, and a
   legacy edit made meanwhile must not be reverted by a snapshot from before
   it. Written whole because `write_config` merges: EVERY global key the
   migration owns (`OWNED_GLOBAL_KEYS`) is written, "" where the translation
   leaves it unset, so a format-2 key already in the file -- an interrupted
   run of an earlier build, a hand edit -- is overwritten with what the
   legacy settings say now rather than surviving because nothing named it.
   The legacy routes' `preset_<k>` keys are the same keys in both layouts and
   are left as they are.

8. **campaigns** -- `campaign(cid)` for each unmarked one, under its own
   `campaign_lock_nowait(cid)`, one at a time; a busy one is skipped and the
   next `ensure` finishes it (it resolves through the translation meanwhile).
   AFTER the marker, not before it: a campaign's routes are translated from
   the connections they name, and before the marker a connection's legacy
   `model` can still be edited -- by this server or another -- so a campaign
   migrated then kept the model the edit replaced, for good. Once the store
   is at format 2 those fields are frozen (refused on write), so what a
   campaign is translated from cannot move under its step, and an unmarked
   campaign reads the same translation until it is reached.

Every derived value is deterministic, so two devices migrating one synced
store write the same bytes. The legacy keys and fields are left in place,
frozen, for older builds.

**What `ensure` holds.** The migration lock (`locks.inference_migration_lock`,
a process lock plus a proclock), TRIED and never waited on: a second `ensure`
returns the current status at once. It takes no run exclusion -- that is a fact
about one app's run registry, and `main.start` holds it (CLAUDE.md). It
resolves the store root inside that hold (`PUT /config/data-dir` holds the
same lock across a move, so the root cannot change between the two) and checks
it before each step and each item, so a data-dir switch mid-run writes no
marker into either tree: the run belongs to the root it started on. The same
checks read the caller's `stop` flag, which lifespan shutdown sets; a stopped
run is `pending` and resumes on the next `ensure`.

**Failure policy.** A failed backup, an I/O error on the final global write,
and a connection file that exists but cannot be read fail the migration (no
marker; the next start retries). The last is not "no such connection": every
step persists what it reads, so a read a sync client blocked would otherwise
be written down for good as an empty model with no preset. An item that
cannot be migrated -- an unreadable `campaign.md` (or one naming an unreadable
connection), a connection or model-facts write that fails or is refused, a
busy campaign -- is skipped, its reason recorded in `skipped`, and the run
goes on.

**Status.** `done`, `pending` and `newer` are derived from the store on every
call: the global marker, and whether any readable campaign is unmarked (an
unreadable one is reported in `skipped`, also derived, and does not hold the
store at `pending` -- nothing a re-run does can read it). Only `running`, a
failure reason, the last run's skips and the safety archive's name are
remembered, keyed by root, in `<home>/.cache/inference-migration.json` --
derived data, outside every backup.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from .. import (
    atomic,
    backups,
    config,
    embed_space,
    llm_connections,
    locks,
    paths,
    revision,
    routing,
)
from .. import inference_keys as keys
from ..campaigns import paths as campaign_paths
from ..campaigns import read as campaign_read
from ..frontmatter import dump_frontmatter, parse_frontmatter
from . import facts, providers, translate

log = logging.getLogger(__name__)

#: Every state `status` answers.
STATES: tuple[str, ...] = ("done", "pending", "running", "failed", "newer")

#: The legacy routes' preset keys: spelled the same in both layouts
#: (`inference_keys.GLOBAL_KEYS`), so the migration leaves them as they are.
_SHARED_PRESET_KEYS = frozenset(routing.PRESET_CONFIG_KEYS)

#: The global keys the migration writes, every one on every switch: the
#: format-2 layout without the marker (written beside them) and without the
#: preset keys both layouts share.
OWNED_GLOBAL_KEYS: tuple[str, ...] = tuple(
    k for k in keys.GLOBAL_KEYS
    if k != keys.FORMAT_KEY and k not in _SHARED_PRESET_KEYS)

#: The reason a run that found its root moved under it reports.
MOVED = "the storage location changed during the upgrade; it resumes on the next start there"
#: The reason a run stopped by its caller (app shutdown) reports.
STOPPED = "the app shut down during the upgrade; it resumes on the next start"


class Status(NamedTuple):
    state: str
    reason: str = ""
    skipped: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """The JSON shape (`not_migrated`'s `status`, Settings)."""
        return {"state": self.state, "reason": self.reason, "skipped": list(self.skipped)}


# ---- what is remembered ----
_active_guard = threading.Lock()
#: Roots a migration is running on in THIS process -- `running` for a caller
#: on the migrating thread itself, which the reentrant lock would let through.
_active: set[str] = set()


@contextmanager
def _running_on(root: Path) -> Iterator[None]:
    with _active_guard:
        _active.add(str(root))
    try:
        yield
    finally:
        with _active_guard:
            _active.discard(str(root))


def _status_path(root: Path) -> Path:
    return root / ".cache" / "inference-migration.json"


def _recall(root: Path) -> dict:
    """What the last run on `root` left: `{}` when nothing, when unreadable, or
    when it was written for another root (a copied or synced store)."""
    try:
        raw = json.loads(_status_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict) or raw.get("root") != str(root):
        return {}
    return raw


def _remember(root: Path, *, running: bool, failed: str = "",
              skipped: list[str] | tuple[str, ...] = (), safety: str = "") -> None:
    """Record the run's state for `root`. Best effort: derived data, and a
    status that cannot be written must not stop the migration it describes."""
    note = {"root": str(root), "running": running, "pid": os.getpid(),
            "failed": failed, "skipped": list(skipped), "safety": safety}
    path = _status_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, json.dumps(note, indent=2) + "\n")
    except OSError as exc:
        log.warning("inference migration: could not record its status -- %s", exc)


def _is_running(root: Path, note: dict) -> bool:
    """A run on `root` in this process, or one another process says it has.

    Another process's note can outlive a crash; that lasts until the next
    start on this root, whose own run rewrites it -- or until the store is
    done, which `status` checks first."""
    with _active_guard:
        if str(root) in _active:
            return True
    return bool(note.get("running")) and note.get("pid") != os.getpid()


def _kept_safety(note: dict) -> str:
    """The safety archive an earlier run on this root took, when it is still
    in the backup directory; "" when there is none to reuse."""
    name = note.get("safety")
    if (not isinstance(name, str) or not name.startswith(backups.SAFETY_PREFIX)
            or Path(name).name != name):
        return ""
    try:
        return name if (backups.backup_dir() / name).is_file() else ""
    except OSError:
        return ""


# ---- reading the store ----
def _config_exists(root: Path) -> bool:
    return (root / "config.md").exists()


def _campaign_marks() -> tuple[dict[str, bool], dict[str, str]]:
    """(cid -> whether its `campaign.md` carries the current (or a newer)
    marker, cid -> why it cannot be read) -- every campaign is in exactly
    one of the two."""
    marks: dict[str, bool] = {}
    unreadable: dict[str, str] = {}
    for cid, _name, _world in campaign_read.world_refs():
        if not paths.safe_id(cid):
            continue
        try:
            meta, _ = parse_frontmatter(campaign_paths.campaign_meta_path(cid)
                                        .read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            unreadable[cid] = str(exc)
            continue
        marks[cid] = keys.is_current(meta) or keys.is_newer(meta)
    return marks, unreadable


def _unreadable_skips(unreadable: dict[str, str]) -> list[str]:
    return [f"campaign {cid}: {why}" for cid, why in unreadable.items()]


def _merged(*groups: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """The skips of every group, in order, each once."""
    return tuple(dict.fromkeys(s for group in groups for s in group))


def status() -> Status:
    """Where the store at `paths.home()` stands. Never writes."""
    root = paths.home()
    if not _config_exists(root):
        # A fresh store is born current (ruling 13); there is nothing to move.
        return Status("done")
    cfg = config.read_config()
    if keys.is_newer(cfg):
        return Status("newer")
    note = _recall(root)
    marks, unreadable = _campaign_marks()
    remembered = [str(s) for s in note.get("skipped") or () if isinstance(s, str)]
    skipped = _merged(remembered, _unreadable_skips(unreadable))
    # Done first: what the store says outranks a note that can outlive a crash.
    if keys.is_current(cfg) and False not in marks.values():
        return Status("done", "", skipped)
    if _is_running(root, note):
        return Status("running", "", skipped)
    failed = note.get("failed")
    failed = failed if isinstance(failed, str) else ""
    return Status("failed" if failed else "pending", failed, skipped)


# ---- the translation, persisted ----
#: What a connection or facts WRITE may raise that skips its item.
_UNREADABLE = (llm_connections.ConnectionNotFound, locks.StoreBusy, OSError,
               UnicodeDecodeError)


def _lookup() -> translate.Lookup:
    """A raw connection by id, memoised for one run.

    None only when no connection by that id exists -- a dangling reference,
    persisted as one. A file that is there but cannot be read RAISES
    (`llm_connections.ConnectionUnreadableError`, an `OSError`): what is read here
    is written down beside the marker, and a read a sync client blocked would
    otherwise become a selection with an empty model and no preset, for good.
    The global switch fails on it and the next start retries; a campaign is
    skipped and finished next time."""
    seen: dict[str, dict | None] = {}

    def lookup(conn_id: str) -> dict | None:
        if conn_id not in seen:
            seen[conn_id] = llm_connections.read_connection_strict(conn_id)
        return seen[conn_id]

    return lookup


def connection_reader() -> translate.Lookup:
    """The strict, memoised connection lookup the migration translates with
    (`_lookup`), for `campaign_fields` called from outside it: a campaign born
    as a copy into a store already switched (`campaigns.lifecycle
    .publish_birth`)."""
    return _lookup()


def _selection_keys() -> Iterator[Callable[[str], str]]:
    """The key builder of every selection a scope can hold: each generative
    role and its fallback, and each route's pin."""
    for role in keys.GENERATIVE_ROLES:
        yield functools.partial(keys.role_key, role)
        yield functools.partial(keys.fallback_key, role)
    for route in routing.ROUTES:
        yield functools.partial(keys.pin_key, route.key)


def _enriched(fields: dict[str, str], lookup: translate.Lookup) -> dict[str, str]:
    """`fields` with an unset Claude model written as `opus` wherever it is a
    selection's model -- what the Claude adapter runs an unset model as, so
    nothing that resolves changes; what a later edit starts from does."""
    out = dict(fields)
    for key in _selection_keys():
        provider = out.get(key("provider"), "")
        if not provider or out.get(key("model")):
            continue
        raw = lookup(provider)
        if raw is not None and raw.get("kind") == "claude":
            out[key("model")] = config.DEFAULT_CLAUDE_MODEL
    return out


def _persistable(view: dict) -> dict[str, str]:
    """A translated campaign view as the keys to write: the shared preset keys
    left alone, and an empty value dropped -- an absent key and "" read the
    same, a campaign's frontmatter is a file people read by hand, and a
    campaign is migrated and marked in one write, so nothing a partial run
    wrote is left for a later one to clear."""
    return {k: str(v) for k, v in view.items()
            if k not in _SHARED_PRESET_KEYS and str(v) != ""}


def global_fields(cfg: dict, lookup: translate.Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `config.md` migrates to (steps 5-7): every
    one of `OWNED_GLOBAL_KEYS`, "" where it is unset."""
    view = translate.global_view(cfg, lookup)
    fields = dict.fromkeys(OWNED_GLOBAL_KEYS, "")
    fields.update((k, str(view[k])) for k in OWNED_GLOBAL_KEYS if k in view)
    if embed_space.resolve(cfg) is None:
        # A legacy choice that never embedded stays off (ruling 5).
        for part in keys.EMBEDDING_PARTS:
            fields[keys.role_key("embedding", part)] = ""
    return _enriched(fields, lookup)


def campaign_fields(meta: dict, lookup: translate.Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `campaign.md` migrates to (steps 6-7)."""
    return _enriched(_persistable(translate.campaign_view(meta, lookup, current=False)),
                     lookup)


# ---- one campaign ----
def campaign(cid: str) -> bool:
    """Migrate one unmarked campaign -- its routes, split routes and its own
    marker -- and return whether it wrote.

    The CALLER holds `campaign_lock(cid)`: `ensure` under its no-wait hold, and
    a new-layout write to an unmarked campaign inside the hold that covers the
    write (spec 11.1), so the two are one step. This never takes the lock, and
    refuses with RuntimeError when the calling thread does not hold it.

    A marked campaign (or one a newer build marked) is left alone. The write is
    atomic, bumps the campaign's revision in the same hold, and does NOT stamp
    `updated`: the library is ordered by it, and an upgrade is not something
    that happened in the campaign.
    """
    if not locks.holds_campaign(cid):
        raise RuntimeError(f"migrate.campaign({cid!r}) needs the caller to hold its lock")
    mp = campaign_paths.campaign_meta_path(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    if keys.is_current(meta) or keys.is_newer(meta):
        return False
    meta.update(campaign_fields(meta, _lookup()))
    meta[keys.FORMAT_KEY] = keys.CURRENT_FORMAT
    atomic.write_text(mp, dump_frontmatter(meta, body))
    revision.bump(cid)
    return True


# ---- the run ----
@contextmanager
def held_still() -> Iterator[bool]:
    """Try the migration lock and hold it for the block; yields whether it was
    free. For an operation that must not overlap a migration -- `PUT
    /config/data-dir` -- and must not wait for one either."""
    lock = locks.inference_migration_lock()
    got = lock.acquire(blocking=False)
    try:
        yield got
    finally:
        if got:
            lock.release()


def ensure(stop: threading.Event | None = None) -> Status:
    """Run the migration on the store at `paths.home()`, synchronously; return
    the status it leaves. Returns the current status at once, without waiting,
    when the migration lock is held. `stop`, once set, ends the run at the next
    item boundary, leaving it `pending`."""
    with held_still() as free:
        if not free:
            return status()
        # Inside the hold: a data-dir switch holds this lock across its move,
        # so the root read here is the one the run's first writes land in.
        root = paths.home()
        # `running` from the moment the lock is ours, so a second caller that
        # lost the lock never reads `pending` off a run that has begun.
        with _running_on(root):
            outcome = _run(root, stop)
    return outcome if outcome is not None else status()


class _Run:
    """One run's pinned root, stop flag, skips and safety archive."""

    def __init__(self, root: Path, stop: threading.Event | None, safety: str,
                 skipped: list[str]):
        self.root = root
        self.stop = stop
        self.safety = safety
        self.skipped = skipped

    def halted(self) -> str:
        """Why the run must stop before its next item; "" to go on."""
        if self.stop is not None and self.stop.is_set():
            return STOPPED
        if paths.home() != self.root:
            return MOVED
        return ""

    def remember(self, *, running: bool, failed: str = "") -> None:
        _remember(self.root, running=running, failed=failed, skipped=self.skipped,
                  safety=self.safety)


def _run(root: Path, stop: threading.Event | None) -> Status | None:
    """The run, under the lock: None when the store's own status says how it
    ended, else the Status to report (a failure, a stop, a root that moved)."""
    if not _config_exists(root):
        return None                        # a fresh store is never migrated
    # A newer build's store first: it is not this build's to touch at all, and
    # the connection seeding below writes (which it refuses on its own too).
    if keys.is_newer(config.read_config()):
        return None
    llm_connections.ensure_migrated()
    cfg = config.read_config()
    if keys.is_newer(cfg):
        return None
    current = keys.is_current(cfg)
    marks, unreadable = _campaign_marks()
    left = [cid for cid, marked in marks.items() if not marked]
    if current and not left:
        return None
    run = _Run(root, stop, _kept_safety(_recall(root)), _unreadable_skips(unreadable))
    run.remember(running=True)
    outcome: Status | None = None
    try:
        outcome = _steps(run, current, left)
    except (OSError, UnicodeDecodeError, locks.StoreBusy) as exc:
        outcome = Status("failed", f"the upgrade stopped: {exc}")
    except BaseException as exc:
        # Recorded, then raised: whatever it was, the note must not go on
        # saying `running` for a run that has ended.
        outcome = Status("failed", f"the upgrade stopped: {exc!r}")
        raise
    finally:
        failed = outcome.reason if outcome is not None and outcome.state == "failed" else ""
        run.remember(running=False, failed=failed)
    return None if outcome is None else outcome._replace(skipped=tuple(run.skipped))


def _steps(run: _Run, current: bool, left: list[str]) -> Status | None:
    """The steps; None when they all ran, else the Status to stop on. The
    campaigns come after the switch (step 8, above)."""
    if not current:
        if (stopped := _before_campaigns(run)) is not None:
            return stopped
        if (stopped := _switch(run)) is not None:
            return stopped
        # Listed again now the marker is down: a campaign created since the
        # run began was born at format 1 (before the switch) and is unmarked,
        # and one created (or forked) from here on is born marked
        # (`campaigns.lifecycle.publish_birth`, in the switch's own lock).
        marks, _ = _campaign_marks()
        left = [cid for cid, marked in marks.items() if not marked]
    for cid in left:
        if why := run.halted():
            return Status("pending", why)
        _campaign_step(cid, run.skipped)
    return None


def _before_campaigns(run: _Run) -> Status | None:
    """Steps 1-2 (backup, providers); None when they all ran. Step 3, the
    model facts, is `_switch`'s."""
    if why := run.halted():
        return Status("pending", why)
    if not run.safety:
        try:
            made = backups.create_backup(prefix=backups.SAFETY_PREFIX)
        except (OSError, locks.StoreBusy) as exc:
            return Status("failed", f"the safety backup failed: {exc}")
        run.safety = made.name
        # At once, so a run killed from here on is not backed up again.
        run.remember(running=True)
    if why := run.halted() or _providers(run):
        return Status("pending", why)
    return None


def _switch(run: _Run) -> Status | None:
    """Step 3, then steps 5-7 and 9 as one write, from a read under the same
    hold.

    `llm_connections.LOCK` -- which is `config_lock`, cross-process -- is held
    around it. A connection write that refuses the legacy model fields at
    format 2 reads the format under that lock, so the switch cannot land
    between its check and its write, from this server or another.

    The model facts are copied inside that same hold: the app serves at
    format 1 until the marker lands, so a `vision`, `prefill`, `post_process`
    or `model` edit made before it would otherwise be switched past without
    ever reaching the facts that format 2 reads instead -- and with the hold
    cross-process, an edit by another server cannot land between the copy
    and the marker either."""
    with llm_connections.LOCK:
        if why := run.halted():
            return Status("pending", why)
        early = config.read_config()
        if keys.is_current(early) or keys.is_newer(early):
            return None                    # switched meanwhile, by another device
        if why := _facts(run):
            return Status("pending", why)
        with locks.config_lock():
            if why := run.halted():
                return Status("pending", why)
            cfg = config.read_config()
            if keys.is_current(cfg) or keys.is_newer(cfg):
                return None                # switched meanwhile, by another device
            fields = global_fields(cfg, _lookup())
            fields[keys.FORMAT_KEY] = keys.CURRENT_FORMAT
            config.write_config(**fields)
    return None


def _providers(run: _Run) -> str:
    """Step 2: `preset` and `billing` on each connection that has no preset.
    Returns why it stopped early, or "". A connection that cannot be read
    raises (`list_connections_strict`) and fails the run: left out, it would
    be switched with no preset and its model facts never written."""
    for listed in llm_connections.list_connections_strict():
        if why := run.halted():
            return why
        # Read again in the hold that writes: the preset is inferred from the
        # record, and another server's edit landing between the list and the
        # write would otherwise be stamped with a stale inference. A record
        # that cannot be read fails the run, as the list does.
        with llm_connections.LOCK:
            conn = llm_connections.read_connection_strict(listed["id"])
            if conn is None or str(conn.get("preset") or ""):
                continue
            try:
                llm_connections.update_connection(
                    conn["id"], preset=providers.infer(conn).id,
                    billing=providers.billing(conn))
            except _UNREADABLE as exc:
                run.skipped.append(f"provider {conn['id']}: {exc}")
    return ""


def _stated(conn: dict) -> dict[str, Callable[[str, str], None]]:
    """A connection's legacy model fields that differ from the defaults, each
    as the facts write that states it (provider id, model)."""
    out: dict[str, Callable[[str, str], None]] = {}
    vision = str(conn.get("vision") or "")
    if vision:
        out["vision"] = lambda pid, model: facts.set_stated(pid, model, vision=vision)
    if conn.get("prefill") is True:
        out["prefill"] = lambda pid, model: facts.set_stated(pid, model, prefill=True)
    post_process = str(conn.get("post_process") or "")
    if post_process not in ("", "none"):
        out["post_process"] = lambda pid, model: facts.set_stated(
            pid, model, post_process=post_process)
    return out


def _facts(run: _Run) -> str:
    """Step 3: each connection's stated model behaviour, as its model's facts.
    A value the writer refuses, or a write that fails, is skipped alone; the
    others still land. Returns why it stopped early, or "". A connection that
    cannot be read fails the run, as in `_providers`."""
    for conn in llm_connections.list_connections_strict():
        if why := run.halted():
            return why
        model = facts.model_of(conn)
        for field, state in _stated(conn).items():
            try:
                state(conn["id"], model)
            except (ValueError, *_UNREADABLE) as exc:
                run.skipped.append(
                    f"facts {conn['id']} {model or '(no model)'} {field}: {exc}")
    return ""


def _campaign_step(cid: str, skipped: list[str]) -> None:
    """Step 8 for one campaign, under its own no-wait hold."""
    try:
        with locks.campaign_lock_nowait(cid) as got:
            if not got:
                skipped.append(f"campaign {cid}: busy; finished on the next start")
                return
            campaign(cid)
    except (OSError, UnicodeDecodeError, ValueError, campaign_paths.CampaignNotFound,
            locks.StoreBusy) as exc:
        skipped.append(f"campaign {cid}: {exc}")
