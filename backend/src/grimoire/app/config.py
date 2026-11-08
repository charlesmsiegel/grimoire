"""The app's model configuration (044): one OpenAI-compatible endpoint, from a small JSON file.

The file names the endpoint and the model, and the environment variable that holds the key. The key itself
is never in the file or the library. The runtime calls the model under a trusted ``app:runtime`` handle,
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
                  "director"})
#: Calls a director reply (a scene with no NPC that can answer) may make per turn; 0 turns the stage off.
DEFAULT_DIRECTOR_CALLS = 1


class ConfigError(ValueError):
    """The model configuration cannot be used; the app does not start."""


@dataclass(frozen=True)
class ModelConfig:
    endpoint: ProviderEndpoint
    model: str
    key_env: str | None
    max_output_tokens: int
    director_calls: int


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
    for name, value in (("max_output_tokens", tokens), ("director", director)):
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "max_output_tokens" else 0):
            raise ConfigError(f"{name} must be a whole number")
    try:
        endpoint = ProviderEndpoint(raw["provider_id"], raw["base_url"], needs_key=key_env is not None,
                                    max_tokens_field=raw.get("max_tokens_field", "max_tokens"))
    except (ValueError, TypeError) as error:
        raise ConfigError(str(error)) from None
    return ModelConfig(endpoint, raw["model"], key_env, tokens, director)


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
    runtime = GenerationRuntime(adapter, (RouteChoice(config.endpoint.provider_id, config.model),), handle,
                                max_output_tokens=config.max_output_tokens)
    caps = {"director_generation": config.director_calls} if config.director_calls else {}
    return runtime, InferencePolicy(caps)


__all__ = ["ConfigError", "ModelConfig", "build_runtime", "load_model_config", "parse_model_config"]
