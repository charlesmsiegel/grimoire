"""Thin OpenRouter (OpenAI-compatible) client with normalized errors."""

from __future__ import annotations

import json
import os
import ssl
from typing import AsyncIterator

import certifi
import httpx

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind = kind  # missing_key | auth | rate_limit | network | bad_response
        self.detail = detail or kind


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    return "bad_response"


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
        return {"model": model, "messages": messages, "stream": stream}

    def _headers(self, key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def stream(self, messages, model: str, key: str) -> AsyncIterator[str]:
        if not key:
            raise OpenRouterError("missing_key", "OpenRouter API key is not set")
        try:
            http = self._client()
            async with http.stream(
                "POST", API_URL, headers=self._headers(key),
                json=self._payload(messages, model, True),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise OpenRouterError(_status_kind(resp.status_code), resp.text)
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
        except OpenRouterError:
            raise
        except httpx.HTTPError as exc:
            raise OpenRouterError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenRouterError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, key)])

    async def aclose(self) -> None:
        if self._owns and self._http is not None:
            await self._http.aclose()
