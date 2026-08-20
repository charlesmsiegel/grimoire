# Image descriptions and art recall — implementation plan

**Spec:** `docs/superpowers/specs/2026-08-20-image-descriptions-and-art-recall-design.md`

**Goal:** describable images on the four `assets`-backed surfaces, ranked
against the moment and offered to the model as a droppable prompt section, with
handles rewritten into markdown on the way in.

**Tech stack:** FastAPI + pytest; React + vitest. Gate: `make check`
(`PY=$PWD/backend/.venv/bin/python` in a fresh checkout).

## Global constraints

- Absent sidecar key = undescribed; explicit `""` = reviewed, never offered.
- Every store write through `store.atomic`; filesystem access through the
  resolvers; module-scope acyclic imports; `store/locks.py` classification.
- The three lint gates are ratcheted: resolving a finding requires
  `make baseline` and committing the smaller file with the fix.
- No handle is ever persisted to a transcript.
- With no descriptions in the store, the assembled prompt is byte-identical.

---

### Task 1 — `store/image_descriptions.py`

- Create: `backend/src/grimoire/store/image_descriptions.py`
- Test: `backend/tests/test_image_descriptions_store.py`

`DESCRIPTIONS_FILE = "descriptions.json"`. Directory-level primitives so both
the per-version folders and the campaign library's flat directory use one rule,
mirroring how `assets.list_in` was split out for `campaign_images`:

- `read_in(d, names) -> dict[str, str]` — tolerant; drops keys not in `names`.
- `write_in(d, descriptions, names)` — strict; `ValueError` on an unknown key.
- `set_in(d, name, text, names)` — read-modify-write of one entry, raw read so
  entries for untouched images survive.
- `read(root, aid, vid, name, base)` / `set(root, aid, vid, name, text, base)` —
  the per-version wrappers.
- `undescribed(root, base)` — every stored image with no sidecar key.

### Task 2 — `assets` + overlay + lifecycle integration

- Modify: `store/assets.py` (`delete_image` drops the entry, beside the
  existing `clear_focus`), `store/overlay.py` (`read_description`, mirroring
  `read_focus`'s "campaign-side if it holds the image or the sidecar"),
  `store/campaigns/lifecycle.py` (`_prune_duplicate_files` carve-out),
  `store/world_bundle.py` (correct the no-URLs claim).
- Test: `backend/tests/test_image_descriptions_overlay.py`

### Task 3 — routes

- Modify: `routes/characters.py`, `routes/worlds.py`, `routes/entities.py`,
  `routes/campaigns.py`; shared helper in `routes/common.py`.
- Test: `backend/tests/test_image_description_routes.py`

`GET`/`PUT` `.../images/{name}/description` on all four surfaces, world and
campaign; `GET .../images/undescribed`, registered **before** the generic
`{kind}/{eid}` entity routes.

### Task 4 — `store/context/art.py`: pool, ranking, handles

- Create: `backend/src/grimoire/store/context/art.py`
- Test: `backend/tests/test_context_art.py`

- `candidates(cid, cast, current_loc, wi_entities) -> list[dict]` — pool
  assembly; each entry `{handle, description, url, kind, id, name}`.
- `rank(candidates, recent_text) -> list[dict]` — keyword scoring, semantic
  upgrade via `embed_space.resolve`; depth and threshold from `config.md`;
  `[]` on any provider failure.
- `HANDLE = re.compile(...)`, `parse_handle`, and
  `resolve_handles(cid, text) -> str` — stateless rewrite, the three-rule test
  from the spec, unknown handles deleted.

### Task 5 — the prompt section

- Modify: `store/context/assemble.py` (`SECTIONS` + `_assemble` data),
  `templates/scene/sections/available_art.j2` (new),
  `templates/README.md` if it enumerates sections.
- Test: `backend/tests/test_context_available_art.py`, plus an eval case.

### Task 6 — the return path

- Modify: `routes/streaming.py::_persist_reply` — resolve after
  `turnstate.split_block`, before `split_reply`.
- Test: `backend/tests/test_persist_reply_art.py`

### Task 7 — the vision draft endpoint

- Modify: `routes/models.py` or a new `routes/` entry; refuse on `claude` kind.
- Test: `backend/tests/test_describe_image_route.py` with `llm_fakes`.

### Task 8 — frontend

- Modify: `components/CharacterEditor.tsx`, `PCEditor.tsx`, `EntityEditor.tsx`,
  `PostImagePicker.tsx`, `api/client.ts`, `api/types.ts`.
- Create: `components/DescribeQueue.tsx` (+ test), modelled on `TaggingQueue`.

### Task 9 — docs, baselines, gate

- `make baseline`, commit the smaller lint baselines with the change.
- Update `CLAUDE.md` only if a rule it states has moved.
- `make check` green.
