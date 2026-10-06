"""Apply, dismiss and restore -- the store half (Decisions 16-18, spec §12.3,
§12.4, §12.8, §22).

A finding is a guess the sweep made against records as they stood. Acting on it
later has to prove those records still mean what the reader looked at
(`review.check_candidate`), validate every part of the operation before the
first write (`review.plan_apply`), and clean the cache up in an order whose
failure costs nothing (`review.settle`: suppression first, then the drop). The
closure writes themselves go through `routes/ledger.py`'s helpers, so a hand
edit to the ledger still has exactly one home.
"""

from __future__ import annotations

import json

import pytest

from grimoire.routes import ledger as ledger_routes
from grimoire.store import (
    campaigns,
    characters,
    commitments,
    journal,
    locks,
    plot,
    scenes,
    undo,
    worlds,
)
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import candidates, canon, doc, pending, review

MAP = "thread:mara-s-map"
CHART = "thread:winifred-s-chart"
FEUD = "thread:the-realm-feud"
OATH = "commitment:mara-s-oath"
DEBT = "commitment:winifred-s-debt"
VOW = "commitment:seraphine-s-vow"

DUP = "possible_duplicate"
REL = "possible_relation"
CLOSURE = "possible_thread_closure"
RESOLUTION = "possible_commitment_resolution"


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara", "default",
                                characters.blank_card("Mara"))
    cid = campaigns.create_campaign("Run", wid)
    plot.set_movement(cid, "mara-s-map", "Mara's map", "open", "The map was stolen.", "")
    plot.set_movement(cid, "winifred-s-chart", "Winifred's chart", "open",
                      "Winifred lost the chart.", "")
    plot.set_movement(cid, "the-realm-feud", "The Realm feud", "closed", "It ended.", "")
    commitments.set_movement(cid, "mara-s-oath", "Mara's oath", "promise", "open", "",
                             "She swore it.", "")
    commitments.set_movement(cid, "winifred-s-debt", "Winifred's debt", "threat", "open",
                             "by midwinter", "She owes it.", "")
    commitments.set_movement(cid, "seraphine-s-vow", "Seraphine's vow", "promise", "open",
                             "by spring", "She vowed it.", "")
    return cid


@pytest.fixture
def s0(cid):
    return scenes.create_scene(cid, "Saltmarch docks")


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _files(cid) -> dict[str, bytes]:
    root = _root(cid)
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _proposal(decision, **over):
    base = {"decision": decision, "from": "", "to": "", "relation": "", "status": "",
            "reason": "", "evidence_scenes": []}
    base.update(over)
    return base


def _cache(cid, kind, refs, *, proposal=None, signals=None) -> str:
    """Cache one finding whose stored fingerprint is the current one; its id."""
    fp = pending.fingerprint(pending.Current.load(cid), kind, list(refs))
    assert fp is not None
    data = candidates.read(cid)
    key = canon.candidate_id(kind, refs)
    data["records"][key] = {"kind": kind, "refs": list(refs), "fingerprint": fp,
                            "signals": signals or {}, "proposal": proposal,
                            "created": "2026-01-01T00:00:00"}
    candidates.write(cid, data)
    return key


def _refused(fn, *args, **kwargs) -> review.RefusedError:
    with pytest.raises(review.RefusedError) as caught:
        fn(*args, **kwargs)
    return caught.value


def _check(cid, key, expect=None, **kw):
    with locks.campaign_lock(cid):
        return review.check_candidate(cid, key, expect, **kw)


def _plan(cid, key, body, expect=None):
    with locks.campaign_lock(cid):
        return review.plan_apply(cid, review.check_candidate(cid, key, expect), body)


# ---------------------------------------------------------- the ledger helpers


@pytest.mark.parametrize(("beat_scene", "expected"), [
    ("003--y", "005--x"),      # an earlier evidence scene never moves it back
    ("007--z", "007--z"),      # a later one moves it forward
])
def test_move_thread_with_a_beat_never_moves_last_scene_backwards(cid, beat_scene, expected):
    plot.set_movement(cid, "mara-s-map", "", "", "", "005--x")
    before = plot.get(cid, "mara-s-map")
    rows = len(journal.read(cid))
    with locks.campaign_lock(cid):
        ledger_routes.move_thread(cid, "mara-s-map", status="closed",
                                  beat="Mara burned the map.", scene=beat_scene,
                                  keep_later_scene=True, label="Mara's map — closed")
    after = plot.get(cid, "mara-s-map")
    assert after["beats"][-1] == {"scene": beat_scene, "text": "Mara burned the map."}
    assert after["last_scene"] == expected
    assert after["status"] == "closed" and after["title"] == "Mara's map"
    written = journal.read(cid)
    assert len(written) == rows + 1
    assert written[-1]["label"] == "Mara's map — closed"
    undo.undo(cid, written[-1]["id"])
    assert plot.get(cid, "mara-s-map") == before


def test_move_thread_without_a_beat_keeps_last_scene_and_title(cid):
    plot.set_movement(cid, "mara-s-map", "", "", "", "005--x")
    before = plot.get(cid, "mara-s-map")
    with locks.campaign_lock(cid):
        ledger_routes.move_thread(cid, "mara-s-map", status="closed", label="closed")
    after = plot.get(cid, "mara-s-map")
    assert after == {**before, "status": "closed"}


def test_move_commitment_keeps_kind_and_due(cid):
    commitments.set_movement(cid, "winifred-s-debt", "", "", "", None, "", "004--w")
    before = commitments.get(cid, "winifred-s-debt")
    rows = len(journal.read(cid))
    with locks.campaign_lock(cid):
        ledger_routes.move_commitment(cid, "winifred-s-debt", status="fulfilled",
                                      label="Winifred's debt — fulfilled")
    after = commitments.get(cid, "winifred-s-debt")
    assert after == {**before, "status": "fulfilled"}
    assert (after["kind"], after["due"], after["last_scene"]) == ("threat", "by midwinter",
                                                                  "004--w")
    with locks.campaign_lock(cid):
        ledger_routes.move_commitment(cid, "mara-s-oath", due="by midwinter",
                                      label="Mara's oath — due copied")
    assert commitments.get(cid, "mara-s-oath")["due"] == "by midwinter"
    assert len(journal.read(cid)) == rows + 2


def test_move_commitment_with_a_beat_keeps_the_later_scene(cid):
    commitments.set_movement(cid, "mara-s-oath", "", "", "", None, "", "005--x")
    with locks.campaign_lock(cid):
        ledger_routes.move_commitment(cid, "mara-s-oath", status="broken",
                                      beat="She broke it.", scene="003--y",
                                      keep_later_scene=True, label="broken")
    after = commitments.get(cid, "mara-s-oath")
    assert after["beats"][-1] == {"scene": "003--y", "text": "She broke it."}
    assert (after["status"], after["last_scene"]) == ("broken", "005--x")


# ------------------------------------------------------------- validate_alias


def test_validate_alias_answers_both_standings_and_writes_nothing(cid):
    before = _files(cid)
    out = review.validate_alias(cid, VOW, DEBT)
    assert out == {"source": {"status": "open", "kind": "promise", "due": "by spring"},
                   "canonical": {"ref": DEBT, "status": "open", "kind": "threat",
                                 "due": "by midwinter"}}
    assert _files(cid) == before


def test_create_alias_refusals_unchanged(cid):
    """`create_alias` refuses through `validate_alias` now; the refusal is the
    same object either way (the whole of test_continuity_review.py runs too)."""
    direct = _refused(review.validate_alias, cid, MAP, FEUD)
    via_create = _refused(review.create_alias, cid, MAP, FEUD)
    assert (direct.status, direct.kind, direct.extra) == (
        via_create.status, via_create.kind, via_create.extra)
    assert direct.kind == "liveness_mismatch"
    assert review.validate_alias(cid, MAP, FEUD, accept_status_change=True)["canonical"][
        "status"] == "closed"
    assert doc.read(cid)["aliases"] == {}


# ------------------------------------------------------------ check_candidate


def test_check_candidate_passes_a_live_finding(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    checked = _check(cid, key)
    assert checked["record"]["refs"] == [MAP, CHART]
    assert checked["fingerprint"] == candidates.read(cid)["records"][key]["fingerprint"]
    assert isinstance(checked["current"], pending.Current)


def test_check_candidate_stale_carries_current_records(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    plot.set_movement(cid, "mara-s-map", "Mara's lost map", "", "", "")
    refused = _refused(_check, cid, key)
    assert (refused.status, refused.kind) == (409, "stale_candidate")
    current = pending.fingerprint(pending.Current.load(cid), DUP, [MAP, CHART])
    assert refused.extra["reason"] == "records"
    assert refused.extra["current"]["fingerprint"] == current
    titles = [row["title"] for row in refused.extra["current"]["records"]]
    assert titles == ["Mara's lost map", "Winifred's chart"]


def test_resubmitting_against_the_current_fingerprint_passes(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    cached = candidates.read(cid)["records"][key]["fingerprint"]
    plot.set_movement(cid, "mara-s-map", "Mara's lost map", "", "", "")
    refused = _refused(_check, cid, key, cached)
    assert refused.kind == "stale_candidate"
    current = refused.extra["current"]["fingerprint"]
    assert current != cached
    assert _check(cid, key, current)["fingerprint"] == current


def test_a_live_finding_against_an_old_expectation_is_stale(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    assert _refused(_check, cid, key, "fp1_old").kind == "stale_candidate"


def test_a_gone_evidence_scene_is_stale(cid, s0):
    gone = scenes.create_scene(cid, "Realm road")
    key = _cache(cid, CLOSURE, [MAP], signals={"reason": "stale"},
                 proposal=_proposal("close", reason="It was answered.",
                                    evidence_scenes=[s0, gone]))
    scenes.delete_scene(cid, gone)
    refused = _refused(_check, cid, key)
    assert (refused.status, refused.kind, refused.extra["reason"]) == (
        409, "stale_candidate", "evidence")
    assert refused.extra["current"]["fingerprint"] == candidates.read(cid)["records"][key][
        "fingerprint"]
    out = review.dismiss(cid, key, "dismiss")
    assert out["ok"] is True
    assert key not in candidates.read(cid)["records"]


def test_apply_on_a_finding_no_longer_cached_is_not_found(cid):
    refused = _refused(_check, cid, canon.candidate_id(DUP, [MAP, CHART]))
    assert (refused.status, refused.kind) == (404, "not_found")
    key = _cache(cid, DUP, [MAP, CHART])
    candidates.drop(cid, key)
    assert _refused(_check, cid, key).kind == "not_found"
    assert _refused(review.dismiss, cid, key, "dismiss").kind == "not_found"


def _suppressed(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    fp = candidates.read(cid)["records"][key]["fingerprint"]
    doc.put_suppression(cid, fp, {"kind": DUP, "refs": [MAP, CHART], "decision": "dismiss",
                                  "created": ""})
    return key


def _satisfied(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    doc.put_link(cid, canon.link_id("related_to", MAP, CHART),
                 {"a": MAP, "b": CHART, "relation": "related_to", "created": "",
                  "scene": "", "note": ""})
    return key


def _gone(cid):
    key = _cache(cid, DUP, [MAP, CHART])
    doc.put_alias(cid, MAP, {"to": CHART, "created": "", "source": "manual", "note": ""})
    return key


def _settled(cid):
    return _cache(cid, CLOSURE, [MAP], signals={"reason": "touched"},
                  proposal=_proposal("keep_open", reason="Still going."))


@pytest.mark.parametrize("make", [_suppressed, _satisfied, _gone, _settled],
                         ids=["suppressed", "satisfied", "gone", "settled"])
def test_hidden_verdicts_are_not_found(cid, make):
    key = make(cid)
    before = _files(cid)
    applied = _refused(_check, cid, key)
    dismissed = _refused(review.dismiss, cid, key, "dismiss")
    assert (applied.status, applied.kind) == (404, "not_found")
    assert (dismissed.status, dismissed.kind) == (404, "not_found")
    assert _files(cid) == before


def test_apply_refuses_while_continuity_json_is_malformed(cid):
    key = _cache(cid, CLOSURE, [MAP], signals={"reason": "stale"})
    (_root(cid) / "continuity.json").write_text(json.dumps({"aliases": []}), encoding="utf-8")
    before = _files(cid)
    applied = _refused(_check, cid, key)
    dismissed = _refused(review.dismiss, cid, key, "dismiss")
    assert (applied.status, applied.kind) == (409, "malformed")
    assert (dismissed.status, dismissed.kind) == (409, "malformed")
    assert _files(cid) == before


def test_an_unreadable_ledger_is_refused_not_hidden(cid):
    key = _cache(cid, CLOSURE, [MAP], signals={"reason": "stale"})
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    refused = _refused(_check, cid, key)
    assert (refused.status, refused.kind) == (409, "unreadable")


# ---------------------------------------------------------------- plan_apply


def _pair(cid):
    return _cache(cid, DUP, [MAP, CHART])


def _cross(cid):
    return _cache(cid, REL, [MAP, OATH])


def _owed(cid):
    return _cache(cid, DUP, [OATH, DEBT])


def _dated(cid):
    return _cache(cid, DUP, [VOW, DEBT])


def _closure(cid):
    return _cache(cid, CLOSURE, [MAP], signals={"reason": "stale"})


def _resolution(cid):
    return _cache(cid, RESOLUTION, [OATH], signals={"reason": "overdue"})


def _open_into_closed(cid):
    return _cache(cid, DUP, [MAP, FEUD])


@pytest.mark.parametrize(("make", "body", "status", "kind"), [
    (_cross, {"op": "alias", "canonical": MAP}, 400, "bad_op"),
    (_pair, {"op": "alias", "canonical": FEUD}, 400, "bad_canonical"),
    (_cross, {"op": "link", "from": OATH, "to": MAP, "relation": "pays_off"},
     400, "invalid_relation"),
    (_owed, {"op": "link", "from": OATH, "to": DEBT, "relation": "subthread_of"},
     400, "invalid_relation"),
    (_owed, {"op": "link", "from": OATH, "to": DEBT, "relation": "continues"},
     400, "invalid_relation"),
    (_resolution, {"op": "resolve", "status": "closed"}, 400, "bad_status"),
    (_closure, {"op": "close", "beat": "Mara burned the map."}, 400, "bad_scene"),
    (_dated, {"op": "alias", "canonical": DEBT, "copy_due": True}, 400, "due_not_copyable"),
    (_pair, {"op": "alias", "canonical": CHART, "copy_due": True}, 400, "due_not_copyable"),
    (_open_into_closed, {"op": "alias", "canonical": FEUD}, 409, "liveness_mismatch"),
    (_pair, {"op": "link", "from": MAP, "to": FEUD, "relation": "related_to"},
     400, "invalid_relation"),
    (_pair, {"op": "link", "from": MAP, "to": MAP, "relation": "related_to"},
     400, "invalid_relation"),
    (_closure, {"op": "link", "from": MAP, "to": CHART, "relation": "related_to"},
     400, "bad_op"),
    (_pair, {"op": "close"}, 400, "bad_op"),
    (_closure, {"op": "resolve", "status": "fulfilled"}, 400, "bad_op"),
    (_pair, {"op": "keep_open"}, 400, "bad_op"),
    (_pair, {"op": "merge"}, 400, "bad_op"),
    (_pair, {}, 400, "bad_op"),
    (_pair, {"op": "alias", "canonical": CHART, "beat": "A beat."}, 400, "bad_op"),
    (_closure, {"op": "close", "beat": "Gone.", "scene": "009--nowhere"}, 400, "bad_scene"),
    (_closure, {"op": "close", "status": "open"}, 400, "bad_status"),
    (_pair, {"op": "alias", "canonical": CHART, "accept_status_change": "yes"},
     400, "bad_body"),
    (_pair, {"op": 3}, 400, "bad_body"),
    (_pair, {"op": "alias", "canonical": ["thread:mara-s-map"]}, 400, "bad_body"),
], ids=["alias-cross-type", "canonical-not-a-ref", "pays-off-reversed",
        "subthread-on-commitments", "continues-on-commitments", "resolve-closed",
        "beat-without-scene", "copy-due-over-a-due", "copy-due-on-threads",
        "open-into-closed", "link-outside-the-pair", "link-to-itself", "link-on-a-closure",
        "close-on-a-pair", "resolve-on-a-thread", "keep-open-on-a-pair", "unknown-op",
        "no-op", "beat-on-an-alias", "unknown-scene", "close-with-another-status",
        "bool-as-text", "op-not-text", "canonical-not-text"])
def test_plan_apply_validates_everything_before_any_write(cid, make, body, status, kind):
    key = make(cid)
    before = _files(cid)
    refused = _refused(_plan, cid, key, body)
    assert (refused.status, refused.kind) == (status, kind)
    if kind == "liveness_mismatch":
        assert refused.extra["source"]["status"] == "open"
        assert refused.extra["canonical"] == {"ref": FEUD, "status": "closed", "kind": "",
                                              "due": ""}
    assert _files(cid) == before


def test_plan_apply_rejects_a_body_that_is_not_an_object(cid):
    key = _pair(cid)
    assert _refused(_plan, cid, key, ["op"]).kind == "bad_body"


def test_plan_apply_plans_each_op(cid, s0):
    assert _plan(cid, _open_into_closed(cid),
                 {"op": "alias", "canonical": FEUD, "accept_status_change": True}) == {
        "op": "alias", "beat": "", "scene": "",
        "alias": {"ref": MAP, "to": FEUD, "accept": True}}
    assert _plan(cid, _owed(cid), {"op": "alias", "canonical": OATH, "copy_due": True}) == {
        "op": "alias", "beat": "", "scene": "",
        "alias": {"ref": DEBT, "to": OATH, "accept": False},
        "copy_due": "by midwinter", "target": "mara-s-oath"}
    assert _plan(cid, _cross(cid), {"op": "link", "from": MAP, "to": OATH,
                                    "relation": "pays_off", "scene": s0}) == {
        "op": "link", "beat": "", "scene": s0,
        "link": {"a": MAP, "b": OATH, "relation": "pays_off"}}
    assert _plan(cid, _pair(cid), {"op": "link", "from": CHART, "to": MAP,
                                   "relation": "continues"})["link"] == {
        "a": CHART, "b": MAP, "relation": "continues"}
    assert _plan(cid, _closure(cid), {"op": "close", "beat": " Mara burned it. ",
                                      "scene": s0}) == {
        "op": "close", "status": "closed", "beat": "Mara burned it.", "scene": s0,
        "target": "mara-s-map"}
    assert _plan(cid, _resolution(cid), {"op": "resolve", "status": "broken",
                                         "expect_fingerprint": None}) == {
        "op": "resolve", "status": "broken", "beat": "", "scene": "",
        "target": "mara-s-oath"}
    assert _plan(cid, _closure(cid), {"op": "close", "scene": s0})["scene"] == ""
    assert _plan(cid, _resolution(cid), {"op": "keep_open"}) == {
        "op": "keep_open", "beat": "", "scene": "", "decision": "keep_open"}


def test_a_temporal_pair_links_from_the_commitment(cid):
    from grimoire.store import events
    event = "event:" + events.create(cid, "The coronation", "2026-05-09", "")
    key = _cache(cid, REL, [OATH, event])
    assert _plan(cid, key, {"op": "link", "from": OATH, "to": event,
                            "relation": "before"})["link"] == {
        "a": OATH, "b": event, "relation": "before"}
    assert _plan(cid, key, {"op": "link", "from": event, "to": OATH,
                            "relation": "related_to"})["link"]["relation"] == "related_to"
    for bad in ({"op": "link", "from": event, "to": OATH, "relation": "before"},
                {"op": "link", "from": OATH, "to": event, "relation": "pays_off"},
                {"op": "alias", "canonical": OATH}):
        assert _refused(_plan, cid, key, bad).status == 400


# ------------------------------------------------------- dismiss and restore


def test_dismiss_writes_a_suppression_and_removes_the_finding(cid):
    key = _pair(cid)
    fp = candidates.read(cid)["records"][key]["fingerprint"]
    out = review.dismiss(cid, key, "dismiss")
    assert out == {"ok": True, "fingerprint": fp}
    stored = doc.read(cid)["suppressions"][fp]
    assert {k: stored[k] for k in ("kind", "refs", "decision")} == {
        "kind": DUP, "refs": [MAP, CHART], "decision": "dismiss"}
    assert stored["created"]
    assert key not in candidates.read(cid)["records"]


def test_keep_open_is_lifecycle_only(cid):
    pair, closure = _pair(cid), _closure(cid)
    before = _files(cid)
    for key, decision in ((pair, "keep_open"), (closure, "postpone")):
        refused = _refused(review.dismiss, cid, key, decision)
        assert (refused.status, refused.kind) == (400, "bad_decision")
    assert _files(cid) == before
    fp = review.dismiss(cid, closure, "keep_open")["fingerprint"]
    assert doc.read(cid)["suppressions"][fp]["decision"] == "keep_open"
    # Suppressed and still cached elsewhere would read `suppressed`; here it is gone.
    assert closure not in candidates.read(cid)["records"]


def test_dismissing_a_stale_finding_is_refused(cid):
    key = _pair(cid)
    plot.set_movement(cid, "winifred-s-chart", "Winifred's torn chart", "", "", "")
    before = _files(cid)
    refused = _refused(review.dismiss, cid, key, "dismiss")
    assert (refused.status, refused.kind) == (409, "stale_candidate")
    assert _files(cid) == before
    current = refused.extra["current"]["fingerprint"]
    assert review.dismiss(cid, key, "dismiss", current)["fingerprint"] == current
    assert current in doc.read(cid)["suppressions"]


def test_restore_suppression_round_trip_and_404(cid):
    key = _pair(cid)
    fp = review.dismiss(cid, key, "dismiss")["fingerprint"]
    assert review.restore_suppression(cid, fp) == {"ok": True}
    assert fp not in doc.read(cid)["suppressions"]
    refused = _refused(review.restore_suppression, cid, fp)
    assert (refused.status, refused.kind) == (404, "not_found")


def test_restore_refuses_while_suppressions_are_malformed(cid):
    (_root(cid) / "continuity.json").write_text(json.dumps({"suppressions": []}),
                                                 encoding="utf-8")
    assert _refused(review.restore_suppression, cid, "fp1_x").kind == "malformed"


def test_suppression_writes_are_not_journalled(cid):
    key = _closure(cid)
    rows = len(journal.read(cid))
    fp = review.dismiss(cid, key, "keep_open")["fingerprint"]
    review.restore_suppression(cid, fp)
    assert len(journal.read(cid)) == rows


def test_settle_without_a_decision_only_drops(cid):
    key = _pair(cid)
    record = candidates.read(cid)["records"][key]
    assert review.settle(cid, key, record, record["fingerprint"], None) == ["cache"]
    assert doc.read(cid)["suppressions"] == {}
    assert key not in candidates.read(cid)["records"]


def test_dismiss_with_a_failing_cache_drop_still_suppresses(cid, monkeypatch):
    key = _pair(cid)
    fp = candidates.read(cid)["records"][key]["fingerprint"]

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(candidates, "drop", boom)
    with pytest.raises(review.PartialSettleError) as caught:
        review.dismiss(cid, key, "dismiss")
    assert caught.value.landed == ["suppression"]
    assert isinstance(caught.value, OSError)
    assert fp in doc.read(cid)["suppressions"]
    verdicts = {k: v for k, _rec, v in pending.findings(cid)}
    assert verdicts == {key: "suppressed"}


def test_a_suppression_failure_lands_nothing(cid, monkeypatch):
    key = _pair(cid)
    cache = (_root(cid) / "continuity_candidates.json").read_bytes()

    def malformed(*_a, **_k):
        raise doc.ContinuityError("continuity.json's suppressions section cannot be read")

    monkeypatch.setattr(doc, "put_suppression", malformed)
    refused = _refused(review.dismiss, cid, key, "dismiss")
    assert (refused.status, refused.kind) == (409, "malformed")
    assert (_root(cid) / "continuity_candidates.json").read_bytes() == cache


def test_the_dismiss_body_is_additive():
    from grimoire.routes.models import ContinuityDismiss
    assert ContinuityDismiss().decision is None
    body = ContinuityDismiss(decision="keep_open", expect_fingerprint="fp1_x")
    assert (body.decision, body.expect_fingerprint) == ("keep_open", "fp1_x")
