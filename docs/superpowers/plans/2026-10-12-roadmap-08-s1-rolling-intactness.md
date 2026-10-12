# 08-S1 Extract the rolling-summary intactness test — Plan

**Spec:** [08 history SearchDocuments](../specs/2026-10-09-roadmap-08-history-search-documents-design.md), section 4.2 and slice 08-S1. **Delivers:** part of 08-C1 (the shared intactness test `intact_rolling` relies on). No new feature.

**Goal:** one store function answers "is this scene's stored rolling summary still about its transcript?" for both the route (`prior` / `stale`) and `store/searchdocs` (whether a running summary may enter a search document).

## Tasks

- [x] **Move the test.** `routes/scenes._rolling_view` and `_rolling_digest` move, docstrings and all, to `store/rolling_intact.py` as `view(cid, sid, scene, facts)` and `prefix_digest(cid, sid, covered, total)`. The module may import `scenes`, `regex` and `rolling_summary`; it is not `rolling_summary.py`, which `scenes.write` imports. Every route call site calls the store function; responses and prompts are unchanged. Registered on the `store` facade and its API snapshot.
- [x] **Rule-file signature.** `regex.layers.signature(cid, connections=())`: a `statcache.signature` (absent-ok) over `campaign.md` (the world a campaign sits on is read from there), the global, world and campaign rule files, and each named connection's record and rule file. `None` when a key does not resolve. `llm_connections.connection_path` is the public spelling of a connection record's path, for a caller that stats rather than reads it.
- [x] **Tests** (`test_rolling_intact.py`): the function's `stale` equals the route's over a fresh fold, an append, a hidden covered post, a hidden post past the prefix, a cut, an edit, a display-only and then a prompt rule; the signature moves with the campaign and global files and with a named connection's rules, and not with a connection nobody named.

## Deviations from the spec

- The spec leaves the module's name open; it is `store/rolling_intact.py`.
- The signature also covers `campaign.md` and each connection's record, because `layers.effective` reads both: the world comes from the campaign, and a deleted connection contributes no rules while its rule file can remain.

## Verification

`pytest tests/test_rolling_intact.py tests/test_rolling_summary_routes.py tests/test_regex_prompt.py tests/test_hide_from_context.py tests/test_turn_follow_ups.py`, the import, paths and regex-prompt guards, `make check-lint check-mypy`.
