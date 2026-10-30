# Provider adapter (first slice)

**Status:** this is the first slice of [Model Routing And Budgeting](roadmap/sprints/model-routing-and-budgeting.md) (Wave 2, #013).

`grimoire.providers` is an *adapter* package: it uses the network, a clock and the environment, so no pure package may import it. Its scope is narrow:

- It sends **one planned, authorized model call** to an OpenAI-compatible streaming endpoint.
- It books every attempt on the turn's `TurnUsageLedger`.
- It accepts a reply only when the provider really finished.

What it does not include:

- no scene runner;
- no prompt composition (owned by 014 and 027);
- no prices;
- no persisted state.

A green test suite here does not mean an app exists.

## Shape

| Part | Role |
|---|---|
| `ProviderEndpoint`, `ProviderRegistry` | **Trusted configuration**, fixed when the adapter is built: provider id, base URL, whether a key is needed, the `max_tokens` field name and per-endpoint `Timeouts`. URLs are `https`, or `http` only for loopback (`127.0.0.0/8`, `::1`, `localhost`, with the connected peer checked). Callers never pass URLs. |
| `RouteChoice(provider_id, model)` | What a caller chooses: primary first, then fallbacks. |
| `Credentials` | Trusted code that knows the keys. Keys are wrapped in a `Secret` (printable ASCII, redacted `repr`). `EnvironmentCredentials` stands in for keys under the reserved `secrets/` root until the native file store (010) exists. |
| `ModelRequest` | Opaque messages plus a **required** `max_output_tokens`, so every call is bounded. |
| `TurnMeter` | One per turn and the **only booking path**. An adapter refuses a second live meter for the same campaign and turn; this guard is in-process only, and cross-process fencing belongs to 027 and 030. It holds the current ledger value and, for each call, its reports, status, delivery flag and fixed request digest. It is single-threaded: concurrent or re-entrant use is refused (`busy`). |
| `ProviderAdapter.execute(meter, call, request, routes, handle, …)` | Runs one planned call and returns a `CallResult`. |
| `HttpTransport` | `http.client` over TLS (or loopback HTTP). It has no proxy support and no injection seams. |
| `grimoire.testing.providers` | Test-only: `ScriptedTransport`, `ScriptedClock` and `explore_provider_faults`. |

## Rules

**Planned calls only.** These are refused before anything is sent:

- `UnplannedCallError`: a `PlannedCall` that is not exactly one of the plan's calls (fabricated, declined, from another turn, or with a forged purpose).
- `CallRefused`:
  - a call that already completed, was poisoned, or has no attempts left;
  - a call that is busy;
  - a call whose prompt changed after an attempt was booked;
  - a call that delivered text earlier, unless the caller passes `redo_after_delivery=True`.

**Authorization covers the destination.** For each attempt the adapter:

1. builds the exact body and wraps it in an envelope `{"url", "body"}`;
2. authorizes `execute` on `providers/<id>` with that envelope as the payload;
3. checks the `Decision` and its resource;
4. reads the key;
5. sends exactly the URL and body decoded from the authorized envelope.

The `approvals(label, attempt, envelope)` callback receives those bytes. Handles must come from the adapter's own `Authorizer` (`Authorizer.issued`). Authorization calls are serialized, because approval consumption is not thread-safe.

**An attempt begins at `connect()`.** Bookings follow from where the attempt ended:

| What happened | Booked as | Usage |
|---|---|---|
| Refusal before `connect()`: authorization denied, missing or bad key, cancelled first | **not booked** | none. If no attempt was ever booked, the call stays unmetered in the summary. The meter status is that of the latest execution (`refused` or `cancelled`), even when an earlier attempt was booked `failed`; the reports keep every booked attempt. |
| Cancelled while `connect()` was completing | `not_sent` | `0/0`; the connection is closed without writing. |
| `connect()` failed: refused, DNS, connect timeout, TLS | `not_sent` | `0/0`, measurement `estimated` |
| Anything after `connect()` returned | `failed`, `cancelled` or `completed` | counts the endpoint reported, else `None` |

**Booking is guaranteed:**

- Each attempt is booked in a `finally` that covers `BaseException`.
- If the precise attempt cannot be built or recorded, a fallback with unknown usage is booked, and the call ends without a reply.
- If even the fallback fails, or an interrupt lands mid-booking, the call is **poisoned**: it stays write-ahead marked and every later `execute` of it is refused. A poisoned call may hide a sent attempt, so the summary's `complete` flag is unreliable for that turn; `meter.poisoned_call_ids` lists such calls, and `cost_evidence` excludes the turn.

**`on_delta` text is provisional.** Deltas reach the consumer before the attempt ends, and may come from an attempt that later fails, truncates or is cancelled. A consumer may display them, but must commit nothing unless `CallResult.status == "accepted"`, and then only `CallResult.reply`.

**Completion and acceptance.** A `finish_reason` counts only when it is a non-null, non-empty string. Real streams send `null` on every content chunk.

- **After a finish reason**, the attempt is `completed` however the stream then ends: EOF, `[DONE]`, a reset, a timeout (reads shrink to a 5 s usage grace) or a cancel.
- **Before one**, how the stream ends decides:
  - EOF, or a chunked stream cut short, is `truncated` (`failed`);
  - a cancel is `cancelled`;
  - a read timeout or the total deadline is `timeout`.
- **Acceptance:** `CallResult.reply` is set only for `accepted`, meaning a completed attempt whose finish reason is `stop`. An empty string is a valid reply. Any other finish reason (`length`, `content_filter`…) is `incomplete`: billed, terminal, with no reply, so the speaker needs a new turn.

**Retries and fallbacks:**

- **Retryable kinds:** `timeout`, `network`, `rate_limit`, `server_error`, `truncated` and `bad_response`. In-stream and 200-body `error` objects count as retryable only when their code is 429 or 5xx.
- **Only before delivery:** they are retried only while no text has reached `on_delta` for this call, and only within `max_attempts_per_call`.
- **Route choice:** attempt *i* of an execution uses `routes[i]`, then repeats the last route.
- **`Retry-After`:** honored only as delta-seconds, capped at `max_retry_wait` (30 s), and the wait is cancel-aware.
- **Terminal kinds:** `auth`, `account_limit`, `bad_request`, `tls`, `peer_not_loopback`, other provider errors, cancellation and consumer exceptions. `tls` covers certificate failures and TLS protocol errors (such as plaintext on a TLS port); a TLS connection reset is `network`.
- **After delivery,** any failure ends the call (fail visibly).

**HTTP status mapping:**

| Status | Kind |
|---|---|
| 401, 403 | `auth` |
| 402 | `account_limit` |
| 408, 504 | `timeout` |
| 429 | `rate_limit` |
| other 4xx | `bad_request` |
| 5xx | `server_error` |
| 1xx, 3xx, other 2xx | `bad_response` |

Redirects are never followed. A 200 whose first line is not SSE is classified from its whole body (up to 64 KiB).

**Usage** maps only the documented OpenAI fields:

| Token field | Source |
|---|---|
| `input_tokens` | `prompt_tokens` (already includes cached tokens) |
| `output_tokens` | `completion_tokens` |
| `cache_read_tokens` | `prompt_tokens_details.cached_tokens` |
| `reasoning_tokens` | `completion_tokens_details.reasoning_tokens` |
| `cache_write_tokens` | always `None` |

A bad count (bool, negative, non-int or absent) is `None`, never `0`. Inconsistent slices are dropped to `None` and recorded in `usage_note`.

## Provenance and the evidence rule

The adapter alone chooses `Measurement`:

| Attempt | Measurement |
|---|---|
| Sent through **exactly** `HttpTransport` | `provider_reported` |
| Sent through any other transport, including the scripted fake and `HttpTransport` subclasses | `synthetic` |
| `not_sent` | `estimated` |

`provider_reported` means "counts that arrived over a real socket from the configured endpoint". It does not mean verified billing.

The route label `<id>@<host[:port]>/<model>` and `AttemptReport.peer_loopback` show whether that endpoint was local. Latency (`first_byte_s`, `total_s`) is `measured` only over `HttpTransport` with the real monotonic clock.

**Evidence rule for cost comparisons (015):** use a turn only when `meter.cost_evidence` is true. That requires all of the following:
- every count is provider-reported;
- every planned call was accepted;
- no call is poisoned (`meter.poisoned_call_ids`);
- every sent attempt reached a non-loopback peer.

A loopback test or self-hosted server is therefore never hosted-billing evidence, even though its counts are `provider_reported`.

## Privacy

None of the following ever contains prompt text, reply text or a key:

- the ledger;
- reports;
- `CallResult`, `ProviderError` and meter reprs;
- the authorizer audit.

Further rules:

- **Provider error messages** may echo the prompt, so they live only in `ProviderError.diagnostic`. That field is excluded from `str` and `repr`; the key is redacted from the whole message *before* it is truncated to 300 characters. Provider-chosen `error_code`, `error_type` and served model names are restricted to short ASCII identifiers and dropped if they contain the key. A finish reason that is not a short identifier, or contains the key, is reported as `other` (still finished, never accepted).
- **Transport and HTTP exceptions** are converted outside their `except` blocks, so tracebacks (even with `capture_locals`) carry neither the Authorization header nor stdlib frames.
- **CI** uses loopback servers and scripted transports only.

## Limits of this slice

- **Not yet supported:** proxies, Anthropic's native wire shape, async, embeddings, tools, images, prices and session budgets.
- **DNS** resolution is not bounded by the socket timeouts.
- **Each attempt has a hard deadline** (`Timeouts.total`) that counts connection time: a timer shuts the socket down, so a trickling stream cannot outlive it, and a connection that used up the budget sends nothing (`not_sent`, `timeout`). The wait for the status line uses the read timeout, not the connect timeout.
- **Cancellation** wakes a blocked read by shutting down the socket. A cancel during `connect()` waits for the connect timeout. On Windows, waking a blocked `recv` is not guaranteed (the test is `xfail` there).
- **Stale-run fencing** across processes is not covered (owned by 027 and 030).

## Open question for 027

The adapter works with trusted and untrusted handles alike. TOOL_AUTHORITY currently says that code which parses imported content or model output must use an untrusted handle. Such a handle needs a human approval per attempt, which a live scene cannot use.

The open question for the requester and 027: may a generation runtime hold a trusted `app:` handle, on the grounds that the destination comes only from trusted configuration and prompt text is payload, never authority? This slice does not narrow the rule.

## Downstream consumers

- **027** calls `execute` once per speaking NPC.
- **015** uses the evidence rule above.
- **014** may use the scripted fake.
- **035** decides who consumes `on_delta`.
- **010** supplies `Credentials` from `secrets/`.
