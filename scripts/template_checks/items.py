"""The decision items that need no store: the voice-drift judge, the speaker
pick, the turn intent and plan, the decide kits, the history planner and the
evidence-sufficiency check."""

from __future__ import annotations

from grimoire import schemas
from grimoire.store import (
    epistemic_access,
    history_rerank,
    response_protocol,
    turn_plan,
    voice_drift,
)
from grimoire.store.history import model as history_model
from grimoire.store.history import plan as history_plan
from grimoire.store.history import sufficiency as history_sufficiency

from .harness import REPORT, check, render, require_carried
from .prompts import TRANSCRIPT

ANCHOR = "Clipped. Never uses contractions.\nAnswers questions with questions."
# The voice-drift judge is a decision item (slice F), checked with the other
# decide-era templates below. BOTH branches of its optional correction, because
# they are different prompts and only one of them is the ordinary case. The
# scene prompt tells the writer a correction outranks the anchor, so a judge
# that could not see it would flag the model for obeying its instructions --
# the block exists for that, and a verifier that only rendered the empty branch
# would not notice it disappearing.
CORRECTION = "Use contractions; the last scene was too stiff."

#: The voice-drift judge's standard and criteria, carried out of its legacy
#: one-call prompt (the `voice_drift/system.j2` slice F deleted) into the
#: decide-era templates word for word (I8): (fragment, the template it went
#: to). Invent no criterion, and drop none. While the legacy prompt existed, a
#: coverage check held that every sentence of it was inside these fragments,
#: the verdict bullets (`VOICE_DRIFT_OPTIONS`, under their labels) or its
#: reply format, which `decide/system.j2` owns now.
VOICE_DRIFT_CARRIED = (
    (("You are checking one character's dialogue in a played scene against their voice "
      "standard: their voice anchor, as modified by any outstanding correction shown with "
      "it."), "voice_drift/question.j2"),
    (("Judge ONLY how the character sounds. A character may do anything, feel anything, or "
      "change their mind; that is the story, not drift. Drift is unsupported register, "
      "diction, or rhythm — flattening into generic narrator prose, acquiring vocabulary or "
      "formality the standard excludes, violating an explicit enduring speech constraint, or "
      "persistently sounding interchangeable with the rest of the cast. Judge in context: "
      "topic, familiarity, urgency, or emotion can change delivery. Omitting a habitual "
      "catchphrase, using a plain shared answer, or speaking sincerely instead of joking is "
      "not sufficient evidence of drift. Do not turn tendencies or example lines into "
      "compulsory routines."), "voice_drift/question.j2"),
    (("Where a correction is shown, it SUPERSEDES the anchor on expression wherever the two "
      "conflict. Lines obeying it are not drift even where the anchor alone would rule them "
      "out. Neither a correction nor an anchor can require an earlier decision, demand, or "
      "event to recur, or override current facts, player control, or knowledge limits. Do "
      "not issue a corrective that would undo the story to restore a verbal habit."),
     "voice_drift/question.j2"),
    (("Be conservative. If the lines are consistent with that standard, report `in_voice`. "
      "A false alarm costs the next scene a correction it did not need."),
     "voice_drift/question.j2"),
    (("the corrective the model will be given on its next turn: one or two sentences, "
      "addressed to the writer, naming the specific way the voice slipped and what to do "
      "instead."), "voice_drift/explain.j2"),
    ("Quote a short offending line if it helps.", "voice_drift/explain.j2"),
    ("empty unless the verdict is `drift`.", "voice_drift/explain.j2"),
)
#: Each verdict's description, carried out of its legacy bullet word for word
#: after the label, keyed by the verdict it describes: `option.j2` must render
#: exactly this text for exactly this verdict. A description that moved to the
#: wrong verdict inverts the judge, and the gate (parser-only) cannot see it.
VOICE_DRIFT_OPTIONS = {
    voice_drift.DRIFT: "they spoke, and they sounded wrong against that standard.",
    voice_drift.IN_VOICE: ("they spoke enough to judge, and they sounded right against "
                           "that standard."),
    voice_drift.NOT_ENOUGH: (
        "they were silent, or said too little to tell. This is a real answer, not a "
        "fallback: use it whenever you cannot actually hear the voice in this scene. Do "
        "NOT report `in_voice` for a character who barely spoke — saying nothing is not "
        "evidence of sounding right, and a standing correction stays in force until a "
        "scene shows otherwise."),
}

# The speaker pick as a decision item (slice F, spec 7.4), which `_select`
# sends through `decide()`. Both branches of the round's user direction,
# since the context renders its line only when there is one. Its one choice
# is `response_selector_question.j2`'s, over the round's eligible refs (each
# described by its name) and `grimoire` (described by
# `response_selector_grimoire.j2`), with null allowed: null is the hand-back.
SELECTOR_ROSTER = [{"ref": "characters:mara", "name": "Mara"},
                   {"ref": "characters:winifred", "name": "Winifred"}]
SELECTOR_CONVERSATION = [{"speaker": "You", "content": "Winifred, where were you?"},
                         {"speaker": "Mara", "content": "Tell her <nothing> & go."}]

#: The selector's criteria, carried out of its retired one-call prompt into
#: the decide-era templates word for word (I8): (fragment, the template it
#: went to). Invent no criterion, and drop none. Two were deliberately
#: reworded, not dropped (controller ruling, Task 9 fix round 1), because the
#: decide prompt lays the same material out differently: `decide/user.j2`
#: puts the item's context ABOVE its questions, so the transcript is no
#: longer "below", and the roster is the choice's options rather than an
#: "Available NPCs:" listing, so a character is missing "from the options".
SELECTOR_CARRIED = (
    ("Choose at most one initial speaker for the observable conversation above.",
     "scene/response_selector_question.j2"),
    ("Choose a listed NPC reference", "scene/response_selector_question.j2"),
    ("or null to return control to the player.", "scene/response_selector_question.j2"),
    (("An established NPC's actions or physical reactions belong to that NPC, even without "
      "speech."), "scene/response_selector_question.j2"),
    ("Choose their reference for those contributions.",
     "scene/response_selector_question.j2"),
    ("Grimoire does not continue their actions or provide a closing recap.",
     "scene/response_selector_question.j2"),
    (("A character already established in the cast or transcript is not new just because "
      "they are missing from the options."), "scene/response_selector_question.j2"),
    (("Choose null only when nobody has a distinct contribution"),
     "scene/response_selector_question.j2"),
    ("Never invent private knowledge or script reactions.",
     "scene/response_selector_question.j2"),
    ("Observable transcript:", "scene/response_selector_context.j2"),
    ("User direction:", "scene/response_selector_context.j2"),
)
#: What the retired prompt said `grimoire` is chosen for, keyed by the option
#: it describes: `response_selector_grimoire.j2` must render exactly this text,
#: and it must be the `grimoire` option's. The listed NPCs' options are the
#: roster's own, each described by its name.
SELECTOR_OPTIONS = {
    response_protocol.GRIMOIRE_REF: ("RARE narrator intervention: an independent scene event "
                                     "or a genuinely new character's entrance that matters now; "
                                     "never an NPC reaction, atmosphere, or a closing recap"),
}


def _voice_drift_case(corr: str) -> None:
    """One correction branch: the item's context is `user.j2`, the legacy user
    message unchanged; its one choice is `question.j2`'s, each of its three
    options described by `option.j2` for that verdict."""
    label = f"correction={bool(corr)}"
    item = voice_drift.build_item("Seraphine Vale", ANCHOR, TRANSCRIPT, correction=corr)
    check(f"voice drift item context ({label})", item.context,
          render("voice_drift/user.j2", name="Seraphine Vale", anchor=ANCHOR,
                 transcript=TRANSCRIPT, correction=corr))
    REPORT.require(f"voice drift item questions ({label})",
                   [q.id for q in item.questions] == [voice_drift.QUESTION_ID],
                   f"asks {[q.id for q in item.questions]}")
    (choice,) = item.questions
    check(f"voice drift item question ({label})", choice.instructions,
          render("voice_drift/question.j2"))
    REPORT.require(f"voice drift item options ({label})",
                   [o.id for o in choice.options]
                   == [voice_drift.DRIFT, voice_drift.IN_VOICE, voice_drift.NOT_ENOUGH]
                   and not choice.allow_none,
                   f"offers {[o.id for o in choice.options]} (allow_none={choice.allow_none})")
    for opt in choice.options:
        check(f"voice drift option {opt.id} ({label})", opt.description,
              render("voice_drift/option.j2", verdict=opt.id))
        REPORT.require(f"voice drift option {opt.id} described ({label})",
                       bool(opt.description.strip()), "option.j2 rendered nothing")


def check_voice_drift() -> None:
    """The voice-drift judge as a decision item (slice F, spec 7.4), as the absorb
    phase sends it. Both correction branches (`CORRECTION`, above)."""
    for corr in ("", CORRECTION):
        _voice_drift_case(corr)
    check("voice drift explain", voice_drift.explain(), render("voice_drift/explain.j2"))
    require_carried("voice drift", VOICE_DRIFT_CARRIED)
    for verdict, text in VOICE_DRIFT_OPTIONS.items():
        check(f"voice drift option carried ({verdict})",
              text, render("voice_drift/option.j2", verdict=verdict))
    REPORT.require("voice drift options are the item's",
                   list(VOICE_DRIFT_OPTIONS)
                   == [o.id for o in voice_drift.build_item(
                       "Seraphine Vale", ANCHOR, TRANSCRIPT).questions[0].options],
                   f"carried descriptions for {list(VOICE_DRIFT_OPTIONS)}")


def _selector_case(note: str) -> None:
    label = f"note={bool(note)}"
    item = response_protocol.selector_item(SELECTOR_ROSTER, SELECTOR_CONVERSATION, note)
    check(f"selector item context ({label})", item.context,
          render("scene/response_selector_context.j2", conversation=SELECTOR_CONVERSATION,
                 note=note))
    REPORT.require(f"selector item user direction ({label})",
                   ("User direction:" in item.context) == bool(note),
                   "the user direction line does not follow the round's note")
    REPORT.require(f"selector item questions ({label})",
                   [q.id for q in item.questions] == [response_protocol.SELECTOR_QUESTION],
                   f"asks {[q.id for q in item.questions]}")
    (choice,) = item.questions
    check(f"selector item question ({label})", choice.instructions,
          render("scene/response_selector_question.j2"))
    REPORT.require(f"selector item options ({label})",
                   [(o.id, o.description) for o in choice.options[:-1]]
                   == [(e["ref"], e["name"]) for e in SELECTOR_ROSTER]
                   and choice.options[-1].id == response_protocol.GRIMOIRE_REF
                   and choice.allow_none,
                   f"offers {[o.id for o in choice.options]} (allow_none={choice.allow_none})")
    check(f"selector option grimoire ({label})", choice.options[-1].description,
          render("scene/response_selector_grimoire.j2"))


def check_selector() -> None:
    for note in ("", "Let Mara answer first."):
        _selector_case(note)
    require_carried("selector", SELECTOR_CARRIED)
    for ref, text in SELECTOR_OPTIONS.items():
        check(f"selector option carried ({ref})", text,
              render("scene/response_selector_grimoire.j2"))
    REPORT.require("selector options are the item's",
                   list(SELECTOR_OPTIONS)
                   == [o.id for o in response_protocol.selector_item(
                       SELECTOR_ROSTER, SELECTOR_CONVERSATION).questions[0].options][
                           len(SELECTOR_ROSTER):],
                   f"carried descriptions for {list(SELECTOR_OPTIONS)}")

    # A follow-on decision sees only the eligible successors that the backend
    # supplied (the current/used speakers are removed before building the item).
    # The narrator can be excluded entirely, and the choice must still allow null.
    follow = response_protocol.selector_item(
        SELECTOR_ROSTER[1:], SELECTOR_CONVERSATION, follow_on=True, include_grimoire=False)
    (follow_choice,) = follow.questions
    check("follow-on selector question", follow_choice.instructions,
          render("scene/response_selector_follow_on_question.j2"))
    REPORT.require("follow-on options exactly match eligibility",
                   [o.id for o in follow_choice.options] == ["characters:winifred"]
                   and follow_choice.allow_none,
                   "an ineligible speaker or narrator reached follow-on options")


# --- the turn intent (roadmap 02 §6): its item, and the block it sends -------
#
# `turn_plan.intent_item` renders `turn_plan/item.j2` as the item's context
# (its brief line only when there is a brief) and one choice,
# `turn_plan/stance_question.j2`, over `turn_plan.STANCES`, each described by
# `turn_plan/stance.j2`, null allowed. `turn_plan.block` renders
# `scene/turn_intent.j2` from the descriptions of the answers it was handed.

def _intent_case(brief: str) -> None:
    label = f"brief={bool(brief)}"
    item = turn_plan.intent_item("Mara", SELECTOR_CONVERSATION, brief)
    check(f"intent item context ({label})", item.context,
          render("turn_plan/item.j2", name="Mara", brief=brief,
                 conversation=SELECTOR_CONVERSATION))
    REPORT.require(f"intent item brief line ({label})",
                   ("What Mara is like:" in item.context) == bool(brief),
                   "the brief line does not follow the brief")
    (stance,) = item.questions
    check(f"intent item question ({label})", stance.instructions,
          render("turn_plan/stance_question.j2").strip())
    REPORT.require(f"intent item options ({label})",
                   stance.id == turn_plan.STANCE_ID and stance.allow_none
                   and [o.id for o in stance.options] == list(turn_plan.STANCES),
                   f"offers {[o.id for o in stance.options]} (allow_none={stance.allow_none})")
    for option in stance.options:
        check(f"intent option {option.id} ({label})", option.description,
              render("turn_plan/stance.j2", stance=option.id).strip())
        REPORT.require(f"intent option {option.id} described", bool(option.description),
                       "turn_plan/stance.j2 renders nothing for it")


def _part(template: str, plan: dict, key: str, var: str) -> str:
    """One answer's description for the intent block, or "" when the plan
    holds no answer for it."""
    return render(template, **{var: plan[key]}).strip() if key in plan else ""


def check_turn_intent() -> None:
    for brief in ("", "Guarded <and> quick."):
        _intent_case(brief)
    for plan in ({"stance": "deflect"}, {"stance": "press", "disclosure": "hint", "tension": 2},
                 {"disclosure": "withhold"}):
        expected = render("scene/turn_intent.j2", name="Mara",
                          stance=_part("turn_plan/stance.j2", plan, "stance", "stance"),
                          disclosure=_part("turn_plan/disclosure.j2", plan, "disclosure",
                                           "disclosure"),
                          tension=_part("turn_plan/tension.j2", plan, "tension",
                                        "level")).strip()
        check(f"intent block ({sorted(plan)})", turn_plan.block(plan, "Mara"), expected)


def check_turn_plan() -> None:
    """The turn plan (§7.2): the same context, then stance, disclosure (a choice
    over `DISCLOSURES` described by `turn_plan/disclosure.j2`, null allowed)
    and tension (a score over `TENSION_LEVELS`)."""
    plan = turn_plan.plan_item("Mara", SELECTOR_CONVERSATION, "Guarded.")
    check("plan item context", plan.context,
          render("turn_plan/item.j2", name="Mara", brief="Guarded.",
                 conversation=SELECTOR_CONVERSATION))
    REPORT.require("plan item questions",
                   [q.id for q in plan.questions]
                   == [turn_plan.STANCE_ID, turn_plan.DISCLOSURE_ID, turn_plan.TENSION_ID],
                   f"asks {[q.id for q in plan.questions]}")
    _, disclosure, tension = plan.questions
    check("plan disclosure question", disclosure.instructions,
          render("turn_plan/disclosure_question.j2").strip())
    check("plan tension question", tension.instructions,
          render("turn_plan/tension_question.j2").strip())
    REPORT.require("plan disclosure options",
                   [(o.id, o.description) for o in disclosure.options]
                   == [(d, render("turn_plan/disclosure.j2", disclosure=d).strip())
                       for d in turn_plan.DISCLOSURES] and disclosure.allow_none,
                   f"offers {[o.id for o in disclosure.options]}")
    REPORT.require("plan tension levels", tension.levels == turn_plan.TENSION_LEVELS,
                   f"levels {tension.levels}")
    # The decide tool's item context in play (§8.1): the asking actor's name and
    # the observable window, never the writer's whole prompt.
    REPORT.require("tool item context",
                   "Observable transcript:" in render("turn_plan/tool_item.j2", name="Mara",
                                                      conversation=SELECTOR_CONVERSATION)
                   and "Mara" in render("turn_plan/tool_item.j2", name="Mara", conversation=[]),
                   "turn_plan/tool_item.j2 lost its name or its transcript")
    for value in turn_plan.DISCLOSURES:
        REPORT.require(f"disclosure {value} described",
                       bool(render("turn_plan/disclosure.j2", disclosure=value).strip()),
                       "turn_plan/disclosure.j2 renders nothing for it")
    for level in range(len(turn_plan.TENSION_LEVELS)):
        REPORT.require(f"tension {level} described",
                       bool(render("turn_plan/tension.j2", level=level).strip()),
                       "turn_plan/tension.j2 renders nothing for it")


def check_epistemic_access() -> None:
    """The decide kits (roadmap 02-S7): epistemic access."""
    for basis in ((), ("present", "asleep")):
        label = f"basis={bool(basis)}"
        question = epistemic_access.AccessQuestion("characters:mara", "Mara", "ev1",
                                                   "Seraphine <takes> the key.", basis)
        (item,) = epistemic_access.build_items([question])
        check(f"epistemic item context ({label})", item.context,
              render("epistemic_access/item.j2", name="Mara", excerpt=question.excerpt,
                     basis=list(basis)))
        (access,) = item.questions
        check(f"epistemic question ({label})", access.instructions,
              render("epistemic_access/question.j2").strip())
        REPORT.require(f"epistemic options ({label})",
                       [(o.id, o.description) for o in access.options]
                       == [(c, render("epistemic_access/class.j2", access=c).strip())
                           for c in epistemic_access.CLASSES]
                       and access.allow_none and all(o.description for o in access.options),
                       f"offers {[o.id for o in access.options]}")


def check_history_rerank() -> None:
    """The decide kits (roadmap 02-S7): history relevance."""
    for when in ("", "the night of the storm"):
        label = f"when={bool(when)}"
        candidate = history_rerank.Candidate("scene-2", "The ledger <goes> under the pier.", when)
        (item,) = history_rerank.build_items("Where is the ledger?", [candidate])
        check(f"rerank item context ({label})", item.context,
              render("history_rerank/item.j2", query="Where is the ledger?",
                     excerpt=candidate.excerpt, when=when))
        (relevance,) = item.questions
        check(f"rerank question ({label})", relevance.instructions,
              render("history_rerank/question.j2").strip())
        REPORT.require(f"rerank levels ({label})", relevance.levels == history_rerank.LEVELS,
                       f"levels {relevance.levels}")
    ranked = history_rerank.build_items_ranked(
        "Where is the ledger?", [history_rerank.Candidate("a", "x"),
                                 history_rerank.Candidate("b", "y", "spring")])
    check("rerank ranked context", ranked.context,
          render("history_rerank/rank_context.j2", query="Where is the ledger?"))
    (rank,) = ranked.questions
    check("rerank ranked question", rank.instructions,
          render("history_rerank/rank_question.j2").strip())
    check("rerank ranked pointwise", rank.pointwise,
          render("history_rerank/pointwise.j2").strip())


def check_history_plan() -> None:
    """The history planner (roadmap 10-C1): `store/history/plan.build_messages`."""
    for tried in (None, history_plan.Tried(("s1",), ("Who <hid> it?",), ("ledger",))):
        label = f"repair={tried is not None}"
        plan_input = history_plan.PlanInput(
            window="Mara <asks> about the ledger.",
            cast=(history_plan.Record("characters:mara", "mara", "characters"),),
            threads=(history_plan.Record("thread:debt", "The Debt", "thread"),),
            found=(history_plan.Found("The pier", "spring", "Mara hides it."),),
            tried=tried, coverage="thin", tiers=(1,), depth=3)
        plan_schema = history_plan.schema_for(history_plan.offered(plan_input), 3)
        built = history_plan.build_messages(plan_input)
        check(f"history plan system ({label})", built[0]["content"],
              render("history_plan/system.j2", schema=plan_schema, repair=tried is not None,
                     max_subjects=history_plan.MAX_SUBJECTS,
                     max_questions=history_plan.MAX_QUESTIONS,
                     question_chars=history_plan.QUESTION_CHARS,
                     max_terms=history_plan.MAX_TERMS, term_chars=history_plan.TERM_CHARS,
                     depth=3).strip())
        REPORT.require(f"history plan schema in prompt ({label})",
                       schemas.render(plan_schema) in built[0]["content"],
                       "the system prompt lost the rendered schema")
        check(f"history plan user ({label})", built[1]["content"],
              render("history_plan/user.j2", window=plan_input.window,
                     cast=[{"handle": "s1", "name": "mara", "kind": "characters"}], mentioned=[],
                     threads=[{"handle": "s2", "name": "The Debt", "kind": "thread"}], groups=[],
                     found=[{"title": "The pier", "date": "spring",
                             "summary": "Mara hides it."}],
                     tried=None if tried is None else {"subjects": ["s1"],
                                                       "questions": ["Who <hid> it?"],
                                                       "terms": ["ledger"]},
                     coverage="thin", tiers=[1], depth=3).strip())


def check_sufficiency() -> None:
    """The evidence-sufficiency check (roadmap 10-C3): `store/history/sufficiency.item`."""
    for found in ((), (history_model.EvidenceItem(
            history_model.SceneRef("001--a", None), "The pier", "spring",
            summary="Mara <hides> it.",
            excerpts=(history_model.Excerpt((), "Mara: here.\nmore"),)),)):
        label = f"evidence={len(found)}"
        check_item = history_sufficiency.item("Where is the <ledger>?",
                                              history_model.Evidence(items=found))
        check(f"sufficiency item context ({label})", check_item.context,
              render("history_check/item.j2", window="Where is the <ledger>?",
                     evidence=[{"title": "The pier", "date": "spring",
                                "summary": "Mara <hides> it.",
                                "excerpt": "Mara: here."}] if found else []).strip())
        (sufficient_q,) = check_item.questions
        check(f"sufficiency question ({label})", sufficient_q.instructions,
              render("history_check/question.j2").strip())
        REPORT.require(f"sufficiency predicate ({label})",
                       sufficient_q.id == history_sufficiency.QUESTION_ID,
                       f"asks {sufficient_q.id}")


CHECKS = (check_voice_drift, check_selector, check_turn_intent, check_turn_plan,
          check_epistemic_access, check_history_rerank, check_history_plan, check_sufficiency)
