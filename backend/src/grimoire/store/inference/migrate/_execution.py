"""Strict mapping/campaign writes and the pinned-root resumable migration/retirement sequence under the original outer hold."""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ... import (
    atomic,
    backups,
    config,
    frontmatter,
    llm_connections,
    locks,
    paths,
    revision,
    sampler_presets,
)
from ... import inference_keys as keys
from ... import inference_retired as retired
from ...campaigns import paths as campaign_paths
from .. import facts, legacy_plan, providers, retire
from . import _contracts, _tracking

# ---- the translation, persisted ----
#: What a connection or facts WRITE may raise that skips its item.
_UNREADABLE = (llm_connections.ConnectionNotFound, locks.StoreBusy, OSError,
               UnicodeDecodeError)


def _lookup() -> legacy_plan.Lookup:
    """A raw connection by id, memoised for one run: the planner's `migrate`
    mode (`legacy_plan.lookup`).

    None only when no connection by that id exists -- a dangling reference,
    persisted as one. A file that is there but cannot be read RAISES
    (`llm_connections.ConnectionUnreadableError`, an `OSError`): what is read here
    is written down beside the marker, and a read a sync client blocked would
    otherwise become a selection with an empty model and no preset, for good.
    The global switch fails on it and the next start retries; a campaign is
    skipped and finished next time."""
    return legacy_plan.lookup(mode="migrate")


def connection_reader() -> legacy_plan.Lookup:
    """The strict, memoised connection lookup the migration translates with
    (`_lookup`), for `campaign_fields` called from outside it: a campaign born
    as a copy into a store already switched (`campaigns.lifecycle
    .publish_birth`)."""
    return legacy_plan.lookup(mode="migrate")


def global_fields(cfg: dict, lookup: legacy_plan.Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `config.md` migrates to (steps 5-7): every
    one of `OWNED_GLOBAL_KEYS`, "" where it is unset -- the planner's
    `mapped` (`legacy_plan.global_mapped`, which `global_plan` plans it with),
    and never its derived-preset repoints (N2): a persisted preset key always
    names a preset file that exists."""
    return legacy_plan.global_mapped(cfg, lookup)


def campaign_fields(meta: dict, lookup: legacy_plan.Lookup) -> dict[str, str]:
    """The format-2 keys a legacy `campaign.md` migrates to (steps 6-7): the
    planner's `mapped` (`legacy_plan.campaign_mapped`), never its repoints
    (N2)."""
    return legacy_plan.campaign_mapped(meta, lookup)


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

    A `campaign.md` that holds no record (`RecordUnreadableError`) is never
    rewritten. The read, the check and the write are in `config.format_hold`
    (reentrant: the settings write calls this inside its own), so a store a
    newer build switched -- before this campaign's turn or while the loop ran
    -- raises `config.NewerFormatError` and this campaign is not marked.
    """
    if not locks.holds_campaign(cid):
        raise RuntimeError(f"migrate.campaign({cid!r}) needs the caller to hold its lock")
    mp = campaign_paths.campaign_meta_path(cid)
    with config.format_hold():
        meta, body = frontmatter.read_record(mp, f"campaign {cid}'s campaign.md")
        if keys.is_current(meta) or keys.is_newer(meta):
            return False
        lookup = _lookup()
        if legacy_plan.is_retired(meta):
            # A campaign marked retired before it was ever migrated is
            # derived nothing, so a GLM effort its pins carried is lost: its
            # notes go into the retirement record BEFORE this write, the one
            # moment they can still be planned (guarantee 7; review M-2, I-1).
            retired.record_notes(_stranded_notes(meta, lookup, cid))
        meta.update(campaign_fields(meta, lookup))
        meta[keys.FORMAT_KEY] = keys.CURRENT_FORMAT
        atomic.write_text(mp, frontmatter.dump_frontmatter(meta, body))
    revision.bump(cid)
    return True


def _stranded_notes(meta: dict, lookup: legacy_plan.Lookup,
                    cid: str) -> tuple[retired.Note, ...]:
    """The notes the planner gives an unmarked campaign carrying the
    retirement marker (`legacy_plan._stranded`)."""
    return legacy_plan.campaign_plan(meta, glob={}, global_current=True, lookup=lookup,
                                     presets=sampler_presets.read_preset_strict,
                                     cid=cid).notes


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


def ensure(stop: threading.Event | None = None) -> _contracts.Status:
    """Run the migration on the store at `paths.home()`, synchronously; return
    the status it leaves. Returns the current status at once, without waiting,
    when the migration lock is held. `stop`, once set, ends the run at the next
    item boundary, leaving it `pending`."""
    attempted = try_ensure(stop=stop)
    return attempted if attempted is not None else _tracking.status()


def try_ensure(stop: threading.Event | None = None, *,
               expected_root: Path | None = None) -> _contracts.Status | None:
    """One immediate acquisition attempt; None means contention only (#496).

    `ensure` keeps its status-returning no-wait contract. The background
    starter needs to distinguish a lost acquisition from an actual pass that
    deliberately leaves busy campaigns pending: a brief status probe must not
    consume the sole startup attempt. The caller may pin its requested root;
    check it inside the acquired hold before any initialization or write.
    """
    with held_still() as free:
        if not free:
            return None
        # Inside the hold: a data-dir switch holds this lock across its move,
        # so the root read here is the one the run's first writes land in.
        root = paths.home()
        if expected_root is not None and root != expected_root:
            return _contracts.Status("pending", _contracts.MOVED)
        # `running` from the moment the lock is ours, so a second caller that
        # lost the lock never reads `pending` off a run that has begun.
        with _tracking._running_on(root):
            outcome = _run(root, stop)
    return outcome if outcome is not None else _tracking.status()


class _Run:
    """One run's pinned root, stop flag, skips and safety archives."""

    def __init__(self, root: Path, stop: threading.Event | None, safety: str,
                 skipped: list[str], retire_safety: str = ""):
        self.root = root
        self.stop = stop
        self.safety = safety
        self.skipped = skipped
        #: Whether THIS run created `safety` (not reused it): only then does
        #: it stand in for retirement's archive (N13).
        self.created_safety = False
        self.retire_safety = retire_safety
        self.retire_failed = ""
        self.progress: dict | None = None
        self.progress_owner = ""
        self._progress_at = 0.0
        self._saved_progress: dict | None = None

    def halted(self) -> str:
        """Why the run must stop before its next item; "" to go on."""
        if self.stop is not None and self.stop.is_set():
            return _contracts.STOPPED
        if paths.home() != self.root:
            return _contracts.MOVED
        return ""

    def remember(self, *, running: bool, failed: str = "") -> None:
        _tracking._remember(self.root, running=running, failed=failed, skipped=self.skipped,
                  safety=self.safety, retire_safety=self.retire_safety,
                  retire_failed=self.retire_failed, progress=self.progress,
                  progress_owner=self.progress_owner)


    def archive_progress(self, phase: str, done: int, total: int | None) -> None:
        """Local counters are exact; notes persist transitions and <=4 ticks/s."""
        self.progress = {"phase": phase, "done": done, "total": total}
        with _tracking._active_guard:
            if str(self.root) in _tracking._active:
                _tracking._active[str(self.root)] = self.progress
        now = time.monotonic()
        previous = self._saved_progress
        transition = previous is None or (previous["phase"], previous["total"]) != (phase, total)
        if transition or (total is not None and done == total) or now - self._progress_at >= 0.25:
            self.remember(running=True)
            self._progress_at = now
            self._saved_progress = self.progress


def _archive(run: _Run, prefix: str, phase: str) -> Path:
    """Both safety phases share publication/progress teardown (#496)."""
    with _tracking._progress_lease(run.root) as owner:
        run.progress_owner = owner
        try:
            return backups.create_backup(
                prefix=prefix, on_progress=lambda done, total: run.archive_progress(phase, done, total))
        finally:
            run.progress = None
            run.progress_owner = ""
            with _tracking._active_guard:
                if str(run.root) in _tracking._active:
                    _tracking._active[str(run.root)] = None
            run.remember(running=True)


def _run(root: Path, stop: threading.Event | None) -> _contracts.Status | None:
    """The run, under the lock: None when the store's own status says how it
    ended, else the Status to report (a failure, a stop, a root that moved)."""
    if not _tracking._config_exists(root):
        return None                        # a fresh store is never migrated
    # A `config.md` that holds no record is not a legacy store with nothing
    # set: switched, it would be stamped current over an empty layout and its
    # legacy keys never read again once they arrive. Nothing is written --
    # the connection seeding below would write it too -- and the next start
    # retries.
    try:
        _tracking._config_record(root)
    except (OSError, UnicodeDecodeError) as exc:
        reason = f"the upgrade stopped: {exc}"
        note = _tracking._recall(root)
        _tracking._remember(root, running=False, failed=reason, safety=_tracking._kept_safety(note),
                  retire_safety=_tracking._kept_retire_safety(note),
                  retire_failed=_tracking._note_text(note, "retire_failed"))
        return _contracts.Status("failed", reason, (), _tracking._retirement(_tracking._note_text(note, "retire_failed")))
    # A newer build's store first: it is not this build's to touch at all, and
    # the connection seeding below writes (which it refuses on its own too).
    if keys.is_newer(config.read_config()):
        return None
    llm_connections.ensure_migrated()
    cfg = config.read_config()
    if keys.is_newer(cfg):
        return None
    current = keys.is_current(cfg)
    marks, unreadable = _tracking._campaign_marks()
    left = [cid for cid, marked in marks.items() if not marked]
    idle, planned = _idle(root, current, left)
    if idle:
        return None
    note = _tracking._recall(root)
    run = _Run(root, stop, _tracking._kept_safety(note), _tracking._unreadable_skips(unreadable),
               _tracking._kept_retire_safety(note))
    run.remember(running=True)
    outcome: _contracts.Status | None = None
    try:
        outcome = _steps(run, current, left, planned)
    except (OSError, UnicodeDecodeError, locks.StoreBusy) as exc:
        outcome = _contracts.Status("failed", f"the upgrade stopped: {exc}")
    except BaseException as exc:
        # Recorded, then raised: whatever it was, the note must not go on
        # saying `running` for a run that has ended.
        outcome = _contracts.Status("failed", f"the upgrade stopped: {exc!r}")
        raise
    finally:
        failed = outcome.reason if outcome is not None and outcome.state == "failed" else ""
        run.remember(running=False, failed=failed)
    if outcome is None:
        return None
    return dataclasses.replace(outcome, skipped=tuple(run.skipped),
                               retirement=_tracking._retirement(run.retire_failed))


def _idle(root: Path, current: bool, left: list[str]) -> tuple[bool, retire.PassPlan | None]:
    """Whether this start has nothing to write -- the store current, every
    campaign marked, and retirement with no unit it can write -- and the
    retirement pass planned on the way, when it was.

    Something left by retirement is not on its own a run: a store whose
    leftovers cannot be read just now (an unreadable campaign, a connection a
    sync client holds) is not re-run on every start. Why they could not be
    planned is noted -- only when it changed, so an idle start writes
    nothing -- and `retire.left()` names them in the status."""
    if not current or left:
        return False, None
    if not retire.left():
        return True, None
    planned = retire.pass_plan(legacy_plan.lookup(mode="retire"))
    if not any(unit.work for unit in planned.units):
        _tracking._note_retire_failed(root, "; ".join(planned.dropped))
        return True, planned
    return False, planned


def _steps(run: _Run, current: bool, left: list[str],
           planned: retire.PassPlan | None = None) -> _contracts.Status | None:
    """The steps; None when they all ran, else the Status to stop on. The
    campaigns come after the switch (step 8, above), and retirement after
    them -- `planned` is its pass when `_run` already planned it, on a store
    with nothing else to do (nothing has been written since)."""
    if not current:
        if (stopped := _before_campaigns(run)) is not None:
            return stopped
        if (stopped := _switch(run)) is not None:
            return stopped
        # Listed again now the marker is down: a campaign created since the
        # run began was born at format 1 (before the switch) and is unmarked,
        # and one created (or forked) from here on is born marked
        # (`campaigns.lifecycle.publish_birth`, in the switch's own lock).
        marks, _ = _tracking._campaign_marks()
        left = [cid for cid, marked in marks.items() if not marked]
    for cid in left:
        if why := run.halted():
            return _contracts.Status("pending", why)
        try:
            _campaign_step(cid, run.skipped)
        except config.NewerFormatError:
            # A newer build switched the store while the loop ran: what is
            # left is not this build's to mark (spec 11.3).
            return _contracts.Status("newer")
    _retire(run, planned)
    return None


def _before_campaigns(run: _Run) -> _contracts.Status | None:
    """Steps 1-2 (backup, providers); None when they all ran. Step 3, the
    model facts, is `_switch`'s."""
    if why := run.halted():
        return _contracts.Status("pending", why)
    if not run.safety:
        try:
            made = _archive(run, backups.SAFETY_PREFIX, "safety_backup")
        except (OSError, locks.StoreBusy) as exc:
            return _contracts.Status("failed", f"the safety backup failed: {exc}")
        run.safety = made.name
        run.created_safety = True
        # At once, so a run killed from here on is not backed up again.
        run.remember(running=True)
    if why := run.halted() or _providers(run):
        return _contracts.Status("pending", why)
    return None


def _switch(run: _Run) -> _contracts.Status | None:
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
            return _contracts.Status("pending", why)
        _tracking._config_record(run.root)           # a placeholder fails the run
        early = config.read_config()
        if keys.is_newer(early):
            return _contracts.Status("newer")         # not this build's to go on with
        if keys.is_current(early):
            return None                    # switched meanwhile, by another device
        if why := _facts(run):
            return _contracts.Status("pending", why)
        with locks.config_lock():
            if why := run.halted():
                return _contracts.Status("pending", why)
            _tracking._config_record(run.root)
            cfg = config.read_config()
            if keys.is_newer(cfg):
                return _contracts.Status("newer")
            if keys.is_current(cfg):
                return None                # switched meanwhile, by another device
            lookup = _lookup()
            if legacy_plan.is_retired(cfg):
                # As `campaign`: a `config.md` already marked retired is
                # derived nothing; its notes are recorded before the switch.
                retired.record_notes(legacy_plan.global_plan(
                    cfg, lookup, sampler_presets.read_preset_strict).notes)
            fields = global_fields(cfg, lookup)
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


def _facts(run: _Run) -> str:
    """Step 3: each connection's stated model behaviour, as its model's facts
    (`facts.adopt_legacy`, which first takes back whatever an earlier,
    interrupted run copied, so a field the user reset to its default since, or
    a model the connection moved off, is not left stating the old value).
    Every connection is visited, one that states nothing too, for that reason.
    A value the writer refuses, or a write that fails, is skipped alone; the
    others still land. Returns why it stopped early, or "". A connection that
    cannot be read fails the run, as in `_providers` -- and so does a facts
    file that cannot be read (`facts.FactsUnreadableError`): skipped, its
    copy would be switched past and never made.

    Each connection is read through the migration's lookup (`_lookup`), as
    the switch's mapping and play's overlay read it: a connection retirement
    stripped answers with the fields the retirement record holds for it, so
    a copy made from them is never taken back as "now states nothing", and
    one whose record entry has not arrived raises
    (`retired.EntryMissingError`) and fails the run, which the next start
    retries."""
    lookup = _lookup()
    for listed in llm_connections.list_connections_strict():
        if why := run.halted():
            return why
        conn = lookup(listed["id"])
        if conn is None:
            continue
        model = facts.model_of(conn)
        label = f"facts {conn['id']} {model or '(no model)'}"
        try:
            refused = facts.adopt_legacy(conn["id"], model, legacy_plan.stated(conn))
        except facts.FactsUnreadableError:
            raise
        except (ValueError, *_UNREADABLE) as exc:
            run.skipped.append(f"{label}: {exc}")
            continue
        run.skipped.extend(f"{label} {field}: {why}" for field, why in refused.items())
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


# ---- retirement (slice I, ruling 6) ----
def _retire(run: _Run, planned: retire.PassPlan | None = None) -> None:
    """Retirement, after the marker and the campaigns: the archive, then
    `config.md`, then each campaign (`store.inference.retire`). Never moves
    the migration's state: whatever stops it is recorded in
    `run.retire_failed`, what it did not reach is `retire.left()`'s, and the
    next `ensure` goes on from there.

    0. The pass is planned whole first (`retire.pass_plan`). When any unit
       deletes or replaces a stored value (`retire.needs_archive`), a
       `pre-retirement-settings-grimoire-` archive is taken before the pass's first
       write of any kind, marker-only writes included -- unless this same run
       CREATED the `pre-inference-` one, or an earlier pass on this root took
       one that is still there (reused, R2-3). If it fails, nothing is
       written. A pass that only adds markers takes none (N3).
    1. `retire.retire_global`, then
    2. `retire.retire_campaign` for each campaign with work, under its own
       `campaign_lock_nowait`: a busy one is left for the next start, then
    3. the strip (`retire.strip_connection`), connection by connection, which
       stops where its precondition fails -- every scope retired -- and goes
       on next start.

    Each unit is planned again inside its own hold; one that now needs the
    archive this pass did not take is left for the next run (R3-1). A file
    that cannot be read (`RecordUnreadableError`, the read's own errors,
    `ConnectionUnreadableError`) stops its own unit and nothing else, and a
    newer build's marker stops the pass. `run.halted()` is checked before
    each unit (N19)."""
    if run.halted():
        return
    plan = planned if planned is not None else retire.pass_plan(
        legacy_plan.lookup(mode="retire"))
    archived = _retire_archive(run, plan)
    if archived is None:
        return
    failures = list(plan.dropped)
    try:
        _retire_units(run, plan, archived, failures)
    except config.NewerFormatError:
        return                      # a newer build switched the store meanwhile
    finally:
        run.retire_failed = "; ".join(failures)


def _retire_archive(run: _Run, plan: retire.PassPlan) -> bool | None:
    """Step 0: whether the pass is covered by an archive -- one this run
    created (`pre-inference-`), one an earlier pass took and the note names,
    or one taken now because the pass needs it. None when that archive
    failed: nothing may be written."""
    archived = run.created_safety or bool(run.retire_safety)
    if not retire.needs_archive(plan) or archived:
        return archived
    try:
        made = _archive(run, backups.RETIRE_PREFIX, "retirement_backup")
    except (OSError, locks.StoreBusy) as exc:
        run.retire_failed = f"the retirement archive failed: {exc}"
        return None
    run.retire_safety = made.name
    # At once, so a pass killed from here on reuses it.
    run.remember(running=True)
    return True


def _retire_units(run: _Run, plan: retire.PassPlan, archived: bool,
                  failures: list[str]) -> None:
    """Steps 1-3, unit by unit, appending each failure to `failures`;
    `config.NewerFormatError` is raised."""
    for unit in plan.units:
        if not unit.work:
            continue
        if run.halted():
            return
        if unit.conn_id:
            stopped, why = _strip_unit(unit, archived)
            if why:
                failures.append(why)
            if stopped:
                return
        elif why := _retire_unit(unit, archived):
            failures.append(why)


def _strip_unit(unit: retire.Unit, archived: bool) -> tuple[bool, str]:
    """One connection's strip: `(stop, why it failed)`. The strip stops at
    the first connection whose precondition does not hold -- a campaign this
    pass could not retire (busy, say) holds every connection's, until the
    next start. A file that cannot be read stops that connection alone."""
    try:
        return retire.strip_connection(unit.conn_id, archived=archived) is None, ""
    except retire.ArchiveNeededError:
        return False, ""
    except retire.UNIT_ERRORS as exc:
        return False, f"connection {unit.conn_id}: {exc}"


def _retire_unit(unit: retire.Unit, archived: bool) -> str:
    """One unit of the pass; why it failed, or "" (done, busy, or left for
    the next run). `config.NewerFormatError` is raised."""
    label = f"campaign {unit.cid}" if unit.cid else "config.md"
    try:
        if not unit.cid:
            retire.retire_global(legacy_plan.lookup(mode="retire"), archived=archived)
            return ""
        with locks.campaign_lock_nowait(unit.cid) as got:
            if got:
                retire.retire_campaign(unit.cid, legacy_plan.lookup(mode="retire"),
                                       archived=archived)
        return ""
    except retire.ArchiveNeededError:
        return ""                   # grew work since the plan: the next run's
    except retire.UNIT_ERRORS as exc:
        return f"{label}: {exc}"
