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
against a dry-run baseline, and at most ``(calls planned) x (attempts per call)`` requests are sent.
Exit status: 0 written, 1 run failed, 2 refused or bad usage. On any failure nothing is left in
``--out``.

Known limit: V2 speaks the OpenAI-compatible chat protocol only. Against an ``anthropic`` baseline it
calls Anthropic's OpenAI-compatible endpoint, which does not offer prompt caching and does not report
cached-token counts, while 0.2.1 called Anthropic's own API. The comparison then says the cost is
``usage_incomplete`` rather than guessing, and the report says why; input and output token sums are
still shown.
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
    CampaignActor, CharacterDefinition, ControlAssignment, DefinitionIndex, InstanceRef, Knowledge,
    SourceDescriptor, SourceRef, actor_knowledge,
)
from grimoire.core.authority import DIRECTOR
from grimoire.core.inference import InferencePolicy, InferenceStage, TurnUsageLedger
from grimoire.core.model import ActorView
from grimoire.evals.format import BenchmarkFormatError, Scenario
from grimoire.evals.format2 import FORMAT_V2, RunV2, parse_run_v2
from grimoire.evals.gate2 import compare_v2
from grimoire.evals.loader import benchmark_file, evidence_digest, load_json, load_scenario
from grimoire.evals.policy import GatePolicy
from grimoire.evals.report import render_v2
from grimoire.evals.runtime_record import record_runtime_run
from grimoire.evals.v021 import verify_evidence
from grimoire.generation import GenerationError, HeardLine, npc_brief, plan_generation_turn
from grimoire.generation.imported import VoiceTexts, derive_profile
from grimoire.providers import (
    EnvironmentCredentials, ProviderEndpoint, ProviderRegistry, RouteChoice, StaticCredentials,
)
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.meter import TurnMeter
from grimoire.providers.transport import HttpTransport
from grimoire.runtime import DEFAULT_MAX_OUTPUT_TOKENS, GenerationRuntime
from grimoire.runtime.record import recorded_turn
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.voice import SecretFact, VoiceCatalog, resolve_voice

from .v021 import CaptureFailed, CaptureRefused, _Parser, _write, check_key_env, check_out_dir, forbidden_roots

#: Where V2 sends each baseline provider's calls: OpenAI-compatible chat completions only.
ENDPOINTS = {"openrouter": "https://openrouter.ai/api/v1", "anthropic": "https://api.anthropic.com/v1"}
#: Said in the report whenever a baseline's provider is reached by another protocol than 0.2.1 used.
PROTOCOL_NOTES = {
    "anthropic": ("0.2.1 called Anthropic's own Messages API; V2 calls Anthropic's OpenAI-compatible "
                  "endpoint, which offers no prompt caching and reports no cached-token counts, so the "
                  "cost comparison is usage_incomplete and the token sums are not like for like."),
}
DRY_RUN_ENDPOINT = "https://dry-run.invalid/v1"
#: The harness a run of this module names in its source.
HARNESS = "grimoire.capture.v2/1"
OWNER, PRINCIPAL = "human:capture", "app:capture-runtime"
#: Each planner's dry-run suggestion: presentation only, no new fact.
DRY_RUN_SUGGESTION = "Stay in your own voice: keep to how you usually speak."
DRY_RUN_SUGGESTION_USAGE = {"prompt_tokens": 600, "completion_tokens": 20,
                            "prompt_tokens_details": {"cached_tokens": 0}}
_BUDGET = re.compile(r"max_tokens=(unset|[1-9][0-9]{0,6})")
WORLD, CAMPAIGN = "world:capture", "c-capture"


@dataclass(frozen=True)
class CompareOptions:
    scenario: str
    baseline: Path
    out: Path
    mode: str  # "dry_run" or "live"
    api_key_env: str | None = None
    with_planning: bool = False


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
    voices: Mapping[str, object]
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


def _dry_steps(setup: Mapping, scenario: Scenario, planning: bool) -> list:
    from grimoire.testing.providers import Reply  # synthetic only: never on the live path

    replies = setup["dry_run"]["replies"]
    steps = []
    for script in scenario.script:
        if planning:
            steps += [Reply(deltas=(DRY_RUN_SUGGESTION,), usage=dict(DRY_RUN_SUGGESTION_USAGE))
                      for _ in script.speakers]
        for actor_id in script.speakers:
            reply = replies[str(script.turn)][actor_id]
            steps.append(Reply(deltas=(reply["text"],), usage={
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
        endpoint = ProviderEndpoint(baseline.provider, ENDPOINTS[baseline.provider])
        credentials = EnvironmentCredentials({baseline.provider: options.api_key_env})
        adapter = ProviderAdapter(ProviderRegistry([endpoint]), credentials, authorizer, HttpTransport())
    else:
        from grimoire.testing.providers import ScriptedClock, ScriptedTransport

        endpoint = ProviderEndpoint(baseline.provider, DRY_RUN_ENDPOINT)
        clock = ScriptedClock()
        adapter = ProviderAdapter(ProviderRegistry([endpoint]), StaticCredentials({baseline.provider: "dry-run"}),
                                  authorizer, ScriptedTransport(_dry_steps(setup, scenario, planning)),
                                  clock=clock, wait=clock.wait)
    return GenerationRuntime(adapter, (RouteChoice(baseline.provider, baseline.model),), handle,
                             max_output_tokens=baseline.sent_max_tokens)


def planned_requests(scenario: Scenario, baseline: Baseline, *, with_planning: bool) -> int:
    """The most provider requests one comparison can send: every planned call at every attempt."""
    speaking = sum(len(script.speakers) for script in scenario.script)
    calls = speaking + (2 * speaking if with_planning else 0)  # the planning run plans and speaks
    return calls * baseline.attempts


def play(options: CompareOptions, baseline: Baseline, scenario: Scenario, setup: Mapping, *,
         planning: bool) -> dict:
    """The V2 run, as a ``/2`` run dict with the baseline's conditions."""
    cast = _cast(scenario, setup)
    runtime = _runtime(options, baseline, setup, scenario, planning)
    caps = {"planning": max(len(script.speakers) for script in scenario.script)} if planning else {}
    policy = InferencePolicy(caps, max_attempts_per_call=baseline.attempts)
    player_text = {turn["turn"]: turn["player_text"] for turn in setup["turns"]}
    player = InstanceRef(CAMPAIGN, "character", cast.player)
    heard: list[HeardLine] = []
    recorded = []
    for script in scenario.script:
        said = HeardLine(cast.names[player], player_text[script.turn], player)
        briefs = [_brief(cast, scenario, actor_id, script.turn, (*heard, said)) for actor_id in script.speakers]
        plan = plan_generation_turn(CAMPAIGN, f"t{script.turn}", present=cast.present, speakers=script.speakers,
                                    controls=cast.controls, policy=policy,
                                    planners=script.speakers if planning else ())
        meter = TurnMeter(TurnUsageLedger(plan))
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
    document = record_runtime_run(
        scenario, recorded, run_id=f"v2-{options.mode.replace('_', '-')}-{name}",
        label=f"V2 runtime ({options.mode}, {baseline.provider}, {name})",
        conditions=baseline.conditions, harness=HARNESS,
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


def _report(options: CompareOptions, baseline: Baseline, scenario: Scenario, runs: Mapping[str, dict]) -> str:
    evidence = verify_evidence(baseline.run, baseline.bundle, evidence_digest(baseline.bundle), scenario)
    parts = [f"V2 against the 0.2.1 capture: {options.mode}, provider {baseline.provider}, "
             f"model {baseline.model}, V2 max_tokens={baseline.sent_max_tokens}, "
             f"baseline evidence sha256 {baseline.run.source.evidence_sha256}"]
    if options.mode == "dry_run":
        parts.append("Dry run: scripted replies and synthetic numbers; nothing was sent.")
    if baseline.max_tokens is None:
        parts.append("0.2.1 sent no output cap (max_tokens=unset) and V2 always sends one, so the "
                     "budgets differ and the gate reports the runs as incomparable.")
    if baseline.provider in PROTOCOL_NOTES and options.mode == "live":
        parts.append(PROTOCOL_NOTES[baseline.provider])
    v2 = parse_run_v2(runs["baseline"], scenario)
    parts.append("== V2 (one call per speaking NPC) against 0.2.1 ==\n"
                 + render_v2(compare_v2(scenario, baseline.run, v2, baseline_evidence=evidence)))
    if "planning" in runs:
        planned = parse_run_v2(runs["planning"], scenario)
        policy = GatePolicy(allowed_flags=frozenset({InferenceStage.PLANNING.value}))
        parts.append("== V2 with optional planning against plain V2 (an opt-in billed extra) ==\n"
                     + render_v2(compare_v2(scenario, v2, planned, policy)))
    return "\n\n".join(parts) + "\n"


def run_compare(options: CompareOptions) -> Path:
    try:
        scenario = load_scenario(benchmark_file(options.scenario, "scenario.json"))
    except BenchmarkFormatError as error:
        raise CaptureRefused(f"scenario {options.scenario!r}: {error}") from None
    setup = _setup(options.scenario, scenario)
    baseline = load_baseline(options.baseline, scenario)
    key = None
    if options.mode == "live":
        if baseline.run.source.mode != "live":
            raise CaptureRefused("--live needs a live 0.2.1 baseline; this one is a dry run")
        if not options.api_key_env:
            raise CaptureRefused("--live needs --api-key-env")
        check_key_env(options.api_key_env)
        key = os.environ.get(options.api_key_env) or None
        if key is None:
            raise CaptureRefused(f"the environment variable {options.api_key_env} is not set")
    out = check_out_dir(options.out, forbidden=forbidden_roots(Path(options.baseline).resolve()))
    if options.mode == "live":
        print(f"live: at most {planned_requests(scenario, baseline, with_planning=options.with_planning)} "
              f"billed requests to {baseline.provider} ({baseline.model})", file=sys.stderr)
    runs = {"baseline": play(options, baseline, scenario, setup, planning=False)}
    if options.with_planning:
        runs["planning"] = play(options, baseline, scenario, setup, planning=True)
    files = {f"v2-{name}.run.json": json.dumps(raw, indent=2, ensure_ascii=False) + "\n"
             for name, raw in runs.items()}
    files["comparison-report.txt"] = _report(options, baseline, scenario, runs)
    if key and any(key in text for text in files.values()):
        raise CaptureFailed("an output contains the provider key; nothing was written")
    _write(out, files)
    return out


# --- command line --------------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m grimoire.capture.v2",
                     description="Play a 0.2.1 capture's scenario on V2 and compare usage and voice.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--baseline", required=True, type=Path,
                        help="the 0.2.1 capture directory (run.json and evidence.json)")
    parser.add_argument("--out", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="no network; synthetic numbers")
    mode.add_argument("--live", action="store_true", help="real provider calls; billed")
    parser.add_argument("--with-planning", action="store_true",
                        help="also play V2 with optional presentation planning (more billed calls)")
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
        if args.live and not args.confirm_billing:
            raise CaptureRefused("--live makes billed provider calls; add --confirm-billing")
        if args.live and not args.api_key_env:
            raise CaptureRefused("--live needs --api-key-env naming the key's variable")
        if args.api_key_env:
            check_key_env(args.api_key_env)
        options = CompareOptions(args.scenario, args.baseline, args.out, "live" if args.live else "dry_run",
                                 args.api_key_env, args.with_planning)
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
