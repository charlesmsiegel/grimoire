"""The investigation gate's graders (spec 10.3).

Correctness per entry point -- a proposal against its gold decision, with
`uncertain` scored as an abstention; an answer against its needles and its
citations against the gold scenes -- and the two leakage lines, kept apart
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


def proposal(gold_decision: str, gold_scenes: Iterable[str], found: dict | None) -> str:
    """A proposal graded: none, or `uncertain`, is an abstention; the gold
    word citing only gold scenes is correct; anything else is wrong."""
    if not found or found.get("decision") in ("", "uncertain", None):
        return ABSTAINED
    cited = {_scene(s) for s in found.get("evidence_scenes") or []}
    if found.get("decision") == gold_decision and cited <= set(gold_scenes):
        return CORRECT
    return WRONG


def answer(needles: Iterable[str], gold_scenes: Iterable[str], found: dict | None
           ) -> tuple[str, bool]:
    """An answer graded, and whether its citations are valid (every one a
    gold scene). No answer is an abstention."""
    if not found or not found.get("text"):
        return ABSTAINED, True
    cited = {_scene(c) for c in found.get("citations") or []}
    valid = bool(cited) and cited <= set(gold_scenes)
    text = str(found["text"]).casefold()
    hit = any(n.casefold() in text for n in needles)
    return (CORRECT if hit and valid else WRONG), valid


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


def hidden_route_clean(hidden: dict, nowhere: dict) -> bool:
    """A search over a hidden-only word answers exactly as a search over a
    word that appears nowhere: the same envelope, item for item."""
    return hidden == nowhere
