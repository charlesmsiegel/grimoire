"""Characterize the writer barriers before separating mutation from its outcome.

The ordinary-kind barrier, sheet barrier and membership recovery barrier differ
on purpose. These cases pin their order and observable mutations, alongside the
voice-drift checks missing from the broader absorb characterization run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.store import campaigns, characters, overlay, voice_drift, worlds
from grimoire.store.absorb import apply as absorb_apply
from tests import frozen_copy


@pytest.fixture
def voice_campaign(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[str, Path, str]:
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    world = worlds.world_root(worlds.create_world("Realm"))
    characters.create_character(world, "Seraphine", "default", characters.blank_card("Seraphine"))
    cid = campaigns.create_campaign("Saltmarch", world.name)
    overlay.set_voice_anchor(cid, "seraphine", "Clipped.")
    anchor = overlay.voice_anchor_record(cid, "seraphine")
    fingerprint = voice_drift.anchor_fingerprint(anchor["text"], anchor["id"])
    root = campaigns.campaign_root(cid)
    voice_drift.write(root, "seraphine", "She hedged.", fingerprint)
    return cid, root, fingerprint


@pytest.mark.parametrize("edit_changes,payload_changes,state,reason,note", [
    ({}, {}, "applied", "", "She used contractions."),
    ({"after": ""}, {"op": "clear"}, "applied", "", ""),
    ({"target": {"kind": "pcs", "id": "seraphine"}}, {}, "skipped", "", "She hedged."),
    ({"after": ""}, {}, "failed", "cannot be blank", "She hedged."),
    ({"after": "x" * (voice_drift.MAX_NOTE + 1)}, {}, "failed", "cannot be longer", "She hedged."),
    ({"target": {"kind": "characters", "id": "mara"}}, {}, "failed", "no longer exists", "She hedged."),
    ({}, {"anchor": ""}, "failed", "does not record which anchor", "She hedged."),
    ({}, {"anchor": "0" * 64}, "failed", "voice anchor changed", "She hedged."),
    ({"before": "Earlier note."}, {}, "failed", "finding changed", "She hedged."),
    ({}, {"before_anchor": "0" * 64}, "failed", "re-confirmed", "She hedged."),
])
def test_voice_writer_preserves_guards_and_clear_intent(
    voice_campaign: tuple[str, Path, str], edit_changes: dict, payload_changes: dict,
    state: str, reason: str, note: str,
) -> None:
    cid, root, fingerprint = voice_campaign
    edit = {"id": "voice_drift:seraphine", "kind": "voice_drift",
            "target": {"kind": "characters", "id": "seraphine"}, "field": "voice_drift",
            "before": "She hedged.", "after": "She used contractions.",
            "payload": {"op": "raise", "anchor": fingerprint, "before_anchor": fingerprint,
                        **payload_changes}, **edit_changes}
    outcome = absorb_apply._apply_one(cid, root, edit, None)
    assert outcome["state"] == state
    assert reason in outcome.get("reason", "")
    assert voice_drift.read(root, "seraphine") == note


@pytest.mark.parametrize("attempted,error,expected", [
    (False, OSError("disk"), {"state": "failed", "id": "membership:mara", "kind": "error", "reason": "disk"}),
    (True, OSError("disk"), {"state": "pending", "id": "membership:mara"}),
    (False, ValueError("identity"), {"state": "failed", "id": "membership:mara", "kind": "conflict", "reason": "identity"}),
])
def test_membership_failure_keeps_attempt_mutation_and_recovery_barrier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, attempted: bool,
    error: Exception, expected: dict,
) -> None:
    snapshots = []
    monkeypatch.setattr(absorb_apply.undo_store, "snapshot", lambda *args: snapshots.append(args))

    def fail(cid, edit, neutral, *, sid, undo_store):
        edit["_membership_attempted"] = attempted
        raise error

    monkeypatch.setattr(absorb_apply.membership, "apply", fail)
    edit = {"id": "membership:mara", "kind": "membership"}
    assert absorb_apply._apply_one("saltmarch", tmp_path, edit, None) == expected
    assert edit == {"id": "membership:mara", "kind": "membership", "_membership_attempted": attempted}
    assert snapshots == []


def test_unexpected_sheet_error_keeps_its_existing_exception_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """An ordinary broad catch must not absorb an unexpected sheet failure."""
    calls = []
    monkeypatch.setattr(absorb_apply.undo_store, "snapshot", lambda *args: calls.append(args))

    def fail(*args):
        raise RuntimeError("unexpected sheet failure")

    monkeypatch.setattr(absorb_apply.audit_apply, "apply_delta", fail)
    edit = {"id": "sheet:mara", "kind": "sheet"}
    with pytest.raises(RuntimeError, match="unexpected sheet failure"):
        absorb_apply._apply_one("saltmarch", tmp_path, edit, "001--saltmarch")
    assert calls == []


def test_unknown_ordinary_kind_still_probes_undo_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls = []

    def snapshot(cid, edit):
        calls.append((cid, dict(edit)))
        return None, None

    monkeypatch.setattr(absorb_apply.undo_store, "snapshot", snapshot)
    edit = {"id": "unknown", "kind": "unknown", "target": {}}
    assert absorb_apply._apply_one("saltmarch", tmp_path, edit, None) == {
        "state": "failed", "id": "unknown", "kind": "error",
        "reason": "this campaign does not know how to apply a 'unknown' change"}
    assert calls == [("saltmarch", edit)]


@pytest.mark.parametrize("actor,expected", [
    ("seraphine", characters.VersionNotFound),
    ("mara", characters.CharacterNotFound),
])
@pytest.mark.parametrize("writing", [False, True])
def test_nullable_locked_version_keeps_character_before_version_validation(
    voice_campaign: tuple[str, Path, str], actor: str, expected: type[Exception], writing: bool,
) -> None:
    """An unlocked card read/write rejects None through the existing validator."""
    root = worlds.world_root("realm")
    before = frozen_copy.digest(root)
    with pytest.raises(expected):
        if writing:
            characters.update_version(root, actor, None, characters.blank_card("Seraphine"))
        else:
            characters.read_card(root, actor, None)
    assert frozen_copy.digest(root) == before
