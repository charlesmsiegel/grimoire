"""Embeddings client for an OpenAI-compatible ``/embeddings`` endpoint.

The vector half of what `openai_compatible.py` does for chat: same wire
conventions, same `LLMError` taxonomy, same "base_url is caller-supplied and
the key is optional" posture. Nothing here reads config or touches the store —
`store/context/semantic.py` owns that, the way `llm.py` owns it for chat.

Two clients. **`EmbeddingsClient` is synchronous**, and every current caller
uses it: the world-info activation seam (`store/context/world_state.activate`)
is reached from a synchronous `build_messages`, and an async client there would
mean threading `await` up through the whole context builder and its six call
sites, for a call that is off by default. The cost is real and stated rather
than hidden: while a recall is in flight it holds a worker thread -- every
caller is a `def` handler, a `run_in_threadpool` or a `to_thread`, and
`store.inference.embed` refuses a call on the app's event-loop thread
(01h-C4a) -- which is why `TIMEOUT` is a tight bound rather than the generous
one the streaming clients take. See semantic.py's docstring for the tradeoff
in full. **`AsyncEmbeddingsClient`** (01h-C4b) is the same request for a caller
already in a coroutine, one per app; it shares everything but the transport.

Two invariants the parser exists to hold, both of which produce a *silently*
wrong prompt rather than an error when they are missed:

- **Vectors come back in input order.** The OpenAI schema carries an explicit
  ``index`` per row because arrival order is not promised. Trusting arrival
  order pairs each entry with somebody else's vector, and every similarity
  after that is meaningless while looking perfectly healthy.
- **Every input gets exactly one vector.** A short, duplicated, or
  out-of-range index set leaves a slot unfilled; `bad_response` is the honest
  answer, and the caller degrades to keyword-only.

One bound the synchronous client does *not* achieve, named here rather than
papered over:
`READ_SLICE` bounds each read and `TIMEOUT` is checked between body chunks, but
response **headers** arrive before `stream()` yields, outside that loop, and
every successful socket read resets httpx's timer. An endpoint that drip-feeds
an incomplete header a byte at a time is therefore bounded per read and not in
total. No arrangement of httpx timeouts fixes it — httpx has no total-request
deadline, and a synchronous call cannot be cancelled from outside without a
watchdog thread that would outlive the call it abandoned. `AsyncEmbeddingsClient`
closes it, because there the whole call is one `asyncio` deadline (roadmap
01h-C4b); a synchronous call still has the hole against a deliberately hostile
endpoint, and the user chooses the endpoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import threading
import time

import certifi
import httpx

from . import llm_usage, wire
from .llm_errors import LLMError

#: Inputs per request. The endpoints cap request size rather than list length,
#: and entry bodies here are prompt-sized, so this is a payload bound and not a
#: rate limit — batching is what keeps a first run over a whole world's lore
#: from becoming one HTTP call per entry.
BATCH = 64

#: Seconds one `embed` call may take **in total** — every batch, every read,
#: start to finish. Deliberately tighter than the chat clients' 120s: this call
#: holds a worker thread for its length (see the module docstring), and a
#: recall that has not returned by now is worth abandoning — the caller's
#: fallback is the keyword activation that shipped before any of this.
#:
#: A wall-clock deadline, not an httpx timeout, because those are not the same
#: thing and the difference is the whole point. httpx bounds each network
#: *operation*: a server that emits one byte every 29 seconds resets the read
#: timer forever and holds the request open indefinitely — measured at 0.9s
#: elapsed against a 0.3s read timeout, and it scales without bound. That would
#: pin a FastAPI threadpool worker for as long as the server cared to dribble,
#: which is exactly the "bounded, fails soft" promise this layer makes.
TIMEOUT = 30.0

#: Longest a single blocking read may take. httpx sets one timeout per
#: *request*, not per read, so the deadline above can only be enforced between
#: chunks — a read already in flight when it passes runs to its own timeout.
#: Handing httpx the whole remaining budget therefore let a server emitting a
#: chunk just under each read timeout overrun the deadline by a further
#: TIMEOUT: bytes at ~29s and ~58s kept a 30s call blocked until 58s. Slicing
#: the read timeout bounds the total at TIMEOUT + READ_SLICE, and a server that
#: cannot produce one chunk in a third of the whole budget was not going to
#: finish inside it anyway.
READ_SLICE = 10.0

#: Ceiling on one response body. 64 inputs at 3072 dimensions — the largest
#: batch of the widest common model — is about 4MB of JSON, so this is ~8x
#: headroom for anything legitimate and still a bound. Without one, "read the
#: whole body" is an unbounded allocation on a caller's say-so.
MAX_BYTES = 32 * 1024 * 1024

#: Components one vector may have. `MAX_BYTES` bounds the body but not how it
#: is *distributed*: 32MB of `0.1,` is ~8M components, and one row that wide
#: costs ~260MB to parse and ~400MB by the time `_vectors` has narrowed it
#: (measured: 8MB of body peaked at 99MB). That matters here specifically
#: because `_vectors` runs OUTSIDE `_post`'s handlers -- deliberately, so a bug
#: in it is not disguised as a provider failure -- and `semantic.recall` catches
#: only LLMError and OSError, so a MemoryError raised there fails the whole
#: context build rather than falling back to keyword activation. Checking
#: `len` first costs nothing and is the only bound that can precede the
#: allocation. 32768 is ~10x the widest model in common use (3072), so it
#: rejects nothing real and still bounds the work at a few hundred KB.
MAX_DIMS = 32768

#: Components one *response* may carry, summed across its rows. `MAX_DIMS`
#: alone is a per-row bound, and a per-row bound does not add up to a bound: a
#: full batch of 64 rows each exactly at MAX_DIMS is 2.1M components in 8.4MB
#: of JSON -- comfortably under MAX_BYTES, and every row passes the per-row
#: check. That is the same escape MAX_DIMS was added to close, reassembled out
#: of rows that are individually fine.
#:
#: Measured on that response, and worth splitting because only half of it is
#: this module's problem: `json.loads` costs 68MB and `_vectors` adds 18MB on
#: top. The parse happens inside `_post`'s handlers, so it degrades to
#: keyword-only like any other failure; `_vectors` runs outside them, so its
#: share is the part that would fail the context build. The bound cuts that
#: share to 4.7MB. It does not (and cannot) touch the 68MB -- MAX_BYTES is
#: what bounds the parse.
#:
#: `BATCH * 8192` is a full batch of the widest embedding model anybody ships
#: -- 8192 is comfortably past text-embedding-3-large's 3072 and NV-Embed's
#: 4096 -- so no real response comes near it.
MAX_TOTAL_DIMS = BATCH * 8192


class EmbeddingsError(LLMError):
    pass


# ---- the request (01h §3.4) ----

#: The `code` of a reply whose vectors are not the width the request asked
#: for (`DimensionsMismatchError`).
DIMENSIONS_MISMATCH = "dimensions_mismatch"


class DimensionsMismatchError(EmbeddingsError):
    """A reply that ignored a requested `dimensions` (01h §3.4).

    `missing_key`, the kind for "configured, but not usably" (the redirect
    precedent below): an endpoint that silently drops an unknown field
    answers every later request the same way, so no caller retries it, and
    storing its native-width vectors under a space that claims the requested
    width would be honest only until it started honouring the field. The
    widths ride the exception for the model test's verdict."""

    def __init__(self, requested: int, returned: int, field_name: str):
        super().__init__(
            "missing_key",
            f"requested {requested} dimensions in `{field_name}`, the endpoint returned "
            f"{returned}: it ignores the field",
            code=DIMENSIONS_MISMATCH)
        self.requested_dims = requested
        self.returned_dims = returned


def check_sendable(options: wire.EmbedOptions | None) -> None:
    """`ValueError` unless `options` is None or an `EmbedOptions` that can be
    sent: a `param` mode with its field and both values, and a `dimensions`
    that is a positive whole number. The facts validator holds a stated
    block to this and more; this holds a hand-built one, before anything is
    sent."""
    if options is None:
        return
    if not isinstance(options, wire.EmbedOptions):
        raise ValueError("embedding options must be a wire.EmbedOptions")
    if options.input not in wire.EMBED_INPUT_MODES:
        raise ValueError("unknown embedding input mode")
    if options.input == "param" and not (options.param_field and options.query_value
                                         and options.document_value):
        raise ValueError("the request-field input type needs a field and both values")
    dims = options.dimensions
    if dims is not None and (isinstance(dims, bool) or not isinstance(dims, int) or dims < 1
                             or not options.dimensions_field):
        raise ValueError("requested dimensions must be a positive whole number with a field")


def check_queries(queries: object, count: int) -> None:
    """`ValueError` unless `queries` -- how many of the `count` texts, from the
    first, are queries -- is a whole number from 0 to `count`."""
    if isinstance(queries, bool) or not isinstance(queries, int) or not 0 <= queries <= count:
        raise ValueError("queries must be a whole number from 0 to the number of texts")


def prepare_inputs(texts: list[str], options: wire.EmbedOptions | None,
                   queries: int) -> list[str]:
    """The inputs as sent: in the `prefix` input type each text gets the query
    prefix (the first `queries` texts) or the document prefix (the rest);
    otherwise the texts unchanged. A mixed batch is still one request,
    because the type rides each text. The callers clip before this, so a
    prefix is never cut off."""
    if options is None or options.input != "prefix":
        return list(texts)
    return [(options.query_prefix if i < queries else options.document_prefix) + text
            for i, text in enumerate(texts)]


def segments(count: int, options: wire.EmbedOptions | None,
             queries: int) -> list[tuple[int, int, bool]]:
    """`(start, end, is_query)` runs of the inputs that may share a request.
    One run for every mode but `param`, which carries one input type per
    request, so its queries and its documents are separate runs (an empty
    side is no run). Each run is then batched by `BATCH`."""
    if options is None or options.input != "param":
        return [(0, count, False)] if count else []
    runs = [(0, queries, True), (queries, count, False)]
    return [run for run in runs if run[1] > run[0]]


def request_body(model: str, chunk: list[str], options: wire.EmbedOptions | None,
                 query: bool = False) -> dict:
    """One request's JSON body: `{"model", "input"}` -- byte for byte what was
    sent before options existed -- plus, in the `param` input type, the type
    as `<param_field>: <query_value or document_value>` (`query` says which),
    and a requested width as `<dimensions_field>: n`."""
    body: dict = {"model": model, "input": chunk}
    if options is None:
        return body
    if options.input == "param":
        body[options.param_field] = options.query_value if query else options.document_value
    if options.dimensions is not None:
        body[options.dimensions_field] = options.dimensions
    return body


def check_widths(vectors: list[list[float]], options: wire.EmbedOptions | None) -> None:
    """`DimensionsMismatchError` unless every vector is the width `options`
    requested (when they request one)."""
    if options is None or options.dimensions is None:
        return
    for vector in vectors:
        if len(vector) != options.dimensions:
            raise DimensionsMismatchError(options.dimensions, len(vector),
                                          options.dimensions_field)


#: The `code` of the "deadline passed before the request" error when it fires
#: for a call's **first** batch: no request of this call went out, so nothing
#: was spent and the caller files an aborted attempt rather than a failed one.
#: The same check on a later batch carries no code, because the batches before
#: it were sent (and billed). The kind and the wording are the same either way,
#: so a caller that never looks at the code degrades exactly as before.
NOT_SENT = "not_sent"

#: The `code` of every other error the call's own deadline raised: a later
#: batch it stopped before its request, a body it cut mid-read, or a timeout
#: that fired once it was spent. Whether that is a failure depends on whose
#: deadline it was -- the client's own `TIMEOUT` cutting a slow provider is the
#: provider failing; a caller's budget running out is the caller's clock
#: (`store.inference.embed`) -- so the client only names it.
DEADLINE = "deadline"

#: The holder keys a batch must report for the call's total to be the call's.
_SUMMED = ("prompt_tokens", "cost_usd")

#: Every key `embed` owns in the caller's holder. Cleared at call entry, so a
#: holder that was filled before never leaks a stale value into this call's row.
_OWNED = ("prompt_tokens", "cost_usd", "cost_basis", "model")

#: Longest provider-reported model id kept. The same bound
#: `store.alternates.MAX_MODEL_CHARS` puts on a model id reaching the ledger
#: (not imported: this module sits below the store). A longer one is not kept,
#: rather than clipped into a name nobody reported.
MAX_MODEL_CHARS = 200


class _Spend:
    """One call's running accounting: the caller's holder, the sums so far and
    the keys a read batch failed to report. Travels as one object so the three
    cannot be passed apart -- a fresh `lost` for a later batch would make it
    look like the first.
    """

    def __init__(self, usage: dict):
        self.usage = usage
        self.totals: dict[str, int | float] = {}
        self.lost: set[str] = set()
        #: A batch's request has gone out and its body is not read yet.
        self.in_flight = False
        for key in _OWNED:
            usage.pop(key, None)

    def lose(self) -> None:
        """A batch was sent and failed before its body was read: it may have
        been billed, and what it reported is unknown, so no sum is the
        call's. Every summed key is dropped for the rest of the call, as for a
        read batch that omits one (`fold`)."""
        self.in_flight = False
        self.lost.update(_SUMMED)
        for key in _SUMMED:
            self.usage.pop(key, None)
        self.usage.pop("cost_basis", None)

    def fold(self, body: object) -> None:
        """Fold one batch's parsed body into the holder.

        Runs **before** `_vectors` validates the body, so a reply that was
        billed and then turned out malformed still counts. Never raises: the
        usage block is trailing metadata, and a shape nobody expected costs the
        statistic, not the embed.

        A count is a sum over every batch, so it is only the call's count if
        every batch reported it. Once a batch that was read lacks one, that key
        is dropped and stays dropped for the rest of the call -- a partial sum
        is written as "absent" rather than as a smaller number (CLAUDE.md,
        Costs: a price nobody reported is never rendered as zero). A body that
        is not an object is a read batch that reported nothing. A batch that
        failed after its request went out but before its body was read (a
        transport error, an oversized body, the deadline mid-read) never
        reaches here: it may have been billed for counts nobody read, so it
        drops every sum (`lose`). One the deadline stopped before its request,
        or whose error body was read (an HTTP error status, a redirect), sent
        or spent nothing unread and leaves the sums as they are.

        `completion_tokens` is never written: an embedding generates nothing.
        The row's `operation: "embed"` is what tells a reader that absence is
        a structural zero rather than an uncounted half
        (`usage._completion_count`), so a rate can model the row.
        """
        body = body if isinstance(body, dict) else {}
        model = body.get("model")
        if isinstance(model, str) and 0 < len(model) <= MAX_MODEL_CHARS:
            self.usage["model"] = model
        block = body.get("usage")
        block = block if isinstance(block, dict) else {}
        seen = {"prompt_tokens": llm_usage.tokens(block.get("prompt_tokens")),
                "cost_usd": llm_usage.money(block.get("cost"))}
        for key in _SUMMED:
            value = seen[key]
            if value is None:
                self.lost.add(key)
            elif key not in self.lost:
                self.totals[key] = self.totals.get(key, 0) + value
        for key in _SUMMED:
            if key in self.lost:
                self.usage.pop(key, None)
            else:
                self.usage[key] = self.totals[key]
        if "cost_usd" in self.lost:
            self.usage.pop("cost_basis", None)
        else:
            self.usage["cost_basis"] = llm_usage.BILLED


def _lost_unread(spend: _Spend | None) -> None:
    """`spend.lose()` when the failing batch was sent and its body not read."""
    if spend is not None and spend.in_flight:
        spend.lose()


def _status_kind(status: int) -> str:
    """The `LLMError` kind for an HTTP status.

    Deliberately NOT a copy of the chat clients' function, which map every
    non-auth, non-429 status to `bad_response`. They can: nothing branches on
    the kind, it only picks the wording the user sees. Here `semantic._embed`
    *acts* on it — `bad_response` is the one kind it retries, because it is the
    one a document in the batch could have caused. A 500/502/503 is the server
    failing, not the input, so mapping it to `bad_response` made every context
    build during an outage send a second request that was already known to be
    getting the same answer.
    """
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "network"
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


def _number(value: object) -> float | None:
    """`value` as a float, or None if it is not a JSON number.

    `bool` is excluded explicitly: it is a subclass of `int`, so a stray JSON
    ``true`` would otherwise become the component 1.0 and quietly skew a
    similarity instead of reporting a malformed body.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value)
    except OverflowError:
        # JSON has no integer bound and Python's int has none either, so a
        # component of 10**400 arrives as an int that no float can hold. Left
        # uncaught this raised OUT of the client -- `_vectors` runs after
        # `_post`'s handlers, and `semantic.recall` catches only LLMError and
        # OSError -- so a malformed endpoint failed the whole context build
        # instead of falling back to keyword activation.
        return None


def _vectors(body: object, count: int) -> list[list[float]]:
    """The `count` vectors in `body`, ordered by the row's own `index`.

    Raises `EmbeddingsError("bad_response")` unless every slot is filled
    exactly once with a list of numbers — see the module docstring.
    """
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise EmbeddingsError("bad_response", "embeddings response has no data array")
    out: list[list[float]] = [[] for _ in range(count)]
    filled = 0
    seen_dims = 0
    for row in rows:
        if not isinstance(row, dict):
            raise EmbeddingsError("bad_response", "embeddings response row is not an object")
        index = row.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            raise EmbeddingsError("bad_response", f"embeddings response index out of range: {index!r}")
        raw = row.get("embedding")
        if not isinstance(raw, list) or not raw:
            raise EmbeddingsError("bad_response", "embeddings response row has no vector")
        # Both bounds go before the comprehensions below, which materialize a
        # float per component and then a second list of them -- that is the
        # allocation, and `len` is the only thing that can precede it. The
        # running total is what makes this a bound rather than a per-row
        # courtesy: 64 rows individually under MAX_DIMS still add up to 86MB.
        seen_dims += len(raw)
        if len(raw) > MAX_DIMS:
            raise EmbeddingsError(
                "bad_response", f"embeddings response vector has {len(raw)} components")
        if seen_dims > MAX_TOTAL_DIMS:
            raise EmbeddingsError(
                "bad_response", f"embeddings response carries {seen_dims} components in total")
        vector = [_number(v) for v in raw]
        if any(v is None for v in vector):
            raise EmbeddingsError("bad_response", "embeddings response vector is not numeric")
        if out[index]:
            raise EmbeddingsError("bad_response", f"embeddings response repeats index {index}")
        out[index] = [v for v in vector if v is not None]  # narrows for the type checker
        filled += 1
    if filled != count:
        raise EmbeddingsError("bad_response",
                              f"embeddings response has {filled} vectors for {count} inputs")
    return out


def _bounded(raw: bytearray, part: bytes) -> None:
    """Refuse a part that would take the body past `MAX_BYTES`.

    Before the append, not after. `iter_bytes` yields *decoded* bytes, so a
    gzip bomb makes one part far larger than the bound -- appending first
    copies it into `raw` as well, so the peak was the overshoot twice over.
    Checking first stops at the first oversized part and never copies it.

    What this cannot bound is `part` itself: httpx has already decoded and
    allocated it by the time it is yielded, and taking that away means
    `iter_raw` plus decompressing here, which trades a bounded overshoot for
    hand-rolled inflate. So the peak is MAX_BYTES plus one decoded chunk,
    against an endpoint the user chose."""
    if len(raw) + len(part) > MAX_BYTES:
        raise EmbeddingsError("bad_response", f"embeddings response exceeded {MAX_BYTES} bytes")


def _answer(status: int, location: str | None, raw: bytes) -> object:
    """The parsed body of a response read in full, or the error it answers:
    shared by both clients, so a redirect, an error status and a body that is
    not JSON mean the same thing from either door."""
    if 300 <= status < 400:
        # The client does not follow redirects, so a 3xx would sail past the
        # check below as a "success" whose body is empty, and surface as
        # "response is not JSON" -- which sends the user looking at a
        # perfectly good endpoint. A FastAPI-based server whose route is
        # `/embeddings/` returns 307 for the path this module builds, so this
        # is a plausible configuration, not a hostile one.
        #
        # Named rather than followed, deliberately. Following costs the
        # deadline: httpx bounds each network operation, so a chain of hops
        # multiplies TIMEOUT by the hop count, and this module has already had
        # to fight for that bound twice. `missing_key` is the kind for
        # "configured, but not usably" (it is what an empty base URL raises),
        # and it is not the kind `_embed` retries -- a second request would
        # take the same redirect.
        target = location or "somewhere else"
        raise EmbeddingsError(
            "missing_key",
            f"embeddings endpoint redirected ({status}) to {target} — "
            f"point the connection's base URL there")
    if status >= 400:
        raise EmbeddingsError(_status_kind(status),
                              _extract_error(raw.decode("utf-8", "replace")), status=status)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # A 200 that is not JSON is a captive portal or a proxy login page,
        # not a transport failure — naming it `network` would send the user
        # looking at their connection instead of their base URL.
        raise EmbeddingsError(
            "bad_response", f"embeddings response is not JSON: {exc}") from exc


def _check_call(texts: list[str], model: str, base_url: str,
                options: wire.EmbedOptions | None, queries: int) -> None:
    """What both clients refuse before anything is sent: options that cannot
    be sent or a `queries` out of range (`ValueError`), and -- for a call
    with texts -- no base URL or no model (`missing_key`, matching
    openai_compatible.stream: neither is a provider failure, they are "this
    is not configured yet", and the caller treats the whole kind as "stay on
    keyword activation")."""
    check_sendable(options)
    check_queries(queries, len(texts))
    if not texts:
        return
    if not base_url:
        raise EmbeddingsError("missing_key", "No embeddings base URL configured")
    if not model:
        raise EmbeddingsError("missing_key", "No embeddings model configured")


def _requests(texts: list[str], options: wire.EmbedOptions | None,
              queries: int) -> list[tuple[list[str], bool]]:
    """`(inputs as sent, is_query)` for each request a call makes, in order:
    every `segments` run, batched by `BATCH`."""
    sent = prepare_inputs(texts, options, queries)
    return [(sent[start:min(start + BATCH, end)], query)
            for begin, end, query in segments(len(sent), options, queries)
            for start in range(begin, end, BATCH)]


def _verify() -> ssl.SSLContext:
    # Same stale-$SSL_CERT_FILE guard as the chat clients: an override left
    # by a removed conda install points at a missing file and crashes TLS
    # setup, so honor it only when it exists.
    cert = os.environ.get("SSL_CERT_FILE")
    cafile = cert if cert and os.path.exists(cert) else certifi.where()
    return ssl.create_default_context(cafile=cafile)


def _headers(key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class EmbeddingsClient:
    def __init__(self, http: httpx.Client | None = None):
        self._http = http
        self._owns = http is None
        # This class's one production instance is a module global in
        # `context/semantic.py`, reached from `build_messages` -- which FastAPI
        # runs on a threadpool worker for every sync route handler (a roll, a
        # check). So two threads really can enter the lazy init below at once,
        # and an unguarded `if self._http is None` hands each of them its own
        # client: one is stored, the rest are dropped without ever being
        # closed, leaking a connection pool per race. Measured at 8 clients for
        # 8 threads. The async provider clients have no equivalent problem --
        # they are built once per app (#215) and only ever touched from the
        # loop thread.
        self._lock = threading.Lock()

    def _client(self) -> httpx.Client:
        with self._lock:
            if self._http is None:
                self._http = httpx.Client(
                    timeout=httpx.Timeout(TIMEOUT, connect=min(TIMEOUT, 10.0)),
                    verify=_verify(),
                )
            return self._http

    def embed(self, texts: list[str], model: str, key: str, base_url: str,
              deadline: float | None = None,
              usage: dict | None = None, *, options: wire.EmbedOptions | None = None,
              queries: int = 0) -> list[list[float]]:
        """Embed `texts`, returning one vector per input, in input order.

        An empty `texts` returns ``[]`` without touching the network — the
        steady state once every entry is cached, so it must be free.

        `deadline` is an absolute `time.monotonic()` instant, for a caller
        making several calls under one budget. Without it each call starts a
        fresh `TIMEOUT`, so a loop that checks its own budget before calling —
        `semantic._isolate` did exactly this — is bounded by that check plus a
        whole further TIMEOUT, not by the budget it thinks it is enforcing.

        `usage` is the holder `store.usage.Meter` hands a call: what the
        provider stated is folded into it as each batch's body is read --
        `prompt_tokens` and `cost_usd` (with `cost_basis`) summed over the
        batches, `model` as the provider named it. A key stays out unless every
        batch read reported it; see `_Spend.fold`. Without a holder nothing is read.

        `options` are the model's stated embedding options (01h), the object
        the caller's vector space was computed from, and `queries` how many of
        `texts`, from the first, are queries; the rest are documents
        (`prepare_inputs`). In the `param` input type the queries and the
        documents go in separate requests (`segments`), still one call under
        one deadline and one holder. A requested width is checked on every
        reply (`DimensionsMismatchError`). Options that cannot be sent, or a
        `queries` out of range, are a `ValueError` before anything is sent.
        """
        _check_call(texts, model, base_url, options, queries)
        if not texts:
            return []
        url = base_url.rstrip("/") + "/embeddings"
        # One deadline for the whole call, so batching cannot multiply it:
        # a per-request bound would let ten batches take ten times TIMEOUT.
        if deadline is None:
            deadline = time.monotonic() + TIMEOUT
        out: list[list[float]] = []
        spend = None if usage is None else _Spend(usage)
        for n, (chunk, query) in enumerate(_requests(texts, options, queries)):
            out.extend(self._post(url, chunk, model, key, deadline,
                                  spend=spend, first=n == 0, options=options, query=query))
        return out

    def _post(self, url: str, chunk: list[str], model: str, key: str,
              deadline: float, *, spend: _Spend | None,
              first: bool, options: wire.EmbedOptions | None = None,
              query: bool = False) -> list[list[float]]:
        try:
            body = self._fetch(url, chunk, model, key, deadline, first=first, spend=spend,
                               options=options, query=query)
        except EmbeddingsError:
            _lost_unread(spend)
            raise
        except httpx.HTTPError as exc:
            _lost_unread(spend)
            # A timeout sized to what was left of the deadline, firing once it
            # is spent, is the deadline's (`DEADLINE`), not the transport's.
            spent = (isinstance(exc, httpx.TimeoutException)
                     and time.monotonic() >= deadline)
            raise EmbeddingsError("network", str(exc),
                                  code=DEADLINE if spent else None) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            _lost_unread(spend)
            raise EmbeddingsError("network", str(exc)) from exc
        # Before `_vectors`, so a reply that was billed and is then refused as
        # malformed still counts.
        if spend is not None:
            spend.fold(body)
        # Deliberately outside the handlers above: `_vectors` raises
        # EmbeddingsError by design, and anything else escaping it is a bug in
        # this module, which must not be disguised as a provider failure.
        vectors = _vectors(body, len(chunk))
        # After the fold: a reply of the wrong width was read, and billed.
        check_widths(vectors, options)
        return vectors

    def _fetch(self, url: str, chunk: list[str], model: str, key: str,
               deadline: float, *, first: bool, spend: _Spend | None = None,
               options: wire.EmbedOptions | None = None, query: bool = False) -> object:
        """The response body, parsed, within what is left of the deadline.

        Streamed rather than read whole so the deadline can be enforced
        *between* chunks: that is the only place a drip-feeding server can be
        interrupted, since every arriving byte resets httpx's own read timer.
        A read already in flight when the deadline passes still runs to its own
        timeout, which is why that timeout is `READ_SLICE` and not the whole
        remaining budget — it is what bounds the overrun. The same loop drains
        error responses, so a slow 500 is bounded too.

        `first` says no batch of this call has been sent yet, which is what lets
        the spent-deadline error below say `NOT_SENT` (on a later batch it is
        the deadline's, `DEADLINE`). `spend` is told when the request goes out
        and when its body has been read (`_Spend.in_flight`), so a failure
        between the two drops the call's sums (`_lost_unread`).
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EmbeddingsError("network", "embeddings deadline passed before the request",
                                  code=NOT_SENT if first else DEADLINE)
        if spend is not None:
            spend.in_flight = True
        slice_ = min(remaining, READ_SLICE)
        with self._client().stream(
            "POST", url, headers=_headers(key),
            json=request_body(model, chunk, options, query),
            timeout=httpx.Timeout(slice_, connect=min(remaining, 10.0)),
        ) as resp:
            raw = bytearray()
            for part in resp.iter_bytes():
                _bounded(raw, part)
                raw += part
                if time.monotonic() > deadline:
                    raise EmbeddingsError(
                        "network", f"embeddings response exceeded {TIMEOUT}s",
                        code=DEADLINE)
            # The body is read: whatever follows (an error status, a redirect,
            # a body that is not JSON) was answered in full.
            if spend is not None:
                spend.in_flight = False
            return _answer(resp.status_code, resp.headers.get("location"), bytes(raw))

    def close(self) -> None:
        with self._lock:
            if self._owns and self._http is not None:
                self._http.close()
                self._http = None


class AsyncEmbeddingsClient:
    """The same endpoint, for a caller already on an event loop (roadmap
    01h-C4b): `store.inference.embed.embed` is its one door.

    It shares everything but the transport with `EmbeddingsClient` -- the
    request builder and its `param` split (`_requests`, `request_body`), the
    accounting (`_Spend`), the parser and its bounds (`_vectors`,
    `_bounded`, `check_widths`), the status map and the redirect refusal
    (`_answer`) -- so a reply means the same thing from either door.

    **One total deadline.** The whole call, every batch and every read,
    headers included, runs under one `asyncio.timeout`. That closes the hole
    the module docstring names for the synchronous client: a server that
    drip-feeds its headers a byte at a time is cut at the deadline, not at
    each read. The deadline's error is the sync client's: `NOT_SENT` when no
    request had gone out, else `DEADLINE`.

    **Cancellation is real.** A cancel mid-request drops the call's sums if a
    batch was in flight (`_Spend.lose`, since it may have been billed for
    counts nobody read) and propagates; the operation files the meter
    `aborted`.

    One per app, built in the lifespan and closed at its exit
    (`routes.get_embeddings`), and touched only from that app's loop, so --
    unlike the synchronous client -- it needs no lock around its lazy pool.
    """

    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(TIMEOUT, connect=min(TIMEOUT, 10.0)),
                verify=_verify(),
            )
        return self._http

    async def embed(self, texts: list[str], model: str, key: str, base_url: str,
                    deadline: float | None = None,
                    usage: dict | None = None, *, options: wire.EmbedOptions | None = None,
                    queries: int = 0) -> list[list[float]]:
        """`EmbeddingsClient.embed`, awaited: the same arguments, the same
        vectors in input order, the same errors -- with the deadline over the
        whole call."""
        _check_call(texts, model, base_url, options, queries)
        if not texts:
            return []
        url = base_url.rstrip("/") + "/embeddings"
        if deadline is None:
            deadline = time.monotonic() + TIMEOUT
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EmbeddingsError("network", "embeddings deadline passed before the request",
                                  code=NOT_SENT)
        spend = None if usage is None else _Spend(usage)
        out: list[list[float]] = []
        sent = False
        try:
            async with asyncio.timeout(remaining):
                for chunk, query in _requests(texts, options, queries):
                    sent = True
                    out.extend(await self._post(url, chunk, model, key, deadline,
                                                spend=spend, options=options, query=query))
        except TimeoutError as exc:
            _lost_unread(spend)
            raise EmbeddingsError("network", f"embeddings call exceeded its deadline ({TIMEOUT}s"
                                  " unless the caller set one)",
                                  code=DEADLINE if sent else NOT_SENT) from exc
        return out

    async def _post(self, url: str, chunk: list[str], model: str, key: str,
                    deadline: float, *, spend: _Spend | None,
                    options: wire.EmbedOptions | None, query: bool) -> list[list[float]]:
        try:
            body = await self._fetch(url, chunk, model, key, deadline, spend=spend,
                                     options=options, query=query)
        except EmbeddingsError:
            _lost_unread(spend)
            raise
        except httpx.HTTPError as exc:
            _lost_unread(spend)
            spent = (isinstance(exc, httpx.TimeoutException)
                     and time.monotonic() >= deadline)
            raise EmbeddingsError("network", str(exc),
                                  code=DEADLINE if spent else None) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            _lost_unread(spend)
            raise EmbeddingsError("network", str(exc)) from exc
        except BaseException:
            # A cancel -- the caller's, or the deadline's own -- with a batch
            # in flight: it may have been billed for counts nobody read.
            _lost_unread(spend)
            raise
        if spend is not None:
            spend.fold(body)
        vectors = _vectors(body, len(chunk))
        check_widths(vectors, options)
        return vectors

    async def _fetch(self, url: str, chunk: list[str], model: str, key: str,
                     deadline: float, *, spend: _Spend | None,
                     options: wire.EmbedOptions | None, query: bool) -> object:
        """The response body, parsed. httpx's own timeouts are only a floor
        here (the remaining budget, per operation); the call's
        `asyncio.timeout` is what bounds it."""
        # A second's slack, so the call's own deadline is what fires.
        remaining = max(deadline - time.monotonic(), 0.0) + 1.0
        if spend is not None:
            spend.in_flight = True
        async with self._client().stream(
            "POST", url, headers=_headers(key),
            json=request_body(model, chunk, options, query),
            timeout=httpx.Timeout(remaining, connect=min(remaining, 10.0)),
        ) as resp:
            raw = bytearray()
            async for part in resp.aiter_bytes():
                _bounded(raw, part)
                raw += part
            if spend is not None:
                spend.in_flight = False
            return _answer(resp.status_code, resp.headers.get("location"), bytes(raw))

    async def aclose(self) -> None:
        """Close the pool this client opened (an injected one is the
        caller's); the next call opens a new one."""
        if self._owns and self._http is not None:
            http, self._http = self._http, None
            await http.aclose()
