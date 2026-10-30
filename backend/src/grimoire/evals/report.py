"""Plain-text gate report. It cites ids only; it never prints contribution or marker text."""

from __future__ import annotations

from fractions import Fraction

from .checks import Scorecard
from .gate import GateReport

SYNTHETIC_NOTICE = ("no regression against a synthetic baseline — not a cost/quality "
                    "certification")
#: "measured" is what the run file says about itself. Until measured runs carry
#: provider-ledger evidence (sprint 015), the report must not present them as verified.
SELF_DECLARED_NOTICE = ("provenance \"measured\" is self-declared by the run file; no provider "
                        "usage ledger backs it yet")


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, Fraction):
        return f"{float(value):.3f}" if value.denominator != 1 else str(value.numerator)
    return str(value)


def _mapping(values) -> str:
    return ", ".join(f"{key}={_fmt(value)}" for key, value in values.items()) or "none"


def _rows(card: Scorecard) -> list[tuple[str, str]]:
    return [
        ("provenance", card.provenance),
        ("NPC lines", _fmt(card.npc_lines)),
        ("leak rate (NPC lines)", _fmt(card.leak_rate)),
        ("choices negated by label", _fmt(card.negated_by_label)),
        ("choices negated by evidence", _fmt(card.negated_by_evidence)),
        ("choices unrealized", _fmt(card.unrealized)),
        ("cast distinguishability", _fmt(card.distinguishability)),
        ("calls by purpose", _mapping(card.calls_by_purpose)),
        ("uncached input tokens", _fmt(card.uncached_input)),
        ("cached input tokens", _fmt(card.cached_input)),
        ("output tokens", _fmt(card.output)),
        ("weighted billed tokens", _fmt(card.weighted_cost)),
        ("cost by purpose", _mapping(card.cost_by_purpose)),
        ("cost by feature flag", _mapping(card.cost_by_flag)),
        ("total call latency ms", _fmt(card.latency_ms)),
        ("judge scores (informational)", _mapping(card.judge_scores)),
    ]


def render(report: GateReport) -> str:
    lines = [f"story-quality gate: {'PASS' if report.passed else 'FAIL'}",
             f"baseline: {report.baseline_id}", f"candidate: {report.candidate_id}"]
    if report.passed and report.synthetic:
        lines.append(f"note: {SYNTHETIC_NOTICE}")
    elif report.synthetic:
        lines.append("note: at least one run is synthetic")
    if "measured" in (report.baseline.provenance, report.candidate.provenance):
        lines.append(f"note: {SELF_DECLARED_NOTICE}")
    loosened = report.policy.non_defaults()
    if loosened:
        lines.append(f"policy (non-default): {_mapping(loosened)}")
    lines.append("")
    lines.append(f"findings ({len(report.findings)}):")
    for finding in report.findings:
        where = " ".join(part for part in (
            f"turn {finding.turn}" if finding.turn is not None else "",
            finding.subject or "", f"[{finding.evidence}]" if finding.evidence else "") if part)
        lines.append(f"  - {finding.check} ({finding.basis}){' ' + where if where else ''}: "
                     f"{finding.message}")
    lines.append("")
    base, cand = _rows(report.baseline), _rows(report.candidate)
    width = max(len(label) for label, _ in base)
    column = max(len("baseline"), *(len(value) for _, value in base))
    lines.append(f"{'metric':<{width}}  {'baseline':<{column}}  candidate")
    for (label, before), (_, after) in zip(base, cand):
        lines.append(f"{label:<{width}}  {before:<{column}}  {after}")
    lines.append("judge scores never decide the verdict.")
    return "\n".join(lines) + "\n"
