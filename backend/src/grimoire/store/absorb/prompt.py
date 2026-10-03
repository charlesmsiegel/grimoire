"""The extraction prompt: the system/user message pair the absorb call sends.

Prompt text lives in templates/absorb/; this file only assembles the two
messages around the snapshots `snapshots.py` renders.
"""

from __future__ import annotations

from ... import prompts


def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None, plot_snapshot: str | None = None,
                 group_snapshot: str | None = None,
                 commitment_snapshot: str | None = None,
                 fact_snapshot: str | None = None,
                 steering_snapshot: str | None = None,
                 tracked_snapshot: list | None = None) -> list[dict]:
    """`tracked_snapshot` is the scene tracker's final state as the narrator
    reads it (`tracker.view.lines_for` with no viewer): every value, private
    ones marked. Empty or None renders nothing, so a scene the tracker never
    saw reads exactly as it did before there was a tracker."""
    return [{"role": "system",
             "content": prompts.render("absorb/system.j2",
                                       steering=bool(steering_snapshot))},
            {"role": "user", "content": prompts.render(
                "absorb/user.j2", facts=facts, state_snapshot=state_snapshot,
                rel_snapshot=rel_snapshot, plot_snapshot=plot_snapshot,
                group_snapshot=group_snapshot,
                commitment_snapshot=commitment_snapshot,
                fact_snapshot=fact_snapshot,
                steering_snapshot=steering_snapshot,
                tracked_snapshot=tracked_snapshot or [], transcript=transcript)}]
