"""Chat-completions client for an arbitrary OpenAI-compatible endpoint —
same wire protocol/error-shape as openrouter.py, but base_url is
caller-supplied and the API key is optional. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import AsyncIterator

import certifi
import httpx

from .llm import LLMError


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
        # Adjacent same-role runs created by *folding* (consecutive system
        # messages accumulated in `pending`, then joined by flush()) are
        # already merged into one string before this is called. Merging here
        # too would also collapse a folded system-turn into whatever real
        # user turn preceded it mid-conversation, which is wrong: see
        # test_strict_system_before_assistant_becomes_its_own_user_turn.
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
            # grimoire's context.py only ever emits system/user/assistant
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
                      strict: bool = False) -> AsyncIterator[str]:
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        payload_messages = _strict_messages(messages) if strict else messages
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {"model": model, "messages": payload_messages, "stream": True}
        try:
            http = self._client()
            async with http.stream("POST", url, headers=self._headers(key), json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text))
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
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenAICompatibleError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str, base_url: str, strict: bool = False) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, key, base_url, strict)])

    async def list_models(self, base_url: str, key: str) -> list[dict]:
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        url = base_url.rstrip("/") + "/models"
        try:
            http = self._client()
            resp = await http.get(url, headers=self._headers(key))
            if resp.status_code >= 400:
                raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text))
            data = resp.json().get("data", [])
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        out = []
        for m in data:
            pricing = m.get("pricing") or {}
            out.append({
                "id": m["id"], "name": m.get("name") or m["id"],
                "context": m.get("context_length"),
                "prompt": pricing.get("prompt"), "completion": pricing.get("completion"),
            })
        return out

    async def aclose(self) -> None:
        if self._owns and self._http is not None:
            await self._http.aclose()
