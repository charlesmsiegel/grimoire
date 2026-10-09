# Incoming LLM capture

Set **Settings → Logging → Debug** before starting a generation. The
next call captures the full incoming response body in the existing
`<library>/logs/YYYY-MM.jsonl` file, under `kind: "llm_incoming"` and
`module: "llm.capture"`. Returning the floor to Info stops capture.

HTTP adapters record every decoded SSE line before parsing: reasoning,
unknown JSON fields, usage-only chunks, comments, blank lines, malformed data,
and `[DONE]`. HTTP error responses retain their body too. Claude records every
message and field exposed by the Agent SDK; this cannot recover fields the
SDK itself discarded. This is decoded response capture, not a byte-level
network trace. Request bodies, request URLs, and HTTP headers are not captured.

A native decision (a provider's own decisions endpoint, used for a model that
cannot generate text) has no stream. Its adapter records the whole reply as one
`decision_body` event, before any field is read, so the answers, the usage
block and any field the contract does not use are all in it; an error response
is recorded as `http_error_body`, as for a chat call. One native item is one
call with its own `call_id` (its retries are attempts of that call), so a batch
of N items is N calls here, and an item refused before it was sent records
nothing. The request body is not captured, as for every other call; where a
decision is also kept in the prompt log (a separate record, not this file), that
shows the normalised request the item was sent.

Rows carry a logical `call_id`, `attempt`, requested `model`, `provider`,
per-attempt `sequence`, and `elapsed_ms` since that attempt started. The
provider's actual model field remains in the captured body. Start/end events
mark each attempt; end status distinguishes complete, error, and interrupted
calls. Retries and fallback attempts share a call ID and keep separate frames.

To reconstruct one event, group rows by `(call_id, attempt, sequence)`, order
them by `part`, concatenate their `payload` strings, and JSON-decode the result
once. SSE and error payloads become the original decoded strings; SDK payloads
become objects with message `type` and `fields`. `parts` gives the expected
number of pieces. Long fields are split, not clipped. This can be used to
inspect reasoning fields without adding them to the scene or later prompts.

The existing monthly log cap still applies and writes `log_capped` when hit.
Changing the floor mid-call or a failed disk write can also leave an incomplete
capture; missing parts or an absent end event must not be read as a complete
response. Logs are best-effort diagnostics, not an archival guarantee. No
whole-response buffer is held in memory. Debug mode does add serialization
and disk writes on the streaming path; arrival timings include that observer
overhead and are not the proposed per-operation latency instrumentation.

Full response bodies can contain private campaign prose and reasoning. Inspect
the log before sharing it. The absorb duplicate check (the `continuity-identity`
task) is no exception: its replies, including each row's `reason`, are captured
at Debug level like every other LLM response, under the existing Settings
disclosure, while its info-level log row carries counts and modes only. The
reconciliation sweep after End Scene or a refresh (the `continuity-reconcile`
task) is the same: its replies, including each proposal's `reason`, are
captured at Debug level under that disclosure, and its info-level log row
carries counts and modes only. No model settings or reasoning requests are changed: capture records only what the
provider or SDK actually sends.
