# 07-S7 retrieval projections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide deterministic scene affiliation metadata and explicit visibility rules for future retrieval consumers.

**Architecture:** Extend S2's pure membership reader with one plain Pydantic projection. Candidate groups come from the inverse entries of the cast; group records remain authoritative. This slice adds no prompt, embedding, route or stored membership authority.

**Tech Stack:** Python 3.11+, plain Pydantic v1/v2, pytest; existing membership/group_refs leaf helpers.

**Spec:** [07 explicit group membership](../specs/2026-10-09-roadmap-07-explicit-group-membership-design.md), sections12.1-12.5,07-C3c,17.7. Consumes approved S2 Roster/Index/affiliated/groups_for/prompt_visible contracts; execute only after S2 code gates. User authorized all slices inline with an independent brutal-review gate.

## Global Constraints

- Plain Pydantic models; no dataclasses, Field, validators, ConfigDict or model methods for serialization. Use external serialization compatibility when required by a caller.
- SCENE_GROUP_LIMIT=8 is an explicit renderer cap. Defaults visible=None and limit=None retain every metadata group, including secret and gm-only, with secrecy marked.
- Renderers must pass prompt_visible; gm-only never reaches any model, including embeddings. Secret needs explicit narrator framing; actor perspective has no implicit opt-in.
- Cast is the consumer's own roster intersection. This pure projection does not read actors or validate existence.
- Present membership never claims past membership or actor knowledge. Membership/co-affiliation are recall signals, never ACTOR_KNOWN evidence.
- No new dependency, store write, migration, actor frontmatter, scene suggestion prompt or frozen fixture change. Placeholder fixtures only.
- Explicit worktree PY for make; preserve ratchets, guards and comments. Commit coherent increments liberally.

## Review Focus

1. A leader absent from Members still affiliates; duplicate slash/colon cast spellings count that actor once (Task1 tests).
2. More than8 groups on a cast: uncapped metadata retains all, while the explicit renderer cap follows the full ordering (Task1 tests).
3. A secret/gm-only group with a distinctive name: unframed prompt projection cannot expose that name; metadata retains its marked ref (Task1 tests).
4. A large library and tiny cast: candidate enumeration uses by_actor rather than scanning Index.groups (Task1 guarded mapping test).
5. Invalid cast tokens or a mutated returned model must not alter the Index or leak groups into successive calls (Task1 tests).

---

## File map

- Modify backend/src/grimoire/store/membership.py: SceneGroup, SCENE_GROUP_LIMIT, scene_groups and consumer-boundary docstrings.
- Create backend/tests/test_membership_projections.py: pure projection, visibility, bounds, ownership and candidate-cost checks.
- Validation artifact/execution ledger/ROADMAP-CHECKLIST.md: actual gate evidence only.

### Task 1: Pure scene projection, consumer contract and slice gates

**Files:** membership.py; test_membership_projections.py; slice validation and roadmap evidence.

**Interfaces:** Consumes S2 Index(groups,by_actor,digest), Roster(ref,name,secrecy,group_type,leader,members,headquarters), groups_for(index,actor), affiliated(index,group), prompt_visible(roster,*,secret_framed=False). Produces:

```python
class SceneGroup(BaseModel):
    group: str
    name: str
    secrecy: str
    present: tuple[str, ...]
    leader_present: bool

SCENE_GROUP_LIMIT = 8

def scene_groups(index: Index, cast: Iterable[str], *,
                 visible: Callable[[Roster], bool] | None = None,
                 limit: int | None = None) -> tuple[SceneGroup, ...]: ...
```

- [ ] **Step1: Write projection tests.** `test_scene_groups_orders_present_count_name_ref`: duplicate cast, independent leader-only affiliate, member+leader counted once; present refs sorted and leader_present exact. `test_scene_groups_normalizes_actor_cast_only`: characters/seraphine, pcs/winifred normalize to colon; malformed/nonactor tokens ignored, dangling well-formed refs retained if cast supplies them. `test_scene_metadata_uncapped_and_prompt_projection_explicit`:10 affiliate groups with public, secret and gm-only names; None defaults retain10 and secrecy; prompt_visible drops forbidden names; explicitly framed secret allowed, gm-only always absent; explicit8 uses final ordering. `test_scene_projection_candidate_cost`: guarded groups Mapping refuses whole-library iteration, permits only by_actor candidates. `test_scene_projection_is_pure_and_returns_independent_models`: input unchanged, returned edits do not affect a later call; empty cast and zero/negative limit return empty.
- [ ] **Step2: Run RED.** `make test-py-fast PY=<main absolute Python> TESTS=backend/tests/test_membership_projections.py`. Expected missing SceneGroup/scene_groups/SCENE_GROUP_LIMIT failures.
- [ ] **Step3: Implement the pure projection.** Normalize slash tokens by splitting once and use group_refs.actor_ref for validation; no case folding. Deduplicate cast, union group IDs from by_actor, look up those rosters, apply visible if supplied, compute sorted affiliated intersection and leader_present. Sort by(-len(present),name,group); limit only after ordering; nonpositive explicit limit yields empty. Construct new SceneGroup instances each call. Docstrings carry12.3/12.4: affiliation includes unlisted leader; audience expansion remains11's decision; current state never establishes history or knowledge; public/unframed visibility controls any rendered or embedded name; no actor-perspective secret opt-in. Existing APIs and no-caller prompt bytes stay unchanged.
- [ ] **Step4: Verify coherent pure deliverable and commit.** Membership/projection suites plus Pydantic/import guards; lint/mypy with unchanged or reduced baselines. Expected all pass. Commit independently before final gate documentation.
- [ ] **Step5: Complete slice gates.** `make check PY=<absolute main Python>` with the recorded Pydantic1 Python3.11 target override. Expected complete gate exits0; no skipped targets. Native /codex:review uses frozen diff. Independent agent uses brutal-review against this slice and its consumer boundaries; reproduce/fix blockers. Final installed /codex:adversarial-review uses frozen diff plus originating spec, especially metadata/rendering distinction. Record exact SHAs/threadIDs/counts and deferred limitations. No browser flow changed; pure contract tests are its verification.
- [ ] **Step6: Record completion.** Check only passed S7 cells; Landed stays blank pending integration authorization. Theory note explains the sole roster authority, inverse-bounded candidate set, current-state semantics and explicit model visibility boundary. Continue other slices already authorized.
