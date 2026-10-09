"""Effective controls for the screens: a sampler preset previewed on a model.

The per-kind decisions all live in one gateway function,
`llm_sampling.effective` (spec 8) -- the same one the facade calls per attempt
and `resolve` stores on each `Attempt.controls`. This module only feeds it what
a screen asks about (a preset, a connection, a model that need not be the
connection's own), lowered exactly as `resolve` lowers an attempt, and adds each
control's capability `source` where a control rests on a capability.
"""

from __future__ import annotations

from ... import llm_sampling
from . import capabilities, resolve

#: The capability a preset control's answer rests on, for the controls that
#: have one; the capability's source then replaces the gateway's own. None of
#: today's ten does -- the nine samplers and `reasoning_effort` are decided by
#: the adapter and the catalog row, which `effective` already names -- so this
#: is empty until a capability-backed control (prefill, structured output)
#: becomes a preset control.
CAPABILITY: dict[str, str] = {}


def preview(preset_id: str, conn: dict, model: str | None, operation: str = "") -> dict:
    """`llm_sampling.effective` for sampler preset `preset_id` on `conn` serving
    `model` (its own model when empty): `{requested, effective, controls}`, each
    control `{state, wire, why, source}`.

    On a decision (`operation` "decide") a model `resolve.native_only` would
    answer natively is sent no sampling, so every control is `n/a`
    (`llm_sampling.not_applicable`) -- the resolver's own rule, so the preview
    and a resolved attempt never disagree. Any other operation previews as
    generation.

    Never raises: an unreadable preset is no preset, and an unreadable catalog
    is a model nothing is known of -- both read by `resolve`'s own lowering.
    """
    lowered = resolve.lower(conn, resolve.preset_sampling(preset_id), model or None)
    if operation == "decide" and resolve.native_only(capabilities.caps_for(lowered)):
        return llm_sampling.not_applicable(lowered, llm_sampling.WHY_NATIVE)
    out = llm_sampling.effective(lowered)
    mapped = {name: cap for name, cap in CAPABILITY.items() if name in out["controls"]}
    if mapped:
        caps = capabilities.caps_for(lowered)
        for name, cap in mapped.items():
            out["controls"][name]["source"] = caps[cap].source
    return out
