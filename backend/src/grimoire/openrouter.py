"""Thin OpenRouter (OpenAI-compatible) client with normalized errors."""

from __future__ import annotations

import json
import os
import ssl
from typing import AsyncIterator

import certifi
import httpx

from . import llm_usage
from .llm_errors import LLMError, retry_after_seconds

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(LLMError):
    pass


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    return "bad_response"


def _extract_error(text: str) -> str:
    """Pull a human-readable message out of an OpenRouter error body."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip()
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    if isinstance(err, dict):
        return str(err.get("message") or err.get("detail") or err)
    return str(err)


class OpenRouterClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _verify(self) -> ssl.SSLContext:
        # httpx trusts $SSL_CERT_FILE, but a stale value (e.g. left by a removed
        # conda install) points at a missing file and crashes TLS setup. Honor a
        # valid override; otherwise fall back to certifi's bundle.
        cert = os.environ.get("SSL_CERT_FILE")
        cafile = cert if cert and os.path.exists(cert) else certifi.where()
        return ssl.create_default_context(cafile=cafile)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=30.0), verify=self._verify()
            )
        return self._http

    def _payload(self, messages, model, stream):
        # `usage.include` is what makes OpenRouter attach token counts and the
        # call's cost in credits to the final SSE chunk (#152). Free, and
        # accepted by every model on the platform -- unlike the equivalent
        # option on an arbitrary endpoint, which `openai_compatible` therefore
        # does not send.
        return {"model": model, "messages": messages, "stream": stream,
                "usage": {"include": True}}

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def stream(self, messages, model: str, key: str,
                     usage: dict | None = None) -> AsyncIterator[str]:
        """`usage`, when given, is filled in place with what the provider
        reported about this call — see `llm_usage`. It arrives on the last
        chunk, long after the caller has consumed the deltas it wanted, which
        is why it comes back through a holder rather than a return value."""
        if not key:
            raise OpenRouterError("missing_key", "OpenRouter API key is not set")
        try:
            http = self._client()
            async with http.stream(
                "POST", API_URL, headers=self._headers(key),
                json=self._payload(messages, model, True),
                # The facade owns the read bound (#243) — it is the configurable,
                # provider-independent one, and a read timeout here would cap it
                # at 120s no matter what the user set, including "0 = no bound".
                # Every other bound stays.
                timeout=httpx.Timeout(None, connect=30.0, write=30.0, pool=30.0),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    # The provider's own window, when it names one. A guessed
                    # backoff is what you use for not knowing; Retry-After is
                    # knowing (#144).
                    raise OpenRouterError(_status_kind(resp.status_code),
                                         _extract_error(resp.text),
                                         retry_after_seconds(resp.headers))
                async for line in resp.aiter_lines():
                    # Every frame is proof of life, including the ones this
                    # parser drops: a comment keep-alive, or a delta carrying
                    # only `reasoning`. The facade times the gap between yields,
                    # so silence here would read as a wedged upstream (#243).
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
                    # Before the delta lookup, not after it: the chunk carrying
                    # the usage block has an empty `choices` (or none at all),
                    # so reading it inside the same try as the delta would skip
                    # accounting on exactly the frame that carries it.
                    llm_usage.from_openai_chunk(obj, usage)
                    try:
                        delta = obj["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except OpenRouterError:
            raise
        except httpx.HTTPError as exc:
            raise OpenRouterError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenRouterError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str,
                       usage: dict | None = None) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, key, usage)])

    async def aclose(self) -> None:
        # Reset, not just close: `_client()` is lazy, so leaving the closed
        # client in place would hand it back to the next caller (and every
        # request through it raises). `EmbeddingsClient.close` does the same.
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None
