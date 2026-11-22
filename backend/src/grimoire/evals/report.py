"""Plain-text gate report. It cites ids only; it never prints contribution or marker text."""

from __future__ import annotations

from fractions import Fraction

from .checks import Scorecard
from .format3 import FORMAT_V3
from .gate import GateReport
from .gate2 import GateReportV2, ScorecardV2
from .sampled import MIN_SAMPLES_FOR_NOISE, NOISE_STANDARD_ERRORS, SampledReport

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


# --- grimoire.story-quality/2 and /3 -----------------------------------------------------------

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
    # /2 reports stay as they were; only a /3 run can narrate, so only its report counts it.
    narration = [("narration lines", _fmt(card.narration_lines))] if card.format == FORMAT_V3 else []
    return [
        ("usage status", STATUS_LABELS[card.status]),
        ("source", card.source),
        ("NPC lines", _fmt(card.npc_lines)),
        *narration,
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


def _status_note(statuses: set[str], passed: bool) -> list[str]:
    notes = []
    if statuses == {"measured"}:
        notes.append(f"note: {MEASURED_NOTICE}")
    elif passed:
        notes.append(f"note: {SYNTHETIC_NOTICE if 'synthetic' in statuses else NOT_VERIFIED_NOTICE}")
    else:
        notes.append("note: at least one run is not a verified measurement")
    if "provider_reported_unverified" in statuses:
        notes.append(f"note: {UNVERIFIED_NOTICE}")
    return notes


def _findings(findings) -> list[str]:
    lines = [f"findings ({len(findings)}):"]
    for finding in findings:
        where = " ".join(part for part in (
            f"turn {finding.turn}" if finding.turn is not None else "",
            finding.subject or "", f"[{finding.evidence}]" if finding.evidence else "") if part)
        lines.append(f"  - {finding.check} ({finding.basis}){' ' + where if where else ''}: "
                     f"{finding.message}")
    return lines


#: Under a reader's labels, the per-sample rows that come from the run's own record and from phrase
#: markers are renamed, so a table does not read as contradicting the labelled verdict.
LABELLED_ROWS = {
    "choices negated by label": "choices negated in the run's own record",
    "choices unrealized": "choices unrealized by phrase markers (not used)",
}


def _sample_table(title: str, ids, cards, *, labelled: bool = False) -> list[str]:
    columns = [[value for _, value in _rows_v2(card)] for card in cards]
    labels = [LABELLED_ROWS.get(label, label) if labelled else label
              for label, _ in _rows_v2(cards[0])]
    heads = [f"sample {index}" for index in range(1, len(cards) + 1)]
    width = max(len(label) for label in [*labels, "metric"])
    widths = [max(len(head), *(len(value) for value in column)) for head, column in zip(heads, columns)]
    lines = [f"{title}: " + ", ".join(f"{head}={run_id}" for head, run_id in zip(heads, ids)),
             f"{'metric':<{width}}  " + "  ".join(f"{head:<{w}}" for head, w in zip(heads, widths)).rstrip()]
    for row, label in enumerate(labels):
        cells = "  ".join(f"{column[row]:<{w}}" for column, w in zip(columns, widths))
        lines.append(f"{label:<{width}}  {cells}".rstrip())
    return lines


def render_samples(report: SampledReport) -> str:
    """A sampled comparison: the verdict, its rule, the aggregates, then every sample's metrics."""
    voice = report.voice
    lines = [f"story-quality gate: {'PASS' if report.passed else 'FAIL'}",
             f"samples: {len(report.baselines)} baseline, {len(report.candidates)} candidate",
             *_status_note({card.status for card in (*report.baselines, *report.candidates)},
                           report.passed)]
    loosened = report.policy.non_defaults()
    if loosened:
        lines.append(f"policy (non-default): {_mapping(loosened)}")
    lines += [
        "rule: every hard invariant must hold in every sample; a choice must be given effect in a "
        "strict majority of each side's samples; voice compares mean cast distinguishability, "
        f"allowing {NOISE_STANDARD_ERRORS} standard errors of the difference for sampling noise "
        f"when both sides have at least {MIN_SAMPLES_FOR_NOISE} samples; cost and latency compare means.",
        ("choices: decided by a reader's labels (honored or complicated gives effect, ignored does "
         "not, negated is a hard finding); phrase markers decide nothing"
         if report.choice_basis == "labels" else
         "choices: decided by phrase markers in the model text"),
        "", *_findings(report.findings), "",
        "choices given effect (samples realizing it, baseline / candidate):"]
    for choice_id, (base, cand) in sorted(report.realized.items()):
        lines.append(f"  - {choice_id}: {base}/{len(report.baselines)} / "
                     f"{cand}/{len(report.candidates)}")
    lines.append("cast distinguishability: baseline mean "
                 f"{_fmt(voice.baseline_mean)} ({', '.join(_fmt(v) for v in voice.baseline) or 'n/a'}), "
                 f"candidate mean {_fmt(voice.candidate_mean)} "
                 f"({', '.join(_fmt(v) for v in voice.candidate) or 'n/a'}); allowed drop "
                 f"{report.policy.max_voice_drop:.3f}, noise allowance {voice.noise_allowance:.3f}")
    lines.append(f"weighted billed tokens (mean): baseline "
                 f"{'n/a (usage incomplete)' if report.baseline_cost is None else _fmt(report.baseline_cost)}, "
                 f"candidate "
                 f"{'n/a (usage incomplete)' if report.candidate_cost is None else _fmt(report.candidate_cost)}")
    labelled = report.choice_basis == "labels"
    lines += ["", *_sample_table("baseline samples", report.baseline_ids, report.baselines,
                                 labelled=labelled), "",
              *_sample_table("candidate samples", report.candidate_ids, report.candidates,
                             labelled=labelled),
              "judge scores never decide the verdict."]
    return "\n".join(lines) + "\n"
