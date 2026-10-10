"""Chat-completions client for an arbitrary OpenAI-compatible endpoint —
same wire protocol/error-shape as openrouter.py, but base_url is
caller-supplied and the API key is optional. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md.
"""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import AsyncIterator, Mapping

import certifi
import httpx

from . import catalog, content_parts, decisions, llm_capture, llm_reasoning, llm_usage
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
        return content_parts.scrub(text.strip())
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    if isinstance(err, dict):
        return content_parts.scrub(str(err.get("message") or err.get("detail") or err))
    return content_parts.scrub(str(err))


def _as_parts(content: str | list) -> list:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def _join(a: str | list, b: str | list) -> str | list:
    """`a` and `b` as one content, separated by a blank line.

    Two strings join exactly as they always have. A list on either side -- a
    turn carrying image parts (#377) -- makes the result a parts list, with the
    separator merged into the adjacent text so the text reads the same as the
    string join would."""
    if isinstance(a, str) and isinstance(b, str):
        return a + "\n\n" + b
    out: list = []
    for part in [*_as_parts(a), {"type": "text", "text": "\n\n"}, *_as_parts(b)]:
        if (part.get("type") == "text" and out and out[-1].get("type") == "text"):
            out[-1] = {"type": "text", "text": out[-1]["text"] + part["text"]}
        else:
            out.append(part)
    return out


def _strict_messages(messages: list[dict]) -> list[dict]:
    """Fold system messages into adjacent user turns and guarantee the result
    starts with role=user and alternates strictly — required by chat-completion
    backends (e.g. z.ai's GLM coding endpoint) that reject a system message
    mid-conversation or a non-user opening turn.

    A user turn may hold content parts rather than a string (an image beside
    its text, #377); `_join` folds those the way it folds strings."""
    folded: list[dict] = []
    pending: list[str] = []

    def flush(extra: str | list = "") -> str | list:
        nonlocal pending
        joined: str | list = "\n\n".join(pending)
        if extra or not isinstance(extra, str):
            joined = _join(joined, extra) if pending else extra
        pending = []
        return joined

    def append(role: str, content: str | list) -> None:
        if folded and folded[-1]["role"] == role:
            folded[-1]["content"] = _join(folded[-1]["content"], content)
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


# --- native decisions (slice H, spec 7.4) ------------------------------------
#
# OpenAI's decisions endpoint, `POST {base_url}/decisions`, reached only from
# the OpenAI preset: every other preset of this kind lists `decide_native` in
# `never`. One item per request. The API reference,
# https://developers.openai.com/api/reference/resources/decisions/methods/create
# (checked 2026-10-08), documents no character set for a question `name` or a
# choice `value` -- only a length far past any Grimoire id -- so ids go
# verbatim, as `openrouter`'s do. The wire shapes are written here rather than
# shared with it -- the two differ in every container, and this module shares
# no code with that one (the module docstring) -- while the rules both obey
# (the reserved none, reading a key back, the envelope that answered nothing)
# are `decisions`'.

#: The wire `type` each question kind is asked, and answered, as.
_TYPES = {decisions.Predicate: "predicate", decisions.Choice: "choice",
          decisions.Score: "score"}

#: The answer `type` a question the model declined comes back as, per question:
#: the others in the same request can still be answered.
_REFUSAL = "refusal"


def _question(q: decisions.NativeQuestion) -> dict:
    asked: dict[str, object] = {"type": _TYPES[type(q)], "name": q.id,
                                "instructions": q.instructions}
    if isinstance(q, decisions.Choice):
        described = {opt.id: opt.description for opt in q.options}
        asked["choices"] = [
            {"value": wire,
             "description": (decisions.NATIVE_NONE_TEXT if key == decisions.NONE_KEY
                             else described[key])}
            for wire, key in decisions.native_choice_keys(q)]
    elif isinstance(q, decisions.Score):
        # Labelled by index, so a label read back is the key Grimoire uses.
        asked["levels"] = [{"label": str(i), "description": text}
                           for i, text in enumerate(q.levels)]
    return asked


def decision_body(item: decisions.Item, model: str) -> dict:
    """The request body asking `item` of `model`: `questions` an array, each
    named by its question id. Aliases are the structured parser's and are not
    sent; nothing asks for a rationale, which this endpoint does not return."""
    return {"model": model, "input": item.context,
            "questions": [_question(q) for q in decisions.native_questions(item)]}


def _choice_distribution(keys: dict[str, str], reported: object) -> dict | None:
    """A choice's `probabilities` array as a mapping keyed by Grimoire's keys,
    or None for a report that is not one. A value listed twice makes the whole
    report invalid -- which of its two probabilities is meant cannot be told --
    so it is dropped, never repaired, and so is an entry whose value is
    neither a string nor a boolean. A boolean value (the reference types
    values as either) reads as itself, which no option id equals, so
    `native_answer` drops the report whole."""
    if not isinstance(reported, list):
        return None
    out: dict[object, object] = {}
    seen: set[tuple[type, object]] = set()
    for entry in reported:
        if not isinstance(entry, Mapping):
            return None
        wire = entry.get("value")
        if not isinstance(wire, (str, bool)) or (type(wire), wire) in seen:
            return None
        seen.add((type(wire), wire))
        out[decisions.native_key(keys, wire)] = entry.get("probability")
    return out


def _score_distribution(q: decisions.Score, reported: object) -> dict | None:
    """A score's `probabilities` array as a mapping keyed `str(i)`: by its
    `value` when that is an int level index in range, else by its `label`
    (which `decision_body` sent as `str(i)`). None for a report that is not
    one, including a level listed twice."""
    if not isinstance(reported, list):
        return None
    out: dict[str, object] = {}
    for entry in reported:
        if not isinstance(entry, Mapping):
            return None
        value, label = entry.get("value"), entry.get("label")
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < len(q.levels):
            key = str(value)
        elif isinstance(label, str):
            key = label
        else:
            return None
        if key in out:
            return None
        out[key] = entry.get("probability")
    return out


def _answer(q: decisions.NativeQuestion, raw: object) -> decisions.Answer:
    """One answer object read through `decisions.native_answer`. A refusal is
    `refused`; an answer of any other `type` than the one asked is
    `unreadable`. `confidence` is not carried, and a score's weighted `score`
    is never its answer (the per-level argmax is)."""
    if not isinstance(raw, Mapping):
        return decisions.Answer(None, "unreadable")
    if raw.get("type") == _REFUSAL:
        return decisions.native_answer(q, refused=True)
    if raw.get("type") != _TYPES[type(q)]:
        return decisions.Answer(None, "unreadable")
    if isinstance(q, decisions.Predicate):
        # P(true). A distribution is never sent for a predicate.
        return decisions.native_answer(q, probability=raw.get("probability"))
    if isinstance(q, decisions.Choice):
        keys = dict(decisions.native_choice_keys(q))
        probabilities = _choice_distribution(keys, raw.get("probabilities"))
        if raw.get("choice", decisions.UNSTATED) is None:
            # `choice` is a string or a boolean (the reference), so a null is
            # no answer: only the reserved none key abstains. What it reported
            # still rides on the unreadable answer.
            reported = decisions.native_answer(q, distribution=probabilities)
            return decisions.Answer(None, "unreadable", distribution=reported.distribution)
        chosen = decisions.native_key(keys, raw["choice"]) if "choice" in raw else decisions.UNSTATED
        return decisions.native_answer(q, chosen=chosen, distribution=probabilities)
    return decisions.native_answer(q, distribution=_score_distribution(q, raw.get("probabilities")))


def decision_result(body: object, item: decisions.Item) -> decisions.ItemResult:
    """`body`, a decisions response, read as `item`'s answers, each matched to
    its question by `name`.

    Raises `bad_response` when `body` is not the documented envelope, and when
    it is but answers none of `item`'s questions: that is not an answer, and the
    item falls through. A question missing beside answered ones is
    `unreadable`, with one warning per call; so is one whose name is answered
    twice, since which answer is meant cannot be told. An answer with no name,
    or a name nobody asked, matches nothing."""
    answers = body.get("answers") if isinstance(body, Mapping) else None
    if not isinstance(answers, list):
        raise OpenAICompatibleError("bad_response", "the decisions reply held no answers")
    by_name: dict[str, object] = {}
    twice: set[str] = set()
    for raw in answers:
        name = raw.get("name") if isinstance(raw, Mapping) else None
        if not isinstance(name, str):
            continue
        if name in by_name:
            twice.add(name)
        by_name[name] = raw
    result = decisions.native_result(
        item, {q.id: (decisions.Answer(None, "unreadable") if q.id in twice
                      else _answer(q, by_name[q.id]))
               for q in decisions.native_questions(item) if q.id in by_name},
        "OpenAI")
    if result is None:
        raise OpenAICompatibleError("bad_response",
                                    "the decisions reply answered none of the questions")
    return result


def _decision_usage(body: object, usage: dict | None) -> None:
    """The response's `usage` block (the API reference's; the guide documents
    none), mapped onto the holder's fields as reported -- never through the
    chat parser, whose shape this is not. The cache pair sits beside the
    prompt count, inside it, as it does on a chat reply (#148). No cost is
    reported, so the row is unpriced, and the ledger never models a native
    row (`store.usage._modellable`): decisions bill input tokens only. An
    absent or unusable field files nothing; the served `model` overwrites the
    one asked for, as a chat reply's does."""
    if usage is None or not isinstance(body, Mapping):
        return
    model = body.get("model")
    if isinstance(model, str) and model:
        usage["model"] = model
    block = body.get("usage")
    if not isinstance(block, Mapping):
        return
    details = block.get("input_tokens_details")
    details = details if isinstance(details, Mapping) else {}
    for field, value in (("prompt_tokens", block.get("input_tokens")),
                         ("completion_tokens", block.get("output_tokens")),
                         ("cache_read_tokens", details.get("cached_tokens")),
                         ("cache_write_tokens", details.get("cache_write_tokens"))):
        count = llm_usage.tokens(value)
        if count is not None:
            usage[field] = count


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
                      strict: bool = False, usage: dict | None = None,
                      reasoning_effort: str = "",
                      sampling: dict | None = None,
                      schema: dict | None = None) -> AsyncIterator[str]:
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
        # The preset's parameters first, so the request's own fields always
        # win (see `openrouter._payload`); `llm_sampling.split` has already
        # dropped whatever this endpoint is not declared to take.
        payload = {**(sampling or {}), "model": model, "messages": payload_messages,
                   "stream": True}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        # Structured output (spec 7.2), only ever given for an attempt whose
        # resolver knows the endpoint takes it: a strict endpoint refuses a
        # field it does not know, which is why nothing here sends it unasked.
        # Spelled here rather than borrowed: each adapter owns its wire body.
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "strict": True, "schema": schema}}
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
                    llm_capture.emit(usage, "http_error_body", content_parts.scrub(resp.text))
                    # The provider's own window, when it names one. A guessed
                    # backoff is what you use for not knowing; Retry-After is
                    # knowing (#144).
                    raise OpenAICompatibleError(_status_kind(resp.status_code),
                                               _extract_error(resp.text),
                                               retry_after_seconds(resp.headers),
                                               status=resp.status_code)
                async for line in resp.aiter_lines():
                    # Scrubbed for the capture only; the line itself is parsed
                    # as sent. A 200 stream can quote the request back too.
                    llm_capture.emit(usage, "sse_line", content_parts.scrub_line(line))
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
                    llm_reasoning.from_chunk(obj, usage)
                    if llm_reasoning.pending(usage):
                        yield ""
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

    async def decide(self, item: decisions.Item, model: str, key: str, base_url: str, *,
                     usage: dict | None = None,
                     bound: float | None = None) -> decisions.ItemResult:
        """Ask `item` of `{base_url}/decisions`: one POST, never retried here
        (the facade decides that), with no beta header (the reference shows
        none). A status is raised as `stream` raises one, `Retry-After`
        included, and a transport failure as `network`. `bound` is the read
        timeout in seconds (None or <= 0: none; spelled as `list_models`
        spells it), and a reply that does not arrive within it is a `timeout`,
        which the facade does not retry. An empty key or base URL is refused
        unsent: the endpoint is OpenAI's, which answers no request without one.

        The body is captured before any field is read, and the usage block is
        filed before the answers are, so a billed envelope that answers nothing
        still reports what it counted."""
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        if not key:
            raise OpenAICompatibleError("missing_key", "This provider's API key is not set")
        url = base_url.rstrip("/") + "/decisions"
        read = bound if bound is not None and bound > 0 else None
        try:
            resp = await self._client().post(
                url, headers=self._headers(key), json=decision_body(item, model),
                timeout=httpx.Timeout(read, connect=30.0, write=30.0, pool=30.0))
        except httpx.ReadTimeout as exc:
            raise OpenAICompatibleError(
                "timeout", f"the model sent nothing for {bound:g}s — giving up") from exc
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenAICompatibleError("network", str(exc)) from exc
        if resp.status_code >= 400:
            llm_capture.emit(usage, "http_error_body", content_parts.scrub(resp.text))
            raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text),
                                        retry_after_seconds(resp.headers),
                                        status=resp.status_code)
        llm_capture.emit(usage, "decision_body", content_parts.scrub(resp.text))
        try:
            body = resp.json()
        except ValueError as exc:
            raise OpenAICompatibleError("bad_response",
                                        f"the decisions reply was not JSON: {exc}") from exc
        _decision_usage(body, usage)
        return decision_result(body, item)

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
                raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text),
                                            status=resp.status_code)
            body = resp.json()
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        # A 200 whose body is JSON but not a catalog — `{"data": null}` from a
        # server with nothing loaded, a bare array, a gateway's status document.
        # Outside the funnel above deliberately: `except Exception` there would
        # relabel this as `network`, which is the one thing it is not.
        data = catalog.rows(body)
        if data is None:
            raise OpenAICompatibleError("bad_response", "model list was not a catalog")
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
