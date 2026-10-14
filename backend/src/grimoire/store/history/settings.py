"""The history keys; off costs no document or transcript reads.

09's four, and 10's two planning keys (`history_plan`, `history_plan_repair`),
which have no effect while `history_recall_depth` is 0."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class Settings:
    depth: int
    budget: int
    threshold: float
    rerank: bool
    #: `history_plan`: "auto" plans on thin or empty coverage; anything else
    #: is "off" (roadmap 10).
    plan: str = "off"
    #: `history_plan_repair`: whether "auto" may take the repair hop.
    repair: bool = True


def integer(value: object, default: int) -> int:
    try:
        return max(int(str(value)), 0)
    except (ValueError, TypeError, OverflowError):
        return default


def read(cfg: dict | None = None) -> Settings | None:
    cfg = config.read_config() if cfg is None else cfg
    depth = integer(cfg.get("history_recall_depth"), 0)
    if not depth:
        return None
    try:
        threshold = float(cfg.get("history_recall_threshold", 0.35))
    except (ValueError, TypeError, OverflowError):
        threshold = 0.35
    if not math.isfinite(threshold) or not -1 <= threshold <= 1:
        threshold = 0.35
    return Settings(
        depth,
        integer(cfg.get("history_recall_budget"), 1200),
        threshold,
        cfg.get("history_recall_rerank") == "on",
        "auto" if cfg.get("history_plan") == "auto" else "off",
        cfg.get("history_plan_repair", "on") != "off",
    )
