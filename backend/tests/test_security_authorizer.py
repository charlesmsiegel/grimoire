"""Authorizer decisions: deny by default, approvals bound and single-use."""

import inspect

import pytest

import grimoire.security.authorizer as authorizer_module
from grimoire.security import (
    Approval, Authorizer, Grant, Permission, Reason, ResourcePath, payload_digest,
)

OWNER = "human:owner"
WORKER = "card-import"
HARBOR = ResourcePath.parse("worlds/harbor")
WREN = "worlds/harbor/characters/wren"
PAYLOAD = b'{"name": "Wren"}'


def harbor_grant(grant_id="g-harbor", principal=WORKER, granted_by=OWNER,
                 permissions=(Permission.INSPECT, Permission.PROPOSE, Permission.APPLY)):
    return Grant(grant_id, principal, frozenset(permissions), HARBOR, granted_by)


def wren_approval(approval_id="a-wren", payload=PAYLOAD, approved_by=OWNER,
                  permission=Permission.APPLY, principal=WORKER):
    return Approval(approval_id, principal, permission, ResourcePath.parse(WREN),
                    payload_digest(payload), approved_by)


@pytest.fixture
def authority():
    authority = Authorizer([OWNER, "human:other"])
    authority.add_grant(harbor_grant())
    return authority


def test_handle_has_no_admin_surface(authority):
    handle = authority.handle_for(WORKER)
    assert {name for name in dir(handle) if not name.startswith("_")} == {
        "principal", "trusted", "authorize"}
    assert list(inspect.signature(handle.authorize).parameters) == [
        "permission", "resource", "payload", "approval_id"]
    with pytest.raises(AttributeError):
        handle.trusted = True  # type: ignore[misc]


def test_handles_are_untrusted_unless_human_or_app(authority):
    assert authority.handle_for(WORKER).trusted is False
    assert authority.handle_for("app:importer", trusted=True).trusted is True
    assert authority.handle_for(OWNER, trusted=True).trusted is True
    with pytest.raises(ValueError):
        authority.handle_for(WORKER, trusted=True)
    for bad in ("Card", "x\n", "", None):
        with pytest.raises(ValueError):
            authority.handle_for(bad)  # type: ignore[arg-type]


def test_owner_and_identifier_rules(authority):
    for owners in ([], ["card-import"], ["app:importer"]):
        with pytest.raises(ValueError):
            Authorizer(owners)
    with pytest.raises(ValueError):
        authority.add_grant(harbor_grant("g-x", granted_by="human:stranger"))
    with pytest.raises(ValueError):
        authority.add_grant(harbor_grant())  # duplicate id
    authority.revoke_grant("g-harbor")
    with pytest.raises(ValueError):
        authority.add_grant(harbor_grant())  # revoked ids are never reused
    with pytest.raises(TypeError):
        authority.add_grant("g-harbor")  # type: ignore[arg-type]


def test_approval_ids_are_never_reused(authority):
    authority.register_approval(wren_approval())
    assert authority.handle_for(WORKER).authorize(
        Permission.APPLY, WREN, PAYLOAD, "a-wren").reason is Reason.APPROVED
    with pytest.raises(ValueError):
        authority.register_approval(wren_approval())


def test_trusted_apply_needs_no_approval():
    authority = Authorizer([OWNER])
    authority.add_grant(harbor_grant(principal="app:importer"))
    decision = authority.handle_for("app:importer", trusted=True).authorize(
        Permission.APPLY, WREN, PAYLOAD)
    assert decision.allowed and decision.reason is Reason.GRANTED
    assert decision.grant_id == "g-harbor" and decision.approval_id is None


def test_untrusted_apply_requires_single_use_approval(authority):
    handle = authority.handle_for(WORKER)
    assert handle.authorize(Permission.APPLY, WREN, PAYLOAD).reason is Reason.APPROVAL_REQUIRED
    authority.register_approval(wren_approval())
    decision = handle.authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren")
    assert decision.allowed and decision.reason is Reason.APPROVED
    assert decision.approval_id == "a-wren" and decision.grant_id == "g-harbor"
    assert str(decision.resource) == WREN
    assert authority.pending_approvals() == ()
    replay = handle.authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren")
    assert not replay.allowed and replay.reason is Reason.APPROVAL_REQUIRED


def test_approval_binds_payload_resource_principal_and_permission():
    authority = Authorizer([OWNER])
    authority.add_grant(harbor_grant(permissions=tuple(Permission)))
    authority.add_grant(harbor_grant("g-twin", principal="twin-import",
                                     permissions=tuple(Permission)))
    authority.register_approval(wren_approval())
    worker, twin = authority.handle_for(WORKER), authority.handle_for("twin-import")
    attempts = [
        worker.authorize(Permission.APPLY, WREN, b"different bytes", "a-wren"),
        worker.authorize(Permission.APPLY, "worlds/harbor/characters/mara", PAYLOAD, "a-wren"),
        twin.authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren"),
        worker.authorize(Permission.EXECUTE, WREN, PAYLOAD, "a-wren"),
    ]
    assert [d.reason for d in attempts] == [Reason.APPROVAL_REQUIRED] * 4
    assert all(d.approval_id is None for d in attempts)
    assert [a.approval_id for a in authority.pending_approvals()] == ["a-wren"]


def test_approval_must_come_from_the_issuer_of_a_covering_grant(authority):
    with pytest.raises(ValueError):
        authority.register_approval(wren_approval(approved_by="human:other"))
    with pytest.raises(ValueError):
        authority.register_approval(wren_approval(principal="twin-import"))
    with pytest.raises(ValueError):
        authority.register_approval(wren_approval(permission=Permission.EXECUTE))
    assert authority.pending_approvals() == ()


def test_grant_revocation_permanently_kills_pinned_approvals(authority):
    authority.register_approval(wren_approval())
    authority.revoke_grant("g-harbor")
    handle = authority.handle_for(WORKER)
    assert handle.authorize(Permission.PROPOSE, WREN, PAYLOAD).reason is Reason.NOT_GRANTED
    assert authority.pending_approvals() == ()
    authority.add_grant(harbor_grant("g-harbor-2"))
    assert handle.authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren").reason is (
        Reason.APPROVAL_REQUIRED)


def test_revoking_an_approval_before_use(authority):
    authority.register_approval(wren_approval())
    authority.revoke_approval("a-wren")
    authority.revoke_approval("a-wren")  # idempotent
    assert authority.handle_for(WORKER).authorize(
        Permission.APPLY, WREN, PAYLOAD, "a-wren").reason is Reason.APPROVAL_REQUIRED
    with pytest.raises(KeyError):
        authority.revoke_approval("missing")
    with pytest.raises(KeyError):
        authority.revoke_grant("missing")


def test_trusted_handle_does_not_consume_approval():
    authority = Authorizer([OWNER])
    authority.add_grant(harbor_grant(principal="app:importer"))
    authority.register_approval(wren_approval(principal="app:importer"))
    decision = authority.handle_for("app:importer", trusted=True).authorize(
        Permission.APPLY, WREN, PAYLOAD, "a-wren")
    assert decision.reason is Reason.GRANTED and decision.approval_id is None
    assert [a.approval_id for a in authority.pending_approvals()] == ["a-wren"]


@pytest.mark.parametrize("args", [
    ("apply", WREN, PAYLOAD, None),
    (Permission.APPLY, ResourcePath.parse(WREN), PAYLOAD, None),
    (Permission.APPLY, None, PAYLOAD, None),
    (Permission.APPLY, WREN, "text", None),
    (Permission.APPLY, WREN, PAYLOAD, 5),
    (Permission.APPLY, WREN, None, None),
    (Permission.PROPOSE, WREN, None, None),
    (Permission.INSPECT, WREN, b"x", None),
    (None, None, None, None),
])
def test_malformed_requests_are_denied_not_raised(authority, args):
    decision = authority.handle_for(WORKER).authorize(*args)
    assert not decision.allowed and decision.reason is Reason.MALFORMED_REQUEST
    assert decision.resource is None


def test_internal_errors_deny_and_do_not_consume(authority, monkeypatch):
    authority.register_approval(wren_approval())

    def broken(payload):
        raise RuntimeError("boom")

    monkeypatch.setattr(authorizer_module, "payload_digest", broken)
    decision = authority.handle_for(WORKER).authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren")
    assert not decision.allowed and decision.reason is Reason.INTERNAL_ERROR
    assert authority.audit()[-1].reason is Reason.INTERNAL_ERROR
    assert [a.approval_id for a in authority.pending_approvals()] == ["a-wren"]


def test_default_denials(authority):
    stranger = authority.handle_for("stranger")
    worker = authority.handle_for(WORKER)
    assert stranger.authorize(Permission.INSPECT, WREN).reason is Reason.NOT_GRANTED
    assert worker.authorize(Permission.EXECUTE, "providers/openai", b"{}").reason is (
        Reason.NOT_GRANTED)
    assert worker.authorize(Permission.INSPECT, "secrets/provider-key").reason is (
        Reason.RESERVED_RESOURCE)
    assert worker.authorize(Permission.INSPECT, "worlds/harbor/../rival").reason is (
        Reason.INVALID_PATH)
    assert worker.authorize(Permission.INSPECT, "worlds/harbor2/x").reason is Reason.NOT_GRANTED


def test_earliest_added_covering_grant_is_reported(authority):
    authority.add_grant(Grant("g-world", WORKER, frozenset({Permission.INSPECT}),
                              ResourcePath.parse("worlds"), OWNER))
    decision = authority.handle_for(WORKER).authorize(Permission.INSPECT, WREN)
    assert decision.grant_id == "g-harbor"
    authority.revoke_grant("g-harbor")
    assert authority.handle_for(WORKER).authorize(Permission.INSPECT, WREN).grant_id == "g-world"


def test_audit_records_every_decision(authority):
    handle = authority.handle_for(WORKER)
    handle.authorize(Permission.INSPECT, WREN)
    handle.authorize(Permission.INSPECT, "Worlds/Harbor")
    handle.authorize("inspect", WREN)  # type: ignore[arg-type]
    records = authority.audit()
    assert [r.sequence for r in records] == [1, 2, 3]
    first, invalid, malformed = records
    assert (first.principal, first.permission, first.resource, first.trusted,
            first.allowed, first.reason, first.grant_id, first.approval_id) == (
        WORKER, Permission.INSPECT, WREN, False, True, Reason.GRANTED, "g-harbor", None)
    assert invalid.resource is None and invalid.reason is Reason.INVALID_PATH
    assert malformed.permission is None and malformed.reason is Reason.MALFORMED_REQUEST


def test_describe_policy_lists_grants_and_pending_approvals(authority):
    authority.register_approval(wren_approval())
    assert authority.describe_policy() == (
        "card-import may inspect, propose, apply in worlds/harbor (granted by human:owner)",
        f"human:owner approved card-import to apply {payload_digest(PAYLOAD)[:19]}\u2026 "
        "at worlds/harbor/characters/wren",
    )


def test_handle_origin_cannot_be_rewritten(authority):
    handle = authority.handle_for(WORKER)
    for name in ("_trusted", "_principal", "_authority", "principal"):
        with pytest.raises(AttributeError):
            setattr(handle, name, True)
        with pytest.raises(AttributeError):
            delattr(handle, name)
    assert handle.authorize(Permission.APPLY, WREN, PAYLOAD).reason is Reason.APPROVAL_REQUIRED


def test_string_subclass_approval_id_cannot_replay(authority):
    class Flip(str):
        calls = 0

        def __hash__(self):
            Flip.calls += 1
            return str.__hash__(self) if Flip.calls % 2 else 12345

    authority.register_approval(wren_approval())
    handle = authority.handle_for(WORKER)
    decision = handle.authorize(Permission.APPLY, WREN, PAYLOAD, Flip("a-wren"))
    assert decision.reason is Reason.MALFORMED_REQUEST
    assert handle.authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren").reason is Reason.APPROVED
    for _ in range(3):
        Flip.calls = 0
        replay = handle.authorize(Permission.APPLY, WREN, PAYLOAD, Flip("a-wren"))
        assert not replay.allowed


def test_subclassed_arguments_are_malformed(authority):
    class Address(str):
        def split(self, *args, **kwargs):
            return ["worlds", "harbor", "..", "..", "secrets", "provider-key"]

    class Payload(bytes):
        def decode(self, *args, **kwargs):
            return "something else"

    authority.register_approval(wren_approval())
    handle = authority.handle_for(WORKER)
    assert handle.authorize(Permission.INSPECT, Address("x")).reason is Reason.MALFORMED_REQUEST
    assert handle.authorize(Permission.APPLY, WREN, Payload(PAYLOAD), "a-wren").reason is (
        Reason.MALFORMED_REQUEST)
    assert handle.authorize(Permission.APPLY, WREN, bytearray(PAYLOAD), "a-wren").reason is (
        Reason.MALFORMED_REQUEST)
    assert [a.approval_id for a in authority.pending_approvals()] == ["a-wren"]


def test_unauditable_decisions_deny_and_do_not_consume(authority, monkeypatch):
    authority.register_approval(wren_approval())

    def broken(**fields):
        raise MemoryError

    monkeypatch.setattr(authorizer_module, "AuditRecord", broken)
    decision = authority.handle_for(WORKER).authorize(Permission.APPLY, WREN, PAYLOAD, "a-wren")
    assert not decision.allowed and decision.reason is Reason.INTERNAL_ERROR
    assert authority.audit() == ()
    assert [a.approval_id for a in authority.pending_approvals()] == ["a-wren"]
