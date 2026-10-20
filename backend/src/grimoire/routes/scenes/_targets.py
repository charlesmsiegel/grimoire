"""Creation and inspection share one reading of the resolved chat target."""

from __future__ import annotations

from ... import wire
from ...store.inference import resolve as inference
from ...store.inference.resolved import ResolvedInference
from ._router import router as router


def _chat_target(cid: str) -> tuple[ResolvedInference, str]:
    """What chat would run on in campaign `cid`: the resolution (its `chain`
    None when nothing resolves) and the model its primary target
    sends ("" then).

    A display read -- the context view's, and the `model` a new scene is
    stamped with (`post_scene`, `post_scene_import`) -- so it never refuses: a
    keyless or incapable primary is still where chat would go, and the turn
    itself is what reports it."""
    # routing-ok: what chat WOULD run on, shown and stamped on a new scene; never refuses
    resolved = inference.resolve("chat", cid)
    return resolved, _target_model(resolved.chain)


def _primary(chain: wire.Chain | None) -> wire.Target | None:
    """A resolution's primary target, None when nothing resolved."""
    return chain.primary if chain is not None else None


def _target_model(chain: wire.Chain | None) -> str:
    """The model a resolution's primary target sends, "" when nothing
    resolved -- a display fact (`wire.Target.model`, the effective model)."""
    return chain.primary.model if chain is not None else ""


def _target_kind(chain: wire.Chain | None) -> str:
    """The adapter a resolution's primary target is sent through, "" when
    nothing resolved -- a display fact (`wire.Target.kind`)."""
    return chain.primary.kind if chain is not None else ""
