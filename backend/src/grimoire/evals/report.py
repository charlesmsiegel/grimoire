"""Plain-text gate report. It cites ids only; it never prints contribution or marker text."""

from __future__ import annotations

from fractions import Fraction

from .checks import Scorecard
from .gate import GateReport
from .gate2 import GateReportV2, ScorecardV2

SYNTHETIC_NOTICE = ("no regression against a synthetic baseline — not a cost/quality "
                    "certification")
#: "measured" is what the run file says about itself. Until measured runs carry
#: provider-ledger evidence (sprint 015), the report must not present them as verified.
#: /2: a capture's provider-reported ledger whose evidence was not supplied or did not verify.
UNVERIFIED_NOTICE = ("provider-reported (declared by capture, unverified): pass --evidence-… to "
                     "verify")
MEASURED_NOTICE = ("measured: the capture evidence's digest and re-conversion verified; the "
                   "evidence bundle itself is not authenticated and the provider's own receipt "
                   "is not independently verified")
#: A passing /2 comparison where a run is neither synthetic nor a verified measurement.
NOT_VERIFIED_NOTICE = ("no regression, but at least one run is not a verified measurement — "
                       "not a cost/quality certification")
STATUS_LABELS = {
    "measured": "measured (provider-reported, evidence consistent; origin and provider receipt "
                "unverified)",
    "provider_reported_unverified": "provider-reported (declared by capture, unverified)",
    "estimated": "estimated",
    "synthetic": "synthetic",
    "incomplete": "incomplete (unmetered calls or unreported counts)",
}
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


# --- grimoire.story-quality/2 ------------------------------------------------------------------

def _counted(sums, unreported, name: str) -> str:
    """A sum is a lower bound while any attempt left it unreported; say so beside it."""
    missing = unreported[name]
    return f"{sums[name]}" + (f" ({missing} unreported)" if missing else "")


def _rows_v2(card: ScorecardV2) -> list[tuple[str, str]]:
    weighted = ("n/a (usage incomplete)" if card.weighted_cost is None
                else _fmt(card.weighted_cost))
    cost = f"{card.cost_usd_reported:.6f}" + (
        f" ({card.cost_unreported} unreported)" if card.cost_unreported else "")
    by_actor = ", ".join(
        f"{actor or 'scene'}=in {_counted(t.sums, t.unreported, 'input_tokens')}"
        f"/out {_counted(t.sums, t.unreported, 'output_tokens')}"
        for actor, t in sorted(card.by_actor.items(), key=lambda item: item[0] or "")) or "none"
    return [
        ("usage status", STATUS_LABELS[card.status]),
        ("source", card.source),
        ("NPC lines", _fmt(card.npc_lines)),
        ("leak rate (NPC lines)", _fmt(card.leak_rate)),
        ("choices negated by label", _fmt(card.negated_by_label)),
        ("choices negated by evidence", _fmt(card.negated_by_evidence)),
        ("choices unrealized", _fmt(card.unrealized)),
        ("choices unlabelled", _fmt(card.unlabelled)),
        ("cast distinguishability", _fmt(card.distinguishability)),
        ("calls by stage", _mapping(card.calls_by_stage)),
        ("attempts", _fmt(card.attempts)),
        ("unmetered calls", _fmt(card.unmetered)),
        ("input tokens", _counted(card.sums, card.unreported, "input_tokens")),
        ("cache read tokens", _counted(card.sums, card.unreported, "cache_read_tokens")),
        ("cache write tokens", _counted(card.sums, card.unreported, "cache_write_tokens")),
        ("output tokens", _counted(card.sums, card.unreported, "output_tokens")),
        ("reasoning tokens", _counted(card.sums, card.unreported, "reasoning_tokens")),
        ("weighted billed tokens", weighted),
        ("cost usd (reported)", cost),
        ("cost basis", ", ".join(card.cost_bases) or "none"),
        ("usage by actor", by_actor),
        ("total call latency ms", f"{card.latency_ms_reported}" + (
            f" ({card.latency_unreported} unreported)" if card.latency_unreported else "")),
        ("judge scores (informational)", _mapping(card.judge_scores)),
    ]


def render_v2(report: GateReportV2) -> str:
    lines = [f"story-quality gate: {'PASS' if report.passed else 'FAIL'}",
             f"baseline: {report.baseline_id}", f"candidate: {report.candidate_id}"]
    statuses = (report.baseline.status, report.candidate.status)
    if all(status == "measured" for status in statuses):
        lines.append(f"note: {MEASURED_NOTICE}")
    elif report.passed:
        lines.append(f"note: {SYNTHETIC_NOTICE if 'synthetic' in statuses else NOT_VERIFIED_NOTICE}")
    else:
        lines.append("note: at least one run is not a verified measurement")
    if "provider_reported_unverified" in statuses:
        lines.append(f"note: {UNVERIFIED_NOTICE}")
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
    base, cand = _rows_v2(report.baseline), _rows_v2(report.candidate)
    width = max(len(label) for label, _ in base)
    column = max(len("baseline"), *(len(value) for _, value in base))
    lines.append(f"{'metric':<{width}}  {'baseline':<{column}}  candidate")
    for (label, before), (_, after) in zip(base, cand):
        lines.append(f"{label:<{width}}  {before:<{column}}  {after}")
    lines.append("judge scores never decide the verdict.")
    return "\n".join(lines) + "\n"
