"""World Change Sets (043): review and apply an atomic two-record world change, and refuse a stale one."""

from __future__ import annotations

import json
import threading

import pytest

from grimoire.changesets import ChangeSet, ChangeSetError
from grimoire.changesetstore import ChangeSetConflict
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, RecordEnvelope, WriteDenied
from grimoire.store.errors import AuthorityUnavailable
from grimoire.testing.fileops import snapshot

from changeset_fixtures import (
    DOCKS, GUILD, HALL, MANIFEST, WREN, hand_write, library, move_the_guild, record_path, seed_world,
)
from store_helpers import HARBOR, OWNER, owner_handle


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_world(opened)
        yield opened


def changed_files(before: dict, after: dict) -> set[str]:
    """Files added, removed or rewritten (directories are not state)."""
    return {path for path in before.keys() | after.keys()
            if before.get(path) != after.get(path) and (before.get(path) or after.get(path)) is not None}


# -- the proving scenario ---------------------------------------------------------------------------

def test_preview_then_apply_an_atomic_two_record_world_change(store):
    lib = library(store)
    review = lib.review(move_the_guild(lib))

    assert review.applicable and not review.conflicts and not review.needs_approval
    assert [(item.action, item.path) for item in review.files] == [
        ("create", record_path(DOCKS.at("r1"))),
        ("revise", record_path(GUILD.at("r2"))),
        ("manifest", MANIFEST),
    ]
    assert [item.summary for item in review.files] == [
        "create location/saltmarch-docks (Saltmarch Docks) as r1",
        "revise organization/ashen-guild (Ashen Guild) from r1 to r2",
        "update the heads of world:harbor",
    ]
    guild = review.files[1]
    # the revision is diffed against the head it replaces, so the reviewer sees the one changed field
    assert guild.old_path == record_path(GUILD.at("r1"))
    assert '-    "seat": "world:harbor/location/old-hall"\n' in guild.diff
    assert '+    "seat": "world:harbor/location/saltmarch-docks"\n' in guild.diff
    assert '"motto"' not in "".join(line for line in guild.diff.splitlines(True) if line[:1] in "+-"
                                    and not line.startswith(("+++", "---")))
    assert '+    "location/saltmarch-docks": "r1",\n' in review.files[2].diff
    assert '+    "organization/ashen-guild": "r2"\n' in review.files[2].diff

    before = snapshot(store.root)
    applied = lib.apply(review)
    after = snapshot(store.root)

    # exactly the reviewed bytes, and no unrelated file
    assert changed_files(before, after) == set(review.preview())
    assert all(after[path] == data for path, data in review.preview().items())
    assert applied.revisions == (DOCKS.at("r1"), GUILD.at("r2"))
    assert review.state == "applied"
    head = store.check_definition(GUILD)
    assert head.ref == GUILD.at("r2") and head.envelope.payload["seat"] == str(DOCKS)
    assert store.check_definition(DOCKS).envelope.name == "Saltmarch Docks"
    assert after[record_path(GUILD.at("r1"))] == before[record_path(GUILD.at("r1"))]  # revisions are kept


def test_a_revision_keeps_its_base_owner_and_unknown_envelope_keys(store):
    lib = library(store)
    lib.apply(lib.review(move_the_guild(lib)))
    written = json.loads((store.root / record_path(GUILD.at("r2"))).read_text("utf-8"))
    assert written["owner"] == "human:author" and written["x-review"] == {"checked": True}
    created = json.loads((store.root / record_path(DOCKS.at("r1"))).read_text("utf-8"))
    assert created["owner"] == "human:owner"


def test_a_revision_of_an_unowned_record_stays_unowned(store):
    # the change set's author is attribution for what it creates, never a new owner of what it revises
    lib = library(store)
    hall = lib.revise(HARBOR, "location", "old-hall", {"traits": {"spatial": {}}, "ruined": True})
    review = lib.review(ChangeSet("cs-hall", HARBOR, "The hall burns", "human:someone", (hall,)))
    assert "owner" not in review.files[0].diff
    lib.apply(review)
    assert "owner" not in json.loads((store.root / record_path(HALL.at("r2"))).read_text("utf-8"))


# -- an external edit between proposal and acceptance -----------------------------------------------

def test_a_hand_edit_of_a_target_aborts_and_writes_nothing(store):
    lib = library(store)
    review = lib.review(move_the_guild(lib))
    path = record_path(GUILD.at("r1"))
    hand_write(store.root, path, (store.root / path).read_bytes().replace(b"ash remembers", b"ash forgets"))
    before = snapshot(store.root)

    with pytest.raises(ChangeSetConflict) as caught:
        lib.apply(review)
    assert [(c.target, c.reason) for c in caught.value.conflicts] == [("organization/ashen-guild", "base_edited")]
    assert caught.value.needs_redraft
    assert snapshot(store.root) == before
    with pytest.raises(ChangeSetError) as again:
        lib.apply(review)
    assert again.value.reason == "already_decided"


def test_a_head_moved_by_another_change_set_is_a_conflict(store):
    lib = library(store)
    first, second = lib.review(move_the_guild(lib, "cs-1")), lib.review(move_the_guild(lib, "cs-2"))
    lib.apply(first)
    before = snapshot(store.root)
    with pytest.raises(ChangeSetConflict) as caught:
        lib.apply(second)
    assert {(c.target, c.reason) for c in caught.value.conflicts} == {
        ("location/saltmarch-docks", "exists"), ("organization/ashen-guild", "head_moved"),
        ("manifest", "manifest_changed")}
    assert snapshot(store.root) == before


def test_an_unrelated_manifest_change_asks_for_a_fresh_review_not_a_redraft(store):
    lib = library(store)
    changeset = move_the_guild(lib)
    stale = lib.review(changeset)
    wren = lib.revise(HARBOR, "character", "wren", {"bio": "a sailor, now harbourmaster"})
    lib.apply(lib.review(ChangeSet("cs-wren", HARBOR, "Promote Wren", "human:owner", (wren,))))

    with pytest.raises(ChangeSetConflict) as caught:
        lib.apply(stale)
    assert [(c.target, c.reason) for c in caught.value.conflicts] == [("manifest", "manifest_changed")]
    assert not caught.value.needs_redraft

    fresh = lib.review(changeset)  # the same proposal, reviewed against today's manifest
    assert fresh.applicable
    lib.apply(fresh)
    assert store.check_definition(WREN).ref == WREN.at("r2")  # the other change is kept
    assert store.check_definition(GUILD).ref == GUILD.at("r2")


def test_a_pinned_record_that_changes_aborts_the_change_set(store):
    lib = library(store)
    changeset = move_the_guild(lib)
    pinned = ChangeSet(changeset.changeset_id, HARBOR, changeset.title, changeset.author, changeset.changes,
                       preconditions=(lib.pin(HARBOR, "location", "old-hall"),))
    review = lib.review(pinned)
    path = record_path(HALL.at("r1"))
    hand_write(store.root, path, (store.root / path).read_bytes().replace(b"Old Hall", b"Burned Hall"))
    with pytest.raises(ChangeSetConflict) as caught:
        lib.apply(review)
    assert [(c.target, c.reason) for c in caught.value.conflicts] == [("location/old-hall", "base_edited")]


def test_review_reports_conflicts_instead_of_raising(store):
    lib = library(store)
    lib.apply(lib.review(move_the_guild(lib, "cs-1")))
    review = lib.review(move_the_guild(lib, "cs-2"))  # drafted again: the guild now revises r2
    assert not review.applicable
    assert [(c.target, c.reason) for c in review.conflicts] == [("location/saltmarch-docks", "exists")]
    with pytest.raises(ChangeSetConflict):
        lib.apply(review)


def test_creating_an_id_another_kind_holds_is_a_conflict(store):
    lib = library(store)
    review = lib.review(ChangeSet("cs", HARBOR, "Wren the place", "human:owner",
                                  (lib.create(HARBOR, "location", "wren", {}),)))
    assert [(c.target, c.reason) for c in review.conflicts] == [("location/wren", "entity_id_taken")]


# -- drafting -----------------------------------------------------------------------------------------

def test_revise_follows_the_head_and_takes_the_next_free_revision(store):
    txn = store.transaction()  # an unheaded r7 left by hand: the next revision must not reuse it
    txn.put(RecordEnvelope(WREN.at("r7"), {"bio": "a draft"}, name="Wren"), None)
    txn.commit(owner_handle())
    lib = library(store)
    change = lib.revise(HARBOR, "character", "wren", {"bio": "x"})
    assert (change.base, change.revision, change.name) == ("r1", "r8", "Wren")
    renamed = lib.revise(HARBOR, "character", "wren", {"bio": "x"}, name=None)
    assert renamed.name is None


@pytest.mark.parametrize("call, reason", [
    (lambda lib: lib.revise("world:nowhere", "character", "wren", {}), "missing_source"),
    (lambda lib: lib.revise(HARBOR, "character", "nobody", {}), "missing"),
    (lambda lib: lib.review(ChangeSet("cs", "world:nowhere", "t", "human:owner",
                                      (lib.create("world:nowhere", "location", "x", {}),))), "missing_source"),
])
def test_drafting_and_review_refusals(store, call, reason):
    with pytest.raises(ChangeSetError) as caught:
        call(library(store))
    assert caught.value.reason == reason


# -- authority: approvals bound to the reviewed bytes ---------------------------------------------------

WORKER = "app:world-author"


def worker_authority() -> Authorizer:
    authority = Authorizer([OWNER])
    harbor = ResourcePath.parse("sources/world/harbor")
    authority.add_grant(Grant("g-owner", OWNER, frozenset({Permission.APPLY}), harbor, OWNER))
    authority.add_grant(Grant("g-worker", WORKER, frozenset({Permission.APPLY}), harbor, OWNER))
    return authority


def ids():
    counter = iter(range(1, 1000))
    return lambda: f"ap-{next(counter)}"


def test_an_untrusted_writer_needs_a_human_approval_of_the_exact_bytes(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for(WORKER))
    review = lib.review(move_the_guild(lib))
    assert review.applicable and review.needs_approval == tuple(review.preview())
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        lib.apply(review)
    assert snapshot(store.root) == before and review.state == "open"

    shown = review.describe_approvals(OWNER)
    assert sorted(shown) == sorted(review.preview())
    assert all(text.startswith(f"{OWNER} approved {WORKER} to apply sha256:") and text.endswith(f" at {path}")
               for path, text in shown.items())
    approvals = lib.approve(review, authority, approved_by=OWNER, new_id=ids())
    assert sorted(approvals) == sorted(review.preview())
    assert {a.payload_digest for a in authority.pending_approvals()} == {f.digest for f in review.files}
    lib.apply(review, approvals)
    assert store.check_definition(GUILD).ref == GUILD.at("r2")
    assert authority.pending_approvals() == ()


def test_approvals_for_one_review_do_not_apply_another(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for(WORKER))
    approved = lib.review(move_the_guild(lib, "cs-1"))
    approvals = lib.approve(approved, authority, approved_by=OWNER, new_id=ids())
    other = move_the_guild(lib, "cs-2")
    other = ChangeSet("cs-2", HARBOR, other.title, other.author,
                      (other.changes[0], lib.revise(HARBOR, "organization", "ashen-guild", {"seat": "elsewhere"})))
    different = lib.review(other)
    before = snapshot(store.root)
    with pytest.raises(WriteDenied):
        lib.apply(different, approvals)  # same paths, other bytes: the digests do not match
    assert snapshot(store.root) == before


def test_reject_writes_nothing_and_withdraws_its_approvals(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for(WORKER))
    review = lib.review(move_the_guild(lib))
    lib.approve(review, authority, approved_by=OWNER, new_id=ids())
    before = snapshot(store.root)
    lib.reject(review)
    assert review.state == "rejected" and authority.pending_approvals() == ()
    assert snapshot(store.root) == before
    with pytest.raises(ChangeSetError) as caught:
        lib.approve(review, authority, approved_by=OWNER, new_id=ids())
    assert caught.value.reason == "already_decided"


def test_only_a_human_can_approve_and_a_conflicted_review_cannot_be(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for(WORKER))
    review = lib.review(move_the_guild(lib))
    with pytest.raises(ValueError):
        lib.approve(review, authority, approved_by=WORKER, new_id=ids())  # Approval refuses a non-human
    assert authority.pending_approvals() == ()
    lib2 = library(store)
    lib2.apply(lib2.review(move_the_guild(lib2, "cs-9")))
    conflicted = lib.review(move_the_guild(lib, "cs-10"))
    with pytest.raises(ChangeSetError) as caught:
        lib.approve(conflicted, authority, approved_by=OWNER, new_id=ids())
    assert caught.value.reason == "not_applicable"


def test_a_writer_without_a_grant_is_shown_the_refusal_and_cannot_be_approved(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for("app:stranger"))
    review = lib.review(move_the_guild(lib))
    assert {(check.allowed, check.reason) for check in review.permissions} == {(False, "not_granted")}
    with pytest.raises(ChangeSetError) as caught:
        lib.approve(review, authority, approved_by=OWNER, new_id=ids())
    assert caught.value.reason == "not_applicable" and authority.pending_approvals() == ()
    with pytest.raises(WriteDenied):
        lib.apply(review)


def test_a_trusted_owner_needs_no_approval_and_gets_none(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for(OWNER, trusted=True))
    review = lib.review(move_the_guild(lib))
    assert lib.approve(review, authority, approved_by=OWNER, new_id=ids()) == {}
    assert review.describe_approvals(OWNER) == {} and authority.pending_approvals() == ()
    lib.apply(review)


def test_a_review_blocked_by_an_imprecise_number_is_reported_not_raised(store):
    # a decimal a double cannot hold, written by hand: rewriting the manifest would round it
    path = store.root / MANIFEST
    path.write_bytes(path.read_bytes().replace(b'"x-notes"', b'"x-scale": 1e-400,\n  "x-notes"'))
    lib = library(store)
    review = lib.review(move_the_guild(lib))
    assert not review.applicable and review.blocked and "ImpreciseNumber" in review.blocked[0]


def test_a_failed_apply_withdraws_its_approvals(store):
    # the "review again" path must not leave approvals behind that another transaction could use
    authority = worker_authority()
    lib = library(store, authority.handle_for(WORKER))
    stale = lib.review(move_the_guild(lib))
    lib.approve(stale, authority, approved_by=OWNER, new_id=ids())
    owner_lib = library(store)
    wren = owner_lib.revise(HARBOR, "character", "wren", {"bio": "harbourmaster"})
    owner_lib.apply(owner_lib.review(ChangeSet("cs-wren", HARBOR, "Promote Wren", "human:owner", (wren,))))

    with pytest.raises(ChangeSetConflict):
        lib.apply(stale)
    assert stale.state == "failed" and authority.pending_approvals() == ()
    lib.reject(stale)  # a failed review may still be rejected; nothing is left to withdraw
    assert stale.state == "rejected"


def test_a_reject_while_an_apply_waits_for_the_lock_wins(store):
    lib = library(store)
    review = lib.review(move_the_guild(lib))
    before = snapshot(store.root)
    outcome = []

    def apply():
        try:
            lib.apply(review)
            outcome.append("applied")
        except ChangeSetError as error:
            outcome.append(error.reason)

    with store.lock:  # another writer holds the library: the apply waits, the reject decides first
        worker = threading.Thread(target=apply)
        worker.start()
        lib.reject(review)
    worker.join(timeout=30)
    assert outcome == ["already_decided"] and review.state == "rejected"
    assert snapshot(store.root) == before


def test_content_the_store_cannot_encode_is_refused_at_review(store):
    lib = library(store)
    huge = lib.create(HARBOR, "location", "vault", {"n": 10 ** 5000})
    with pytest.raises(ChangeSetError) as caught:
        lib.review(ChangeSet("cs-vault", HARBOR, "A vault", "human:owner", (huge,)))
    assert caught.value.reason == "unstorable"


class FlakyAuthority:
    """An authority whose ledger breaks on its second registration and stays broken until healed."""

    def __init__(self, inner: Authorizer) -> None:
        self.inner, self.registered, self.broken = inner, 0, False

    def register_approval(self, approval) -> None:
        self.registered += 1
        if self.registered == 2:
            self.broken = True
            raise OSError("ledger append failed")
        self.inner.register_approval(approval)

    def pending_approvals(self):
        if self.broken:
            raise OSError("authority unavailable")
        return self.inner.pending_approvals()

    def revoke_approval(self, approval_id: str) -> None:
        self.inner.revoke_approval(approval_id)


def test_an_approval_whose_rollback_fails_is_still_withdrawn_before_reject_succeeds(store):
    inner = worker_authority()
    lib = library(store, inner.handle_for(WORKER))
    review = lib.review(move_the_guild(lib))
    flaky = FlakyAuthority(inner)
    with pytest.raises(OSError):
        lib.approve(review, flaky, approved_by=OWNER, new_id=ids())
    assert len(inner.pending_approvals()) == 1  # registered, and the rollback could not reach the authority
    with pytest.raises(OSError):
        lib.reject(review)  # cannot report success while that approval is still usable
    assert review.state == "open"
    flaky.broken = False
    lib.reject(review)
    assert review.state == "rejected" and inner.pending_approvals() == ()


class PoisonedOnRevoke:
    """Like a durable authority whose ledger write fails while revoking: memory says revoked,
    the durable ledger does not, and from then on it is not usable."""

    def __init__(self, inner: Authorizer) -> None:
        self.inner, self.usable = inner, True

    def register_approval(self, approval) -> None:
        self.inner.register_approval(approval)

    def pending_approvals(self):
        return self.inner.pending_approvals()

    def revoke_approval(self, approval_id: str) -> None:
        self.inner.revoke_approval(approval_id)  # in memory only
        self.usable = False
        raise OSError("ledger append failed")


def test_a_withdrawal_an_unusable_authority_cannot_confirm_never_counts(store):
    inner = worker_authority()
    lib = library(store, inner.handle_for(WORKER))
    review = lib.review(move_the_guild(lib))
    authority = PoisonedOnRevoke(inner)
    lib.approve(review, authority, approved_by=OWNER, new_id=ids())
    for _ in range(2):  # its pending list no longer shows the approval, but nothing durable says so
        with pytest.raises((OSError, AuthorityUnavailable)):
            lib.reject(review)
    assert review.state == "open"
    lib.reject(review, inner)  # the reopened authority finishes the withdrawal
    assert review.state == "rejected" and inner.pending_approvals() == ()


def test_approving_again_replaces_the_earlier_approvals(store):
    authority = worker_authority()
    lib = library(store, authority.handle_for(WORKER))
    review = lib.review(move_the_guild(lib))
    first = lib.approve(review, authority, approved_by=OWNER, new_id=ids())
    second = lib.approve(review, authority, approved_by=OWNER, new_id=lambda: f"again-{len(authority.pending_approvals())}")
    assert {a.approval_id for a in authority.pending_approvals()} == set(second.values())
    with pytest.raises(WriteDenied):
        lib.apply(review, first)  # withdrawn: no longer authorizes anything
    lib.apply(review, second)
    assert review.state == "applied" and authority.pending_approvals() == ()
