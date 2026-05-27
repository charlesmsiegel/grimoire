"""Request objects for orchestrator internal APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.types.state import StateDelta


@dataclass(frozen=True)
class ExtractionRequest:
    campaign_id: str
    turn_id: str
    scene_id: str
    post_text: str
    pc_ref: str | None
    extract_mode: Any  # ExtractionMode enum
    composition: Any  # Composition
    context_snapshot: Any | None = None
    mechanics_module: str | None = None


@dataclass(frozen=True)
class DeltaApplyRequest:
    campaign_id: str
    turn_id: str
    scene_id: str
    pc_ref: str | None
    deltas: list[StateDelta]
    composition: Any  # Composition
