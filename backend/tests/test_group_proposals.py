"""Host leases name group identities before generation, never at save."""

import pytest

from grimoire.store import campaigns, group_proposals, group_refs, locks
from tests.test_commits_store import _campaign
from tests.test_group_membership_store import _enable_case_sensitive
from tests.test_locks_store import _probe


@pytest.fixture
def scope(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    root = campaigns.campaign_root(cid)
    world = campaigns.world_root_of(cid)
    identity = group_proposals.GroupIdentity(store=str(tmp_path.resolve()), campaign=str(root.resolve()),
        layer="world", world=str(world.resolve()))
    return cid, root, world, identity


def test_only_host_registration_mints_identity_and_same_capture_preserves_it(scope):
    cid, root, world, identity = scope
    with locks.world_lifecycle_lock(root.parent.parent, world):
        a = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
        b = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
    assert a.token == b.token
    assert group_proposals.validate(cid, root, "groups:salt-circle", a.token, identity).token == a.token
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(cid, root, "groups:realm", a.token, identity)


def test_retirement_prevents_same_slug_recreation(scope):
    cid, root, world, identity = scope
    with locks.world_lifecycle_lock(root.parent.parent, world):
        old = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
        group_proposals.retire(cid, root, {"groups:salt-circle"})
        new = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
    assert old.token != new.token
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(cid, root, "groups:salt-circle", old.token, identity)


def test_observed_binding_mismatch_is_durable_even_after_return(scope):
    cid, root, world, identity = scope
    with locks.world_lifecycle_lock(root.parent.parent, world):
        old = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
    other = group_proposals.GroupIdentity(store=identity.store, campaign=identity.campaign,
        layer="world", world=str(world.parent / "saltmarch"))
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(cid, root, "groups:salt-circle", old.token, other)
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(cid, root, "groups:salt-circle", old.token, identity)


def test_trusted_owned_copy_survives_world_retirement(scope):
    cid, root, world, identity = scope
    owned = group_proposals.GroupIdentity(store=identity.store, campaign=identity.campaign, layer="campaign", world=None)
    with locks.world_lifecycle_lock(root.parent.parent, world):
        lease = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
        group_proposals.copied(cid, root, "groups:salt-circle", lease.token, owned)
        group_proposals.retire_world(cid, root, str(world.resolve()))
    assert group_proposals.validate(cid, root, "groups:salt-circle", lease.token, owned).token == lease.token


@pytest.mark.parametrize("raw", ["{bad", "[]", '{"v":2,"groups":{}}', '{"v":1,"groups":{"groups:realm":null}}'])
def test_corrupt_lease_account_is_not_absence(scope, raw):
    cid, root, _world, identity = scope
    path = root / "membership_review_tokens.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(group_refs.GroupIdentityConflict):
        group_proposals.validate(cid, root, "groups:salt-circle", "mara", identity)
    assert path.read_text(encoding="utf-8") == raw


def test_barrier_pins_store_and_rejects_reverse_campaign_order(scope, monkeypatch):
    cid, root, world, _identity = scope
    barrier = locks.world_lifecycle_lock(root.parent.parent, world)
    before = barrier._path()
    monkeypatch.setattr(locks.paths, "home", lambda: root / "saltmarch")
    assert barrier._path() == before
    with locks.campaign_lock(cid), pytest.raises(RuntimeError, match="before campaign"), barrier:
        pytest.fail("reverse acquisition entered")


def test_barrier_excludes_another_process_and_reentrant_release_keeps_exclusion(scope):
    _cid, root, world, _identity = scope
    barrier = locks.world_lifecycle_lock(root.parent.parent, world)
    factory = f"world_lifecycle_lock(locks.paths.home(), locks.paths.home() / 'worlds' / {world.name!r})"
    with barrier:
        with barrier:
            pass
        assert _probe(root.parent.parent, factory, wait=0.1) == "BUSY"
    assert _probe(root.parent.parent, factory, wait=0.1) == "GOT"


def test_explicit_old_root_cannot_write_under_another_stores_campaign_lock(scope, monkeypatch):
    cid, root, world, identity = scope
    with locks.world_lifecycle_lock(root.parent.parent, world):
        lease = group_proposals.register(cid, root, {"groups:salt-circle": identity})["groups:salt-circle"]
    before = (root / "membership_review_tokens.json").read_bytes()
    monkeypatch.setenv("GRIMOIRE_HOME", str(root.parent.parent / "saltmarch"))
    with pytest.raises(group_refs.GroupIdentityConflict, match="store"):
        group_proposals.retire(cid, root, {"groups:salt-circle"})
    assert (root / "membership_review_tokens.json").read_bytes() == before
    assert lease.token


def test_barrier_refuses_reverse_world_actor_order(scope):
    _cid, root, world, _identity = scope
    barrier = locks.world_lifecycle_lock(root.parent.parent, world)
    with locks.world_actor_lock(world.name), pytest.raises(RuntimeError, match="world-actor"):
        barrier.acquire(False)


def test_distinct_native_case_store_roots_do_not_share_campaign_authority(monkeypatch, tmp_path):
    _enable_case_sensitive(tmp_path)
    a, b = tmp_path / "realm", tmp_path / "REALM"
    cid = _campaign(monkeypatch, a)
    root = campaigns.campaign_root(cid)
    owned = group_proposals.GroupIdentity(store=str(a.resolve()), campaign=str(root.resolve()),
        layer="campaign", world=None)
    _campaign(monkeypatch, b)
    assert not root.samefile(campaigns.campaign_root(cid))
    with pytest.raises(group_refs.GroupIdentityConflict, match="store"):
        group_proposals.register(cid, root, {"groups:salt-circle": owned})
    assert not (root / "membership_review_tokens.json").exists()


def test_a_lease_write_bumps_the_campaign_revision_and_a_reread_does_not(scope):
    """The absorb start is @computes_only, so the middleware stamps nothing for
    it: a capture that writes a lease must move the revision itself, and one
    that only re-reads the same lease must not."""
    from grimoire.store import revision
    cid, root, world, identity = scope
    before = revision.current(cid)
    with locks.world_lifecycle_lock(root.parent.parent, world):
        group_proposals.register(cid, root, {"groups:salt-circle": identity})
        written = revision.current(cid)
        group_proposals.register(cid, root, {"groups:salt-circle": identity})
    assert written != before
    assert revision.current(cid) == written
