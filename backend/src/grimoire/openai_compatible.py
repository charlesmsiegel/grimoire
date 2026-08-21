"""Chat-completions client for an arbitrary OpenAI-compatible endpoint —
same wire protocol/error-shape as openrouter.py, but base_url is
caller-supplied and the API key is optional. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md.
"""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import AsyncIterator

import certifi
import httpx

from . import catalog, llm_usage
from .llm_errors import LLMError, retry_after_seconds

#: Bound for the health probe (#146). The client's own 120s default is sized
#: for a generation; a reader who pressed "Test connection" is watching a
#: spinner, and two minutes of one is indistinguishable from a hung app. The
#: same value and the same reasoning as `openrouter.PROBE_TIMEOUT` — separate
#: because these two modules deliberately share no code (see the module
#: docstring), not because the number is a different one.
PROBE_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class OpenAICompatibleError(LLMError):
    pass


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    return "bad_response"


def _extract_error(text: str) -> str:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip()
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    if isinstance(err, dict):
        return str(err.get("message") or err.get("detail") or err)
    return str(err)


def _strict_messages(messages: list[dict]) -> list[dict]:
    """Fold system messages into adjacent user turns and guarantee the result
    starts with role=user and alternates strictly — required by chat-completion
    backends (e.g. z.ai's GLM coding endpoint) that reject a system message
    mid-conversation or a non-user opening turn."""
    folded: list[dict] = []
    pending: list[str] = []

    def flush(extra: str = "") -> str:
        nonlocal pending
        parts = [*pending, extra] if extra else list(pending)
        pending = []
        return "\n\n".join(parts)

    def append(role: str, content: str) -> None:
        if folded and folded[-1]["role"] == role:
            folded[-1]["content"] += "\n\n" + content
        else:
            folded.append({"role": role, "content": content})

    for m in messages:
        if m["role"] == "system":
            pending.append(m["content"])
        elif m["role"] == "user":
            append("user", flush(m["content"]))
        elif m["role"] == "assistant":
            if pending:
                append("user", flush())
            append("assistant", m["content"])
        else:
            # grimoire's context/assemble.py only ever emits system/user/assistant
            # today, but silently folding an unrecognized role into
            # "assistant" would misattribute its content as model-authored.
            raise OpenAICompatibleError("bad_response", f"unsupported message role: {m['role']!r}")
    if pending:
        append("user", flush())
    if not folded or folded[0]["role"] != "user":
        folded.insert(0, {"role": "user", "content": "(continue)"})
    return folded


class OpenAICompatibleClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _verify(self) -> ssl.SSLContext:
        cert = os.environ.get("SSL_CERT_FILE")
        cafile = cert if cert and os.path.exists(cert) else certifi.where()
        return ssl.create_default_context(cafile=cafile)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=30.0), verify=self._verify()
            )
        return self._http

    def _headers(self, key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def stream(self, messages, model: str, key: str, base_url: str,
                      strict: bool = False, usage: dict | None = None) -> AsyncIterator[str]:
        """`usage` is filled in place when the endpoint volunteers an accounting
        block — see `llm_usage`.

        Nothing is *asked* for, and that asymmetry with `openrouter` is
        deliberate (#152). The OpenAI spec's way to request one is
        `stream_options: {"include_usage": true}`, and `base_url` here points at
        whatever the user configured: llama.cpp, vLLM, LM Studio, a vendor's
        own gateway. A strict endpoint rejects a request field it does not know
        with a 400 — this module already carries `_strict_messages` because one
        such endpoint refused a message ordering — and trading "generation
        works" for "generation is counted" is the wrong way round. Endpoints
        that report usage unprompted (many do) are recorded; the rest land in
        the ledger as unpriced calls, which `store.usage` counts rather than
        hides."""
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        payload_messages = _strict_messages(messages) if strict else messages
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {"model": model, "messages": payload_messages, "stream": True}
        try:
            http = self._client()
            async with http.stream(
                "POST", url, headers=self._headers(key), json=payload,
                # The facade owns the read bound (#243) — a read timeout here
                # would cap the configured one at 120s, including "0 = no
                # bound", which is exactly the slow-local-endpoint case this
                # setting exists for. list_models keeps the client default.
                timeout=httpx.Timeout(None, connect=30.0, write=30.0, pool=30.0),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    # The provider's own window, when it names one. A guessed
                    # backoff is what you use for not knowing; Retry-After is
                    # knowing (#144).
                    raise OpenAICompatibleError(_status_kind(resp.status_code),
                                               _extract_error(resp.text),
                                               retry_after_seconds(resp.headers))
                async for line in resp.aiter_lines():
                    # Proof of life for the facade's idle bound, including the
                    # frames dropped below (keep-alives, and deltas carrying
                    # only reasoning_content) — see openrouter.stream (#243).
                    yield ""
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    # Ahead of the delta lookup: a usage block rides a chunk
                    # with no choices, so reading it after would skip it.
                    llm_usage.from_openai_chunk(obj, usage)
                    try:
                        delta = obj["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenAICompatibleError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str, base_url: str,
                       strict: bool = False, usage: dict | None = None) -> str:
        return "".join([chunk async for chunk
                        in self.stream(messages, model, key, base_url, strict, usage)])

    async def list_models(self, base_url: str, key: str,
                          bound: httpx.Timeout | None = None) -> list[dict]:
        """This endpoint's catalog. `bound` overrides the client's own timeout,
        which is sized for a generation — see `PROBE_TIMEOUT`. (Not spelled
        `timeout`: a parameter by that name on an async function is what
        ASYNC109 flags, and it means the httpx one either way.)"""
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        url = base_url.rstrip("/") + "/models"
        try:
            http = self._client()
            headers = self._headers(key)
            # Two spellings rather than a `**kwargs` splat: httpx distinguishes
            # "the client's default" (the argument absent) from "no timeout at
            # all" (`timeout=None`), so there is no single value that means
            # "leave it alone" -- and a splatted dict types as `Any` at every
            # other parameter of `get`, which is five mypy errors for one call.
            resp = await (http.get(url, headers=headers, timeout=bound) if bound is not None
                          else http.get(url, headers=headers))
            if resp.status_code >= 400:
                raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text))
            data = resp.json().get("data", [])
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        return catalog.entries(data)

    async def probe(self, base_url: str, key: str) -> None:
        """Ask this endpoint whether it is up and accepts this key (#146).

        `/models` is the probe because it is the only thing an OpenAI-compatible
        server is asked for that costs nothing to answer — the alternative is a
        one-token completion, which on a metered gateway charges the reader for
        clicking "Test connection". Every server this kind exists for
        (llama.cpp, vLLM, LM Studio, ollama, the vendor gateways) serves it.

        The gap that leaves, stated rather than hidden: a server that generates
        happily but exposes no catalog answers 404, and this reports that as
        `bad_response` — "reached something at that URL; it did not answer the
        question". That is a false alarm for such a server, and the honest one:
        nothing short of spending a generation can tell it apart from a base
        URL with a typo in it, and reporting healthy on the strength of *any*
        HTTP response would call a 404 from an unrelated web server a working
        LLM endpoint.

        The catalog's own 120s bound is not this call's: the reader is watching
        a spinner, not a generation. `list_models` keeps its when it is being
        used as a catalog.
        """
        await self.list_models(base_url, key, bound=PROBE_TIMEOUT)

    async def aclose(self) -> None:
        # Reset, not just close: `_client()` is lazy, so leaving the closed
        # client in place would hand it back to the next caller (and every
        # request through it raises). `EmbeddingsClient.close` does the same.
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None
