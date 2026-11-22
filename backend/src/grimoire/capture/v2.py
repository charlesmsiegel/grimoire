"""``python -m grimoire.capture.v2``: play a 0.2.1 capture's scenario on V2 and compare (068).

One step for the billed V2-vs-0.2.1 comparison. It reads 015's capture directory (``run.json`` and
``evidence.json``, as ``python -m grimoire.capture.v021`` writes them), verifies the evidence, takes the
provider, served model, output budget and attempt limit from it, plays the same scripted turns on
V2's own generation runtime (one call per speaking NPC, nothing else) and writes the V2 run as a
``grimoire.story-quality/2`` run of source ``v2-runtime`` with the baseline's recording conditions,
then the gate's report of V2 against 0.2.1. ``--with-planning`` adds a second V2 run with 068's
optional presentation planning on (one capped, untrusted suggestion before each speaking NPC's own
reply), compared with the plain V2 run, so the optional stage's cost and voice are reported apart.

Each V2 brief carries only what the 0.2.1 side was given: the card's description, personality and
example turns (read with V2's own card importer), the lines spoken aloud before the call and this
turn's player text. A scenario secret is screened only for an actor that has not learned it yet by
the scenario's own ``known_from`` turn, and its text is never shown to anyone.

Explicitly opt-in, as the 0.2.1 capture is. ``--dry-run`` sends nothing anywhere: a scripted transport
answers with the scenario's dry-run replies and every number is synthetic. ``--live`` is billed and
needs ``--confirm-billing`` and the *name* of an environment variable holding the key; it is refused
against a dry-run baseline, and at most ``(calls planned) x (attempts per call) x (samples)`` requests
are sent.

``--samples N`` plays V2 N times (and, with planning, N more times), and ``--baseline`` may be
repeated with other 0.2.1 captures of the same play. With more than one sample on a side the report
gates all of them together (:mod:`grimoire.evals.sampled`), so one noisy reply cannot decide it.
Exit status: 0 written, 1 run failed, 2 refused or bad usage. On any failure nothing is left in
``--out``.

Against an ``anthropic`` baseline V2 calls Anthropic's own Messages API, as 0.2.1 did, so cache
reads and writes are provider-reported and the cost can be compared; an ``openrouter`` baseline is
reached over OpenAI-compatible chat completions. The two sides still assemble different prompts, so
the report says the token sums are not like for like.

The first comparison (V2 against 0.2.1) is 068's proving scenario. The second, with planning, is an
experiment: a failure there rejects the optional stage, not V2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from grimoire.core import (
    CampaignActor,
    CharacterDefinition,
    ControlAssignment,
    DefinitionIndex,
    InstanceRef,
    Knowledge,
    SourceDescriptor,
    SourceRef,
    actor_knowledge,
)
from grimoire.core.authority import DIRECTOR
from grimoire.core.inference import InferencePolicy, InferenceStage, TurnUsageLedger
from grimoire.core.model import ActorView
from grimoire.evals.format import BenchmarkFormatError, Scenario
from grimoire.evals.format2 import FORMAT_V2, RunV2, parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import (
    benchmark_file,
    evidence_digest,
    load_json,
    load_scenario,
)
from grimoire.evals.policy import GatePolicy
from grimoire.evals.report import render_samples, render_v2
from grimoire.evals.runtime_record import record_runtime_run
from grimoire.evals.sampled import compare_samples
from grimoire.evals.v021 import describe_rejected, rejected_replies, verify_evidence
from grimoire.generation import (
    GenerationError,
    HeardLine,
    npc_brief,
    plan_generation_turn,
)
from grimoire.generation.imported import VoiceTexts, derive_profile
from grimoire.providers import (
    EnvironmentCredentials,
    ProviderEndpoint,
    ProviderRegistry,
    RouteChoice,
    StaticCredentials,
)
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.cancel import CancelToken
from grimoire.providers.config import Secret, Timeouts
from grimoire.providers.errors import TransportError
from grimoire.providers.meter import TurnMeter
from grimoire.providers.transport import Connection, HttpTransport, Response
from grimoire.runtime import DEFAULT_MAX_OUTPUT_TOKENS, GenerationRuntime
from grimoire.runtime.record import recorded_turn
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.voice import ResolvedVoice, SecretFact, VoiceCatalog, resolve_voice

from .v021 import (
    CaptureFailed,
    CaptureRefused,
    _Parser,
    _write,
    check_key_env,
    check_out_dir,
    forbidden_roots,
)

#: Where V2 sends each baseline provider's calls, and the wire protocol it speaks there.
ENDPOINTS = {"openrouter": "https://openrouter.ai/api/v1", "anthropic": "https://api.anthropic.com/v1"}
PROTOCOLS = {"openrouter": "openai_chat", "anthropic": "anthropic_messages"}
#: Said in the report whenever a baseline's provider is reached by another protocol than 0.2.1 used.
PROTOCOL_NOTES = {
    "anthropic": ("Both sides called Anthropic's Messages API, so cache reads and writes are "
                  "provider-reported. The prompts differ (0.2.1 assembled its own, and may have set "
                  "options V2 does not, extended thinking or cache breakpoints among them), so the token "
                  "sums are not like for like."),
}
#: Said in every report: which comparison 068's proving scenario rests on.
VERDICT_NOTE = ("068's proving scenario rests on the first comparison (V2 against 0.2.1). The second is an "
                "experiment: a failure there rejects optional planning, not V2.")
DRY_RUN_ENDPOINT = "https://dry-run.invalid/v1"
#: The harness a run of this module names in its source.
HARNESS = "grimoire.capture.v2/1"
OWNER, PRINCIPAL = "human:capture", "app:capture-runtime"
#: Each planner's dry-run suggestion: presentation only, no new fact.
DRY_RUN_SUGGESTION = "Stay in your own voice: keep to how you usually speak."
DRY_RUN_SUGGESTION_USAGE = {"prompt_tokens": 600, "completion_tokens": 20,
                            "prompt_tokens_details": {"cached_tokens": 0}}
_BUDGET = re.compile(r"max_tokens=(unset|[1-9][0-9]{0,6})")
#: Most V2 plays of each kind one command may make; each is billed in full on --live.
MAX_SAMPLES = 10
WORLD, CAMPAIGN = "world:capture", "c-capture"


@dataclass(frozen=True)
class CompareOptions:
    scenario: str
    #: One 0.2.1 capture directory, or several captures of the same play (one sample each).
    baseline: Path | tuple[Path, ...]
    out: Path
    mode: str  # "dry_run" or "live"
    api_key_env: str | None = None
    with_planning: bool = False
    #: How many times V2 plays the scenario (and, with planning, plays it planned).
    samples: int = 1
    #: An earlier comparison's output, whose V2 runs are scored again instead of played (no calls).
    rescore_of: Path | None = None

    @property
    def baselines(self) -> tuple[Path, ...]:
        return (Path(self.baseline),) if isinstance(self.baseline, (str, Path)) else tuple(
            Path(directory) for directory in self.baseline)


@dataclass(frozen=True)
class Baseline:
    run: RunV2
    bundle: Mapping
    provider: str
    model: str
    max_tokens: int | None  # None: 0.2.1 sent no output cap
    attempts: int

    @property
    def sent_max_tokens(self) -> int:
        """V2's output cap: the baseline's, or V2's default where 0.2.1 sent none (then incomparable)."""
        return DEFAULT_MAX_OUTPUT_TOKENS if self.max_tokens is None else self.max_tokens

    @property
    def conditions(self) -> dict[str, str]:
        """The V2 run's conditions: the baseline's, with the budget V2 actually sent."""
        return {**self.run.conditions, "budget": f"max_tokens={self.sent_max_tokens}"}


# --- the baseline --------------------------------------------------------------------------------

def load_baseline(directory: Path, scenario: Scenario) -> Baseline:
    """015's capture in ``directory``, if its evidence verifies and its conditions can be matched."""
    directory = Path(directory)
    try:
        raw = load_json(directory / "run.json")
        bundle = load_json(directory / "evidence.json")
        run = parse_run_v2(raw, scenario)
    except (BenchmarkFormatError, ValueError, OSError) as error:
        raise CaptureRefused(f"--baseline {directory}: {error}") from None
    if run.source.kind != "v0.2.1-capture" or not isinstance(bundle, dict):
        raise CaptureRefused("--baseline must be a 0.2.1 capture (run.json and evidence.json)")
    check = verify_evidence(run, bundle, evidence_digest(bundle), scenario)
    if not check.ok:
        raise CaptureRefused(f"--baseline evidence does not verify: {check.reason}")
    provider = bundle.get("provider_kind")
    if provider not in ENDPOINTS:
        raise CaptureRefused(f"--baseline provider {provider!r} has no V2 endpoint here")
    budget = _BUDGET.fullmatch(str(run.conditions.get("budget", "")))
    model = run.conditions.get("model")
    if budget is None or not model:
        raise CaptureRefused("--baseline conditions must name a model and a budget of max_tokens=N")
    cap = None if budget.group(1) == "unset" else int(budget.group(1))
    return Baseline(run, bundle, provider, model, cap, run.policy.max_attempts_per_call)


def load_baselines(directories: Sequence[Path], scenario: Scenario) -> tuple[Baseline, ...]:
    """Every capture in ``directories``; they must be distinct captures of the same play."""
    if not directories:
        raise CaptureRefused("--baseline is required")
    loaded = tuple(load_baseline(directory, scenario) for directory in directories)
    first = loaded[0]
    digests = [each.run.source.evidence_sha256 for each in loaded]
    if len(set(digests)) != len(digests):
        raise CaptureRefused("--baseline names the same capture more than once")
    for directory, each in zip(directories[1:], loaded[1:]):
        same = ((each.provider, each.model, each.max_tokens, each.attempts, each.run.conditions,
                 each.run.source.mode)
                == (first.provider, first.model, first.max_tokens, first.attempts,
                    first.run.conditions, first.run.source.mode))
        if not same:
            raise CaptureRefused(f"--baseline {directory}: its provider, model, budget, attempts, "
                                 f"conditions or mode differ from the first capture's")
    return loaded


def _setup(name: str, scenario: Scenario) -> dict:
    try:
        setup = load_json(benchmark_file(name, "v021_setup.json"))
    except BenchmarkFormatError as error:
        raise CaptureRefused(f"scenario {name!r}: {error}") from None
    if not isinstance(setup, dict) or setup.get("scenario_id") != scenario.scenario_id:
        raise CaptureRefused(f"scenario {name!r}: v021_setup.json is for another scenario")
    return setup


# --- the V2 side -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Cast:
    controls: tuple[ControlAssignment, ...]
    voices: Mapping[str, ResolvedVoice]
    views: Mapping[str, tuple[InstanceRef, str, str]]  # actor id: (ref, name, card description)
    names: Mapping[InstanceRef, str]
    present: tuple[str, ...]
    player: str


def _cast(scenario: Scenario, setup: Mapping) -> _Cast:
    """Every NPC's voice from its card text, read by V2's importer; the player as a human seat."""
    npcs = {npc["actor_id"]: npc for npc in setup["npcs"]}
    player = setup["pc"]["actor_id"]
    if set(npcs) != set(scenario.director_ids()) or not scenario.is_player(player):
        raise CaptureRefused("v021_setup.json's cast is not the scenario's")
    definitions, profiles, actors, views = [], [], {}, {}
    for actor_id, npc in npcs.items():
        card = npc["card"]
        ref = SourceRef(WORLD, "character", actor_id, "r1")
        derived = derive_profile(ref, npc["name"], VoiceTexts(examples=card.get("mes_example", ""),
                                                              personality=card.get("personality", ""),
                                                              description=card.get("description", "")))
        if derived.profile is None:
            raise CaptureRefused(f"{actor_id}'s card has no voice text")
        definitions.append(CharacterDefinition(ref, npc["name"]))
        profiles.append(derived.profile)
        actors[actor_id] = CampaignActor(CAMPAIGN, actor_id, SourceRef(WORLD, "character", actor_id, None))
        views[actor_id] = (InstanceRef(CAMPAIGN, "character", actor_id), npc["name"], card.get("description", ""))
    index = DefinitionIndex([SourceDescriptor(WORLD, "synthetic")], definitions)
    catalog = VoiceCatalog(profiles)
    voices = {actor_id: resolve_voice(actor, catalog, index) for actor_id, actor in actors.items()}
    controls = tuple(ControlAssignment(CAMPAIGN, actor_id, DIRECTOR) for actor_id in npcs) + (
        ControlAssignment(CAMPAIGN, player, "human:player"),)
    names = {ref: name for ref, name, _ in views.values()}
    names[InstanceRef(CAMPAIGN, "character", player)] = setup["pc"]["name"]
    return _Cast(controls, voices, views, names, (*npcs, player), player)


def _secrets(scenario: Scenario, actor_id: str, turn: int) -> tuple[SecretFact, ...]:
    """The scenario secrets ``actor_id`` has not learned by ``turn``: screened, never shown."""
    return tuple(SecretFact(fact.fact_id, fact.markers) for fact in scenario.facts
                 if fact.known_from.get(actor_id, turn + 1) > turn)


def _brief(cast: _Cast, scenario: Scenario, actor_id: str, turn: int, heard: Sequence[HeardLine]):
    ref, name, description = cast.views[actor_id]
    view = ActorView(CAMPAIGN, actor_id, name, description,
                     knowledge=actor_knowledge(Knowledge(CAMPAIGN), ref, as_of=turn))
    return npc_brief(view, cast.voices[actor_id], heard=heard, secrets=_secrets(scenario, actor_id, turn),
                     names=dict(cast.names))


class _DryRunResponse:
    """One scripted 200 SSE answer, read line by line as the adapter reads a real stream."""

    def __init__(self, lines: Sequence[bytes], cancel: CancelToken) -> None:
        self.status = 200
        self.headers: Mapping[str, str] = {"content-type": "text/event-stream"}
        self._lines, self._index, self._cancel = list(lines), 0, cancel

    def read_line(self, max_bytes: int, timeout: float) -> bytes:
        if self._cancel.cancelled or self._index >= len(self._lines):
            return b""  # a cancelled or finished stream reads as closed
        line = self._lines[self._index]
        self._index += 1
        if len(line) > max_bytes:
            raise TransportError("bad_response", "read")
        return line

    def error_body(self, limit: int) -> bytes:
        rest, self._index = b"".join(self._lines[self._index:]), len(self._lines)
        return rest[:limit]


class _DryRunConnection:
    peer_loopback = False

    def __init__(self, lines: Sequence[bytes], cancel: CancelToken) -> None:
        self._lines, self._cancel = lines, cancel

    def send(self, body: bytes, headers: Mapping[str, str], key: Secret | None, *,
             key_scheme: str = "bearer") -> Response:
        return _DryRunResponse(self._lines, self._cancel)

    def close(self) -> None:
        pass


class _DryRunTransport:
    """Answers each ``connect`` with the next scripted reply; nothing leaves the process.

    The product's own small fake, not the test harness's (which product code may not import): only
    whole successful streams, one per planned call, in the order the runtime issues them.
    """

    def __init__(self, replies: Sequence[tuple[str, Mapping]], model: str) -> None:
        self._replies, self._model = list(replies), model

    def connect(self, url: str, timeouts: Timeouts, cancel: CancelToken) -> Connection:
        if not self._replies:
            raise CaptureFailed("the dry run asked for more calls than the scenario scripts")
        text, usage = self._replies.pop(0)
        return _DryRunConnection(_sse(text, usage, self._model), cancel)


def _sse(text: str, usage: Mapping, model: str) -> list[bytes]:
    def data(obj: object) -> bytes:
        return b"data: " + json.dumps(obj).encode() + b"\n"
    return [data({"model": model, "choices": [{"delta": {"content": text}, "finish_reason": None}]}),
            data({"model": model, "choices": [{"delta": {}, "finish_reason": "stop"}]}),
            data({"model": model, "choices": [], "usage": dict(usage)}),
            b"data: [DONE]\n"]


class _DryRunClock:
    """A deterministic clock and wait, so a dry run's synthetic timings never vary."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.01
        return self.now

    def wait(self, token: CancelToken, seconds: float) -> bool:
        self.now += seconds
        return token.cancelled


def _dry_steps(setup: Mapping, scenario: Scenario, planning: bool) -> list[tuple[str, Mapping]]:
    replies = setup["dry_run"]["replies"]
    steps: list[tuple[str, Mapping]] = []
    for script in scenario.script:
        if planning:
            steps += [(DRY_RUN_SUGGESTION, dict(DRY_RUN_SUGGESTION_USAGE)) for _ in script.speakers]
        for actor_id in script.speakers:
            reply = replies[str(script.turn)][actor_id]
            steps.append((reply["text"], {
                "prompt_tokens": reply["prompt_tokens"], "completion_tokens": reply["completion_tokens"],
                "prompt_tokens_details": {"cached_tokens": reply.get("cache_read_tokens", 0)}}))
    return steps


def _runtime(options: CompareOptions, baseline: Baseline, setup: Mapping, scenario: Scenario,
             planning: bool) -> GenerationRuntime:
    authorizer = Authorizer({OWNER})
    authorizer.add_grant(Grant("capture-providers", PRINCIPAL, frozenset({Permission.EXECUTE}),
                               ResourcePath(("providers",)), OWNER))
    handle = authorizer.handle_for(PRINCIPAL, trusted=True)
    if options.mode == "live":
        assert options.api_key_env is not None  # run_compare refuses a live run without it
        endpoint = ProviderEndpoint(baseline.provider, ENDPOINTS[baseline.provider],
                                    protocol=PROTOCOLS[baseline.provider])
        credentials = EnvironmentCredentials({baseline.provider: options.api_key_env})
        adapter = ProviderAdapter(ProviderRegistry([endpoint]), credentials, authorizer, HttpTransport())
    else:
        endpoint = ProviderEndpoint(baseline.provider, DRY_RUN_ENDPOINT)
        clock = _DryRunClock()
        adapter = ProviderAdapter(ProviderRegistry([endpoint]), StaticCredentials({baseline.provider: "dry-run"}),
                                  authorizer, _DryRunTransport(_dry_steps(setup, scenario, planning), baseline.model),
                                  clock=clock, wait=clock.wait)
    return GenerationRuntime(adapter, (RouteChoice(baseline.provider, baseline.model),), handle,
                             max_output_tokens=baseline.sent_max_tokens)


def planned_requests(scenario: Scenario, baseline: Baseline, *, with_planning: bool,
                     samples: int = 1) -> int:
    """The most provider requests one comparison can send: every planned call of every sample at
    every attempt."""
    speaking = sum(len(script.speakers) for script in scenario.script)
    calls = speaking + (2 * speaking if with_planning else 0)  # the planning run plans and speaks
    return calls * baseline.attempts * samples


def spent(meters: Sequence[TurnMeter]) -> str:
    """What the turns played so far sent, as counts only (never text): for a run that fails or is stopped."""
    attempts = [a for meter in meters for a in meter.ledger.attempts if a.outcome != "not_sent"]
    sums = {name: sum(getattr(a.usage, name) or 0 for a in attempts) for name in ("input_tokens", "output_tokens")}
    return (f"{len(attempts)} requests sent; reported input tokens {sums['input_tokens']}, "
            f"output tokens {sums['output_tokens']}")


def served_model(meters: Sequence[TurnMeter], requested: str) -> str:
    """The model that answered this run, as its recorded condition (the 0.2.1 capture's own rule).

    The requested model unless a reply named another. A provider that resolved the request
    to another snapshot, or answered from more than one model, is recorded as it served, so the gate
    reports the model condition as differing from the baseline instead of comparing unlike runs.
    """
    served = {report.served_model for meter in meters for call in meter.ledger.plan.calls
              for report in meter.reports(call.call_id) if report.served_model}
    if not served:
        return requested
    return " + ".join(sorted(served))


def play(options: CompareOptions, baseline: Baseline, scenario: Scenario, setup: Mapping, *,
         planning: bool, meters: list[TurnMeter] | None = None, sample: int | None = None) -> dict:
    """The V2 run, as a ``/2`` run dict with the baseline's conditions."""
    cast = _cast(scenario, setup)
    runtime = _runtime(options, baseline, setup, scenario, planning)
    caps = {"planning": max(len(script.speakers) for script in scenario.script)} if planning else {}
    policy = InferencePolicy(caps, max_attempts_per_call=baseline.attempts)
    player_text = {turn["turn"]: turn["player_text"] for turn in setup["turns"]}
    player = InstanceRef(CAMPAIGN, "character", cast.player)
    heard: list[HeardLine] = []
    recorded = []
    own: list[TurnMeter] = []
    for script in scenario.script:
        said = HeardLine(cast.names[player], player_text[script.turn], player)
        briefs = [_brief(cast, scenario, actor_id, script.turn, (*heard, said)) for actor_id in script.speakers]
        plan = plan_generation_turn(CAMPAIGN, f"t{script.turn}", present=cast.present, speakers=script.speakers,
                                    controls=cast.controls, policy=policy,
                                    planners=script.speakers if planning else ())
        meter = TurnMeter(TurnUsageLedger(plan))
        own.append(meter)
        if meters is not None:
            meters.append(meter)
        try:
            outcome = runtime.run_turn(meter, briefs, controls=cast.controls)
        except GenerationError as error:
            raise CaptureFailed(f"turn {script.turn}: {error.reason}") from None
        recorded.append(recorded_turn(script.turn, meter, outcome,
                                      player_lines=((cast.player, player_text[script.turn]),)))
        if outcome.status != "complete":
            break  # the gate reports the unspoken calls; a later turn would hear a different scene
        heard.append(said)
        heard.extend(HeardLine(line.speaker, line.text, line.actor) for line in outcome.lines)
    name = "planning" if planning else "baseline"
    suffix = "" if sample is None else f"-{sample}"
    document = record_runtime_run(
        scenario, recorded, run_id=f"v2-{options.mode.replace('_', '-')}-{name}{suffix}",
        label=(f"V2 runtime ({options.mode}, {baseline.provider}, {name}"
               + ("" if sample is None else f", sample {sample}") + ")"),
        conditions={**baseline.conditions, "model": served_model(own, baseline.model)}, harness=HARNESS,
        route=f"{baseline.provider}:{baseline.model}",
        choice_outcomes={choice.choice_id: "unlabelled" for choice in scenario.choices})
    # No director reply is planned, so the /3 record is a /2 run: the format 0.2.1's capture is in.
    # Its source names no mode (a v2-runtime source has none): the label and each attempt's
    # measurement (synthetic or provider-reported) say how it was made.
    document["format"] = FORMAT_V2
    try:
        parse_run_v2(document, scenario)
    except BenchmarkFormatError as error:
        raise CaptureFailed(f"the V2 run cannot be recorded: {error}") from None
    return document


def _rejected_note(baselines: Sequence[Baseline]) -> str:
    """What the 0.2.1 replies its captures rejected as empty and Retried cost, or ``""``."""
    spent = [rejected_replies(each.bundle) for each in baselines]
    if not any(each["replies"] for each in spent):
        return ""
    totals = {name: sum(each[name] for each in spent) for name in spent[0]}
    replies, with_any = totals["replies"], sum(1 for each in spent if each["replies"])
    return (f"0.2.1 rejected {replies} repl{'y' if replies == 1 else 'ies'} as empty in "
            f"{with_any} of the {len(baselines)} captures, and its own Retry asked again. Those "
            f"replies took {describe_rejected(totals)}. They are not in the 0.2.1 side: its cost "
            "and latency are those of the play 0.2.1 kept, a lower bound on what its player paid.")


def _report(options: CompareOptions, baselines: Sequence[Baseline], scenario: Scenario,
            runs: Mapping[str, Sequence[dict]]) -> str:
    first = baselines[0]
    evidence = [verify_evidence(each.run, each.bundle, evidence_digest(each.bundle), scenario)
                for each in baselines]
    digests = ", ".join(each.run.source.evidence_sha256 for each in baselines)
    parts = [(f"V2 against the 0.2.1 capture{'s' if len(baselines) > 1 else ''}: {options.mode}, "
              f"provider {first.provider}, model {first.model}, V2 max_tokens={first.sent_max_tokens}, "
              f"baseline evidence sha256 {digests}")]
    sampled = options.samples > 1 or len(baselines) > 1
    if sampled:
        parts.append(f"Samples: {len(baselines)} 0.2.1 capture{'s' if len(baselines) > 1 else ''}, "
                     f"{options.samples} V2 play{'s' if options.samples > 1 else ''}"
                     f"{' of each kind' if options.with_planning else ''}. "
                     "One reply is one draw from the model, so each side is read over all its samples.")
    if options.rescore_of is not None:
        parts.append(f"Rescored: the V2 runs were read back from {options.rescore_of}, written by an "
                     f"earlier {options.mode} comparison, and scored again with the scenario's "
                     "current markers. Nothing was sent and nothing was billed.")
    if options.mode == "dry_run":
        parts.append("Dry run: scripted replies and synthetic numbers; nothing was sent.")
    rejected = _rejected_note(baselines)
    if rejected:
        parts.append(rejected)
    if first.max_tokens is None:
        parts.append("0.2.1 sent no output cap (max_tokens=unset) and V2 always sends one, so the "
                     "budgets differ and the gate reports the runs as incomparable.")
    if first.provider in PROTOCOL_NOTES and options.mode == "live":
        parts.append(PROTOCOL_NOTES[first.provider])
    plain = [parse_run_v2(raw, scenario) for raw in runs["baseline"]]
    title = "== V2 (one call per speaking NPC) against 0.2.1 ==\n"
    if sampled:
        parts.append(title + render_samples(compare_samples(
            scenario, [each.run for each in baselines], plain, baseline_evidence=evidence)))
    else:
        parts.append(title + render_v2(compare_v2(scenario, first.run, plain[0],
                                                  baseline_evidence=evidence[0])))
    if "planning" in runs:
        parts.append(VERDICT_NOTE)
        planned = [parse_run_v2(raw, scenario) for raw in runs["planning"]]
        policy = GatePolicy(allowed_flags=frozenset({InferenceStage.PLANNING.value}))
        title = "== V2 with optional planning against plain V2 (an opt-in billed extra) ==\n"
        parts.append(title + (render_samples(compare_samples(scenario, plain, planned, policy)) if sampled
                              else render_v2(compare_v2(scenario, plain[0], planned[0], policy))))
    return "\n\n".join(parts) + "\n"


def run_compare(options: CompareOptions) -> Path:
    try:
        scenario = load_scenario(benchmark_file(options.scenario, "scenario.json"))
    except BenchmarkFormatError as error:
        raise CaptureRefused(f"scenario {options.scenario!r}: {error}") from None
    if isinstance(options.samples, bool) or not isinstance(options.samples, int) or not (
            1 <= options.samples <= MAX_SAMPLES):
        raise CaptureRefused(f"--samples must be a whole number from 1 to {MAX_SAMPLES}")
    setup = _setup(options.scenario, scenario)
    directories = options.baselines
    baselines = load_baselines(directories, scenario)
    first = baselines[0]
    key = None
    if options.mode == "live":
        if first.run.source.mode != "live":
            raise CaptureRefused("--live needs a live 0.2.1 baseline; this one is a dry run")
        if not options.api_key_env:
            raise CaptureRefused("--live needs --api-key-env")
        check_key_env(options.api_key_env)
        key = os.environ.get(options.api_key_env) or None
        if key is None:
            raise CaptureRefused(f"the environment variable {options.api_key_env} is not set")
    forbidden = [*forbidden_roots(directories[0].resolve()),
                 *(Path(os.path.realpath(directory)) for directory in directories[1:])]
    out = check_out_dir(options.out, forbidden=forbidden)
    if options.mode == "live":
        ceiling = planned_requests(scenario, first, with_planning=options.with_planning,
                                   samples=options.samples)
        print(f"live: at most {ceiling} billed requests to {first.provider} ({first.model}) "
              f"over {options.samples} sample{'s' if options.samples > 1 else ''}; "
              "if any play fails or is stopped, nothing is written", file=sys.stderr)
    meters: list[TurnMeter] = []
    numbered = options.samples > 1
    kinds = [False, True] if options.with_planning else [False]
    runs: dict[str, list[dict]] = {}
    try:
        for planning in kinds:
            runs["planning" if planning else "baseline"] = [
                play(options, first, scenario, setup, planning=planning, meters=meters,
                     sample=sample if numbered else None)
                for sample in range(1, options.samples + 1)]
    except BaseException:  # failed or interrupted: nothing is written, but say what was already sent
        if options.mode == "live" and meters:
            print(f"stopped after billed calls: {spent(meters)}", file=sys.stderr)
        raise
    files = {f"v2-{name}{f'-{index}' if numbered else ''}.run.json":
             json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
             for name, samples in runs.items() for index, raw in enumerate(samples, start=1)}
    files["comparison-report.txt"] = _report(options, baselines, scenario, runs)
    if key and any(key in text for text in files.values()):
        raise CaptureFailed("an output contains the provider key; nothing was written")
    _write(out, files)
    return out


_SAVED_RUN = re.compile(r"v2-(baseline|planning)(?:-([1-9][0-9]*))?\.run\.json")
_SAVED_HEADER = re.compile(r"V2 against the 0\.2\.1 captures?: (dry_run|live), .*?baseline evidence "
                           r"sha256 ([0-9a-f]{64}(?:, [0-9a-f]{64})*)\n")


def saved_comparison(directory: Path) -> tuple[str, tuple[str, ...], dict[str, list[dict]]]:
    """An earlier comparison's mode, baseline evidence digests and V2 runs, read back from its output."""
    if not directory.is_dir():
        raise CaptureRefused(f"--rescore {directory}: not a comparison's output directory")
    try:
        header = (directory / "comparison-report.txt").read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        raise CaptureRefused(f"--rescore {directory}: no readable comparison-report.txt") from None
    match = _SAVED_HEADER.match(header)
    if match is None:
        raise CaptureRefused(f"--rescore {directory}: comparison-report.txt names no 0.2.1 baselines")
    found: dict[str, dict[int, Path]] = {}
    for path in directory.iterdir():
        if (name := _SAVED_RUN.fullmatch(path.name)) is not None:
            found.setdefault(name[1], {})[int(name[2] or 0)] = path
    if "baseline" not in found:
        raise CaptureRefused(f"--rescore {directory}: holds no V2 run")
    numbers = sorted(found["baseline"])
    if numbers != [0] and numbers != list(range(1, len(numbers) + 1)):
        raise CaptureRefused(f"--rescore {directory}: its V2 runs are not numbered 1 to {len(numbers)}")
    if "planning" in found and sorted(found["planning"]) != numbers:
        raise CaptureRefused(f"--rescore {directory}: its planned and plain V2 runs do not pair up")
    runs: dict[str, list[dict]] = {}
    for kind, paths in found.items():
        try:
            runs[kind] = [load_json(paths[number]) for number in numbers]  # type: ignore[misc]
        except BenchmarkFormatError as error:
            raise CaptureRefused(f"--rescore {directory}: {error}") from None
    return match[1], tuple(match[2].split(", ")), runs


def run_rescore(scenario_name: str, baselines: Sequence[Path], rescore: Path, out: Path) -> Path:
    """Score an earlier comparison's V2 runs again, against the same captures, with the current
    scenario. Nothing is sent: the runs are read back from ``rescore``."""
    try:
        scenario = load_scenario(benchmark_file(scenario_name, "scenario.json"))
    except BenchmarkFormatError as error:
        raise CaptureRefused(f"scenario {scenario_name!r}: {error}") from None
    rescore = Path(os.path.realpath(rescore))
    mode, digests, runs = saved_comparison(rescore)
    loaded = load_baselines(baselines, scenario)
    if tuple(each.run.source.evidence_sha256 for each in loaded) != digests:
        raise CaptureRefused("--baseline must name the captures the earlier comparison used, in "
                             "the same order")
    for kind, samples in runs.items():
        for raw in samples:
            try:
                parse_run_v2(raw, scenario)
            except BenchmarkFormatError as error:
                raise CaptureRefused(f"--rescore {rescore}: a {kind} V2 run: {error}") from None
    forbidden = [*forbidden_roots(Path(baselines[0]).resolve()), rescore,
                 *(Path(os.path.realpath(directory)) for directory in baselines[1:])]
    out = check_out_dir(out, forbidden=forbidden)
    options = CompareOptions(scenario_name, tuple(baselines), out, mode,
                             with_planning="planning" in runs, samples=len(runs["baseline"]),
                             rescore_of=rescore)
    _write(out, {"comparison-report.txt": _report(options, loaded, scenario, runs)})
    return out


# --- command line --------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m grimoire.capture.v2",
                     description="Play a 0.2.1 capture's scenario on V2 and compare usage and voice.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--baseline", required=True, type=Path, action="append",
                        help="a 0.2.1 capture directory (run.json and evidence.json); repeat it "
                             "for several captures of the same play, one sample each")
    parser.add_argument("--out", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="no network; synthetic numbers")
    mode.add_argument("--live", action="store_true", help="real provider calls; billed")
    mode.add_argument("--rescore", type=Path, metavar="DIR",
                      help="score an earlier comparison's V2 runs in DIR again with the current "
                           "scenario, against the same --baseline captures; no calls, nothing billed")
    parser.add_argument("--with-planning", action="store_true",
                        help="also play V2 with optional presentation planning (more billed calls)")
    parser.add_argument("--samples", type=int, default=None, metavar="N",
                        help=f"V2 plays of each kind, 1 to {MAX_SAMPLES} (each billed on --live)")
    parser.add_argument("--api-key-env", metavar="VAR",
                        help="name of the environment variable holding the key (live)")
    parser.add_argument("--confirm-billing", action="store_true",
                        help="required with --live: real calls are billed")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code or 0)
    try:
        if args.rescore is not None:
            given = [flag for flag, value in (("--with-planning", args.with_planning),
                                              ("--samples", args.samples is not None),
                                              ("--api-key-env", args.api_key_env),
                                              ("--confirm-billing", args.confirm_billing)) if value]
            if given:
                raise CaptureRefused(f"--rescore makes no calls and reads its runs from DIR; "
                                     f"drop {', '.join(given)}")
            out = run_rescore(args.scenario, tuple(args.baseline), args.rescore, args.out)
            print(f"rescored comparison written to {out}")
            return 0
        if args.live and not args.confirm_billing:
            raise CaptureRefused("--live makes billed provider calls; add --confirm-billing")
        if args.live and not args.api_key_env:
            raise CaptureRefused("--live needs --api-key-env naming the key's variable")
        if args.api_key_env:
            check_key_env(args.api_key_env)
        options = CompareOptions(args.scenario, tuple(args.baseline), args.out,
                                 "live" if args.live else "dry_run", args.api_key_env,
                                 args.with_planning, 1 if args.samples is None else args.samples)
        out = run_compare(options)
    except CaptureRefused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    except CaptureFailed as error:
        print(f"comparison failed: {error}", file=sys.stderr)
        return 1
    print(f"comparison written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
