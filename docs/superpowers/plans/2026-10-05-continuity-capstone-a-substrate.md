# Continuity Capstone — Slice A: Canonical Continuity Substrate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `continuity.json` (aliases, links, suppressions) and the canonical projections over plot threads and commitments, with journalled, reversible alias/link writes and the routes to manage them. No consumer changes behaviour yet.

**Architecture:** A new package `backend/src/grimoire/store/continuity/`, split into leaf IO (`doc`), pure identity helpers (`canon`), read projections (`effective`, `involvement`) and journalled orchestration (`review`). The split keeps `test_import_guard`'s module graph acyclic, given that `scene_refs` and `undo` must import the IO module. One new router, `routes/continuity.py`, is composed before `entities`. The existing ledger routes gain alias redirection and a delete cascade.

**Tech Stack:** Python 3.11, FastAPI, pydantic (v1/v2-agnostic), pytest with `TestClient`.

**Spec:** `docs/superpowers/specs/2026-10-04-continuity-capstone-design.md`. This plan implements spec §31 Slice A, and the requirements it names in §4, §5 (all of it), §7.0–§7.2, §8, §12.8 and §21 (the alias/link/read routes). Later slices (B–G) get their own plans; nothing here switches a prompt, ledger, Todo or shell consumer to the effective projections. That is Slice B.

## Global Constraints

- Privacy: test fixtures and docstrings use only the placeholder names Seraphine, Mara, Winifred, Realm and Saltmarch, or invented slugs such as `missing-map`. No real store content.
- Every store write goes through `store.atomic` (`test_atomic_guard`), and every path goes through `campaigns.paths.campaign_root(cid)` (`test_paths_guard`).
- JSON is written as `json.dumps(data, indent=2, sort_keys=True) + "\n"`.
- `store/continuity/` imports are at module scope, and the graph stays acyclic. A cross-package import binds a submodule (`from ..campaigns import paths as campaigns_paths`), never a name (`test_import_guard`).
- `store/continuity/__init__.py` contains only a docstring: no imports.
- Any module that writes continuity.json goes into `locks.DOMAIN_MODULES`, and each of its public `cid`-taking mutators uses `with locks.campaign_lock(cid):`. `UNREVIEWED` must not grow.
- Pydantic bodies use plain `BaseModel` fields with `| None = None` defaults: no `Field`, validators, `ConfigDict` or unions (`test_pydantic_guard`).
- Refusals use `HTTPException(status, detail={"kind": ..., "detail": ...})`.
- **Identity law** (spec §7.1): for a campaign with no aliases, `effective.threads(cid, include_closed=X) == [dict(r, aliases=[]) for r in plot.open_threads(cid, include_closed=X)]`, and the same for commitments. This holds byte for byte, in the same order.
- Lint ratchet: `make check-lint` and `make check-mypy` must not grow `lint-baselines/*.json`. A new finding is fixed, not baselined.
- Run backend tests from `backend/` as `PYTHONPATH=src .venv/bin/python -m pytest <paths> -q`.

## Review Focus

1. **A hand-edited alias cycle** (`thread:a → thread:b → thread:a` written directly into continuity.json): `canonical_ref` terminates and returns the input ref, `effective.threads` shows both records, and `diagnostics.dangling_aliases` lists both entries. Pinned in Task 3 and Task 4.
2. **Plot ids containing `/` or `:`** (model-written ids are arbitrary strings): `split_ref` splits on the first `:`, and `DELETE /continuity/aliases?ref=thread:a/b` reaches the alias. Pinned in Task 3 and Task 8.
3. **A garbled plot.json** (`{ no`): `effective.threads` raises exactly as `plot.open_threads` does, so callers' existing tolerance still applies. `GET /continuity` answers 200 with titles falling back to ids. Pinned in Task 4 and Task 8.
4. **An alias whose canonical record was removed outside the cascade** (the undo of a create): the source is shown as its own effective record, and the alias is listed as dangling. Pinned in Task 4.
5. **Undoing an alias deletion after the reverse alias was created** (A→B deleted, then B→A created, then the first deletion is undone): this is refused with 409, not a cycle on disk and not a 500. Pinned in Task 6.

---

## File structure

| File | Responsibility |
|---|---|
| `store/continuity/__init__.py` | Package docstring only |
| `store/continuity/doc.py` | continuity.json IO; primitive mutators; `repoint_scenes`; restores used by undo |
| `store/continuity/canon.py` | Ref parsing, canonicalization, link ids, candidate ids, fingerprints (pure, plus `doc` reads) |
| `store/continuity/effective.py` | Effective thread/commitment records and rows, effective links, diagnostics |
| `store/continuity/involvement.py` | Touched scenes, the stage history (moved from briefing), and `of(cid, refs)` |
| `store/continuity/review.py` | Validated, journalled alias/link create/remove; `forget_ref`; `merged_sources`; `describe` |
| `store/briefing.py` | Calls `involvement.touched_scenes` / `involvement.stage_history` (no behaviour change) |
| `store/undo.py` | Two new targets: `continuity_alias`, `continuity_link` |
| `store/scene_refs.py` | Adds `continuity.doc` to the fan-out |
| `store/locks.py` | `store.continuity.doc` in `DOMAIN_MODULES` |
| `store/__init__.py`, `tests/store_api_baseline.json` | Facade gains `continuity` |
| `routes/continuity.py` | `GET /continuity`; alias and link POST/DELETE |
| `routes/models.py` | `ContinuityAliasCreate`, `ContinuityLinkCreate` |
| `routes/__init__.py` | Compose `continuity` before `entities` |
| `routes/ledger.py` | Alias redirect on PUT; `has_merged_records` and the cascade on DELETE |
| `routes/campaigns.py` | Event DELETE cascades into `forget_ref` |

All backend paths are relative to `backend/src/grimoire/`, and tests to `backend/tests/`.

---

### Task 1: `continuity.doc` — the file, its reader/writer split, and primitive mutators

**Files:**
- Create: `store/continuity/__init__.py`, `store/continuity/doc.py`
- Modify: `store/locks.py` (`DOMAIN_MODULES`), `store/__init__.py` (import + `__all__`), `tests/store_api_baseline.json` (regenerate deliberately)
- Test: `tests/test_continuity_doc.py`

**Interfaces:**
- Produces:
  - `SECTIONS = ("aliases", "links", "suppressions")`
  - `class ContinuityError(Exception)`
  - `read(cid) -> dict`: tolerant. Always returns `{"version": 1, "aliases": {}, "links": {}, "suppressions": {}, **unknown_top_level_keys}`, with any malformed section replaced by `{}`.
  - `malformed(cid) -> list[str]`: the section names that are malformed, plus `"file"` when the whole file is unparseable or not a dict.
  - `get_alias(cid, ref) -> dict | None`, `get_link(cid, lid) -> dict | None`
  - `put_alias(cid, ref, record: dict)`, `drop_alias(cid, ref) -> dict | None`
  - `put_link(cid, lid, record: dict)`, `drop_link(cid, lid) -> dict | None`
  - `put_suppression(cid, fp, record: dict)`, `drop_suppression(cid, fp) -> dict | None`

  Every mutator takes `campaign_lock`, reads strictly, and raises `ContinuityError` when the file or the touched section is malformed. Every mutator preserves unknown top-level keys and record fields.

- [ ] **Step 1: Write the failing tests** in `tests/test_continuity_doc.py`. Copy the `client` and `cid` fixtures from `tests/test_ledger_routes.py`: `GRIMOIRE_HOME` set to `tmp_path`, `importlib.reload(store)`, then a world and a campaign created through the API. Later store-level test files in this plan reuse the same two fixtures. Tests:
  - `test_absent_file_reads_empty`: `read(cid) == {"version": 1, "aliases": {}, "links": {}, "suppressions": {}}` and `malformed(cid) == []`.
  - `test_unparseable_file_reads_empty_and_refuses_writes`: write `"{ no"` to `campaign_root(cid)/"continuity.json"`. Then `read` is empty, `malformed(cid) == ["file"]`, and `put_alias(...)` raises `ContinuityError`. The file text is unchanged afterwards.
  - `test_one_malformed_section_reads_empty_others_survive`: the file is `{"aliases": [], "links": {"l1": {...}}, "suppressions": {}}`. Then `read(cid)["aliases"] == {}`, `read(cid)["links"]` keeps `l1`, `malformed(cid) == ["aliases"]`, `put_link` succeeds, and `put_alias` raises `ContinuityError`.
  - `test_unknown_keys_preserved`: the file has top-level `"extra": 1` and an alias record field `"x": 2`. After `put_link(...)` both are still on disk.
  - `test_put_and_drop_round_trip`: for each of the three section pairs, put then drop returns the record, and a second drop returns `None`.
  - `test_written_json_is_sorted_and_newline_terminated`.

- [ ] **Step 2: Run the tests and confirm they fail**

  Run: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_doc.py -q`
  Expected: errors on `ModuleNotFoundError: grimoire.store.continuity`.

- [ ] **Step 3: Implement `store/continuity/doc.py`**

  - Path: `campaigns_paths.campaign_root(cid) / "continuity.json"`.
  - Model the reader on `events.read` and the strict mutation read on `events._mutable`. The reader catches `OSError`, `UnicodeDecodeError` and `json.JSONDecodeError`, and a non-dict top level reads as empty.
  - A section is malformed when it is present and not a dict.
  - Each mutator does `with locks.campaign_lock(cid):` → strict read → modify one key → `atomic.write_text`.

  Add `"store.continuity.doc"` to `DOMAIN_MODULES` with a comment stating why: continuity.json is rewritten whole, so two unlocked read-modify-writes lose one.

  Add `continuity` to the `from . import (...)` block and to `__all__` in `store/__init__.py`.

- [ ] **Step 4: Run the tests and confirm they pass**, along with the guards this touches

  Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_continuity_doc.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_import_guard.py -q`
  Expected: PASS.

- [ ] **Step 5: Regenerate the store facade baseline deliberately**

  Run, from `backend/`:

  ```bash
  PYTHONPATH=src .venv/bin/python -c "import json, grimoire.store as s; print(json.dumps({'all': sorted(s.__all__), 'dir': sorted(n for n in dir(s) if not n.startswith('_'))}, indent=2))" > tests/store_api_baseline.json
  ```

  Then run `git diff tests/store_api_baseline.json`. Expected: the only additions are `continuity` in both lists. Then run `pytest tests/test_store_api_baseline.py -q`, which must PASS. If the diff shows anything else, stop and investigate rather than committing.

- [ ] **Step 6: Commit**

  `git commit -m "feat(continuity): add continuity.json store with tolerant reader and strict writer"`

---

### Task 2: Scene renames reach continuity.json

**Files:**
- Modify: `store/continuity/doc.py`, `store/scene_refs.py` (import, the fan-out tuple, and the docstring's store count and list)
- Test: `tests/test_continuity_doc.py`

**Interfaces:**
- Produces: `doc.repoint_scenes(cid, mapping: dict[str, str]) -> None`. It is tolerant: an unparseable file or a malformed `links` section is skipped silently, as in `commitments.repoint_scenes`. It rewrites `links[*].scene` only, and writes only when something was hit.

- [ ] **Step 1: Write the failing tests**
  - `test_repoint_rewrites_link_scene`: a link with `scene: "003--old"`, followed by `repoint_scenes(cid, {"003--old": "003--new"})`, leaves `get_link(...)["scene"] == "003--new"`.
  - `test_repoint_skips_unparseable_file`: the file holds `"{ no"`. `repoint_scenes` does not raise, and the file is unchanged.
  - `test_scene_refs_fans_out_to_continuity`: `store.scene_refs.repoint(cid, {...})` rewrites the link's scene.

- [ ] **Step 2: Run them and confirm they fail.**

- [ ] **Step 3: Implement `repoint_scenes`.** Add `continuity_doc` to `scene_refs.repoint`'s tuple, imported as `from .continuity import doc as continuity_doc`. Update the docstring: “Twenty-one stores” becomes “Twenty-two stores”, the list gains “continuity (each reviewed link's `scene`)”, and the `usage` paragraph says “twenty-third”.

- [ ] **Step 4: Run** `pytest tests/test_continuity_doc.py tests/test_import_guard.py tests/test_scene_refs*.py -q` (glob whatever exists). Expected: PASS.

- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): follow scene renames in reviewed links"`

---

### Task 3: `continuity.canon` — refs, canonicalization, ids and fingerprints

**Files:**
- Create: `store/continuity/canon.py`
- Test: `tests/test_continuity_canon.py`

**Interfaces:**
- Consumes: `doc.read`, `doc.ContinuityError`.
- Produces:
  - `split_ref(ref: str) -> tuple[str, str]`: splits on the FIRST `:`. Raises `ValueError` if `ref` is not a str, has no `:`, or either part is empty.
  - `ref_kind(ref) -> str`. The prefix table maps:

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

    An unknown prefix raises `ValueError`.
  - `PREFIX = {"thread": "thread", "commitment": "commitment", "event": "event", ...}`: kind→prefix, the inverse of the table above. It is the one mapping §4 asks for.
  - `actor_ref(token: str) -> str`: `"characters/x"` → `"characters:x"`. A colon form passes through unchanged.
  - `resolve(aliases: dict, ref: str, *, strict: bool = False) -> str`: pure. It follows `aliases[ref]["to"]` transitively. On a cycle, the lenient form returns the input `ref` and the strict form raises `doc.ContinuityError`. A record whose `to` is not a str stops the walk there.
  - `canonical_ref(cid, ref, *, strict=False) -> str` and `canonical_refs(cid, refs, *, strict=False) -> dict[str, str]`. These read `doc.read(cid)["aliases"]` once. The lenient form returns the input on any read failure. The strict form raises `ContinuityError` when `doc.malformed(cid)` names `file` or `aliases`.
  - `link_id(relation: str, a: str, b: str) -> str`: `"l" + sha256(f"{relation}\0{a}\0{b}".encode()).hexdigest()[:20]`, with `(a, b)` sorted first when `relation == "related_to"`.
  - `candidate_id(kind: str, refs: list[str]) -> str`: `f"{kind}-" + sha256("\0".join(sorted(refs)).encode()).hexdigest()[:16]`.
  - `pair_fingerprint(kind: str, sides: list[dict]) -> str`. Each side is `{ref, title, status, kind, due}`; missing keys read as `""`.
  - `lifecycle_fingerprint(kind: str, ref: str, status: str, beat_count: int, latest_beat: str, due: str, temporal_link_ids: list[str]) -> str`.

  Both fingerprints are `"fp1_" + sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()`, with the payloads exactly as spec §5.5 defines them: sides sorted by ref, and temporal link ids sorted.

- [ ] **Step 1: Write the failing tests**
  - `test_split_ref_uses_first_colon`: `split_ref("birthday:characters:mara:740000") == ("birthday", "characters:mara:740000")`, and `split_ref("thread:a/b") == ("thread", "a/b")`. `split_ref("nocolon")`, `split_ref(":x")` and `split_ref("thread:")` each raise `ValueError`.
  - `test_actor_ref_converts_slash_form`.
  - `test_resolve_transitive`: `{"thread:a": {"to": "thread:b"}, "thread:b": {"to": "thread:c"}}` resolves `thread:a` to `thread:c`.
  - `test_resolve_cycle_lenient_returns_input_and_strict_raises`: with `thread:a↔thread:b`, the lenient form returns `"thread:a"` and the strict form raises `ContinuityError`.
  - `test_canonical_ref_unreadable_file_returns_input`.
  - `test_link_id_symmetric_only_for_related_to`: `link_id("related_to", "thread:a", "event:x") == link_id("related_to", "event:x", "thread:a")`, and `link_id("pays_off", a, b) != link_id("pays_off", b, a)`.
  - `test_candidate_id_order_independent`.
  - `test_pair_fingerprint_ignores_side_order_and_beats`: the same sides in reversed order give an equal fingerprint, while changing one side's `title` gives a different one.
  - `test_lifecycle_fingerprint_moves_with_beat_count_and_latest_beat_only`: changing `beat_count` or `latest_beat` changes the fingerprint, and the function takes no `last_scene` at all (assert that the signature has no such parameter, via `inspect.signature`).

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `canon.py`.**
- [ ] **Step 4: Run** `pytest tests/test_continuity_canon.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): canonical refs, link and candidate ids, fingerprints"`

---

### Task 4: `continuity.effective` — merged records under the identity law, effective links, diagnostics

**Files:**
- Create: `store/continuity/effective.py`
- Test: `tests/test_continuity_effective.py`

**Interfaces:**
- Consumes: `canon.resolve`, `canon.split_ref`, `canon.link_id`, `doc.read`, `plot.read`, `plot.open_threads`, `commitments.read`, `commitments.open_commitments`, `events.read`, `scene_ids.parse_sid`.
- Produces:
  - `records(cid, kind: str) -> dict[str, dict]`, where `kind` is `"thread"` or `"commitment"`.
    - It is keyed by canonical ref and includes closed/resolved groups.
    - Each value: `{ref, id, title, status, kind?, due?, beats: [{scene, text}], last_scene, latest_beat, aliases: [{ref, title, status}], members: [physical id, ...]}`. `kind` and `due` are present for commitments only.
    - It raises whatever `plot.read` / `commitments.read` raise.
  - `threads(cid, include_closed=False) -> list[dict]` and `commitments(cid, include_resolved=False) -> list[dict]`. These are the `open_threads` / `open_commitments` row keys plus `aliases`.
  - `links(cid) -> list[dict]`: effective links, each `{id, relation, a, b, a_raw, b_raw, scene, note, created}`, where `a` and `b` are canonical.
  - `diagnostics(cid) -> dict`: `{dangling_aliases: [{ref, to, reason}], broken_links: [{id, reason}], hidden_links: [{id, reason}]}`.
    - `reason` for a dangling alias ∈ `missing_target` | `missing_source` | `cycle` | `wrong_type`.
    - `reason` for a broken or hidden link ∈ `missing_endpoint` | `self_collapsing` | `duplicate` | `invalid_relation`.

**Which aliases are live.** An alias `src → to` is **live** iff all of these hold:

- `canon.resolve(aliases, src)` differs from `src`, meaning it is not cycle-degraded;
- the source and the resolved target share a prefix, and that prefix is `thread` or `commitment`;
- both physical records exist.

Every other alias is dangling, and its source is shown as its own record. Live alias members are ordered by sorted ref.

**Beat merge algorithm** (used only for groups with at least one live alias; this is the part the signatures do not determine):

```python
def _merge_beats(members: list[list[dict]]) -> list[dict]:
    # members[0] is the canonical's stored beats; the rest are aliases in sorted-ref order.
    # Split each member's list into runs: a run starts at a beat whose scene
    # parse_sid() accepts, and carries the uncomparable beats that follow it.
    # Leading uncomparable beats form a run whose key is -1.
    runs = []
    for m_index, beats in enumerate(members):
        current_key, current = -1, []
        for beat in beats:
            parsed = parse_sid(beat["scene"]) if isinstance(beat.get("scene"), str) else None
            if parsed is not None:
                if current:
                    runs.append((current_key, m_index, len(runs), current))
                current_key, current = parsed["number"], [beat]
            else:
                current.append(beat)
        if current:
            runs.append((current_key, m_index, len(runs), current))
    runs.sort(key=lambda r: (r[0], r[1], r[2]))   # stable: play order, then member order, then input order
    out, seen = [], {}
    for key, m_index, _, run in runs:
        for beat in run:
            sig = (beat.get("scene"), beat.get("text"))
            if sig in seen and seen[sig] != m_index:
                continue                      # an exact duplicate ACROSS records only
            seen.setdefault(sig, m_index)
            out.append(beat)
    return out
```

The effective `last_scene` is the comparable scene with the greatest `parse_sid(...)["number"]` among every member's physical `last_scene` and every merged beat's scene. Ties go to the canonical member's value. If nothing is comparable, use the canonical's physical `last_scene`. `latest_beat` is the text of the merged list's last beat, or `""`.

**Rows.**

- Build rows from `plot.open_threads(cid, include_closed=True)` (or `commitments.open_commitments(cid, include_resolved=True)`) and add `aliases: []` to each.
- For each canonical with live aliases: replace its row's `last_scene` / `latest_beat` with the merged values, set `aliases` to the members' `{ref: "thread:<id>", title, status}`, and drop the alias-source rows.
- Apply the open filter on the canonical row's status, with exactly the predicate `open_threads` / `open_commitments` use.
- Re-sort by `(last_scene, id)`.

When no alias is live, the result is exactly the physical rows with `aliases: []`. That is the identity law.

**Links.**

- Canonicalize each stored link's endpoints with `canon.resolve`.
- A link is broken if an endpoint's prefix or relation is outside spec §5.3's table (`invalid_relation`), or the canonical endpoint's record is missing (`missing_endpoint`; threads and commitments are checked in plot/commitments, events in `events.read`).
- A link is hidden if its endpoints canonicalize to one ref (`self_collapsing`), or if it repeats the `(relation, a, b)` of a link kept earlier in sorted-id order (`duplicate`; `related_to` endpoints are sorted first).

The §5.3 table is the constant `RELATIONS: dict[str, tuple[frozenset[str], frozenset[str], bool]]`, mapping relation → (allowed a kinds, allowed b kinds, directional). It lives in `effective.py` and Task 7 reuses it.

- [ ] **Step 1: Write the failing tests.** Build fixtures through `store.plot.set_movement`, `store.commitments.set_movement` and `doc.put_alias` / `doc.put_link`, with ids such as `missing-map` and `lost-chart`, and scene ids such as `001--saltmarch`, `002--tribunal` and `003--ferry`.
  - `test_identity_law_threads`: the fixture includes a status-only `set_movement` whose scene `003--ferry` is newer than every beat, beats appended out of scene order, and a closed thread. Assert `effective.threads(cid) == [dict(r, aliases=[]) for r in plot.open_threads(cid)]` and the same with `include_closed=True`.
  - `test_identity_law_commitments`: the same shape for commitments.
  - `test_alias_hides_source_and_merges_beats`: `missing-map` (beats at `001`, `003`) aliased into `lost-chart` (a beat at `002`). The open rows show one row, `id == "lost-chart"`, `aliases == [{"ref": "thread:missing-map", "title": ..., "status": ...}]`, and a `latest_beat` equal to the `003` beat text. `records(cid, "thread")["thread:lost-chart"]["beats"]` is in scene order `001, 002, 003`.
  - `test_duplicate_beat_dropped_across_records_only`: the same `(scene, text)` in both members appears once. Two identical beats within one record both survive.
  - `test_uncomparable_beat_keeps_position_after_predecessor`: a beat with `scene: ""` stays right after the beat it followed.
  - `test_effective_last_scene_includes_status_only_moves`.
  - `test_canonical_status_is_authoritative_and_filters`: the canonical is closed and the alias is open, so `threads(cid)` has neither row.
  - `test_transitive_alias_merges_into_final_target`.
  - `test_missing_target_degrades_source_to_own_record`: an alias to `thread:gone` leaves the source visible, and `diagnostics(cid)["dangling_aliases"][0]["reason"] == "missing_target"`.
  - `test_cycle_degrades_both`: the Review Focus 1 case.
  - `test_wrong_type_alias_ignored`: `thread:a → commitment:b` is not live, and is reported as `wrong_type`.
  - `test_removing_alias_restores_two_records`.
  - `test_links_canonicalize_dedupe_and_hide`: two `related_to` links in opposite order are both stored, and `links()` returns one. After aliasing endpoint a into b, a `continues` link between them is hidden as `self_collapsing`. A link to a missing event is broken with `missing_endpoint`. A `pays_off` from a commitment to a thread is broken with `invalid_relation`.
  - `test_garbled_plot_raises_like_open_threads`: plot.json holds `"{ no"`, and `effective.threads(cid)` raises the same exception type as `plot.open_threads(cid)`.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `effective.py`.**
- [ ] **Step 4: Run** `pytest tests/test_continuity_effective.py tests/test_import_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): effective thread/commitment projections under the identity law"`

---

### Task 5: `continuity.involvement` — move briefing's join, then generalise it

**Files:**
- Create: `store/continuity/involvement.py`
- Modify: `store/briefing.py`. Delete `_touched_scenes` and `_stage_history`, and call the moved functions. The docstrings move with them.
- Test: `tests/test_continuity_involvement.py`. The existing `tests/test_briefing*.py` must pass **unmodified**.

**Interfaces:**
- Consumes: `effective.records`, `chronicle.read_chronicle`, `appearances.cast.roster`, `canon.resolve`, `canon.actor_ref`, `doc.read`.
- Produces:
  - `touched_scenes(records) -> dict[str, set[str]]`: the moved `briefing._touched_scenes`, byte-for-byte in behaviour.
  - `stage_history(cid, refs: set[str]) -> dict[str, set[str]]`: the moved `briefing._stage_history`. Refs stay in `"<kind>/<id>"` form.
  - `scene_actors(cid) -> dict[str, set[str]]`: scene id → actor refs in **colon** form, from roster ∪ chronicle cast. It is tolerant like `stage_history`.
  - `of(cid, refs: list[str]) -> dict[str, dict]`: `{input_ref: {"actors": sorted list, "scenes": sorted list}}`.
    - It handles only `thread:` and `commitment:` refs. Any other ref maps to `{"actors": [], "scenes": []}`.
    - Scenes are the canonical group's merged beat scenes ∪ every member's `last_scene`, minus `""`.
    - It takes no lock.

- [ ] **Step 1: Run the briefing tests first** to record that they are green before the move.

  Run: `PYTHONPATH=src .venv/bin/python -m pytest tests -q -k briefing`
  Expected: PASS.

- [ ] **Step 2: Move the two functions** into `involvement.py` (public names, unchanged bodies), and point `briefing.build` at them. Re-run `-k briefing`. Expected: PASS, with no test file edited.

- [ ] **Step 3: Write the failing tests for the generalisation**
  - `test_of_unions_roster_and_chronicle`: `characters/mara` is in a chronicle cast for `001--saltmarch`, and `pcs/seraphine` is in the appearances roster for `002--tribunal`. A thread with beats in both gives `of(cid, ["thread:missing-map"])["thread:missing-map"]["actors"] == ["characters:mara", "pcs:seraphine"]`.
  - `test_of_aggregates_alias_group`: an alias source's beat scene contributes when either the source ref or the canonical ref is asked for, and the output is keyed by the ref asked for.
  - `test_of_includes_last_scene_without_beat`.
  - `test_of_tolerates_garbled_chronicle`: the roster actors still appear.
  - `test_of_unknown_kind_is_empty`.

- [ ] **Step 4: Implement `scene_actors` and `of`.** Run `pytest tests/test_continuity_involvement.py -q -k "involvement or briefing"` and `tests/test_import_guard.py`. Expected: PASS.

- [ ] **Step 5: Commit**

  `git commit -m "refactor(briefing): move the involvement join into continuity.involvement and generalise it"`

---

### Task 6: Undo targets for aliases and links, with validated restores

**Files:**
- Modify: `store/continuity/doc.py` (add the restores), `store/undo.py` (`read_value` / `write_value` branches and the import)
- Test: `tests/test_continuity_undo.py`

**Interfaces:**
- Produces:
  - `doc.restore_alias(cid, ref, value: dict | None) -> None`.
    - `None` drops the alias.
    - A dict must have a str `to`. The ref's and the target's prefixes must be equal and in {`thread`, `commitment`}, and `ref != to`.
    - It must not create a cycle: check with `canon`-equivalent logic, **re-implemented inside doc as a private `_reaches(aliases, start, target)`**, because doc may not import canon.
    - Otherwise it raises `ContinuityError`.
  - `doc.restore_link(cid, lid, value: dict | None) -> None`.
    - `None` drops the link.
    - A dict needs str `a`, `b` and `relation`, with the relation's prefix triple allowed. Use a private copy of the prefix table, kept in sync by a test against `effective.RELATIONS`.
    - It refuses (raises `ContinuityError`) when another stored link already has the same `link_id`-equivalent triple, with `related_to` sorted.
  - `undo.read_value` / `undo.write_value` handle these targets:
    - `{"w": "continuity_alias", "ref": ...}` → `doc.get_alias` / `doc.restore_alias`;
    - `{"w": "continuity_link", "id": ...}` → `doc.get_link` / `doc.restore_link`.

    In `write_value`, a `doc.ContinuityError` is re-raised as `UndoConflict(str(exc))`.

- [ ] **Step 1: Write the failing tests**
  - `test_undo_alias_create_and_redo`: journal a create through `store.undo.journalled(cid, {"w": "continuity_alias", "ref": "thread:a"}, kind="continuity_alias", ref={"kind": "continuity_alias", "id": "thread:a"}, field="alias", label="A → merged into B")` around `doc.put_alias`. `store.undo.undo(cid, jid)` removes the alias, and undoing the reversal puts it back.
  - `test_undo_restore_refuses_cycle`: the Review Focus 5 case. Expect `pytest.raises(store.undo.UndoConflict)`, and continuity.json still has exactly one alias, `B→A`.
  - `test_undo_link_restore_refuses_equivalent`: undoing the deletion of a `related_to` link after the reverse-order equivalent link was created raises `UndoConflict`.
  - `test_restore_link_table_matches_effective_relations`: doc's private table equals `effective.RELATIONS`, compared as prefix sets.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement** the restores and the two undo branches. `undo.py` imports `from .continuity import doc as continuity_doc`.
- [ ] **Step 4: Run** `pytest tests/test_continuity_undo.py tests/test_import_guard.py tests -k undo -q`. Expected: PASS.
- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): journalled aliases and links are undoable with validated restores"`

---

### Task 7: `continuity.review` — validated, journalled alias and link writes, and the cascade helpers

**Files:**
- Create: `store/continuity/review.py`
- Test: `tests/test_continuity_review.py`

**Interfaces:**
- Consumes: `doc.*`, `canon.*`, `effective.records`, `effective.RELATIONS`, `undo.journalled`, `plot.get`, `commitments.get`, `commitments.set_movement`, `events.read`, `locks.campaign_lock`.
- Produces:
  - `class Refused(Exception)`, with `.status: int`, `.kind: str`, `.detail: str` and `.extra: dict`.
  - `describe(cid, ref) -> str`: the record's title, an event's name, or the ref itself. It never raises.
  - `create_alias(cid, ref, to, *, replace=False, accept_status_change=False, copy_due=False, note="", source="manual") -> dict`. Returns `{"alias": {ref, to, created, source, note}, "affected": [refs]}`. Refusals:

    | Condition | Status / kind |
    |---|---|
    | unparseable ref | 400 `bad_ref` |
    | self alias | 400 `self_alias` |
    | prefix mismatch, or a prefix outside thread/commitment | 400 `wrong_type` |
    | source or target physically missing | 404 `not_found` |
    | resolving `to` reaches `ref` | 409 `alias_cycle` |
    | source already aliased and not `replace` | 409 `alias_exists`, `extra={"to": current}` |
    | live-ness of the source's physical status ≠ the target group's effective status, and not `accept_status_change` | 409 `liveness_mismatch`, `extra={"source": {status, kind, due}, "canonical": {status, kind, due}}` |
    | `doc.ContinuityError` | 409 `malformed` |

    - Liveness means thread status casefold ≠ `closed`, and commitment status casefold ∉ `commitments.RESOLVED`.
    - `affected` = the other alias sources whose resolution passed through `ref` before the write.
    - `copy_due=True` (commitments only, when the source due is non-empty and the canonical's is empty) then makes a second journalled write: `commitments.set_movement(canon_id, "", "", "", source_due, "", stored_last_scene)` under the `{"w": "commitment", "id": canon_id}` target.
    - Journalled with the target `{"w": "continuity_alias", "ref": ref}`, `kind="continuity_alias"`, `ref={"kind": "continuity_alias", "id": ref}`, `field="alias"`, and `label=f"{describe(ref)} → merged into {describe(to)}"`.
  - `remove_alias(cid, ref) -> dict`: 404 `not_found` if absent. Journalled with the label `f"{describe(ref)} — unmerged"`.
  - `create_link(cid, a, b, relation, *, scene="", note="") -> dict`. Returns `{"link": {id, a, b, relation, scene, note, created}}`. Refusals: 400 `bad_ref` / `invalid_relation` (per `effective.RELATIONS`); 404 `not_found` for a missing endpoint; 409 `link_exists` when an effective link already canonicalizes to the same triple. Endpoints are canonicalized before storing; that is stored “as given at creation”, which at creation is canonical. Journalled under `{"w": "continuity_link", "id": lid}`.
  - `remove_link(cid, lid) -> dict`: 404 if absent; journalled.
  - `merged_sources(cid, ref) -> list[str]`: the alias sources whose `to == ref` directly, sorted.
  - `forget_ref(cid, ref) -> list[str]`: removes every alias whose source or `to` is `ref`, and every link with `a == ref` or `b == ref`, each through its own journalled removal labelled `"… — removed with deleted record"`. Returns the removed alias refs and link ids. A `doc.ContinuityError` propagates.

  Every public mutator wraps its body in `with locks.campaign_lock(cid):`.

- [ ] **Step 1: Write the failing tests**, one per row of the refusal table above, plus:
  - `test_create_alias_journals_and_undo_removes`;
  - `test_liveness_mismatch_then_accept`: an open `missing-map` into a closed `lost-chart` gets 409, then succeeds with `accept_status_change=True`;
  - `test_copy_due_writes_explicit_movement`: the canonical's `due` equals the source's, and the journal has two new rows;
  - `test_affected_lists_transitive_sources`;
  - `test_replace_is_one_journal_row_restoring_previous`;
  - `test_create_link_dedupes_against_effective`;
  - `test_forget_ref_removes_aliases_and_links_and_journals_each`.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement `review.py`.**
- [ ] **Step 4: Run** `pytest tests/test_continuity_review.py tests/test_import_guard.py tests/test_lock_domain_guard.py -q`. Expected: PASS.
- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): validated, journalled alias and link writes"`

---

### Task 8: The continuity router

**Files:**
- Create: `routes/continuity.py`
- Modify: `routes/models.py`, `routes/__init__.py` (add `continuity` to the import list and to the `_compose` loop, anywhere before `entities`)
- Test: `tests/test_continuity_routes.py`, `tests/test_route_order.py` (only if it reports a new crossing that needs pinning)

**Interfaces:**
- Consumes: Task 7's `review.*`, `effective.links` / `diagnostics`, `doc.read` / `malformed`, `store.embed_space.resolve`.
- Produces (all under `/api`):
  - `GET /campaigns/{cid}/continuity` → `{aliases: [{ref, to, canonical, title, to_title, created, source, note, dangling: bool}], links: [effective link + {a_title, b_title}], suppressions: [{fingerprint, kind, refs, decision, created}], diagnostics, malformed, matching: "basic" | "semantic"}`.
    - `matching` is `"semantic"` iff `store.embed_space.resolve()` is non-None. Any exception from it reads as `"basic"`.
    - Titles come from `review.describe`, so a garbled plot.json falls back to ids and still answers 200.
    - The read holds `locks.best_effort_campaign_lock(cid)`.
  - `POST /campaigns/{cid}/continuity/aliases`, body `ContinuityAliasCreate{ref: str, to: str, replace: bool | None, accept_status_change: bool | None, copy_due: bool | None, note: str | None}` → 200 with `review.create_alias`'s result.
  - `DELETE /campaigns/{cid}/continuity/aliases?ref=<source ref>` → 200 `{ok: true}`.
  - `POST /campaigns/{cid}/continuity/links`, body `ContinuityLinkCreate{a: str, b: str, relation: str, scene: str | None, note: str | None}` → 200.
  - `DELETE /campaigns/{cid}/continuity/links/{lid}` → 200.

  `review.Refused` becomes `HTTPException(e.status, detail={"kind": e.kind, "detail": e.detail, **e.extra})`. An unknown campaign answers 404, using the same check as `routes/ledger._campaign_or_404`.

- [ ] **Step 1: Write the failing tests**, with the `TestClient` fixture and a `cid` fixture copied from `tests/test_ledger_routes.py`. Create threads through `POST /api/campaigns/{cid}/ledger/threads`.
  - `test_get_continuity_empty`: 200, with empty lists, `malformed == []` and `matching == "basic"`.
  - `test_alias_round_trip_and_journal`: POST alias → 200; GET shows it; `GET /journal`'s newest row has `kind == "continuity_alias"`; DELETE `?ref=` → 200; GET shows none.
  - `test_alias_refusals_map_to_http`: self → 400 `self_alias`; missing → 404; cycle → 409 `alias_cycle`, with `detail.kind` checked.
  - `test_delete_alias_ref_with_slash`: the Review Focus 2 case. Write a plot id containing `/` directly with `store.plot.set_movement`, then alias it and delete it by query.
  - `test_links_round_trip`.
  - `test_get_continuity_survives_garbled_plot`: the Review Focus 3 case.
  - `test_continuity_routes_not_captured_by_entities`: `GET /api/campaigns/{cid}/continuity` returns the continuity shape (has `aliases`), not an entity listing.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the router and the models.**
- [ ] **Step 4: Run** `pytest tests/test_continuity_routes.py tests/test_route_order.py tests/test_pydantic_guard.py tests/test_routing_guard.py -q`. Expected: PASS. If `test_route_order` reports an unpinned crossing between `/campaigns/{cid}/continuity/...` and an existing pattern, add it to that test's pinned table, with a one-line comment naming why continuity wins.
- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): read and alias/link routes"`

---

### Task 9: The ledger and event routes respect aliases

**Files:**
- Modify: `routes/ledger.py` (`put_thread`, `put_commitment`, `delete_thread`, `delete_commitment`), `routes/campaigns.py` (`delete_campaign_event`)
- Test: `tests/test_ledger_routes.py` (new tests appended), `tests/test_continuity_routes.py`

**Interfaces:**
- Consumes: `canon.canonical_ref`, `review.merged_sources`, `review.forget_ref`, `review.describe`.
- Produces:
  - `PUT /ledger/threads/{pid}` and `PUT /ledger/commitments/{mid}` accept a query `physical: bool = False`. When false and `canonical_ref(cid, "thread:"+pid)` differs, the write targets the canonical id. The journal label is `"<canonical title> — thread (via merged <source title>)"`, and the response is `{"ok": true, "id": <id written>}`.
  - `DELETE /ledger/threads/{pid}` and `DELETE /ledger/commitments/{mid}` accept a query `force: bool = False`. If `review.merged_sources(cid, ref)` is non-empty and not `force`, they answer 409 `{kind: "has_merged_records", detail, refs}` before any write. Otherwise they delete, then call `review.forget_ref(cid, ref)` inside the **same** `campaign_lock` hold.
  - `DELETE /events/{eid}` calls `review.forget_ref(cid, "event:"+eid)` inside its existing hold, after `forget_event`, wrapped in `contextlib.suppress(store.continuity.doc.ContinuityError)`. The docstring gains one paragraph stating why: it is the same id-reuse reason as notices.

- [ ] **Step 1: Write the failing tests**
  - `test_put_thread_on_alias_source_writes_canonical`: after aliasing `missing-map` into `lost-chart`, PUT a beat to `/threads/missing-map`. `plot.get(cid, "lost-chart")` has the beat, and `missing-map` is untouched.
  - `test_put_thread_physical_flag_writes_source`.
  - `test_delete_alias_target_refused_then_forced`: 409 `has_merged_records` with `refs == ["thread:missing-map"]`. With `?force=true` it answers 200, the alias is gone, and the journal holds both the plot deletion and the alias removal.
  - `test_delete_source_removes_its_alias_and_links`.
  - `test_delete_event_removes_links`: a `commitment --before--> event` link is removed when the event is deleted. Recreating an event with the same name does not re-attach it: `GET /continuity` has no links.

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement the route changes.**
- [ ] **Step 4: Run** `pytest tests/test_ledger_routes.py tests/test_continuity_routes.py tests -k "event" -q`. Expected: PASS.
- [ ] **Step 5: Commit**

  `git commit -m "feat(continuity): ledger writes follow merges; deletes cascade to aliases and links"`

---

### Task 10: Slice gate

**Files:** none new, unless a gate asks for one. Possibly: `lint-baselines/*.json` (only if a finding was *resolved*, per CLAUDE.md's ratchet two-step) and `CONTRIBUTING.md` (only if a guard or marker was added; none is planned).

- [ ] **Step 1: Run the fast gates**

  Run: `make check-lint check-mypy check-templates PY=$PWD/backend/.venv/bin/python` (from the repo root)
  Expected: all three pass. A new ruff or mypy finding in `store/continuity/` or `routes/continuity.py` is fixed in code.

- [ ] **Step 2: Run the backend suite**

  Run: `PYTHONPATH=backend/src backend/.venv/bin/python -m pytest backend -q`
  Expected: PASS, apart from failures already present on the base commit. Record the baseline's failing set before starting this slice. `test_atomic.py::test_a_read_only_record_is_not_silently_replaced` fails when run as root and is environmental.

- [ ] **Step 3: Run the pydantic-1 suite**

  Run: `make check-pydantic1 PY=$PWD/backend/.venv/bin/python`
  Expected: PASS. This catches a v2-only pydantic idiom in `routes/models.py`.

- [ ] **Step 4: Review**

  Run `/codex:review` against the slice diff, or record an independent Claude review in its place if Codex is unavailable. Resolve the findings, then commit.

---

## Self-review notes

- **Spec coverage, Slice A:**

  | Spec section | Task |
  |---|---|
  | §5 shape and reader/writer split | 1 |
  | §5.1 alias API | 7, 8 |
  | §5.2 canonicalization | 3 |
  | §5.3 link vocabulary | 4 (`RELATIONS`), 7 |
  | §5.4 link identity | 3, 4, 7 |
  | §5.5 ids and fingerprints | 3 (functions only; candidates use them in Slice D) |
  | §5.6 renames | 2 |
  | §5.7 deletes | 9 |
  | §5.8 fork residual | no code; documented in the spec |
  | §7.0 package layout and lock domain | 1, plus every task |
  | §7.1 identity law and merge | 4 |
  | §7.2 ledger redirect | 9 (the absorb redirect is in Slice C, which rewrites materialization) |
  | §8 involvement | 5 |
  | §12.8 undo targets | 6 |
  | §21 read, alias and link routes | 8 |

- **Deferred to later slices:**
  - Slice B: §7.3 consumer switching, `GET /ledger` effective rows (§12.5), and the frozen-campaign sweep additions.
  - Slice D: suppression routes and candidate apply.
- **Type consistency:** `effective.RELATIONS` is defined in Task 4 and used in Tasks 6 and 7. `review.Refused` is defined in Task 7 and used in Task 8. `canon.resolve` is used in Tasks 4 and 5. `doc.ContinuityError` is used in Tasks 3, 6, 7 and 9.
