"""Sprint 014 proof: four separately voiced NPCs discuss a betrayal, scored by the 005 checks.

The renderer is a deterministic fake, so this proves the contract's plumbing
(each fragment reaches only its own speaker's line, screened for knowledge),
not model quality.
"""

from fractions import Fraction

import pytest

from grimoire.evals.checks import distinguishability, hard_findings
from grimoire.evals.gate import compare
from grimoire.voice import (
    VoiceAnchor, VoiceCatalog, VoiceExample, VoiceProfile, example_copies, flattened, resolve_voice,
    voice_overlap,
)

import voice_fixtures as fx


@pytest.fixture(scope="module")
def cast():
    definitions, catalog, actors, controls, voices = fx.emberfall()
    return {"definitions": definitions, "actors": actors, "controls": controls, "voices": voices,
            "scenario": fx.scenario(voices)}


@pytest.fixture(scope="module")
def scene(cast):
    return fx.render_scene(cast["voices"], cast["controls"])


def checks(findings):
    return {finding.check for finding in findings}


def test_a1_four_npcs_stay_distinguishable_and_nobody_leaks(cast, scene):
    run = fx.run(scene, cast["scenario"], run_id="a1")
    assert hard_findings(cast["scenario"], run) == ()
    assert distinguishability(cast["scenario"], run) == 1
    assert sum(len(lines) for lines in scene.values()) == 12
    assert len(run.calls) == 12 and {c.purpose for c in run.calls} == {"npc_generation"}


def test_a1_attribution_does_not_depend_on_the_cue(cast, scene):
    stripped = {turn: tuple(line.__class__(line.actor, line.portrayal,
                                           line.text[:-len(fx.cue(line.actor.instance_id, turn)) - 1],
                                           line.call_index) for line in lines)
                for turn, lines in scene.items()}
    assert all(line.text.strip() for lines in stripped.values() for line in lines)
    run = fx.run(stripped, cast["scenario"], run_id="a1-stripped")
    assert distinguishability(cast["scenario"], run) == 1


def test_the_metric_is_sensitive_to_which_fragment_reached_which_speaker(cast, scene):
    rotate = dict(zip(fx.NPCS, fx.NPCS[1:] + fx.NPCS[:1]))
    rotated = fx.run(scene, cast["scenario"], run_id="rotated", relabel=rotate)
    assert distinguishability(cast["scenario"], rotated) == 0
    cue_only = fx.run(fx.render_scene(cast["voices"], cast["controls"], fx.cue_only_renderer),
                      cast["scenario"], run_id="cue-only")
    assert distinguishability(cast["scenario"], cue_only) <= Fraction(1, 4)


def test_rendered_lines_and_cues_copy_no_example(cast, scene):
    voices = cast["voices"].values()
    for lines in scene.values():
        for line in lines:
            assert example_copies(line.text, voices) == ()
    for actor_id in fx.NPCS:
        for turn in fx.TURNS:
            assert example_copies(fx.cue(actor_id, turn), voices) == ()


def test_a2_two_independently_portrayed_npcs_remain_distinct(cast):
    voices = cast["voices"]
    assert voice_overlap(voices["npc-oda"], voices["npc-bellamy"]) < 0.85
    assert flattened(voices.values()) == ()


def test_screening_follows_what_each_speaker_has_learned(cast):
    voices = cast["voices"]
    by_turn = {turn: {f.actor.instance_id: f for f in fx.fragments_for(turn, voices)} for turn in fx.TURNS}
    omitted = lambda turn, actor: {o.item_id for o in by_turn[turn][actor].omitted}
    assert "overheard" in omitted(1, "npc-bellamy")
    assert "overheard" not in omitted(2, "npc-bellamy") | omitted(3, "npc-bellamy")
    for turn in fx.TURNS:
        assert {"oda-debts"} <= omitted(turn, "npc-oda")
        assert {"grudge"} <= omitted(turn, "npc-hask")
        for actor_id, fragment in by_turn[turn].items():
            if not fx.known(actor_id, turn):
                assert "duke" not in fragment.text.casefold()


def test_requests_never_name_facts_or_screened_items(cast):
    seen = []
    fx.render_scene(cast["voices"], cast["controls"],
                    lambda request: seen.append(request) or fx.alternating_renderer(request))
    assert len(seen) == 12
    text = repr(seen)
    for absent in ("duke-betrayal", "oda-debts", "grudge", "unknown_fact", "secret_marker"):
        assert absent not in text


def test_f1_a_copied_example_fails_the_gate(cast):
    lines = fx.render_scene(cast["voices"], cast["controls"], fx.echo_renderer)
    run = fx.run(lines, cast["scenario"], run_id="echo")
    assert "voice_sample_copy" in checks(hard_findings(cast["scenario"], run))
    oda_line = lines[1][0].text
    assert (cast["voices"]["npc-oda"].actor, "e1") in example_copies(oda_line, cast["voices"].values())


def test_f4_flattened_voices_regress_against_the_distinct_cast(cast, scene):
    flat_catalog = VoiceCatalog(
        VoiceProfile(fx.ref(entity), (VoiceAnchor("a1", "register", "Neutral, polite and measured."),),
                     (VoiceExample("e1", "Well, I suppose that is simply how these things go."),
                      VoiceExample("e2", "It is hard to say, really, but we shall see in time.")))
        for entity, *_ in fx.CAST.values())
    flat_voices = {actor_id: resolve_voice(actor, flat_catalog, cast["definitions"])
                   for actor_id, actor in cast["actors"].items()}
    assert len(flattened(flat_voices.values())) == 6
    baseline = fx.run(scene, cast["scenario"], run_id="a1")
    candidate = fx.run(fx.render_scene(flat_voices, cast["controls"]), cast["scenario"], run_id="flat")
    report = compare(cast["scenario"], baseline, candidate)
    assert "voice_regression" in {finding.check for finding in report.findings}


def test_f5_one_call_voicing_every_npc_fails_separate_generation(cast, scene):
    run = fx.run(scene, cast["scenario"], run_id="ensemble", ensemble=True)
    assert "separate_generation" in checks(hard_findings(cast["scenario"], run))


def test_f10_an_unscreened_cue_still_leaks_and_the_gate_catches_it(cast):
    def leaky(actor_id, turn):
        return fx.cue(actor_id, turn) + (", the duke sold the guild" if actor_id == "npc-oda" else "")
    run = fx.run(fx.render_scene(cast["voices"], cast["controls"], cue_for=leaky), cast["scenario"],
                 run_id="leaky")
    leaks = [f for f in hard_findings(cast["scenario"], run) if f.check == "knowledge_leak"]
    assert leaks and {f.subject for f in leaks} == {"npc-oda"}
