"""Shared OpenAI-style chat-completions wire handling.

openrouter.py and openai_compatible.py speak the same protocol and normalize
failures into the same `LLMError` shape; they differ only in where the endpoint
comes from, whether the API key is required, and which `LLMError` subclass they
raise. That common part — the TLS-pinned httpx client, the SSE decode loop, and
the error mapping — lives here so a fix to any of it lands in both.

Subclasses set `error_cls` and supply their own `stream()` policy.
"""

from __future__ import annotations

import contextlib
import json
import os
import ssl
from typing import AsyncIterator, Iterator

import certifi
import httpx

from .llm import LLMError


def status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    return "bad_response"


def extract_error(text: str) -> str:
    """Pull a human-readable message out of an OpenAI-style error body."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip()
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    if isinstance(err, dict):
        return str(err.get("message") or err.get("detail") or err)
    return str(err)


class ChatCompletionsClient:
    """Base for the OpenAI-compatible chat-completions clients."""

    error_cls: type[LLMError] = LLMError

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

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    @contextlib.contextmanager
    def _normalized_errors(self) -> Iterator[None]:
        """Map transport and setup failures onto this client's error type. An
        error we already raised passes through unchanged, so a 4xx stays
        `auth`/`rate_limit`/`bad_response` instead of being relabelled
        `network`."""
        try:
            yield
        except self.error_cls:
            raise
        except httpx.HTTPError as exc:
            raise self.error_cls("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise self.error_cls("network", str(exc)) from exc

    async def _stream_chat(self, url: str, key: str, payload: dict) -> AsyncIterator[str]:
        """POST a streaming chat completion and yield each content delta."""
        with self._normalized_errors():
            http = self._client()
            async with http.stream("POST", url, headers=self._headers(key),
                                   json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise self.error_cls(status_kind(resp.status_code), extract_error(resp.text))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta

    async def aclose(self) -> None:
        if self._owns and self._http is not None:
            await self._http.aclose()
