# Campaign settings (#73)

## Intent and scope

Finish the remaining campaign settings described in issue #73 while reusing the
model-routing and campaign controls that already ship. A campaign follows global
defaults until its author explicitly overrides a field. Storage, credentials,
appearance, providers and embedding selection remain global.

## Configuration contract

`GET /api/campaigns/{cid}/settings` returns `overrides`, `inherited` and
`effective` maps. The allowlist is `context_scan_depth`, `context_budget`,
`recap_depth`, `archive_depth`, `rolling_summary_every`, `scene_break_every`,
`system_prompt`, `user_label`, and `assistant_label`. Values are strings.
`PUT` accepts a partial map of those fields: a string selects an override and
`null` removes it. Unknown fields and invalid values refuse the entire write
with 400; nonexistent campaigns answer 404. GET and PUT return no credentials
or unrelated global settings.

Counts are ASCII decimal integers in 0..2147483647, a portable signed 32-bit
range; oversized strings are rejected before conversion. Zero is a real
override: no scan/recap/archive window, no context ceiling, or disabled automatic
summary/break scheduling respectively. Labels must
contain non-whitespace text and no line boundaries. A system prompt is arbitrary
text, including multiline text and an explicitly empty string. An empty override
suppresses the global system prompt; inheritance requires removing the override.
Text must encode as UTF-8: unpaired Unicode surrogates are invalid, and a
manually encoded invalid prompt falls back to the global value. Malformed JSON
shapes are not prompts and must not reach recursive JSON decoding.
Campaign default prompts remain additive standing context, separate from this
replacement of the global system prompt.

Store numeric and label fields as string frontmatter in `campaign.md`. Store
the prompt in `system_prompt_json`, an ASCII-escaped JSON string scalar, because the
frontmatter writer deliberately flattens literal line boundaries. No migration
or automatic write occurs when a campaign merely inherits. Malformed manually
edited override values fall back to the corresponding global value. A settings
write reads strictly, holds the campaign lock across read/merge/atomic publish,
preserves unrelated metadata and body, and advances `updated` and the revision
only when something changed. Register its mutator module in the lock domain.

## Consumers

Use one `store.campaign_settings` module for the allowlist, validation, effective
values and writes. Keep `store.config` independent of campaign readers to retain
the acyclic import graph. Context scans, prompt packing, recap selection, archive
retrieval, history scan/omission/budget calculations and the rolling-summary and
scene-break routes must use the campaign's effective values. Opener full-recap
depth remains its existing deliberate fixed override. Display labels read the
campaign settings endpoint, not the global config cache. With no override, existing
prompts and behavior must remain unchanged.

## User interface

Add `/campaigns/:cid/settings`, reachable from the campaign hub and inspector.
Use the existing page shell and navigation primitives. Provide Models, Context &
summaries, Calendar and Advanced sections. Reuse `CampaignModels` and
`CalendarConfig`; preserve existing hub panels and inspector access rather than
breaking existing controls. Keep a link to global storage settings instead of
inventing campaign-specific storage.

The context and advanced sections show each field's effective/inherited value,
an explicit choice to override it, and a save/cancel flow. Unchecking override
removes a stored setting. Controls display pending/error states, preserve drafts
on failed saves, prevent duplicate saves, ignore responses belonging to another
campaign, and refresh the play view after settings change. Settings reads bypass
the cache; writes announce the existing config-change event. Navigating with a
dirty draft asks before discarding it or retains the draft until explicitly
cancelled. This includes the reused Calendar and Models forms, including a
Models row change that would discard its open form. The page sends no model call.

## Verification and review

Pin API precedence, reset-to-inherit, explicit zero, empty/multiline prompt,
invalid mixed writes, missing campaign, no-op writes, unrelated metadata/body,
strict corrupt-record refusal, and cross-campaign isolation with isolated stores.
Prove real context assembly and summary scheduling use overrides. UI tests cover
read-only values, edit/save/cancel, failed saves, navigation/campaign changes,
and reused settings sections. Run focused tests, the full `make check` gate
with the main checkout's Python interpreter, and isolated browser verification
when available. Run the repository's Codex checkpoints for spec, plan, diff,
and final conformance before reporting completion.

## Theory

Campaign settings are an explicit partial opinion over global play defaults.
Absence means inherit; an empty prompt and a zero count are deliberate opinions.
The main risk is a consumer reading globals after another consumer has applied
overrides, so verification follows each count through retrieval, composition,
inspection and scheduling rather than checking only the settings endpoint.

## Spec checkpoint

Codex adversarial review identified ambiguous zero scheduling and unbounded
numeric conversion. Both are resolved above; consumer tests must include disabled
scheduling, both numeric boundaries, and oversized decimal strings.


## Implementation review and validation outcome

The implementation passed independent review after correcting retained-editor
navigation, pending-save protection, malformed prompt decoding and invalid Unicode
diagnostics. The final Codex adversarial comparison against this spec returned
PASS with no unresolved actionable findings.

Feature, frontend, browser and build results are recorded in the
[implementation plan](../plans/2026-10-14-campaign-settings.md#verification-record).
The complete gate passed, including the unchanged backend coverage floor and
both full backend suites (current Pydantic and Pydantic 1). Verification also
resolved temporary-library cache ownership, timer-ownership reporting and
CPU-timing quantization in existing guards. Those changes passed the same code
and adversarial conformance checkpoints. The plan records exact counts, the
isolated environment, retained negative controls and the deliberately added
campaign-settings facade name; frozen campaign and golden wire fixtures remain
unchanged.
