# 08-S6 Group metadata from 07 — Plan

**Spec:** [08](../specs/2026-10-09-roadmap-08-history-search-documents-design.md), sections 4.2, 4.4 and 4.6, slice 08-S6. **Delivers:** 08-C1 in full (the `groups` slice). **Needs:** 08-S2, 07-S2 and 07-S7 (landed).

## Tasks

- [x] `CampaignInputs` takes one `overlay.group_capture(cid)` per sweep and reads `membership.campaign_snapshot(cid, capture=...)`, which applies 07's detached rule. It keeps the inverse index and each group's source file. A capture that fails means no groups, and `groups` is named in `degraded`.
- [x] The `groups` slice is `membership.scene_groups(index, <full cast refs>, visible=None, limit=None)`. Each row becomes `{group, name, secrecy, present, leader_present}`, uncapped, with gm-only rows included and marked. It is digested as canonical JSON, exempt from `MAX_META_REFS`, and never rendered. Each listed group's file joins the document's `materialized` paths.
- [x] Tests: a membership edit moves the key and `meta`, not the text, and calls no embedder; a leader-only group is listed; 70 groups are all listed and `groups` is absent from `truncated`; a gm-only group is listed, marked, and never in the text.

## Verification

`pytest tests/test_searchdocs_scene.py`, `make check-lint check-mypy`.
