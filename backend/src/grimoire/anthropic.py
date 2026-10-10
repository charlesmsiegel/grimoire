"""The Anthropic Messages API, spoken directly over raw `httpx`.

Not the `anthropic` SDK, and on purpose: the backend is packaged verbatim into
the Android app (Chaquopy), so base dependencies must install there, and the
SDK needs pydantic v2 and a compiled `jiter`. The wire is small enough to
speak by hand -- one POST that streams SSE, one paginated GET -- and the shape
of everything else here is `openrouter.py`'s: an `LLMError` subclass carrying
the HTTP `status`, every received line handed to `llm_capture` before it is
parsed, every error message scrubbed of image bytes, `yield ""` per line as
proof of life, and token counts filled into the caller's `usage` holder.

A gateway module: it imports nothing from `grimoire.store` (#239). What a
connection's preset sends -- `max_tokens`, `stop_sequences`, `thinking`,
`output_config`, any sampling parameter the model takes -- is decided by
`llm_sampling.effective` per attempt and arrives here as `effective`, ready to
merge. This adapter decides nothing about controls.

Two Messages API rules shape the request, and `_messages` is where both live:

- The system prompt is a top-level field, not a role. The LEADING system
  messages become it; a system message after the conversation has started
  (post-history instructions, a reroll steer, an author's note) is folded into
  the conversation as user-side text at the point it was written -- leading
  the next user turn, or, when an assistant turn comes next, as user text
  right there (joining a user turn just before it). Nothing moves past a turn
  it was written before, so a steer ahead of a prefill stays ahead of it.
- A trailing assistant turn is a prefill, and current models refuse one with a
  400. It is sent as given: rewriting it here would silently change what a
  "Keep writing" asked for, and the 400 reaches the reader as `bad_response`
  carrying the API's own message.

Pricing is not in a response, so nothing here records a cost: a call is
`modelled_usd` where the user's per-token table prices its model, and an
unpriced call otherwise -- never `$0`.
"""

from __future__ import annotations

import json
import os
import ssl
from collections.abc import AsyncIterator

import certifi
import httpx

from . import (
    catalog,
    content_parts,
    llm_capture,
    llm_reasoning,
    llm_sampling,
    llm_usage,
    tool_calls,
)
from .llm_errors import LLMError, retry_after_seconds

#: Where a connection with no `base_url` of its own is sent. The `anthropic`
#: provider preset (`store.inference.providers`) names the same host; this
#: module may not import the store, so it is spelled here as well.
DEFAULT_BASE_URL = "https://api.anthropic.com"
#: The API version every request names. Pinned: a newer version is a wire
#: change this adapter would have to be read against.
API_VERSION = "2023-06-01"
#: Same bound and same reason as `openrouter.PROBE_TIMEOUT`: the catalog and
#: the key probe are a reader watching a spinner, not a generation.
PROBE_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
#: The Models API's largest page, and how many pages a listing follows. The
#: page cap is a backstop against a server whose `has_more` never turns false,
#: not an expected size: one page holds every model Anthropic serves today.
MODELS_PAGE_LIMIT = 1000
MODELS_MAX_PAGES = 20

#: An error frame's `error.type` as a failure kind. `overloaded_error` is the
#: SSE twin of a 529, and a retry is what fixes it, so it is a `rate_limit`
#: like the 529 is; `api_error` is the twin of a 500.
_FRAME_KINDS = {
    "overloaded_error": "rate_limit",
    "rate_limit_error": "rate_limit",
    "api_error": "network",
    "authentication_error": "auth",
    "permission_error": "auth",
}


class AnthropicError(LLMError):
    pass


def _status_kind(status: int) -> str:
    """401/403 `auth`; 429 and 529 (overloaded) `rate_limit`; any other 5xx
    `network` -- the provider failing, which a retry or the fallback can fix --
    and any other 4xx `bad_response`: this request was refused."""
    if status in (401, 403):
        return "auth"
    if status in (429, 529):
        return "rate_limit"
    if status >= 500:
        return "network"
    return "bad_response"


def _message_of(err: object) -> str:
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or err)
    return str(err)


def _extract_error(text: str) -> str:
    """The `error.message` of a `{"type": "error", "error": {...}}` body, or
    the body's text when it is not one (a proxy's HTML, a bare string)."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return content_parts.scrub(text.strip())
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    return content_parts.scrub(_message_of(err))


def _error_code(text: str) -> str | None:
    """The `error.details.error_code` of an error body, or None: what tells a
    spend cap's 429 from a rate limit's (`llm_errors.account_limit`)."""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    err = obj.get("error") if isinstance(obj, dict) else None
    details = err.get("details") if isinstance(err, dict) else None
    code = details.get("error_code") if isinstance(details, dict) else None
    return code if isinstance(code, str) and code else None


def _frame_error(obj: dict) -> AnthropicError:
    """The error an in-stream `error` frame stands for. No `status`: the frame
    arrived inside a 200, and `LLMError.status` is an HTTP response's."""
    err = obj.get("error")
    etype = err.get("type") if isinstance(err, dict) else None
    return AnthropicError(_FRAME_KINDS.get(str(etype), "bad_response"),
                          content_parts.scrub(_message_of(err)))


def _root(base_url: str) -> str:
    """The API root a connection's `base_url` names, without a trailing `/v1`
    (the preset's URL has none; a pasted one often does)."""
    root = (base_url or "").strip().rstrip("/") or DEFAULT_BASE_URL
    return root.removesuffix("/v1")


# ---- the request ----
#: The user turn put ahead of a conversation that would otherwise open on the
#: assistant (a scene started from a greeting): the API refuses that with a
#: 400. The same text `openai_compatible._strict_messages` inserts for the same
#: reason -- spelled again because the two adapters share no code.
OPENING_TURN = "(continue)"


def _image(part: dict) -> dict:
    """An OpenAI-style `image_url` part as the API's own image block.

    A `data:` URI must be base64 -- the only form this API takes inline -- and
    keeps only its media type (`data:image/png;charset=x;base64,...` is
    `image/png`). One that is not base64 is refused rather than sent on as a
    URL the API would try, and fail, to fetch. Any other URL is a URL source."""
    ref = part.get("image_url")
    url = ref.get("url", "") if isinstance(ref, dict) else str(ref or "")
    if not url.startswith("data:"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    head, sep, data = url.partition(";base64,")
    if not sep:
        raise AnthropicError("bad_response", "an image data URI that is not base64 cannot be sent")
    media = head[len("data:"):].split(";", 1)[0]
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _blocks(content: object) -> list[dict]:
    """A message's content as content blocks, empty text dropped (the API
    refuses a text block with nothing but whitespace in it)."""
    parts = content if isinstance(content, list) else [content]
    out: list[dict] = []
    for raw in parts:
        part = {"type": "text", "text": raw} if isinstance(raw, str) else raw
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                out.append({"type": "text", "text": text})
        elif part.get("type") == "image_url":
            out.append(_image(part))
        else:
            # Already a Messages API block (or a type this side does not
            # know): passed on, for the API to accept or name in its 400.
            out.append(part)
    return out


def _system_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text"
                       and isinstance(p.get("text"), str))
    return ""


def _messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """`(system, turns)` as the Messages API takes them -- see the module
    docstring for the two rules. Consecutive same-role turns merge, and a turn
    left with no content is dropped."""
    lead = 0
    while lead < len(messages) and messages[lead].get("role") == "system":
        lead += 1
    system = [text for text in (_system_text(m.get("content", "")) for m in messages[:lead])
              if text.strip()]
    turns: list[dict] = []
    pending: list[dict] = []

    def append(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for m in messages[lead:]:
        role, content = m.get("role"), m.get("content", "")
        if role == "system":
            pending.extend(_blocks(content))
            continue
        if role not in ("user", "assistant"):
            # Folding an unknown role into either side would misattribute its
            # content -- `openai_compatible._strict_messages` refuses it too.
            raise AnthropicError("bad_response", f"unsupported message role: {role!r}")
        if role == "user":
            append("user", [*pending, *_blocks(content)])
        else:
            append("user", pending)
            append("assistant", _blocks(content))
        pending = []
    append("user", pending)
    return _opened("\n\n".join(system), turns)


def _opened(joined: str, turns: list[dict]) -> tuple[str, list[dict]]:
    """`(system, turns)` made sendable: the API needs at least one message,
    and refuses a first one from the assistant. The last turn is left alone."""
    if not turns:
        if not joined:
            raise AnthropicError("bad_response", "nothing to send: the prompt is empty")
        # Nothing but a system prompt: the API needs at least one message, and
        # every other adapter sends this text as the prompt it is.
        return "", [{"role": "user", "content": [{"type": "text", "text": joined}]}]
    if turns[0]["role"] == "assistant":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": OPENING_TURN}]})
    return joined, turns


class AnthropicClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _verify(self) -> ssl.SSLContext:
        # A stale $SSL_CERT_FILE crashes TLS setup; see `openrouter._verify`.
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
        return {"x-api-key": key, "anthropic-version": API_VERSION}

    def _payload(self, messages: list[dict], model: str, effective: dict | None,
                 schema: dict | None = None, tools: tuple[dict, ...] | None = None,
                 tool_choice: str | None = None) -> dict:
        """`effective` first, so the request's own fields always win over a
        preset (see `openrouter._payload`); `max_tokens` is required by the
        API, and `effective` always carries it when it came from
        `llm_sampling.effective` -- the default is for a direct caller.

        `schema` asks for structured output (spec 7.2): `output_config.format`
        with `json_schema`, not a forced tool, which current models answer with
        a 400. MERGED into `output_config`, because adaptive thinking's effort
        lives there too and must survive beside it. Only ever given for an
        attempt its resolver flagged capable.

        `tools` and `tool_choice` (01g) only when given, the choice lowered
        as handed (`tool_calls.anthropic_choice`): forced tool use beside
        thinking is a 400 the CALLER avoids, never rewritten here."""
        system, turns = _messages(messages)
        body = {**(effective or {}), "model": model, "messages": turns, "stream": True}
        body.setdefault("max_tokens", llm_sampling.ANTHROPIC_MAX_TOKENS)
        if schema is not None:
            body["output_config"] = {**body.get("output_config", {}),
                                     "format": {"type": "json_schema", "schema": schema}}
        if tools is not None:
            body["tools"] = tool_calls.anthropic_tools(tools)
            if tool_choice is not None:
                body["tool_choice"] = tool_calls.anthropic_choice(tool_choice)
        if system:
            body["system"] = system
        return body

    async def stream(self, messages, model: str, key: str,
                     usage: dict | None = None, effective: dict | None = None,
                     base_url: str = "", schema: dict | None = None,
                     tools: tuple[dict, ...] | None = None,
                     tool_choice: str | None = None) -> AsyncIterator[str]:
        """`usage`, when given, is filled in place (see `llm_usage`):
        `prompt_tokens` is input + cache read + cache write, because the API's
        `input_tokens` counts only what was neither, and the two cache counts
        ride beside it as slices of it (#148)."""
        if not key:
            raise AnthropicError("missing_key", "Anthropic API key is not set")
        reader = _Reader(usage)
        try:
            body = self._payload(messages, model, effective, schema, tools, tool_choice)
            http = self._client()
            async with http.stream(
                "POST", _root(base_url) + "/v1/messages",
                headers={**self._headers(key), "content-type": "application/json",
                         "accept": "text/event-stream"},
                json=body,
                # The facade owns the read bound (#243); see openrouter.stream.
                timeout=httpx.Timeout(None, connect=30.0, write=30.0, pool=30.0),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    llm_capture.emit(usage, "http_error_body", content_parts.scrub(resp.text))
                    raise AnthropicError(_status_kind(resp.status_code),
                                         _extract_error(resp.text),
                                         retry_after_seconds(resp.headers),
                                         status=resp.status_code,
                                         code=_error_code(resp.text))
                async for line in resp.aiter_lines():
                    llm_capture.emit(usage, "sse_line", content_parts.scrub_line(line))
                    # Every line is proof of life -- an `event:` line, a ping,
                    # a thinking delta nobody displays. Adaptive thinking can
                    # run a long while before the first text (#243).
                    yield ""
                    text = reader.line(line)
                    if llm_reasoning.pending(usage):
                        yield ""
                    if text:
                        yield text
                    if reader.stopped:
                        break
            reader.finish()
        except AnthropicError:
            raise
        except httpx.HTTPError as exc:
            raise AnthropicError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise AnthropicError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str, usage: dict | None = None,
                       effective: dict | None = None, base_url: str = "") -> str:
        return "".join([chunk async for chunk in self.stream(
            messages, model, key, usage, effective=effective, base_url=base_url)])

    async def _get(self, url: str, key: str, params: dict[str, str]) -> httpx.Response:
        """One bounded GET, errors normalized -- `openrouter._get`'s funnel."""
        if not key:
            raise AnthropicError("missing_key", "Anthropic API key is not set")
        try:
            resp = await self._client().get(url, headers=self._headers(key),
                                            params=params, timeout=PROBE_TIMEOUT)
        except httpx.HTTPError as exc:
            raise AnthropicError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise AnthropicError("network", str(exc)) from exc
        if resp.status_code >= 400:
            raise AnthropicError(_status_kind(resp.status_code), _extract_error(resp.text),
                                 retry_after_seconds(resp.headers), status=resp.status_code,
                                 code=_error_code(resp.text))
        return resp

    async def list_models(self, key: str, base_url: str = "") -> list[dict]:
        """The Models API's catalog, every page of it (to `MODELS_MAX_PAGES`).

        Its rows carry `display_name`, `max_input_tokens`, `max_tokens` and a
        `capabilities` tree, which `catalog.entry` reads into the features
        `llm_sampling.effective` decides thinking and `max_tokens` from."""
        url = _root(base_url) + "/v1/models"
        rows: list = []
        after: str | None = None
        for _ in range(MODELS_MAX_PAGES):
            params = {"limit": str(MODELS_PAGE_LIMIT)}
            if after:
                params["after_id"] = after
            resp = await self._get(url, key, params)
            try:
                body = resp.json()
            except ValueError as exc:
                raise AnthropicError("bad_response", f"model list was not JSON: {exc}") from exc
            data = catalog.rows(body)
            if data is None:
                raise AnthropicError("bad_response", "model list was not a catalog")
            rows.extend(data)
            last = body.get("last_id")
            if body.get("has_more") is not True or not isinstance(last, str) or not last:
                break
            after = last
        return catalog.entries(rows)

    async def probe(self, key: str, base_url: str = "") -> None:
        """Ask whether this key works: one model from the Models API. Free,
        and authenticated -- a revoked key is a 401 here, as it would be on
        the next turn (#146). Returns on yes, raises on no."""
        await self._get(_root(base_url) + "/v1/models", key, {"limit": "1"})

    async def aclose(self) -> None:
        # Reset, not just close -- see `openrouter.aclose`.
        if self._owns and self._http is not None:
            await self._http.aclose()
            self._http = None


# ---- reading the stream ----
class _Reader:
    """One response's SSE frames, read: what they say, and what they counted.

    `line` takes every received line and answers the text it carries ("" for
    none); thinking goes to `llm_reasoning`'s side channel, never the text, and
    an `error` frame raises. Every frame names its event in its own `type`, so
    the `event:` lines are only proof of life."""

    def __init__(self, usage: dict | None):
        self.usage = usage
        #: The prompt-side components seen so far -- `message_start` carries
        #: them and newer API versions repeat them, cumulatively, on
        #: `message_delta`, so the sum is recomputed from what is known.
        self.counts: dict[str, int] = {}
        self.stopped = False
        self.stop_reason: str | None = None
        self.category: str | None = None

    def line(self, line: str) -> str:
        if not line.startswith("data:"):
            return ""
        try:
            obj = json.loads(line[len("data:"):].strip())
        except json.JSONDecodeError:
            return ""
        if not isinstance(obj, dict):
            return ""
        kind = obj.get("type")
        if kind == "error":
            raise _frame_error(obj)
        if kind == "message_start":
            self._start(obj.get("message"))
        elif kind == "content_block_start":
            # The whole frame, so its block `index` is in hand (01g).
            tool_calls.feed_anthropic(self.usage, obj)
            return self._block(obj.get("content_block"))
        elif kind == "content_block_delta":
            tool_calls.feed_anthropic(self.usage, obj)
            return self._block(obj.get("delta"))
        elif kind == "message_delta":
            self._delta(obj)
        elif kind == "message_stop":
            self.stopped = True
        return ""

    def finish(self) -> None:
        """The body has ended: raise unless it ended a complete reply.

        A refusal (on `message_delta`) is checked first -- it is the model's
        own answer, and more exact than any cut that followed it. Then a body
        that ended before `message_stop` was cut off upstream, however much
        of it arrived (or an empty 200): `network`, as any other upstream
        failure, so the facade counts a failed attempt rather than accepting
        a truncated reply."""
        self.check_refusal()
        if not self.stopped:
            raise AnthropicError("network", "the response ended before message_stop")

    def check_refusal(self) -> None:
        """A refusal is a failure, however much text arrived before it: a
        refused reply is not a complete one, and "the model wrote nothing"
        would hide that it chose not to. Once text has reached the caller the
        facade re-raises rather than retrying (`llm._resilient`)."""
        if self.stop_reason == "refusal":
            said = (f"the model declined ({self.category})" if self.category
                    else "the model declined")
            raise AnthropicError("bad_response", content_parts.scrub(said))

    def _start(self, message: object) -> None:
        if not isinstance(message, dict):
            return
        model = message.get("model")
        if self.usage is not None and isinstance(model, str) and model:
            self.usage["model"] = model
        self._usage(message.get("usage"))

    def _block(self, block: object) -> str:
        """The text a content block start or delta carries. A signature, a
        tool's partial JSON, a redacted thinking block: nothing."""
        if not isinstance(block, dict):
            return ""
        kind = block.get("type")
        if kind in ("thinking", "thinking_delta"):
            llm_reasoning.feed(self.usage, block.get("thinking"))
            return ""
        text = block.get("text") if kind in ("text", "text_delta") else None
        return text if isinstance(text, str) else ""

    def _delta(self, frame: dict) -> None:
        delta = frame.get("delta")
        if isinstance(delta, dict):
            self.stop_reason = delta.get("stop_reason") or self.stop_reason
            tool_calls.anthropic_stop(self.usage, delta.get("stop_reason"))
            self.category = _category(delta, frame) or self.category
        self._usage(frame.get("usage"))

    def _usage(self, block: object) -> None:
        """Fold one usage block into the holder. Never raises, and an absent
        count is never written as zero (`llm_usage.tokens`)."""
        usage = self.usage
        if usage is None or not isinstance(block, dict):
            return
        found = (("input", llm_usage.tokens(block.get("input_tokens"))),
                 ("read", llm_usage.cache_read(block)),
                 ("write", llm_usage.cache_written(block)))
        self.counts.update({name: n for name, n in found if n is not None})
        if self.counts:
            usage["prompt_tokens"] = sum(self.counts.values())
        if "read" in self.counts:
            usage["cache_read_tokens"] = self.counts["read"]
        if "write" in self.counts:
            usage["cache_write_tokens"] = self.counts["write"]
        output = llm_usage.tokens(block.get("output_tokens"))
        if output is not None:
            usage["completion_tokens"] = output


def _category(delta: dict, frame: dict) -> str | None:
    """A refusal's `stop_details.category`, wherever the frame put it."""
    for holder in (delta, frame):
        details = holder.get("stop_details")
        if isinstance(details, dict) and isinstance(details.get("category"), str):
            return details["category"]
    return None
