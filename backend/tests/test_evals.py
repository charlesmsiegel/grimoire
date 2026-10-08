"""The eval suite, run in replay mode as part of the ordinary test run.

pytest (and so `make check` and CI) IS the gate — an eval suite that only ran when
someone remembered to invoke it would not catch the prompt edit it exists to
catch. Replay is offline and deterministic, so it belongs here; the live mode
(`evals/run.py --live`) costs money and is never a test.

The suite lives at the repo root rather than under backend/src, because the
Android build packages backend/src verbatim into the APK.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402
from evals.graders import Check  # noqa: E402

PAIRS = [(case, rec) for case in case_mod.CASES for rec in case.recordings]


@pytest.mark.parametrize("case,recording", PAIRS,
                         ids=[f"{c.id}.{r.variant}" for c, r in PAIRS])
def test_recording_scores_as_declared(monkeypatch, tmp_path, case, recording):
    """A must-PASS recording passes; a must-FAIL one still fails.

    The second half is the load-bearing one. A grader that quietly stopped
    detecting anything would leave every must-PASS case green, and only the
    counterexamples notice.
    """
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    result = runner.replay(case, recording)
    assert not result.error, result.error
    assert result.passed, "\n".join(
        f"{c.name}: {c.detail}" for c in result.failures)


def test_every_case_has_a_recordable_baseline():
    """`--record` writes to the baseline variant; a case without one would
    silently record nothing on a live run."""
    for case in case_mod.CASES:
        assert any(r.variant == case_mod.BASELINE for r in case.recordings), case.id
        assert any(not r.expect_pass for r in case.recordings), \
            f"{case.id} has no counterexample — its graders are unproven"


def test_no_orphan_recordings():
    """Every file in recordings/ is claimed by a case. Renaming a case without
    deleting its old files would otherwise leave dead fixtures behind that no
    longer score anything."""
    declared = {rec.path(case.id).name
                for case in case_mod.CASES for rec in case.recordings}
    on_disk = {p.name for p in case_mod.RECORDINGS.iterdir() if p.is_file()}
    assert on_disk == declared, f"orphaned: {sorted(on_disk - declared)}"


def test_every_case_names_a_routed_task():
    """A live run sends each case where the app sends that generation, so each
    case names the task the app meters it under, and a route claims it."""
    from grimoire.store import routing
    for case in case_mod.CASES:
        assert case.task in routing.TASK_ROUTE, (case.id, case.task)
    assert case_mod.BY_ID["absorb"].task == "absorb"
    assert case_mod.BY_ID["scene-length"].task == "chat"


def test_a_live_run_resolves_through_the_seam_not_the_active_connection(
        monkeypatch, tmp_path):
    """On a migrated store `active_connection_id` is frozen for older builds;
    a live pass must be scored on the model the app plays on now."""
    from grimoire.store import config, llm_connections
    from grimoire.store.inference import migrate
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    config.read_config()
    llm_connections.create_connection("openai_compatible", "Mara Local",
                                      base_url="http://localhost:1234/v1")
    llm_connections.create_connection("openai_compatible", "Winifred Local",
                                      base_url="http://localhost:5678/v1")
    assert migrate.ensure().state == "done"
    config.write_config(role_primary_provider="mara-local", role_primary_model="big",
                        role_fast_provider="winifred-local", role_fast_model="small")

    conns = runner.resolve_connections(case_mod.CASES)

    assert (conns["chat"]["id"], conns["chat"]["model"]) == ("mara-local", "big")
    # Absorb's route defaults to the Fast role.
    assert (conns["absorb"]["id"], conns["absorb"]["model"]) == ("winifred-local", "small")

    # A refusal is the seam's own reason.
    config.write_config(role_primary_provider="openrouter", role_primary_model="vendor/m")
    with pytest.raises(RuntimeError, match="OpenRouter key not set"):
        runner.resolve_connections((case_mod.BY_ID["scene-length"],))


def test_live_resolves_a_decide_case_on_its_task(monkeypatch, tmp_path):
    """I9: a case with a `schema` is a decide case. Live, its task resolves as
    a decide operation and the reply is asked for with `schema=`, so it
    measures what production sends; replay scores the recording and never
    builds a schema."""
    from grimoire.store import config, llm_connections
    from grimoire.store.inference import migrate
    from tests.llm_fakes import FakeLLM

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    config.read_config()
    llm_connections.create_connection("openai_compatible", "Mara Local",
                                      base_url="http://localhost:1234/v1")
    llm_connections.create_connection("openai_compatible", "Winifred Local",
                                      base_url="http://localhost:5678/v1")
    assert migrate.ensure().state == "done"
    config.write_config(role_primary_provider="mara-local", role_primary_model="big",
                        role_decision_provider="winifred-local",
                        role_decision_model="small", use_scene_break="decision")

    reply = '{"0": {"answers": {"break": true}, "rationale": "They left."}}'
    asked: list[dict] = []
    wanted = {"type": "object", "properties": {}}

    def schema(ctx: dict) -> dict:
        asked.append(ctx)
        return wanted

    case = case_mod.Case(
        id="planted-decide", hypothesis="a planted decide case",
        build=lambda: {"fixture": True},
        prompt=lambda ctx: [{"role": "user", "content": "Is the scene over?"}],
        grade=lambda ctx, out: [Check("planted.reply", out == reply)],
        recordings=(case_mod.Recording(case_mod.BASELINE),),
        task="scene-break", schema=schema)
    plain = case_mod.BY_ID["scene-length"]

    conns = runner.resolve_connections((case, plain))
    decide_conn = conns[runner.conn_key(case)]
    assert (decide_conn["id"], decide_conn["model"]) == ("winifred-local", "small")
    assert conns[runner.conn_key(plain)]["id"] == "mara-local"
    assert runner.conn_key(plain) == "chat"

    fake = FakeLLM([[reply]])
    result = runner.live(case, decide_conn, client=fake)
    assert result.passed, result.error or result.failures
    assert fake.schemas == [wanted]
    assert fake.conn is decide_conn
    assert asked and asked[0]["fixture"] is True

    # Replay of the same case reads its recording and asks for no schema.
    asked.clear()
    monkeypatch.setattr(case_mod, "RECORDINGS", tmp_path)
    (tmp_path / "planted-decide.compliant.md").write_text(reply, encoding="utf-8")
    assert runner.replay(case, case.baseline).passed
    assert asked == []
    assert fake.calls == 1


def test_a_generate_case_is_sent_with_no_schema(monkeypatch, tmp_path):
    from tests.llm_fakes import FakeLLM

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["scene-length"]
    assert case.schema is None and runner.operation(case) == "generate"
    fake = FakeLLM([["Seraphine Vale shrugs."]])
    runner.live(case, {"kind": "openrouter", "id": "openrouter"}, client=fake)
    assert fake.schemas == [None]


def test_decide_scene_break_holds_the_decide_prompt_contract(monkeypatch, tmp_path):
    """The permanent scene-break decide case: its prompt is the structured
    prompt production sends for `build_item`'s item, a live run sends that
    item's schema, and each counterexample isolates one output check."""
    from grimoire import decisions, inference
    from grimoire.store import scene_break

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["decide-scene-break"]
    assert case.task == "scene-break"
    assert {r.variant: r.expect_fail for r in case.recordings} == {
        "compliant": (), "undecodable": ("decide.json",),
        "wrong": ("decide.answer",), "no-reason": ("decide.rationale",)}
    ctx = runner.prepare(case)
    (item,) = ctx["items"]
    assert item.questions[0].id == scene_break.QUESTION_ID
    assert ctx["messages"] == inference.structured_messages(
        [item], explain=scene_break.explain())
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema([item], explain=True)


def test_decide_voice_drift_holds_the_decide_prompt_contract(monkeypatch, tmp_path):
    """The permanent voice-drift decide case: its prompt is the structured
    prompt the switch will send for `build_item`'s item -- the outstanding
    correction included -- a live run sends that item's schema, and each
    counterexample isolates one output check."""
    from grimoire import decisions, inference
    from grimoire.store import voice_drift

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["decide-voice-drift"]
    assert case.task == "voice-drift"
    assert {r.variant: r.expect_fail for r in case.recordings} == {
        "compliant": (), "undecodable": ("decide.json",),
        "wrong": ("decide.answer",), "no-note": ("decide.rationale",),
        "long-note": ("decide.rationale",)}
    ctx = runner.prepare(case)
    (item,) = ctx["items"]
    assert item.questions[0].id == voice_drift.QUESTION_ID
    assert ctx["correction"] and ctx["correction"] in item.context
    assert "Clipped. Never uses contractions." in item.context
    assert ctx["messages"] == inference.structured_messages(
        [item], explain=voice_drift.explain())
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema([item], explain=True)


def test_decide_speaker_holds_the_decide_prompt_contract(monkeypatch, tmp_path):
    """The permanent speaker decide case: its prompt is the structured prompt
    the switch will send for `selector_item`'s item -- no rationale asked for,
    the roster as options beside `grimoire`, null allowed -- a live run sends
    that item's schema, and each counterexample isolates the answer."""
    from grimoire import decisions, inference
    from grimoire.store import response_protocol

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["decide-speaker"]
    assert case.task == "response-selector"
    assert {r.variant: r.expect_fail for r in case.recordings} == {
        "compliant": (), "undecodable": ("decide.json",),
        "off-roster": ("decide.answer",), "abstained": ("decide.answer",)}
    ctx = runner.prepare(case)
    (item,) = ctx["items"]
    (choice,) = item.questions
    assert choice.id == response_protocol.SELECTOR_QUESTION and choice.allow_none
    assert [o.id for o in choice.options] == ["characters:mara", "characters:winifred",
                                              response_protocol.GRIMOIRE_REF]
    assert ctx["messages"] == inference.structured_messages([item])
    assert "Rationale" not in ctx["messages"][1]["content"]
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema([item], explain=False)


def test_decide_continuity_identity_holds_the_decide_prompt_contract(monkeypatch, tmp_path):
    """The permanent duplicate-check decide case: its prompt is the structured
    prompt the switch will send for `build_items`' items over what `examine`
    finds, a live run sends that batch's schema, and each counterexample
    carries today's recording's failure mode into the decide shape."""
    from grimoire import decisions, inference
    from grimoire.store.continuity import identity

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["decide-continuity-identity"]
    assert case.task == "continuity-identity"
    assert {r.variant: r.expect_fail for r in case.recordings} == {
        "compliant": (), "undecodable": ("identity.json",),
        "merged": ("identity.distinct", "identity.continuation"),
        "unknown-id": ("identity.known_ids", "identity.same_obligation")}
    ctx = runner.prepare(case)
    exam = ctx["exam"]
    items = ctx["items"]
    assert items == identity.build_items(exam.prompt_rows(), exam.live)
    assert len(items) == len(exam.rows) == 3
    assert ctx["messages"] == inference.structured_messages(items, explain=identity.explain())
    assert set(ctx["expected"]) == {"r1", "r2", "r3"}
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema(items, explain=True)
    # Today's case still stands beside it until the switch retires it.
    assert "continuity-identity" in case_mod.BY_ID


def test_decide_continuity_reconcile_holds_the_decide_prompt_contract(monkeypatch, tmp_path):
    """The permanent reconciliation decide case: its prompt is the structured
    prompt the switch will send for `build_items`' items over the payload the
    case builds, a live run sends that batch's schema, and each counterexample
    carries today's recording's failure mode into the decide shape."""
    from grimoire import decisions, inference
    from grimoire.store.continuity import reconcile

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["decide-continuity-reconcile"]
    assert case.task == "continuity-reconcile"
    assert {r.variant: r.expect_fail for r in case.recordings} == {
        "compliant": (), "undecodable": ("reconcile.json",),
        "merged": ("reconcile.distinct", "reconcile.continuation"),
        "eager": ("reconcile.keep_open", "reconcile.unproven"),
        "unfounded": ("reconcile.evidence",),
        "timid": ("reconcile.cross_type", "reconcile.close", "reconcile.fulfilled")}
    ctx = runner.prepare(case)
    payload = ctx["payload"]
    items = ctx["items"]
    assert items == reconcile.build_items(payload)
    assert len(items) == len(payload["candidates"]) == 7
    assert ctx["messages"] == inference.structured_messages(items, explain=reconcile.explain())
    assert set(ctx["expected"]) == {f"c{n}" for n in range(1, 8)}
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema(items, explain=True)
    # Today's case still stands beside it until the switch retires it.
    assert "continuity-reconcile" in case_mod.BY_ID
