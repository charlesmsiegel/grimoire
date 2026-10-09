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
   What is copied is recorded beside it (`facts.adopt_legacy`), so a resumed
   run takes back a copy the record no longer says before copying afresh.
   Copied in the `llm_connections.LOCK` hold that writes the marker
   (`_switch`), so a legacy edit made before it is not switched past;
4. *(derived reasoning presets: not the migration's.)* They are
   retirement's, persisted after the marker by `retire` (slice I, rulings 4
   and 6, below); the migration persists the planner's `mapped` only, never a
   repoint, so every preset key it writes names a preset file that exists
   (N2);
5. **roles**, 6. **routes**, 7. **split routes**, 9. **marker** -- ONE
   `config.md` write, the last, derived from a read taken inside the
   `config_lock` hold that writes it: the planner's mapping of the legacy
   settings (`legacy_plan.global_plan(...).mapped`, never its repoints),
   persisted **verbatim** (padded ids, dangling references and
   `PRESET_CLEAR` exactly as it gives them; the split routes are already in
   it, read from their parents) with two enrichments -- an unset Claude model
   is written as `opus` wherever it is a selection's model, and the Embedding
   role is set only when the legacy configuration actually embeds
   (`embed_space.resolve`'s answer over the run's strict lookup,
   `legacy_plan.legacy_embeds`; ruling 5) -- plus `inference_format: "2"`.

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
   next `ensure` finishes it (it resolves through the planner, in memory,
   meanwhile).
   AFTER the marker, not before it: a campaign's routes are translated from
   the connections they name, and before the marker a connection's legacy
   `model` can still be edited -- by this server or another -- so a campaign
   migrated then kept the model the edit replaced, for good. Once the store
   is at format 2 those fields are frozen (refused on write), so what a
   campaign is translated from cannot move under its step, and an unmarked
   campaign reads the same mapping, in memory, until it is reached.

10. **retirement** (`_retire`, slice I ruling 6) -- after the campaigns, in
   the same run, and on every `ensure` of a current store that
   `retire.left()` says has anything left: the `pre-retirement-` archive when
   the pass deletes or replaces a stored value (skipped when this run CREATED
   the `pre-inference-` one, reused when an earlier pass's is still there),
   then `config.md`'s derived presets, repoint, legacy-key deletion and
   retirement marker in one write, then each campaign likewise under its own
   no-wait hold (an unmarked one migrated in that same write). It never moves
   `status().state`; what it has left is `Status.retirement`.

Every derived value is deterministic, so two devices migrating one synced
store write the same bytes. The migration itself leaves the legacy keys and
fields in place, frozen, for older builds; retirement then removes the keys
(and, in Task 6b, the connection fields).

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
a connection file that exists but cannot be read, a `config.md` that holds no
record (zero bytes, or unfenced: `RecordUnreadableError`) and a model-facts
file that cannot be read (`facts.FactsUnreadableError`) fail the migration (no
marker; the next start retries). None of them is "nothing there": every step
persists what it reads, so a read a sync client blocked would otherwise be
written down for good -- an empty model with no preset, an empty layout
stamped current, a facts file holding only the copy. An item that cannot be
migrated -- an unreadable `campaign.md` (undecodable, or holding no record;
it is never rewritten), one naming an unreadable connection, a connection or
model-facts write that fails or is refused, a busy campaign -- is skipped, its
reason recorded in `skipped`, and the run goes on. A newer build's marker,
whenever it is seen -- at the start, in the switch's hold, or under a
campaign's hold while the loop runs -- ends the run as `newer` and nothing
more is marked.

**Status.** `done`, `pending` and `newer` are derived from the store on every
call: the global marker, and whether any readable campaign is unmarked (an
unreadable one is reported in `skipped`, also derived, and does not hold the
store at `pending` -- nothing a re-run does can read it). Only `running`, a
failure reason, the last run's skips and the safety archive's name are
remembered, keyed by root, in `<home>/.cache/inference-migration.json` --
derived data, outside every backup -- beside retirement's archive name and
why its last pass stopped short (`retire_safety`, `retire_failed`).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .. import (
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
from .. import inference_keys as keys
from .. import inference_retired as retired
from ..campaigns import paths as campaign_paths
from ..campaigns import read as campaign_read
from . import facts, legacy_plan, providers, retire

log = logging.getLogger(__name__)

#: Every state `status` answers.
STATES: tuple[str, ...] = ("done", "pending", "running", "failed", "newer")

#: The global keys the migration writes, every one on every switch
#: (`legacy_plan.OWNED_GLOBAL_KEYS`, where the mapping lives).
OWNED_GLOBAL_KEYS: tuple[str, ...] = legacy_plan.OWNED_GLOBAL_KEYS

#: The reason a run that found its root moved under it reports.
MOVED = "the storage location changed during the upgrade; it resumes on the next start there"
#: The reason a run stopped by its caller (app shutdown) reports.
STOPPED = "the app shut down during the upgrade; it resumes on the next start"


def _no_retirement() -> dict:
    """`Status.retirement` when nothing about retirement was asked: a fresh
    dict per Status, so no caller can edit another's."""
    return {"left": [], "failed": ""}


@dataclasses.dataclass(frozen=True)
class Status:
    state: str
    reason: str = ""
    skipped: tuple[str, ...] = ()
    #: Retirement's own account (slice I): `left`, what it has still to do
    #: (`retire.left()`), and `failed`, why the last pass on this root stopped
    #: short ("" when it did not). Never moves `state`: a store whose
    #: migration is done reads `done` whatever retirement has left.
    retirement: dict = dataclasses.field(default_factory=_no_retirement)

    def as_dict(self) -> dict:
        """The JSON shape (`not_migrated`'s `status`, Settings)."""
        return {"state": self.state, "reason": self.reason, "skipped": list(self.skipped),
                "retirement": {"left": list(self.retirement.get("left") or ()),
                               "failed": str(self.retirement.get("failed") or "")}}


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
              skipped: list[str] | tuple[str, ...] = (), safety: str = "",
              retire_safety: str = "", retire_failed: str = "") -> None:
    """Record the run's state for `root`. Best effort: derived data, and a
    status that cannot be written must not stop the migration it describes.
    `retire_safety` is the `pre-retirement-` archive the pass took or reused,
    and `retire_failed` why the last retirement pass stopped short."""
    note = {"root": str(root), "running": running, "pid": os.getpid(),
            "failed": failed, "skipped": list(skipped), "safety": safety,
            "retire_safety": retire_safety, "retire_failed": retire_failed}
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


def _kept_safety(note: dict, key: str = "safety",
                prefix: str = backups.SAFETY_PREFIX) -> str:
    """The safety archive an earlier run on this root took (`key` in its note,
    named under `prefix`), when it is still in the backup directory; "" when
    there is none to reuse. Retirement's is `_kept_retire_safety`."""
    name = note.get(key)
    if (not isinstance(name, str) or not name.startswith(prefix)
            or Path(name).name != name):
        return ""
    try:
        return name if (backups.backup_dir() / name).is_file() else ""
    except OSError:
        return ""


def _kept_retire_safety(note: dict) -> str:
    """The `pre-retirement-` archive an earlier pass on this root took, when
    it is still there (R2-3): reused, so a resumed pass takes no second one."""
    return _kept_safety(note, "retire_safety", backups.RETIRE_PREFIX)


def _note_retire_failed(root: Path, failed: str) -> None:
    """Record `failed` as why retirement stopped short on `root`, keeping the
    rest of the note -- unless the note already says it."""
    note = _recall(root)
    if _note_text(note, "retire_failed") == failed:
        return
    skipped = [s for s in note.get("skipped") or () if isinstance(s, str)]
    _remember(root, running=False, failed=_note_text(note, "failed"), skipped=skipped,
              safety=_kept_safety(note), retire_safety=_kept_retire_safety(note),
              retire_failed=failed)


def _retirement(failed: str) -> dict:
    """`Status.retirement`: what `retire.left()` says now, and `failed`."""
    return {"left": list(retire.left()), "failed": failed}


def _note_text(note: dict, key: str) -> str:
    value = note.get(key)
    return value if isinstance(value, str) else ""


# ---- reading the store ----
def _config_exists(root: Path) -> bool:
    return (root / "config.md").exists()


#: C's strict record read, moved to `store/frontmatter.py` (slice I) so
#: retirement -- which this module calls, and so cannot import -- shares it.
#: Bound here too: `migrate.RecordUnreadableError` is what callers catch.
RecordUnreadableError = frontmatter.RecordUnreadableError


def _config_record(root: Path) -> dict[str, str]:
    """`config.md`'s raw frontmatter; `RecordUnreadableError` (or the read's
    own `OSError`/`UnicodeDecodeError`) when it cannot be read as a record."""
    return frontmatter.read_record(root / "config.md", "config.md")[0]


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
            meta, _ = frontmatter.read_record(campaign_paths.campaign_meta_path(cid),
                                              f"campaign {cid}'s campaign.md")
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
    # Retirement's account rides beside the state and never moves it.
    retirement = _retirement(_note_text(note, "retire_failed"))
    # Done first: what the store says outranks a note that can outlive a crash.
    if keys.is_current(cfg) and False not in marks.values():
        return Status("done", "", skipped, retirement)
    if _is_running(root, note):
        return Status("running", "", skipped, retirement)
    failed = _note_text(note, "failed")
    return Status("failed" if failed else "pending", failed, skipped, retirement)


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

    def halted(self) -> str:
        """Why the run must stop before its next item; "" to go on."""
        if self.stop is not None and self.stop.is_set():
            return STOPPED
        if paths.home() != self.root:
            return MOVED
        return ""

    def remember(self, *, running: bool, failed: str = "") -> None:
        _remember(self.root, running=running, failed=failed, skipped=self.skipped,
                  safety=self.safety, retire_safety=self.retire_safety,
                  retire_failed=self.retire_failed)


def _run(root: Path, stop: threading.Event | None) -> Status | None:
    """The run, under the lock: None when the store's own status says how it
    ended, else the Status to report (a failure, a stop, a root that moved)."""
    if not _config_exists(root):
        return None                        # a fresh store is never migrated
    # A `config.md` that holds no record is not a legacy store with nothing
    # set: switched, it would be stamped current over an empty layout and its
    # legacy keys never read again once they arrive. Nothing is written --
    # the connection seeding below would write it too -- and the next start
    # retries.
    try:
        _config_record(root)
    except (OSError, UnicodeDecodeError) as exc:
        reason = f"the upgrade stopped: {exc}"
        note = _recall(root)
        _remember(root, running=False, failed=reason, safety=_kept_safety(note),
                  retire_safety=_kept_retire_safety(note),
                  retire_failed=_note_text(note, "retire_failed"))
        return Status("failed", reason, (), _retirement(_note_text(note, "retire_failed")))
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
    idle, planned = _idle(root, current, left)
    if idle:
        return None
    note = _recall(root)
    run = _Run(root, stop, _kept_safety(note), _unreadable_skips(unreadable),
               _kept_retire_safety(note))
    run.remember(running=True)
    outcome: Status | None = None
    try:
        outcome = _steps(run, current, left, planned)
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
    if outcome is None:
        return None
    return dataclasses.replace(outcome, skipped=tuple(run.skipped),
                               retirement=_retirement(run.retire_failed))


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
        _note_retire_failed(root, "; ".join(planned.dropped))
        return True, planned
    return False, planned


def _steps(run: _Run, current: bool, left: list[str],
           planned: retire.PassPlan | None = None) -> Status | None:
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
        marks, _ = _campaign_marks()
        left = [cid for cid, marked in marks.items() if not marked]
    for cid in left:
        if why := run.halted():
            return Status("pending", why)
        try:
            _campaign_step(cid, run.skipped)
        except config.NewerFormatError:
            # A newer build switched the store while the loop ran: what is
            # left is not this build's to mark (spec 11.3).
            return Status("newer")
    _retire(run, planned)
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
        run.created_safety = True
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
        _config_record(run.root)           # a placeholder fails the run
        early = config.read_config()
        if keys.is_newer(early):
            return Status("newer")         # not this build's to go on with
        if keys.is_current(early):
            return None                    # switched meanwhile, by another device
        if why := _facts(run):
            return Status("pending", why)
        with locks.config_lock():
            if why := run.halted():
                return Status("pending", why)
            _config_record(run.root)
            cfg = config.read_config()
            if keys.is_newer(cfg):
                return Status("newer")
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
       `pre-retirement-grimoire-` archive is taken before the pass's first
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
        made = backups.create_backup(prefix=backups.RETIRE_PREFIX)
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
