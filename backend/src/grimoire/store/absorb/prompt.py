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
                 steering_snapshot: str | None = None) -> list[dict]:
    return [{"role": "system", "content": prompts.render("absorb/system.j2")},
            {"role": "user", "content": prompts.render(
                "absorb/user.j2", facts=facts, state_snapshot=state_snapshot,
                rel_snapshot=rel_snapshot, plot_snapshot=plot_snapshot,
                group_snapshot=group_snapshot,
                commitment_snapshot=commitment_snapshot,
                fact_snapshot=fact_snapshot,
                steering_snapshot=steering_snapshot, transcript=transcript)}]
