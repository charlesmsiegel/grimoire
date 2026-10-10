"""`grimoire.wire`: the typed attempt (`Target`) and chain (`Chain`) that the
resolver builds beside the lowered connection dict (slice I, Task 7)."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import sys

import pytest

from grimoire import llm_usage, wire

from . import wire_kit


def test_a_target_never_prints_its_key():
    t = wire_kit.target(api_key="sk-test-secret")
    assert "sk-test-secret" not in repr(t)
    assert "sk-test-secret" not in repr(wire.Chain(t))
    assert "sk-test-secret" not in repr(wire.Chain(wire_kit.target(), t))
    assert t.api_key == "sk-test-secret"


def test_with_account_never_shares_a_block():
    t = wire_kit.target()
    u = t.with_account(decision_mode="structured")
    assert t.account.decision_mode == "" and u.account.decision_mode == "structured"
    assert u.account is not t.account
    # The rest of the block, and of the target, is carried over.
    assert u.account.billing == t.account.billing == "metered"
    assert dataclasses.replace(u, account=t.account) == t


def test_with_account_refuses_a_field_the_ledger_does_not_file():
    with pytest.raises(TypeError):
        wire_kit.target().with_account(colour="red")


def test_the_account_fields_are_the_ledgers():
    assert tuple(f.name for f in dataclasses.fields(wire.Account)) == llm_usage.ACCOUNT_FIELDS


def test_a_chain_stamps_both_targets():
    c = wire_kit.chain(wire_kit.target(), wire_kit.target(provider_id="spare"))
    stamped = c.with_account(operation="decide", role="decision")
    assert [t.account.operation for t in stamped.attempts] == ["decide", "decide"]
    assert [t.account.role for t in stamped.attempts] == ["decision", "decision"]
    assert [t.account.operation for t in c.attempts] == ["", ""]
    alone = wire_kit.chain(wire_kit.target()).with_account(operation="generate")
    assert alone.fallback is None and alone.primary.account.operation == "generate"


def test_alone_drops_only_the_fallback():
    primary = wire_kit.target(structured=True)
    c = wire_kit.chain(primary, wire_kit.target(provider_id="spare"))
    assert [t.provider_id for t in c.attempts] == ["openrouter", "spare"]
    assert c.alone() == wire.Chain(primary)
    assert c.alone().primary is primary
    assert c.alone().attempts == (primary,)


def test_without_sampling_drops_only_the_preset():
    sampled = wire.Sampling("warm", "Warm", "global", {"temperature": 1.1})
    t = wire_kit.target(sampling=sampled, structured=True)
    bare = t.without_sampling()
    assert bare.sampling == wire.Sampling()
    assert dataclasses.replace(bare, sampling=sampled) == t


def test_defaulted_targets_share_no_mutable_block():
    """Each default is built per target: `frozen` does not stop a write into
    `sampling.params`, so a shared one would reach every defaulted target."""
    a, b = wire.Target("a", "openrouter", "m"), wire.Target("b", "openrouter", "m")
    assert a.sampling == b.sampling and a.sampling is not b.sampling
    assert a.sampling.params is not b.sampling.params
    a.sampling.params["temperature"] = 0.5
    assert b.sampling.params == {} and wire.Target("c", "k", "m").sampling.params == {}
    assert a.account is not b.account


def test_a_label_is_the_name_else_the_id():
    assert wire_kit.target().label == "OpenRouter"
    assert wire_kit.target(provider_name="").label == "openrouter"


def test_every_value_is_frozen():
    t = wire_kit.target()
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.model = "vendor/other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        t.account.role = "primary"  # type: ignore[misc]


def test_wire_imports_nothing_from_the_package():
    """Standard library only (#239): the store builds these and the gateway
    sends them, so the module may import neither."""
    tree = ast.parse(inspect.getsource(wire))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import in wire.py: {ast.dump(node)}"
            imported.add((node.module or "").split(".")[0])
    assert imported, "the walk found no imports at all"
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# ---- 01i: the model's size ----
def test_a_target_built_by_hand_has_unknown_limits():
    from grimoire.store.inference import resolved
    unknown = wire.Limits(wire.Limit(None, "unknown"), wire.Limit(None, "unknown"))
    assert wire_kit.target().limits == unknown == wire.Limits()
    assert resolved.UNBUILT.limits == unknown
    assert resolved.Attempt("openrouter", "vendor/m", "").limits == unknown
    stated = wire.Limits(window=wire.Limit(8192, "user"))
    assert resolved.Attempt("p", "m", "", target=wire_kit.target(limits=stated)).limits is stated


def test_limits_are_frozen_and_name_a_known_source():
    limits = wire.Limits()
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.window = wire.Limit(1, "user")  # type: ignore[misc]
    assert limits.window.source in wire.LIMIT_SOURCES
    assert wire.LIMIT_SOURCES == ("user", "catalog", "unknown")
