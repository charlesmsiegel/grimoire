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

  Checked per holder, not structurally: ``test_locks_store.py`` spies on the
  registry and asserts the order each of the two actually asks for. A THIRD
  holder written tomorrow is checked by nothing, which is the gap #267 was
  filed about and this paragraph does not close. Adding one means adding its
  test beside those two.
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
    # `facts.json` the same (#114), and with one reason of its own on top of
    # the whole-file rewrite: `record` retires the superseded fact and writes
    # its replacement in one read-modify-write, so an unlocked pair can also
    # leave a fact retired by an id that never landed.
    "store.facts",
    "store.sheets.tally",
    "store.sheets.writer",
    "store.audit.baselines",
    "store.proposals",
    "store.rolls",
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
        "for the same reason as the two above -- and its "
        "`_prune_untracked_copies` sweep adds a shape the rest of this entry "
        "does not cover: it DELETES campaign files on the strength of their "
        "absence from a manifest snapshot taken earlier in the same call, so a "
        "materialization landing in that window looks like residue and loses "
        "its copy. The writer's next step then raises rather than corrupting "
        "anything (the sweep only takes copies still byte-identical to the "
        "world, so nothing the user has edited is at risk), but it is a "
        "delete-under-a-concurrent-writer, not merely a lost edit (#270 "
        "review). Fixing these is a concurrency change that needs its own "
        "review, which is why this guard classifies them rather than closing "
        "them."
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
    "store.playing",
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

    That last sentence is a rule, not a guarantee, and the difference is worth
    stating because getting it wrong is the whole of #267. What enforces it is
    two per-holder tests in ``test_locks_store.py``, one for each holder that
    exists today. Nothing checks a holder nobody has written yet -- and #267
    happened precisely because the second holder was written without knowledge
    of the first, each carrying a comment calling itself the only one. If you
    are adding a third, route it through here and add its test beside those
    two; do not trust this paragraph to have stopped you.

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
