# Progressive Emergent Characters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make play-derived characters safer and more three-dimensional by preserving evidence, confidence, and open questions, and by seeding an initial dossier when approved.

**Architecture:** Extend the existing absorb `new_characters` payload rather than adding a new record type. The backend parses/stages/applies the added metadata, while the scene review UI exposes it for editing before approval. Approved new characters get a deterministic first dossier from the reviewed metadata and scene context.

**Tech Stack:** Python store/routes/tests, Jinja prompt templates, React/TypeScript scene review UI, Vitest, pytest.

## Global Constraints

- Keep the existing staged-edit review contract.
- Do not create world-level characters from campaign play; emergent characters remain campaign-overlay records.
- Do not require another LLM call for initial dossier seeding.
- Avoid real campaign or character names in tests.

---

### Task 1: Parse and Stage Progressive Metadata

**Files:**
- Modify: `templates/absorb/system.j2`
- Modify: `backend/src/grimoire/store/absorb.py`
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `new_characters` JSON entries from the absorb prompt.
- Produces: staged `new_character` edits with payload keys `evidence`, `confidence`, and `open_questions`.

- [x] **Step 1: Write failing parser/materializer tests**

Add tests proving `parse_output()` preserves the three new fields and `materialize()` defaults missing metadata to `confidence: "thin"` with empty evidence/questions.

- [x] **Step 2: Run focused pytest and confirm failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_parse_output_new_entities backend/tests/test_absorb_store.py::test_materialize_new_character_creates_staged_edit backend/tests/test_absorb_store.py::test_materialize_new_character_without_progressive_metadata_defaults_thin -q`

Expected: FAIL because the metadata fields are not parsed/staged yet.

- [x] **Step 3: Implement prompt, parser, and materializer changes**

Update the absorb prompt to ask for `evidence`, `confidence`, and `open_questions`; parse them as strings; normalize confidence to `thin`, `sketched`, or `established`; include them in `new_character.payload`.

- [x] **Step 4: Run focused pytest and confirm pass**

Run the same pytest command.

Expected: PASS.

### Task 2: Apply Metadata and Seed Dossiers

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py`
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: approved `new_character` staged edit payload metadata.
- Produces: character card text containing provenance metadata and `characters/<id>/dossier.md` seeded deterministically.

- [x] **Step 1: Write failing apply test**

Add a test applying a new character with evidence/confidence/open questions and asserting the created card contains the metadata and the dossier is written.

- [x] **Step 2: Run focused pytest and confirm failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py::test_apply_new_character_seeds_progressive_metadata_and_dossier -q`

Expected: FAIL because apply does not write the metadata/dossier yet.

- [x] **Step 3: Implement minimal apply support**

Append reviewed provenance metadata to the description field and write a deterministic dossier paragraph after `overlay.create_character()`.

- [x] **Step 4: Run focused pytest and confirm pass**

Run the same pytest command.

Expected: PASS.

### Task 3: Frontend Review Fields

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `StagedEdit.payload` keys `evidence`, `confidence`, and `open_questions`.
- Produces: editable review controls that round-trip the metadata in `saveChronicle()`.

- [x] **Step 1: Write failing Vitest**

Extend the existing new-character proposal test to assert evidence, confidence, and open questions controls render and are sent after edits.

- [x] **Step 2: Run focused Vitest and confirm failure**

Run from `frontend/`: `npx vitest run src/routes/CampaignView.test.tsx -t "new_character proposal"`

Expected: FAIL because controls are absent.

- [x] **Step 3: Implement review controls**

Add a confidence select plus evidence/open-question textareas inside the `new_character` review block.

- [x] **Step 4: Run focused Vitest and confirm pass**

Run the same Vitest command.

Expected: PASS.

### Task 4: Regression Verification

**Files:**
- Test only.

- [x] **Step 1: Run backend absorb tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q`

Expected: PASS.

- [x] **Step 2: Run frontend campaign view tests**

Run from `frontend/`: `npx vitest run src/routes/CampaignView.test.tsx`

Expected: PASS.
