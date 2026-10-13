"""The four history keys; off costs no document or transcript reads."""

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
    )
