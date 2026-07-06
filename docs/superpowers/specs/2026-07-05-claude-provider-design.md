# Claude Agent SDK Provider — Design

**Status:** shelved (plan written 2026-07-05, not started). Per convention, rename
this file and the matching plan to the current date before starting work.

## Motivation

Grimoire currently sends every generation to OpenRouter (`backend/src/grimoire/openrouter.py`),
billed per-token against an OpenRouter key. The owner has a Claude Pro/Max
subscription that already covers Claude Code usage. Routing grimoire's prompts
through that subscription avoids double-paying for tokens.

## Findings (verified 2026-07-05)

- **The plain Anthropic SDK (Messages API) cannot use a subscription.** Pro/Max
  does not include API access; the `anthropic` package requires API-key billing
  through platform.claude.com.
- **The Claude Agent SDK can.** `claude-agent-sdk` (PyPI) wraps the local Claude
  Code runtime and inherits its login. On a machine where Claude Code is logged
  in it just works; headless machines use `claude setup-token` →
  `CLAUDE_CODE_OAUTH_TOKEN`. Usage draws from the subscription's normal usage
  limits, shared with interactive Claude Code sessions
  (support.claude.com article 15036540, "Use the Claude Agent SDK with Your
  Claude Plan" — a planned separate SDK credit was paused).
- **Policy boundary:** subscription OAuth is for the subscriber's own use.
  Third-party developers may not offer Claude.ai login or route other users'
  requests through Pro/Max credentials (code.claude.com/docs/en/legal-and-compliance).
  Grimoire as a personal single-user app is on the permitted side; a distributed
  grimoire would need users to bring API keys.

## Design

A minimal provider seam, two providers:

| Piece | Decision |
|---|---|
| Provider selection | New config keys `provider` (`openrouter` \| `claude`, default `openrouter`) and `claude_model` (default `opus`; Claude Code accepts `opus`/`sonnet`/`haiku` aliases or full IDs). |
| Dispatch | `LLMClient` facade in new `backend/src/grimoire/llm.py` with `stream(messages, cfg)` / `complete(messages, cfg)`. Routes pass the whole config dict; the facade picks the provider per call. |
| Errors | `LLMError(kind, detail)` base in `llm.py`; `OpenRouterError` and new `ClaudeAgentError` subclass it. Routes catch `LLMError`. Existing error kinds (`missing_key`, `auth`, `rate_limit`, `network`, `bad_response`) gain `missing_dependency`. |
| Claude client | New `backend/src/grimoire/claude_agent.py`. Lazy-imports `claude_agent_sdk`; system messages become `ClaudeAgentOptions.system_prompt`; remaining turns are flattened into a single transcript prompt (the SDK takes one prompt string, not a message array); tools disabled, `max_turns=1`. |
| Dependency | Optional extra `grimoire[claude]` so the base install stays light. Missing SDK surfaces as a normal in-app error (`missing_dependency`), not a crash. |
| Streaming granularity | v1 yields per `AssistantMessage` (usually one chunk per reply). The SSE plumbing to the frontend is unchanged; only the typewriter effect degrades. |
| Frontend | Provider select on the Configuration page; OpenRouter key + model combobox shown only for `openrouter`, a plain Claude model input for `claude`. `key_set` gating (`_require_key`) becomes provider-aware. |

## Non-goals / deferred

- Token-level delta streaming via `ClaudeAgentOptions(include_partial_messages=True)`
  — verify the installed SDK's `StreamEvent` shape when picking this up.
- A model-list endpoint for Claude (the OpenRouter `ModelCombobox` stays
  OpenRouter-only).
- Auth status indicator (e.g. "Claude Code logged in ✓") on the config page.

## Risks

- **SDK API drift.** `claude-agent-sdk` is young; verify the import surface
  (`query`, `ClaudeAgentOptions`, `AssistantMessage`, `TextBlock`,
  `CLINotFoundError`, `ProcessError`) against the installed version before
  implementing Task 3 of the plan.
- **Shared limits.** Heavy grimoire use eats into interactive Claude Code
  capacity for the same subscription window.
- **Deployment coupling.** The backend must run on a machine with Claude Code
  installed and logged in (or `CLAUDE_CODE_OAUTH_TOKEN` set). Pointing
  `GRIMOIRE_HOME` at a synced folder does not carry auth across devices.
