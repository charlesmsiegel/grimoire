# Remove unused declarations and duplicate rules

The user approved items 1-9 of the 2026-10-18 code-removal report and excluded item 10. This is behavior-preserving cleanup: retain every HTTP route and request model. Delete REFACTORING_REPORT.md after verification.

## Theory and scope

A stored text field is stripped only when it is a string; otherwise return its fallback unchanged. Use store.fieldtext.text for the seven local copies in facts, scene_ideas, briefing, search, timeline, absorb.materializer, and suggest. Keep unique boundary rationale beside the relevant consumers.

Remove the report's 22 unused frontend client methods, expectedImageQuery, DivergedRecord, routes.entities._entity_image_promote, routes.streaming._sse_response, and the test-only character_turns._selector_item. Retarget both selector regression tests to _selector_inputs(...)[0]; remove obsolete mocks and update comments without weakening assertions.

Extract continuity retargeting into continuity.retarget, below identity and absorb.materializer. Preserve canonical id, blank title, absent versus blank due, thread status restrictions, commitment kind/status preservation, and citation fields.

Extract bounded history trimming into store._retention.trim(entries, keep, *, rows, max_bytes). Pass each ledger's constants at the call. Preserve an oversized current batch and calculate each entry's serialized size once. Keep ledgers, locks, identifiers and writers separate.

Extract shape validation into store._idea_provenance.drivers and anchor. Keep campaign reference validation with its existing owners, and preserve accepted reference spelling, empty strings in other fields and malformed-shape handling.

Extract actor snapshot hashing into store._actor_hash.content_hash, retaining both public dir_content_hash names as aliases. Preserve ordered UTF-8 concatenation without new separators or sorting.

The three root-level helper modules have private names so importing them does not extend the frozen public store facade. Preserve the existing API snapshot.

## Acceptance

Retain generic record-art operations, run polling, membership, and all item-10 APIs. Keep the import graph acyclic and bind submodules in store imports. Preserve regression assertions; add characterization coverage for unpinned contracts. Run focused tests before and after, update only smaller lint baselines, then run the repository gate. Never regenerate frozen fixture homes or touch the real library. Report production-source savings separately from tests and documentation.
