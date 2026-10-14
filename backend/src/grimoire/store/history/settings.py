"""The history keys; off costs no document or transcript reads.

09's four, and 10's two planning keys (`history_plan`, `history_plan_repair`),
which have no effect while `history_recall_depth` is 0."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

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
    return _settings(cfg, depth)


def explicit(depth: int, cfg: dict | None = None) -> Settings:
    """The settings for a caller that names its own `depth` (12's
    `search_history`): the configured budget and threshold, whether or not
    turn recall is on, and never a rerank -- an investigation's own model is
    the one reading the results."""
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise ValueError("an explicit history depth is a positive whole number")
    cfg = config.read_config() if cfg is None else cfg
    return replace(_settings(cfg, depth), rerank=False)


def _settings(cfg: dict, depth: int) -> Settings:
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
