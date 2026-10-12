"""The turn intent and turn plan (roadmap 02 §6, §7): closed questions about
HOW the assigned actor answers, decided before their contribution is
composed, and the block that carries the answer into that one prompt.

Pure: it renders templates (so a caller runs it in the threadpool), and owns
no call -- `routes/character_turns` asks the decision, stores its record on
the response, and hands the record back to `store.context.compose_turn` as
`plan`, which renders `block` after the history.

A stance is a way of answering, never a script: it never says WHAT the
character says. Disclosure is about what the character already knows, so it
can make them more or less forthcoming and can never grant knowledge; tension
shapes pacing. The vocabularies are small and closed on purpose, and live in
templates so revising one is a template edit plus a recording update.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple

from .. import decisions, draws, prompts
from . import tokens

#: The intent's one question, and the plan's first.
STANCE_ID = "stance"
STANCES: tuple[str, ...] = (
    "engage",    # answer or go along openly
    "press",     # push, challenge or accuse
    "deflect",   # evade, change the subject, answer another question
    "refuse",    # decline openly
    "deceive",   # say what the character believes untrue
    "withdraw",  # disengage, go quiet, leave the exchange
    "act",       # let an action carry the reply rather than words
)

#: The plan's second question: of what the character already knows, how much
#: they let out. Never drawn (§7.2).
DISCLOSURE_ID = "disclosure"
DISCLOSURES: tuple[str, ...] = ("share", "hint", "withhold")

#: The plan's third question, a three-level score. Never drawn (§7.2).
TENSION_ID = "tension"
TENSION_LEVELS: tuple[str, ...] = ("ease off", "hold steady", "raise the stakes")


#: Tokens of the actor's own card a turn decision reads. Argued structurally:
#: a closed question over a short context answers as well as over a long one,
#: and the item is re-sent per contribution, so its context is paid every
#: time. Tuned against the play gate's adherence grader later, never against
#: a real library.
INTENT_BRIEF_TOKENS = 600


def stance_description(stance: str) -> str:
    return prompts.render("turn_plan/stance.j2", stance=stance).strip()


def disclosure_description(disclosure: str) -> str:
    return prompts.render("turn_plan/disclosure.j2", disclosure=disclosure).strip()


def tension_description(level: int) -> str:
    return prompts.render("turn_plan/tension.j2", level=level).strip()


def block(plan: Mapping | None, name: str) -> str:
    """The intent block for `name`'s contribution, from the response's
    `intent` record, or "" when there is nothing to send: no record, a
    skipped one, or one whose every answer went unread. Only a value the
    vocabulary knows renders -- a hand-edited record naming something else
    sends nothing for that question rather than a guess."""
    if not plan or plan.get("skipped"):
        return ""
    stance = plan.get("stance")
    disclosure = plan.get("disclosure")
    tension = plan.get("tension")
    parts = {
        "stance": stance_description(stance) if stance in STANCES else "",
        "disclosure": disclosure_description(disclosure) if disclosure in DISCLOSURES else "",
        "tension": (tension_description(tension)
                    if isinstance(tension, int) and not isinstance(tension, bool)
                    and 0 <= tension < len(TENSION_LEVELS) else ""),
    }
    if not any(parts.values()):
        return ""
    return prompts.render("scene/turn_intent.j2", name=name, **parts).strip()


def brief_of(card: Mapping) -> str:
    """The actor's own card brief: its description, then its personality --
    the actor's OWN material, which the writer reads too, and nothing about
    anyone else -- clipped to `INTENT_BRIEF_TOKENS` (`clip`)."""
    parts = [str(card.get(key) or "").strip() for key in ("description", "personality")]
    return clip("\n\n".join(p for p in parts if p), INTENT_BRIEF_TOKENS)


def clip(text: str, limit: int) -> str:
    """`text` cut to at most `limit` tokens (`tokens.count_tokens`), at a word
    boundary where one is near: a character guess first, then shrunk until it
    counts under the limit. Never longer than `text`."""
    if limit <= 0:
        return ""
    if tokens.count_tokens(text) <= limit:
        return text
    cut = text[: limit * 4]
    while cut and tokens.count_tokens(cut) > limit:
        cut = cut[: int(len(cut) * 0.9)]
    space = cut.rfind(" ")
    if space > len(cut) // 2:
        cut = cut[:space]
    return cut.rstrip()


def stance_question() -> decisions.Choice:
    """The stance question: a choice over `STANCES`, each described by its
    template, null allowed (no particular stance is a real answer)."""
    return decisions.Choice(
        STANCE_ID, prompts.render("turn_plan/stance_question.j2").strip(),
        tuple(decisions.Option(s, stance_description(s)) for s in STANCES),
        allow_none=True)


def context(name: str, conversation: Sequence[dict], brief: str) -> str:
    """A turn decision item's context (`turn_plan/item.j2`)."""
    return prompts.render("turn_plan/item.j2", name=name, brief=brief,
                          conversation=list(conversation))


def intent_item(name: str, conversation: Sequence[dict], brief: str) -> decisions.Item:
    """The turn intent as one decision item (02-C2b): the actor's name, their
    own `brief` and the observable `conversation` (the speaker pick's window,
    through the prompt-phase regex view) as context, and one question,
    `stance`."""
    return decisions.Item(context(name, conversation, brief), (stance_question(),))


def stance_of(result: decisions.ItemResult) -> tuple[str | None, str]:
    """`(stance, reason)` from the item's result: a stance the vocabulary
    knows with reason "", or None with why -- `abstained` (no particular
    stance, a real answer), `refused`, `unreadable` or `error`. Nothing
    unread is guessed into a stance."""
    answer = result.answers.get(STANCE_ID)
    if answer is None:
        return None, "unreadable"
    if isinstance(answer.answer, str) and answer.answer in STANCES:
        return answer.answer, ""
    return None, answer.reason or "unreadable"


def stance_eligibility() -> draws.Eligibility:
    """The stance draw's one reshaping (§6.5): the none excluded (whether a
    stance applies at all is the model's answer), and a cutoff at half of
    indifference over the `n` stances, the none not counted; no floor."""
    return draws.Eligibility(exclude=(decisions.NONE_KEY,),
                             cutoff=1 / (2 * len(STANCES)))


def drawn_stance(item: decisions.Item, result: decisions.ItemResult,
                 seed: int) -> tuple[str | None, str, dict | None]:
    """`(stance, reason, draw record)` (roadmap 02 §6.5). An answer of None --
    abstained, refused, unreadable, an error -- is never drawn: `stance_of`'s
    reading, with no record. Otherwise the stance is drawn through
    `draws.draw` with `stance_eligibility()`: `sampled` is the drawn stance;
    `answer` (no usable report: always the case on a structured stage) is the
    plain answer; and a cutoff that empties the set leaves the plain answer as
    the stance. The record is 01c's, unchanged, stored with the intent.
    Raises only `draws.draw`'s `ValueError` for misuse."""
    stance, reason = stance_of(result)
    if stance is None:
        return None, reason, None
    question = next(q for q in item.questions if q.id == STANCE_ID)
    drawn = draws.draw(question, result, seed=seed, purpose=STANCE_ID,
                       eligibility=stance_eligibility())
    if drawn.basis in ("sampled", "answer") and drawn.value in STANCES:
        return str(drawn.value), "", drawn.record
    return stance, "", drawn.record


def approach(intent: Mapping | None) -> str:
    """The inspector's line for a response's `intent` (roadmap 02 §6.5),
    without its "Approach:" label: the stance, how it was chosen -- "sampled,
    reported 38%" (the weight the model reported for it, before the draw's
    cutoff and renormalising, so not the odds it was drawn at) or "most
    likely" (the answer itself) -- and, for a plan, the disclosure and
    tension that were read. "" when nothing was read (skipped, abstained, or
    no intent at all)."""
    if not intent or intent.get("stance") not in STANCES:
        return ""
    stance = str(intent["stance"])
    drawn = intent.get("draw") or {}
    how = ""
    if drawn.get("basis") == "sampled":
        weights = {str(key): weight for key, weight in drawn.get("distribution") or ()}
        weight = weights.get(stance)
        how = (f" (sampled, reported {round(weight * 100)}%)"
               if isinstance(weight, (int, float)) else " (sampled)")
    elif drawn.get("basis") == "answer":
        how = " (most likely)"
    parts = [stance + how]
    if intent.get("disclosure") in DISCLOSURES:
        parts.append(str(intent["disclosure"]))
    tension = intent.get("tension")
    if isinstance(tension, int) and not isinstance(tension, bool) \
            and 0 <= tension < len(TENSION_LEVELS):
        parts.append(TENSION_LEVELS[tension])
    return " · ".join(parts)


# --- the turn plan (02-C3) ---------------------------------------------------


def disclosure_question() -> decisions.Choice:
    """Of what the character already knows, how much they let out: a choice
    over `DISCLOSURES`, null allowed. Read as answered, never drawn (§7.2): a
    random reveal of a secret is a continuity event nobody chose."""
    return decisions.Choice(
        DISCLOSURE_ID, prompts.render("turn_plan/disclosure_question.j2").strip(),
        tuple(decisions.Option(d, disclosure_description(d)) for d in DISCLOSURES),
        allow_none=True)


def tension_question() -> decisions.Score:
    """Whether the reply cools, holds or raises the scene: a three-level
    score, read as answered and never drawn (pacing is not a coin flip)."""
    return decisions.Score(TENSION_ID, prompts.render("turn_plan/tension_question.j2").strip(),
                           TENSION_LEVELS)


def plan_item(name: str, conversation: Sequence[dict], brief: str) -> decisions.Item:
    """The turn plan as ONE item over `intent_item`'s context: (stance,
    disclosure, tension), answered together in one call (02-C3)."""
    return decisions.Item(context(name, conversation, brief),
                          (stance_question(), disclosure_question(), tension_question()))


class Extra(NamedTuple):
    """One item another feature rides on the plan's `decide()` call (§7.3,
    the slot 13-C3's NPC Action choice uses). Its `owner` key names it in the
    record; `read` turns its `ItemResult` into what is stored (a draw
    included, which is the owner's to make), and None stores the result's
    capture form (`decisions.outcome`). The plan renders none of it, and it
    is never conditioned on this call's stance: both are answered together."""

    owner: str
    item: decisions.Item
    read: Callable[[decisions.ItemResult], Any] | None = None


def plan_items(plan: decisions.Item, extra: Sequence[Extra] = ()) -> tuple[decisions.Item, ...]:
    """`(plan, *extra items)`: one `decide("turn-plan", ...)` call -- one
    request on a structured stage while they fit one chunk, one request per
    item on a native one. Refuses two extras under one owner key."""
    owners = [e.owner for e in extra]
    if len(set(owners)) != len(owners) or not all(owners):
        raise ValueError(f"extra items need distinct, non-empty owners: {owners}")
    return (plan, *(e.item for e in extra))


def plan_answers(result: decisions.ItemResult) -> dict[str, Any]:
    """The plan's two never-drawn answers as the record stores them: a known
    disclosure (else None) and a tension level index (else None)."""
    disclosure = result.answers.get(DISCLOSURE_ID)
    tension = result.answers.get(TENSION_ID)
    level = tension.answer if tension is not None else None
    return {
        "disclosure": (disclosure.answer if disclosure is not None
                       and disclosure.answer in DISCLOSURES else None),
        "tension": (level if isinstance(level, int) and not isinstance(level, bool)
                    and 0 <= level < len(TENSION_LEVELS) else None),
    }


def extra_records(extra: Sequence[Extra],
                  results: Sequence[decisions.ItemResult]) -> dict[str, Any]:
    """Each extra item's result under its owner's key, as its owner reads it
    (`Extra.read`) or in its capture form -- stored untouched, rendered by
    nobody."""
    out: dict[str, Any] = {}
    for spec, result in zip(extra, results, strict=True):
        out[spec.owner] = (spec.read(result) if spec.read is not None
                           else decisions.outcome("", "", "", [result])["items"][0])
    return out
