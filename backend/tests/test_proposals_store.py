import pathlib
import threading

import pytest

from grimoire.store import campaigns, proposals, worlds


def _scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    return cid, "s1"


def test_new_get_roundtrip_and_unique_ids(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    r1 = proposals.new(cid, sid, {"check": "brawl"})
    assert r1["id"].startswith("pr-") and len(r1["id"]) == 35
    assert r1["status"] == "pending"
    assert proposals.get(cid, sid)["payload"] == {"check": "brawl"}
    r2 = proposals.new(cid, "s2", {"check": "stealth"})
    assert r2["id"] != r1["id"]


def test_new_supersedes_previous_pending(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    proposals.new(cid, sid, {"check": "brawl"})
    r2 = proposals.new(cid, sid, {"check": "stealth"})
    assert proposals.get(cid, sid)["id"] == r2["id"]


def test_claim_cas(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    assert proposals.claim(cid, sid, rec["id"]) is True
    assert proposals.get(cid, sid)["status"] == "resolving"
    assert proposals.claim(cid, sid, rec["id"]) is False      # not pending anymore
    assert proposals.claim(cid, sid, "pr-999999") is False    # wrong id


def test_claim_concurrent_single_winner(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    wins = []
    def racer():
        if proposals.claim(cid, sid, rec["id"]):
            wins.append(1)
    threads = [threading.Thread(target=racer) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1


def test_transition_cas_and_resolution(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    assert proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved",
                                {"tier": "success"}) is True
    got = proposals.get(cid, sid)
    assert got["status"] == "resolved" and got["resolution"]["tier"] == "success"
    # wrong expected state, wrong id: both lose without writing
    assert proposals.transition(cid, sid, rec["id"], ("pending",), "declined") is False
    assert proposals.transition(cid, sid, "pr-999999", ("resolved",), "narrated") is False
    assert proposals.get(cid, sid)["status"] == "resolved"


def test_supersede_during_resolve_wins(monkeypatch, tmp_path):
    """The critical race: a new send supersedes while an accept holds the
    claim — the commit CAS must lose and the record must stay superseded."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    assert proposals.claim(cid, sid, rec["id"]) is True
    proposals.supersede(cid, sid)                      # new send lands mid-resolve
    assert proposals.get(cid, sid)["status"] == "superseded"
    assert proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved",
                                {"tier": "success"}) is False
    assert proposals.get(cid, sid)["status"] == "superseded"
    assert proposals.get(cid, sid)["resolution"] is None


def test_supersede_covers_every_non_terminal_state(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    for status in ("pending", "resolving", "resolved", "declined"):
        rec = proposals.new(cid, sid, {})
        if status != "pending":
            proposals.claim(cid, sid, rec["id"])
            if status == "resolved":
                proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved")
            elif status == "declined":
                proposals.transition(cid, sid, rec["id"], ("resolving",), "pending")
                proposals.transition(cid, sid, rec["id"], ("pending",), "declined")
        proposals.supersede(cid, sid)
        assert proposals.get(cid, sid)["status"] == "superseded"
    # narrated is terminal: untouched
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved")
    proposals.transition(cid, sid, rec["id"], ("resolved",), "narrated")
    proposals.supersede(cid, sid)
    assert proposals.get(cid, sid)["status"] == "narrated"


def test_malformed_file_never_reuses_ids(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    old = proposals.new(cid, sid, {})
    (campaigns.campaign_root(cid) / "proposals.json").write_text("{nope", encoding="utf-8")
    assert proposals.get(cid, sid) is None
    fresh = proposals.new(cid, sid, {})
    assert fresh["id"] != old["id"]          # uuid ids: corruption can't re-mint


def test_commit_narration_atomicity(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved", {"tier": "success"})
    persisted = []
    assert proposals.commit_narration(cid, sid, rec["id"],
                                      lambda: persisted.append(1)) is True
    assert persisted == [1]
    assert proposals.get(cid, sid)["status"] == "narrated"


def test_commit_narration_without_scene_file_uses_intent_zero(monkeypatch, tmp_path):
    """Commit succeeds for a scene id with no scene file on disk — the
    atomicity test above already relies on this (sids in this file are pure
    proposals keys, never created via scenes.create_scene). The contract:
    a missing scene is an empty transcript, so narration_intent is recorded
    as 0 — "trim nothing on a retry", which is exactly right when no
    transcript exists yet."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved", {})
    persisted = []
    assert proposals.commit_narration(cid, sid, rec["id"],
                                      lambda: persisted.append(1)) is True
    assert persisted == [1]                  # persist ran exactly once
    got = proposals.get(cid, sid)
    assert got["status"] == "narrated"
    assert got["narration_intent"] == 0


def test_commit_narration_drops_after_supersede(monkeypatch, tmp_path):
    """The continuation-vs-supersede race: text streamed for a proposal that
    got superseded mid-stream must never persist."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved", {})
    proposals.supersede(cid, sid)            # a new send lands mid-stream
    persisted = []
    assert proposals.commit_narration(cid, sid, rec["id"],
                                      lambda: persisted.append(1)) is False
    assert persisted == []                   # nothing written
    assert proposals.get(cid, sid)["status"] == "superseded"


def test_update_resolution_persists_on_resolved_and_superseded(monkeypatch, tmp_path):
    """Projection metadata persists on a same-id record whether it is still
    resolved or already superseded, and never changes the record's status."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    pid = rec["id"]
    proposals.claim(cid, sid, pid)
    proposals.transition(cid, sid, pid, ("resolving",), "resolved", {"tier": "success"})

    # resolved: writes metadata, status unchanged
    assert proposals.update_resolution(cid, sid, pid, {"tier": "success", "roll_id": "r1"}) is True
    got = proposals.get(cid, sid)
    assert got["status"] == "resolved" and got["resolution"]["roll_id"] == "r1"

    # superseded (same id): still writes metadata, status stays superseded
    proposals.supersede(cid, sid)
    assert proposals.get(cid, sid)["status"] == "superseded"
    assert proposals.update_resolution(
        cid, sid, pid, {"tier": "success", "roll_id": "r1", "line_intent": 3}) is True
    got = proposals.get(cid, sid)
    assert got["status"] == "superseded"
    assert got["resolution"]["line_intent"] == 3 and got["resolution"]["roll_id"] == "r1"


def test_update_resolution_refuses_wrong_id_replaced_or_pending(monkeypatch, tmp_path):
    """False (no write) for wrong id, a replaced record, or a non-terminal
    status that never carried a resolution — the caller must stop."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    pid = rec["id"]

    # pending: not resolved/superseded -> refused
    assert proposals.update_resolution(cid, sid, pid, {"roll_id": "r1"}) is False
    assert proposals.get(cid, sid)["resolution"] is None

    proposals.claim(cid, sid, pid)
    proposals.transition(cid, sid, pid, ("resolving",), "resolved", {"tier": "success"})
    # wrong id -> refused, real record untouched
    assert proposals.update_resolution(cid, sid, "pr-999999", {"roll_id": "r9"}) is False
    assert proposals.get(cid, sid)["resolution"] == {"tier": "success"}

    # replaced record (supersede + brand-new record with a different id) -> refused
    proposals.supersede(cid, sid)
    fresh = proposals.new(cid, sid, {})
    assert fresh["id"] != pid
    assert proposals.update_resolution(cid, sid, pid, {"roll_id": "r9"}) is False
    assert proposals.get(cid, sid)["id"] == fresh["id"]


def test_write_is_atomic_replace(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    proposals.new(cid, sid, {})
    # no temp litter and the file parses after every operation
    root = campaigns.campaign_root(cid)
    assert [p.name for p in root.glob("proposals.json*")] == ["proposals.json"]


def test_only_this_module_touches_proposals_json():
    """#242, the architectural half: the heal-before-retire guarantee lives in
    `supersede`/`new`/`commit_narration`, so it only holds while this module is
    the sole writer of proposals.json. A future path that opened the file
    directly would bypass the boundary AND every test that guards it, silently
    — exactly the failure mode the issue is about. Comments and docstrings may
    name the file; code outside the module may not reach it."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"
    owner = src / "store" / "proposals.py"
    offenders = []
    for path in src.rglob("*.py"):
        if path == owner:
            continue
        for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.split("#", 1)[0]
            if "proposals.json" in line:
                offenders.append(f"{path.relative_to(src)}:{n}: {raw.strip()}")
    assert offenders == [], (
        "proposals.json must only be reached through store/proposals.py:\n"
        + "\n".join(offenders))
