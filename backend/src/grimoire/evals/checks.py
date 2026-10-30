"""Hard story-quality invariants and per-run scorecards (pure; no I/O, no model calls)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType

from .format import NARRATION, NPC_GENERATION, Call, Contribution, Run, Scenario
from .policy import GatePolicy
from .text import cosine, first_marker, has_words, longest_shared_run, trigram_counts

#: A model NPC line sharing this many contiguous normalised characters with a voice sample is a copy.
SAMPLE_COPY_CHARS = 24
RP_PURPOSES = (NPC_GENERATION, NARRATION)


@dataclass(frozen=True, slots=True)
class Finding:
    """One reason to reject a run. Cites ids and indices only, never contribution text."""

    check: str
    basis: str  # "invariant" | "label" | "evidence" | "comparison"
    message: str
    turn: int | None = None
    subject: str | None = None
    evidence: str | None = None


def finding_key(finding: Finding) -> tuple:
    return (finding.check, finding.turn or 0, finding.subject or "", finding.evidence or "",
            finding.message)


@dataclass(frozen=True, slots=True)
class Scorecard:
    npc_lines: int
    leak_lines: int
    leak_rate: Fraction | None
    negated_by_label: int
    negated_by_evidence: int
    unrealized: int
    distinguishability: Fraction | None
    calls_by_purpose: Mapping[str, int]
    uncached_input: int
    cached_input: int
    output: int
    weighted_cost: Fraction
    cost_by_purpose: Mapping[str, Fraction]
    cost_by_flag: Mapping[str, Fraction]
    latency_ms: int
    judge_scores: Mapping[str, float]


# --- traversal helpers --------------------------------------------------------------------------

def _model_lines(run: Run, *, spoken: bool = False) -> Iterator[tuple[int, int, Contribution]]:
    """Model contributions; with ``spoken``, only those that say something.

    Blank or punctuation-only lines count as silence for "who spoke" questions (script,
    orphaned calls, voice scoring) but still count as model-authored for every other check.
    """
    for turn in run.turns:
        for position, contribution in enumerate(turn.contributions):
            if contribution.author == "model" and (not spoken or has_words(contribution.text)):
                yield turn.turn, position, contribution


def _npc_lines(scenario: Scenario, run: Run) -> Iterator[tuple[int, int, Contribution]]:
    directors = set(scenario.director_ids())
    for turn, position, contribution in _model_lines(run, spoken=True):
        if contribution.speaker_id in directors:
            yield turn, position, contribution


def _who(contribution: Contribution) -> str:
    return contribution.speaker_id or "narration"


# --- individual checks --------------------------------------------------------------------------

def _leaks(scenario: Scenario, run: Run) -> list[tuple[Finding, bool]]:
    """Knowledge leaks, each flagged True when it is in an NPC line (not narration)."""
    directors = set(scenario.director_ids())
    players = [actor.actor_id for actor in scenario.actors if actor.control == "player"]
    results = []
    for turn, position, line in _model_lines(run):
        is_npc = line.speaker_id in directors
        if not is_npc and line.speaker_id is not None:
            continue  # a model voicing a player character is a sovereignty violation instead
        for fact in scenario.facts:
            index = first_marker(line.text, fact.markers)
            if index is None:
                continue
            knowers = [line.speaker_id] if is_npc else players
            if any(fact.known_from.get(actor, turn + 1) <= turn for actor in knowers):
                continue
            message = (f"NPC states fact before learning it (contribution {position})" if is_npc
                       else f"narration states a fact no player character knows yet "
                            f"(contribution {position})")
            results.append((Finding("knowledge_leak", "invariant", message, turn, _who(line),
                                    f"{fact.fact_id}#{index}"), is_npc))
    return results


def _sovereignty(scenario: Scenario, run: Run) -> Iterator[Finding]:
    for turn, position, line in _model_lines(run):
        if scenario.is_player(line.speaker_id):
            yield Finding("player_sovereignty", "invariant",
                          f"model authored a player character's contribution {position}",
                          turn, line.speaker_id, f"contribution#{position}")
        index = first_marker(line.text, scenario.pc_authorship_markers)
        if index is not None:
            yield Finding("player_sovereignty", "invariant",
                          f"model text authors player character speech or interiority "
                          f"(contribution {position})", turn, _who(line), f"pc_authorship#{index}")


def _player_input(scenario: Scenario, run: Run) -> Iterator[Finding]:
    """Each scripted choice is played exactly once, verbatim; nothing else is player input."""
    unplayed = Counter((choice.turn, choice.actor_id, choice.text) for choice in scenario.choices)
    for turn in run.turns:
        for position, line in enumerate(turn.contributions):
            if line.author != "player":
                continue
            key = (turn.turn, line.speaker_id, line.text)
            if unplayed[key]:
                unplayed[key] -= 1
            else:
                yield Finding("player_input_altered", "invariant",
                              f"unscripted player contribution {position}", turn.turn,
                              line.speaker_id)
    for choice in scenario.choices:
        key = (choice.turn, choice.actor_id, choice.text)
        if unplayed[key]:
            unplayed[key] -= 1
            yield Finding("player_input_altered", "invariant",
                          "scripted player choice is missing or altered", choice.turn,
                          choice.choice_id)


def _evidence_text(run: Run, choice_turn: int, actor_id: str, text: str, window: int) -> list[str]:
    """Model text that may realise a choice: after the scripted line on its turn, then the window."""
    texts = []
    for turn in run.turns:
        if turn.turn == choice_turn:
            after = None
            for position, contribution in enumerate(turn.contributions):
                if (contribution.author == "player" and contribution.speaker_id == actor_id
                        and contribution.text == text):
                    after = position
                    break
            if after is not None:
                texts += [c.text for c in turn.contributions[after + 1:] if c.author == "model"]
        elif choice_turn < turn.turn <= choice_turn + window:
            texts += [c.text for c in turn.contributions if c.author == "model"]
    return texts


def _choices(scenario: Scenario, run: Run) -> Iterator[Finding]:
    for choice in scenario.choices:
        if run.choice_outcomes[choice.choice_id] == "negated":
            yield Finding("choice_negated", "label", "run labels the choice negated",
                          choice.turn, choice.choice_id, "label")
        texts = _evidence_text(run, choice.turn, choice.actor_id, choice.text, choice.window)
        negations = sorted({index for text in texts
                            if (index := first_marker(text, choice.negation_markers)) is not None})
        for index in negations:
            yield Finding("choice_negated", "evidence", "model text negates the choice",
                          choice.turn, choice.choice_id, f"negation#{index}")
        if not any(first_marker(text, choice.effect_markers) is not None for text in texts):
            yield Finding("choice_negated", "evidence",
                          "no model text gives effect to the choice", choice.turn,
                          choice.choice_id, "unrealized")


def _continuity(scenario: Scenario, run: Run) -> Iterator[Finding]:
    for fact in scenario.established_facts:
        for turn, position, line in _model_lines(run):
            if turn < fact.from_turn:
                continue
            index = first_marker(line.text, fact.contradiction_markers)
            if index is not None:
                yield Finding("continuity_contradiction", "invariant",
                              f"{_who(line)} contradicts an established fact "
                              f"(contribution {position})", turn, fact.fact_id,
                              f"contradiction#{index}")


def _script(scenario: Scenario, run: Run) -> Iterator[Finding]:
    turns = {turn.turn: turn for turn in run.turns}
    for entry in scenario.script:
        if entry.turn not in turns:
            yield Finding("script_incomplete", "invariant", "scripted turn is missing", entry.turn,
                          "turn")
            continue
        spoke = {line.speaker_id for turn, _, line in _model_lines(run, spoken=True)
                 if turn == entry.turn}
        for speaker in entry.speakers:
            if speaker not in spoke:
                yield Finding("script_incomplete", "invariant", "scripted speaker did not speak",
                              entry.turn, speaker)


def _calls(scenario: Scenario, run: Run) -> Iterator[Finding]:
    calls: dict[str, Call] = {call.call_id: call for call in run.calls}
    directors = set(scenario.director_ids())
    uses: Counter[str] = Counter()
    spoken = Counter(line.call_id for _, _, line in _model_lines(run, spoken=True))
    for turn, position, line in _model_lines(run):
        call = calls[line.call_id]  # parse_run guarantees the reference
        uses[call.call_id] += 1
        if line.speaker_id in directors and (
                call.purpose != NPC_GENERATION or call.actor_id != line.speaker_id):
            yield Finding("separate_generation", "invariant",
                          f"NPC contribution {position} is not from its own npc_generation call",
                          turn, line.speaker_id, call.call_id)
        elif line.speaker_id is None and call.purpose != NARRATION:
            yield Finding("separate_generation", "invariant",
                          f"narration contribution {position} is not from a narration call",
                          turn, "narration", call.call_id)
    for call in run.calls:
        if uses[call.call_id] > 1:
            yield Finding("separate_generation", "invariant",
                          f"one call produced {uses[call.call_id]} contributions", call.turn,
                          call.call_id)
        if call.purpose in RP_PURPOSES and not spoken[call.call_id]:
            yield Finding("orphan_generation", "invariant",
                          f"{call.purpose} call produced no non-blank contribution", call.turn, call.call_id)
        if call.purpose not in RP_PURPOSES and call.feature_flag not in run.enabled_flags:
            yield Finding("unflagged_inference", "invariant",
                          f"extra {call.purpose!r} call is not behind an enabled feature flag",
                          call.turn, call.call_id, call.feature_flag or "no flag")


def _sample_copies(scenario: Scenario, run: Run) -> Iterator[Finding]:
    samples = [(actor.actor_id, index, sample) for actor in scenario.actors
               if actor.control == "director" for index, sample in enumerate(actor.voice_samples)]
    for turn, position, line in _model_lines(run):
        for actor_id, index, sample in samples:
            if longest_shared_run(line.text, sample) >= SAMPLE_COPY_CHARS:
                yield Finding("voice_sample_copy", "invariant",
                              f"model contribution {position} copies a voice sample", turn,
                              _who(line), f"{actor_id}#sample{index}")


def hard_findings(scenario: Scenario, run: Run) -> tuple[Finding, ...]:
    """Every invariant violation of ``run``; empty means none of the hard checks failed."""
    findings = [finding for finding, _ in _leaks(scenario, run)]
    for check in (_sovereignty, _player_input, _choices, _continuity, _script, _calls,
                  _sample_copies):
        findings.extend(check(scenario, run))
    return tuple(sorted(findings, key=finding_key))


# --- metrics ------------------------------------------------------------------------------------

def distinguishability(scenario: Scenario, run: Run) -> Fraction | None:
    """Share of NPC lines attributed to their own speaker by voice alone.

    Ties, and lines sharing no trigram with any profile, count as wrong.
    """
    profiles = {actor.actor_id: trigram_counts("\n".join(actor.voice_samples))
                for actor in scenario.actors if actor.control == "director"}
    correct = total = 0
    for _, _, line in _npc_lines(scenario, run):
        total += 1
        counts = trigram_counts(line.text)
        similarity = {actor_id: cosine(counts, profile) for actor_id, profile in profiles.items()}
        best = max(similarity.values())
        winners = [actor_id for actor_id, value in similarity.items() if value == best]
        correct += best > 0 and winners == [line.speaker_id]
    return Fraction(correct, total) if total else None


def weighted_cost(call: Call, policy: GatePolicy) -> Fraction:
    uncached = call.input_tokens - call.cached_input_tokens
    return (uncached + call.cached_input_tokens * policy.exact("cached_input_weight")
            + call.output_tokens)


def scorecard(scenario: Scenario, run: Run, policy: GatePolicy) -> Scorecard:
    npc_lines = sum(1 for _ in _npc_lines(scenario, run))
    leaking = {(f.turn, f.message) for f, is_npc in _leaks(scenario, run) if is_npc}
    choice_findings = list(_choices(scenario, run))
    by_purpose: Counter[str] = Counter()
    cost_by_purpose: dict[str, Fraction] = {}
    cost_by_flag: dict[str, Fraction] = {}
    for call in run.calls:
        cost = weighted_cost(call, policy)
        by_purpose[call.purpose] += 1
        cost_by_purpose[call.purpose] = cost_by_purpose.get(call.purpose, Fraction(0)) + cost
        if call.feature_flag is not None:
            cost_by_flag[call.feature_flag] = cost_by_flag.get(call.feature_flag, Fraction(0)) + cost
    return Scorecard(
        npc_lines=npc_lines,
        leak_lines=len(leaking),
        leak_rate=Fraction(len(leaking), npc_lines) if npc_lines else None,
        negated_by_label=len({f.subject for f in choice_findings if f.basis == "label"}),
        negated_by_evidence=len({f.subject for f in choice_findings
                                 if f.basis == "evidence" and f.evidence != "unrealized"}),
        unrealized=len({f.subject for f in choice_findings if f.evidence == "unrealized"}),
        distinguishability=distinguishability(scenario, run),
        calls_by_purpose=MappingProxyType(dict(sorted(by_purpose.items()))),
        uncached_input=sum(c.input_tokens - c.cached_input_tokens for c in run.calls),
        cached_input=sum(c.cached_input_tokens for c in run.calls),
        output=sum(c.output_tokens for c in run.calls),
        weighted_cost=sum(cost_by_purpose.values(), Fraction(0)),
        cost_by_purpose=MappingProxyType(dict(sorted(cost_by_purpose.items()))),
        cost_by_flag=MappingProxyType(dict(sorted(cost_by_flag.items()))),
        latency_ms=sum(c.latency_ms for c in run.calls),
        judge_scores=run.judge_scores,
    )
