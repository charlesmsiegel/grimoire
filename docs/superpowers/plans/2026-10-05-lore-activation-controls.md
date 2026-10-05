# Lore Activation Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** World-info entries activate under author-controlled rules (secondary-key logic, per-entry scan depth, capped recursion, transcript-derived sticky/cooldown, budget priority). Items, groups and creatures count as present. `known_by` scopes per-character calls. The inspector says why each entry is in the prompt. Absorb's new-record proposals keep their evidence and can be published to the world.

**Architecture:** One pure engine, `store/context/activation.py`, replaces the body of `world_state.activate`. It is fed per-post transcript text and parsed per-entry controls (`store/lore_fields.py`), and returns hits with structured reasons. The packer learns one optional per-section `shed` hook. The rows carry the reasons to the inspector. Absorb changes ride the existing commit journal and the existing `store/provenance.py`.

**Tech Stack:** Python 3.11 / FastAPI / pydantic v1+v2-agnostic models, pytest; React + TypeScript, vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-10-05-lore-activation-controls-design.md`, the source of every rule below. Read it first. Section references (§n) are to the spec.

## Global Constraints

- Absent new fields + `lore_recursion_depth` 0 ⇒ narrator prompt **byte-identical** to before (§1). The frozen-campaign `snapshot.json` is regenerated only if it moves, with the move explained in the commit.
- Field bounds (§3): `scan_depth` 0–100, `sticky` 0–50, `cooldown` 0–50, `priority` 0–1000 default 100, `lore_recursion_depth` 0–3 default 0. An edit outside a bound is a 400, never clamped. Adopt clamps imported values into bounds.
- `key_logic` ∈ `and_any` (default) · `and_all` · `not_any` · `not_all`. `recursion` ∈ `both` (default) · `pulled_only` · `pulls_only` · `none`. `keep` ∈ `true` | absent. `known_by` refs are `characters:<id>` / `pcs:<id>` only.
- Reasons and citations never reach a prompt (§2, §9.3).
- Backend rules from CLAUDE.md:
  - imports at module scope;
  - inside `store/`, cross-package imports bind submodules;
  - pydantic models are plain `BaseModel` fields, dumped via `routes.common._dump`;
  - every store write goes through existing writers;
  - a new store module that mutates campaign state is classified in `store/locks.py`;
  - no new LLM call site.
- Lint gates are ratcheted (`lint-baselines/`). New code adds no findings. If a task resolves one, run `make baseline` and commit the smaller file with it.
- Test fixtures use only the placeholder names Seraphine, Mara, Winifred, Realm, Saltmarch. Common nouns like "lantern" are fine; no new proper names.
- Run vitest from `frontend/` (`make check-web` or `cd frontend && npx vitest run <file>`). Backend: `make check-py`, or `cd backend && PYTHONPATH=src .venv/bin/python -m pytest <file> -q`.

## Review Focus

1. **Hand-edited garbage in a new field** (`priority: high`, `sticky: -3`, `key_logic: maybe`): activation treats it as absent and never raises; the editor refuses it on save. Tested in Task 1 (`parse` leniency) and Task 7 (engine with a garbage-valued entry).
2. **Keys with punctuation or non-ASCII** (`Saltmarch's`, `C++`, `Café`): per-post bitmap matching must agree with today's joined-text `keyword_hit`. Tested in Task 6 by checking equivalence over a table of keys and posts.
3. **A scene with no posts yet (opener) or scan depth 0**: activation runs on the seed alone, timed effects start "ready", and nothing indexes out of range. Tested in Task 6.
4. **A lore body containing macros (`{{user}}`, `{{random:a,b}}`)** with and without shedding: the prompt is byte-identical with no budget, and shedding never re-draws a macro. Tested in Task 10.
5. **An inspector capture recorded before this change** (rows without `entries`): it still renders, and the prompt-log reader still accepts it. Tested in Task 11 (frontend) and Task 10 (`prompt_log` round-trip of a row with `entries`).

---

## Slice 1 — fields, audit, adopt

### Task 1: `lore_fields` — the field catalog, lenient parse, strict validation

**Files:**
- Create: `backend/src/grimoire/store/lore_fields.py`
- Test: `backend/tests/test_lore_fields.py`

**Interfaces:**
- Produces:
  ```python
  FIELD_KEYS: tuple[str, ...] = ("secondary_keys", "key_logic", "scan_depth", "sticky",
      "cooldown", "priority", "keep", "recursion", "known_by")
  KEY_LOGIC = ("and_any", "and_all", "not_any", "not_all")
  RECURSION = ("both", "pulled_only", "pulls_only", "none")
  BOUNDS: dict[str, tuple[int, int]]  # scan_depth (0,100), sticky (0,50), cooldown (0,50), priority (0,1000)
  DEFAULT_PRIORITY = 100
  KNOWN_BY_KINDS = ("characters", "pcs")
  @dataclass(frozen=True)
  class Controls:
      secondary_keys: tuple[str, ...] = (); key_logic: str = "and_any"
      scan_depth: int | None = None; sticky: int = 0; cooldown: int = 0
      priority: int = DEFAULT_PRIORITY; keep: bool = False
      recursion: str = "both"; known_by: tuple[str, ...] = ()
      def pulled_ok(self) -> bool   # recursion in (both, pulled_only)
      def pulls_ok(self) -> bool    # recursion in (both, pulls_only)
      def timed(self) -> bool       # sticky or cooldown > 0
  def parse(meta: Mapping[str, object]) -> Controls          # lenient: unparseable/out-of-bound -> default
  def invalid(fields: Mapping[str, object]) -> list[str]      # strict: sorted keys whose value is bad; "" is valid (clears)
  def split_list(value: object) -> tuple[str, ...]            # comma split, strip, drop empties, keep order, dedupe
  ```

- [ ] **Step 1: Write failing tests** in `test_lore_fields.py`:
  - `test_parse_defaults_when_absent`: `parse({}) == Controls()`.
  - `test_parse_reads_every_field`: meta `{"secondary_keys": "tide, harbour", "key_logic": "not_any", "scan_depth": "3", "sticky": "2", "cooldown": "4", "priority": "250", "keep": "true", "recursion": "pulls_only", "known_by": "characters:mara, pcs:winifred"}` parses to the matching `Controls`.
  - `test_parse_is_lenient`: `parse({"priority": "high", "sticky": "-3", "key_logic": "maybe", "scan_depth": "999", "keep": "yes"})` == `Controls()`, and nothing raises.
  - `test_parse_known_by_drops_non_actor_refs`: `"locations:saltmarch, characters:mara"` gives `("characters:mara",)`.
  - `test_invalid_names_each_bad_key`: `invalid({"priority": "1001", "sticky": "x", "key_logic": "and_any", "recursion": "sideways", "known_by": "locations:saltmarch"}) == ["known_by", "priority", "recursion", "sticky"]`.
  - `test_invalid_accepts_blank_as_clear`: `invalid({k: "" for k in FIELD_KEYS}) == []`.
  - `test_invalid_ignores_unknown_keys`: `invalid({"holder": "x"}) == []`, because the caller routes unknown keys elsewhere.
- [ ] **Step 2:** Run `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_lore_fields.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `lore_fields.py` as a leaf module, importing only `entity_schema.referenceable` for `known_by` ids. `keep` is truthy only for the exact string `true` (case-insensitive, stripped). Ints are parsed with `int(str(v).strip())`; a bool is not an int.
- [ ] **Step 4:** Run the tests again. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lore): activation field catalog with lenient parse and strict validation`.

### Task 2: Accept the activation fields on every world-info entity route

**Files:**
- Modify: `backend/src/grimoire/routes/entities.py` (`_check_fields` :65)
- Test: `backend/tests/test_lore_fields_routes.py`

**Interfaces:**
- Consumes: `lore_fields.FIELD_KEYS`, `lore_fields.invalid`.
- Produces: the four entity create/update routes accept `fields` containing activation keys for any kind in `entities.ENTITY_KINDS`. A 400 `detail` reads `"invalid values for {kind}: {keys}"`, the existing wording.

- [ ] **Step 1: Failing tests:**
  - `test_world_lore_saves_activation_fields`: `POST /worlds/{wid}/lore` with `fields={"priority":"250","known_by":"characters:mara"}`, then `GET` shows `meta.priority == "250"`.
  - `test_campaign_item_saves_activation_fields`: same through `POST /campaigns/{cid}/items`.
  - `test_bad_activation_value_is_400`: `PUT` with `fields={"sticky":"51"}` gives 400, and `"sticky"` is in the detail.
  - `test_blank_clears_a_field`: `PUT` with `fields={"priority":""}` removes `priority` from meta.
- [ ] **Step 2:** Run the tests. Expected: FAIL (`unknown fields for lore: priority`).
- [ ] **Step 3: Implement.** In `_check_fields`, partition `fields` into activation keys and schema keys. Run `entity_schema.invalid_keys/invalid_values` on the schema part, and `lore_fields.invalid` on the activation part. Merge both lists into the existing two 400 messages.
- [ ] **Step 4:** Run the tests, plus `tests/test_routes.py -k entity`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lore): entity routes accept activation fields for every world-info kind`.

### Task 3: Lorebook audit — widen the stash, `adopt`, map new imports

**Files:**
- Modify: `backend/src/grimoire/store/lorebook.py`
- Test: `backend/tests/test_lorebook_store.py` (extend)

**Interfaces:**
- Consumes: `lore_fields.BOUNDS`, `lore_fields.FIELD_KEYS`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class AdoptResult: fields: dict[str, str]; unmapped: tuple[str, ...]
  def adopt(stash: Mapping[str, object]) -> AdoptResult
  def pending_adopt(meta: Mapping[str, object]) -> AdoptResult  # adopt(json st_extensions) minus keys meta already has; empty if no/garbled stash
  ```

- [ ] **Step 1: Failing tests.** Put a hand-authored fixture in the test file covering each row of the §4.1 table in both spellings, at top level and nested under `extensions`. Tests:
  - `test_stash_keeps_previously_lost_fields`: a standalone entry with top-level `sticky:2, cooldown:3, delay:1, ignoreBudget:true, group:"g", vectorized:true` keeps all of them in `st_extensions`.
  - `test_adopt_maps_the_audit_table`, parametrized per row. Examples:
    - `{"keysecondary":["tide"],"selective":True,"selectiveLogic":2}` maps to `{"secondary_keys":"tide","key_logic":"not_any"}`;
    - `{"selective":False,"keysecondary":["tide"]}` maps to `{}`;
    - `{"extensions":{"sticky":2,"cooldown":3}}` maps to `{"sticky":"2","cooldown":"3"}`;
    - `{"priority":5,"order":900}` maps to `{"priority":"5"}`, and `{"order":900}` maps to `{"priority":"900"}`;
    - `{"ignoreBudget":True}` maps to `{"keep":"true"}`;
    - `{"excludeRecursion":True}` maps to `{"recursion":"pulls_only"}`; `{"preventRecursion":True}` maps to `{"recursion":"pulled_only"}`; both map to `{"recursion":"none"}`;
    - `{"scanDepth":None}` maps to `{}`; `{"scan_depth":4}` maps to `{"scan_depth":"4"}`.
  - `test_adopt_clamps_into_bounds`: `{"order":5000,"sticky":99}` maps to `{"priority":"1000","sticky":"50"}`.
  - `test_adopt_reports_unmapped`: `{"probability":50,"position":1,"delay":2}` has `unmapped == ("delay","position","probability")` (sorted).
  - `test_commit_writes_native_fields_and_stash`: importing an entry with `keysecondary` writes `secondary_keys` frontmatter and still writes `st_extensions`.
  - `test_reimport_is_still_a_noop`: the existing dedupe still skips an identical re-import.
  - `test_pending_adopt_skips_fields_the_record_has`.
  - `test_pending_adopt_tolerates_garbled_stash`: `st_extensions: "{not json"` gives an empty result.
- [ ] **Step 2:** Run `tests/test_lorebook_store.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Extend `_ST_EXTENSION_FIELDS` with every spelling the table marks lost: `sticky, cooldown, delay, ignoreBudget, ignore_budget, group, groupOverride, group_override, groupWeight, group_weight, useGroupScoring, use_group_scoring, vectorized, characterFilter, character_filter, triggers, automationId, automation_id`. Write `adopt` with a `_pick(stash, *names)` helper that returns the first present value, checking the top level and then `stash["extensions"]`. In `commit`, merge `adopt(ext).fields` into the `fields` passed to `create_entity`, beside `st_extensions`. Update the module docstring: the stash is now also the adopt source.
- [ ] **Step 4:** Run the tests, plus `tests/test_routes.py -k lorebook`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lorebook): audit table, widened stash, adopt maps ST settings to native fields`.

### Task 4: Adopt routes (per entry and bulk, world and campaign)

**Files:**
- Modify: `backend/src/grimoire/routes/entities.py`; `backend/src/grimoire/routes/models.py` (no body needed; responses are plain dicts)
- Test: `backend/tests/test_lore_adopt_routes.py`

**Interfaces:**
- Consumes: `lorebook.pending_adopt`, `entities.update_entity`, `overlay.update_entity`, `store.undo.journalled`, `locks.campaign_lock`.
- Produces routes:
  - `GET  /worlds/{wid}/{kind}/{eid}/adopt-st` and `GET /campaigns/{cid}/{kind}/{eid}/adopt-st` return `{"fields": {...}, "unmapped": [...]}`.
  - `POST` on the same paths returns `{"applied": {...}}`. It writes only the pending fields and never overwrites an existing key.
  - `POST /worlds/{wid}/adopt-st` and `POST /campaigns/{cid}/adopt-st` return `{"applied": [{"kind","id","fields"}], "skipped": [{"kind","id","reason"}]}`. They iterate `entities.ENTITY_KINDS` over records **held in that root**: `entities.list_entities(root, kind)`, where root is the world root or `overlay.croot_of(cid)`.
  - Campaign writes go under `locks.campaign_lock(cid)` and inside `store.undo.journalled(cid, {"w": "entity", "kind": kind, "id": eid}, kind="lore", ref={"kind": kind, "id": eid}, field="body", label=...)`. This mirrors `_campaign_entity_update`.

- [ ] **Step 1: Failing tests:**
  - `test_preview_lists_pending_fields`.
  - `test_apply_writes_pending_and_keeps_existing`: the record already has `priority: 7`; the stash says order 900; after apply, `priority` is still `7`.
  - `test_bulk_world_apply_reports_applied_and_skipped`: a record with no stash is not listed; a garbled stash is skipped with reason `"unreadable st_extensions"`.
  - `test_bulk_campaign_apply_ignores_inherited_world_records`.
  - `test_apply_on_record_without_stash_is_noop_200`.
- [ ] **Step 2:** Run the tests. Expected: FAIL (404).
- [ ] **Step 3: Implement** the six handlers as `def` (not `async def`), reusing `_check_kind`-style guards from the same file for 404s.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lorebook): adopt imported SillyTavern settings per entry or in bulk`.

### Task 5: Editor — Activation disclosure, sidebar chips, adopt banner

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx`; `frontend/src/api/client.ts`; `frontend/src/api/types.ts`
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: Task 2 fields over the existing `createEntity`/`updateEntity` `fields` payload; Task 4 routes.
- Produces:
  - In `types.ts`: `ACTIVATION_FIELDS: { key: string; label: string; widget: "text" | "number" | "choice" | "bool" | "refs"; min?: number; max?: number; options?: string[] }[]`. It mirrors `lore_fields`, in `FIELD_KEYS` order.
  - In `client.ts`: `previewAdoptSt(scope, kind, id): Promise<{fields: Record<string,string>; unmapped: string[]}>`, `adoptSt(scope, kind, id)`, and `adoptStAll(scope)`.

- [ ] **Step 1: Failing tests** (extend `EntityEditor.test.tsx`; follow the list/detail rule):
  - `"activation settings show as chips in the read-only view"`: an entity with `priority: "250"`, `sticky: "2"` shows `Priority: 250` and `Sticky: 2 posts` in `.detail-sidebar`, with no `textarea`.
  - `"Edit reveals the Activation disclosure and saves changed fields only"`: change priority to 300 and save; `api.updateEntity` is called with `expect.objectContaining({fields: {priority: "300"}})`.
  - `"known_by renders as chips that open the actor"`: the chip click calls the existing `onOpenOwner` handler with `characters:mara`. A ref to a deleted actor renders as a non-clickable `chip missing` with the text `missing: characters:gone`.
  - `"adopt banner applies imported settings"`: `meta.st_extensions` is present and `previewAdoptSt` resolves `{fields:{sticky:"2"},unmapped:["probability"]}`. The banner lists `sticky`. Clicking **Apply** calls `adoptSt`, then reselects the record.
- [ ] **Step 2:** Run `cd frontend && npx vitest run src/components/EntityEditor.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `select()` loads `ACTIVATION_FIELDS` keys into the existing `fields`/`loadedFields` state, so `changedFields` diffs them for free.
  - The form renders a `<details className="activation-settings">` titled **Activation** containing the inputs. `refs` uses `refOptions(scope, ["characters","pcs"])` with checkboxes like `ownerPicker`.
  - The sidebar adds one `.side-section` (`<h4>Activation</h4>`) with `chip on` spans, plus `known_by` as `chip owner-chip` buttons.
  - The banner shows in view mode when `previewAdoptSt` returns non-empty `fields`.
  - The world/campaign lore section header gets an **Apply imported settings to all** button calling `adoptStAll`. It sits beside the existing `+ New` in the same rail header.
- [ ] **Step 4:** Run the tests. Expected: PASS. Then run `npm run typecheck` from `frontend/`.
- [ ] **Step 5:** Commit `feat(lore): activation settings and adopt banner in the entity editor`.

---

## Slice 2 — the activation engine

### Task 6: `activation.py` — bitmaps, windows, key logic, timed replay

**Files:**
- Create: `backend/src/grimoire/store/context/activation.py`
- Test: `backend/tests/test_lore_activation.py`

**Interfaces:**
- Consumes: `world_state.keyword_hit` semantics (re-implemented per post with compiled patterns: `re.compile(rf"\b{re.escape(k)}\b", re.IGNORECASE)`); `lore_fields.Controls`.
- Produces:
  ```python
  class KeyIndex:
      def __init__(self, posts: Sequence[tuple[int, str]], seed: str) -> None
      @property
      def n(self) -> int                        # number of slots = len(posts) + (1 if seed else 0)
      def hits(self, key: str) -> list[bool]    # per slot, memoised per key; seed is the last slot
      def newest(self, keys, upto: int, depth: int | None) -> tuple[str, int] | None
          # newest (key, slot) hit among the last `depth` posts before boundary `upto`, seed slot included
          # when upto == n; None = no hit
      def post_index(self, slot: int) -> int | None   # transcript index of a post slot; None for the seed slot
  def direct_match(entry: dict, idx: KeyIndex, upto: int, depth: int) -> dict | None
      # applies key_logic; returns {"key", "secondary", "slot"} or None
  def timed_state(entry: dict, idx: KeyIndex, depth: int) -> dict
      # replays boundaries 0..idx.n (§5.3); returns {"state": "ready"|"active"|"cooling",
      #  "fresh": bool, "from_slot": int|None, "remaining": int}
  ```
  An entry dict here is `_world_info`'s shape plus `"controls": Controls` (Task 8 adds it).

- [ ] **Step 1: Failing tests:**
  - `test_bitmap_matches_joined_text` (Review Focus 2): for keys `["Saltmarch's", "C++", "Café", "harbour"]` and posts `["at Saltmarch's gate", "c++ notes", "CAFÉ", "harbourmaster"]`, every depth 0–4 and seed `""`/`"the harbour"`, the window verdict (`newest(...) is not None`) equals `keyword_hit(keys, "\n".join(window_posts + [seed]).strip())`.
  - `test_key_logic_four_operators`, parametrized:
    - primary `tide`; secondary `["storm","ship"]`; window text `"tide and storm"`;
    - expected: `and_any` → hit, `and_all` → none, `not_any` → none, `not_all` → hit;
    - and with empty secondary every operator equals the primary alone.
  - `test_per_entry_scan_depth_overrides_global`: depth 1 misses a key in post n-2; depth 3 finds it.
  - `test_scan_depth_zero_sees_only_seed` (Review Focus 3); `test_empty_scene_with_seed` (no posts, seed matches; `timed_state` is ready → active fresh, no IndexError).
  - `test_sticky_carries_then_expires`: sticky 2, key only in post 3 of 8 (depth 1). Boundary 4 is fresh, boundaries 5–6 are carried with remaining 2 then 1, and boundary 7 is ready.
  - `test_cooldown_blocks_a_match`: sticky 0, cooldown 2, key in posts 3 and 4 (depth 1). At boundary 5 the state is `cooling`, remaining 1, even though the key matches.
  - `test_replay_is_exact_from_scene_start`: activation at post 0 with sticky 1 / cooldown 3 holds back a match at post 3. This proves no window horizon.
  - `test_director_note_counts_as_a_post`: posts include a `[Director]` line whose text matches; it starts sticky like any post.
- [ ] **Step 2:** Run `tests/test_lore_activation.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** A boundary `b` covers slots `[0, b)`, and the current boundary is `n`. An entry's window at boundary `b` is slots `[max(0, b - depth), b)` among the post slots, plus the seed slot only when `b == n`. The machine, per boundary from 1 to n:
  - In `cooling`, decrement and become `ready` at 0.
  - In `active` with `carry > 0`, decrement.
  - A direct match while `ready` makes the state `active` with `carry = sticky`, `from_slot = slot`, `fresh = True`.
  - An `active` state whose carry is exhausted and has no new match becomes `cooling` with `cooldown`, or `ready` if cooldown is 0.
  - A fresh match while `active` refreshes `from_slot` and `carry`.
  - Keyless entries are never timed.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lore): activation engine — per-post bitmaps, key logic, derived sticky/cooldown`.

### Task 7: `activation.run` — levels, recursion, presence, reasons, recall

**Files:**
- Modify: `backend/src/grimoire/store/context/activation.py`
- Test: `backend/tests/test_lore_activation.py` (extend)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class Hit:  ref: str; entry: dict; level: int; reason: dict; direct: bool; age: int
  @dataclass(frozen=True)
  class Held: ref: str; entry: dict; reason: dict
  @dataclass(frozen=True)
  class Result: keyword: list[Hit]; recalled: list[Hit]; held_back: list[Held]; present: dict[str, dict]
  def structural_presence(entries: list[dict], base: Mapping[str, dict],
                          current_location: str | None) -> dict[str, dict]
  def run(entries: list[dict], posts: Sequence[tuple[int, str]], seed: str,
          base_present: Mapping[str, dict], *, scan_depth: int, recursion_depth: int,
          current_location: str | None, pinned_refs: frozenset = frozenset(),
          excluded_refs: frozenset = frozenset(),
          recall: Callable[[list[dict], str], list[dict]] | None = None,
          recall_text: str = "") -> Result
  ```
  Reason dicts (JSON-safe, the inspector's contract):
  - `{"type":"key","key":str,"secondary":str|None,"post":int|None,"seed":bool}`
  - `{"type":"keyless"}`, `{"type":"pinned"}`
  - `{"type":"sticky","from_post":int|None,"remaining":int}`
  - `{"type":"recursion","via":ref,"key":str}`
  - `{"type":"recall","score":float|None}`
  - Any of these gains `"owner": ref, "owner_presence": {...}` when an owner gate was satisfied.
  - Held: `{"type":"cooldown","remaining":int}`.
  - Presence reasons: `{"type":"cast"|"current_location"|"held_by"|"led_by"|"headquarters"|"habitat"|"activated","via":ref|None}`.
  - `age` (§6): the post index of the newest hit; `idx.n` for a seed-only hit; `from_post` for sticky; `-1` for keyless, pinned, recursion and presence-unlock.
  - Entry structural refs are read from `entry["refs"] = {"holder": [...], "leader": [...], "headquarters": [...], "habitat": [...]}` (Task 8 fills this in).

- [ ] **Step 1: Failing tests:**
  - `test_order_is_gm_only_exclude_pin_owner_keys`: one entry per gate, checking the existing `activate` docstring's guarantees (gm-only beats a pin; an exclude beats recall; a pin beats the owner gate).
  - `test_activated_preserves_prompt_order`: the hit order equals input order regardless of level.
  - `test_recursion_depth_cap_and_cycle`: A's body names B and B's body names A, both keyed. A is direct. With R=0 only A activates. With R=1, B activates via A (reason `recursion`, `via` A's ref). Nothing loops.
  - `test_recursion_flags`: one case per `recursion` value, covering both pulling and being pulled.
  - `test_cooling_entry_cannot_be_pulled`.
  - `test_presence_by_activation_unlocks_owned_lore_without_recursion`: R=0. The group entry `groups:guild` is keyed and hit, and the lore is owned by `groups:guild`. The lore activates at level 1 and its reason carries `owner_presence.type == "activated"`.
  - `test_structural_presence_fixed_point`: Mara leads `groups:guild`, and `items:lantern` has holder `groups:guild`. The result includes the item with `held_by` via the group. Shuffle the input order 10 times with a seeded RNG; the result is identical every time.
  - `test_absent_owner_beats_sticky`.
  - `test_recall_gets_only_unactivated_gate_passing_keyed_entries` and `test_recall_hits_add_no_presence`.
  - `test_garbage_controls_behave_as_absent` (Review Focus 1): an entry whose meta was parsed from garbage behaves like a plain entry.
  - `test_age_values`: one case per age rule.
  - `test_one_search_per_key_per_post` (§5.4): 200 entries × 3 keys over 300 posts, with timed effects on half. Patch the compiled-pattern `search` with a counter; the window phase issues at most `distinct_keys × slots` searches. Recursion searches are counted separately and stay ≤ `R × activated × candidate keys`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement** `run` exactly as spec §5.2's level pseudocode, with `P = structural_presence(...)` first.
  - Level n ≥ 1 considers only entries not yet activated and not held back.
  - Recursion text is each puller's `body`. `via` is the first puller in input order whose body satisfies the pulled entry's key logic.
  - Stop when a level adds nothing or after `recursion_depth + 1` levels.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lore): activation levels, recursion, structural and activated presence, reasons`.

### Task 8: Wire the engine into context assembly; `lore_recursion_depth` setting

**Files:**
- Modify:
  - `backend/src/grimoire/store/context/world_state.py` (`activate` :55, `_world_info` :151)
  - `backend/src/grimoire/store/context/assemble.py` (:129–171, :284–355, return dict :450)
  - `backend/src/grimoire/store/config.py` (`_CONFIG_KEYS` :199, defaults :231)
  - `backend/src/grimoire/routes/config.py` (`_public_config` :89)
  - `backend/src/grimoire/routes/models.py` (`ConfigUpdate` :26)
  - `frontend/src/routes/ConfigView.tsx` (context section :122, row near :860)
  - `frontend/src/api/types.ts` (:113, :188)
- Test: `backend/tests/test_lore_activation_context.py`; `backend/tests/test_routes.py` (config key round-trip); `frontend/src/routes/ConfigView.test.tsx` (if present; otherwise extend the existing config test that covers `context_scan_depth`)

**Interfaces:**
- Consumes: `activation.run`, `lore_fields.parse`.
- Produces:
  - `config.lore_recursion_depth() -> int`, which is `min(_count("lore_recursion_depth", DEFAULT_LORE_RECURSION_DEPTH), 3)` with `DEFAULT_LORE_RECURSION_DEPTH = "0"`.
  - `world_state._world_info(cid, posts, seed, *, exclude, present, pinned_refs, excluded_refs, scan_depth, recursion_depth, current_location, recall_text) -> tuple[list[dict], list[dict], activation.Result]`. Each entry gains `"controls"` and `"refs"`; refs come from `entity_schema.parse_refs` on `holder/leader/headquarters/habitat`.
  - `world_state.activate(...)` keeps its exact signature and return. It wraps `run` with `posts=[(0, recent_text)]`, `seed=""`, `scan_depth=1`, `recursion_depth=0`, and parses controls when absent.
  - `_assemble`'s return dict gains `"wi_result": activation.Result`.
  - `posts` for a narrator call are `[(i, m["content"]) for i, m in enumerate(history)]` from the full transcript.
  - For an actor call, `posts` are filtered to the message objects `actor.observed_history` returned: identity, `id(m)`, because it returns elements of its input. This keeps their original transcript indices.

- [ ] **Step 1: Failing tests:**
  - `test_prompt_is_byte_identical_without_new_fields`:
    - Build a campaign with keyed, keyless, owned, secret and gm-only lore.
    - Capture `build_messages` before wiring by asserting against a literal expected-string fixture generated from the pre-change code. Generate it once with `git stash`, then commit the fixture with the test.
  - `test_recursion_setting_round_trips_and_caps`: `PUT /config {"lore_recursion_depth":"9"}` then `config.lore_recursion_depth() == 3`.
  - `test_secondary_keys_change_activation_end_to_end`: a `not_any` entry is absent from `build_messages` when its secondary word is in the last post.
  - `test_actor_call_uses_observed_post_indices`: an NPC that arrived at post 4 gets a reason `post` index ≥ 4, never a renumbered one.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Keep `recent_text` computed exactly as today for mechanics, archive and recall. `_world_info` receives `recall_text=recent_text`.
  - Thread `config.scan_depth()` and `config.lore_recursion_depth()`.
  - Add the setting row in ConfigView, beside scan depth: `<NumField id="cfg-lore-recursion-depth" label="Lore recursion depth" unit="levels" placeholder="0" caption="0 = off, max 3" …>`.
  - Add the key to `_public_config` and `ConfigUpdate`. The two tests in `test_routes.py` that hold `_public_config` to `ConfigUpdate` must still pass.
- [ ] **Step 4:** Run `tests/test_lore_activation_context.py tests/test_context.py tests/test_pins_context.py tests/test_context_semantic.py tests/test_actor_context.py tests/test_frozen_campaign.py -q`. Expected: PASS, with the frozen snapshot unmoved.
- [ ] **Step 5:** Commit `feat(lore): context assembly runs the activation engine; lore_recursion_depth setting`.

---

## Slice 3 — presence and knowledge

### Task 9: Full-scene presence on NPC calls; `known_by`; owners picker widened

**Files:**
- Modify:
  - `backend/src/grimoire/store/context/actor.py` (`known_entries` :39)
  - `backend/src/grimoire/store/context/assemble.py` (presence build :327; current-setting check :320; narrowing :162–171)
  - `frontend/src/api/loreOwners.ts` (:108)
  - `evals/cases.py` (owned-lore containment case :381)
- Test: `backend/tests/test_actor_context.py` (extend); `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Produces: `actor.knows(entry: dict, actor_ref: str | None) -> bool` (§8). `known_entries(entries, actor_ref)` is reimplemented as a filter over `knows` with the same signature. `entry["controls"].known_by` is read when present; otherwise it is parsed from `entry.get("known_by")`.
- `present` is built from the exclude-filtered cast **before** `cast = selected` (save `scene_cast = cast` before narrowing). `present` is passed only to `_world_info`; confirm with `grep -n "present" assemble.py` that no other consumer exists.

- [ ] **Step 1: Failing tests:**
  - `test_known_by_reaches_named_actor_and_narrator_only`:
    - Lore has `owners: characters:mara`, `known_by: characters:winifred`, `secrecy: secret`, keyless.
    - Mara and Winifred are both present.
    - The narrator prompt and Winifred's call contain it; Mara's call does not.
  - `test_location_owned_public_lore_reaches_npc_there` (§8.2).
  - `test_character_owned_lore_still_private_to_owner`.
  - `test_known_by_deleted_actor_reaches_nobody_and_does_not_raise`.
  - `test_item_held_by_present_npc_unlocks_owned_lore`.
  - Frontend `"owners picker offers items, groups and creatures"`.
  - Evals: extend the owned-lore case with a `known_by` variant (§11). `pytest backend -k evals` runs it offline.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement** `knows` per §8.1/§8.2. In `loreOwners.ts`, change the line to `refOptions(scope, ["characters","pcs","locations","items","groups","creatures"])`.
- [ ] **Step 4:** Run the tests plus `tests/test_actor_scoped_skip.py tests/test_frozen_campaign.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lore): known_by, full-scene presence on NPC calls, object owners`.

---

## Slice 4 — shedding

### Task 10: Per-entry shedding in the packer; per-entry macro expansion; reasons on rows

**Files:**
- Modify:
  - `backend/src/grimoire/store/context/pack.py` (`pack` :130)
  - `backend/src/grimoire/store/context/assemble.py` (`_render_sections` :872; `_breakdown` :1265)
  - `backend/src/grimoire/store/prompt_log.py` (only if a row with extra keys fails `_well_formed`)
- Test: `backend/tests/test_lore_shedding.py`; `backend/tests/test_prompt_log_store.py` (extend)

**Interfaces:**
- Consumes: `Result` from `a["wi_result"]`.
- Produces:
  - Section dicts may carry `"shed": {"units": list[{"ref","priority","keep","pinned","direct","age","pos"}], "render": Callable[[frozenset[str]], str]}`.
  - `pack.pack` output sections gain `"shed_refs": list[str]` when any unit was shed. A section without `shed` packs exactly as before.
  - `_breakdown` rows for `world_info` / `recalled_lore` gain `"entries": [{"ref","name","kind","secrecy","priority","keep","level","reason","shed": bool}]`. `world_info` also gains `"held_back": [{"ref","name","reason"}]`.
  - The `shed` callable is stripped from the row, which is built from explicit keys as today.

- [ ] **Step 1: Failing tests:**
  - `test_section_without_shed_packs_as_before`: the `pack` output is unchanged for an existing fixture.
  - `test_sheds_lowest_priority_first_then_ties` (§6): priorities 50/100/100/100 with a recursion hit, an old match, a new match and a seed match. The shed order is asserted exactly, including keyless (age -1) and seed (age n) at equal priority.
  - `test_keep_and_pinned_never_shed_section_kept`.
  - `test_all_shed_marks_dropped`.
  - `test_secret_sheds_with_its_entry`.
  - `test_unbounded_budget_untouched`.
  - `test_macros_expand_once_per_entry_and_identically` (Review Focus 4): with a seeded RNG, a world_info body containing `{{random:a,b,c}}` renders byte-identical to the pre-change render with no budget, and shedding another entry does not change its expanded text.
  - `test_prompt_log_round_trips_rows_with_entries` (Review Focus 5): `record` then `read_entry` returns the `entries` list. An old-format row with no `entries` still reads.
  - `test_reasons_never_reach_the_prompt`: every reason type is present; none of `"key '"`, `"pulled in"`, `"owner present"` or any reason value string appears in `build_messages` output.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - In `_render_sections`, for `section.id == "world_info"`, expand the heading first, as today.
  - Then expand each entry body with `macros.expand_macros` in template order: public then secret, prompt order within each.
  - Render the template with the expanded lists and skip the section-level body expansion.
  - Attach `shed` whose `render(kept)` re-renders the template from the already-expanded bodies of `kept` refs, so no macro is re-drawn.
  - In `pack`, when a tier's candidate carries `shed` and is not pinned:
    - shed units in key `(priority, direct, age, -pos)` ascending, skipping `keep`/pinned units;
    - after each, set `text = render(kept)` and re-measure `system_cost()`;
    - stop when it fits;
    - if only protected units remain, leave the section;
    - if none remain, restore the original text and mark `dropped`.
  - `recalled_lore` keeps whole-section dropping.
- [ ] **Step 4:** Run the tests plus `tests/test_pins_context.py tests/test_context.py tests/test_prompt_log_store.py tests/test_context_compare.py tests/test_frozen_campaign.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(lore): shed world info per entry under budget; reasons on inspector rows`.

---

## Slice 5 — inspector

### Task 11: Show each entry's reason in the context inspector

**Files:**
- Create: `frontend/src/components/loreReasons.ts`; `frontend/src/components/loreReasons.test.ts`
- Modify: `frontend/src/components/ContextBreakdown.tsx`; `frontend/src/api/types.ts` (`ContextSection` :1051)
- Test: `frontend/src/components/SceneInspector.test.tsx` (extend)

**Interfaces:**
- Produces:
  - Types: `LoreReason` (the union of the Task 7 reason dicts), `LoreEntryRow`, `HeldEntryRow`.
  - `ContextSection.entries?: LoreEntryRow[]` and `held_back?: HeldEntryRow[]`.
  - `describeReason(reason: LoreReason, names: Record<string,string>): string`.

- [ ] **Step 1: Failing tests:**
  - `loreReasons.test.ts`: one assertion per reason type with the spec §10 copy, verbatim:
    - `"key 'Saltmarch' in post #41"`
    - `"pulled in by Realm charter"`
    - `"always on"`
    - `"pinned"`
    - `"owner present: the lantern (held by Mara)"`
    - `"sticky — 2 posts left (from post #38)"`
    - `"recalled (similarity 0.52)"`
    - `"on cooldown — 3 posts"`
    - and a seed match: `"key 'Saltmarch' in this turn's note"`.
  - `SceneInspector.test.tsx` `"world info lists each entry with its reason"`: expanding World info shows one line per entry; a shed entry carries `.shed` and the text `shed: budget`.
  - `"a capture without entries still renders"` (Review Focus 5).
- [ ] **Step 2:** Run `cd frontend && npx vitest run src/components/loreReasons.test.ts src/components/SceneInspector.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** Below the section `<pre>`, render `<ul className="ctx-entries">` with `<li>`. Each has `<span className="chip on">{name}</span> {describeReason(...)}`, and `className="shed"` plus `shed: budget` when shed. Held-back entries go in a second list headed "Held back". There is no navigation (spec §10).
- [ ] **Step 4:** Run the tests and `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(inspector): per-entry world-info reasons`.

---

## Slice 6 — absorb

### Task 12: `new_lore` kinds, citations for created records

**Files:**
- Modify:
  - `backend/src/grimoire/store/absorb/parse.py` (:252)
  - `backend/src/grimoire/store/absorb/materializer.py` (:725–754)
  - `backend/src/grimoire/store/absorb/apply.py` (`_apply_one` new_lore :576; provenance step :780)
  - `templates/absorb/system.j2` (:19)
- Test: `backend/tests/test_absorb_store.py` (extend)

**Interfaces:**
- Produces:
  - Parsed `new_lore` rows carry `"kind"` ∈ `("lore","items","groups","creatures")`, defaulting to `"lore"`.
  - A staged `new_lore` edit has `target.kind` = that kind and `payload.kind`.
  - `_apply_one`'s applied outcome gains `"created": {"kind", "id"}` for every `new_*` creation.

- [ ] **Step 1: Failing tests:**
  - `test_parse_new_lore_kind_clamps`: `"items"` stays; `"weapon"` and missing both give `"lore"`.
  - `test_materialize_dedupes_new_lore_per_kind`: an existing item named "Lantern" skips an items proposal named "Lantern" but not a lore one.
  - `test_apply_new_lore_creates_the_proposed_kind` and `test_apply_clamps_a_client_edited_kind`.
  - `test_created_record_citation_is_recorded`: after `apply_edits`, `provenance.read(cid)` has key `"items/<new id>#body"` with the quote.
  - `test_absorb_prompt_asks_for_kind`: the rendered system prompt contains `"kind"` within the `new_lore` line.
- [ ] **Step 2:** Run `tests/test_absorb_store.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Reword `system.j2:19` to: `"new_lore" (list of {"name","body","keys","kind"} — for a new group, item, creature, piece of history, or concept mentioned that isn't a person or a place; "kind" is one of "lore", "items", "groups", "creatures"; same "keys" convention as above)`.
  - In the provenance step, key on `{**e, "target": prior["created"]}` when `prior` has `created`.
- [ ] **Step 4:** Run the tests plus `python scripts/verify_templates.py`, `tests/test_llm_fakes.py` and `tests -k evals`. Expected: PASS. If a cassette matcher fails, update its `system_contains` in `backend/tests/fixtures/llm/` to the reworded text.
- [ ] **Step 5:** Commit `feat(absorb): propose items, groups and creatures; keep created records' citations`.

### Task 13: Publish to the world as a journalled commit step

**Files:**
- Create: `backend/src/grimoire/store/absorb/publish.py`
- Modify:
  - `backend/src/grimoire/routes/scenes.py` (`put_chronicle` :4028, after `apply_edits` :4230, before `commits.record` :4234)
  - `backend/src/grimoire/store/locks.py` (classify `store.absorb.publish` in `DOMAIN_MODULES`)
- Test: `backend/tests/test_absorb_publish.py`

**Interfaces:**
- Consumes: `sync.promote(cid, kind, eid)`, `sync.library_status(cid, kind, eid) -> {"in_library","diverged",...}`, `sync.LibraryMoveError`, and `progress["edits"][str(i)]["created"]` from Task 12.
- Produces: `publish.publish_created(cid: str, edits: list[dict], progress: dict, checkpoint: Callable[[], None] | None) -> tuple[list[dict], list[dict]]`. It returns `(published, failed)`: `published` is `[{"kind","id"}]` and `failed` is `[{"kind","id","reason"}]`. It runs under `locks.campaign_lock(cid)`, which is re-entrant inside `put_chronicle`'s hold. Journal: `progress["publish"][str(i)]` holds `"pending"`, then `{"state":"published"}` or `{"state":"failed","reason"}`, checkpointed after each transition. The `put_chronicle` result dict gains `"published"` and `"publish_failed"`.

- [ ] **Step 1: Failing tests:**
  - `test_world_destination_publishes_after_create`: the world now holds the record.
  - `test_campaign_destination_never_promotes`.
  - `test_precondition_failure_is_reported_not_raised`: the world already has the id, so the result is `publish_failed` with the reason; the save still returns 200.
  - `test_lost_response_retry_replays_published`: same token twice; the second response equals the first, and promote is called once (monkeypatched counter).
  - `test_crash_between_promote_and_outcome_resolves_published`:
    - Monkeypatch `checkpoint` to raise after promote.
    - Retry: `library_status` shows the record in the library and not diverged, so the step is recorded `published` without a second promote.
  - `test_lock_domain_guard_passes`: run `tests/test_lock_domain_guard.py`.
- [ ] **Step 2:** Run `tests/test_absorb_publish.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per spec §9.4. Only edits whose `payload.destination == "world"` and whose slot is `applied` with `created` are published. A slot already `published` or `failed` is reused. A `pending` slot is resolved with `library_status` (`in_library and not diverged` means published), and otherwise promote is called again.
- [ ] **Step 4:** Run the tests plus `tests/test_routes.py -k chronicle tests/test_commits_store.py tests/test_lock_domain_guard.py tests/test_lock_order_guard.py`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(absorb): publish new records to the world as a journalled commit step`.

### Task 14: Review row — keys, kind, destination, publish failures

**Files:**
- Modify:
  - `frontend/src/components/review/AbsorbEditRow.tsx`
  - `frontend/src/components/review/useSceneReview.ts` (save result handling near :674)
  - `frontend/src/api/types.ts` (chronicle save result type)
- Test: `frontend/src/components/review/SceneReview.test.tsx`

**Interfaces:**
- Consumes: the existing `setPayload` (`editPayload(i, patch)`); Task 13's response keys.
- Produces:
  - payload fields `keys: string`, `kind: "lore"|"items"|"groups"|"creatures"` (new_lore only), and `destination: "campaign"|"world"` (new_lore and new_location);
  - `publishFailures` state rendered as a notice in the review's existing save-failure surface.

- [ ] **Step 1: Failing tests:**
  - `"new_lore row edits keys, kind and destination and saves them"`: the PUT body's edit payload includes `{keys:"tide, harbour", kind:"items", destination:"world"}`.
  - `"world destination explains how it is undone"`: the text `undone from the world page` is shown under the control.
  - `"a publish failure is reported after save"`: the save resolves with `publish_failed:[{kind:"lore",id:"x",reason:"the world already has a record named x"}]`, and the notice shows that reason.
- [ ] **Step 2:** Run `cd frontend && npx vitest run src/components/review/SceneReview.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Keys: a text input with `aria-label={`Keys ${e.label}`}`.
  - Kind: a `<select aria-label={`Kind ${e.label}`}>`.
  - Destination: two radios named `dest-${i}`, labelled "Campaign" and "World library".
  - Helper text: "Publishing to the library is undone from the world page (demote), not from Changes."
- [ ] **Step 4:** Run the tests and `npm run typecheck`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(review): edit keys, kind and destination of new records; report publish failures`.

---

### Task 15: Whole-branch gate

- [ ] **Step 1:** Run `make check` (with `PY=backend/.venv/bin/python` if needed). Expected: every target green.
- [ ] **Step 2:** If a ratchet target fails because a finding count **fell**, run `make baseline` and commit `chore: ratchet baselines`. If one **rose**, fix the new finding.
- [ ] **Step 3:** If the frozen-campaign snapshot moved, read the diff. A move is acceptable only from §1's listed intentional changes. Then regenerate with `cd backend && PYTHONPATH=src .venv/bin/python -m tests.fixtures.frozen_campaign.sweep` and commit with the reason.
- [ ] **Step 4:** Update `CLAUDE.md` only if a rule a future agent could break was introduced: the `shed` hook contract in pack, and "activation reasons never reach the prompt". Keep it to one bullet under Working notes, and run `tests/test_docs_guard.py`.
