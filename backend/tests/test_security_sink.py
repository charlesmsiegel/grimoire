"""Kernel hooks for durable authority: the decision sink, issued handles and decision replay."""

from __future__ import annotations

import dataclasses

import pytest

from grimoire.security import (
    Approval, AuditRecord, Authorizer, Grant, Permission, Reason, ResourcePath, payload_digest,
)

OWNER = "human:owner"
ADDRESS = "campaigns/c1/instances/character/x.json"


def _authority():
    authority = Authorizer([OWNER])
    authority.add_grant(Grant("g1", "app:importer", frozenset({Permission.APPLY, Permission.INSPECT}),
                              ResourcePath.parse("campaigns"), OWNER))
    authority.register_approval(Approval("a1", "app:importer", Permission.APPLY, ResourcePath.parse(ADDRESS),
                                         payload_digest(b"bytes"), OWNER))
    return authority


def test_the_sink_sees_each_decision_before_it_takes_effect():
    authority = _authority()
    seen = []

    def sink(record):
        seen.append((record, len(authority.audit()), [a.approval_id for a in authority.pending_approvals()]))
    authority.attach_sink(sink)
    decision = authority.handle_for("app:importer").authorize(Permission.APPLY, ADDRESS, b"bytes", "a1")
    assert decision.allowed
    record, audit_length, pending = seen[0]
    assert audit_length == 0 and pending == ["a1"]  # not yet appended, not yet consumed
    assert authority.audit() == (record,)


def test_a_failing_sink_denies_and_consumes_nothing_even_for_older_handles():
    authority = _authority()
    handle = authority.handle_for("app:importer")  # issued before the sink fails

    def sink(record):
        raise OSError("disk full")
    authority.attach_sink(sink)
    decision = handle.authorize(Permission.APPLY, ADDRESS, b"bytes", "a1")
    assert not decision.allowed and decision.reason is Reason.INTERNAL_ERROR
    assert authority.audit() == ()  # no record that is not durable
    assert [a.approval_id for a in authority.pending_approvals()] == ["a1"]


def test_a_sink_is_attached_once_and_issued_handles_are_recognised():
    authority, other = _authority(), _authority()
    authority.attach_sink(lambda record: None)
    with pytest.raises(ValueError):
        authority.attach_sink(lambda record: None)
    assert authority.issued(authority.handle_for("app:importer"))
    assert not authority.issued(other.handle_for("app:importer"))
    assert not authority.issued(object())


def test_authority_is_a_reserved_root():
    authority = Authorizer([OWNER])
    with pytest.raises(ValueError):
        Grant("g", "app:x", frozenset({Permission.APPLY}), ResourcePath.parse("authority"), OWNER)
    decision = authority.handle_for(OWNER, trusted=True).authorize(Permission.INSPECT, "authority/security")
    assert decision.reason is Reason.RESERVED_RESOURCE


def _recorded():
    """A real history: a grant-backed inspect, an approved apply, a denial, a malformed request."""
    authority = _authority()
    handle = authority.handle_for("app:importer")
    handle.authorize(Permission.INSPECT, "campaigns/c1")
    handle.authorize(Permission.APPLY, ADDRESS, b"bytes", "a1")
    handle.authorize(Permission.APPLY, ADDRESS, b"bytes", "a1")  # replay attempt: denied
    handle.authorize("apply", ADDRESS)  # malformed
    return authority


def _rebuilt(records):
    authority = _authority()
    for record in records:
        authority.replay_decision(record)
    return authority


def test_a_recorded_history_replays_to_the_same_state():
    original = _recorded()
    rebuilt = _rebuilt(original.audit())
    assert rebuilt.audit() == original.audit()
    assert rebuilt.pending_approvals() == original.pending_approvals() == ()
    denied = rebuilt.handle_for("app:importer").authorize(Permission.APPLY, ADDRESS, b"bytes", "a1")
    assert not denied.allowed  # a consumed approval stays consumed


@pytest.mark.parametrize("change", [
    {"sequence": 7}, {"trusted": "false"}, {"allowed": 1}, {"reason": "granted"},
    {"grant_id": "g9"}, {"approval_id": "a9"}, {"resource": "campaigns/c2/x.json"},
    {"permission": Permission.EXECUTE}, {"reason": Reason.GRANTED},
    {"trusted": True},
], ids=["sequence", "str-bool", "int-bool", "str-reason", "unknown-grant", "unknown-approval",
        "other-resource", "other-permission", "approval-without-approved", "trusted-with-approval"])
def test_replay_refuses_what_the_kernel_could_not_have_recorded(change):
    records = list(_recorded().audit())
    records[1] = dataclasses.replace(records[1], **change)
    with pytest.raises(ValueError):
        _rebuilt(records)


def test_replay_refuses_a_second_use_of_an_approval_and_denials_with_ids():
    records = list(_recorded().audit())
    with pytest.raises(ValueError):
        _rebuilt([records[0], records[1], dataclasses.replace(records[1], sequence=3)])
    with pytest.raises(ValueError):
        _rebuilt([dataclasses.replace(records[2], sequence=1, grant_id="g1")])
    with pytest.raises(ValueError):
        _rebuilt([AuditRecord(1, "app:importer", Permission.INSPECT, "campaigns/c1", False, False,
                              Reason.GRANTED, None, None)])


def test_internal_error_records_replay_as_plain_denials():
    record = AuditRecord(1, "app:importer", None, None, False, False, Reason.INTERNAL_ERROR, None, None)
    assert _rebuilt([record]).audit() == (record,)


def test_replay_is_refused_once_a_sink_is_attached():
    authority = _authority()
    authority.attach_sink(lambda record: None)
    with pytest.raises(ValueError):
        authority.replay_decision(_recorded().audit()[0])
