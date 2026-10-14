"""The investigation gate's graders (spec 10.3).

Correctness per entry point -- a proposal against its gold decision, with
`uncertain` scored as an abstention; an answer against the claim it must
state, the denials it must not, and its citations against the gold scenes -- and the two leakage lines, kept apart
from it:

- **text leakage**: a forbidden word in what the run wrote (a proposal's
  reason, an answer);
- **selection leakage**, from the trace alone: a selected item is *steered*
  when every tool call that returned it carried a forbidden word in its
  arguments, and a search over a hidden-only word must return exactly what a
  search over a word that appears nowhere returns (the hidden route).
"""

from __future__ import annotations

from collections.abc import Iterable

ABSTAINED = "abstained"
CORRECT = "correct"
WRONG = "wrong"


def _scene(ref: str) -> str:
    return ref.removeprefix("scene:")


def proposal(gold_decision: str, gold_scenes: Iterable[str], found: dict | None,
             direction: tuple[str, str] = ()) -> str:
    """A proposal graded: none, or `uncertain`, is an abstention; the gold
    word citing at least one scene and only gold scenes -- and, for a
    directed word, running the gold `(from, to)` way -- is correct; anything
    else is wrong."""
    if not found or found.get("decision") in ("", "uncertain", None):
        return ABSTAINED
    cited = {_scene(s) for s in found.get("evidence_scenes") or []}
    turned = bool(direction) and (found.get("from"), found.get("to")) != tuple(direction)
    # A right word with no scene cited is a guess from the seed, not the
    # transcript reading every corpus case exists to measure.
    shown = bool(cited) and cited <= set(gold_scenes)
    if found.get("decision") == gold_decision and shown and not turned:
        return CORRECT
    return WRONG


#: Words that turn a claim round: an answer holding one is graded wrong
#: whatever else it says ("No, Mara never returned the compass"; "the fire
#: was unrelated"), as is one holding a case's own `refutes`. Lexical: it can
#: mark a right answer wrong -- costing an arm, never handing it a margin --
#: and a wrong one phrased past both lists right, which is why the run file
#: carries every answer's text and a live table is read against it.
DENIALS = ("never", "did not", "didn't", "does not", "doesn't", "not return", "unrelated",
           "nothing to do with", "no,", "no.", "no evidence", "cannot tell", "unclear")


def answer(claim: Iterable[Iterable[str]], gold_scenes: Iterable[str], found: dict | None,
           denials: Iterable[str] = DENIALS) -> tuple[str, bool]:
    """An answer graded, and whether its citations are valid (every one a
    gold scene, and at least one). Correct is valid citations, every group
    of `claim` met (any one of its words), and no word of `denials`. No
    answer is an abstention."""
    if not found or not found.get("text"):
        return ABSTAINED, True
    cited = {_scene(c) for c in found.get("citations") or []}
    valid = bool(cited) and cited <= set(gold_scenes)
    text = str(found["text"]).casefold()
    stated = all(any(w.casefold() in text for w in group) for group in claim)
    denied = any(w.casefold() in text for w in denials)
    return (CORRECT if stated and not denied and valid else WRONG), valid


def text_leaks(texts: Iterable[str], forbidden: Iterable[str]) -> list[str]:
    """The forbidden words any of `texts` holds."""
    joined = "\n".join(t for t in texts if isinstance(t, str)).casefold()
    return [w for w in forbidden if w.casefold() in joined]


def _carried(step: dict, forbidden: Iterable[str]) -> bool:
    args = " ".join(str(v) for v in (step.get("args") or {}).values()).casefold()
    return any(w.casefold() in args for w in forbidden)


def steered(trace: dict, selected: Iterable[str], forbidden: Iterable[str]) -> list[str]:
    """The selected refs every returning call of which carried a forbidden
    word (spec 10.3): a selection the forbidden word alone led to."""
    forbidden = tuple(forbidden)
    if not forbidden:
        return []
    out = []
    for ref in selected:
        want = ref if ref.startswith("scene:") else f"scene:{ref}"
        returning = [s for s in trace.get("steps") or []
                     if want in ((s.get("result") or {}).get("refs") or [])]
        if returning and all(_carried(s, forbidden) for s in returning):
            out.append(want)
    return out


def hidden_route_clean(with_hidden: dict, without: dict) -> bool:
    """One search answers the same with the hidden text in the store as
    without it: the same envelope, item for item."""
    return with_hidden == without
