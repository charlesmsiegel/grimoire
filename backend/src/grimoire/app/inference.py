"""The app's inference settings, usage and diagnostics (112): what the settings and costs screens call.

``InferenceApi`` is trusted application code beside 044's ``TextApi``, acting for the library's owner. It owns
only the app's model configuration file (outside the library: it names the key's environment variable, never the
key) and what the table's runner is given from it: 027's runtime and 004's policy. Every other answer is read
from records other services own, and nothing in the library is written:

- ``models`` and ``check_models`` read the configuration and check a draft of it; nothing is sent to a provider.
- ``save_models`` writes the configuration at the revision the client read, and gives the runner the runtime
  and policy built from it, so the next turn plays by it.
- ``usage`` reads one chat's turn files through 035's ``SceneRunner.open``: every planned call of every take
  and labelling, with each attempt 013 booked (outcome, measurement, route, tokens), and nothing a call said.
- ``diagnostics`` reads 048's traces of this process's runner calls, held in memory and never persisted.

No price ships (013), so every cost is ``unpriced``: unknown, never free. See docs/INFERENCE_UI.md.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from grimoire.core.identity import ChatRef
from grimoire.core.inference import InferencePolicy, InferenceStage, UsageTotals
from grimoire.core.inference.usage import TOKEN_FIELDS
from grimoire.providers.config import route_label
from grimoire.scene import SceneRefused
from grimoire.store import LibraryLocked, LockUnavailable, OsLocker, StoreError
from grimoire.store.jsonfmt import etag, is_etag
from grimoire.textapi import ApiRefused
from grimoire.tracing import TraceLog, call_costs

from .config import (
    KEYS,
    MAX_ATTEMPTS,
    MAX_FALLBACKS,
    MAX_OUTPUT_TOKENS,
    ConfigError,
    ModelConfig,
    build_runtime,
    config_json,
    parse_model_config,
)

#: What a key variable the app itself may name looks like. A configuration saved from the app names the
#: variable a key is read from; were any name allowed, whoever holds the serve's token could send any of the
#: app's environment (a cloud credential, say) to an endpoint of their choosing. A file the owner wrote by hand
#: keeps whatever name it gives: saving it again unchanged is allowed.
APP_KEY_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,119}_API_KEY")
#: The most bytes of configuration read: a real one is a few hundred.
MAX_CONFIG_BYTES = 64 * 1024
#: Turns a usage answer lists at most; its totals still cover every turn.
MAX_TURNS = 1000
DEFAULT_TURNS = 200
#: Traces of runner calls kept in memory for ``diagnostics``.
KEEP_TRACES = 64
#: Seconds a save waits for another writer of the same settings file before it is refused ``busy``.
LOCK_WAIT = 2.0
_LOCKER = OsLocker()

#: The optional stages, and why the app plays none but the director's reply yet. A stage is shown as it is:
#: the app never offers a switch that would change nothing.
_NOT_WIRED = {
    InferenceStage.DECISION: "045 decisions run only for NPCs with a 039 agenda, and the app configures none yet",
    InferenceStage.LABELLING: "035's labelling needs a labeller runtime and handle the app does not set up yet",
    InferenceStage.PLANNING: "no V2 service plans an NPC with a model yet",
    InferenceStage.EPISTEMIC_INFERENCE: "no V2 service infers perception with a model yet",
    InferenceStage.RECALL: "memory recall is deterministic in V2; no model-assisted recall exists yet",
    InferenceStage.GRAPH_LOOKUP: "graph traversal is deterministic in V2; no model-assisted lookup exists yet",
    InferenceStage.CRITIQUE: "no V2 service asks a model for critique yet",
}
#: Features of the settings screen that have no V2 service behind them, with the reason the screen shows.
UNAVAILABLE = (
    ("connection_test", "a test request needs a call outside a turn, and 013's adapter sends only a turn's planned "
                        "calls; checking a draft sends nothing"),
    ("director_model", "027's runtime calls one list of models for every call, so the director uses the NPC models"),
    ("other_endpoints", "the app configures one endpoint; fallbacks are other models on that endpoint"),
    ("prices", "no prices ship (013), so every cost is unknown, not free"),
    ("session_budget", "004 caps calls per turn only; session budgets are a later milestone"),
    ("prompt_preview", "no V2 service previews or keeps a prompt: briefs are built for a call and never stored"),
    ("presets", "no provider presets exist in V2; name the endpoint and model"),
)


class SettingsFile:
    """The model configuration file, written whole and atomically, at the revision the client read.

    ``path`` is ``None`` when the app was served without one: settings are then read-only. The file need not
    exist yet: the app starts without a model, and the first save creates it."""

    def __init__(self, path: Path | None) -> None:
        self.path = None if path is None else Path(path)
        self._lock = threading.Lock()

    @property
    def writable(self) -> bool:
        return self.path is not None

    def read(self) -> tuple[bytes | None, str | None]:
        """(the file's bytes, their etag); ``(None, None)`` when it does not exist (or none is configured)."""
        if self.path is None:
            return None, None
        try:
            with open(self.path, "rb") as handle:
                data = handle.read(MAX_CONFIG_BYTES + 1)
        except FileNotFoundError:
            return None, None
        return data, etag(data)

    def write(self, config: ModelConfig, expected: str | None) -> str:
        """Replace the file with ``config`` if it is still at ``expected`` (``None``: it must not exist yet).
        Returns the new etag. Refused ``changed`` when it moved on: nothing is written."""
        if self.path is None:
            raise ApiRefused("read_only", "this app was served without a settings file (serve --model FILE)")
        data = (json.dumps(config_json(config), indent=2, sort_keys=True) + "\n").encode("utf-8")
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                held = self._hold()
            except OSError as error:
                raise ApiRefused("settings_unwritable", f"the settings file cannot be written ({error.strerror})") from None
            try:
                self._check(expected)
                try:
                    handle, temporary = tempfile.mkstemp(prefix=".model-", suffix=".tmp", dir=self.path.parent)
                    try:
                        with os.fdopen(handle, "wb") as out:
                            out.write(data)
                            out.flush()
                            os.fsync(out.fileno())
                        self._check(expected)  # a hand edit while the new file was written wins: refused, not lost
                        os.replace(temporary, self.path)
                    except BaseException:
                        try:
                            os.unlink(temporary)
                        except OSError:
                            pass
                        raise
                except OSError as error:
                    raise ApiRefused("settings_unwritable",
                                     f"the settings file cannot be written ({error.strerror})") from None
            finally:
                _LOCKER.release(held)
        return etag(data)

    def _check(self, expected: str | None) -> None:
        try:
            _current, found = self.read()
        except OSError as error:
            raise ApiRefused("settings_unreadable", f"the settings file cannot be read ({error.strerror})") from None
        if found != expected:
            raise ApiRefused("changed", "the settings changed since they were read; nothing was written")

    def _hold(self) -> object:
        """The OS lock on the file's ``.lock`` beside it: one writer at a time, across processes and instances
        (another serve, another tab's app, a test). Waits briefly, then refuses ``busy``: nothing is written."""
        lock = self.path.with_name(self.path.name + ".lock")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        os.close(os.open(lock, flags, 0o600))
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                return _LOCKER.acquire(lock)
            except LockUnavailable as error:
                raise ApiRefused("settings_unwritable", f"the settings file cannot be locked ({error})") from None
            except LibraryLocked:
                if time.monotonic() >= deadline:
                    raise ApiRefused("busy", "another save of these settings is under way; nothing was written") \
                        from None
                time.sleep(0.05)


def _problem(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _config_message(error: ConfigError) -> str:
    """A configuration error as the screen may show it. Unknown keys are not echoed: a key pasted as one would be."""
    text = str(error)
    if text.startswith("unknown keys"):
        return "the configuration has keys this app does not know"
    return text


class InferenceApi:
    """The settings, usage and diagnostics ops over one served ``TextApi``.

    ``settings`` is the app's ``SettingsFile``; ``owner`` the library owner the runtime's provider grant is
    made by; ``lock`` (default: the text API's own) serializes these ops with the text API's, so a save never
    swaps the runner's runtime in the middle of a turn. A runtime
    is built from the file at once (``environ`` and ``transport`` as 044's ``build_runtime`` takes them) and
    given to ``text_api``'s runner; a file that cannot be used leaves the app without a model, with the reason
    ``models`` shows. ``traces`` holds the runner's 048 traces when it has no tracer of its own."""

    def __init__(self, text_api, settings: SettingsFile, *, owner: str, lock=None,  # type: ignore[no-untyped-def]
                 environ: Mapping[str, str] | None = None, transport=None, keep: int = KEEP_TRACES) -> None:
        self.text_api = text_api
        self.settings = settings
        self.owner = owner
        self.environ = os.environ if environ is None else environ
        self.transport = transport
        self._lock = lock if lock is not None else text_api.lock  # one lock with the text API's ops: never mid-turn
        self.traces: TraceLog | None = None
        self.keep = keep
        runner = text_api.runner
        if runner.tracer is None:  # 048's hook: timed in memory only, never written
            self.traces = TraceLog(keep)
            runner.tracer = self.traces.record
        self.config: ModelConfig | None = None
        self.problem: dict | None = None
        self._loaded: str | None = None  # the etag of the file the configuration was read from
        self._ops: dict[str, Callable[[Mapping[str, object]], dict]] = {
            "models": self._models, "check_models": self._check, "save_models": self._save,
            "usage": self._usage, "diagnostics": self._diagnostics,
        }
        with self._lock:
            self._load()

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(sorted(self._ops))

    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        handler = self._ops.get(op) if isinstance(op, str) else None
        if handler is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        with self._lock:
            return handler(args)

    # -- the configuration -------------------------------------------------------------------------------
    def _parse(self, data: bytes) -> ModelConfig:
        if len(data) > MAX_CONFIG_BYTES:
            raise ConfigError("the configuration file is too large")
        try:
            raw = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise ConfigError("the configuration file is not a JSON object") from None
        return parse_model_config(raw)

    def _load(self) -> None:
        """Read the file and give the runner what it configures; a file that cannot be used leaves no model."""
        if self.settings.path is None:  # read-only: whatever runtime the text API was built with stays
            self.config = None
            self.problem = None if self.text_api.runtime is not None else _problem(
                "not_configured", "no settings file: generation is off, everything else works")
            return
        self._loaded = None
        try:
            data, self._loaded = self.settings.read()
        except OSError as error:
            self._apply(None, _problem("settings_unreadable", f"the settings file cannot be read ({error.strerror})"))
            return
        if data is None:
            self._apply(None, _problem("not_configured", "no model is configured yet: generation is off"))
            return
        try:
            config = self._parse(data)
        except ConfigError as error:
            self._apply(None, _problem("settings_invalid", _config_message(error)))
            return
        self._build(config)

    def _build(self, config: ModelConfig) -> None:
        try:
            runtime, policy = build_runtime(config, self.owner, environ=self.environ, transport=self.transport)
        except ConfigError as error:  # the key variable is not set: saved, but nothing can be generated
            self.config = config
            self._give(None, None)
            self.problem = _problem("key_missing", f"{error}: set it where the app runs, then restart the app")
            return
        self.config, self.problem = config, None
        self._give(runtime, policy)

    def _apply(self, config: ModelConfig | None, problem: dict | None) -> None:
        self.config, self.problem = config, problem
        self._give(None, None)

    def _give(self, runtime, policy) -> None:  # type: ignore[no-untyped-def]
        """The runner plays its next call by these (the text API's calls hold the same lock)."""
        runner = self.text_api.runner
        runner.runtime = runtime
        runner.policy = policy if policy is not None else InferencePolicy()
        self.text_api.runtime = runtime

    def _settings_view(self, config: ModelConfig | None) -> dict | None:
        if config is None:
            return None
        view = config_json(config)
        view.setdefault("key_env", None)
        view["key_set"] = None if config.key_env is None else bool(self.environ.get(config.key_env))
        return view

    def _stages(self, config: ModelConfig | None) -> list[dict]:
        stages = [{"stage": InferenceStage.NPC_GENERATION.value, "kind": "baseline", "enabled": config is not None,
                   "cap": None, "available": True,
                   "note": "one call per speaking NPC: the baseline, never switched off"}]
        director = 0 if config is None else config.director_calls
        stages.append({"stage": InferenceStage.DIRECTOR_GENERATION.value, "kind": "optional",
                       "enabled": director > 0, "cap": min(director, 1) if director else None, "available": True,
                       "note": "one director or narrator reply when asked for, at most one per turn"})
        for stage, reason in _NOT_WIRED.items():
            stages.append({"stage": stage.value, "kind": "optional", "enabled": False, "cap": None,
                           "available": False, "note": reason})
        return stages

    def _models(self, args: Mapping[str, object]) -> dict:
        try:
            _data, found = self.settings.read()
        except OSError:
            found = None
        if found != self._loaded:  # edited (or removed) by hand since it was read: the file is what holds
            self._load()
            found = self._loaded
        config = self.config
        routes = [] if config is None else [route_label(config.endpoint, model) for model in config.models]
        return {
            "settings": self._settings_view(config), "etag": found,
            "file": {"configured": self.settings.writable, "exists": found is not None},
            "generation": {"available": self.text_api.runtime is not None, "problem": self.problem, "routes": routes,
                           "local": None if config is None else config.endpoint.loopback_literal},
            "stages": self._stages(config),
            "limits": None if config is None else {
                "attempts_per_call": config.attempts, "max_output_tokens": config.max_output_tokens,
                "director_calls_per_turn": min(config.director_calls, 1)},
            "unavailable": [{"feature": feature, "reason": reason} for feature, reason in UNAVAILABLE],
            "bounds": {"fallbacks": MAX_FALLBACKS, "attempts": MAX_ATTEMPTS, "max_output_tokens": MAX_OUTPUT_TOKENS},
        }

    def _draft(self, args: Mapping[str, object]) -> tuple[ModelConfig | None, list[dict]]:
        """The draft ``settings`` of ``args`` as a configuration, or the problems that stop it."""
        draft = args.get("settings")
        if not isinstance(draft, dict):
            return None, [{"field": "settings", "message": "settings must be a JSON object"}]
        raw = {key: value for key, value in draft.items() if key != "key_set"}
        if raw.get("key_env") in (None, ""):
            raw.pop("key_env", None)
        unknown = set(raw) - KEYS
        if unknown:
            return None, [{"field": "settings", "message": "the settings have fields this app does not know"}]
        key_env = raw.get("key_env")
        # a hand-written name that does not look like a provider key is kept only for the endpoint it was
        # written for: the app never sends it anywhere the owner did not name by hand
        kept = (self.config is not None and key_env == self.config.key_env
                and raw.get("base_url") == self.config.endpoint.base_url)
        if isinstance(key_env, str) and not kept and not APP_KEY_ENV.fullmatch(key_env):
            # never echoed: a key pasted here by mistake must not come back to the screen
            return None, [{"field": "key_env", "message": "name the key's environment variable, in capitals, "
                                                          "ending _API_KEY (such as OPENROUTER_API_KEY)"}]
        try:
            return parse_model_config(raw), []
        except ConfigError as error:
            message = _config_message(error)
            field = next((key for key in sorted(KEYS) if message.startswith(key)), "settings")
            return None, [{"field": field, "message": message}]

    def _check(self, args: Mapping[str, object]) -> dict:
        """Check a draft without saving it or sending anything: what a save would make of it."""
        config, problems = self._draft(args)
        result = {"ok": config is not None, "problems": problems, "sent": False, "routes": [], "local": None,
                  "key_set": None}
        if config is not None:
            result["routes"] = [route_label(config.endpoint, model) for model in config.models]
            result["local"] = config.endpoint.loopback_literal
            result["key_set"] = None if config.key_env is None else bool(self.environ.get(config.key_env))
            if result["key_set"] is False:
                result["problems"] = [{"field": "key_env", "message": f"{config.key_env} is not set where the app "
                                                                      "runs: saved settings would generate nothing"}]
        return result

    def _save(self, args: Mapping[str, object]) -> dict:
        """Write the draft at the revision read (``expected``: ``models``' ``etag``, ``null`` for no file yet) and
        play the next turn by it."""
        if "expected" not in args:
            raise ApiRefused("invalid_field", "expected is the etag models gave (null: no settings file yet)")
        expected = args["expected"]
        if expected is not None and not (isinstance(expected, str) and is_etag(expected)):
            raise ApiRefused("invalid_field", "expected must be the etag models gave, or null")
        config, problems = self._draft(args)
        if config is None:
            raise ApiRefused("invalid_settings", problems[0]["message"])
        self._loaded = self.settings.write(config, expected)
        self._build(config)
        return self._models({})

    # -- usage ---------------------------------------------------------------------------------------------
    def _usage(self, args: Mapping[str, object]) -> dict:
        text = args.get("chat")
        if not isinstance(text, str):
            raise ApiRefused("invalid_field", "chat must be a str")
        try:
            chat = ChatRef.parse(text)
        except (ValueError, TypeError):
            raise ApiRefused("invalid_field", "chat is not a valid reference") from None
        limit = args.get("limit", DEFAULT_TURNS)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TURNS:
            raise ApiRefused("invalid_field", f"limit must be 1 to {MAX_TURNS}")
        skip = args.get("skip", 0)  # the newest turns left out, so earlier ones can be listed
        if isinstance(skip, bool) or not isinstance(skip, int) or skip < 0:
            raise ApiRefused("invalid_field", "skip must be a whole number of turns, 0 or more")
        try:
            scene = self.text_api.runner.open(chat)
        except SceneRefused as error:
            raise ApiRefused(getattr(error, "reason", None) or "refused", str(error)) from None
        except StoreError as error:  # nothing was written: reading only
            raise ApiRefused(type(error).__name__, str(error)) from None
        names = {actor.instance_id: name for actor, name in scene.names.items()}
        stored = [(item, False) for item in scene.turns.turns] + [(item, True) for item in scene.turns.set_aside]
        stored.sort(key=lambda pair: (pair[0].record.number, pair[1]))
        turns, everything, by_stage, unmetered, unsent = [], [], {}, {}, {}
        for item, set_aside in stored:
            record = item.record
            entries = []
            for take in record.takes:
                for call in take.calls:
                    entries.append(_call_view(call, names, take=take.take, state=take.state))
                if take.declined:
                    entries.append({"take": take.take, "state": take.state, "declined": list(take.declined)})
            for label in record.labels:
                for call in label.calls:
                    entries.append(_call_view(call, names, label=label.label, state=label.state))
            for entry in entries:
                attempts = entry.get("_attempts", ())
                if "stage" in entry:
                    everything.extend(attempts)
                    by_stage.setdefault(entry["stage"], []).extend(attempts)
                    unmetered[entry["stage"]] = unmetered.get(entry["stage"], 0) + (not attempts)
                    unsent[entry["stage"]] = unsent.get(entry["stage"], 0) + (
                        not attempts and entry["status"] == "not_issued")
            turns.append({"turn": record.turn_id, "number": record.number, "set_aside": set_aside,
                          "calls": [{key: value for key, value in entry.items() if key != "_attempts"}
                                    for entry in entries]})
        problems = [{"code": code, "detail": detail} for code, detail in scene.problems]
        end = max(len(turns) - skip, 0)
        window = turns[max(end - limit, 0):end]
        return {
            "chat": str(chat), "turns": window, "skip": skip, "listed": len(window), "of": len(turns),
            "total": _totals(everything, sum(unmetered.values()), sum(unsent.values())),
            "by_stage": [{"stage": stage, **_totals(attempts, unmetered[stage], unsent[stage])}
                         for stage, attempts in sorted(by_stage.items())],
            "complete": scene.turns.complete, "problems": problems,
        }

    # -- diagnostics ---------------------------------------------------------------------------------------
    def _diagnostics(self, args: Mapping[str, object]) -> dict:
        if self.traces is None:
            return {"available": False, "reason": "the runner's traces go to another sink", "traces": []}
        traces = []
        for trace in reversed(self.traces.traces):
            costs = call_costs(trace)
            traces.append({
                "operation": trace.operation, "status": trace.status, "seconds": round(trace.seconds, 6),
                "spans": [{"operation": item.operation, "count": item.count, "seconds": round(item.seconds, 6),
                           "self_seconds": round(item.self_seconds, 6)} for item in trace.breakdown()],
                "calls": [{"stage": role.stage, "actor": role.actor_id, "calls": role.calls,
                           "attempts": role.attempts, "retries": role.retries, "failed": role.failed,
                           "lost": role.lost, "latency": role.latency, "provider_seconds": role.provider_seconds,
                           "first_byte_seconds": role.first_byte_seconds, "cost": None,
                           "cost_state": role.cost_state, "usage": _usage_totals(role.usage)}
                          for role in costs.roles],
            })
        return {"available": True, "kept": self.keep, "traces": traces}


def _tokens(usage) -> dict:  # type: ignore[no-untyped-def]
    return {name: getattr(usage, name) for name in TOKEN_FIELDS}


def _call_view(call, names: Mapping[str, str], *, state: str, take: int | None = None,  # type: ignore[no-untyped-def]
               label: int | None = None) -> dict:
    kind = "baseline" if call.stage is InferenceStage.NPC_GENERATION else "optional"
    return {
        "take": take, "label": label, "state": state, "phase": call.phase, "stage": call.stage.value, "kind": kind,
        "actor": call.actor_id, "name": None if call.actor_id is None else names.get(call.actor_id),
        "status": call.status, "reason": call.reason,
        "attempts": [{"attempt": attempt.attempt, "outcome": attempt.outcome, "measurement": attempt.measurement.value,
                      "route": attempt.route, "tokens": _tokens(attempt.usage),
                      "cost": None, "cost_state": "unpriced"} for attempt in call.attempts],
        "_attempts": call.attempts,
    }


def _usage_totals(totals: UsageTotals) -> dict:
    return {"attempts": totals.attempts, "sums": dict(totals.sums), "unreported": dict(totals.unreported),
            "lower_bound": {name: totals.unreported[name] > 0 for name in TOKEN_FIELDS},
            "measurements": sorted(m.value for m in totals.measurements)}


def _totals(attempts, unmetered: int, unsent: int) -> dict:  # type: ignore[no-untyped-def]
    """Every attempt summed as 004 sums them: an unreported count is counted, never taken as zero. A planned
    call with no attempt booked is unmetered (004); ``not_issued`` of them were never sent, and what the rest used
    is unknown, not free. No prices: the cost is unknown."""
    view = _usage_totals(UsageTotals.of(attempts))
    view["unmetered_calls"] = unmetered
    view["not_issued_calls"] = unsent
    if unmetered > unsent:  # a call issued with no attempt booked used something unknown: every sum is a floor
        view["lower_bound"] = {name: True for name in TOKEN_FIELDS}
    view["cost"] = None
    view["cost_state"] = "unpriced"
    return view


__all__ = ["APP_KEY_ENV", "InferenceApi", "SettingsFile", "UNAVAILABLE"]
