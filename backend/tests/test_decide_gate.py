"""The decide gate (spec 7.4, plan ruling 7): offline, a conversion's
structured parse may beat today's on its recorded corpus, and may never lose
an entry to it.

The judge is proven here on a planted conversion built from the decision
contract's own types and a tmp corpus, so what it counts, what it calls a
regression and how it compares outcomes are pinned before any real corpus
exists. The real conversions (`gate.GATES`) arrive one per prepare task; the
parametrized tests below hold each of them to the gate, to its legacy parse
tests' inputs, and to its corpus file.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import sys
import types
from pathlib import Path

import pytest

from grimoire import decisions
from grimoire.store.continuity import identity
from tests.llm_fakes import decision_reply

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evals import gate  # noqa: E402
from evals import legacy as legacy_mod  # noqa: E402
from evals import run as run_mod  # noqa: E402

# --- a planted conversion ---------------------------------------------------

_BREAK = decisions.Predicate("break", "Has the scene reached a natural resting point?")


def _items() -> tuple[decisions.Item, ...]:
    return (decisions.Item("Seraphine Vale leaves the Saltmarch pier.", (_BREAK,)),)


def _legacy(text: str) -> object:
    """A stand-in for today's parse: `yes` or `no`, then the reason after a
    colon; anything else is no break and no reason."""
    word, _, reason = text.partition(":")
    return [word.strip() == "yes", reason.strip()]


def _decide(results: tuple[decisions.ItemResult, ...], entry: gate.Entry) -> object:
    """A stand-in for a call site's mapping: the stored break and reason."""
    (result,) = results
    return [result.answers["break"].answer is True, result.rationale]


def _planted(tmp_path: Path, entries: list[dict], *, cases: tuple[str, ...] = ()) -> gate.Conversion:
    corpus = tmp_path / "planted.json"
    corpus.write_text(json.dumps(entries), encoding="utf-8")
    return gate.Conversion(id="planted", items=_items, explain=True, legacy=_legacy,
                           decide=_decide, corpus=corpus, legacy_cases=cases)


def _yes(reason: str) -> str:
    return json.dumps({"0": {"answers": {"break": True}, "rationale": reason}})


BOTH_RIGHT = {"shape": "plain", "intended": [True, "They left."],
              "legacy": "yes: They left.", "decide": _yes("They left.")}
DECIDE_BEATS = {"shape": "fenced", "intended": [True, "They left."],
                "legacy": "```\nyes: They left.\n```",
                "decide": "```json\n" + _yes("They left.") + "\n```"}


# --- judge ------------------------------------------------------------------

def test_judge_counts_and_passes_when_decide_never_loses(tmp_path):
    result = gate.judge(_planted(tmp_path, [BOTH_RIGHT, DECIDE_BEATS]))
    assert result == gate.GateResult(conversion="planted", entries=2, legacy_right=1,
                                     decide_right=2, regressions=())
    assert result.passed


def test_judge_names_a_regression(tmp_path):
    loses = {"shape": "prose-wrapped", "intended": [True, "They left."],
             "legacy": "yes: They left.", "decide": "I think so."}
    # Wrong on both sides is no regression: the decide side lost nothing.
    both_wrong = {"shape": "garbled", "intended": [True, "They left."],
                  "legacy": "maybe", "decide": "maybe"}
    result = gate.judge(_planted(tmp_path, [BOTH_RIGHT, loses, both_wrong]))
    assert (result.entries, result.legacy_right, result.decide_right) == (3, 2, 1)
    assert len(result.regressions) == 1
    assert "prose-wrapped" in result.regressions[0]
    assert not result.passed


def test_judge_compares_whole_outcomes(tmp_path):
    """The break matches; the stored reason does not. A boolean-only judge
    would call this decide-right."""
    reasonless = {"shape": "no-rationale", "intended": [True, "They left."],
                  "legacy": "yes: They left.",
                  "decide": json.dumps({"0": {"answers": {"break": True}}})}
    result = gate.judge(_planted(tmp_path, [reasonless]))
    assert result.decide_right == 0
    assert len(result.regressions) == 1 and "no-rationale" in result.regressions[0]


def test_outcomes_compare_as_json_values(tmp_path):
    """A mapping that returns a tuple is compared with the corpus's list."""
    conv = _planted(tmp_path, [BOTH_RIGHT])
    as_tuples = gate.Conversion(
        id=conv.id, items=conv.items, explain=conv.explain,
        legacy=lambda text: tuple(_legacy(text)),  # type: ignore[arg-type]
        decide=lambda results, entry: tuple(_decide(results, entry)),  # type: ignore[arg-type]
        corpus=conv.corpus, legacy_cases=())
    assert gate.judge(as_tuples).decide_right == 1


def test_a_boolean_is_not_its_integer(tmp_path):
    """`True == 1` in Python; a stored `1` is not an intended `true`."""
    conv = _planted(tmp_path, [BOTH_RIGHT])
    as_ints = gate.Conversion(
        id=conv.id, items=conv.items, explain=conv.explain, legacy=conv.legacy,
        decide=lambda results, entry: [int(results[0].answers["break"].answer is True),
                                       results[0].rationale],
        corpus=conv.corpus, legacy_cases=())
    assert gate.judge(as_ints).regressions


def test_the_decide_side_reads_aux_through_its_entry(tmp_path):
    """`aux` carries a conversion's extra replies (scene-break's title) to the
    decide mapping."""
    seen = []
    entry = {**BOTH_RIGHT, "aux": {"title": "The Long Night"}}
    conv = _planted(tmp_path, [entry])
    conv = gate.Conversion(
        id=conv.id, items=conv.items, explain=conv.explain, legacy=conv.legacy,
        decide=lambda results, e: seen.append(dict(e.aux)) or _decide(results, e),
        corpus=conv.corpus, legacy_cases=())
    assert gate.judge(conv).passed
    assert seen == [{"title": "The Long Night"}]


@pytest.mark.parametrize("bad", [
    [],                                                    # proves nothing
    [{**BOTH_RIGHT, "extra": 1}],                          # an unknown key
    [{k: v for k, v in BOTH_RIGHT.items() if k != "intended"}],
    [{**BOTH_RIGHT, "legacy": ["yes"]}],                   # a reply is a string
    [{**BOTH_RIGHT, "aux": {"title": 3}}],
    {"0": BOTH_RIGHT},                                     # a list, not an object
    [{**BOTH_RIGHT, "ruling": ""}],                        # a ruling says why
    [{**BOTH_RIGHT, "ruling": ["why"]}],
], ids=["empty", "unknown-key", "missing-key", "non-string-reply", "bad-aux", "not-a-list",
        "empty-ruling", "non-string-ruling"])
def test_load_refuses_a_malformed_corpus(tmp_path, bad):
    corpus = tmp_path / "planted.json"
    corpus.write_text(json.dumps(bad), encoding="utf-8")
    conv = gate.Conversion(id="planted", items=_items, explain=True, legacy=_legacy,
                           decide=_decide, corpus=corpus, legacy_cases=())
    with pytest.raises(ValueError, match=r"planted\.json"):
        gate.load(conv)


def _three_items() -> tuple[decisions.Item, ...]:
    return tuple(decisions.Item(f"Scene {n}.", (_BREAK,)) for n in range(3))


def _batch_decide(results: tuple[decisions.ItemResult, ...], entry: gate.Entry) -> object:
    return [r.answers[q].answer for r in results for q in r.answers]


def _batch_legacy(text: str) -> object:
    return [word.strip() == "yes" for word in text.split(",")]


def _batch(answers: list[bool]) -> str:
    return json.dumps({str(i): {"answers": {"break": a}} for i, a in enumerate(answers)})


def test_judge_scores_a_batch_of_several_items(tmp_path):
    """A conversion of three items is scored whole on both sides, and a
    regression on item 2 alone is reported."""
    ok = {"shape": "all-three", "intended": [True, False, True],
          "legacy": "yes, no, yes", "decide": _batch([True, False, True])}
    loses = {"shape": "second-item", "intended": [False, True, False],
             "legacy": "no, yes, no", "decide": _batch([False, False, False])}
    corpus = tmp_path / "batch.json"
    corpus.write_text(json.dumps([ok, loses]), encoding="utf-8")
    conv = gate.Conversion(id="batch", items=_three_items, explain=False,
                           legacy=_batch_legacy, decide=_batch_decide, corpus=corpus,
                           legacy_cases=())
    result = gate.judge(conv)
    assert (result.entries, result.legacy_right, result.decide_right) == (2, 2, 1)
    assert len(result.regressions) == 1
    assert "second-item" in result.regressions[0]
    assert not result.passed


def test_judge_refuses_a_batch_that_would_not_fit_one_call(tmp_path):
    conv = _planted(tmp_path, [BOTH_RIGHT])
    nine = gate.Conversion(
        id=conv.id, explain=True, legacy=_legacy, decide=_decide, corpus=conv.corpus,
        items=lambda: tuple(decisions.Item(f"Scene {n}.", (_BREAK,)) for n in range(9)),
        legacy_cases=())
    with pytest.raises(ValueError, match=r"planted: .*needs 2 calls"):
        gate.judge(nine)


# --- the real conversions ---------------------------------------------------

GATES = pytest.mark.parametrize("conv", gate.GATES, ids=[c.id for c in gate.GATES])


@GATES
def test_every_gate_passes(conv):
    result = gate.judge(conv)
    assert result.passed, "\n".join(result.regressions)


@GATES
def test_every_legacy_parse_case_is_a_gate_entry(conv):
    """I8: the corpus starts from today's parser tests, so every input those
    tests feed the legacy parser is the legacy reply of some entry. The legacy
    tests are deleted only once this holds."""
    assert conv.legacy_cases, f"{conv.id} names no legacy parse case"
    replies = {entry.legacy for entry in gate.load(conv)}
    missing = [case for case in conv.legacy_cases if case not in replies]
    assert not missing, f"{conv.id}: legacy parse inputs with no gate entry: {missing}"


# --- scene-break ------------------------------------------------------------

def _scene_break() -> gate.Conversion:
    return next(c for c in gate.GATES if c.id == "scene-break")


def test_scene_break_is_gated_on_what_the_call_site_stores():
    """The outcome is `_break_commit`'s triple -- break, reason, and the title
    only on a break -- on both sides, never the boolean alone."""
    conv = _scene_break()
    assert conv.explain
    reply = '{"break": true, "reason": "The ledger changed hands.", "title": "The Long Walk Back"}'
    assert conv.legacy(reply) == [True, "The ledger changed hands.", "The Long Walk Back"]
    assert conv.legacy('{"break": false, "reason": "Not yet.", "title": "Later"}') == [
        False, "Not yet.", ""]
    (item,) = conv.items()
    yes = '{"0": {"answers": {"over": true}, "rationale": "The ledger changed hands."}}'
    entry = gate.Entry("planted", None, "", yes, {"title": '"The Long Walk Back."'})
    parsed = decisions.parse(yes, (item,), explain=True)
    assert conv.decide(parsed, entry) == [True, "The ledger changed hands.",
                                          "The Long Walk Back"]
    no = '{"0": {"answers": {"over": false}, "rationale": "Not yet."}}'
    parsed = decisions.parse(no, (item,), explain=True)
    assert conv.decide(parsed, entry) == [False, "Not yet.", ""]


def test_scene_break_decide_reads_every_entry():
    """Today's parse loses the quoted title; the structured parse loses
    nothing, the decide-only shapes (unwrapped, flattened) included."""
    result = gate.judge(_scene_break())
    assert result.passed, "\n".join(result.regressions)
    assert result.decide_right == result.entries
    shapes = {entry.shape for entry in gate.load(_scene_break())}
    assert {"string-boolean", "truncated", "unwrapped-item", "flattened",
            "no-reason-yes", "quoted-title", "leading-apostrophe",
            "trailing-possessive", "explained-title"} <= shapes


def _with_settle(conv: gate.Conversion, settle) -> gate.Conversion:
    return gate.Conversion(id=conv.id, items=conv.items, explain=conv.explain,
                           legacy=conv.legacy, decide=conv.decide, corpus=conv.corpus,
                           legacy_cases=(), settle=settle)


NO = {"shape": "no", "intended": [False, ""], "legacy": "no",
      "decide": json.dumps({"0": {"answers": {"break": False}, "rationale": ""}})}


def test_a_settle_that_reaches_todays_parse_is_refused(tmp_path):
    """`settle` runs on both sides, so one that binds `evals.legacy` would
    carry today's parse onto the decide side -- directly, through a wrapper,
    or through a closure."""
    conv = _planted(tmp_path, [BOTH_RIGHT, NO])
    copy = legacy_mod.scene_break_parse_output
    for settle in (legacy_mod.scene_break_parse_output,
                   lambda value: legacy_mod.scene_break_parse_output(str(value)) and value,
                   functools.partial(lambda value, parse: value, parse=copy),
                   (lambda: lambda value: copy(str(value)) and value)()):
        with pytest.raises(ValueError, match=r"evals\.legacy"):
            gate.judge(_with_settle(conv, settle))
    assert gate.judge(_with_settle(conv, lambda value: value)).passed


def _reaches_legacy(value):
    return legacy_mod.scene_break_parse_output(str(value)) and value


def _one_further(value):
    return _reaches_legacy(value)


def _through_a_closure():
    parse = _one_further

    def inner(value):
        return parse(value)
    return inner


_PARTIAL_HELPER = functools.partial(lambda value, step: step(value), step=_one_further)


def _through_a_partial(value):
    return _PARTIAL_HELPER(value)


def test_a_settle_that_reaches_todays_parse_at_any_depth_is_refused(tmp_path):
    """CODE-M7: the guard follows what a settle binds to any depth -- a helper
    that calls a helper that calls today's parse, through a closure or a
    partial on the way -- not only the settle's own globals."""
    conv = _planted(tmp_path, [BOTH_RIGHT, NO])
    # Each lambda does a little more than call the helper, as a real settle
    # would, so none is a bare alias of it.
    for settle in (lambda value: _reaches_legacy(value) or value,
                   lambda value: _one_further(value) or value,
                   _through_a_closure(),
                   _through_a_partial):
        with pytest.raises(ValueError, match=r"evals\.legacy"):
            gate.judge(_with_settle(conv, settle))


def test_a_settle_that_merges_what_the_corpus_tells_apart_is_refused(tmp_path):
    """A settle that collapses every outcome to one value would pass any gate
    whose corpus intended that value; it is refused rather than scored."""
    conv = _planted(tmp_path, [BOTH_RIGHT, NO])
    with pytest.raises(ValueError, match="merges"):
        gate.judge(_with_settle(conv, lambda value: [True, "They left."]))
    # Merging into one value is fine where the corpus intends only one.
    alone = _planted(tmp_path, [BOTH_RIGHT])
    assert gate.judge(_with_settle(alone, lambda value: [True, "They left."])).passed
    assert gate.judge(conv).passed


@GATES
def test_every_settle_keeps_the_corpus_apart_and_off_the_legacy_copy(conv):
    """The guards run inside `judge`; here each real conversion meets them."""
    assert legacy_mod.__name__ not in gate._homes(conv.settle)
    gate.judge(conv)


# --- voice drift ------------------------------------------------------------

def _voice_drift() -> gate.Conversion:
    return next(c for c in gate.GATES if c.id == "voice-drift")


def test_voice_drift_is_gated_on_what_the_call_site_stores():
    """The outcome is what `_stage_voice_drift` stores or reports: the failed
    check's reason when one fails, else the verdict with its note. Both sides
    go through the production `check_failure` -- the call-site mapping, not a
    parser -- while today's parse itself stays the frozen copy."""
    conv = _voice_drift()
    assert conv.explain
    assert conv.legacy is legacy_mod.voice_drift_parse_output

    def legacy_outcome(text: str) -> object:
        return conv.settle(conv.legacy(text))

    assert legacy_outcome('{"verdict": "in_voice", "note": "a little terse"}') == [
        "in_voice", "a little terse"]
    assert legacy_outcome('{"verdict": "drift"}') == ["failed",
                                                      "drift reported with no corrective"]
    assert legacy_outcome("no idea") == ["failed", "unreadable verdict from the voice judge"]
    (item,) = conv.items()
    entry = gate.Entry("planted", None, "", "")
    drift = '{"0": {"answers": {"verdict": "drift"}, "rationale": "She used contractions."}}'
    parsed = decisions.parse(drift, (item,), explain=True)
    assert conv.settle(conv.decide(parsed, entry)) == ["drift", "She used contractions."]
    parsed = decisions.parse('{"0": {"answers": {"verdict": "drift"}}}', (item,),
                                explain=True)
    assert conv.settle(conv.decide(parsed, entry)) == [
        "failed", "drift reported with no corrective"]


def test_voice_drift_decide_reads_every_entry():
    """Today's parse loses only the doubled space; the structured parse loses
    nothing -- the unwrapped and
    flattened shapes included, and a reply in today's format, whose `note`
    is the corrective the user wants kept -- and a note over the cap fails
    the check on both sides."""
    conv = _voice_drift()
    result = gate.judge(conv)
    assert result.passed, "\n".join(result.regressions)
    assert result.decide_right == result.entries
    assert result.legacy_right == result.entries - 1
    entries = {entry.shape: entry for entry in gate.load(conv)}
    assert {"flattened", "unwrapped-item", "double-space", "long-note",
            "todays-format"} <= set(entries)
    # A reply in today's format to the decide prompt is the drift it reports,
    # with its note as the corrective: the flattened shape reads `note` as the
    # rationale, so nothing the judge said is lost for its spelling.
    assert entries["todays-format"].intended == ["drift", "She used contractions."]
    assert entries["double-space"].intended == ["in_voice", ""]
    assert entries["long-note"].intended[0] == "failed"


def test_settle_maps_both_sides_through_one_function(tmp_path):
    """A conversion's `settle` is the call site's mapping from either side's
    parsed value to its outcome, applied to both, so it cannot favour one."""
    seen = []

    def settle(value: object) -> object:
        seen.append(value)
        return value

    conv = _planted(tmp_path, [BOTH_RIGHT])
    settled = gate.Conversion(id=conv.id, items=conv.items, explain=conv.explain,
                              legacy=conv.legacy, decide=conv.decide, corpus=conv.corpus,
                              legacy_cases=(), settle=settle)
    assert gate.judge(settled).passed
    assert seen == [[True, "They left."], [True, "They left."]]
    flipped = gate.Conversion(id=conv.id, items=conv.items, explain=conv.explain,
                              legacy=conv.legacy, decide=conv.decide, corpus=conv.corpus,
                              legacy_cases=(), settle=lambda value: [not value[0], value[1]])
    result = gate.judge(flipped)
    assert (result.legacy_right, result.decide_right) == (0, 0)


# --- speaker ----------------------------------------------------------------

def _speaker() -> gate.Conversion:
    return next(c for c in gate.GATES if c.id == "speaker")


def test_speaker_is_gated_on_what_the_call_site_stores():
    """The outcome is the `(next, issue)` pair `_select` hands `_round_state`:
    today through the frozen `selector_parse` over the round's eligible refs,
    after the switch through the production `selection_of`. Both of today's
    issue strings survive, and a null hands control back with no issue."""
    from grimoire.store import response_protocol as rp

    conv = _speaker()
    assert not conv.explain
    assert _from_legacy(conv.legacy)
    assert conv.legacy('{"next":"characters:mara"}') == ("characters:mara", None)
    assert conv.legacy('{"next":"grimoire"}') == ("grimoire", None)
    assert conv.legacy('{"next":null}') == (None, None)
    assert conv.legacy('{"next":"absent"}') == (None, rp.INELIGIBLE)
    assert conv.legacy("not json") == (None, rp.INVALID_HANDOFF)
    (item,) = conv.items()
    (choice,) = item.questions
    assert choice.allow_none
    assert [o.id for o in choice.options] == ["characters:mara", "characters:winifred",
                                              "grimoire"]
    entry = gate.Entry("planted", None, "", "")
    for reply, want in (('{"0": {"answers": {"next": "characters:winifred"}}}',
                         ("characters:winifred", None)),
                        ('{"0": {"answers": {"next": null}}}', (None, None)),
                        ('{"0": {"answers": {"next": "absent"}}}', (None, rp.INELIGIBLE)),
                        ("not json", (None, rp.INVALID_HANDOFF))):
        parsed = decisions.parse(reply, (item,), explain=False)
        assert conv.decide(parsed, entry) == want, reply


def test_speaker_decide_reads_every_entry():
    """Today's parse loses the fenced, the prose-wrapped, the extra-key and
    the case-folded replies, each of which names a listed speaker; the
    structured parse loses nothing, and agrees with today's on every issue it
    raises. The case-folded win is `decisions.normalise` reaching a ref: a
    widening the switch brings, recorded here so it is not a surprise."""
    conv = _speaker()
    result = gate.judge(conv)
    assert result.passed, "\n".join(result.regressions)
    assert result.decide_right == result.entries
    assert result.legacy_right == result.entries - 4
    entries = {entry.shape: entry for entry in gate.load(conv)}
    assert {"grimoire", "fenced", "prose-wrapped", "extra-key", "flattened",
            "unwrapped-item", "non-string", "truncated", "case-folded"} <= set(entries)
    for shape in ("fenced", "prose-wrapped", "extra-key", "case-folded"):
        assert conv.legacy(entries[shape].legacy) != tuple(entries[shape].intended), shape
    assert entries["non-string"].intended == [None, "ineligible or repeated speaker"]
    assert entries["truncated"].intended == [None, "missing or invalid handoff"]


def test_speaker_near_twin_out_of_the_roster_resolves_to_the_listed_ref():
    """The edge of the case-folded widening, pinned as it is: normalisation is
    refused only between refs the round offers, so a reply naming a near-twin
    that is NOT offered (sitting out, or never cast) resolves to the listed
    ref it normalises onto, where today's exact match says ineligible.

    Store ids cannot form such a pair: `slugify` mints only lowercase letters,
    digits and single hyphens, on which `normalise` is one-to-one -- the test
    below holds that for every pair of slugs it can make from one stem. It
    takes a hand-made id (`safe_id` accepts `mara_vale`)."""
    from grimoire.store import paths
    from grimoire.store import response_protocol as rp

    roster = [{"ref": "characters:mara-vale", "name": "Mara Vale"},
              {"ref": "characters:winifred", "name": "Winifred"}]
    item = rp.selector_item(roster, [{"speaker": "You", "content": "Mara?"}])
    reply = '{"0": {"answers": {"next": "characters:mara_vale"}}}'
    (parsed,) = decisions.parse(reply, (item,), explain=False)
    assert rp.selection_of(parsed) == ("characters:mara-vale", None)
    assert legacy_mod.selector_parse('{"next":"characters:mara_vale"}', roster) == (
        None, rp.INELIGIBLE)
    assert paths.safe_id("mara_vale")
    slugs = {paths.slugify(name) for name in ("Mara Vale", "Mara-Vale", "mara_vale",
                                              "MARA  VALE", "Mara--Vale", "maravale")}
    assert len({decisions.normalise(slug) for slug in slugs}) == len(slugs)
    assert all(slug == slug.lower() and "_" not in slug and " " not in slug
               for slug in slugs)


# --- continuity identity ---------------------------------------------------

def _identity_gate() -> gate.Conversion:
    return next(c for c in gate.GATES if c.id == "continuity-identity")


def test_continuity_identity_is_gated_on_what_the_call_site_stores():
    """The outcome is whether `take` decided, and each examined row's
    decision, status, reason and target once `Examination.decide` has run:
    today through the frozen parse, after the switch through the production
    `answers_of`. Both sides are settled by the production `take` on a fresh
    in-memory examination, so the acceptance guard is the same code."""
    from grimoire.store.continuity import identity

    conv = _identity_gate()
    assert conv.explain
    assert _from_legacy(conv.legacy)
    items = conv.items()
    assert len(items) == 3
    # One question per row: each `existing` folded with the candidate it names.
    assert [[q.id for q in item.questions] for item in items] == [["decision"]] * 3
    assert [[o.id for o in item.questions[0].options] for item in items] == [
        ["existing:find-the-ledger", "existing:maras-map", "existing:the-burned-chart",
         "new", "uncertain"],
        ["existing:find-the-ledger", "existing:winifreds-chart", "new", "uncertain"],
        ["existing:the-midnight-deadline", "new", "uncertain"]]
    assert conv.settle(conv.legacy("I think so.")) == [
        False, [["unchecked", "hint_only", identity.UNREADABLE, None]] * 3]
    entry = gate.Entry("planted", None, "", "")
    reply = json.dumps({"0": {"answers": {"decision": "existing:the-old-map"},
                              "rationale": "Mara's map is the old map."}})
    parsed = decisions.parse(reply, items, explain=True)
    assert conv.settle(conv.decide(parsed, entry)) == [True, [
        ["existing", "accepted", "Mara's map is the old map.", "maras-map"],
        ["unchecked", "hint_only", identity.NO_ANSWER, None],
        ["unchecked", "hint_only", identity.NO_ANSWER, None]]]


def test_continuity_identity_decide_reads_every_entry():
    """Today's parse loses only what the slice settled by ruling: a cased or
    spaced spelling of an offered id, a reply in today's format to the
    decide prompt, and -- since the id is folded into the decision -- an
    ``existing`` naming an unoffered record or none, which is a word outside
    the options rather than a downgraded id. The structured parse loses
    nothing -- an item it never reached stays unchecked, and a repeated key
    keeps its first value."""
    conv = _identity_gate()
    result = gate.judge(conv)
    assert result.passed, "\n".join(result.regressions)
    assert result.decide_right == result.entries
    ruled = [e for e in gate.load(conv) if e.ruling]
    assert result.legacy_right == result.entries - len(ruled) == result.entries - 5
    entries = {entry.shape: entry for entry in gate.load(conv)}
    for shape in ("unoffered-id", "other-kind-ref", "no-id"):
        assert entries[shape].intended[1][0][:2] == ["uncertain", "accepted"], shape
    assert {"one-item-unreadable", "normalised-id", "todays-format", "alias-source",
            "ref-form", "closed-candidate", "explicit-target", "second-move",
            "no-id", "sole-candidate", "unoffered-id", "other-kind-ref"} <= set(entries)
    assert entries["todays-format"].intended[0] is True
    assert {row[0] for row in entries["todays-format"].intended[1]} == {"unchecked"}
    assert [row[0] for row in entries["one-item-unreadable"].intended[1]] == [
        "new", "unchecked", "new"]
    fenced = next(e for e in entries.values() if e.legacy.startswith("Here you go:"))
    assert fenced.decide.count('"0":') == 2


# --- continuity reconcile --------------------------------------------------

def _reconcile_gate() -> gate.Conversion:
    return next(c for c in gate.GATES if c.id == "continuity-reconcile")


def test_continuity_reconcile_is_gated_on_the_proposals_the_sweep_stores():
    """The outcome is the proposals dict persist 2 is handed, or None for a
    failed run: today through the frozen parse, after the switch through the
    production `proposals_of`. The fixture holds one candidate per
    vocabulary, and the thread closure shows all four scenes, so one item
    asks three evidence questions and has a fourth scene to leave out."""
    from grimoire.store.continuity import reconcile

    conv = _reconcile_gate()
    assert conv.explain
    assert _from_legacy(conv.legacy)
    payload = gate._RECONCILE_PAYLOAD
    items = conv.items()
    assert [c["vocabulary"] for c in payload["candidates"]] == [
        "same_thread", "same_commitment", "cross", "temporal", "thread", "commitment"]
    assert len(items) == 6
    closure = items[4]
    assert len(reconcile.item_scenes(payload, payload["candidates"][4])) == 4
    assert [q.id for q in closure.questions if q.id in reconcile.EVIDENCE_IDS] == list(
        reconcile.EVIDENCE_IDS)
    assert conv.settle(conv.legacy("I think so.")) is None
    entry = gate.Entry("planted", None, "", "")
    parsed = decisions.parse(json.dumps({"4": {"answers": {
        "decision": "close", "evidence_scene": "0001--saltmarch-docks"}}}), items,
        explain=True)
    assert conv.settle(conv.decide(parsed, entry)) == {payload["candidates"][4]["id"]: {
        "decision": "close", "from": "", "to": "", "relation": "", "status": "closed",
        "reason": "", "evidence_scenes": ["0001--saltmarch-docks"]}}


def test_continuity_reconcile_decide_reads_every_entry():
    """Today's parse loses only what the slice settled by ruling: a status
    verdict without a rationale, a fourth cited scene, a scene the item does
    not show, a reply in today's format to the decide prompt, and a reply
    naming a candidate we did not send -- whose decide twin carries an index
    past the batch, which leaves the whole reply unread (slice F), real
    verdicts beside it included -- and a bare ``pays_off``, which the folded
    choice reads as the one way it runs. The structured parse loses nothing
    -- a candidate it never reached gets no proposal, and a repeated key
    keeps its first value. Every directed twin answers one folded option, so
    no twin asks a direction apart from its decision."""
    conv = _reconcile_gate()
    result = gate.judge(conv)
    assert result.passed, "\n".join(result.regressions)
    assert result.decide_right == result.entries
    ruled = [e for e in gate.load(conv) if e.ruling]
    assert result.legacy_right == result.entries - len(ruled) == result.entries - 8
    entries = {entry.shape: entry for entry in gate.load(conv)}
    assert {"todays-format", "two-scenes-cited", "four-scenes-cited",
            "evidence-from-another-candidate", "one-item-unreadable", "null-decision",
            "related-without-letters", "closure-without-rationale",
            "unknown-keys-and-repeated-candidate", "repeated-candidate",
            "stray-index-beside-real-answers", "pays-off-without-letters"} <= set(entries)
    assert entries["todays-format"].intended == {}
    assert entries["unknown-keys-and-repeated-candidate"].intended == {}
    # The stray index's price, printed with something to lose: today's parse
    # keeps two real verdicts, decide neither.
    stray = entries["stray-index-beside-real-answers"]
    assert stray.intended == {}
    assert sorted(p["decision"] for p in conv.settle(conv.legacy(stray.legacy)).values()) == [
        "close", "duplicate"]
    # The repeated key, apart from the foreign indices: its first value stands.
    assert [p["decision"] for p in entries["repeated-candidate"].intended.values()] == [
        "uncertain"]
    assert len(entries["four-scenes-cited"].intended[
        gate._RECONCILE_PAYLOAD["candidates"][4]["id"]]["evidence_scenes"]) == 3
    unread = entries["one-item-unreadable"].intended
    assert gate._RECONCILE_PAYLOAD["candidates"][1]["id"] not in unread and len(unread) == 5
    for entry in entries.values():
        if entry.shape != "todays-format":
            assert '"from"' not in entry.decide and '"to"' not in entry.decide, entry.shape
    assert '"decision": "continuation_b_of_a"' in entries["continuation-b-to-a"].decide


def test_the_reconcile_sources_are_gate_entries_with_their_twins():
    """Each deleted parse test's reply, adapted through its own maps, is the
    legacy side of an entry whose decide side is its twin: the checkable form
    of "every deleted parse input is a gate entry" (ruling 11, M11)."""
    conv = _reconcile_gate()
    pairs = {(entry.legacy, entry.decide) for entry in gate.load(conv)}
    assert len(gate.RECONCILE_SOURCES) >= 30
    for source, elements, keys, scenes in gate.RECONCILE_SOURCES:
        adapted = gate._adapt(elements, keys, scenes)
        assert adapted in conv.legacy_cases, source
        assert (adapted, gate._twin(elements, keys, scenes)) in pairs, source


def test_reconcile_adapt_refuses_a_map_that_merges_two_candidates():
    element = {"candidate": "c1", "decision": "close"}
    with pytest.raises(ValueError, match="same fixture"):
        gate._adapt([element], {"c1": "c5", "c2": "c5"}, {})
    with pytest.raises(ValueError, match="same fixture"):
        gate._adapt([element], {"c1": "c5"}, {"s0": "0001--saltmarch-docks",
                                              "s1": "0001--saltmarch-docks"})


# --- the legacy side is the frozen copy ------------------------------------

def test_legacy_imports_nothing_from_grimoire():
    """The frozen parsers stay frozen only while nothing in production can
    reach into them: `evals/legacy.py` imports the standard library alone."""
    tree = ast.parse(Path(legacy_mod.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add("." * node.level + (node.module or ""))
    assert imported <= {"__future__", "json", "re"}, sorted(imported)


#: The production modules today's parsers live in. A conversion's `legacy`
#: reaching any of them would measure the decide side against code the switch
#: tasks edit and then delete.
_PRODUCTION = frozenset({"grimoire.store.scene_break", "grimoire.store.voice_drift",
                         "grimoire.store.response_protocol",
                         "grimoire.routes.character_turns",
                         "grimoire.store.continuity.identity",
                         "grimoire.store.continuity.reconcile",
                         "grimoire.store.absorb.parse"})


def _names(code: types.CodeType) -> set[str]:
    out = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            out |= _names(const)
    return out


def _from_legacy(fn: object) -> bool:
    """Whether `fn` is an `evals.legacy` function, or a wrapper (a partial, a
    lambda, a local adapter) that calls through `evals.legacy` and binds no
    production parser module or function."""
    while isinstance(fn, functools.partial):
        fn = fn.func
    if getattr(fn, "__module__", "") == legacy_mod.__name__:
        return True
    code, scope = getattr(fn, "__code__", None), getattr(fn, "__globals__", {})
    if code is None:
        return False
    bound = [scope[n] for n in _names(code) if n in scope]
    if fn.__closure__:  # type: ignore[attr-defined]
        bound += [cell.cell_contents for cell in fn.__closure__]  # type: ignore[attr-defined]

    def home(value: object) -> str:
        return (value.__name__ if isinstance(value, types.ModuleType)
                else getattr(value, "__module__", "") or "")

    homes = {home(value) for value in bound}
    return legacy_mod.__name__ in homes and not homes & _PRODUCTION


def test_the_legacy_source_check_bites():
    # A production parser, bound directly or through a wrapper, is refused.
    # Scene-break's left with the switch (Task 6); `parse_title` is a production
    # parser of the same module that stays.
    from grimoire.store import scene_break
    assert _from_legacy(legacy_mod.scene_break_parse_output)
    assert _from_legacy(functools.partial(legacy_mod.selector_parse, eligible=[]))
    assert _from_legacy(lambda text: legacy_mod.scene_break_parse_output(text)["break"])
    assert not _from_legacy(scene_break.parse_title)
    assert not _from_legacy(lambda text: scene_break.parse_title(text) or None)
    # Calling through the frozen copy does not excuse a production binding beside it.
    assert not _from_legacy(lambda text: (legacy_mod.scene_break_parse_output(text),
                                          scene_break.parse_title(text)))
    assert not _from_legacy(_legacy)  # a planted stand-in reaches no copy at all


@GATES
def test_every_conversion_parses_today_through_the_frozen_copy(conv):
    assert _from_legacy(conv.legacy), (
        f"{conv.id}: `legacy` must call evals/legacy.py, never a production parser")


def test_every_corpus_file_belongs_to_a_gate():
    """Each conversion's corpus is `evals/gate/<id>.json`, and nothing else
    lives there: a renamed conversion would otherwise leave a corpus scoring
    nothing."""
    for conv in gate.GATES:
        assert conv.corpus == gate.GATE_DIR / f"{conv.id}.json", conv.id
    assert len({c.id for c in gate.GATES}) == len(gate.GATES)
    claimed = {c.corpus.name for c in gate.GATES}
    on_disk = ({p.name for p in gate.GATE_DIR.iterdir() if p.is_file()}
               if gate.GATE_DIR.is_dir() else set())
    assert on_disk == claimed, f"orphaned: {sorted(on_disk - claimed)}"


# --- the CLI ----------------------------------------------------------------

def test_run_gate_flag_exits_zero_when_all_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gate, "GATES", (_planted(tmp_path, [BOTH_RIGHT, DECIDE_BEATS]),))
    assert run_mod.main(["--gate"]) == 0
    out = capsys.readouterr().out
    assert "planted: legacy 1/2, decide 2/2 -- PASS" in out


def test_run_gate_flag_is_offline_and_isolated(tmp_path, monkeypatch, capsys):
    """`--gate` constructs no client, and judges each conversion in a
    throwaway store rather than the user's real one."""
    from grimoire import llm

    def no_client(*args, **kwargs):
        raise AssertionError("--gate constructed an LLMClient")

    monkeypatch.setattr(llm, "LLMClient", no_client)
    real = tmp_path / "real-store"
    monkeypatch.setenv("GRIMOIRE_HOME", str(real))
    homes: list[str] = []

    def items() -> tuple[decisions.Item, ...]:
        homes.append(os.environ["GRIMOIRE_HOME"])
        return _items()

    conv = _planted(tmp_path, [BOTH_RIGHT, DECIDE_BEATS])
    planted = gate.Conversion(id=conv.id, items=items, explain=conv.explain,
                              legacy=conv.legacy, decide=conv.decide,
                              corpus=conv.corpus, legacy_cases=())
    monkeypatch.setattr(gate, "GATES", (planted,))
    assert run_mod.main(["--gate"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert len(homes) == 1 and homes[0] != str(real)
    assert os.environ["GRIMOIRE_HOME"] == str(real)
    assert not real.exists()


def test_run_gate_flag_exits_nonzero_on_a_regression(tmp_path, monkeypatch, capsys):
    loses = {"shape": "prose-wrapped", "intended": [True, "They left."],
             "legacy": "yes: They left.", "decide": "I think so."}
    monkeypatch.setattr(gate, "GATES", (_planted(tmp_path, [BOTH_RIGHT, loses]),))
    assert run_mod.main(["--gate"]) == 1
    out = capsys.readouterr().out
    assert "planted: legacy 2/2, decide 1/2 -- FAIL" in out
    assert "prose-wrapped" in out


def test_run_gate_flag_prints_each_entry_settled_by_ruling(tmp_path, monkeypatch, capsys):
    """SPEC-M-5 / CODE-M6: an entry whose `intended` is a deliberate change of
    behaviour says so (`ruling`), and the report prints it -- so a legacy loss
    by ruling is visible beside the score, never folded into it unseen."""
    ruled = {**DECIDE_BEATS, "ruling": "A fenced reply is read now, by design."}
    monkeypatch.setattr(gate, "GATES", (_planted(tmp_path, [BOTH_RIGHT, ruled]),))
    assert run_mod.main(["--gate"]) == 0
    out = capsys.readouterr().out
    assert "planted: legacy 1/2, decide 2/2 -- PASS" in out
    assert ("    ruling: entry 1 (fenced), legacy loses: "
            "A fenced reply is read now, by design.") in out


def test_the_corpora_mark_their_deliberate_changes():
    """Each entry the slice settled by ruling carries its reason; the report
    prints every one."""
    ruled = {(conv.id, entry.shape) for conv in gate.GATES for entry in gate.load(conv)
             if entry.ruling}
    assert ruled == {("scene-break", "multi-line"), ("scene-break", "quoted-title"),
                     ("voice-drift", "todays-format"), ("speaker", "case-folded"),
                     ("continuity-identity", "normalised-id"),
                     ("continuity-identity", "todays-format"),
                     ("continuity-identity", "unoffered-id"),
                     ("continuity-identity", "other-kind-ref"),
                     ("continuity-identity", "no-id"),
                     ("continuity-reconcile", "closure-without-rationale"),
                     ("continuity-reconcile", "closure-without-rationale-spaces"),
                     ("continuity-reconcile", "four-scenes-cited"),
                     ("continuity-reconcile", "evidence-from-another-candidate"),
                     ("continuity-reconcile", "todays-format"),
                     ("continuity-reconcile", "unknown-keys-and-repeated-candidate"),
                     ("continuity-reconcile", "stray-index-beside-real-answers"),
                     ("continuity-reconcile", "pays-off-without-letters")}
    printed = gate.report([gate.judge(conv) for conv in gate.GATES])
    assert printed.count("ruling: ") == len(ruled)
    assert "ruling: entry 6 (multi-line), legacy loses: " in printed


def test_run_gate_flag_with_no_conversions_says_so(monkeypatch, capsys):
    monkeypatch.setattr(gate, "GATES", ())
    assert run_mod.main(["--gate"]) == 0
    assert "no conversions" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [["--live"], ["--live", "--record"], ["--record"],
                                   ["--case", "roll-fence"]],
                         ids=["live", "record", "record-alone", "case"])
def test_gate_refuses_live_record_and_case(extra, capsys):
    """The gate is offline and runs every conversion: anything that would
    spend money or narrow it is an argparse error, before anything runs."""
    with pytest.raises(SystemExit) as exc:
        run_mod.main(["--gate", *extra])
    assert exc.value.code == 2
    assert "--gate" in capsys.readouterr().err


@pytest.mark.parametrize("spelled", [
    "existing: find-the-ledger", "EXISTING: find-the-ledger ", "existing:  find-the-ledger",
    " existing: thread:find-the-ledger ", "Existing: Find-The-Ledger"])
def test_existing_with_a_space_after_the_colon_names_the_offered_id(spelled):
    """Off strict mode (a prompt-only re-send, a fallback without the mode, a
    server that ignores `response_format`), "existing:" followed by the id is
    as naturally written with a space as without, and both name the record:
    the spaced spelling is an alias of the option, so the row merges as the
    unspaced one does, where it used to read as no option and fall to
    ``uncertain``."""
    items = gate._identity_items()
    exam = gate._identity_exam()
    reply = decision_reply({"decision": spelled}, {"decision": "new"}, {"decision": "new"})
    results = decisions.parse(reply, items, explain=True)
    assert results[0].answers[identity.DECISION_ID].answer == "existing:find-the-ledger"
    answers = identity.answers_of(exam.prompt_rows(), results)
    assert answers[0]["decision"] == "existing" and answers[0]["id"] == "find-the-ledger"
    assert identity.take(exam, answers)
    assert (exam.rows[0].decision, exam.rows[0].status, exam.rows[0].target) == (
        "existing", "accepted", "find-the-ledger")

