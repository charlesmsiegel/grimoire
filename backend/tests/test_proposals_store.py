import ast
import json
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
    # wrong expected state, wrong id: both lose without writing (legal edges,
    # so the CAS is what refuses them — not the edge guard below)
    assert proposals.transition(cid, sid, rec["id"], ("pending",), "declined") is False
    assert proposals.transition(cid, sid, "pr-999999", ("pending",), "resolving") is False
    assert proposals.get(cid, sid)["status"] == "resolved"


def test_transition_refuses_every_exit_from_the_projectable_states(monkeypatch, tmp_path):
    """#242: the generic CAS must not be a back door out of `resolved`.

    `superseded` and `narrated` are owned by `supersede`/`commit_narration`,
    which heal first. `declined` is the subtler one and is why this guard
    enumerates EDGES rather than target statuses: `resolved -> declined` keeps
    the resolution but puts the record outside what `project()` accepts, so a
    later `supersede()` heals nothing and retires the roll's only recovery
    handle unprojected — the exact bug the issue is about, reached sideways.
    """
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved",
                         {"result": {"total": 5}})
    # `resolved` is a sink for the generic CAS: every target is refused,
    # including `resolved` itself — amending a resolved record is
    # `update_resolution`'s job, and it refuses to change the roll.
    for to in ("narrated", "superseded", "declined", "pending", "resolving", "resolved"):
        with pytest.raises(ValueError, match="illegal edge"):
            proposals.transition(cid, sid, rec["id"], ("resolved",), to)
    assert proposals.get(cid, sid)["status"] == "resolved"   # refused before any write


def test_transition_refuses_a_resolution_on_a_non_resolved_edge(monkeypatch, tmp_path):
    """#242, the data invariant: a roll `result` may only ever land on a status
    `project()` accepts. A legal edge like `pending -> declined` carrying a
    resolution would otherwise store one where `heal()` reads it as projectable
    and `project()` refuses the status — and the next retirement discards the
    roll entirely, never having logged it."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    for from_state, to in (("pending", "declined"), ("pending", "resolving")):
        if from_state != proposals.get(cid, sid)["status"]:
            continue
        with pytest.raises(ValueError, match="may not carry a resolution"):
            proposals.transition(cid, sid, rec["id"], (from_state,), to,
                                 {"result": {"total": 9}})
    got = proposals.get(cid, sid)
    assert got["status"] == "pending" and got["resolution"] is None

    # resolving -> pending is the other non-resolved edge; reach it legally
    proposals.claim(cid, sid, rec["id"])
    with pytest.raises(ValueError, match="may not carry a resolution"):
        proposals.transition(cid, sid, rec["id"], ("resolving",), "pending",
                             {"result": {"total": 9}})
    # and the two edges that MAY carry one still do
    assert proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved",
                                {"result": {"total": 9}}) is True
    assert proposals.get(cid, sid)["resolution"] == {"result": {"total": 9}}


def test_update_resolution_cannot_change_the_rolled_result(monkeypatch, tmp_path):
    """#242: metadata amendments only. Swapping a projected record's `result`
    would leave `rolls.json` holding the ORIGINAL roll — `find_or_append_by_
    proposal` is idempotent by tag — while the next `project()` formats its
    transcript line from the replacement, so the log and the transcript would
    disagree about what was rolled."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    pid = rec["id"]
    proposals.claim(cid, sid, pid)
    proposals.transition(cid, sid, pid, ("resolving",), "resolved",
                         {"result": {"total": 5}})

    # metadata alongside the SAME result: allowed, that is the whole purpose
    assert proposals.update_resolution(
        cid, sid, pid, {"result": {"total": 5}, "roll_id": 3}) is True
    assert proposals.get(cid, sid)["resolution"]["roll_id"] == 3

    for swapped in ({"result": {"total": 20}}, {"result": {"total": 20}, "roll_id": 3}):
        with pytest.raises(ValueError, match="cannot change a resolved roll"):
            proposals.update_resolution(cid, sid, pid, swapped)
    assert proposals.get(cid, sid)["resolution"]["result"] == {"total": 5}

    # the same guard holds once superseded, where the roll stands as history.
    # Status set on disk rather than through supersede(), which would heal and
    # try to project this deliberately label-less stub resolution.
    path = campaigns.campaign_root(cid) / "proposals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[sid]["status"] = "superseded"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot change a resolved roll"):
        proposals.update_resolution(cid, sid, pid, {"result": {"total": 20}})
    assert proposals.get(cid, sid)["resolution"]["result"] == {"total": 5}
    # ...and a metadata-only amendment still lands on the superseded record
    assert proposals.update_resolution(
        cid, sid, pid, {"result": {"total": 5}, "line_intent": 7}) is True
    assert proposals.get(cid, sid)["resolution"]["line_intent"] == 7


def test_heal_raises_rather_than_retiring_a_roll_it_cannot_project(monkeypatch, tmp_path):
    """The backstop. `heal`'s notion of projectable is broader than `project`'s,
    and every gap between them has been a way to lose a roll. The guards above
    make the gap unreachable through the API, so this drives it by corrupting
    the file directly — if a future edge reopens it, retirement must fail loudly
    instead of silently discarding the roll."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    path = campaigns.campaign_root(cid) / "proposals.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[sid]["status"] = "declined"                       # not projectable...
    data[sid]["resolution"] = {"result": {"total": 9}}     # ...but carries a roll
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="would discard the roll"):
        proposals.supersede(cid, sid)
    assert proposals.get(cid, sid)["status"] == "declined"  # not retired

    # a narrated record is the one legitimate project()-declines case: no raise
    data[sid]["status"] = "narrated"
    path.write_text(json.dumps(data), encoding="utf-8")
    proposals.supersede(cid, sid)                           # narrated is terminal
    assert proposals.get(cid, sid)["status"] == "narrated"


def test_transition_checks_the_whole_declaration_not_just_the_winning_edge(monkeypatch, tmp_path):
    """Passing a from-state asserts it may legally reach `to`, so a mixed
    declaration is refused even when the record's actual status names a legal
    edge — otherwise the illegal half would sit there until a record happened
    to be in that state."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    with pytest.raises(ValueError, match="illegal edge"):
        proposals.transition(cid, sid, rec["id"], ("pending", "resolved"), "declined")
    assert proposals.get(cid, sid)["status"] == "pending"


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
    # narrated is terminal: untouched. Reached through commit_narration —
    # `transition` refuses the exits, so this is the only legal route (#242).
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved")
    assert proposals.commit_narration(cid, sid, rec["id"], lambda: None) is True
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
    — exactly the failure mode the issue is about.

    Matched on the AST, not the text: prose may name the file freely (the
    streaming module's crash-window disclosure does, correctly), but only a
    string literal that IS the filename can be used to build a path to it.
    """
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"
    owner = src / "store" / "proposals.py"
    offenders = []
    for path in src.rglob("*.py"):
        if path == owner:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "proposals.json":
                offenders.append(f"{path.relative_to(src)}:{node.lineno}")
    assert offenders == [], (
        "proposals.json must only be reached through store/proposals.py:\n"
        + "\n".join(offenders))
