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
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from grimoire.core.inference import InferencePolicy
from grimoire.providers import EnvironmentCredentials, ProviderEndpoint, ProviderRegistry, RouteChoice
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.transport import HttpTransport
from grimoire.runtime import GenerationRuntime
from grimoire.narration_review import ExtractionRuntime
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest

RUNTIME_PRINCIPAL = "app:runtime"
EXTRACTOR_PRINCIPAL = "app:continuity-extractor"
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
KEYS = frozenset({"provider_id", "base_url", "model", "key_env", "max_tokens_field", "max_output_tokens",
                  "director", "fallbacks", "attempts", "price_prompt", "price_reply", "price_cached", "critique"})
#: The optional prices, per million tokens, of the configured ``model`` (not of its fallbacks): what one prompt
#: token, one reply token and one cached prompt token cost. Decimal text, never a JSON number, so no price is
#: ever a float; no currency is recorded (the number is in whatever currency the provider bills).
PRICE_KEYS = ("price_prompt", "price_reply", "price_cached")
#: Up to 999999.999999: at most six digits before the point and six after it, and nothing else (no sign, no
#: exponent, no spaces), so a price is finite, non-negative and bounded by its form alone.
PRICE_TEXT = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6})?")
#: Calls a director reply (a scene with no NPC that can answer) may make per turn; 0 turns the stage off.
DEFAULT_DIRECTOR_CALLS = 1
#: Attempts per planned call (004's ``max_attempts_per_call``): every one may be billed, so it stays small.
MAX_ATTEMPTS = 3
#: Fallback models a configuration may name (112): attempt *i* uses the *i*-th model, and 013's meter refuses a
#: call given more routes than attempts, so each fallback needs an attempt of its own.
MAX_FALLBACKS = MAX_ATTEMPTS - 1
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
    price_prompt: Decimal | None = None
    price_reply: Decimal | None = None
    price_cached: Decimal | None = None
    critique_calls: int = 0

    @property
    def models(self) -> tuple[str, ...]:
        """The primary model, then its fallbacks: attempt *i* of a call uses the *i*-th (013)."""
        return (self.model, *self.fallbacks)

    @property
    def priced(self) -> bool:
        """Whether a cost can be worked out: both the prompt and the reply price are set."""
        return self.price_prompt is not None and self.price_reply is not None


@dataclass(frozen=True)
class ProviderAccess:
    """What sends a configuration's calls: 013's adapter, the routes attempts use in order, and the app's
    trusted handle. A turn's replies and a test call both go through these, and nothing else sends."""

    adapter: ProviderAdapter
    routes: tuple[RouteChoice, ...]
    handle: object
    extractor: ExtractionRuntime | None = None


def parse_price(value: object, name: str) -> Decimal | None:
    """A price as the file holds it: ``None`` (not set) or decimal text. Anything else is refused, a JSON
    number included: money is never read through a float."""
    if value is None:
        return None
    if not isinstance(value, str) or not PRICE_TEXT.fullmatch(value):
        raise ConfigError(f"{name} must be a price written as text, such as \"3\" or \"0.25\": 0 to 999999.999999, "
                          "with at most 6 decimal places")
    return Decimal(value)


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
    critique = raw.get("critique", 0)
    for name, value, low, high in (("max_output_tokens", tokens, 1, MAX_OUTPUT_TOKENS), ("director", director, 0, 1_000),
                                   ("attempts", attempts, 1, MAX_ATTEMPTS), ("critique", critique, 0, 1)):
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
    if len(fallbacks) >= attempts:  # attempt i uses model i: a model no attempt reaches would halt every call (013)
        raise ConfigError(f"fallbacks are tried only by retries: with {attempts} attempt(s) per call, name at most "
                          f"{attempts - 1} fallback model(s)")
    prices = [parse_price(raw.get(name), name) for name in PRICE_KEYS]
    return ModelConfig(endpoint, raw["model"], key_env, tokens, director, tuple(fallbacks), attempts, *prices, critique)


def config_json(config: ModelConfig) -> dict:
    """``config`` as the file holds it: what ``parse_model_config`` reads back as the same configuration."""
    raw: dict = {"provider_id": config.endpoint.provider_id, "base_url": config.endpoint.base_url,
                 "model": config.model, "max_tokens_field": config.endpoint.max_tokens_field,
                 "max_output_tokens": config.max_output_tokens, "director": config.director_calls,
                 "fallbacks": list(config.fallbacks), "attempts": config.attempts}
    if config.critique_calls:
        raw["critique"] = config.critique_calls
    if config.key_env is not None:
        raw["key_env"] = config.key_env
    for name in PRICE_KEYS:  # written only when set, so a configuration with no prices is the file it always was
        price = getattr(config, name)
        if price is not None:
            raw[name] = format(price, "f")
    return raw


def policy_for(config: ModelConfig) -> InferencePolicy:
    """The 004 policy a configuration plays turns under: a director reply only when enabled, and its attempts."""
    caps = {"director_generation": config.director_calls} if config.director_calls else {}
    if config.critique_calls:
        caps["critique"] = config.critique_calls
    return InferencePolicy(caps, max_attempts_per_call=config.attempts)


def build_access(config: ModelConfig, owner: str, *, environ=os.environ, transport=None) -> ProviderAccess:  # type: ignore[no-untyped-def]
    """The adapter, routes and handle ``config``'s calls go through. A key variable that is not set is refused
    now, not at the first call."""
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
    extractor = None
    if config.critique_calls:
        # The optional configuration and the subsequent Generate action authorize this bounded
        # extra request. Model text cannot choose a principal, route, payload or approval.
        authorizer.add_grant(Grant("app-extractor-providers", EXTRACTOR_PRINCIPAL,
                                   frozenset({Permission.EXECUTE}), ResourcePath(("providers",)), owner))
        extract_handle = authorizer.handle_for(EXTRACTOR_PRINCIPAL, trusted=False)

        def approvals(label: str, attempt: int, payload: bytes) -> str:
            approval_id = "extract-" + uuid.uuid4().hex
            authorizer.register_approval(Approval(
                approval_id, EXTRACTOR_PRINCIPAL, Permission.EXECUTE,
                ResourcePath(("providers", label.split("@", 1)[0])), payload_digest(payload), owner))
            return approval_id

        extractor = ExtractionRuntime(adapter, routes, extract_handle, approvals=approvals,
                                       max_output_tokens=min(config.max_output_tokens, 4096))
    return ProviderAccess(adapter, routes, handle, extractor)


class ConfiguredRuntime(GenerationRuntime):
    """Generation plus a separate optional service for the app's composition root.

    GenerationRuntime never invokes this extractor or interprets its result; SceneRunner does.
    Keeping the bundle here preserves build_runtime's existing (runtime, policy) return contract.
    """

    def __init__(self, *args, continuity_extractor=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.continuity_extractor = continuity_extractor


def runtime_for(access: ProviderAccess, config: ModelConfig) -> GenerationRuntime:
    """027's runtime over ``access``: the turn's replies go where a test call goes."""
    return ConfiguredRuntime(access.adapter, access.routes, access.handle,
                             max_output_tokens=config.max_output_tokens,
                             continuity_extractor=access.extractor if config.critique_calls else None)


def build_runtime(config: ModelConfig, owner: str, *, environ=os.environ, transport=None):  # type: ignore[no-untyped-def]
    """The 027 runtime and the 012 policy for ``config``. A key variable that is not set is refused now, not
    at the first turn."""
    access = build_access(config, owner, environ=environ, transport=transport)
    return runtime_for(access, config), policy_for(config)


__all__ = ["ConfigError", "ModelConfig", "PRICE_KEYS", "ProviderAccess", "build_access", "build_runtime", "config_json",
           "load_model_config", "parse_model_config", "parse_price", "policy_for", "runtime_for"]
