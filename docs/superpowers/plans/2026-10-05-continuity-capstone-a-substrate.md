# Continuity Capstone — Slice A: Canonical Continuity Substrate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `continuity.json` (aliases, links, suppressions) and the canonical projections over plot threads and commitments. Alias and link writes are journalled and reversible, and there are routes to manage them. No consumer changes behaviour yet.

**Architecture:** A new package, `backend/src/grimoire/store/continuity/`, split into:

- leaf IO (`doc`);
- pure alias-graph helpers (`canon`);
- read projections (`effective`, `involvement`);
- journalled orchestration (`review`).

The split keeps `test_import_guard`'s module graph acyclic, because `scene_refs` and `undo` have to import the IO module. One new router, `routes/continuity.py`, is composed before `entities`. The existing ledger routes gain alias redirection and a delete cascade.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), pytest + `TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements spec §31 Slice A and the requirements named in its sections:

- §4;
- §5 (all of it);
- §7.0–§7.2;
- §8;
- §12.8;
- §21's read, alias and link routes.

Later slices (B–G) get their own plans. Nothing here switches a prompt, ledger, Todo or shell consumer to the effective projections; that is Slice B.

**Revision:** this is the second version. It resolves the plan-gate adversarial review (2026-10-05; Claude standing in for Codex). Its findings are mapped in the self-review section at the end.

## Global Constraints

- **Privacy:** test fixtures and docstrings use only the placeholder names Seraphine, Mara, Winifred, Realm and Saltmarch, or invented slugs (`maras-map`, `winifreds-chart`). No real store content.
- **Store writes:** every write goes through `store.atomic` (`test_atomic_guard`), and every path through `campaigns.paths.campaign_root(cid)` (`test_paths_guard`).
- **JSON format:** `json.dumps(data, indent=2, sort_keys=True) + "\n"`.
- **Imports inside `store/continuity/`:** all at module scope, with an acyclic graph. A cross-package import binds a *submodule*: `from ..campaigns import paths as campaigns_paths`, `from .. import scene_ids`, then `scene_ids.parse_sid(...)`. Never bind a function (`test_import_guard`).
- **Package init:** `store/continuity/__init__.py` contains only a docstring, with no imports. So **every importer names the submodule explicitly**:
  - in routes: `from ..store.continuity import canon, doc, effective, review`;
  - in store: `from .continuity import doc as continuity_doc`.

  `store.continuity.review` is never reached as an attribute chain.
- **Lock domain:** only `store.continuity.doc` goes into `locks.DOMAIN_MODULES`. It is the one module with filesystem writes. Do **not** list `canon`, `effective`, `involvement` or `review`: `test_the_declaration_has_no_phantom_modules` fails a module that does not mutate. `review`'s public mutators still wrap their bodies in `with locks.campaign_lock(cid):` (reentrant). `UNREVIEWED` must not grow.
- **Pydantic:** bodies use plain `BaseModel` fields with `| None = None` defaults. No `Field`, validators, `ConfigDict` or unions (`test_pydantic_guard`).
- **Refusals:** `HTTPException(status, detail={"kind": ..., "detail": ...})`.
- **Liveness predicate:** a thread is live iff `fieldtext.text(status).lower() != "closed"`. A commitment is live iff `fieldtext.text(status).lower() not in commitments.RESOLVED`. This is the same predicate `open_threads` / `open_commitments` use, defined once in `effective` as `is_live(kind, status) -> bool`.
- **Record existence:** a physical record exists iff its value in `plot.read` / `commitments.read` / `events.read` is a `dict`. This is the row set `open_threads` iterates. Truthiness is never the test: `{}` exists.
- **Identity law** (spec §7.1): for a campaign with no live aliases,
  - `effective.threads(cid, include_closed=X) == [dict(r, aliases=[]) for r in plot.open_threads(cid, include_closed=X)]`;
  - the same holds for commitments;
  - the equality is byte for byte, in the same order.
- **Lint ratchet:** `make check-lint` and `make check-mypy` must not grow `lint-baselines/*.json`. A new finding is fixed, never baselined.
- **Running backend tests:** from `backend/`, run `PYTHONPATH=src .venv/bin/python -m pytest <paths> -q`. In a git worktree, use the main checkout's interpreter instead, as `CLAUDE.md` describes.
- **Verification steps run one command per purpose.** A `-k` filter is never combined with a file list it would deselect.

## Review Focus

1. **A hand-edited alias cycle** (`thread:a → thread:b → thread:a` written directly into continuity.json):
   - `canon.resolve` terminates and returns the input ref;
   - `effective.live_canon` maps neither ref;
   - `effective.threads` shows both records;
   - `diagnostics.dangling_aliases` lists both entries with reason `cycle`.

   Pinned in Task 3 and Task 4.
2. **Plot ids containing `/` or `:`** (model-written ids are arbitrary strings): `split_ref` splits on the first `:`, and `DELETE /continuity/aliases?ref=thread:a/b` reaches the alias. Pinned in Task 3 and Task 8.
3. **A garbled plot.json** (`{ no`):
   - `effective.threads` raises exactly as `plot.open_threads` does, so the callers' existing tolerance applies;
   - `GET /continuity` answers 200, with titles falling back to ids, `unreadable == ["plot"]`, well-formed `diagnostics`, and no thread alias or link flagged dangling or broken, because existence is unknown.

   Pinned in Task 4 and Task 8.
4. **An alias whose canonical was removed outside the cascade** (undo of a create):
   - the source shows as its own effective record;
   - the alias is listed as dangling;
   - a `PUT /ledger/threads/<source>` writes the **source**, never resurrecting the missing target.

   Pinned in Task 4 and Task 9.
5. **Undoing an alias deletion after the reverse alias was created:** A→B is deleted, B→A is created, then the deletion is undone. The undo is refused with 409, with neither a cycle on disk nor a 500. Pinned in Task 6.

---

## File structure

| File | Responsibility |
|---|---|
| `store/continuity/__init__.py` | Package docstring only |
| `store/continuity/doc.py` | continuity.json IO; primitive mutators; `repoint_scenes`; the restores undo uses |
| `store/continuity/canon.py` | Ref parsing; alias-graph resolution (`resolve`, `canonical_ref`); link ids, candidate ids, fingerprints |
| `store/continuity/effective.py` | `is_live`; `RELATIONS`; `live_canon`; effective records/rows; effective links; diagnostics |
| `store/continuity/involvement.py` | `touched_scenes` and `stage_history` (moved from briefing); `scene_actors`; `of` |
| `store/continuity/review.py` | `Refused`; validated, journalled alias/link create and remove; `forget_ref`; `merged_sources`; `describe` |
| `store/briefing.py` | Calls the moved involvement functions (no behaviour change) |
| `store/timeline.py` | Docstring reference `briefing._touched_scenes` → `involvement.touched_scenes` |
| `store/undo.py` | Two new targets: `continuity_alias`, `continuity_link` |
| `store/scene_refs.py` | `continuity.doc` joins the fan-out |
| `store/locks.py` | `store.continuity.doc` added to `DOMAIN_MODULES` |
| `store/__init__.py`, `tests/store_api_baseline.json` | Facade gains `continuity` |
| `routes/continuity.py` | `GET /continuity`; alias and link POST/DELETE |
| `routes/models.py` | `ContinuityAliasCreate`, `ContinuityLinkCreate` |
| `routes/__init__.py` | Import and compose `continuity` before `entities`; a docstring table row |
| `routes/ledger.py` | Live-alias redirect on PUT; malformed-file refusal, `has_merged_records`, and the cascade on DELETE |
| `routes/campaigns.py` | Event DELETE cascades into `forget_ref` |

Backend paths are relative to `backend/src/grimoire/`, and tests to `backend/tests/`.

---

### Task 1: `continuity.doc` — the file, its reader/writer split, and primitive mutators

**Files:**
- Create: `store/continuity/__init__.py`, `store/continuity/doc.py`
- Modify: `store/locks.py` (`DOMAIN_MODULES`), `store/__init__.py` (import and `__all__`), `tests/store_api_baseline.json` (regenerated deliberately)
- Test: `tests/test_continuity_doc.py`

**Interfaces:**
- Produces:
  - `SECTIONS = ("aliases", "links", "suppressions")`
  - `class ContinuityError(Exception)`
  - `read(cid) -> dict`. Tolerant. Returns `{**unknown_top_level_keys, "version": <stored int, else 1>, "aliases": {}, "links": {}, "suppressions": {}}`, with each well-formed section's stored dict in place. A malformed section reads as `{}`. Section *values* are returned exactly as stored: a record may be any JSON value, and readers check shapes.
  - `malformed(cid) -> list[str]`. Returns `["file"]` when the file is unparseable or its top level is not a dict. Otherwise it returns the names of sections that are present and not dicts, in `SECTIONS` order.
  - `get_alias(cid, ref) -> object | None`, `get_link(cid, lid) -> object | None`. Lenient: both read through `read`.
  - Mutators:
    - `put_alias(cid, ref, record: dict)`, `drop_alias(cid, ref) -> object | None`
    - `put_link(cid, lid, record: dict)`, `drop_link(cid, lid) -> object | None`
    - `put_suppression(cid, fp, record: dict)`, `drop_suppression(cid, fp) -> object | None`

    Every mutator takes `campaign_lock` and reads strictly. It raises `ContinuityError` when the file, or the section it touches, is malformed. It writes the stored `version` back unchanged (or `1` when there is none), and preserves unknown top-level keys and record fields.

- [ ] **Step 1: Write the failing tests** in `tests/test_continuity_doc.py`. Copy the `client` and `cid` fixtures from `tests/test_ledger_routes.py`: they set `GRIMOIRE_HOME` to `tmp_path`, run `importlib.reload(store)`, and create a world and a campaign through the API. Later store-level test files in this plan reuse both fixtures. Tests:
  - `test_absent_file_reads_empty`: `read(cid) == {"version": 1, "aliases": {}, "links": {}, "suppressions": {}}` and `malformed(cid) == []`.
  - `test_unparseable_file_reads_empty_and_refuses_writes`: write `"{ no"` to `campaign_root(cid)/"continuity.json"`. Then `read` is empty, `malformed(cid) == ["file"]`, `put_alias(...)` raises `ContinuityError`, and the file text is unchanged.
  - `test_one_malformed_section_reads_empty_others_survive`: given the file `{"aliases": [], "links": {"l1": {...}}, "suppressions": {}}`:
    - `read(cid)["aliases"] == {}`;
    - `read(cid)["links"]` keeps `l1`;
    - `malformed(cid) == ["aliases"]`;
    - `put_link` succeeds;
    - `put_alias` raises `ContinuityError`.
  - `test_unknown_keys_and_version_preserved`: given top-level `"extra": 1` and `"version": 7`, plus an alias record field `"x": 2`, all three are still on disk after `put_link(...)`.
  - `test_put_and_drop_round_trip`: for each of the three section pairs.
  - `test_written_json_is_sorted_and_newline_terminated`.

- [ ] **Step 2: Run them and confirm they fail.**
  Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_doc.py -q`
  Expected: errors, `ModuleNotFoundError: grimoire.store.continuity`.

- [ ] **Step 3: Implement `store/continuity/doc.py`.**
  - The path is `campaigns_paths.campaign_root(cid) / "continuity.json"`.
  - Model the reader on `events.read`, and the strict mutation read on `events._mutable`. The reader catches `OSError`, `UnicodeDecodeError` and `json.JSONDecodeError`.
  - Each mutator does `with locks.campaign_lock(cid):`, then a strict read, then changes one key, then `atomic.write_text`.
  - Add `"store.continuity.doc"` to `DOMAIN_MODULES`, with a comment: continuity.json is rewritten whole, so two unlocked read-modify-writes lose one.
  - Add `continuity` to the `from . import (...)` block and to `__all__` in `store/__init__.py`.

- [ ] **Step 4: Run the new tests and confirm they pass.**
  Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_doc.py -q`
  Expected: PASS.

- [ ] **Step 5: Run the guards.**
  Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_import_guard.py -q`
  Expected: PASS.

- [ ] **Step 6: Regenerate the facade baseline deliberately.** From `backend/`:

  ```bash
  PYTHONPATH=src .venv/bin/python -c "import json, grimoire.store as s; print(json.dumps({'all': sorted(s.__all__), 'dir': sorted(n for n in dir(s) if not n.startswith('_'))}, indent=2))" > tests/store_api_baseline.json
  ```

  `git diff tests/store_api_baseline.json` must show only `continuity` added to both lists. Then run `pytest tests/test_store_api_baseline.py -q`, which must PASS.

- [ ] **Step 7: Commit:** `feat(continuity): add continuity.json store with tolerant reader and strict writer`

---

### Task 2: Scene renames reach continuity.json

**Files:**
- Modify: `store/continuity/doc.py`; `store/scene_refs.py` (the import, the fan-out tuple, and the docstring count and list)
- Test: `tests/test_continuity_doc.py`

**Interfaces:**
- Produces: `doc.repoint_scenes(cid, mapping: dict[str, str]) -> None`.
  - It takes `campaign_lock`.
  - It is tolerant, like `commitments.repoint_scenes`: an absent file, an unparseable file, a malformed `links` section, a non-dict link record and a non-str `scene` are all skipped silently.
  - It rewrites `links[*].scene` only.
  - It writes only when something matched. It never creates an absent file.

- [ ] **Step 1: Write the failing tests**
  - `test_repoint_rewrites_link_scene`: a link has `scene: "003--old"`. After `repoint_scenes(cid, {"003--old": "003--new"})`, its scene is `"003--new"`.
  - `test_repoint_skips_unparseable_file`: the file is unchanged, and nothing raises.
  - `test_repoint_never_creates_absent_file`: with no continuity.json, a mapping leaves no file behind.
  - `test_scene_refs_fans_out_to_continuity`: `store.scene_refs.repoint(cid, {...})` rewrites the link's scene.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `repoint_scenes` and the fan-out.**
  - In `scene_refs.py`, import `from .continuity import doc as continuity_doc` and add it to `repoint`'s tuple.
  - Update the docstring: “Twenty-one stores” → “Twenty-two stores”; the list gains “continuity (each reviewed link's `scene`)”; the `usage` paragraph's “A twenty-second” → “A twenty-third”.
- [ ] **Step 4: Run** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_doc.py tests/test_scene_refs.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): follow scene renames in reviewed links`

---

### Task 3: `continuity.canon` — refs, alias-graph resolution, ids and fingerprints

**Files:**
- Create: `store/continuity/canon.py`
- Test: `tests/test_continuity_canon.py`

`canon` answers **alias-graph** questions only: what the stored mappings say, independent of whether records exist. Anything about *current state* uses `effective.live_canon` (Task 4).

**Interfaces:**
- Consumes: `doc.read`, `doc.malformed`, `doc.ContinuityError`.
- Produces:
  - `split_ref(ref) -> tuple[str, str]`. Splits on the FIRST `:`. Raises `ValueError` if `ref` is not a str, has no `:`, or either part is empty.
  - `KIND_OF_PREFIX: dict[str, str]`:

    | Prefix | Kind |
    |---|---|
    | `scene` | `scene` |
    | `characters` | `character` |
    | `pcs` | `pc` |
    | `locations` | `location` |
    | `groups` | `group` |
    | `thread` | `thread` |
    | `commitment` | `commitment` |
    | `event` | `event` |
    | `fact` | `fact` |
    | `idea` | `idea` |
    | `holiday` | `holiday` |
    | `birthday` | `birthday` |

    `PREFIX_OF_KIND` is its inverse; together they are the single mapping spec §4 asks for. `ref_kind(ref) -> str` raises `ValueError` for an unknown prefix.
  - `actor_ref(token: str) -> str`: `"characters/x"` → `"characters:x"`. A colon form passes through unchanged.
  - `resolve(aliases: dict, ref: str, *, strict: bool = False) -> str`. Pure. It follows `aliases[ref]["to"]` transitively. The walk stops, returning the last ref reached, when:
    - the record is not a dict;
    - its `to` is not a non-empty str;
    - the record is missing.

    On a **cycle**, the lenient form returns the input `ref`, and the strict form raises `doc.ContinuityError`.
  - `canonical_ref(cid, ref, *, strict=False) -> str` and `canonical_refs(cid, refs, *, strict=False) -> dict[str, str]`. Both read `doc.read(cid)["aliases"]` once.
    - The lenient form returns the input on any failure.
    - The strict form raises `ContinuityError` when `doc.malformed(cid)` contains `"file"` or `"aliases"`.
  - `link_id(relation, a, b) -> str`: `"l" + sha256(f"{relation}\0{a}\0{b}".encode()).hexdigest()[:20]`, with `(a, b)` sorted first when `relation == "related_to"`.
  - `candidate_id(kind, refs) -> str`: `f"{kind}-" + sha256("\0".join(sorted(refs)).encode()).hexdigest()[:16]`.
  - `pair_fingerprint(kind: str, sides: list[dict]) -> str`. Each side is `{ref, title, status, kind, due}`; missing keys and non-str values read as `""`.
  - `lifecycle_fingerprint(kind: str, ref: str, status: str, beat_count: int, latest_beat: str, due: str, temporal_link_ids: list[str]) -> str`. Threads pass `due=""`.

  Both fingerprints are:

  ```
  "fp1_" + sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
  ```

  The payloads are exactly spec §5.5's, with sides sorted by ref and temporal link ids sorted.

- [ ] **Step 1: Write the failing tests**
  - `test_split_ref_uses_first_colon`:
    - `split_ref("birthday:characters:mara:740000") == ("birthday", "characters:mara:740000")`;
    - `split_ref("thread:a/b") == ("thread", "a/b")`;
    - each of `"nocolon"`, `":x"`, `"thread:"` and `3` raises `ValueError`.
  - `test_actor_ref_converts_slash_form`.
  - `test_resolve_transitive`.
  - `test_resolve_stops_at_non_dict_record_and_non_str_to`: `{"thread:a": "thread:b"}` resolves `thread:a` to `thread:a`, and `{"thread:a": {"to": 3}}` likewise.
  - `test_resolve_cycle_lenient_returns_input_and_strict_raises`.
  - `test_canonical_ref_unreadable_file_returns_input_and_strict_raises`.
  - `test_link_id_symmetric_only_for_related_to`.
  - `test_candidate_id_order_independent`.
  - `test_pair_fingerprint_ignores_side_order_and_beats`: changing one side's `title` changes the fingerprint.
  - `test_lifecycle_fingerprint_has_no_last_scene`: changing `beat_count` or `latest_beat` changes it, and `"last_scene" not in inspect.signature(lifecycle_fingerprint).parameters`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `canon.py`.**
- [ ] **Step 4: Run** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_canon.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): refs, alias-graph resolution, ids and fingerprints`

---

### Task 4: `continuity.effective` — the live resolver, merged records under the identity law, effective links, diagnostics

**Files:**
- Create: `store/continuity/effective.py`
- Test: `tests/test_continuity_effective.py`

**Interfaces:**
- Consumes:
  - `canon.split_ref`, `canon.link_id`, `doc.read`;
  - `plot.read`, `plot.open_threads`;
  - `commitments.read`, `commitments.open_commitments`, `commitments.RESOLVED`;
  - `events.read`;
  - `fieldtext.text`;
  - `scene_ids` (module binding).
- Produces:
  - `is_live(kind: str, status) -> bool`: the Global Constraints predicate.
  - `RELATIONS: dict[str, tuple[frozenset[str], frozenset[str], bool]]`: relation → (allowed a-prefixes, allowed b-prefixes, directional). These are spec §5.3's rows:

    | Relation | a | b | Directional |
    |---|---|---|---|
    | `continues` | thread | thread | yes |
    | `subthread_of` | thread | thread | yes |
    | `pays_off` | thread | commitment | yes |
    | `before`, `on`, `after`, `by` | thread or commitment | event | yes |
    | `related_to` | thread, commitment or event | thread, commitment or event | no |

  - `class Ledgers`: a snapshot loaded once per call, `Ledgers.load(cid) -> Ledgers`. It holds `plot`, `commitments` and `events` as dicts, or `None` when that file failed to read, plus an `unreadable: list[str]` naming the files that failed (`"plot"`, `"commitments"`, `"events"`). A file that reads to a non-dict also counts as unreadable.
  - `Ledgers.exists(ref) -> bool | None`: `True` or `False` for a known prefix whose file was readable; `None` when that file is unreadable; `False` for any prefix other than thread, commitment or event.
  - `live_canon(cid, ledgers: Ledgers | None = None) -> dict[str, str]`: the **one** current-state resolver (spec §7.1, “Live canonical”). It maps each alias source whose live canonical differs from itself to that canonical. Rules:
    - **Cycles first:** if `canon.resolve(aliases, src)` detects a cycle, `src` is not mapped.
    - **Otherwise, walk hop by hop from `src`.** Follow the hop `cur → to` only if all of these hold:
      - `aliases[cur]` is a dict, and its `to` is a non-empty str;
      - `split_ref` succeeds on both sides;
      - both prefixes are equal and in {`thread`, `commitment`};
      - `ledgers.exists(to)` is not `False` (unknown counts as passable);
      - `to` has not been visited.
    - **Stop at the last valid hop.** `src` is mapped iff the walk moved, and the source itself also exists: `ledgers.exists(src) is not False`.
  - `diagnostics(cid, ledgers=None) -> dict` → `{dangling_aliases: [{ref, to, reason}], broken_links: [{id, reason}], hidden_links: [{id, reason}], unreadable: [...]}`. Each list is sorted by its key.
    - `dangling_aliases` has one entry per alias whose walk stopped early, naming the failing hop's reason:
      - `cycle`;
      - `malformed_record`: not a dict, or a bad `to`;
      - `wrong_type`;
      - `missing_target`: `exists` is `False`;
      - `missing_source`.

      A walk that stops because a hop's `exists` is `None` is **not** listed.
    - `broken_links` reasons:
      - `malformed_record`: not a dict, a non-str `a`/`b`/`relation`, or `split_ref` fails;
      - `invalid_relation`: the relation is unknown, or the prefixes are outside `RELATIONS`;
      - `missing_endpoint`: a canonical endpoint's `exists` is `False`;
      - `self_collapsing`.
    - `hidden_links` reason: `duplicate`.
  - `links(cid, ledgers=None) -> list[dict]`: the effective links, sorted by id. Each is `{id, relation, a, b, a_raw, b_raw, scene, note, created}`, where `a`/`b` are `live_canon`-canonical. A link is kept unless it is broken or hidden. Duplicates are decided in sorted-id order on `(relation, a, b)`, with `related_to`'s endpoints sorted first.
  - `records(cid, kind) -> dict[str, dict]`, where `kind` ∈ {`thread`, `commitment`}.
    - It raises whatever `plot.read` / `commitments.read` raise.
    - It is keyed by canonical ref, and includes closed and resolved groups.
    - Each value is `{ref, id, title, status, kind?, due?, beats, last_scene, latest_beat, aliases: [{ref, title, status}], members: [physical ids]}`. All field values are coerced through `fieldtext.text`, exactly as `open_threads` coerces them.
  - `threads(cid, include_closed=False) -> list[dict]` and `commitments(cid, include_resolved=False) -> list[dict]`. Each row has `open_threads` / `open_commitments`' keys plus `aliases`.

**Beat merge** (only for a group with ≥1 live member besides the canonical; the signatures do not determine this). Each member's beats are first sanitized:

```
[b for b in beats if isinstance(b, dict)] if isinstance(beats, list) else []
```

```python
def _merge_beats(members: list[list[dict]]) -> list[dict]:
    # members[0]: canonical's sanitized beats; then aliases in sorted-ref order.
    runs = []
    for m_index, beats in enumerate(members):
        current_key, current = -1, []
        for beat in beats:
            scene = fieldtext.text(beat.get("scene"))
            parsed = scene_ids.parse_sid(scene) if scene else None
            if parsed is not None:
                if current:
                    runs.append((current_key, m_index, len(runs), current))
                current_key, current = parsed["number"], [beat]
            else:
                current.append(beat)          # uncomparable: stays after its predecessor
        if current:
            runs.append((current_key, m_index, len(runs), current))
    runs.sort(key=lambda r: (r[0], r[1], r[2]))
    out, owner = [], {}
    for _, m_index, _, run in runs:
        for beat in run:
            sig = (fieldtext.text(beat.get("scene")), fieldtext.text(beat.get("text")))
            if owner.get(sig, m_index) != m_index:
                continue                      # exact duplicate ACROSS records only
            owner.setdefault(sig, m_index)
            out.append(beat)
    return out
```

- **Effective `last_scene`:** the scene with the greatest `parse_sid(...)["number"]` among every member's `fieldtext.text(last_scene)` and every merged beat's scene. Ties go to the canonical member. If nothing is comparable, use the canonical's `fieldtext.text(last_scene)`.
- **`latest_beat`:** `fieldtext.text(merged[-1].get("text"))`, or `""`.

**Rows:**

1. Start from `plot.open_threads(cid, include_closed=True)` (or `open_commitments(cid, include_resolved=True)`), with `aliases: []` added to each row.
2. For each canonical row with live members:
   - set `last_scene` and `latest_beat` to the merged values;
   - set `aliases` to the members' `{ref, title, status}`, from their projected rows;
   - drop the members' own rows.
3. Filter with `is_live` on the canonical row's *projected* status, unless `include_*` is set.
4. Re-sort by `(last_scene, id)`.

A canonical whose physical record is not a dict never appears as a live target, because `exists` is `False`. Its would-be members therefore stay visible.

- [ ] **Step 1: Write the failing tests.** Fixtures use `store.plot.set_movement`, `store.commitments.set_movement`, `doc.put_alias` / `put_link`, and direct file writes for the hand-edited shapes. Scene ids are `001--saltmarch`, `002--realm` and `003--winifred`.
  - `test_identity_law_threads`: the fixture has a status-only move to `003--winifred`, newer than every beat; beats appended out of scene order; a closed thread; a hand-edited `"status": "Closed"`; and a non-dict record. Assert `effective.threads(cid) == [dict(r, aliases=[]) for r in plot.open_threads(cid)]`, and the same with `include_closed=True`.
  - `test_identity_law_commitments`: the same shape.
  - `test_alias_hides_source_and_merges_beats`: `maras-map`, with beats at 001 and 003, aliased into `winifreds-chart`, with a beat at 002.
    - The rows show one `winifreds-chart`, with `aliases == [{"ref": "thread:maras-map", ...}]`.
    - `latest_beat` is the 003 text.
    - `records(...)["thread:winifreds-chart"]["beats"]` is in scene order 001, 002, 003.
  - `test_duplicate_beat_dropped_across_records_only`.
  - `test_uncomparable_beat_keeps_position_after_predecessor`.
  - `test_hand_edited_beat_shapes_tolerated`: `"beats": ["stray"]`, `"beats": {"x": 1}` and `{"scene": "001--saltmarch", "text": ["x"]}` in an aliased group. Nothing raises, and only the dict beats with text survive the merge.
  - `test_effective_last_scene_includes_status_only_moves`.
  - `test_canonical_status_is_authoritative_and_filters`.
  - `test_transitive_alias_merges_into_final_target`.
  - `test_chain_with_missing_tail_stops_at_last_valid_hop`: A→B and B→gone. A merges into B, and diagnostics lists B→gone as `missing_target` and does **not** list A.
  - `test_missing_target_degrades_source_to_own_record`: the Review Focus 4 case.
  - `test_cycle_degrades_both`: the Review Focus 1 case.
  - `test_wrong_type_and_non_dict_alias_records`: `thread:a → commitment:b` is reported as `wrong_type`, and `"thread:c": "thread:d"` as `malformed_record`. Neither merges anything.
  - `test_non_dict_canonical_keeps_members_visible`: `winifreds-chart` is the string `"x"`. Alias `maras-map → winifreds-chart` leaves `maras-map` visible, and the alias is listed as `missing_target`.
  - `test_empty_dict_record_exists`: `winifreds-chart` is `{}`. It counts as existing, and the alias into it is live.
  - `test_removing_alias_restores_two_records`.
  - `test_links_canonicalize_dedupe_and_hide`:
    - `thread:a pays_off commitment:x` and `thread:b pays_off commitment:x` both stored; after alias a→b, `links()` returns one, and the other is in `hidden_links` as `duplicate`.
    - A `continues` link between a and b is in `broken_links` as `self_collapsing`.
    - A link to a missing event is `missing_endpoint`.
    - A `pays_off` from a commitment to a thread is `invalid_relation`.
    - A link record `{"a": 3}` is `malformed_record`.
  - `test_link_through_dangling_alias_stays_effective`: `thread:a → thread:gone` exists, and link `thread:a pays_off commitment:x` is effective with `a == "thread:a"`.
  - `test_link_id_and_endpoints_unchanged_after_later_alias`: the stored record still has its original `a`, and `links()` shows the canonical endpoint with `a_raw` set to the original.
  - `test_garbled_plot_raises_like_open_threads`: `effective.threads` raises the same exception type as `plot.open_threads`.
  - `test_garbled_plot_existence_unknown`: with plot.json `"{ no"`:
    - `diagnostics()["unreadable"] == ["plot"]`;
    - a thread alias is not listed as dangling;
    - a thread link is not broken;
    - `links()` still returns it.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `effective.py`.**
- [ ] **Step 4: Run** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_effective.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit:** `feat(continuity): live resolver and effective projections under the identity law`

---

### Task 5: `continuity.involvement` — move briefing's join, then generalise it

**Files:**
- Create: `store/continuity/involvement.py`
- Modify:
  - `store/briefing.py`: delete `_touched_scenes` / `_stage_history`, call the moved functions, and move their docstrings with them;
  - `store/timeline.py`: its docstring's `briefing._touched_scenes` → `involvement.touched_scenes`.
- Test: `tests/test_continuity_involvement.py`. `tests/test_briefing_route.py` must pass **unmodified**.

**Interfaces:**
- Consumes:
  - `effective.records`, `effective.live_canon`, `effective.Ledgers`;
  - `chronicle.read_chronicle`;
  - `appearances.cast` (module binding: `roster`);
  - `canon.actor_ref`, `canon.split_ref`.
- Produces:
  - `touched_scenes(records) -> dict[str, set[str]]`: the moved `briefing._touched_scenes`, unchanged.
  - `stage_history(cid, refs: set[str]) -> dict[str, set[str]]`: the moved `briefing._stage_history`, unchanged. Refs keep the `"<kind>/<id>"` form.
  - `scene_actors(cid) -> dict[str, set[str]]`: scene id → colon-form actor refs, from roster ∪ chronicle cast. Each source is guarded separately: a roster that raises and a chronicle that raises each contribute nothing. Non-str entries are skipped.
  - `of(cid, refs: list[str]) -> dict[str, dict]`: `{input_ref: {"actors": sorted list, "scenes": sorted list}}`.
    - Only `thread:` and `commitment:` refs are resolved. Anything else, including an unparseable ref, maps to `{"actors": [], "scenes": []}`.
    - Each input ref is canonicalized with `live_canon`.
    - Scenes are the group's merged beat scenes ∪ every member's `last_scene`, minus `""`.
    - If `effective.records` raises for a kind, refs of that kind map to empty.
    - No lock is taken.

- [ ] **Step 1: Record the briefing tests green before the move.** Run `PYTHONPATH=src .venv/bin/python -m pytest tests/test_briefing_route.py -q`. Expected: PASS.
- [ ] **Step 2: Move the two functions** (as public names with unchanged bodies) and point `briefing.build` at them. Re-run the same command. Expected: PASS, with no test file edited.
- [ ] **Step 3: Write the failing tests**
  - `test_of_unions_roster_and_chronicle`: `characters/mara` is in the chronicle cast of `001--saltmarch`, and `pcs/seraphine` is in the appearances roster for `002--realm`. A thread with beats in both scenes gives actors `["characters:mara", "pcs:seraphine"]`.
  - `test_of_aggregates_alias_group`: the result is keyed by the ref that was asked for.
  - `test_of_includes_last_scene_without_beat`.
  - `test_of_tolerates_garbled_chronicle_and_appearances`: write `"{ no"` into the chronicle and into one appearances file. The surviving source's actors still appear, and nothing raises.
  - `test_of_garbled_plot_maps_threads_empty_commitments_still_answer`.
  - `test_of_unknown_or_bad_ref_is_empty`: `"place:x"` and `"nocolon"` both map to empty.
- [ ] **Step 4: Implement `scene_actors` and `of`.**
- [ ] **Step 5: Run the new tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_involvement.py -q`. Expected: PASS.
- [ ] **Step 6: Run the moved code's tests and guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_briefing_route.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 7: Commit:** `refactor(briefing): move the involvement join into continuity.involvement and generalise it`

---

### Task 6: Undo targets for aliases and links, with validated restores

**Files:**
- Modify: `store/continuity/doc.py` (the restores); `store/undo.py` (branches in `read_value` / `write_value`, and the import `from .continuity import doc as continuity_doc`)
- Test: `tests/test_continuity_undo.py`

**Interfaces:**
- Produces:
  - `doc.restore_alias(cid, ref, value) -> None`.
    - `None` drops the alias.
    - Otherwise `value` must be a dict with a non-empty str `to`. The prefixes must be equal and in {thread, commitment}, and `ref != to`.
    - It must not close a cycle: a private `_reaches(aliases, start, target)` walks the stored mappings, stopping at non-dict records.
    - It raises `ContinuityError` otherwise.
  - `doc.restore_link(cid, lid, value) -> None`.
    - `None` drops the link.
    - Otherwise `value` must be a dict with str `a`, `b` and `relation`, and a prefix triple allowed by a private copy of the relation table.
    - It refuses (raises `ContinuityError`) when another stored link (a different id) is **effectively equivalent**. Both links' endpoints are canonicalized through a private `_alias_walk(aliases, ref)`, which follows type-valid hops and stops on a cycle, a non-dict record or a prefix change. They are then compared on `(relation, a, b)`, with `related_to` sorted.

    doc cannot see record existence, so this is the alias-graph approximation of spec §12.8's “equivalent effective link”. Existence is checked again by `effective` at read.
  - `undo.read_value` / `write_value` handle the targets:
    - `{"w": "continuity_alias", "ref": ...}` → `continuity_doc.get_alias` / `restore_alias`;
    - `{"w": "continuity_link", "id": ...}` → `get_link` / `restore_link`.

    In `write_value`, `ContinuityError` is re-raised as `UndoConflict(str(exc))`.

- [ ] **Step 1: Write the failing tests**
  - `test_undo_alias_create_and_redo`: journal a create through `store.undo.journalled(cid, {"w": "continuity_alias", "ref": "thread:a"}, kind="continuity_alias", ref={"kind": "continuity_alias", "id": "thread:a"}, field="alias", label="A → merged into B")` around `doc.put_alias`. Then:
    - `store.undo.undo(cid, jid)` removes the alias;
    - undoing that reversal restores it.
  - `test_undo_link_create_and_redo`.
  - `test_undo_restore_refuses_cycle`, the Review Focus 5 case:
    - `pytest.raises(store.undo.UndoConflict)`;
    - the message is not `store.undo.CONFLICT`;
    - the only alias on disk is B→A.
  - `test_undo_link_restore_refuses_effective_equivalent`:
    1. delete L1 (`thread:a pays_off commitment:x`), journalled;
    2. alias `thread:a → thread:b`;
    3. create L2 (`thread:b pays_off commitment:x`), which has a different id;
    4. undo L1's deletion.

    Assert `UndoConflict`, with a message that is not `store.undo.CONFLICT`. That proves `restore_link`'s check refused it, not the compare-and-swap.
  - `test_restore_tables_match_effective_relations`: doc's private table equals `effective.RELATIONS`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the restores and the two undo branches.**
- [ ] **Step 4: Run the new tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_undo.py -q`. Expected: PASS.
- [ ] **Step 5: Run the existing undo tests and the import guard.** `PYTHONPATH=src .venv/bin/python -m pytest tests -q -k undo` then `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py -q`. Expected: both PASS.
- [ ] **Step 6: Commit:** `feat(continuity): journalled aliases and links are undoable with validated restores`

---

### Task 7: `continuity.review` — validated, journalled alias and link writes, and the cascade helpers

**Files:**
- Create: `store/continuity/review.py`
- Test: `tests/test_continuity_review.py`

**Interfaces:**
- Consumes:
  - `doc.*`, `canon.*`;
  - `effective.Ledgers`, `effective.live_canon`, `effective.records`, `effective.RELATIONS`, `effective.is_live`;
  - `undo.journalled`;
  - `locks.campaign_lock`.
- Produces:
  - `class Refused(Exception)`: `.status: int`, `.kind: str`, `.detail: str`, `.extra: dict`.
  - `describe(cid, ref) -> str`: a thread or commitment title, an event's name, or the ref itself. Never raises, and tolerates garbled files.
  - `create_alias(cid, ref, to, *, replace=False, accept_status_change=False, note="", source="manual") -> dict` → `{"alias": {ref, to, created, source, note}, "affected": [refs]}`. Refusals:

    | Condition | Status / kind |
    |---|---|
    | `split_ref` fails | 400 `bad_ref` |
    | `ref == to` | 400 `self_alias` |
    | prefixes differ, or are outside thread/commitment | 400 `wrong_type` |
    | a ledger file is unreadable | 409 `unreadable` |
    | source or target does not exist | 404 `not_found` |
    | `canon.resolve(aliases, to)` reaches `ref` (cycle-checked) | 409 `alias_cycle` |
    | `ref` already has an alias, and not `replace` | 409 `alias_exists`, `extra={"to": current}` |
    | `is_live(source status) != is_live(target group status)` (group = `live_canon` of `to` after the write), and not `accept_status_change` | 409 `liveness_mismatch`, `extra={"source": {status, kind, due}, "canonical": {ref, status, kind, due}}` |
    | malformed file or `aliases` section | 409 `malformed` |

    - `to` is stored **as given**, and liveness is judged against its live canonical.
    - `affected` lists the other alias sources whose `live_canon` value changes because of the write: compare before and after.
    - It is journalled with the target `{"w": "continuity_alias", "ref": ref}`, `kind="continuity_alias"`, `ref={"kind": "continuity_alias", "id": ref}`, `field="alias"`, and `label=f"{describe(ref)} → merged into {describe(to)}"`.
    - There is **no** due-copy here. Spec §5.1's explicit copy is the client issuing `PUT /ledger/commitments/{canonical id}` with `due`, which is the ledger route's own journalled write; `extra` carries both dues so the client can offer it.
  - `remove_alias(cid, ref) -> dict`: 404 `not_found` when absent. Journalled with the label `f"{describe(ref)} — unmerged from {describe(to)}"`.
  - `create_link(cid, a, b, relation, *, scene="", note="") -> dict` → `{"link": {id, a, b, relation, scene, note, created}, "given": {"a": a, "b": b}}`.
    - The endpoints are stored as their `live_canon` values at creation.
    - Refusals:
      - 400 `bad_ref`;
      - 400 `invalid_relation`;
      - 400 `self_link` when the canonical `a == b`;
      - 409 `unreadable`;
      - 404 `not_found` when an endpoint does not exist;
      - 409 `link_exists` when an effective link already has the same canonical triple;
      - 409 `malformed`.
    - Journalled under `{"w": "continuity_link", "id": lid}`, with the label `f"{describe(a)} {relation} {describe(b)}"`.
  - `remove_link(cid, lid) -> dict`: 404 when absent; journalled.
  - `merged_sources(cid, ref) -> list[str]`: sorted alias sources whose stored `to == ref`. **Strict:** it raises `doc.ContinuityError` when the file or the `aliases` section is malformed.
  - `forget_ref(cid, ref) -> list[str]`: removes every alias whose source or `to` is `ref`, and every link whose stored `a` or `b` is `ref`. Each removal is its own journalled write, labelled `"… — removed with deleted record"`. It returns the removed alias refs and link ids, and lets `doc.ContinuityError` propagate.

  Every public mutator wraps its body in `with locks.campaign_lock(cid):`.

- [ ] **Step 1: Write the failing tests**: one per refusal row in both tables, plus:
  - `test_create_alias_journals_and_undo_removes`;
  - `test_liveness_mismatch_then_accept`: an open `maras-map` into a closed `winifreds-chart` is refused with 409 and accepted with `accept_status_change=True`;
  - `test_alias_to_an_alias_source_is_stored_as_given_and_judged_on_final_canonical`;
  - `test_affected_lists_transitive_sources`;
  - `test_replace_is_one_journal_row_restoring_previous`;
  - `test_create_link_dedupes_against_effective`;
  - `test_self_link_refused_after_alias`;
  - `test_merged_sources_strict_on_malformed`;
  - `test_forget_ref_removes_aliases_and_links_and_journals_each`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `review.py`.**
- [ ] **Step 4: Run the new tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_review.py -q`. Expected: PASS.
- [ ] **Step 5: Run the guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_import_guard.py tests/test_lock_domain_guard.py -q`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): validated, journalled alias and link writes`

---

### Task 8: The continuity router

**Files:**
- Create: `routes/continuity.py`
- Modify:
  - `routes/models.py`;
  - `routes/__init__.py`: import `continuity`, add it to the `_compose` loop anywhere before `entities`, and add a docstring row `` ``continuity``  /campaigns/{cid}/continuity, aliases, links and review `` after `campaigns`.
- Test: `tests/test_continuity_routes.py`

**Interfaces:**
- Consumes: `review.*`, `effective.links`, `effective.diagnostics`, `effective.live_canon`, `effective.Ledgers`, `doc.read`, `doc.malformed`, `store.embed_space.resolve`. Imported as `from ..store.continuity import doc, effective, review`.
- Produces (under `/api`). The path parameter for links is named `lid`, which is already in `test_path_guard_store._FILL`.

  **`GET /campaigns/{cid}/continuity`** returns:

  ```
  {
    aliases: [{ref, to, canonical, title, to_title, created, source, note, dangling: bool, reason}],
    links: [effective link + {a_title, b_title}],
    raw_links: [{id, a, b, relation, scene, note, created, state: "ok" | "broken" | "hidden", reason}],
    suppressions: [{fingerprint, kind, refs, decision, created}],
    diagnostics,
    malformed,
    unreadable,
    matching: "basic" | "semantic"
  }
  ```

  - `canonical` is the `live_canon` value.
  - Records of any shape are reported, never raised on: a non-dict alias record has `to: ""` and `reason: "malformed_record"`.
  - `matching` is `"semantic"` iff `store.embed_space.resolve()` is non-None; any exception counts as `"basic"`.
  - The read holds `locks.best_effort_campaign_lock(cid)`.

  **Writes:**
  - `POST /campaigns/{cid}/continuity/aliases`, body `ContinuityAliasCreate{ref: str, to: str, replace: bool | None, accept_status_change: bool | None, note: str | None}`.
  - `DELETE /campaigns/{cid}/continuity/aliases?ref=<source ref>`.
  - `POST /campaigns/{cid}/continuity/links`, body `ContinuityLinkCreate{a: str, b: str, relation: str, scene: str | None, note: str | None}`.
  - `DELETE /campaigns/{cid}/continuity/links/{lid}`.

  `review.Refused` becomes `HTTPException(e.status, detail={"kind": e.kind, "detail": e.detail, **e.extra})`. An unknown campaign is a 404 on every route.

- [ ] **Step 1: Write the failing tests** (with fixtures copied from `test_ledger_routes.py`; threads are created through `POST /api/campaigns/{cid}/ledger/threads`):
  - `test_get_continuity_empty`.
  - `test_alias_round_trip_and_journal`: newest journal row `kind == "continuity_alias"`.
  - `test_alias_refusals_map_to_http`: checks `detail.kind`.
  - `test_delete_alias_ref_with_slash`: the Review Focus 2 case.
  - `test_links_round_trip`.
  - `test_get_continuity_survives_garbled_plot`: the Review Focus 3 case. It answers 200; `unreadable == ["plot"]`; no alias is dangling; `diagnostics` has all four keys.
  - `test_get_continuity_reports_non_dict_records`: a hand-edited alias `"thread:a": "x"` and link `"l1": 3` answer 200 and are reported as `malformed_record`.
  - `test_unknown_campaign_404_on_every_route`: all five routes.
  - `test_continuity_routes_not_captured_by_entities`: the response has `aliases`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the router and the models.**
- [ ] **Step 4: Run the new route tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_routes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the route and model guards.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_route_order.py tests/test_path_guard_store.py tests/test_pydantic_guard.py tests/test_routing_guard.py -q`. Expected: PASS. (The plan-gate review simulated the new routes against `test_route_order._table()` and found no crossings, so `CROSSING_PAIRS` should not change. If it does fail, stop and report rather than editing the pinned table.)
- [ ] **Step 6: Commit:** `feat(continuity): read and alias/link routes`

---

### Task 9: The ledger and event routes respect aliases

**Files:**
- Modify: `routes/ledger.py` (`put_thread`, `put_commitment`, `delete_thread`, `delete_commitment`); `routes/campaigns.py` (`delete_campaign_event`)
- Test: `tests/test_ledger_routes.py` (appended); `tests/test_continuity_routes.py`

**Interfaces:**
- Consumes: `effective.live_canon`, `review.merged_sources`, `review.forget_ref`, `review.describe`, `doc.malformed`. Imported as `from ..store.continuity import doc, effective, review`.
- Produces:
  - `PUT /ledger/threads/{pid}` and `PUT /ledger/commitments/{mid}` accept a query parameter `physical: bool = False`.
    - When it is false and `effective.live_canon(cid).get("thread:"+pid)` names another record, the write targets that record. The live resolver only maps to records that exist, so a dangling alias never redirects.
    - The journal label is `"<canonical title> — thread (via merged <source title>)"`.
    - The response is `{"ok": true, "id": <id written>}`.
  - `DELETE /ledger/threads/{pid}` and `DELETE /ledger/commitments/{mid}` accept a query parameter `force: bool = False`. Inside the existing `campaign_lock` hold, **before any write**:
    - if `set(doc.malformed(cid)) & {"file", "aliases", "links"}` → 409 `{kind: "malformed", detail}`;
    - if `review.merged_sources(cid, ref)` is non-empty and `force` is false → 409 `{kind: "has_merged_records", detail, refs}`.

    Then delete, then `review.forget_ref(cid, ref)` in the same hold.
  - `DELETE /events/{eid}` calls `review.forget_ref(cid, "event:"+eid)` inside its existing hold, after `forget_event`, wrapped in `contextlib.suppress(doc.ContinuityError)`. Its docstring gains one paragraph:
    - the reason is the same id reuse as notices;
    - a malformed continuity.json does not refuse an event delete, because no event can be an alias target, and any links left behind are reported as broken.

- [ ] **Step 1: Write the failing tests**
  - `test_put_thread_on_alias_source_writes_canonical`.
  - `test_put_thread_physical_flag_writes_source`.
  - `test_put_thread_on_dangling_alias_source_writes_source`: the Review Focus 4 case. `plot.get(cid, "gone") is None` afterwards.
  - `test_put_thread_on_wrong_type_alias_source_writes_source`: given the hand-edited alias `thread:a → commitment:b`, the PUT writes plot id `a` only.
  - `test_delete_alias_target_refused_then_forced`.
  - `test_delete_source_removes_its_alias_and_links`.
  - `test_delete_refused_when_continuity_malformed`: with continuity.json `"{ no"`, the DELETE answers 409 `malformed`, and the thread still exists.
  - `test_delete_event_removes_links_and_recreation_does_not_reattach`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the route changes.**
- [ ] **Step 4: Run the ledger and continuity route tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/test_ledger_routes.py tests/test_continuity_routes.py -q`. Expected: PASS.
- [ ] **Step 5: Run the event tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests -q -k event`. Expected: PASS.
- [ ] **Step 6: Commit:** `feat(continuity): ledger writes follow merges; deletes cascade to aliases and links`

**Interim behaviour, accepted for Slice A:** `GET /ledger` still lists alias-source rows physically, while their PUT redirects. Nothing in the UI can create an alias until Slice D, and Slice B switches `GET /ledger` to effective rows.

---

### Task 10: Slice gate

- [ ] **Step 1:** From the repo root, run `make check-lint check-mypy check-templates PY=$PWD/backend/.venv/bin/python`. Expected: PASS, with any new finding fixed in code.
- [ ] **Step 2:** Run `PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend -q`.
  - Expected: PASS, apart from the failures recorded on the base commit before this slice started.
  - `test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root, and is environmental.
- [ ] **Step 3:** Run `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`. Expected: PASS.
- [ ] **Step 4: Implementation → done.** Run `/codex:review` against the slice diff, or record an independent reviewer in its place when Codex is unavailable. Resolve the findings, then commit.
- [ ] **Step 5: Done → actually done.** Run `/codex:adversarial-review` against the slice diff **and** the spec, asking specifically whether the diff implements spec Slice A (§4, §5, §7.0–7.2, §8, §12.8, §21 read/alias/link), and looking for gaps, drift and quietly dropped requirements. Record a substitute reviewer if Codex is unavailable. Resolve the findings, then commit.

---

## Self-review notes

**Spec coverage (Slice A):**

| Spec section | Task |
|---|---|
| §5 shape, reader/writer split, version and unknown keys | 1 |
| §5.1 alias API | 7, 8 |
| §5.2 canonicalization (alias graph) | 3 |
| §7.1 live canonical | 4 |
| §5.3 link vocabulary | 4 (`RELATIONS`), 6, 7 |
| §5.4 link identity | 3, 4, 7 |
| §5.5 ids and fingerprints | 3 (functions; Slice D uses them) |
| §5.6 renames | 2 |
| §5.7 deletes | 9 |
| §5.8 fork residual | no code |
| §7.0 package layout and lock domain | Global Constraints, 1 |
| §7.1 identity law and merge | 4 |
| §7.2 ledger redirect | 9 (the absorb redirect belongs to Slice C, which rewrites materialization) |
| §8 involvement | 5 |
| §12.8 undo targets | 6 |
| §21 read, alias and link routes | 8 |

**Deliberate deviations from the spec text** (the spec is amended in the same commit as this plan revision):

- `forget_ref` lives in `continuity.review`, not `continuity.doc`. doc cannot import undo, and each removal is journalled.
- `create_alias` takes no due-copy flag. The explicit copy spec §5.1 describes is the client's separate `PUT /ledger/commitments/{id}`, which keeps every hand edit to the ledger inside `routes/ledger.py` (CLAUDE.md). Slice D's apply body decides whether `copy_due` survives there.
- `PUT /ledger/threads|commitments` responses gain `id`.
- Spec §7.1 gains the “Live canonical” definition that `effective.live_canon` implements.

**Plan-gate review (2026-10-05) resolution:**

| Finding | Resolution |
|---|---|
| 1 (two definitions of canonical) | `live_canon`, used for every current-state question; `canon.resolve` for the alias graph only |
| 2 (garbled plot breaks GET) | `Ledgers` with unknown existence; `unreadable` reported |
| 3 (hand-edited shapes) | sanitized beats; `fieldtext.text`; `exists` = is a dict; `resolve` stops at non-dicts; `malformed_record` |
| 4 (malformed file on delete) | 409 before any write; strict `merged_sources` |
| 5 (link restore check) | effective-equivalence walk, and a test that proves the right refusal |
| 6 (verification commands) | one command per purpose |
| 7 (final gate missing) | Task 10 Step 5 |
| 8 (`copy_due`) | dropped from Slice A |
| 9 (raw links) | `raw_links` in GET |
| 10 (self-collapsing placement) | under `broken_links`, per spec §26 |
| 11 (undocumented deviations) | listed above |
| 12 (self-link, unmerge label) | `self_link`, two-sided label |
| 13 (involvement tolerance) | per-source guards |
| 14 (submodule imports) | explicit submodule imports |
| 15 (test gaps) | the added tests in Tasks 2, 4, 6, 7, 8 |
| 16 (housekeeping) | the timeline and routes docstrings, the `.lower()` predicate, module-bound `scene_ids`, thread `due=""`, stored `version` kept, the interim note, worktree `PY` |

**Type consistency:**

- `effective.live_canon`, `effective.Ledgers`, `effective.RELATIONS` and `effective.is_live` are defined in Task 4, and used in Tasks 5–9.
- `review.Refused` is defined in Task 7 and used in Task 8.
- `doc.ContinuityError` is used in Tasks 3, 6, 7 and 9.
