"""The app's model configuration (044): one OpenAI-compatible endpoint, from a small JSON file.

The file names the endpoint, the model (and fallback models on the same endpoint, 112), and the environment
variable that holds the key. The key itself is never in the file or the library. The runtime calls the model under a trusted ``app:runtime`` handle,
granted ``EXECUTE`` on ``providers`` by an in-memory authorizer: the app's own handle for the configured
provider only (013, the audit's call (b)). Those provider decisions are not recorded in the library's
durable security ledger.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from grimoire.core.inference import InferencePolicy
from grimoire.providers import EnvironmentCredentials, ProviderEndpoint, ProviderRegistry, RouteChoice
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.transport import HttpTransport
from grimoire.runtime import GenerationRuntime
from grimoire.security import Authorizer, Grant, Permission, ResourcePath

RUNTIME_PRINCIPAL = "app:runtime"
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
KEYS = frozenset({"provider_id", "base_url", "model", "key_env", "max_tokens_field", "max_output_tokens",
                  "director", "fallbacks", "attempts"})
#: Calls a director reply (a scene with no NPC that can answer) may make per turn; 0 turns the stage off.
DEFAULT_DIRECTOR_CALLS = 1
#: Fallback models a configuration may name (112): each is tried only by a retry, so only with ``attempts`` > 1.
MAX_FALLBACKS = 4
#: Attempts per planned call (004's ``max_attempts_per_call``): every one may be billed, so it stays small.
MAX_ATTEMPTS = 3
MAX_OUTPUT_TOKENS = 32_768


class ConfigError(ValueError):
    """The model configuration cannot be used; the app does not start."""


@dataclass(frozen=True)
class ModelConfig:
    endpoint: ProviderEndpoint
    model: str
    key_env: str | None
    max_output_tokens: int
    director_calls: int
    fallbacks: tuple[str, ...] = ()
    attempts: int = 1

    @property
    def models(self) -> tuple[str, ...]:
        """The primary model, then its fallbacks: attempt *i* of a call uses the *i*-th (013)."""
        return (self.model, *self.fallbacks)


def load_model_config(path: Path) -> ModelConfig:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ConfigError(f"{path}: cannot read the model configuration ({error})") from None
    return parse_model_config(raw)


def parse_model_config(raw: object) -> ModelConfig:
    if not isinstance(raw, dict):
        raise ConfigError("the model configuration must be a JSON object")
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise ConfigError(f"unknown keys: {', '.join(unknown)}")
    for key in ("provider_id", "base_url", "model"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            raise ConfigError(f"{key} is required, as a string")
    key_env = raw.get("key_env")
    if key_env is not None and (not isinstance(key_env, str) or not ENV_NAME.fullmatch(key_env)):
        # never echoed: a key pasted here by mistake must not reach the console
        raise ConfigError("key_env must be the name of an environment variable (letters, digits, _), not a key")
    tokens = raw.get("max_output_tokens", 1024)
    director = raw.get("director", DEFAULT_DIRECTOR_CALLS)
    attempts = raw.get("attempts", 1)
    for name, value, low, high in (("max_output_tokens", tokens, 1, MAX_OUTPUT_TOKENS), ("director", director, 0, 1_000),
                                   ("attempts", attempts, 1, MAX_ATTEMPTS)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            # a director reply is at most one per turn whatever the cap (004); 044 took any count
            raise ConfigError(f"{name} must be a whole number from {low} to {high}")
    fallbacks = raw.get("fallbacks", [])
    if not isinstance(fallbacks, list) or len(fallbacks) > MAX_FALLBACKS:
        raise ConfigError(f"fallbacks must be a list of at most {MAX_FALLBACKS} model names")
    try:
        endpoint = ProviderEndpoint(raw["provider_id"], raw["base_url"], needs_key=key_env is not None,
                                    max_tokens_field=raw.get("max_tokens_field", "max_tokens"))
        for model in (raw["model"], *fallbacks):
            RouteChoice(endpoint.provider_id, model)
    except (ValueError, TypeError) as error:
        raise ConfigError(str(error)) from None
    if len(set(fallbacks)) != len(fallbacks) or raw["model"] in fallbacks:
        raise ConfigError("fallbacks must be other models, each named once")
    return ModelConfig(endpoint, raw["model"], key_env, tokens, director, tuple(fallbacks), attempts)


def config_json(config: ModelConfig) -> dict:
    """``config`` as the file holds it: what ``parse_model_config`` reads back as the same configuration."""
    raw: dict = {"provider_id": config.endpoint.provider_id, "base_url": config.endpoint.base_url,
                 "model": config.model, "max_tokens_field": config.endpoint.max_tokens_field,
                 "max_output_tokens": config.max_output_tokens, "director": config.director_calls,
                 "fallbacks": list(config.fallbacks), "attempts": config.attempts}
    if config.key_env is not None:
        raw["key_env"] = config.key_env
    return raw


def policy_for(config: ModelConfig) -> InferencePolicy:
    """The 004 policy a configuration plays turns under: a director reply only when enabled, and its attempts."""
    caps = {"director_generation": config.director_calls} if config.director_calls else {}
    return InferencePolicy(caps, max_attempts_per_call=config.attempts)


def build_runtime(config: ModelConfig, owner: str, *, environ=os.environ, transport=None):  # type: ignore[no-untyped-def]
    """The 027 runtime and the 012 policy for ``config``. A key variable that is not set is refused now, not
    at the first turn."""
    if config.key_env is not None and not environ.get(config.key_env):
        raise ConfigError(f"the environment variable {config.key_env} is not set")
    authorizer = Authorizer({owner})
    authorizer.add_grant(Grant("app-runtime-providers", RUNTIME_PRINCIPAL, frozenset({Permission.EXECUTE}),
                               ResourcePath(("providers",)), owner))
    handle = authorizer.handle_for(RUNTIME_PRINCIPAL, trusted=True)
    credentials = EnvironmentCredentials({config.endpoint.provider_id: config.key_env} if config.key_env else {})
    adapter = ProviderAdapter(ProviderRegistry([config.endpoint]), credentials, authorizer,
                              transport if transport is not None else HttpTransport())
    routes = tuple(RouteChoice(config.endpoint.provider_id, model) for model in config.models)
    runtime = GenerationRuntime(adapter, routes, handle, max_output_tokens=config.max_output_tokens)
    return runtime, policy_for(config)


__all__ = ["ConfigError", "ModelConfig", "build_runtime", "config_json", "load_model_config", "parse_model_config",
           "policy_for"]
