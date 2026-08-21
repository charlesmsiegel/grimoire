"""Thin OpenRouter (OpenAI-compatible) client with normalized errors."""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import AsyncIterator

import certifi
import httpx

from . import catalog, llm_usage
from .llm_errors import LLMError, retry_after_seconds

#: Everything this provider is reached at hangs off one root. Spelled once
#: because there are now three endpoints on it -- generation, the model catalog
#: (#149) and the key probe (#146) -- and three copies of the host is three
#: places to edit when one of them moves.
BASE_URL = "https://openrouter.ai/api/v1"
API_URL = f"{BASE_URL}/chat/completions"
MODELS_URL = f"{BASE_URL}/models"
#: The endpoint a health check asks. Deliberately NOT `/models`, which is
#: public: it answers 200 for a key that is expired, revoked or gibberish, so a
#: check built on it would call a connection healthy right up until the first
#: turn failed with `auth` -- which is the "the topbar says a key is *set*, not
#: that it *works*" complaint #146 opens with. `/key` describes the credential
#: presented, so a bad one is a 401 and the check has a real answer.
KEY_URL = f"{BASE_URL}/key"
#: Bound for the two non-streaming calls above (the catalog and the probe). The
#: client's own 120s default is sized for a generation; a reader who clicked
#: "Test connection" is watching a spinner, and two minutes of one is
#: indistinguishable from a hung app. Connect gets the smaller half: a host that
#: will not accept a socket within ten seconds is not about to serve a catalog.
PROBE_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


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
        """`Authorization` only when there is something to authorize with.

        `stream` refuses an empty key before it ever reaches here, so from that
        path this reads as unconditional. The catalog deliberately does not
        refuse one -- OpenRouter's `/models` is public, and the setup wizard
        lists it before the reader has typed a key at all (#149) -- and
        `Bearer ` with nothing after it is a malformed credential, which a
        server is entitled to answer 401 to rather than read as absent.
        """
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

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

    async def _get(self, url: str, key: str) -> httpx.Response:
        """One bounded GET against this provider, with its errors normalized.

        The catalog and the probe are the same request twice over — a GET, a
        status check, and every transport failure arriving as `network` — so
        they share it rather than each spelling the funnel out. The status
        mapping is `_status_kind`'s, which is what makes a rejected key read as
        `auth` here exactly as it does mid-generation.
        """
        try:
            resp = await self._client().get(url, headers=self._headers(key),
                                            timeout=PROBE_TIMEOUT)
        except httpx.HTTPError as exc:
            raise OpenRouterError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenRouterError("network", str(exc)) from exc
        if resp.status_code >= 400:
            raise OpenRouterError(_status_kind(resp.status_code), _extract_error(resp.text),
                                  retry_after_seconds(resp.headers))
        return resp

    async def list_models(self, key: str) -> list[dict]:
        """OpenRouter's catalog, server-side (#149).

        `key` is passed but not required: the endpoint is public, so an
        unsaved connection lists it fine (see `_headers`). It is sent when
        there is one because a key can carry account-specific availability,
        and because a 401 here is a cheaper way to learn a key is wrong than
        the first turn of a scene.
        """
        resp = await self._get(MODELS_URL, key)
        try:
            data = resp.json().get("data", [])
        except ValueError as exc:
            raise OpenRouterError("bad_response", f"model list was not JSON: {exc}") from exc
        return catalog.entries(data)

    async def probe(self, key: str) -> None:
        """Ask OpenRouter whether this key works. Returns on yes, raises on no.

        Returning nothing is the point: everything a caller does with the
        answer, it does with the `LLMError` — kind and detail are the health
        report (#146), and inventing a second success/failure vocabulary here
        would give the route two taxonomies to reconcile.
        """
        await self._get(KEY_URL, key)

    async def aclose(self) -> None:
        # Reset, not just close: `_client()` is lazy, so leaving the closed
        # client in place would hand it back to the next caller (and every
        # request through it raises). `EmbeddingsClient.close` does the same.
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None
