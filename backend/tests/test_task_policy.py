"""01d-C1: the per-task policy (`routing.TaskPolicy`), and the rules it is held to.

The policy is code, not settings: `routing.TASK_POLICY` is a table of
literals, so every rule spec 01d §4.3 states is checked here, statically,
over that table. `violations` is the checker; each rule is planted below so
a rule that stopped refusing would fail by name, and a few valid full
policies are planted too, so a checker that refused everything would fail
as well.

The checker reads the store constants each decide call site builds its
item with (`_QUESTION`, `_answers`), which `routing` -- a pure leaf -- cannot
import. 01c-S2 shares the structure (`samples`, `native_first`) and adds its
rules to `_RULES`, including the refusal of `samples` beside `low_margin`.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

from grimoire import adapters, decisions
from grimoire.store import response_protocol, routing, scene_break, voice_drift
from grimoire.store.continuity import identity, reconcile
from grimoire.store.inference import resolve

REPO = Path(__file__).resolve().parents[2]

TaskPolicy = routing.TaskPolicy

#: The deciding question each decide task's call site builds its item with
#: (spec §4.3: "`question` equals the store constant"). Every task on a
#: decide route must be here (`test_every_decide_task_is_pinned`).
_QUESTION: dict[str, str] = {
    "response-selector": response_protocol.SELECTOR_QUESTION,
    "continuity-identity": identity.DECISION_ID,
    "continuity-reconcile": reconcile.DECISION_ID,
    "scene-break": scene_break.QUESTION_ID,
    "voice-drift": voice_drift.QUESTION_ID,
}

#: The tasks whose caller maps `abstained`/`refused` on the deciding question
#: to an outcome of its own (§4.3): the speaker pick's `selection_of` hands
#: control back on a null. The continuity tasks read a decline as not read
#: (`decisions.was_read`), so they may never set `reads_declines`.
_READS_DECLINES = frozenset({"response-selector"})


class _Vocab(NamedTuple):
    """The keys a deciding question can answer, as the record spells them:
    exact keys, and the prefixes of per-row options that cannot be listed."""

    exact: frozenset[str]
    prefixes: frozenset[str] = frozenset()


def _directed(word: str) -> bool:
    """Whether a reconcile word is directed: one `reconcile._offered` offers
    on a pair vocabulary only folded (`duplicate_a_into_b`), never bare."""
    try:
        reconcile.folded(word, "A", "B")
    except KeyError:
        return False
    return True


def _keys(item: decisions.Item, question: str) -> frozenset[str]:
    """The keys `item`'s deciding question can answer, as a record spells
    them (spec 01d §5.1): option ids, `str(level)` for a score, and
    "true"/"false" for a predicate."""
    [asked] = [q for q in item.questions if q.id == question]
    if isinstance(asked, decisions.Choice):
        return frozenset(option.id for option in asked.options)
    if isinstance(asked, decisions.Score):
        return frozenset(str(i) for i in range(len(asked.levels)))
    if isinstance(asked, decisions.Predicate):
        return frozenset({"true", "false"})
    raise AssertionError(f"no answer keys for a {type(asked).__name__}")


#: An identity row as `Examination.prompt_rows` shapes one, offering no
#: candidate: what its builder offers every row, whatever it examines.
_BARE_ROW = {"kind": "thread", "title": "Find the ledger", "beat": "Winifred went looking.",
             "status": "", "commitment_kind": "", "due": "", "quote": "", "speaker": "",
             "certainty": None, "why_new": "", "distinguished_from": [], "candidates": []}


@functools.cache
def _answers() -> dict[str, _Vocab]:
    """Each decide task's answer vocabulary, read off the call site's own item
    builder where one can be built without a store (spec 01d §4.3). Built
    lazily: the builders render templates."""
    [row] = identity.build_items([_BARE_ROW], {})
    return {
        # An empty roster: the one option every pick offers. Each NPC's ref
        # is per scene, so no exact entry can name one.
        "response-selector": _Vocab(_keys(response_protocol.selector_item([], []),
                                          _QUESTION["response-selector"])),
        # A row with no candidates: the words every row is offered.
        # `existing:<id>` is per row, so it is named by its prefix.
        "continuity-identity": _Vocab(_keys(row, _QUESTION["continuity-identity"]),
                                      frozenset({identity.EXISTING_PREFIX})),
        # Restated rather than built: `reconcile.build_items` needs a whole
        # sweep payload (records, chronicle, known scenes). Every word offered
        # bare -- a pair vocabulary's directed words are offered only folded
        # (`reconcile._offered`), and their folded ids pass via `unfolded`.
        "continuity-reconcile": _Vocab(frozenset(
            word for vocab, words in reconcile.DECISIONS.items() for word in words
            if vocab not in reconcile.PAIR_VOCABULARIES or not _directed(word))),
        "scene-break": _Vocab(_keys(scene_break.build_item("", []),
                                    _QUESTION["scene-break"])),
        "voice-drift": _Vocab(_keys(voice_drift.build_item("Seraphine", "anchor", "transcript"),
                                    _QUESTION["voice-drift"])),
    }


def _answerable(task: str, entry: str) -> bool:
    vocab = _answers()[task]
    if entry.endswith(":"):
        return entry in vocab.prefixes
    if entry in vocab.exact:
        return True
    return task == "continuity-reconcile" and reconcile.unfolded(entry) != (entry, "", "")


Rule = Callable[[str, TaskPolicy, routing.Route], list[str]]


def _fallback_known(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if p.fallback not in routing.FALLBACKS:
        return [f"{task}: fallback {p.fallback!r} is not one of {routing.FALLBACKS}"]
    return []


def _escalation_on_decide_only(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if route.operation == "decide":
        return []
    default = TaskPolicy()
    fields = ("escalate_to", "escalate_on", "question", "margins", "escalate_answers",
              "reads_declines")
    set_ = [f for f in fields if getattr(p, f) != getattr(default, f)]
    if set_:
        return [f"{task}: {', '.join(set_)} set on a route whose operation is not decide"]
    return []


def _escalation_complete(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    # `question` alone is allowed: 01c's `samples` needs it too. 01c-S2
    # tightens this to "`question` needs `escalate_to` or `samples`".
    if p.escalate_to:
        out = []
        if not p.escalate_on:
            out.append(f"{task}: escalate_to without escalate_on")
        if not p.question:
            out.append(f"{task}: escalate_to without a question")
        return out
    stale = [f for f in ("escalate_on", "margins", "escalate_answers", "escalate_max",
                         "reads_declines")
             if getattr(p, f) != getattr(TaskPolicy(), f)]
    if stale:
        return [f"{task}: {', '.join(stale)} set without escalate_to (stale)"]
    return []


def _triggers_known(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    out = []
    unknown = [t for t in p.escalate_on if t not in routing.TRIGGERS]
    if unknown:
        out.append(f"{task}: escalate_on names unknown triggers {unknown}")
    if len(set(p.escalate_on)) != len(p.escalate_on):
        out.append(f"{task}: escalate_on repeats a trigger")
    return out


def _escalation_target(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if not p.escalate_to or p.escalate_to == routing.CALLER:
        return []
    if p.escalate_to not in routing.ESCALATION_ROLES:
        return [f"{task}: escalate_to {p.escalate_to!r} is not an escalation role"]
    if p.escalate_to == route.default_role:
        return [(f"{task}: escalate_to is the route's own default role "
                f"{route.default_role!r} (every hop would be skipped same_model)")]
    return []


def _question_pinned(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if route.operation == "decide" and task not in _QUESTION:
        return [(f"{task}: no pinned question -- pin its question in "
                "test_task_policy._QUESTION")]
    if p.question and p.question != _QUESTION.get(task):
        return [(f"{task}: question {p.question!r} is not the call site's "
                f"{_QUESTION.get(task)!r}")]
    return []


def _margins_valid(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    out = []
    low = "low_margin" in p.escalate_on
    if low and not p.margins:
        out.append(f"{task}: low_margin without a margins entry")
    if p.margins and not low:
        out.append(f"{task}: margins set without low_margin")
    kinds = [kind for kind, _ in p.margins]
    if len(set(kinds)) != len(kinds):
        out.append(f"{task}: a margins kind is listed twice")
    for kind, value in p.margins:
        if not adapters.decides_natively(kind):
            out.append(f"{task}: margins kind {kind!r} has no native decisions endpoint")
        if not 0 < value <= routing.MAX_MARGIN:
            out.append(f"{task}: margin {value} for {kind!r} is outside (0, MAX_MARGIN]")
    return out


def _answers_valid(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if not p.escalate_answers:
        return []
    if "low_margin" not in p.escalate_on:
        return [f"{task}: escalate_answers set without low_margin"]
    if task not in _answers():
        return [f"{task}: no answer vocabulary -- add one to test_task_policy._answers"]
    bad = [e for e in p.escalate_answers if not _answerable(task, e)]
    if bad:
        return [f"{task}: escalate_answers {bad} are not keys its question can answer"]
    return []


def _declines_allowed(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if not p.reads_declines or task in _READS_DECLINES:
        return []
    if task.startswith("continuity-"):
        return [(f"{task}: reads_declines on a task that reads a decline as not read "
                "(decisions.was_read)")]
    return [f"{task}: reads_declines on a task whose caller maps no decline to an outcome"]


def _cap_valid(task: str, p: TaskPolicy, route: routing.Route) -> list[str]:
    if not 1 <= p.escalate_max <= decisions.MAX_ITEMS_PER_CALL:
        return [(f"{task}: escalate_max {p.escalate_max} is outside "
                f"[1, {decisions.MAX_ITEMS_PER_CALL}]")]
    return []


_RULES: tuple[Rule, ...] = (
    _fallback_known, _escalation_on_decide_only, _escalation_complete, _triggers_known,
    _escalation_target, _question_pinned, _margins_valid, _answers_valid,
    _declines_allowed, _cap_valid,
)


def violations(table: Mapping[str, TaskPolicy],
               routes: Sequence[routing.Route] = routing.ROUTES) -> list[str]:
    """Every §4.3 rule `table` breaks over `routes`, as sentences naming the
    task; [] for a table the rules accept."""
    by_task = {task: r for r in routes for task in r.tasks}
    out: list[str] = []
    for task, p in table.items():
        route = by_task.get(task)
        if route is None:
            out.append(f"{task}: not a task any route claims")
            continue
        for rule in _RULES:
            out += rule(task, p, route)
    for r in routes:
        fallbacks = {table.get(task, TaskPolicy()).fallback for task in r.tasks}
        if len(fallbacks) > 1:
            out.append(f"{r.key}: its tasks disagree on fallback (for_task hands one's "
                       "resolution to another)")
    return out


# ---- the structure ----
def test_policy_defaults_to_todays_behaviour():
    default = TaskPolicy()
    for task in ("", "no-such-task", "chat"):
        assert routing.policy(task) == default
    assert default.fallback == "role"
    assert default.escalate_to == "" and default.escalate_on == ()
    assert default.question == "" and default.margins == ()
    assert default.escalate_answers == () and default.reads_declines is False
    assert default.escalate_max == decisions.MAX_ITEMS_PER_CALL
    hash(default)


def test_policy_reads_the_table(monkeypatch):
    planted = TaskPolicy(fallback="none")
    monkeypatch.setitem(routing.TASK_POLICY, "absorb", planted)
    assert routing.policy("absorb") is planted
    assert routing.policy("audit") == TaskPolicy()


def test_the_margin_constants():
    assert routing.MAX_MARGIN == 0.5
    assert routing.DEFAULT_MARGIN == 0.2
    assert 0 < routing.DEFAULT_MARGIN <= routing.MAX_MARGIN


# ---- the code table ----
def test_the_code_table_breaks_no_rule():
    assert violations(routing.TASK_POLICY) == []


def test_no_task_escalates_or_drops_its_fallback_at_landing():
    """Every task ships with escalation off and its role's fallback (spec
    §2, §6.4). The change that switches a task on edits this test together
    with its §6.3 evidence in `evals/README.md`."""
    assert routing.TASK_POLICY == {}
    for task, p in routing.TASK_POLICY.items():
        assert p.escalate_to == "", task
        assert p.fallback == "role", task


def test_every_decide_task_is_pinned():
    decide = {task for r in routing.ROUTES if r.operation == "decide" for task in r.tasks}
    assert set(_QUESTION) == set(_answers()) == decide
    assert _QUESTION == {"response-selector": "next", "continuity-identity": "decision",
                         "continuity-reconcile": "decision", "scene-break": "over",
                         "voice-drift": "verdict"}
    assert not _answers()["continuity-reconcile"].exact & {
        "duplicate", "continuation", "subthread", "pays_off"}
    # The builders' vocabularies, as each call site offers them.
    assert _answers()["continuity-identity"].exact == {"new", "uncertain"}
    assert _answers()["response-selector"].exact == {response_protocol.GRIMOIRE_REF}
    assert _answers()["scene-break"].exact == {"true", "false"}
    assert _answers()["voice-drift"].exact == {voice_drift.DRIFT, voice_drift.IN_VOICE,
                                               voice_drift.NOT_ENOUGH}


# ---- the checker accepts what it should ----
_FULL_IDENTITY = TaskPolicy(
    escalate_to="primary", escalate_on=("refused", "abstained", "low_margin"),
    question="decision", margins=(("openrouter", 0.2), ("openai_compatible", 0.5)),
    escalate_answers=("existing:", "new"))


@pytest.mark.parametrize("table", [
    {"continuity-identity": _FULL_IDENTITY},
    {"continuity-reconcile": TaskPolicy(
        escalate_to="fast", escalate_on=("low_margin",), question="decision",
        margins=(("openrouter", routing.DEFAULT_MARGIN),),
        escalate_answers=(reconcile.folded("duplicate", "A", "B"), "distinct"),
        escalate_max=4)},
    {"response-selector": TaskPolicy(escalate_to=routing.CALLER, escalate_on=("refused",),
                                     question="next", reads_declines=True)},
    {"scene-break": TaskPolicy(question="over")},
    {task: TaskPolicy(fallback="none") for task in routing.route_by_key("scene").tasks},
    {task: TaskPolicy(fallback="none") for task in routing.route_by_key("continuity").tasks},
], ids=["identity", "reconcile", "speaker-caller", "question-only", "scene-none",
        "continuity-none"])
def test_a_valid_policy_is_accepted(table):
    assert violations(table) == []


# ---- and refuses each planted violation ----
_DECIDE_PRIMARY = routing.Route("planted", "Planted", "", ("planted-decide",), True,
                                operation="decide", default_role="primary")
_UNPINNED = routing.Route("unpinned", "Unpinned", "", ("unpinned-decide",), True,
                          operation="decide", default_role="decision")


def _escalating(**over) -> TaskPolicy:
    base: dict = {"escalate_to": "primary", "escalate_on": ("refused",),
                  "question": "decision"}
    return TaskPolicy(**{**base, **over})


def _low(task_question: str, **over) -> TaskPolicy:
    base: dict = {"escalate_to": "primary", "escalate_on": ("low_margin",),
                  "question": task_question, "margins": (("openrouter", 0.2),)}
    return TaskPolicy(**{**base, **over})


PLANTED: dict[str, tuple[dict, str]] = {
    "unknown-task": ({"no-such-task": TaskPolicy()}, "not a task any route claims"),
    "fallback-unknown": ({"dossier": TaskPolicy(fallback="never")}, "is not one of"),
    "route-disagrees": ({"chat": TaskPolicy(fallback="none")}, "disagree on fallback"),
    "escalation-on-generate": ({"absorb": _escalating()}, "operation is not decide"),
    "question-on-generate": ({"absorb": TaskPolicy(question="decision")},
                             "operation is not decide"),
    "to-without-on": ({"continuity-identity": _escalating(escalate_on=())},
                      "escalate_to without escalate_on"),
    "to-without-question": ({"continuity-identity": _escalating(question="")},
                            "escalate_to without a question"),
    "on-without-to": ({"scene-break": TaskPolicy(escalate_on=("refused",))},
                      "without escalate_to"),
    "margins-without-to": ({"scene-break": TaskPolicy(margins=(("openrouter", 0.2),))},
                           "without escalate_to"),
    "cap-without-to": ({"scene-break": TaskPolicy(escalate_max=4)}, "without escalate_to"),
    "declines-without-to": ({"response-selector": TaskPolicy(reads_declines=True)},
                            "without escalate_to"),
    "trigger-unknown": ({"continuity-identity": _escalating(escalate_on=("unsure",))},
                        "unknown triggers"),
    "trigger-repeated": ({"continuity-identity": _escalating(
        escalate_on=("refused", "refused"))}, "repeats a trigger"),
    "to-not-a-role": ({"continuity-identity": _escalating(escalate_to="decision")},
                      "is not an escalation role"),
    "question-wrong": ({"scene-break": TaskPolicy(question="verdict")},
                       "is not the call site's"),
    "low-without-margins": ({"scene-break": _low("over", margins=())},
                            "low_margin without a margins entry"),
    "margin-kind-anthropic": ({"scene-break": _low("over", margins=(("anthropic", 0.2),))},
                              "no native decisions endpoint"),
    "margin-kind-claude": ({"scene-break": _low("over", margins=(("claude", 0.2),))},
                           "no native decisions endpoint"),
    "margin-zero": ({"scene-break": _low("over", margins=(("openrouter", 0.0),))},
                    "outside (0, MAX_MARGIN]"),
    "margin-over-cap": ({"scene-break": _low("over", margins=(("openrouter", 0.6),))},
                        "outside (0, MAX_MARGIN]"),
    "margin-kind-twice": ({"scene-break": _low("over", margins=(("openrouter", 0.2),
                                                                 ("openrouter", 0.3)))},
                          "listed twice"),
    "margins-without-low": ({"scene-break": _low("over", escalate_on=("refused",))},
                            "margins set without low_margin"),
    "answers-without-low": ({"voice-drift": _escalating(
        question="verdict", escalate_answers=("drift",))},
        "escalate_answers set without low_margin"),
    "answer-unknown": ({"voice-drift": _low("verdict", escalate_answers=("maybe",))},
                       "are not keys"),
    "prefix-unknown": ({"continuity-identity": _low("decision", escalate_answers=("other:",))},
                       "are not keys"),
    "prefix-elsewhere": ({"continuity-reconcile": _low(
        "decision", escalate_answers=("existing:",))}, "are not keys"),
    "reconcile-unknown": ({"continuity-reconcile": _low(
        "decision", escalate_answers=("rumoured",))}, "are not keys"),
    "reconcile-directed-bare": ({"continuity-reconcile": _low(
        "decision", escalate_answers=("duplicate",))}, "are not keys"),
    "declines-identity": ({"continuity-identity": _escalating(reads_declines=True)},
                          "was_read"),
    "declines-reconcile": ({"continuity-reconcile": _escalating(reads_declines=True)},
                           "was_read"),
    "declines-scene-break": ({"scene-break": _escalating(question="over",
                                                         reads_declines=True)},
                             "maps no decline"),
    "cap-zero": ({"continuity-identity": _escalating(escalate_max=0)}, "escalate_max 0"),
    "cap-over": ({"continuity-identity": _escalating(escalate_max=9)}, "escalate_max 9"),
}


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_each_rule_refuses_its_planted_violation(name):
    table, fragment = PLANTED[name]
    got = violations(table)
    assert len([v for v in got if fragment in v]) == 1, got


def test_escalating_to_the_routes_own_default_role_is_refused(monkeypatch):
    """No decide route defaults to an escalation role today, so the route is
    planted; its task is pinned so only the target rule fires."""
    monkeypatch.setitem(_QUESTION, "planted-decide", "decision")
    routes = (*routing.ROUTES, _DECIDE_PRIMARY)
    got = violations({"planted-decide": _escalating()}, routes)
    assert got == [("planted-decide: escalate_to is the route's own default role "
                   "'primary' (every hop would be skipped same_model)")]


def test_a_decide_task_with_no_pinned_question_is_refused():
    routes = (*routing.ROUTES, _UNPINNED)
    got = violations({"unpinned-decide": TaskPolicy()}, routes)
    assert got == [("unpinned-decide: no pinned question -- pin its question in "
                   "test_task_policy._QUESTION")]


# ---- the Models page knows the policy's reason ----
def test_the_models_page_knows_the_policy_reason():
    """`droppedFallbackWords` words this reason apart (the fallback COULD be
    sent; the policy chooses not to), by comparing the server's sentence. A
    reworded server constant would silently fall back to "cannot be sent"."""
    source = (REPO / "frontend" / "src" / "components" / "inference"
              / "selection.ts").read_text(encoding="utf-8")
    found = re.search(r'export const NO_FALLBACK_POLICY = "(.*?)";', source)
    assert found, "NO_FALLBACK_POLICY is no longer a string literal -- this guard reads it as text"
    assert found.group(1) == resolve.NO_FALLBACK_POLICY
