"""Roadmap 02-S5: the play gate harness, offline. A play run is live and paid,
so `make check` holds the HARNESS: it refuses without `--live`, writes
nothing to the real store, seeds each isolate with exactly its
configuration's selections, drains before it harvests, files rows only under
the eval scope with the fixture campaign's id, fails a configuration that
recorded `skipped`, and its leak check reads offline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grimoire import store
from grimoire.store import paths, turn_plan
from tests.llm_fakes import FakeLLM, decision_reply

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import play  # noqa: E402
from evals import run as run_cli  # noqa: E402
from evals.graders import Check  # noqa: E402

SCENE = play.Selection("openrouter", "vendor/writer")
DECIDER = play.Selection("openrouter", "vendor/decider")
_HANDOFF = '\n```handoff\n{"next":null}\n```'


class _Scripted(FakeLLM):
    """Answers by what is asked: the speaker pick, the turn decision, or the
    reply -- whatever order a configuration asks them in."""

    def __init__(self, speaker: str | None = "Mara", stance: str | None = "deflect"):
        super().__init__([["unused"]])
        self.speaker = speaker
        self.stance = stance

    def _next(self, messages, attempt):
        super()._next(messages, attempt)
        text = "\n".join(str(m["content"]) for m in messages)
        if "Choose at most one initial speaker" in text:
            refs = {name: ref for ref, name in self._refs(text)}
            # The pending-decision fixture: the player's call, so control
            # goes back to them.
            pending = "think it over before I decide" in text
            speaker = None if pending or not self.speaker else refs.get(self.speaker)
            return [decision_reply({"next": speaker})]
        if "Character about to reply:" in text:
            return [decision_reply({"stance": self.stance, "disclosure": "hint",
                                    "tension": 1})]
        return ["She looks away." + _HANDOFF]

    @staticmethod
    def _refs(text):
        import re
        return re.findall(r"- (characters:[\w-]+): (\w+)", text)


@pytest.fixture
def real(monkeypatch, tmp_path):
    """The 'real' store: a keyed openrouter provider and nothing else."""
    home = tmp_path / "real"
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    store.llm_connections.update_connection("openrouter", api_key="sk-or-real")
    return home


def _snapshot(home: Path) -> dict[str, bytes]:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*"))
            if p.is_file() and "logs" not in p.parts}


def _run(feature, client, *, repeat=1, fixtures=play.FIXTURES, seen=None):
    configs = play.matrix(feature, [SCENE], [DECIDER])
    selected = play.read_selected(configs)
    real_home = paths.home()

    import contextlib

    @contextlib.contextmanager
    def isolate():
        with run_cli.temp_home():
            yield
            if seen is not None:
                seen.append(play.seeded_providers())

    return play.run_all(feature, configs, isolate, repeat=repeat, selected=selected,
                        real_home=real_home, client=client, fixtures=fixtures)


def test_play_refuses_without_live():
    with pytest.raises(SystemExit) as refused:
        run_cli.main(["--play", "intent", "--scene", "a/b", "--decision", "a/c"])
    assert refused.value.code == 2


def test_play_needs_its_selections():
    with pytest.raises(SystemExit):
        run_cli.main(["--live", "--play", "intent", "--scene", "a/b"])


def test_the_matrices():
    speaker = play.matrix("speaker", [SCENE], [DECIDER])
    assert [(c.setting, c.cutoff) for c in speaker] == [("off", "1/(2n)"), ("on", "1/(2n)"),
                                                        ("on", "0")]
    assert speaker[1].axes() == {"feature": "on", "scene_selection": "openrouter/vendor/writer",
                                 "decision_selection": "openrouter/vendor/decider",
                                 "sampling": "on", "cutoff": "1/(2n)"}
    other = play.Selection("openrouter", "vendor/cheap", "low")
    plan = play.matrix("plan", [SCENE, other], [DECIDER])
    assert [(c.setting, c.scene.model) for c in plan] == [
        ("off", "vendor/writer"), ("plan", "vendor/writer"), ("plan", "vendor/cheap")]
    assert play.Selection.parse("openrouter/vendor/cheap#low") == other


def test_a_run_writes_nothing_to_the_real_store_and_seeds_exactly_its_selections(real):
    before = _snapshot(real)
    seen: list[set[str]] = []
    results, graded = _run("intent", _Scripted(), fixtures=play.FIXTURES[:1], seen=seen)
    assert _snapshot(real) == before
    assert seen and all(providers == {"openrouter"} for providers in seen)
    assert len(results) == 2 and all(r.rows for r in results)
    for result, g in zip(results, graded, strict=True):
        assert all(row["scope"] == "eval" and row["campaign"] == g.run.cid
                   for row in result.rows)
        assert {row["case"] for row in result.rows} == {"play-intent-addressed"}


def test_the_intent_gate_passes_on_a_well_behaved_run(real):
    _results, graded = _run("intent", _Scripted())
    text, passed = play.report(graded)
    assert passed, text
    off = [g for g in graded if not g.run.config.on]
    on = [g for g in graded if g.run.config.on]
    assert all(not play.feature_rows(g.run) for g in off)
    asked = [g for g in on if g.run.speakers and not g.run.fixture.send.get("director")]
    assert asked and all(play.feature_rows(g.run) for g in asked)
    # A typed director note is the player's direction: never asked.
    directed = [g for g in on if g.run.fixture.send.get("director")]
    assert directed and all(not play.feature_rows(g.run) for g in directed)
    # The pending-decision fixture handed back, with the feature off and on.
    handed = [g for g in graded if g.run.fixture.id == "pending-decision"]
    assert handed and all(not g.run.speakers for g in handed)


def test_a_configuration_that_skipped_fails_the_gate(real, monkeypatch):
    from grimoire.routes import character_turns

    async def skipped(*_args, **_kwargs):
        return {"task": "turn-intent", "skipped": "missing_key"}

    monkeypatch.setattr(character_turns, "_turn_plan", skipped)
    _results, graded = _run("intent", _Scripted(), fixtures=play.FIXTURES[:1])
    hard = {c.name: c for c in play.gate(graded)}
    assert not hard["gate.on_is_on"].ok


def test_the_speaker_gate_draws_and_grades_its_picks(real):
    _results, graded = _run("speaker", _Scripted(speaker="Mara"),
                            fixtures=play.FIXTURES[:2])
    on = [g for g in graded if g.run.config.on]
    assert on and all(g.run.picks for g in on)
    assert all(not g.run.picks for g in graded if not g.run.config.on)
    _text, passed = play.report(graded)
    assert passed


def test_a_consistent_turn_regression_fails_the_gate():
    def graded(on, ok):
        conf = play.matrix("intent", [SCENE], [DECIDER])[1 if on else 0]
        run = play.PlayRun(play.FIXTURES[0], conf, 0)
        return play.Graded(run, (Check("turn.authority", ok),))
    verdict = {c.name: c for c in play.gate([graded(False, True), graded(False, True),
                                             graded(True, False), graded(True, False)])}
    assert not verdict["gate.no_turn_regression"].ok
    mixed = {c.name: c for c in play.gate([graded(False, True), graded(True, False),
                                           graded(True, True)])}
    assert mixed["gate.no_turn_regression"].ok


def test_mass_on_intended_reads_the_weights_actually_drawn():
    pick = {"basis": "sampled", "selected": "a", "answer": "a",
            "distribution": [["a", 0.5], ["b", 0.3], ["grimoire", 0.15], ["<none>", 0.05]],
            "eligibility": {"only": None, "exclude": ["<none>"], "cutoff": 1 / 6, "floor": 0.0}}
    assert play.mass_on_intended(pick, "a") == pytest.approx(0.5 / 0.8)
    assert play.mass_on_intended(pick, "grimoire") == 0.0
    plain = {"basis": "answer", "selected": "b"}
    assert play.mass_on_intended(plain, "b") == 1.0 and play.mass_on_intended(plain, "a") == 0.0


#: Recorded replies for the leak check, offline (§6.6): a clean one, and two
#: that repeat the block's wording.
LEAK_RECORDINGS = {
    "clean": "Mara shrugs and turns the question back on Seraphine. \"Where were YOU?\"",
    "names-the-approach": "Mara's approach in this reply is to dodge. \"Ask someone else.\"",
    "quotes-the-stance": ("Mara decides to " + turn_plan.stance_description("deflect")
                          + ". \"The tide was loud.\""),
}


def test_the_leak_check_reads_offline():
    assert play.leaks(LEAK_RECORDINGS["clean"]) == []
    assert play.leaks(LEAK_RECORDINGS["names-the-approach"])
    assert play.leaks(LEAK_RECORDINGS["quotes-the-stance"])
