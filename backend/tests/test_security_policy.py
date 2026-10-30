"""Grants and approvals are explicit, human-issued and readable."""

import hashlib
import itertools

import pytest

from grimoire.security import (
    Approval, Grant, Permission, ResourcePath, is_valid_principal, payload_digest,
)

HARBOR = ResourcePath.parse("worlds/harbor")
WREN = ResourcePath.parse("worlds/harbor/characters/wren")
DIGEST = payload_digest(b"wren card")


@pytest.mark.parametrize("value", ["card-import", "human:owner", "app:importer", "a" * 64])
def test_valid_principals(value):
    assert is_valid_principal(value)


@pytest.mark.parametrize("value", [
    "", "Card", "x y", "x‮", "card-import\n", "-card", "a" * 65, None, 7,
    "card may apply in worlds (granted by human:owner)",
])
def test_invalid_principals(value):
    assert not is_valid_principal(value)


def test_payload_digest_format():
    assert payload_digest(b"x") == "sha256:" + hashlib.sha256(b"x").hexdigest()
    with pytest.raises(TypeError):
        payload_digest("x")  # type: ignore[arg-type]


@pytest.mark.parametrize("kwargs", [
    {"permissions": frozenset()},
    {"permissions": frozenset({"apply"})},
    {"permissions": {Permission.APPLY}},
    {"scope": ResourcePath.parse("secrets/provider-key")},
    {"scope": "worlds/harbor"},
    {"principal": "Card Import"},
    {"granted_by": "card-import"},
    {"granted_by": "app:importer"},
    {"grant_id": ""},
])
def test_grant_rejects_invalid_values(kwargs):
    base = dict(grant_id="g1", principal="card-import",
                permissions=frozenset({Permission.INSPECT}), scope=HARBOR,
                granted_by="human:owner")
    with pytest.raises(ValueError):
        Grant(**{**base, **kwargs})


def test_grant_describe_has_fixed_permission_order():
    for order in itertools.permutations(Permission):
        grant = Grant("g1", "card-import", frozenset(order), HARBOR, "human:owner")
        assert grant.describe() == (
            "card-import may inspect, propose, apply, execute in worlds/harbor "
            "(granted by human:owner)")


def test_grant_covers_is_scope_and_permission_exact():
    grant = Grant("g1", "card-import", frozenset({Permission.PROPOSE}), HARBOR, "human:owner")
    assert grant.covers("card-import", Permission.PROPOSE, WREN)
    assert not grant.covers("card-import", Permission.APPLY, WREN)
    assert not grant.covers("other", Permission.PROPOSE, WREN)
    assert not grant.covers("card-import", Permission.PROPOSE, ResourcePath.parse("worlds/harbor2"))


@pytest.mark.parametrize("kwargs", [
    {"permission": Permission.INSPECT},
    {"permission": Permission.PROPOSE},
    {"permission": "apply"},
    {"payload_digest": "sha256:ABC"},
    {"payload_digest": "sha256:" + "A" * 64},
    {"payload_digest": DIGEST[7:]},
    {"approved_by": "card-import"},
    {"resource": ResourcePath.parse("secrets/provider-key")},
    {"resource": "worlds/harbor/characters/wren"},
    {"principal": "Card"},
])
def test_approval_rejects_invalid_values(kwargs):
    base = dict(approval_id="a1", principal="card-import", permission=Permission.APPLY,
                resource=WREN, payload_digest=DIGEST, approved_by="human:owner")
    with pytest.raises(ValueError):
        Approval(**{**base, **kwargs})


def test_approval_describe():
    approval = Approval("a1", "card-import", Permission.APPLY, WREN, DIGEST, "human:owner")
    assert approval.describe() == (
        f"human:owner approved card-import to apply {DIGEST[:19]}… "
        "at worlds/harbor/characters/wren")


def test_non_ascii_addresses_are_flagged_for_humans():
    lookalike = ResourcePath.parse("worlds/harbor/characters/mаra")
    grant = Grant("g1", "card-import", frozenset({Permission.APPLY}), lookalike, "human:owner")
    approval = Approval("a1", "card-import", Permission.APPLY, lookalike, DIGEST, "human:owner")
    assert grant.describe().endswith(" [non-ASCII address]")
    assert approval.describe().endswith(" [non-ASCII address]")
    assert "[non-ASCII" not in Grant("g2", "card-import", frozenset({Permission.APPLY}),
                                     HARBOR, "human:owner").describe()
