# Auxiliary Tasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-auxiliary-tasks-design.md`. Hard deps on `extraction-modes` (NONE mode + select_mode hook for `aux_task is not None`) and `swipes-alternates` (`rewrite_post` accept → switch_primary).

**Architecture:** Six branches.

- **A** `feature/aux-A-types-prompts` — `AuxiliaryTask`, `AuxiliaryResult`, `CommitAction`, Jinja prompt templates.
- **B** `feature/aux-B-context-suppression` — Context Builder branches on `auxiliary_task`; budget + suppression matrix.
- **C** `feature/aux-C-runner` — `orchestrator/auxiliary_runner.py` separate loop; `run_auxiliary_task` entry point.
- **D** `feature/aux-D-accept-dispatch` — Accept dispatch per task kind (submit/replace/append/copy/draft).
- **E** `feature/aux-E-rest-ws` — REST routes (7 POST start + accept/discard) + WS aux_* events.
- **F** `feature/aux-F-frontend` — Auxiliary panels, entry-point affordances, in-flight indicator.

**Tech Stack:** Python 3.12, FastAPI, pytest-asyncio, Jinja2; React/TS frontend.

---

## Conventions

Standard. **Hard prerequisites: `extraction-modes` + `swipes-alternates` merged.**

---

## Branch setup

- [ ] **Step S1: Worktrees**

```powershell
git worktree add .worktrees/aux-A-types         -b feature/aux-A-types         main
git worktree add .worktrees/aux-B-context       -b feature/aux-B-context       main
git worktree add .worktrees/aux-C-runner        -b feature/aux-C-runner        main
git worktree add .worktrees/aux-D-accept        -b feature/aux-D-accept        main
git worktree add .worktrees/aux-E-rest-ws       -b feature/aux-E-rest-ws       main
git worktree add .worktrees/aux-F-frontend      -b feature/aux-F-frontend      main
```

---

# Branch A — Types + prompt templates

### Task A1: AuxiliaryTask, AuxiliaryResult, CommitAction

**Files:**
- Create: `backend/src/grimoire/auxiliary/__init__.py`.
- Create: `backend/src/grimoire/auxiliary/types.py`.
- Test: `backend/tests/auxiliary/test_types.py`.

- [ ] **Step 1: Failing tests**

```python
from grimoire.auxiliary.types import (
    AuxiliaryResult, AuxiliaryTask, CommitAction, TaskKind,
)


def test_task_kinds_complete():
    assert {k.value for k in TaskKind} == {
        "impersonate_pc", "rewrite_post", "continue_as",
        "what_would_x_say", "brainstorm", "edit_prose", "translate",
    }


def test_task_kind_to_commit_action_map():
    from grimoire.auxiliary.types import commit_action_for
    assert commit_action_for(TaskKind.IMPERSONATE_PC) == CommitAction.SUBMIT_POST
    assert commit_action_for(TaskKind.REWRITE_POST) == CommitAction.REPLACE_POST
    assert commit_action_for(TaskKind.CONTINUE_AS) == CommitAction.APPEND_POST
    assert commit_action_for(TaskKind.WHAT_WOULD_X_SAY) == CommitAction.COPY
    assert commit_action_for(TaskKind.BRAINSTORM) == CommitAction.COPY
    assert commit_action_for(TaskKind.EDIT_PROSE) == CommitAction.REPLACE_DRAFT
    assert commit_action_for(TaskKind.TRANSLATE) == CommitAction.REPLACE_DRAFT
```

- [ ] **Step 2: Implement** (per design spec).

- [ ] **Step 3: Commit.**

### Task A2: Prompt templates

**Files:**
- Create: `backend/src/grimoire/auxiliary/prompts/impersonate_pc.j2`, `rewrite_post.j2`, `continue_as.j2`, `what_would_x_say.j2`, `brainstorm.j2`, `edit_prose.j2`, `translate.j2`.
- Test: `backend/tests/auxiliary/test_prompts.py`.

- [ ] **Step 1: Failing test**

```python
def test_each_kind_has_template_file():
    from grimoire.auxiliary.prompts import load_template
    for kind in TaskKind:
        tmpl = load_template(kind)
        assert tmpl is not None


def test_impersonate_pc_renders_with_pc_name():
    rendered = load_template(TaskKind.IMPERSONATE_PC).render(
        pc_name="winifred", scene_summary="In the parlor at midnight.",
    )
    assert "winifred" in rendered


def test_translate_minimal_when_no_target_language():
    rendered = load_template(TaskKind.TRANSLATE).render(
        snippet="The crow lit on the wall.", target_language="French",
    )
    assert "French" in rendered
```

- [ ] **Step 2: Write 7 minimal Jinja templates** with task-appropriate instructions; load via `jinja2.Environment(loader=PackageLoader("grimoire.auxiliary", "prompts"))`.

- [ ] **Step 3: Commit + merge A.**

---

# Branch B — Context Builder suppression

### Task B1: Branch on `auxiliary_task`

**Files:**
- Modify: `backend/src/grimoire/context/builder.py:build` — accept `auxiliary_task` parameter.
- Create: `backend/src/grimoire/auxiliary/budgets.py` — `resolve_voice_targets`, per-task budget plan.
- Test: `backend/tests/context/test_auxiliary_suppression.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_brainstorm_omits_pc_card_and_scene_header(builder, seeded_state):
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM)
    prompt = await builder.build(... auxiliary_task=task ...)
    blob = "\n".join(m.content for m in prompt.messages)
    assert "[pc_card" not in blob
    assert "[scene_header" not in blob


async def test_impersonate_pc_includes_active_pc_full_card(...):
    ...


async def test_rewrite_post_loads_original_speakers_voices(builder, seeded_state):
    # Scene's post-to-rewrite has speakers A and B; their voice anchors loaded
    ...


async def test_continue_as_loads_target_npc_only(...):
    ...


async def test_aux_task_suppresses_tool_declarations(...):
    # Even if extractor_mode would be TOOL_USE, aux task forces no tools
    ...
```

- [ ] **Step 2: Implement signature extension**

```python
async def build(
    self,
    player_input: str,
    campaign_id: CampaignId,
    *,
    mechanics_results: list[MechanicsResult] | None = None,
    extra: dict | None = None,
    extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
    auxiliary_task: AuxiliaryTask | None = None,
    branch_id: str,
    pc_ref: str,
    turn_id: str,
) -> AssembledPrompt:
    if auxiliary_task is not None:
        return await self._build_auxiliary(
            player_input, campaign_id, auxiliary_task,
            branch_id=branch_id, pc_ref=pc_ref, turn_id=turn_id,
        )
    # ... existing canonical path ...
```

`_build_auxiliary` reads the per-task budget plan from `auxiliary/budgets.py`:

```python
# backend/src/grimoire/auxiliary/budgets.py
@dataclass(frozen=True, slots=True)
class TaskBudget:
    system_prompt_template: str            # filename in prompts/
    include_active_pc_card: bool
    include_scene_header: bool
    voice_target_resolver: Callable
    recent_posts_count: int


def budget_for(kind: TaskKind) -> TaskBudget:
    return {
        TaskKind.IMPERSONATE_PC: TaskBudget(
            "impersonate_pc.j2", True, True, _voice_active_pc_plus_present, recent_posts_count=6,
        ),
        TaskKind.REWRITE_POST: TaskBudget(
            "rewrite_post.j2", True, True, _voice_original_speakers, recent_posts_count=4,
        ),
        TaskKind.CONTINUE_AS: TaskBudget(
            "continue_as.j2", True, True, _voice_target_npc, recent_posts_count=3,
        ),
        TaskKind.WHAT_WOULD_X_SAY: TaskBudget(
            "what_would_x_say.j2", False, True, _voice_target_npc, recent_posts_count=2,
        ),
        TaskKind.BRAINSTORM:   TaskBudget("brainstorm.j2", False, False, _no_voices, recent_posts_count=0),
        TaskKind.EDIT_PROSE:   TaskBudget("edit_prose.j2", False, False, _no_voices, recent_posts_count=0),
        TaskKind.TRANSLATE:    TaskBudget("translate.j2", False, False, _no_voices, recent_posts_count=0),
    }[kind]
```

`_build_auxiliary` constructs the prompt directly from the budget plan, completely bypassing the canonical tier-pack pipeline. Tools and tracker instructions never appear.

- [ ] **Step 3-N: Tests PASS, commit, merge B.**

---

# Branch C — Auxiliary runner (orchestrator side)

### Task C1: `run_auxiliary_task`

**Files:**
- Create: `backend/src/grimoire/orchestrator/auxiliary_runner.py`.
- Modify: `backend/src/grimoire/orchestrator/service.py` — add `run_auxiliary_task` + `_inflight_aux: dict[str, AuxiliaryResult]`.
- Test: `backend/tests/auxiliary/test_runner.py`.

- [ ] **Step 1: Failing tests** — each TaskKind runs against a fixture; assert no state mutation, no scene file mutation, no extractor invocation.

```python
async def test_brainstorm_produces_text_no_state_change(orchestrator, seeded_state, snapshot_before):
    result = await orchestrator.run_auxiliary_task(
        campaign_id=seeded_state.campaign_id,
        task=AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="ideas for next scene"),
    )
    assert result.text  # non-empty
    snapshot_after = await snapshot_campaign_state(seeded_state.campaign_id)
    assert snapshot_before == snapshot_after  # nothing changed


async def test_impersonate_pc_returns_pending_submit_action(...):
    result = await orchestrator.run_auxiliary_task(...)
    assert result.pending_commit_action == CommitAction.SUBMIT_POST


async def test_concurrent_aux_tasks_demuxed_by_result_id(...):
    # Two parallel tasks → two result_ids in _inflight_aux
    ...


async def test_cancel_via_discard_clears_inflight(orchestrator, ...):
    result = await orchestrator.run_auxiliary_task(...)
    await orchestrator.discard_auxiliary(result.id)
    assert result.id not in orchestrator._inflight_aux
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/orchestrator/auxiliary_runner.py
async def run_auxiliary_task(
    orchestrator,
    *,
    campaign_id: str,
    task: AuxiliaryTask,
    on_token: Callable[[str], Awaitable[None]] | None = None,
) -> AuxiliaryResult:
    result_id = f"ar_{uuid7()}"
    # Build context (suppression matrix applied via builder)
    prompt = await orchestrator.context_builder.build(
        player_input=_synthesize_input(task),
        campaign_id=campaign_id,
        auxiliary_task=task,
        extractor_mode=ExtractionMode.NONE,
        branch_id=f"{campaign_id}:main",
        pc_ref=await orchestrator.characters.active_pc(campaign_id),
        turn_id="aux-noturn",
    )
    # Route through LLM gateway with per-task model
    route = orchestrator.llm_gateway.resolve(f"auxiliary.{task.kind.value}")
    text = ""
    async for chunk in orchestrator.llm_gateway.stream(prompt, route=route):
        text += chunk
        if on_token is not None:
            await on_token(chunk)
    result = AuxiliaryResult(
        id=result_id, task=task, text=text, completed_at=_now(),
        model_used=route.model, tokens=_count_tokens(text),
        pending_commit_action=commit_action_for(task.kind),
        warnings=[],
    )
    orchestrator._inflight_aux[result_id] = result
    await orchestrator.events.emit_aux_complete(campaign_id, result_id, result.model_used, result.tokens)
    return result
```

Plus `discard_auxiliary(result_id)` that pops from `_inflight_aux`.

- [ ] **Step 3-N: Tests PASS, commit, merge C.**

---

# Branch D — Accept dispatch

### Task D1: Per-task-kind accept

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py` — add `accept_auxiliary(result_id, edited_text=None)`.
- Test: `backend/tests/auxiliary/test_accept_dispatch.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_accept_impersonate_pc_submits_canonical_turn(orchestrator, scene_manager, seeded_state):
    aux = AuxiliaryResult(id="ar_1", task=AuxiliaryTask(kind=TaskKind.IMPERSONATE_PC),
                          text="The young man bowed and offered his hand.",
                          pending_commit_action=CommitAction.SUBMIT_POST, ...)
    orchestrator._inflight_aux["ar_1"] = aux
    await orchestrator.accept_auxiliary(seeded_state.campaign_id, "ar_1")
    scene = await scene_manager.get_current_scene(seeded_state.campaign_id)
    last_post = scene.posts[-1]
    assert last_post.author_kind == "user"  # PC-authored
    assert "bowed" in last_post.body


async def test_accept_rewrite_post_swaps_primary(orchestrator, scene_manager, seeded_state):
    aux = AuxiliaryResult(
        id="ar_2",
        task=AuxiliaryTask(kind=TaskKind.REWRITE_POST, target_post_id=seeded_state.posts[-1].id),
        text="New rewritten prose.",
        pending_commit_action=CommitAction.REPLACE_POST,
        ...,
    )
    orchestrator._inflight_aux["ar_2"] = aux
    await orchestrator.accept_auxiliary(seeded_state.campaign_id, "ar_2")
    scene = await scene_manager.get_current_scene(seeded_state.campaign_id)
    post = next(p for p in scene.posts if p.id == seeded_state.posts[-1].id)
    primary_alt = next(a for a in post.alternates if a.id == post.primary_alternate_id)
    assert "New rewritten prose." in primary_alt.text


async def test_accept_continue_as_appends_npc_post(...):
    ...


async def test_accept_copy_action_just_returns_text(orchestrator, ...):
    # COPY action: no state change, returns the text for clipboard
    ...


async def test_accept_replace_draft_returns_text(...):
    ...


async def test_double_accept_409(...):
    # second accept → 409 AUX_ALREADY_COMMITTED
    ...
```

- [ ] **Step 2: Implement dispatch**

```python
async def accept_auxiliary(
    self,
    campaign_id: str,
    result_id: str,
    *,
    edited_text: str | None = None,
) -> AcceptResult:
    aux = self._inflight_aux.pop(result_id, None)
    if aux is None:
        raise AuxiliaryNotFound(result_id)
    text = edited_text if edited_text is not None else aux.text
    if aux.pending_commit_action == CommitAction.SUBMIT_POST:
        return await self.submit_post(campaign_id=campaign_id, player_input=text)
    if aux.pending_commit_action == CommitAction.REPLACE_POST:
        # Run extraction on the new text against the target post's context
        post_id = aux.task.target_post_id
        deltas = await self._extract_for_rewrite(campaign_id, post_id, text)
        # Build a new alternate via regenerate-style path
        alt_id = await self.scenes.append_alternate(post_id, Alternate(
            id=f"a_{uuid7()}", post_id=post_id, text=text,
            delta_set_id=(new_ds := f"ds_{uuid7()}"),
            author_kind="aux", model=aux.model_used, prompt_hash=None,
            steering_hint=aux.task.edit_instruction,
            tokens=aux.tokens, pinned=False, is_primary=False, created_at=_now(),
        ))
        await self.store.apply_delta_set(
            deltas=deltas, delta_set_id=new_ds,
            campaign_id=campaign_id, branch_id=f"{campaign_id}:main",
            turn_id=(await self.scenes.get_turn_for_post(post_id)).id,
            source="orchestrator:aux-rewrite",
        )
        await self.switch_primary_alternate(
            campaign_id=campaign_id, post_id=post_id, alternate_id=alt_id,
        )
        return AcceptResult(committed=True, cascaded_replace=True, alternate_id=alt_id)
    if aux.pending_commit_action == CommitAction.APPEND_POST:
        return await self.append_npc_post(
            campaign_id=campaign_id, character_ref=aux.task.target_character_ref,
            body=text,
        )
    # COPY or REPLACE_DRAFT — no server-side change
    return AcceptResult(committed=True, text=text)
```

- [ ] **Step 3-N: Tests PASS, commit, merge D.**

---

# Branch E — REST + WS

### Task E1: 10 routes + 3 WS events

**Files:**
- Create: `backend/src/grimoire/api/auxiliary.py`.
- Modify: `backend/src/grimoire/api/stream.py` — add `aux_token` / `aux_complete` / `aux_error` to `_FORWARDED_EVENTS`.
- Test: `backend/tests/api/test_auxiliary_routes.py`.

Routes (7 start endpoints + 2 accept/discard + 1 in-flight):

```
POST   /campaigns/{id}/auxiliary/impersonate-pc       body: {steering_hint?}
POST   /campaigns/{id}/auxiliary/rewrite-post         body: {post_id, edit_instruction}
POST   /campaigns/{id}/auxiliary/continue-as          body: {character_ref, target_post_id, steering_hint?}
POST   /campaigns/{id}/auxiliary/what-would-x-say     body: {character_ref, snippet}
POST   /campaigns/{id}/auxiliary/brainstorm           body: {prompt}
POST   /campaigns/{id}/auxiliary/edit-prose           body: {snippet, edit_instruction}
POST   /campaigns/{id}/auxiliary/translate            body: {snippet, target_language}

POST   /campaigns/{id}/auxiliary/{result_id}/accept   body: {edited_text?}
POST   /campaigns/{id}/auxiliary/{result_id}/discard

GET    /campaigns/{id}/auxiliary/in-flight
```

- [ ] **Step 1: Failing tests per route.**

- [ ] **Step 2-N: Implement + commit + merge E.**

---

# Branch F — Frontend

### Task F1: Auxiliary panels + entry points

**Files:**
- Create: `frontend/src/routes/campaign/Auxiliary/AuxPanel.tsx`, `AuxBrainstormPanel.tsx`.
- Modify: `frontend/src/routes/campaign/PostItem.tsx` — per-post menu items.
- Modify: `frontend/src/routes/campaign/Composer.tsx` — "Suggest a post" + "Polish" buttons.
- Modify: `frontend/src/routes/campaign/SideHud/` — in-flight indicator.
- Create: `frontend/src/api/auxiliary.ts`.

- [ ] **Step 1: Failing component tests.**

- [ ] **Step 2-N: Implement** — panel with dotted border, muted bg, Accept/Discard/Try again/Edit/Copy bar.

- [ ] **Step end: commit + merge F.**

---

# Integration check

- [ ] **Step end1: Full suite + frontend tests.**
- [ ] **Step end2: Manual smoke** — run each TaskKind once; assert no state mutation; accept one of each; verify state changes appropriately.
- [ ] **Step end3: COMPLETED doc + delete design.**
