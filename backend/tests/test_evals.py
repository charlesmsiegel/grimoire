"""The eval suite, run in replay mode as part of the ordinary test run.

pytest (and so `make check` and CI) IS the gate — an eval suite that only ran when
someone remembered to invoke it would not catch the prompt edit it exists to
catch. Replay is offline and deterministic, so it belongs here; the live mode
(`evals/run.py --live`) costs money and is never a test.

The suite lives at the repo root rather than under backend/src, because the
Android build packages backend/src verbatim into the APK.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import cases as case_mod  # noqa: E402
from evals import runner  # noqa: E402
from evals.graders import Check  # noqa: E402
from tests import wire_kit  # noqa: E402

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
    """On a format-2 store `active_connection_id` is frozen for older builds;
    a live pass must be scored on the model the app plays on now."""
    from grimoire.store import config, inference_keys, llm_connections
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    config.read_config()
    llm_connections.create_connection("openai_compatible", "Mara Local",
                                      base_url="http://localhost:1234/v1")
    llm_connections.create_connection("openai_compatible", "Winifred Local",
                                      base_url="http://localhost:5678/v1")
    assert inference_keys.is_current(config.read_config())   # born at format 2
    config.write_config(role_primary_provider="mara-local", role_primary_model="big",
                        role_fast_provider="winifred-local", role_fast_model="small")

    conns = runner.resolve_connections(case_mod.CASES)

    chat = conns["chat"].chain.primary
    assert (chat.provider_id, chat.model) == ("mara-local", "big")
    # Absorb's route defaults to the Fast role.
    assert (conns["absorb"].chain.primary.provider_id,
            conns["absorb"].chain.primary.model) == ("winifred-local", "small")

    # A refusal is the seam's own reason.
    config.write_config(role_primary_provider="openrouter", role_primary_model="vendor/m")
    with pytest.raises(RuntimeError, match="OpenRouter key not set"):
        runner.resolve_connections((case_mod.BY_ID["scene-length"],))


def test_live_resolves_a_decide_case_on_its_task(monkeypatch, tmp_path):
    """I9: a case with a `schema` is a decide case. Live, its task resolves as
    a decide operation and is answered down the decide chain of that
    resolution, so it measures what production sends; replay scores the
    recording and never builds a schema."""
    from grimoire import decisions
    from grimoire.store import config, inference_keys, llm_connections
    from tests.llm_fakes import FakeLLM

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    config.read_config()
    llm_connections.create_connection("openai_compatible", "Mara Local",
                                      base_url="http://localhost:1234/v1")
    llm_connections.create_connection("openai_compatible", "Winifred Local",
                                      base_url="http://localhost:5678/v1")
    assert inference_keys.is_current(config.read_config())   # born at format 2
    config.write_config(role_primary_provider="mara-local", role_primary_model="big",
                        role_decision_provider="winifred-local",
                        role_decision_model="small", use_scene_break="decision")

    reply = '{"0": {"answers": {"break": true}, "rationale": "They left."}}'
    item = decisions.Item("Mara and Winifred have left the Saltmarch quay.",
                          (decisions.Predicate("break", "Is the scene over?"),))
    asked: list[dict] = []

    def schema(ctx: dict) -> dict:
        asked.append(ctx)
        return decisions.schema(ctx["items"], explain=True)

    case = case_mod.Case(
        id="planted-decide", hypothesis="a planted decide case",
        build=lambda: {"items": (item,), "explain": "Say why."},
        prompt=lambda ctx: [{"role": "user", "content": "Is the scene over?"}],
        grade=lambda ctx, out: [Check("planted.reply", json.loads(out) == json.loads(reply))],
        recordings=(case_mod.Recording(case_mod.BASELINE),),
        task="scene-break", schema=schema)
    plain = case_mod.BY_ID["scene-length"]

    conns = runner.resolve_connections((case, plain))
    resolved = conns[runner.conn_key(case)]
    assert not isinstance(resolved, dict)
    assert (resolved.task, resolved.operation) == ("scene-break", "decide")
    assert (resolved.chain.primary.provider_id,
            resolved.chain.primary.model) == ("winifred-local", "small")
    assert conns[runner.conn_key(plain)].chain.primary.provider_id == "mara-local"
    assert runner.conn_key(plain) == "chat"

    fake = FakeLLM([[reply]])
    result = runner.live(case, resolved, client=fake)
    assert result.passed, result.error or result.failures
    assert result.note == "backend: structured"
    assert fake.schemas == [decisions.schema([item], explain=True)]
    sent = fake.requests[-1]["target"]
    assert (sent.provider_id, sent.model) == ("winifred-local", "small")

    # Replay of the same case reads its recording and asks for no schema.
    monkeypatch.setattr(case_mod, "RECORDINGS", tmp_path)
    (tmp_path / "planted-decide.compliant.md").write_text(reply, encoding="utf-8")
    assert runner.replay(case, case.baseline).passed
    assert asked == []
    assert fake.calls == 1


# ---- --live on decide cases: the chain, a forced backend, an override ----

#: The models `_decision_store` lists in the seeded OpenRouter catalog: one that
#: only decides (served natively), one that decides natively AND generates
#: (served structured by the chain; either backend can be forced on it), and
#: the generating primary.
DECIDER, BOTH, ACTIVE = "vendor/decider", "vendor/both", "vendor/active"


def _decision_store(monkeypatch, home: Path, decision_model: str) -> None:
    """A format-2 store with the Decision role on `decision_model` at the
    seeded OpenRouter provider, no fallback. Nothing reaches a provider."""
    from grimoire.store import config, inference_keys, llm_connections

    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    config.read_config()
    llm_connections.update_connection("openrouter", api_key="sk-test")
    rev = llm_connections.read_connection_raw("openrouter")["rev"]
    llm_connections.set_cached_models(
        "openrouter", [{"id": DECIDER, "outputs": ["decisions"]},
                       {"id": BOTH, "outputs": ["text", "decisions"]},
                       {"id": ACTIVE, "outputs": ["text"]}], rev)
    assert inference_keys.is_current(config.read_config())   # born at format 2
    config.write_config(role_primary_provider="openrouter", role_primary_model=ACTIVE,
                        role_decision_provider="openrouter",
                        role_decision_model=decision_model)


def _decide_target(case):
    target = runner.resolve_connections((case,))[runner.conn_key(case)]
    assert not isinstance(target, dict)
    return target


def _over(rationale: str = "The debt is paid."):
    """The scene-break case's right answer, as a native endpoint returns it."""
    from grimoire import decisions
    from grimoire.store import scene_break
    return decisions.ItemResult({scene_break.QUESTION_ID: decisions.Answer(True)},
                                rationale=rationale)


def _compliant(case) -> str:
    return case.baseline.path(case.id).read_text(encoding="utf-8")


def test_live_runs_a_decide_case_through_the_chain(monkeypatch, tmp_path):
    """A native-only Decision model: the chain production sends is one native
    stage, so the case is answered natively and nothing is completed."""
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", DECIDER)
    case = case_mod.BY_ID["decide-scene-break"]
    target = _decide_target(case)
    assert target.decision_mode == "native"

    fake = FakeLLM([["never sent"]], decisions=[_over()])
    result = runner.live(case, target, client=fake)

    assert result.passed, result.error or result.failures
    assert result.note == "backend: native"
    assert fake.calls == 0
    ((_item, conn, _retries),) = fake.native_requests
    assert conn.model == DECIDER
    assert "native" in runner.report([result])


def test_live_a_native_decision_carries_no_rationale(monkeypatch, tmp_path):
    """What a real native endpoint returns: answers and no rationale. A native
    item was never asked for one, so on a case whose prompt asks for one its
    `decide.rationale` passes as not applicable -- visibly, in the report."""
    from evals import graders
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", DECIDER)
    case = case_mod.BY_ID["decide-scene-break"]
    fake = FakeLLM([["never sent"]], decisions=[_over(rationale="")])
    result = runner.live(case, _decide_target(case), client=fake)
    assert result.passed, result.failures
    (rationale,) = [c for c in result.checks if c.name == "decide.rationale"]
    assert rationale.ok and rationale.detail == graders.NATIVE_RATIONALE
    assert f"decide.rationale: {graders.NATIVE_RATIONALE}" in runner.report([result])


def test_live_a_structured_reply_without_a_rationale_still_fails(monkeypatch, tmp_path):
    """The n/a is the native item's alone: a structured answer that leaves out
    the rationale it was asked for fails `decide.rationale` as before."""
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", BOTH)
    case = case_mod.BY_ID["decide-scene-break"]
    fake = FakeLLM([['{"0": {"answers": {"over": true}}}']])
    result = runner.live(case, _decide_target(case), client=fake, backend="structured")
    assert result.note == "backend: structured"
    assert [c.name for c in result.failures] == ["decide.rationale"]


@pytest.mark.parametrize("case_id", ["decide-scene-break", "decide-voice-drift"])
def test_only_a_natively_answered_item_reads_its_rationale_as_not_applicable(
        monkeypatch, tmp_path, case_id):
    """The grader reads `native_items` by index: the one item of a rationale
    case is n/a only when IT is in the set. A set naming another item (as a
    mixed batch would) leaves item 0 a structured answer, which fails a
    missing rationale; replay sets no key at all."""
    from evals import graders

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID[case_id]
    ctx = runner.prepare(case)
    reply = json.loads(_compliant(case))
    reply["0"]["rationale"] = ""
    output = json.dumps(reply)

    def rationale(native_items):
        if native_items is not None:
            ctx["native_items"] = native_items
        (check,) = [c for c in case.grade(ctx, output) if c.name == "decide.rationale"]
        return check

    assert not rationale(None).ok
    assert not rationale(frozenset({1})).ok
    native = rationale(frozenset({0}))
    assert native.ok and native.detail == graders.NATIVE_RATIONALE


def test_live_forces_the_native_backend_on_a_dual_capable_model(monkeypatch, tmp_path):
    from grimoire import inference
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", BOTH)
    case = case_mod.BY_ID["decide-scene-break"]
    target = _decide_target(case)
    # The chain serves this model structured; native is forced on the same one.
    assert runner.chain(target) == inference.stages(target)
    assert [s.mode for s in runner.chain(target)] == ["structured"]

    fake = FakeLLM([["never sent"]], decisions=[_over()])
    result = runner.live(case, target, client=fake, backend="native")

    assert result.passed, result.error or result.failures
    assert result.note == "backend: native"
    assert fake.calls == 0
    ((_item, conn, _retries),) = fake.native_requests
    assert conn.model == BOTH


def test_live_forces_the_structured_backend(monkeypatch, tmp_path):
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", BOTH)
    case = case_mod.BY_ID["decide-scene-break"]
    target = _decide_target(case)

    fake = FakeLLM([[_compliant(case)]], decisions=[_over()])
    result = runner.live(case, target, client=fake, backend="structured")

    assert result.passed, result.error or result.failures
    assert result.note == "backend: structured"
    assert fake.native_requests == []
    ctx = runner.prepare(case)
    assert fake.schemas == [case.schema(ctx)]
    assert fake.requests[-1]["target"].model == BOTH


def test_a_forced_backend_measures_the_primary_alone(monkeypatch, tmp_path):
    """A forced stage is the primary without the fallback it carries, so the
    comparison is of one model on two backends."""
    from grimoire.store import config, llm_connections

    _decision_store(monkeypatch, tmp_path / "home", BOTH)
    llm_connections.create_connection("openrouter", "Rowan Spare", api_key="sk-spare")
    config.write_config(role_decision_fallback_provider="rowan-spare",
                        role_decision_fallback_model=ACTIVE)
    target = _decide_target(case_mod.BY_ID["decide-scene-break"])
    assert target.chain.fallback is not None
    for backend in ("native", "structured"):
        (stage,) = runner.chain(target, backend)
        assert stage.mode == backend and stage.chain.fallback is None
        assert stage.retries is None
    assert target.chain.fallback is not None     # the resolution's own chain is untouched


@pytest.mark.parametrize("setup,backend,says", [
    ("anthropic", "native", "no native decisions endpoint"),
    ("custom", "native", "never decides natively"),
    ("known-no", "native", "known unable to decide natively"),
    ("decider", "structured", "known unable to generate"),
])
def test_live_refuses_native_on_a_kind_without_an_endpoint(monkeypatch, tmp_path, capsys,
                                                           setup, backend, says):
    """A forced backend the model cannot take: exit 2, one sentence, nothing
    sent -- every chain is built before the first case runs."""
    from evals import run
    from grimoire.store import config, llm_connections
    from grimoire.store.inference import facts

    _decision_store(monkeypatch, tmp_path / "home", DECIDER)
    if setup == "anthropic":
        llm_connections.create_connection("anthropic", "Mara Direct", api_key="sk-ant")
        config.write_config(role_decision_provider="mara-direct",
                            role_decision_model="mara-large")
    elif setup == "custom":
        llm_connections.create_connection("openai_compatible", "Winifred Local",
                                          base_url="http://localhost:5678/v1")
        config.write_config(role_decision_provider="winifred-local",
                            role_decision_model="small")
    elif setup == "known-no":
        # The catalog says the model decides natively; the user's own fact
        # says it does not -- a known `no`, which the resolver refuses on.
        facts.set_overrides("openrouter", BOTH, {"decide_native": "no"})
        config.write_config(role_decision_model=BOTH)

    def never(*_a, **_k):
        raise AssertionError("a refused run sent something")

    monkeypatch.setattr(runner, "live_all", never)
    monkeypatch.setattr(runner, "live", never)
    code = run.main(["--live", "--decide-backend", backend,
                     "--case", "decide-scene-break", "--case", "scene-length"])
    err = capsys.readouterr().err
    assert code == 2
    assert says in err and len(err.strip().splitlines()) == 1


def test_an_unknown_decide_native_is_not_refused(monkeypatch, tmp_path):
    """Spec 5.3: only a known `no` refuses. A model the catalog says only
    generates leaves `decide_native` unknown (absence says nothing), so a
    native run may be forced on it."""
    from grimoire.store.inference import capabilities

    _decision_store(monkeypatch, tmp_path / "home", ACTIVE)
    target = _decide_target(case_mod.BY_ID["decide-scene-break"])
    assert target.attempts[0].capabilities["decide_native"].value == capabilities.UNKNOWN
    (stage,) = runner.chain(target, "native")
    assert stage.mode == "native"


def test_a_run_whose_chain_has_no_stage_is_refused(monkeypatch, tmp_path, capsys):
    """A resolution `inference.stages` builds no stage for (a primary known
    unable to do either thing) is refused before anything is sent, rather
    than raising mid-run after earlier cases spent money. The seam refuses
    such a model first; this holds the runner on its own."""
    import dataclasses

    from evals import run
    from grimoire import inference
    from grimoire.store.inference import capabilities

    _decision_store(monkeypatch, tmp_path / "home", ACTIVE)
    case = case_mod.BY_ID["decide-scene-break"]
    target = _decide_target(case)
    primary = target.attempts[0]
    no = capabilities.Cap(capabilities.NO, "override")
    unable = dataclasses.replace(target, attempts=(dataclasses.replace(
        primary, decision_mode="",
        capabilities={**primary.capabilities, "generate": no, "decide_native": no}),))
    assert inference.stages(unable) == ()
    with pytest.raises(runner.BackendRefusedError):
        runner.chain(unable)

    def never(*_a, **_k):
        raise AssertionError("a refused run sent something")

    monkeypatch.setattr(runner, "resolve_connections",
                        lambda *_a, **_k: {runner.conn_key(case): unable})
    monkeypatch.setattr(runner, "live_all", never)
    assert run.main(["--live", "--case", "decide-scene-break"]) == 2
    assert "no decide stage" in capsys.readouterr().err


def test_a_mixed_chain_answers_through_live_and_names_each_backend(monkeypatch, tmp_path):
    """A native-only Decision model with a generating fallback: the chain is
    `inference.stages` -- native, then the fallback as a structured stage of
    its own -- and a batch whose middle item the native endpoint fails is
    finished by the fallback. The note counts each backend's items."""
    from grimoire import decisions, inference
    from grimoire.llm_errors import LLMError
    from grimoire.store import config, llm_connections
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", DECIDER)
    llm_connections.create_connection("openrouter", "Rowan Spare", api_key="sk-spare")
    config.write_config(role_decision_fallback_provider="rowan-spare",
                        role_decision_fallback_model=ACTIVE)
    case = case_mod.BY_ID["decide-continuity-identity"]
    target = _decide_target(case)
    stages = runner.chain(target)
    assert stages == inference.stages(target)
    assert [s.mode for s in stages] == ["native", "structured"]

    ctx = runner.prepare(case)
    recorded = json.loads(_compliant(case))
    parsed = decisions.parse(_compliant(case), ctx["items"], explain=True)
    native = [decisions.ItemResult(r.answers) for r in parsed]
    failure = LLMError("bad_response", "the decisions endpoint sent no answers")
    fake = FakeLLM([[json.dumps({"0": recorded["1"]})]],
                   decisions=[native[0], failure, native[2]])
    result = runner.live(case, target, client=fake)

    assert result.passed, result.error or [(c.name, c.detail) for c in result.failures]
    assert len(fake.native_requests) == 3 and fake.calls == 1
    assert fake.requests[-1]["target"].provider_id == "rowan-spare"
    assert result.note == "backend: native 2, structured 1"


def test_a_partly_failed_batch_says_why_in_its_note(monkeypatch, tmp_path):
    """No fallback to finish it: the item the native endpoint failed stays
    unanswered (`Decision.errors` holds why), and the note carries the cause
    beside the answer check that item fails."""
    from grimoire import decisions
    from grimoire.llm_errors import LLMError
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", DECIDER)
    case = case_mod.BY_ID["decide-continuity-identity"]
    target = _decide_target(case)
    ctx = runner.prepare(case)
    parsed = decisions.parse(_compliant(case), ctx["items"], explain=True)
    native = [decisions.ItemResult(r.answers) for r in parsed]
    failure = LLMError("bad_response", "the decisions endpoint sent no answers")
    fake = FakeLLM([["never sent"]], decisions=[native[0], failure, native[2]])
    result = runner.live(case, target, client=fake)

    assert not result.passed and fake.calls == 0
    assert result.note == ("backend: native; "
                           "failed: bad_response: the decisions endpoint sent no answers")
    assert "the decisions endpoint sent no answers" in runner.report([result])


def test_a_live_only_flag_without_live_is_refused():
    from evals import run
    for flags in (["--decide-backend", "native"], ["--provider", "openrouter"],
                  ["--model", "vendor/both"]):
        with pytest.raises(SystemExit) as exc:
            run.main(flags)
        assert exc.value.code == 2
        with pytest.raises(SystemExit):
            run.main(["--gate", *flags])


def test_live_override_writes_no_settings(monkeypatch, tmp_path):
    """`--provider/--model` runs the case on the named model through the
    reroll's seam, and writes nothing: `config.md`, every connection file and
    every facts file are byte-identical afterwards."""
    from evals import run
    from grimoire.store.inference import facts
    from tests.llm_fakes import FakeLLM

    home = tmp_path / "home"
    _decision_store(monkeypatch, home, BOTH)
    facts.set_overrides("openrouter", DECIDER, {"vision": "no"})
    settings = [home / "config.md", *sorted((home / "llm_connections").iterdir())]
    assert any(p.name.endswith(".facts.json") for p in settings)
    before = {p: p.read_bytes() for p in settings}

    fake = FakeLLM([["never sent"]], decisions=[_over()])
    real_live_all = runner.live_all
    monkeypatch.setattr(runner, "live_all",
                        lambda *a, **k: real_live_all(*a, client=fake, **k))
    code = run.main(["--live", "--provider", "openrouter", "--model", DECIDER,
                     "--case", "decide-scene-break"])

    assert code == 0
    ((_item, conn, _retries),) = fake.native_requests
    assert conn.model == DECIDER          # the override served, natively
    assert {p: p.read_bytes() for p in settings} == before
    assert sorted((home / "llm_connections").iterdir()) == settings[1:]


def test_live_structured_decide_case_sends_the_schema(monkeypatch, tmp_path):
    """F's assertion, through the chain: a structured stage sends the case's
    own prompt and its schema -- the case's prompt and schema ARE what
    production sends for its items."""
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", ACTIVE)
    case = case_mod.BY_ID["decide-scene-break"]
    fake = FakeLLM([[_compliant(case)]])
    result = runner.live(case, _decide_target(case), client=fake)

    assert result.passed, result.error or result.failures
    ctx = runner.prepare(case)
    assert fake.schemas == [case.schema(ctx)]
    assert fake.messages == ctx["messages"]


DECIDE_CASES = [c for c in case_mod.CASES if c.schema is not None]


def test_every_decide_case_is_counted():
    assert {c.id for c in DECIDE_CASES} == {
        "decide-scene-break", "decide-voice-drift", "decide-speaker",
        "decide-continuity-identity", "decide-continuity-reconcile",
        # 01e's vocabulary, on synthetic material (no call site asks it yet).
        "decide-rank", "decide-select"}


@pytest.mark.parametrize("case", DECIDE_CASES, ids=[c.id for c in DECIDE_CASES])
def test_every_decide_case_runs_through_live(monkeypatch, tmp_path, case):
    """Every decide case, F's and G's, runs down the chain: its compliant
    recording, answered by the fake as the structured reply, is parsed by the
    chain, written back by `decisions.render` and still grades PASS."""
    from tests.llm_fakes import FakeLLM

    _decision_store(monkeypatch, tmp_path / "home", ACTIVE)
    target = _decide_target(case)
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "fixture"))
    fake = FakeLLM([[_compliant(case)]])
    result = runner.live(case, target, client=fake)
    assert result.passed, result.error or [(c.name, c.detail) for c in result.failures]
    assert result.note == "backend: structured"
    assert fake.calls == 1


def test_a_mixed_decision_names_each_backend():
    """`Decision.backend` is "" when stages with different backends split the
    batch; the note then counts each one's items, in item order, and leaves
    out an item nothing answered."""
    from grimoire import decisions

    def answered(backend: str) -> decisions.ItemResult:
        return decisions.ItemResult({"q": decisions.Answer(True)}, backend=backend)

    mixed = decisions.Decision(
        items=(answered("native"), answered("structured"), answered("native"),
               decisions.ItemResult({"q": decisions.Answer(None, "error")})),
        backend="")
    assert runner.backend_note(mixed) == "backend: native 2, structured 1"
    # Item order: the backend of the lowest-numbered item is named first,
    # whichever stage answered first.
    fallback_first = decisions.Decision(
        items=(answered("structured"), answered("native")), backend="")
    assert runner.backend_note(fallback_first) == "backend: structured 1, native 1"
    assert runner.backend_note(decisions.Decision(items=(answered("native"),),
                                                  backend="native")) == "backend: native"


def test_a_generate_case_is_sent_with_no_schema(monkeypatch, tmp_path):
    from tests.llm_fakes import FakeLLM

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["scene-length"]
    assert case.schema is None and runner.operation(case) == "generate"
    fake = FakeLLM([["Seraphine Vale shrugs."]])
    runner.live(case, wire_kit.resolution(wire_kit.target(model=""),
                                          case.task), client=fake)
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
    prompt absorb sends for `build_items`' items over what `examine` finds, a
    live run sends that batch's schema, and each counterexample carries a
    failure mode of the duplicate check into the decide shape."""
    from grimoire import decisions, inference
    from grimoire.store.continuity import identity

    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case = case_mod.BY_ID["decide-continuity-identity"]
    assert case.task == "continuity-identity"
    assert {r.variant: r.expect_fail for r in case.recordings} == {
        "compliant": (), "undecodable": ("identity.json",),
        "merged": ("identity.distinct", "identity.continuation"),
        "unknown-id": ("identity.known_ids", "identity.same_obligation"),
        "native": (),
        "native-unknown-id": ("identity.known_ids", "identity.same_obligation"),
        "native-refused": ("identity.covers_rows",)}
    assert {r.variant: r.native for r in case.recordings if r.native} == {
        "native": "openai", "native-unknown-id": "openai", "native-refused": "openai"}
    ctx = runner.prepare(case)
    exam = ctx["exam"]
    items = ctx["items"]
    assert items == identity.build_items(exam.prompt_rows(), exam.live)
    assert len(items) == len(exam.rows) == 3
    assert ctx["messages"] == inference.structured_messages(items, explain=identity.explain())
    assert set(ctx["expected"]) == {"r1", "r2", "r3"}
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema(items, explain=True)
    # The one-call case is retired: the decide case is the only one left.
    assert "continuity-identity" not in case_mod.BY_ID


def test_decide_continuity_reconcile_holds_the_decide_prompt_contract(monkeypatch, tmp_path):
    """The permanent reconciliation decide case: its prompt is the structured
    prompt the sweep sends for `build_items`' items over the payload the case
    builds, a live run sends that batch's schema, and each counterexample
    carries a failure mode of the sweep into the decide shape."""
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
        "timid": ("reconcile.cross_type", "reconcile.close", "reconcile.fulfilled"),
        "native": (), "native-unfounded": ("reconcile.evidence",)}
    assert {r.variant: r.native for r in case.recordings if r.native} == {
        "native": "openrouter", "native-unfounded": "openrouter"}
    ctx = runner.prepare(case)
    payload = ctx["payload"]
    items = ctx["items"]
    assert items == reconcile.build_items(payload)
    assert len(items) == len(payload["candidates"]) == 7
    assert ctx["messages"] == inference.structured_messages(items, explain=reconcile.explain())
    assert set(ctx["expected"]) == {f"c{n}" for n in range(1, 8)}
    assert case.schema is not None
    assert case.schema(ctx) == decisions.schema(items, explain=True)
    # The one-call case is retired: the decide case is the only one left.
    assert "continuity-reconcile" not in case_mod.BY_ID


def _native_recording(case_id: str, variant: str = "native") -> tuple:
    case = case_mod.BY_ID[case_id]
    [recording] = [r for r in case.recordings if r.native and r.variant == variant]
    return case, recording, json.loads(recording.path(case_id).read_text(encoding="utf-8"))


def test_the_native_recordings_answer_only_one_choice_per_dependent_answer(
        monkeypatch, tmp_path):
    """The bug the native recordings exist for: a native endpoint answers each
    question alone, so a direction or an id asked apart from its decision came
    back none. These answer what the items ask and nothing that depends on
    another answer -- no `from`, no `to`, no `id` -- and pass: the direction
    and the record ride in the decision (spec 7.4)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case, recording, bodies = _native_recording("decide-continuity-reconcile")
    for body in bodies:
        assert not {"from", "to"} & set(body["answers"])
    assert [b["answers"]["decision"]["choice"] for b in bodies][1:3] == [
        "continuation_b_of_a", "pays_off_b_to_a"]
    result = runner.replay(case, recording)
    assert result.passed, [(c.name, c.detail) for c in result.failures]
    assert {c.name for c in result.checks} >= {"reconcile.continuation", "reconcile.cross_type"}

    case, recording, bodies = _native_recording("decide-continuity-identity")
    for body in bodies:
        assert [a["name"] for a in body["answers"]] == ["decision"]
    assert bodies[0]["answers"][0]["choice"] == "existing:find-the-ledger"
    assert runner.replay(case, recording).passed


def test_a_native_recording_is_graded_on_the_direction_it_chose(monkeypatch, tmp_path):
    """Replay reads a native body through its adapter and the production
    mapping, so the grader sees the direction the endpoint answered: the
    continuation turned around fails its check, and a row merged into the
    record its folded `existing` names fails the verdict that kept it new."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case, _, bodies = _native_recording("decide-continuity-reconcile")
    bodies[1]["answers"]["decision"]["choice"] = "continuation_a_of_b"
    result = runner.score(case, "native", json.dumps(bodies), "openrouter")
    assert {c.name for c in result.failures} == {"reconcile.continuation"}

    case, _, bodies = _native_recording("decide-continuity-identity")
    bodies[1]["answers"][0]["choice"] = "existing:the-saltmarch-smuggling"
    result = runner.score(case, "native", json.dumps(bodies), "openai")
    assert {c.name for c in result.failures} == {"identity.distinct"}


def test_native_grading_reads_what_the_endpoint_answered(monkeypatch, tmp_path):
    """`decisions.render` writes every unread native answer as null, so read
    back as structured text a native refusal looked answered and an
    unoffered `existing:<id>` looked like a plain null. Replay keeps the
    native results beside the text (`ctx["native_results"]`) and the graders
    read those: the unoffered id fails `known_ids` (from its `stated`
    value), a refusal fails `covers_rows` as the app leaves that row
    unchecked, and an abstention on a reconcile decision fails
    `reconcile.covers`, as the app stores that candidate no proposal."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    case, _, bodies = _native_recording("decide-continuity-identity")
    bodies[0]["answers"][0]["choice"] = "existing:maras-map"
    result = runner.score(case, "native", json.dumps(bodies), "openai")
    assert {c.name for c in result.failures} == {"identity.known_ids",
                                                "identity.same_obligation"}
    assert json.loads(result.output)["0"]["answers"]["decision"] is None
    bodies[0]["answers"][0]["choice"] = "maybe"
    result = runner.score(case, "native", json.dumps(bodies), "openai")
    assert {c.name for c in result.failures} == {"identity.enum", "identity.same_obligation"}
    bodies[0]["answers"] = [{"type": "refusal", "name": "decision"}]
    result = runner.score(case, "native", json.dumps(bodies), "openai")
    assert {c.name for c in result.failures} == {"identity.covers_rows"}
    # The same text graded as a structured reply: the null reads as answered.
    plain = runner.score(case, "structured", result.output)
    assert "identity.covers_rows" not in {c.name for c in plain.failures}

    case, _, bodies = _native_recording("decide-continuity-reconcile")
    decision = bodies[1]["answers"]["decision"]
    del decision["choice"]
    decision["probabilities"] = {"continuation_b_of_a": 0.5, "distinct": 0.5}
    result = runner.score(case, "native", json.dumps(bodies), "openrouter")
    assert {c.name for c in result.failures} == {"reconcile.covers"}
    ctx = runner.prepare(case)
    runner.native_output(ctx, "openrouter", json.dumps(bodies))
    assert ctx["native_results"][1].answers["decision"].reason == "abstained"

