"""A run's final call, checked (spec 6; 12-C3). Nothing the model names is
taken on its word.

- **No terminal call** -- a last turn that ended in prose -- is an implicit
  `finish_insufficient_evidence` with no reason; the prose is dropped.
- **`propose`** (E2) is rebuilt by the sweep's own rule
  (`reconcile.rebuild`) against the scenes this run READ (`inspected`), and a
  proposal citing a scene outside that set is `uncertain`, mechanically: a
  scene that only appeared in a search list was not read (6.2).
- **`answer`** (E3) keeps only citations of scenes whose text a tool
  returned, and with none left is `insufficient_evidence` (6.3).
- **`finish_evidence`** (E1) keeps only history evidence ids this run was
  handed (6.1); its re-classification for an actor is 12-S6's.
"""

from __future__ import annotations

from dataclasses import dataclass

from ... import tool_calls
from ..continuity import reconcile
from . import state, tools

#: The most evidence items a selection keeps (spec 6.1).
RP_SELECT_MAX = 6
#: The longest answer kept (spec 4.3).
ANSWER_CHARS = 1200
#: The most citations an answer keeps.
ANSWER_CITATIONS = 6
#: The most scenes a proposal cites (`reconcile.EVIDENCE_SCENES`).
PROPOSAL_SCENES = 3
#: The longest note or reason kept from a finishing call.
NOTE_CHARS = 280


@dataclass(frozen=True)
class Final:
    """A checked final call: `state` is `completed`, `no_change` or
    `insufficient_evidence`; `result` what the entry point acts on (a
    proposal, an answer, a selection) or None; `payload` the trace's
    `final` -- the validated fields only."""
    state: str
    result: dict | None
    payload: dict | None


def _text(value: object, most: int) -> str:
    return value.strip()[:most] if isinstance(value, str) else ""


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def scene_id(ref: str) -> str:
    """`scene:<sid>` (or a bare sid, as the sweep spells evidence) as a sid."""
    return ref.removeprefix("scene:")


def settle(run: state.RunState, call: tool_calls.ToolCall | None, *,
           candidate: dict | None = None) -> Final:
    """`call` -- the run's final call, None when it ended in prose -- checked
    for `run`'s mode. `candidate` is E2's `build_payload` candidate, which a
    `propose` is rebuilt against."""
    args = call.arguments or {} if call is not None else {}
    name = call.name if call is not None else ""
    if name not in tools.TERMINAL.get(run.mode, frozenset()) \
            or name == tools.FINISH_INSUFFICIENT:
        reason = _text(args.get("reason"), NOTE_CHARS) if name else ""
        return Final("insufficient_evidence", None, {"reason": reason} if reason else None)
    if name == tools.FINISH_NO_CHANGE:
        return Final("no_change", None, {"reason": _text(args.get("reason"), NOTE_CHARS)})
    if name == tools.PROPOSE:
        if candidate is None:
            raise ValueError("a proposal is rebuilt against the candidate it is about")
        return _proposal(run, args, candidate)
    if name == tools.ANSWER:
        return _answer(run, args)
    return _selection(run, args)


def _proposal(run: state.RunState, args: dict, candidate: dict) -> Final:
    cited = [scene_id(r) for r in _strings(args.get("evidence_scenes"))][:PROPOSAL_SCENES]
    known = set(run.inspected)
    item = {"decision": args.get("decision"), "from": args.get("from"), "to": args.get("to"),
            "reason": args.get("reason"), "evidence_scenes": cited}
    proposal = reconcile.rebuild(item, candidate, known)
    if any(sid not in known for sid in cited):
        # The positive-evidence floor (capstone §11.4): a scene the run never
        # read cannot stand behind a decision, whatever else it cited.
        proposal = {**proposal, "decision": "uncertain", "from": "", "to": "",
                    "relation": "", "status": ""}
    return Final("completed", proposal, dict(proposal))


def _answer(run: state.RunState, args: dict) -> Final:
    kept: list[str] = []
    for ref in _strings(args.get("citations")):
        sid = scene_id(ref)
        if ref.startswith("scene:") and sid in run.inspected and ref not in kept:
            kept.append(ref)
    kept = kept[:ANSWER_CITATIONS]
    text = _text(args.get("text"), ANSWER_CHARS)
    if not kept or not text:
        return Final("insufficient_evidence", None, {"reason": "no citation was read"})
    answer = {"text": text, "citations": kept}
    return Final("completed", answer, dict(answer))


def _selection(run: state.RunState, args: dict) -> Final:
    ids = [i for i in dict.fromkeys(_strings(args.get("ids"))) if i in run.evidence]
    ids = ids[:RP_SELECT_MAX]
    note = _text(args.get("note"), NOTE_CHARS)
    if not ids:
        return Final("insufficient_evidence", None, {"note": note} if note else None)
    return Final("completed", {"ids": ids}, {"ids": ids, "note": note})
