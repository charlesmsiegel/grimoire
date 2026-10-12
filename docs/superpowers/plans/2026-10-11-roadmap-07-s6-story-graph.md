# 07-S6 Story Graph groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show structured group affiliations for actors already in the Story Graph, without expanding its cast.

**Architecture:** Read S2's effective inverse once in phaseC and build a separate fail-soft structural family after actor creation. The existing graph tuples, typed frontend unions, lens arithmetic, layout and node detail own the presentation; group records stay the only membership authority.

**Tech Stack:** Python3.11+, existing read-only graph/overlay and Pydantic inverse; TypeScript/React, pytest/Vitest. No new dependency.

**Spec:** [07 explicit group membership](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), section10,07-C3b,17.5. Consumes approved S2 membership.campaign_index(cid)->Index; execute after S2 code gates. User authorized all slices inline and independent brutal-review gates.

## Global Constraints

- NODE_KINDS gains group after location; EDGE_KINDS gains member_of and leads after birthday_of; PARTS gains groups before names. Pins and TS unions agree.
- Actor->group edges are structural, IDs from edge_id(kind,actor,group). Groups create no actor nodes; a group without an existing actor affiliate has no node.
- A group node has typed id,kind,label,group_type,secrecy. GM-only groups are visible on this GM page and marked; no prompt-model visibility implication.
- One campaign_index call per build, in phaseC outside the campaign hold, in its own groups _attempt. An ordinary group source/family failure omits groups only. GroupIdentityConflict propagates before payload delivery, including warm memo hits. Preserve temporal plugin/lock separation and best-effort read behavior.
- No store mutation, persistent graph, actor/card/image N+1, new keyboard binding, standing fact family or frozen-fixture change.
- Plain Pydantic when introducing a model, never dataclasses or model configuration/validators. Placeholder names only; guards/ratchets preserved.
- Use explicit worktree PY; commit coherent test/code/doc updates liberally. Native review and independent brutal-review are required slice gates.

## Review Focus

1. Membership references an actor not in any existing graph family: it must not create that actor or a group node with no valid edge (Task1 tests).
2. Leader and member are the same actor: preserve both independently identified edges, one node each (Task1 tests).
3. An unreadable group omits groups only; a corrupt strict identity ledger refuses the whole response, including warm hits (Task1 error-boundary tests).
4. Groups enabled while Actors disabled: every drawn edge still has two visible ends; toggles and Cast preset use one payload with no request (Task2 tests).
5. GM-only group and duplicate labels: type/secrecy are readable, deterministic placement and keyboard selection still work (Task2 tests).

---

## File map

- Modify backend/src/grimoire/store/continuity/graph.py: tuple contracts, phaseC index capture and groups family.
- Extend backend/tests/test_continuity_graph.py: graph pins, groups behavior, failure isolation and constant read cost.
- Modify frontend/src/api/types.ts: NodeKind/EdgeKind/GraphPart and typed group GraphNode variant.
- Modify frontend/src/components/storyGraph/model.ts, layout.ts and NodeDetail.tsx, and model.test.ts/layout.test.ts; extend routes/StoryGraphView.test.tsx.
- Extend frontend/src/testkit/storyGraph.ts only for existing typed fixture factory support.
- Validation/execution/roadmap records: actual outcomes only.

### Task 1: Effective, fail-soft structural group family

**Files:** graph.py, test_continuity_graph.py and test_continuity_routes.py; TS graph unions for the cross-language pin.

**Interfaces:** Consume membership.campaign_index(cid)->Index and its Roster(ref,name,group_type,secrecy,leader,members). Produce group node shape `{id:'groups:<id>',kind:'group',label:<name>,group_type:<type>,secrecy:<value>}` and ordinary structural `_edge('member_of'|'leads',actor,group)` rows. `_group_parts(index: membership.Index | None, nodes: Mapping[str,dict])->tuple[list,list]` consults the live existing node mapping; only character/pc nodes are eligible.

- [ ] **Step1: Write backend tests.** Update tuple/ts_union pins exactly to spec. `test_graph_group_edges_only_name_existing_actors`: Mara in scene, Winifred absent, Seraphine leader-only present through existing relationship/scene family; only present actor edges, no dangling actor creation, irrelevant group absent. `test_graph_leader_and_member_independent_edges`: dual roles yield member_of+leads with distinct IDs; GM-only node retains secrecy and group_type. `test_graph_group_effective_overlay`: inherited detached affiliates filtered; owned-authored refs remain, group copy/edit changes the next graph, dangling refs do not manufacture nodes. `test_graph_bad_groups_omit_only_groups`: ordinary unreadable group file retains unrelated families and adds groups once to omitted. `test_graph_bad_identity_refuses_delivery`: both strict ledgers malformed/unreadable, cold and warm index, raise GroupIdentityConflict and HTTP409; no graph payload. `test_graph_groups_include_plot_and_birthday_only_actors`: affiliates created solely by thread/commitment involvement and birthday_of each receive their appropriate edges. `test_the_graph_reads_groups_once`: instrument campaign_index at both existing read-cost sizes; assert one call outside campaign hold, no per-node entity/card/image reads. Existing deterministic/per-edge endpoint tests include new family.
- [ ] **Step2: Run RED.** `make test-py-fast PY=<main absolute Python> TESTS=backend/tests/test_continuity_graph.py`. Expected updated pins and group nodes/edges missing.
- [ ] **Step3: Implement backend family and typed union.** Import membership and group_refs as store module bindings. Add an explicit GroupIdentityConflict re-raise before _attempt's generic source softening, so strict failures anywhere in graph construction cannot be converted to a partial payload. Capture via `_attempt(omitted,'groups',membership.campaign_index,None,cid)` beside names in build phaseC, pass the captured Index to _assemble, insert group family immediately after events and before scene_ideas, after EVERY actor-producing family (chronicle, plot and events). Build group nodes only after collecting nonempty eligible edges; keep raw canonical group/actor refs. Reuse existing dedup/sort/_edge functions, no new node authority or cache. Update historical comments to explain spec07 lifts groups only; standing facts stay parked. Add typed group variant and unions in TS so its existing pin remains meaningful.
- [ ] **Step4: Verify and commit.** Graph and membership suites plus import/lock/overlay/Pydantic guards, lint/mypy; expected all pass with ratchets unchanged or reduced. Existing graph regression pin cited by capstone stays the acceptance test. Commit backend independently.

### Task 2: Groups toggle, band and readable group detail

**Files:** storyGraph/model.ts,layout.ts,NodeDetail.tsx and their tests; StoryGraphView.test.tsx, api/types.ts, testkit/storyGraph.ts as needed.

**Interfaces:** Show gains groups:boolean; label Groups; OFF false; Cast preset true with actors. NodeDetail uses already projected edges/nodes only; campaign record links through existing sectionHref with `{kind:'record',at:'groups',rid:<group-id>}`. No new API request or edit surface.

- [ ] **Step1: Write frontend tests.** Model toggles default-off/explicit-on, Cast preset, both endpoints visible, member_of/leads independently drawn, groups with Actors off never retain dangling edges. Layout group band below locations, stable name then ref order with duplicate labels, both play/calendar modes place every visible node once. StoryGraphView selection detail shows Type and secrecy (including gm-only), Members and Leader from graph edges (not absent roster actors), chips select existing actors, campaign group link resolves same scope. Existing keyboard/live selection tests remain green without new bindings.
- [ ] **Step2: Run RED.** Existing graph model/layout/view test files with new tests fail unknown groups or absent detail/placement; typecheck exposes every exhaustive NodeKind switch to update.
- [ ] **Step3: Implement UI.** Extend Show/defaults/presets, lensKeeps/edgeKeeps and typed label/status switches using existing patterns. Groups toggle is generated with current Show controls. Add group slot handling and a label/ref sorted separate band below locations; include calendar mode rather than leaving group unplaced. Detail shows type/secrecy, projected member/leader chips independently, and campaign group record link. Preserve graph no-ref-leak labels, accessible edge restatement, existing viewport/selection behavior.
- [ ] **Step4: Verify coherent UI and commit.** Run model/layout/view tests, frontend typecheck and eslint ratchet; expected all pass. Commit before final docs.
- [ ] **Step5: Run slice gates.** Required backend/frontend ownership tests plus full `make check PY=<absolute main Python>` with recorded Pydantic1 Python3.11 target override. Isolated browser verifies Cast groups, leader/member chips, group link and toggles. Actual installed /codex:review on frozen diff, independent brutal-review agent on input classes above, final installed /codex:adversarial-review against diff and originating spec. Reproduce/fix blockers, regular checkpoints; record exact test/gate SHAs/threadIDs and limitations.
- [ ] **Step6: Record completion.** Update only passed S6 cells; Landed remains blank until integration authorization. Theory note describes existing-actor endpoint closure, sole roster authority, constant family read and fail-soft omission. Continue authorized remaining slices.
