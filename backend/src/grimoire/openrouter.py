"""Thin OpenRouter (OpenAI-compatible) client with normalized errors."""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import AsyncIterator, Mapping

import certifi
import httpx

from . import catalog, content_parts, decisions, llm_capture, llm_reasoning, llm_usage
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
#: The decisions endpoint (Jev, slice H). Not under `BASE_URL`: the reference
#: says the operation overrides the `/api/v1` server, and it is an alpha, so it
#: is spelled whole rather than derived from a root it does not share.
#: https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class OpenRouterError(LLMError):
    #: What the upstream endpoint said, when OpenRouter relayed its error under
    #: a wrapper message of its own ("Provider returned error"), else "". See
    #: `_upstream`. Kept beside `detail` rather than folded into it, so every
    #: reader of `detail` -- the preset-refusal match among them -- reads the
    #: error exactly as before; `llm._schema_refusal` is the one reader that
    #: asks for both.
    upstream: str = ""


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
        return content_parts.scrub(text.strip())
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    if isinstance(err, dict):
        return content_parts.scrub(str(err.get("message") or err.get("detail") or err))
    return content_parts.scrub(str(err))


#: Bound on the upstream text `_upstream` keeps: enough for any refusal's
#: sentence, and not an upstream's whole HTML error page.
UPSTREAM_CHARS = 1000


def _upstream(text: str) -> str:
    """What the upstream endpoint said, from an OpenRouter error body that
    relays one: `error.metadata.raw`, the provider-error metadata OpenRouter
    documents beside `metadata.provider_name`. `raw` is the upstream's own
    body, as text or as an object, and is read the way `_extract_error` reads a
    body -- its message when it is an error body, else the text itself. ""
    when the body carries none."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ""
    err = obj.get("error") if isinstance(obj, dict) else None
    meta = err.get("metadata") if isinstance(err, dict) else None
    raw = meta.get("raw") if isinstance(meta, dict) else None
    if raw is None or raw == "":
        return ""
    said = _extract_error(raw if isinstance(raw, str) else json.dumps(raw))
    return said[:UPSTREAM_CHARS]


def _http_error(resp: httpx.Response) -> OpenRouterError:
    """The error an HTTP error response is raised as."""
    # The provider's own window, when it names one. A guessed backoff is what
    # you use for not knowing; Retry-After is knowing (#144).
    error = OpenRouterError(_status_kind(resp.status_code), _extract_error(resp.text),
                            retry_after_seconds(resp.headers), status=resp.status_code)
    error.upstream = _upstream(resp.text)
    return error


# --- native decisions (slice H, spec 7.4) ------------------------------------
#
# One item per request. Question and option ids are sent verbatim: the
# reference documents no character set or length for a question id or a
# criteria key, so a ref such as `characters:mara` goes as itself.

#: The wire `type` each question kind is asked, and answered, as.
_TYPES = {decisions.Predicate: "noul", decisions.Choice: "choice", decisions.Score: "score"}


def _criteria(q: decisions.Choice) -> dict[str, str]:
    """A choice's criteria: `{wire key: description}`, the reserved none (when
    the choice allows one) described by `NATIVE_NONE_TEXT`."""
    described = {opt.id: opt.description for opt in q.options}
    return {wire: (decisions.NATIVE_NONE_TEXT if key == decisions.NONE_KEY else described[key])
            for wire, key in decisions.native_choice_keys(q)}


def _question(q: decisions.NativeQuestion) -> dict:
    asked: dict[str, object] = {"type": _TYPES[type(q)], "instructions": q.instructions}
    # A predicate sends no criteria: they are optional, and a Grimoire
    # predicate has no description of either side to put in them.
    if isinstance(q, decisions.Choice):
        asked["criteria"] = _criteria(q)
    elif isinstance(q, decisions.Score):
        asked["criteria"] = list(q.levels)
    return asked


def decision_body(item: decisions.Item, model: str) -> dict:
    """The request body asking `item` of `model`. Aliases are the structured
    parser's and are not sent; nothing asks for a rationale, which this
    endpoint does not return."""
    return {"model": model, "state": item.context,
            "questions": {q.id: _question(q) for q in decisions.native_questions(item)}}


def _answer(q: decisions.NativeQuestion, raw: object) -> decisions.Answer:
    """One answer object read through `decisions.native_answer`. An answer of
    another `type` than the one asked is `unreadable`; `confidence` and a
    score's `legend` are not carried, and a score's weighted `score` is never
    its answer (the per-level argmax is)."""
    if not isinstance(raw, Mapping) or raw.get("type") != _TYPES[type(q)]:
        return decisions.Answer(None, "unreadable")
    if isinstance(q, decisions.Predicate):
        # `noul` is P(true). A distribution is never sent for a predicate.
        return decisions.native_answer(q, probability=raw.get("noul"))
    if isinstance(q, decisions.Choice):
        keys = dict(decisions.native_choice_keys(q))
        probabilities = raw.get("probabilities")
        if isinstance(probabilities, Mapping):
            probabilities = {decisions.native_key(keys, k): v for k, v in probabilities.items()}
        if raw.get("choice", decisions.UNSTATED) is None:
            # `choice` is a required string (the reference), so a null is no
            # answer: only the reserved none key abstains. What it reported
            # still rides on the unreadable answer.
            reported = decisions.native_answer(q, distribution=probabilities)
            return decisions.Answer(None, "unreadable", distribution=reported.distribution)
        chosen = decisions.native_key(keys, raw["choice"]) if "choice" in raw else decisions.UNSTATED
        return decisions.native_answer(q, chosen=chosen, distribution=probabilities)
    # A score's levels are keyed by index on the wire, as Grimoire keys them.
    return decisions.native_answer(q, distribution=raw.get("probabilities"))


def decision_result(body: object, item: decisions.Item) -> decisions.ItemResult:
    """`body`, a decisions response, read as `item`'s answers.

    Raises `bad_response` when `body` is not the documented envelope, and when
    it is but answers none of `item`'s questions: that is not an answer, and the
    item falls through. A question missing beside answered ones is
    `unreadable`, with one warning per call."""
    answers = body.get("answers") if isinstance(body, Mapping) else None
    if not isinstance(answers, Mapping):
        raise OpenRouterError("bad_response", "the decisions reply held no answers")
    result = decisions.native_result(
        item, {q.id: _answer(q, answers[q.id]) for q in decisions.native_questions(item)
               if q.id in answers},
        "OpenRouter")
    if result is None:
        raise OpenRouterError("bad_response", "the decisions reply answered none of the questions")
    return result


def _decision_usage(body: object, usage: dict | None) -> None:
    """The response's `usage` block, mapped onto the holder's fields -- never
    through the chat parser, whose shape this is not. An absent or unusable
    field files nothing. The served `model` (a dated build) overwrites the one
    asked for, as a chat reply's does."""
    if usage is None or not isinstance(body, Mapping):
        return
    model = body.get("model")
    if isinstance(model, str) and model:
        usage["model"] = model
    block = body.get("usage")
    if not isinstance(block, Mapping):
        return
    for wire, field in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        count = llm_usage.tokens(block.get(wire))
        if count is not None:
            usage[field] = count
    # Credits, which are USD one-for-one, as on the chat endpoint.
    cost = llm_usage.money(block.get("cost"))
    if cost is not None:
        usage["cost_usd"] = cost
        usage["cost_basis"] = llm_usage.BILLED


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

    def _payload(self, messages, model, stream, sampling=None, schema=None):
        # `usage.include` is what makes OpenRouter attach token counts and the
        # call's cost in credits to the final SSE chunk (#152). Free, and
        # accepted by every model on the platform -- unlike the equivalent
        # option on an arbitrary endpoint, which `openai_compatible` therefore
        # does not send.
        #
        # `sampling` is what `llm_sampling.split` decided this request carries.
        # Merged FIRST so it can never overwrite a field this adapter owns: its
        # keys are sampler wire names and cannot collide today, and if one ever
        # did, the request's own shape must win over a preset.
        #
        # `schema` asks for structured output (spec 7.2) and is only ever given
        # for an attempt its resolver flagged capable; absent, the body is the
        # one sent before it existed. Strict, so the reply is held to the
        # schema rather than guided by it -- which is why `decisions` keeps the
        # schema to strict mode's subset. The name is required and says nothing.
        payload = {**(sampling or {}), "model": model, "messages": messages, "stream": stream,
                   "usage": {"include": True}}
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "strict": True, "schema": schema}}
        return payload

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
                     usage: dict | None = None,
                     sampling: dict | None = None,
                     schema: dict | None = None) -> AsyncIterator[str]:
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
                json=self._payload(messages, model, True, sampling, schema),
                # The facade owns the read bound (#243) — it is the configurable,
                # provider-independent one, and a read timeout here would cap it
                # at 120s no matter what the user set, including "0 = no bound".
                # Every other bound stays.
                timeout=httpx.Timeout(None, connect=30.0, write=30.0, pool=30.0),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    llm_capture.emit(usage, "http_error_body", content_parts.scrub(resp.text))
                    raise _http_error(resp)
                async for line in resp.aiter_lines():
                    # Scrubbed for the capture only; the line itself is parsed
                    # as sent. A 200 stream can quote the request back too.
                    llm_capture.emit(usage, "sse_line", content_parts.scrub_line(line))
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
                    llm_reasoning.from_chunk(obj, usage)
                    if llm_reasoning.pending(usage):
                        yield ""
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

    async def decide(self, item: decisions.Item, model: str, key: str, *,
                     usage: dict | None = None,
                     bound: float | None = None) -> decisions.ItemResult:
        """Ask `item` of the decisions endpoint: one POST, never retried here
        (the facade decides that). Errors are the chat endpoint's: a status
        through `_http_error`, `Retry-After` included, and a transport failure
        as `network`. `bound` is the read timeout in seconds (None or <= 0:
        none; not spelled `timeout`, which ASYNC109 reserves), and a
        reply that does not arrive within it is a `timeout`, which the facade
        does not retry.

        The body is captured before any field is read, and the usage block is
        filed before the answers are, so a billed envelope that answers nothing
        still reports what it cost."""
        if not key:
            raise OpenRouterError("missing_key", "OpenRouter API key is not set")
        read = bound if bound is not None and bound > 0 else None
        try:
            resp = await self._client().post(
                DECISIONS_URL, headers=self._headers(key), json=decision_body(item, model),
                timeout=httpx.Timeout(read, connect=30.0, write=30.0, pool=30.0))
        except httpx.ReadTimeout as exc:
            raise OpenRouterError(
                "timeout", f"the model sent nothing for {bound:g}s — giving up") from exc
        except httpx.HTTPError as exc:
            raise OpenRouterError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenRouterError("network", str(exc)) from exc
        if resp.status_code >= 400:
            llm_capture.emit(usage, "http_error_body", content_parts.scrub(resp.text))
            raise _http_error(resp)
        llm_capture.emit(usage, "decision_body", content_parts.scrub(resp.text))
        try:
            body = resp.json()
        except ValueError as exc:
            raise OpenRouterError("bad_response", f"the decisions reply was not JSON: {exc}") from exc
        _decision_usage(body, usage)
        return decision_result(body, item)

    async def _get(self, url: str, key: str,
                   params: dict[str, str] | None = None) -> httpx.Response:
        """One bounded GET against this provider, with its errors normalized.

        The catalog and the probe are the same request twice over — a GET, a
        status check, and every transport failure arriving as `network` — so
        they share it rather than each spelling the funnel out. The status
        mapping is `_status_kind`'s, which is what makes a rejected key read as
        `auth` here exactly as it does mid-generation.

        `params` is the query string, built by httpx rather than by hand.
        """
        try:
            resp = await self._client().get(url, headers=self._headers(key),
                                            params=params, timeout=PROBE_TIMEOUT)
        except httpx.HTTPError as exc:
            raise OpenRouterError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenRouterError("network", str(exc)) from exc
        if resp.status_code >= 400:
            raise _http_error(resp)
        return resp

    async def list_models(self, key: str) -> list[dict]:
        """OpenRouter's catalog, server-side (#149).

        `key` is passed but not required: the endpoint is public, so an
        unsaved connection lists it fine (see `_headers`). It is sent when
        there is one because a key can carry account-specific availability,
        and because a 401 here is a cheaper way to learn a key is wrong than
        the first turn of a scene.
        """
        # `all`, because the default listing is text-output models only and the
        # catalog now records what each model outputs: embedding-only and
        # rerank-only models are there for the Embedding role to find. Pickers
        # of chat models filter them back out (`catalog.listable`).
        resp = await self._get(MODELS_URL, key, params={"output_modalities": "all"})
        try:
            body = resp.json()
        except ValueError as exc:
            raise OpenRouterError("bad_response", f"model list was not JSON: {exc}") from exc
        # A 200 whose body is JSON but not a catalog — `{"data": null}`, a bare
        # array, a proxy's status document — is the provider misbehaving, which
        # is what `bad_response` is for. Reaching the normalizer with it raises
        # a TypeError from inside a route instead, i.e. a 500 for somebody
        # else's malformed reply.
        data = catalog.rows(body)
        if data is None:
            raise OpenRouterError("bad_response", "model list was not a catalog")
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
