"""A second characterization snapshot: store states the first one never built.

`fixtures/inference_baseline.json` (`tests/inference_baseline.py`) froze how
connections resolved before inference slice A. It never built a GLM connection
with a reasoning effort, a route preset over one, a connection whose post-image
setting disagrees with its catalog, prefill or strict post-processing, or a
legacy embedding choice naming an OpenRouter connection -- and slice C's
migration moves every one of those into model facts, roles and presets.
`fixtures/inference_baseline_c.json` records what the code answered for them at
the base of slice C, before any of its changes, and `test_inference_equivalence_c`
holds every later tree to it.

**The JSON is frozen, like the first fixture's: recorded once, from the slice
base, and never regenerated.** A change that makes the test fail has changed
behaviour, and the fix is in the code.

This module reuses the first fixture's builders and its `observe()` by import
and never edits them; `observe` here wraps that one with what it did not record:

- per task, at the global and the campaign scope, the lowered connection's
  `prefill`, `post_process` and `vision`, the wire share
  `llm_sampling.effective(conn)["effective"]`, and `post_images.capability`;
- the image-description seam's answer (`routes.common.require_inference`), as
  `pass` or `refuse` only: why it refuses may legitimately move (a missing
  capability's source, catalog to user), whether it refuses may not.

Invented connection ids and fake keys only; nothing here describes a real
library.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import llm_reasoning, llm_sampling, routes
from grimoire.store import post_images, routing
from grimoire.store.inference import resolve as inference

from . import inference_baseline as base

FIXTURE = Path(__file__).parent / "fixtures" / "inference_baseline_c.json"

#: A z.ai-shaped endpoint: GLM's own home, and an `openai_compatible` kind.
GLM_URL = "https://api.z.ai/api/paas/v4"


# ---- building blocks ----
def _glm(client: TestClient, effort: str) -> str:
    """An `openai_compatible` GLM connection with `effort` and the "warm"
    preset attached, made the active connection."""
    base._presets()
    conn = base._connection(client, "glm", kind="openai_compatible", base_url=GLM_URL,
                            api_key="sk-test-glm", model="glm-5.3",
                            reasoning_effort=effort, sampler_preset="warm")
    # The point of the state: the legacy effort is one `glm_effort` speaks for.
    raw = store.llm_connections.read_connection_raw(conn)
    assert llm_reasoning.glm_effort(raw["model"], raw["reasoning_effort"]) == effort
    base._config(active_connection_id=conn)
    return conn


def _openrouter_catalog(rows: list[dict]) -> None:
    """A catalog sidecar for the seeded connection, tagged with its CURRENT rev
    (written last, so no later edit can strand it)."""
    rev = store.llm_connections.read_connection_raw("openrouter")["rev"]
    store.llm_connections.set_cached_models("openrouter", rows, rev)


# ---- the states ----
def _glm_reasoning(client: TestClient) -> dict:
    ctx = base._fresh(client)
    _glm(client, "low")
    return ctx


def _glm_max_under_route_preset(client: TestClient) -> dict:
    """GLM at `max`, with `summary` routed to a preset that sets no reasoning:
    the route's preset replaces the connection's, and the legacy effort still
    rides along."""
    ctx = base._fresh(client)
    _glm(client, "max")
    base._config(preset_summary="cold")
    return ctx


def _openrouter_glm_with_effort(client: TestClient) -> dict:
    """An OpenRouter connection on a GLM id with an effort set. Legacy
    OpenRouter never sent the field."""
    ctx = base._fresh(client)
    conn = base._connection(client, "router glm", api_key="sk-test-router-glm",
                            model="z-ai/glm-5.3", reasoning_effort="high")
    base._config(active_connection_id=conn)
    return ctx


def _vision_off(client: TestClient) -> dict:
    """Post images switched off on a model whose catalog says it reads them."""
    ctx = base._fresh(client)
    base._ok(client.put("/api/llm-connections/openrouter", json={"vision": "off"}))
    base._config(send_images="on")
    _openrouter_catalog([{"id": "vendor/active", "vision": True,
                          "params": ["temperature", "top_p"]}])
    return ctx


def _vision_on_catalog_no(client: TestClient) -> dict:
    """Post images switched on for a model whose catalog says it reads none."""
    ctx = base._fresh(client)
    base._ok(client.put("/api/llm-connections/openrouter", json={"vision": "on"}))
    base._config(send_images="on")
    _openrouter_catalog([{"id": "vendor/active", "vision": False,
                          "params": ["temperature", "top_p"]}])
    return ctx


def _prefill_strict(client: TestClient) -> dict:
    ctx = base._fresh(client)
    base._ok(client.put("/api/llm-connections/openrouter",
                        json={"prefill": True, "post_process": "strict"}))
    raw = store.llm_connections.read_connection_raw("openrouter")
    assert raw["prefill"] is True and raw["post_process"] == "strict", raw
    return ctx


def _embed_openrouter_legacy(client: TestClient) -> dict:
    """A legacy embedding choice naming an OpenRouter connection: no
    `/embeddings` route for that kind then, so nothing embeds."""
    ctx = base._fresh(client)
    base._spare(client)
    base._config(embeddings_connection_id="spare", embeddings_model="vendor/embed-small")
    return ctx


#: state name -> a builder that fills a fresh store and returns `{"cid": ...}`.
STATES: dict[str, Callable[[TestClient], dict]] = {
    "glm_reasoning": _glm_reasoning,
    "glm_max_under_route_preset": _glm_max_under_route_preset,
    "openrouter_glm_with_effort": _openrouter_glm_with_effort,
    "vision_off": _vision_off,
    "vision_on_catalog_no": _vision_on_catalog_no,
    "prefill_strict": _prefill_strict,
    "embed_openrouter_legacy": _embed_openrouter_legacy,
}


# ---- what is observed, beyond `base.observe` ----
def _sent(task: str, cid: str) -> dict | None:
    """The primary attempt's model behaviour for `task`, or None when nothing
    resolves. Read off the resolution, not the seam: a refusal for want of a
    key does not change what the connection would send.

    Projected from the attempt's target into the keys the frozen baseline
    recorded off the lowered connection dict. `vision` is the post-image
    preference that dict carried, which was its model's facts' (`facts.of`,
    as the attempt read them -- below format 2, the planner's adopted
    legacy facts), "" when they state none: the same value, read where it
    lives."""
    resolved = inference.resolve(task, cid)
    if not resolved.attempts:
        return None
    attempt = resolved.attempts[0]
    target = attempt.target
    vision = attempt.facts.get("vision")
    return {"prefill": target.prefill, "post_process": target.post_process,
            "vision": vision if isinstance(vision, str) else "",
            "effective": llm_sampling.effective(target)["effective"],
            "post_images": post_images.capability(target)}


def _seam(task: str, cid: str) -> str:
    """Whether the seam serves `task`: "pass" or "refuse", nothing more."""
    try:
        routes.common.require_inference(task, cid)
    except HTTPException:
        return "refuse"
    return "pass"


def extra(ctx: dict) -> dict:
    cid = ctx["cid"]
    return {
        "lowered": {task: {"global": _sent(task, ""), "campaign": _sent(task, cid)}
                    for task in [*sorted(routing.TASK_ROUTE), ""]},
        "image_description": {"global": _seam("image-description", ""),
                              "campaign": _seam("image-description", cid)},
    }


def observe(client: TestClient, ctx: dict) -> dict:
    """`base.observe`, plus `extra`, normalised the same way."""
    out = {**base.observe(client, ctx), "c": base._normalise(extra(ctx), base._revs())}
    leftover = re.search(r"[0-9a-f]{16}", json.dumps(out))
    assert leftover is None, f"an unnormalised rev survives: {leftover.group()}"
    return out


def _snapshot(name: str, home: Path) -> dict:
    with base.client_at(home) as client:
        return observe(client, STATES[name](client))


def main(argv: list[str]) -> int:
    if argv != ["--write"]:
        sys.stderr.write("usage: python -m tests.inference_baseline_c --write\n")
        return 2
    baseline = {}
    for name in STATES:
        with tempfile.TemporaryDirectory() as tmp:
            baseline[name] = _snapshot(name, Path(tmp))
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {FIXTURE}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
