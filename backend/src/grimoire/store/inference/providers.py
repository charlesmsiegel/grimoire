"""The provider preset table, and inferring a preset from a connection.

A preset is a friendly front on an adapter (`kind`): it pre-fills the URL and
the billing, and contributes capability facts. The facts say what is true of
EVERY model behind the preset, never of one model:

- `always`   -- every model behind it can (`generate`, `stream`).
- `never`    -- the wire protocol cannot (nothing below may claim past it).
- `possible` -- not ruled out; a per-model source decides.

Pure: no I/O, no store reads. A connection is a plain dict, so this module
needs nothing from `llm_connections` (the test holds the kinds to it).
"""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import urlsplit

BILLINGS: tuple[str, ...] = ("metered", "subscription")

#: The capability vocabulary (spec 6.2).
CAPABILITIES: frozenset[str] = frozenset({
    "generate", "stream", "vision", "embed", "decide_native",
    "structured_output", "prefill",
})


class Preset(NamedTuple):
    id: str
    label: str
    kind: str
    base_url: str
    url_locked: bool
    billing: str
    reports_price: bool
    always: frozenset[str]
    possible: frozenset[str]
    never: frozenset[str]


_GEN = frozenset({"generate", "stream"})
_NONE: frozenset[str] = frozenset()
_EVERYTHING_BUT_DECISION = CAPABILITIES - {"decide_native"}


def _preset(
    preset_id: str, label: str, kind: str, base_url: str, *, locked: bool,
    billing: str = "metered", reports_price: bool = False,
    always: frozenset[str], possible: frozenset[str], never: frozenset[str],
) -> Preset:
    return Preset(preset_id, label, kind, base_url, locked, billing, reports_price,
                  always, possible, never)


_ZAI_POSSIBLE = frozenset({"vision", "structured_output", "prefill"})
_ZAI_NEVER = frozenset({"embed", "decide_native"})

PRESETS: dict[str, Preset] = {p.id: p for p in (
    _preset(
        "openrouter", "OpenRouter", "openrouter", "https://openrouter.ai/api/v1",
        locked=True, reports_price=True, always=_GEN,
        possible=frozenset({"vision", "embed", "decide_native",
                            "structured_output", "prefill"}),
        never=_NONE),
    _preset(
        "anthropic", "Anthropic API", "anthropic", "https://api.anthropic.com",
        locked=True, always=_GEN,
        possible=frozenset({"vision", "structured_output"}),
        never=frozenset({"embed", "decide_native", "prefill"})),
    _preset(
        # The Agent SDK: no URL, and it reports a subscription-equivalent price.
        "claude", "Claude subscription", "claude", "",
        locked=False, billing="subscription", reports_price=True, always=_GEN,
        possible=_NONE,
        never=frozenset({"vision", "embed", "decide_native",
                         "structured_output", "prefill"})),
    _preset(
        "openai", "OpenAI", "openai_compatible", "https://api.openai.com/v1",
        locked=True, always=_GEN,
        possible=frozenset({"vision", "embed", "decide_native",
                            "structured_output", "prefill"}),
        never=_NONE),
    _preset(
        "zai", "z.ai", "openai_compatible", "https://api.z.ai/api/paas/v4",
        locked=True, always=_GEN, possible=_ZAI_POSSIBLE, never=_ZAI_NEVER),
    _preset(
        "zai_coding", "z.ai Coding Plan", "openai_compatible",
        "https://api.z.ai/api/coding/paas/v4",
        locked=True, billing="subscription", always=_GEN,
        possible=_ZAI_POSSIBLE, never=_ZAI_NEVER),
    _preset(
        "ollama", "Ollama", "openai_compatible", "http://localhost:11434/v1",
        locked=False, always=_NONE, possible=_EVERYTHING_BUT_DECISION,
        never=frozenset({"decide_native"})),
    _preset(
        "lmstudio", "LM Studio", "openai_compatible", "http://localhost:1234/v1",
        locked=False, always=_NONE, possible=_EVERYTHING_BUT_DECISION,
        never=frozenset({"decide_native"})),
    _preset(
        "custom", "Custom (OpenAI-compatible)", "openai_compatible", "",
        locked=False, always=_NONE, possible=_EVERYTHING_BUT_DECISION,
        never=frozenset({"decide_native"})),
)}

_OLLAMA_PORT = 11434
_LMSTUDIO_PORT = 1234


def _host_port_path(url: object) -> tuple[str, int | None, str]:
    """`(hostname, port, path)` of a base URL; empty/None parts when unparsable."""
    if not isinstance(url, str) or not url.strip():
        return "", None, ""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return "", None, ""
    return (parts.hostname or "").lower(), port, parts.path.rstrip("/")


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _by_url(url: object) -> str:
    host, port, path = _host_port_path(url)
    if host == "api.openai.com":
        return "openai"
    if host == "api.z.ai":
        # The coding plan's path sits beside the pay-as-you-go one, not under it.
        if _under(path, "/api/coding"):
            return "zai_coding"
        if _under(path, "/api/paas"):
            return "zai"
    if port == _OLLAMA_PORT:
        return "ollama"
    if port == _LMSTUDIO_PORT:
        return "lmstudio"
    return "custom"


def infer(conn: dict) -> Preset:
    """The preset a connection is on.

    An explicit, valid `conn["preset"]` wins; anything else is inferred from
    the adapter and, for `openai_compatible`, the base URL (spec 11.2 step 2).
    """
    explicit = conn.get("preset")
    if isinstance(explicit, str) and explicit in PRESETS:
        return PRESETS[explicit]
    kind = conn.get("kind")
    if kind in ("openrouter", "claude", "anthropic"):
        return PRESETS[str(kind)]
    return PRESETS[_by_url(conn.get("base_url"))]


def billing(conn: dict) -> str:
    """`metered` or `subscription`: the connection's own choice, else its preset's."""
    own = conn.get("billing")
    if isinstance(own, str) and own in BILLINGS:
        return own
    return infer(conn).billing
