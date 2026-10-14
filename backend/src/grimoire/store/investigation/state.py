"""What one investigation run holds between its tool calls (spec 4.1).

Bound by the run, never by the model: the campaign, the mode and the
perspective are set when the run starts, and no tool takes an argument that
changes them. The model only ever names refs and words.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..history import model as history_model

#: The modes a run can be in. `rp_actor` is 12-S6's and is not offered yet.
MODES: tuple[str, ...] = ("rp_narrator", "continuity", "question")


#: The key a step carries its loop call's id under while the run is live.
CALL_KEY = "_call"


@dataclass
class RunState:
    """One run's state. `inspected` holds the scene ids whose transcript text
    (or summary) a tool returned -- the evidence floor a proposal or an
    answer is checked against (spec 6.2, 6.3); a scene that only appeared in a
    search list is not in it. `evidence` maps the run-local ids
    `search_history`, `get_scene_summary` and `get_scene_excerpt` handed the
    model to 09's `EvidenceItem`s, for a selection (spec 6.1). `steps` is the
    trace's step list (`trace.step`). `embed_client` is the app's async
    embeddings client, for `search_history`'s semantic stage (None: lexical
    and structural only).

    Tools may run on the tool pool's threads, so the shared collections are
    written under `lock`. `freeze` ends the writing: a tool thread the loop
    stopped waiting on can still finish after the run is checked, and what it
    would note -- a step, a scene read -- was never delivered to the model,
    so it is dropped rather than let into the check or the trace."""
    cid: str
    mode: str
    sid: str = ""
    perspective: str = history_model.NARRATOR
    embed_client: object | None = None
    inspected: set[str] = field(default_factory=set)
    evidence: dict[str, history_model.EvidenceItem] = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    frozen: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"an investigation mode is one of {', '.join(MODES)}: {self.mode!r}")
        if not self.cid:
            raise ValueError("an investigation runs in a campaign")

    def inspect(self, sid: str) -> None:
        with self.lock:
            if not self.frozen:
                self.inspected.add(sid)

    def freeze(self) -> None:
        """No tool writes after this; the caller's own `note` still lands
        (`final=True`), for the terminal step it adds after the check."""
        with self.lock:
            self.frozen = True

    def hold(self, item: history_model.EvidenceItem) -> str:
        """`item` held under a fresh run-local id (`e1`, `e2`...), the id the
        model is shown; an item already held keeps its id."""
        with self.lock:
            for key, held in self.evidence.items():
                if held.scene.key == item.scene.key and held.evidence_ids == item.evidence_ids:
                    return key
            key = f"e{len(self.evidence) + 1}"
            self.evidence[key] = item
            return key

    def note(self, step: dict, *, final: bool = False, call_id: str = "") -> None:
        """`step` appended, tagged with the loop call it records (`call_id`,
        the `ToolContext`'s) so the run can keep only steps of calls the loop
        delivered; `trace.build` drops the tag."""
        with self.lock:
            if final or not self.frozen:
                self.steps.append({**step, CALL_KEY: call_id} if call_id else step)
