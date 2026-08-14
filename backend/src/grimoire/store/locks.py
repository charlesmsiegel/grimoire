"""Per-campaign serialization: the app's one general-purpose lock domain.

A campaign is the unit of mutual exclusion in grimoire. Everything that
reads-validates-writes campaign-scoped state — scene transcripts, sheets,
audit baselines, roll proposals, the roll log, and the module-pack swap that
can invalidate most of them — serializes on the *same* ``campaign_lock(cid)``.
That unification is deliberate: a module edit holding a campaign's lock must
exclude a proposal derived from the pack it is about to replace, so the two
cannot live in separate lock domains. **This list is the domain**: a
campaign-scoped mutator that keeps a private registry instead is invisible
here and silently outside the exclusion (#255 — ``rolls`` was, until it
joined).

That sentence used to be the whole mechanism, and it is why this file had to
admit four mutators sitting outside the domain with nothing to stop a fifth: a
paragraph cannot fail a test run. The domain is now *declared* below, in
``DOMAIN_MODULES`` / ``OUTSIDE_DOMAIN`` / ``UNREVIEWED``, and
``tests/test_lock_domain_guard.py`` holds those declarations and the code to
each other. The prose here explains the design; the constants are what binds.

Who takes it:

- every ``scenes`` mutator (``scenes._serialized``), ``create_scene``
  included — a scene file is
  rewritten whole, so two unlocked read-modify-writes lose one of them, and
  a transcript is the one thing here that cannot be regenerated (#254);
- every campaign-sheet mutator (``sheets.write``, ``write_creation``,
  ``delete``, ``set_field``, ``advance``);
- ``audit.capture_baseline`` / ``audit.apply_delta``;
- every ``proposals`` state transition, and the routes that wrap a whole
  derive-and-persist span (``routes/streaming.py`` proposal finalizers) so a proposal
  cannot be derived from a pack that is swapped away before it lands;
- every ``rolls`` mutator (``append``, ``find_or_append_by_proposal``,
  ``repoint_scenes``) plus its ``find_by_proposal`` reader — a logged roll
  can carry the id of the proposal it resolved, and proposals are in this
  domain, so the two belong in one (#255);
- ``playing.mark_greeting``'s whole read-scan-write and the recheck-and-mark
  inside ``playing.start_from_greeting``. This closes #318's orphan-clearing
  race outright: ``mark_greeting``'s scan for a stamping scene now shares a
  lock with ``stamp_greeting``, so it can no longer find nothing and clear
  the mark while a concurrent start is mid-flight. The other #318 risk --
  two concurrent starts of the same greeting -- is narrowed, not closed:
  both callers still cast actors, stamp their scene and append the greeting
  body unlocked (the calendar-touching macro expansion in between cannot run
  under this lock, see ``playing.start_from_greeting``), so a loser's scene
  keeps that content even though only one caller now wins the mark and the
  other gets a clean, single-witness ``PlayError``. Before this, both
  callers could win the mark; that half is what changed. ``mark_greeting``'s
  hold spans ``stamping_scene``'s whole sweep -- two frontmatter reads per
  scene -- which blocks every other campaign writer, including an in-flight
  turn's ``append_reply``, for the scan's duration; correctness needs the
  scan inside the lock, so there is no cheaper version, but a large campaign
  on a slow or synced volume could approach ``LOCK_TIMEOUT``;
- ``module_edit`` publication and the world-module rebind route, the only
  actors that hold *every* campaign's lock at once, across the swap;
  ``PUT /campaigns/{cid}/module`` holds just that campaign's;
- read paths that must not observe a half-published pack:
  ``checks.resolve_check``, ``context._mechanics``,
  ``routes.mechanics._continuation_rule_bodies``.

Ordering rules (deadlock avoidance):

    module-edit lock (``module_edit._M``)
      └─ campaign locks, always in sorted cid order  (``hold_all``)
           ├─ audit baseline lock   (``store/audit/baselines.py``)
           └─ rolls lock            (``store/rolls.py``)

- **Every multi-campaign holder goes through** ``hold_all``, which sorts.
  Nothing else may hold more than one campaign lock at a time. The two that
  exist (module publication, the world-module rebind route) did **not** agree
  before ``hold_all``, though this file used to say they did: the route sorted
  by cid and module publication took ``list_campaigns()`` order, which is
  recency (#267). No LLM play flow ever holds more than its own campaign's
  lock.

  Checked twice, and the second one is what makes the rule above binding on
  code nobody has written yet. Per holder, behaviourally:
  ``test_locks_store.py`` spies on the registry and asserts the order each of
  the two actually asks for. Then structurally:
  ``test_lock_order_guard.py`` walks this package's ASTs and fails on the
  *shapes* that hold two campaign locks at once -- an acquisition registered on
  an ``ExitStack``, one carried around a loop, two nested for different
  campaigns -- anywhere but ``hold_all``. A third holder written tomorrow fails
  on arrival rather than deadlocking in production, which is the gap #267 was
  filed about. Add one anyway and you route it through ``hold_all`` and add its
  behavioural test beside those two; what the guard cannot see is a lock
  reached through an alias or a wrapper object, and two locks taken on either
  side of a function call.
- campaign lock -> audit baseline lock, never reversed
  (``store/audit/baselines.py``).

The lock is an ``RLock`` so a caller can compose lower-level mutators —
``audit.apply_delta`` calls ``sheets.set_field`` under an already-held lock —
and it is *additionally* an OS advisory file lock (``store/proclock.py``), so
it excludes concurrent **processes** and not merely threads (#234).

Three limits on that, all deliberate:

- **Not across devices.** The lock file is machine-local, so a lock held on
  one device is invisible to another sharing the store through a synced
  folder. No filesystem lock on a sync-replicated store can do better; the
  sync client resolves simultaneous edits with conflict copies on its own
  schedule. Detecting that after the fact (compare-and-swap on a content
  digest) is a separate, larger change.
- **Not across OS users**, whose lock directories differ.
- **Not every campaign mutation** — only the ones that take this lock. Which
  those are is no longer a claim in this paragraph: see ``DOMAIN_MODULES`` and
  the two lists of what sits outside it, below.

Contention raises ``StoreBusy`` after ``LOCK_TIMEOUT``; one handler in
``main.create_app`` turns that into HTTP 409.

Spec: docs/superpowers/specs/2026-07-28-cross-process-campaign-locks-design.md
"""

from __future__ import annotations

import threading
import time
from contextlib import ExitStack, contextmanager

from . import paths, proclock

_registry_guard = threading.Lock()

# --- the lock domain, declared ----------------------------------------------
#
# Enforced by `tests/test_lock_domain_guard.py`, which walks the package's ASTs
# and works out for itself which modules mutate campaign-scoped state and which
# of those serialize. Every such module must appear in exactly one of the three
# names below, so a newly-written mutator cannot land outside the exclusion
# without somebody saying so in code -- the #255 failure, where `rolls` ran a
# private lock registry and no test could tell.
#
# The unit is a module rather than a function because that is the granularity a
# reader reasons about ("are appearances serialized?"), and because a module's
# private helpers run under whatever their callers hold. A member module may
# still exempt one function with a `# lock-domain-ok: <reason>` comment; the
# guard caps how many of those exist.

# The unit is the module the mutators actually live in, so splitting a module
# into a package re-keys its entry onto the submodules that inherited its
# functions. That is a rename, not a reclassification: the guard re-derives the
# verdict from the code either way, and every function each old entry covered is
# still covered by the entries that replaced it.

#: Modules whose public campaign-scoped mutators all take `campaign_lock(cid)`.
DOMAIN_MODULES: frozenset[str] = frozenset({
    # Reroll alternates read the transcript, decide against it, and write a
    # sidecar beside it -- and `promote` then drives two `scenes` mutators. All
    # of that has to be one critical section with the scene writes it brackets,
    # or a concurrent reply lands between the decision and the swap.
    "store.alternates",
    # `provenance.record` is a read-modify-write of one whole file, so two
    # unserialized callers lose one of the two writes. Its only caller
    # (`absorb.apply.apply_edits`, under `PUT /chronicle`) already holds this
    # lock, and the lock is an RLock, so taking it inside `record` costs a
    # reentrant acquire and buys the module a place in the domain rather than
    # another entry on `UNREVIEWED`'s frozen backlog.
    "store.provenance",
    # The three `store.scenes` submodules that mutate. Each public mutator in
    # them either wears the package's `@locking._serialized` -- `campaign_lock`
    # around the whole body -- or delegates to a private one that does, which is
    # how `create_scene` and `set_datetime` resolve a calendar before the lock.
    "store.scenes.write",
    "store.scenes.moment",
    "store.scenes.lifecycle",
    # `commitments.json` is rewritten whole by `set_movement` and
    # `repoint_scenes`, exactly like `plot.json` -- but this module is new
    # (#115), so it starts inside the exclusion rather than joining the
    # `UNREVIEWED` backlog `plot` sits in.
    "store.commitments",
    # The campaign's cover image (`<campaign>/assets/cover.<ext>`). A new
    # module mutating campaign-scoped state, so it starts inside the exclusion
    # rather than joining the frozen `UNREVIEWED` backlog: `put_cover` and
    # `delete_cover` take the lock around the publish-then-clean sequence, and
    # `delete_cover` verifies the removal under it.
    "store.covers",
    # `journal.json` is rewritten whole by every append, and an append that
    # loses the race loses the only record of a write that already landed --
    # which is worse than a stale panel, because the reversal it carries is the
    # only way back. New module (#31), so it starts inside the exclusion rather
    # than joining the frozen `UNREVIEWED` backlog `changes` sits in.
    "store.journal",
    # `facts.json` the same (#114), and with one reason of its own on top of
    # the whole-file rewrite: `record` retires the superseded fact and writes
    # its replacement in one read-modify-write, so an unlocked pair can also
    # leave a fact retired by an id that never landed.
    "store.facts",
    # `prompts/index.json` is read-modify-written by every capture (allocate an
    # id, append a row, evict past the retention depth), so two concurrent turns
    # would otherwise lose one row and leak its payload. New module (#157), so
    # it starts inside the exclusion rather than joining the `UNREVIEWED`
    # backlog -- the same call `store.commitments` made.
    "store.prompt_log",
    # turnstate.json is rewritten whole by `record`, `repoint_scenes` and
    # `drop_scene`, exactly like commitments.json -- and it is written from
    # inside `_persist_reply`, which already holds this lock, so the entry that
    # files a reply's tracker block and the append that lands the reply are one
    # critical section rather than two.
    "store.turnstate",
    # scene_ideas.json is rewritten whole by `add`, `set_status` and
    # `repoint_scenes`, exactly like facts.json -- and `add` allocates the
    # idea's id from the keys it just read, so two unlocked saves can pick the
    # same slug and one of the two ideas is simply gone. New module (#88), so
    # it starts inside the exclusion rather than joining the `UNREVIEWED`
    # backlog -- the same call `store.commitments` and `store.facts` made.
    "store.scene_ideas",
    "store.sheets.tally",
    "store.sheets.writer",
    "store.audit.baselines",
    "store.proposals",
    "store.rolls",
    # `played.json` is read-modify-written by `mark_greeting` (scans every
    # scene for one stamping `gid` before clearing an orphaned mark) and by
    # `start_from_greeting`'s recheck-and-mark. Both were unlocked until
    # #318 made a played greeting unavailable and made a replay raise --
    # which is what turned "two racers can both pass the guard" from a
    # relabeling into a duplicate play, and what turned the clearing rule's
    # scan into a real TOCTOU against a start mid-flight. `start_from_
    # greeting` deliberately does NOT hold this lock across `context_
    # macros.expand_macros`, which resolves the campaign's calendar
    # provider and runs its (user-authored) code -- same reason `scenes.
    # lifecycle._date_hint` resolves its calendar before `_create_scene`'s
    # lock rather than inside it.
    "store.playing",
})

#: Modules deliberately outside the exclusion, with the reason. An entry here is
#: a decision someone made and can defend, not merely the status quo.
OUTSIDE_DOMAIN: dict[str, str] = {
    # One `store.campaigns` entry until the module became a package; the reason
    # below is that entry's, split across the three submodules its mutators
    # landed in. Nothing was reassessed and nothing changed hands.
    "store.campaigns.lifecycle": (
        "Known gap, not a considered exclusion -- recorded here so it stops "
        "being invisible. `rename_campaign` and `set_campaign_response` each "
        "read-modify-write the campaign meta file, so two concurrent ones lose "
        "an edit (a rename dropped by a `touch` that read the older "
        "frontmatter -- see `store.campaigns.read`). `delete_campaign` rmtrees "
        "a tree that a lock holder may be writing under -- "
        "`module_edit._campaign_locks` already records 'campaign deletion "
        "takes no lock at all' as a known limit of its all-campaign hold. "
        "`ensure_campaign_slim` rewrites that same meta file and is unlocked "
        "for the same reason as the two above. Fixing these is a concurrency "
        "change that needs its own review, which is why this guard classifies "
        "them rather than closing them."
    ),
    "store.campaigns.read": (
        "`touch` read-modify-writes the campaign meta file unlocked, so it can "
        "drop a concurrent `rename_campaign` or `set_campaign_response` by "
        "publishing frontmatter it read before that edit landed. It runs from "
        "`appearances` on every actor change (`transitions.appear`, "
        "`versions.pick_version`, `versions.import_version`), which is what "
        "makes the overlap reachable. Same known gap as "
        "`store.campaigns.lifecycle`, whose mutators it races."
    ),
    "store.campaigns.paths": (
        "`write_manifest` republishes the whole campaign manifest from a dict "
        "its callers read a moment earlier -- `overlay`, `sync`, `migrations` "
        "and `appearances.versions` all read-modify-write it that way -- so "
        "two concurrent callers lose one of the two edits. It was inside the "
        "single `store.campaigns` entry before the split, covered by that "
        "module-level declaration without being named in its prose; it is "
        "named here rather than promoted, because nothing about it was "
        "reviewed."
    ),
}

#: Modules that mutate campaign-scoped state without serializing and have never
#: been assessed for lost-update risk. Not an endorsement -- a frozen backlog.
#: The guard forbids it from GROWING, so this list can only shrink as modules
#: are examined and moved into one of the two above. Anything new mutating
#: campaign state fails the guard until it is classified.
#: Two entries are re-keyed rather than new (see the note above DOMAIN_MODULES):
#: `store.appearances` became a package and its mutators landed in `paths`,
#: `transitions` and `versions`; `store.modules` became a package and
#: `set_campaign_module` landed in `binding`. Same functions, same absence of a
#: review -- the backlog did not grow, it was spelled out at the granularity the
#: code now has.
UNREVIEWED: frozenset[str] = frozenset({
    "store.appearances.paths",         # was store.appearances
    "store.appearances.transitions",   # was store.appearances
    "store.appearances.versions",      # was store.appearances
    "store.assets",
    "store.campaign_climate",
    "store.changes",
    "store.characters",
    "store.chronicle",
    "store.commits",
    "store.dossiers",
    "store.modules.binding",           # was store.modules
    "store.overlay",
    "store.playstate",
    "store.plot",
    "store.relationships",
    "store.sync",
    "store.taglines",
    "store.weather.overrides",
})

# Longer than any legitimate hold, but bounded so a cross-process lock-order
# inversion surfaces as a 409 naming the campaign rather than a wedged server.
# Not a proof: a module migration over a large library on a synced or removable
# filesystem can still exceed it, and its waiter then gets a retryable 409.
LOCK_TIMEOUT = 30.0


class StoreBusy(Exception):
    """Another *process* holds a store lock. One handler maps this to HTTP 409."""

    def __init__(self, name: str, what: str = "resource"):
        super().__init__(f"another grimoire process is editing this {what}")
        self.name = name


class CampaignBusy(StoreBusy):
    def __init__(self, cid: str):
        super().__init__(cid, "campaign")


class ModuleEditBusy(StoreBusy):
    def __init__(self, name: str = "module-edit"):
        super().__init__(name, "module library")


class ConfigBusy(StoreBusy):
    def __init__(self, name: str = "config"):
        super().__init__(name, "configuration")


def _remaining(deadline) -> float:
    """RLock treats -1 as "no timeout" and rejects every other negative, so a
    computed remainder must be clamped rather than passed through."""
    if deadline is None:
        return -1
    return max(0.0, deadline - time.monotonic())


class _ProcessScopedLock:
    """A ``threading.RLock`` plus an OS advisory file lock (#234).

    The file lock is taken on the OUTERMOST acquisition only and released on
    the outermost release, so reentrancy touches the filesystem once -- which
    ``audit.apply_delta`` -> ``sheets.set_field``, ``scenes._serialized``
    nesting under a holder, and ``module_edit._apply`` -> ``recover()`` all
    depend on.

    ``acquire`` keeps ``RLock``'s contract exactly (returns a bool, never
    raises ``StoreBusy``); ``__enter__`` is the layer that raises. The timeout
    is a deadline for the WHOLE acquisition -- time spent on the thread lock is
    subtracted from the file lock's budget -- and it bounds lock *contention*
    only: ``realpath``/``mkdir``/``open`` on an unavailable path are blocking
    syscalls no userspace deadline can interrupt.
    """

    def __init__(self, domain: str, name: str, busy: type[StoreBusy]):
        self._domain = domain
        self._name = name
        self._busy = busy
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None

    def _path(self):
        # Resolved per outermost acquisition, never cached: home() resolves
        # live on every call and the Storage location can change at runtime.
        # The domain keeps a campaign id from ever colliding with the
        # module-edit lock -- see proclock.lock_path.
        return proclock.lock_path(paths.home(), self._domain, self._name)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if timeout != -1 and timeout < 0:   # RLock parity: -1 is the only
            raise ValueError(               # legal negative, not "any negative"
                "timeout value must be a positive number")
        if not blocking:
            if timeout != -1:
                raise ValueError("can't specify a timeout for a non-blocking call")
            ok = self._rlock.acquire(False)
            deadline = proclock.NO_WAIT     # ONE attempt; None would retry forever
        else:
            deadline = time.monotonic() + timeout if timeout >= 0 else None
            ok = self._rlock.acquire(True, _remaining(deadline))
        if not ok:
            return False
        fd = None
        try:
            if self._depth > 0:             # we already hold the file lock
                self._depth += 1
                return True
            fd = proclock.acquire(self._path(), deadline)
            if fd is None:
                self._rlock.release()
                return False
            self._fd = fd                   # installed BEFORE depth becomes 1
            fd = None                       # ownership transferred to self
            self._depth = 1
            return True
        except BaseException:
            # Three windows, and the handler has to tell them apart. `depth`
            # is the discriminator because it is the thing `release()` keys on.
            if self._depth > 0:
                # We had already established (or incremented) the hold when the
                # exception landed -- an asynchronous one, after `depth = 1` or
                # after the reentrant `depth += 1`. The object is *consistent*
                # here, so unwind it the normal way. Doing what the two
                # branches below do instead would release the thread lock while
                # leaving depth > 0 and the fd installed: the lock would then
                # claim to be held with no thread owning it, and the next
                # acquire would see depth > 0 and skip the file lock entirely,
                # silently dropping cross-process exclusion. Worse than a leak.
                self.release()
            else:
                # Either the file lock was taken but never installed (local
                # `fd`), or it was installed and the exception landed before
                # depth reached 1 (`self._fd` set with depth 0, which
                # `release()` would never clean up). Both leak for the life of
                # the process if not reclaimed here.
                if fd is None and self._fd is not None:
                    fd, self._fd = self._fd, None
                if fd is not None:
                    proclock.release(fd)
                self._rlock.release()       # never strand the thread lock
            raise

    def release(self) -> None:
        if not self._rlock._is_owned():     # ownership BEFORE state, so a
            raise RuntimeError(             # non-owner corrupts nothing
                "cannot release un-acquired lock")
        if self._depth > 1:
            self._depth -= 1
            self._rlock.release()
            return
        fd, self._fd = self._fd, None
        try:
            if fd is not None:
                proclock.release(fd)
        finally:                            # even if the unlock raises
            self._depth = 0
            self._rlock.release()

    def __enter__(self):
        if not self.acquire(timeout=LOCK_TIMEOUT):
            raise self._busy(self._name)
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False

    def _is_owned(self) -> bool:
        """RLock parity: two sheets tests assert `modules.resolve` runs inside
        the lock by calling this."""
        return self._rlock._is_owned()


_campaign_locks: dict[str, _ProcessScopedLock] = {}


def campaign_lock(cid: str) -> _ProcessScopedLock:
    """Get-or-create the per-campaign lock atomically -- a plain
    ``if cid not in _campaign_locks: ...`` is a check-then-act race that can
    hand two concurrent first-ever callers different lock objects.

    Keyed by cid alone: two *different* stores' campaigns sharing a cid share
    one lock object, which is over-serialization inside one process and never a
    correctness hole, while the lock *file* is per store.
    """
    with _registry_guard:
        lock = _campaign_locks.get(cid)
        if lock is None:
            lock = _campaign_locks[cid] = _ProcessScopedLock("campaign", cid, CampaignBusy)
        return lock


@contextmanager
def campaign_lock_nowait(cid: str):
    """``campaign_lock`` that never waits: yields whether it got the lock.

    For work that belongs in the domain but must not delay the caller when the
    campaign is busy. ``campaign_lock`` waits up to ``LOCK_TIMEOUT`` (30s), which
    is right for a mutation that has to happen and wrong for one that can simply
    be skipped -- ``store.prompt_log.record`` runs on the generating path, before
    the route returns its streaming response, so waiting there would stall a turn
    for half a minute and then discard the debug snapshot anyway.

    **The caller must honour the boolean.** A body that writes regardless has
    taken no lock at all; this yields False rather than raising precisely so the
    decision is the caller's, and `test_lock_domain_guard.py` counts a `with`
    over this as serialization without being able to check that.

    Reentrant like the underlying RLock: a thread that already holds this
    campaign's lock always gets True.
    """
    lock = campaign_lock(cid)
    got = lock.acquire(blocking=False)
    try:
        yield got
    finally:
        if got:
            lock.release()


@contextmanager
def best_effort_campaign_lock(cid: str, timeout: float = 2.0):
    """Hold the campaign lock if it can be had quickly; otherwise proceed
    without it. Yields whether it was actually held.

    For READ paths whose only stake in the lock is seeing two files in a
    consistent state. `campaign_lock` is the wrong tool there: it raises
    ``StoreBusy`` after ``LOCK_TIMEOUT``, and a reader that can 409 turns a
    nicety into a new way for a turn to fail. `context._assemble` is the case —
    it pairs the transcript with the transient-state ledger, and `post_chat`
    has already appended the player's post by the time it runs, with the undo
    that would take it back off not yet wired. A timeout there would strand
    that post with no reply and nothing able to remove it.

    So the trade is stated rather than inherited: under contention this returns
    an unlocked read, which can observe one writer's two files a moment apart.
    That costs one prompt a stale field. Refusing would cost the turn.

    `acquire` returns a bool and never raises (it keeps ``RLock``'s contract);
    only ``__enter__`` raises, which is exactly the layer being avoided.
    """
    lock = campaign_lock(cid)
    held = lock.acquire(timeout=timeout)
    try:
        yield held
    finally:
        if held:
            lock.release()


_config = _ProcessScopedLock("domain", "config", ConfigBusy)


def config_lock() -> _ProcessScopedLock:
    """The global `config.md` lock.

    `write_config` is a read-merge-write of one file, so two of them lose one
    update — and the writers are not all user-initiated: `_setup_state` on
    `GET /api/config` backfills `setup_done`, which means merely loading the
    app in a second tab can race a setting being saved in the first (#194
    review). Cross-process for the same reason the campaign locks are: the
    file belongs to the store, not to one server.

    It is a leaf. Nothing under this lock takes another, so it has no place in
    the ordering rules above and cannot participate in a cycle.
    """
    return _config


_module_edit = _ProcessScopedLock("domain", "module-edit", ModuleEditBusy)


def module_edit_lock() -> _ProcessScopedLock:
    """The global module-edit lock. Cross-process for the same reason the
    campaign locks are: whole-directory pack publication mutates the shared
    user library, not just one campaign."""
    return _module_edit


@contextmanager
def hold_all(cids):
    """Hold every named campaign lock, in sorted order, under ONE deadline.

    **Sorted order.** The two multi-campaign holders -- ``module_edit.
    _campaign_locks`` and the world-module rebind route -- did NOT agree before
    this function existed, though an earlier version of this docstring said
    they did and called the agreement accidental. ``list_campaigns()`` does
    walk ``sorted(base.iterdir())``, but it ends on
    ``out.sort(key=updated, reverse=True)`` (``campaigns/read.py``), so what it
    returns is recency order; the rebind route sorts by cid. Whenever those two
    orders disagree -- the ordinary case, since ids are slugs and ``updated`` is
    a clock -- the two holders acquire the same locks in opposite orders, and
    two concurrent requests wedge permanently on each other (#267). Both
    endpoints are plain ``def``, so FastAPI runs them in the threadpool and
    they are genuinely concurrent.

    So this is the fix rather than a formality: it is the ONLY place that
    sorts, and no other caller may hold more than one campaign lock.

    That last sentence used to be a rule and not a guarantee, and the
    difference is the whole of #267: the second holder was written without
    knowledge of the first, each carrying a comment calling itself the only
    one, and the only checks were two per-holder tests in
    ``test_locks_store.py`` -- one for each holder that already existed.
    ``test_lock_order_guard.py`` is what now stands between this docstring and
    a third: it reads the package's ASTs and fails any function outside here
    that can hold two campaign locks at once, so the rule is enforced on code
    rather than on whoever reads this. Its reach is stated in its own
    docstring, and it stops short of a lock reached through an alias and of two
    locks taken across a call boundary -- so if you are adding a holder, route
    it through here and add its behavioural test beside those two.

    **One deadline**, not one per lock: applying ``LOCK_TIMEOUT`` to each of N
    locks while holding the earlier ones would give an N x LOCK_TIMEOUT convoy.

    ``ExitStack`` rather than a hand-rolled reversed loop: it registers each
    lock the instant it is acquired, and it runs EVERY registered exit even
    when one of them raises. ``for lock in reversed(held): lock.release()``
    strands every remaining lock the moment one release fails.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    with ExitStack() as stack:
        for cid in sorted(set(cids)):
            lock = campaign_lock(cid)
            if not lock.acquire(timeout=_remaining(deadline)):
                raise CampaignBusy(cid)
            stack.callback(lock.release)
        yield
