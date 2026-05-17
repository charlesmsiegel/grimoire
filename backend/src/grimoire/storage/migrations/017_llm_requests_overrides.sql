-- Per-call retry/timeout overrides on the LLM request audit row (spec
-- llm-gateway-remaining §7). When a caller passes `retry=` or `timeout=`
-- to `gateway.complete`/`stream`/`embed`, the override is serialised as
-- JSON and persisted here so post-hoc analysis ("which drift-check calls
-- used a tighter timeout?") can read it without replaying the event bus.

ALTER TABLE llm_requests ADD COLUMN retry_override TEXT;
ALTER TABLE llm_requests ADD COLUMN timeout_override TEXT;
