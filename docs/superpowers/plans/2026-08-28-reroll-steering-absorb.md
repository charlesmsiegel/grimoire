# Reroll Steering Feeds the Absorb — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every reroll-guidance prompt to a per-scene sidecar and feed the log into the end-of-scene absorb prompt as a lore signal.

**Architecture:** A new `store/steering.py` owns a third per-scene sidecar (`<sid>.steering.json`), appended where `alternates.archive` parks the hint today (same lock hold, unconditional, consecutive-deduped, failsoft). The absorb prompt gains a `steering_snapshot` input rendered as a context block, with a system-prompt paragraph making the notes signal-never-evidence. No new absorb output keys, no UI changes.

**Tech Stack:** Python/FastAPI backend, Jinja2 templates, pytest. No frontend changes.

**Spec:** `docs/superpowers/specs/2026-08-28-reroll-steering-absorb-design.md`

## Global Constraints

- Invented placeholder names only (Seraphine, Mara, Winifred, Saltmarch) in every fixture, test, and doc; never describe real-store contents.
- All store writes through `store.atomic`; markers (`# atomic-ok:` etc.) only with real reasons.
- Imports at module scope, acyclic; inside `store/`, cross-package imports bind submodules (`from . import x`, then `x.f()`).
- pydantic-free store code (Android base deps unchanged).
- The absorb prompt must stay **byte-identical when the steering log is empty** — existing cassettes and evals must not need re-matching.
- Run tests as `cd backend && PYTHONPATH=src python -m pytest tests/<file> -x -q` (use the venv python if `backend/.venv` exists).
- Lint gates are ratcheted: new code lands clean; if a gate fails on an *improvement*, run `make baseline` and commit the smaller file.

---

### Task 1: `store/steering.py` core + path resolver + id budget

**Files:**
- Create: `backend/src/grimoire/store/steering.py`
- Modify: `backend/src/grimoire/store/scenes/paths.py` (add `_steering_path`, extend `_sid_taken`)
- Modify: `backend/src/grimoire/store/scene_ids.py` (`_LONGEST_SUFFIX`)
- Modify: `backend/src/grimoire/store/locks.py` (`DOMAIN_MODULES` += `"store.steering"`)
- Modify: `backend/src/grimoire/store/__init__.py` (import + registry list)
- Test: `backend/tests/test_steering_store.py`

**Interfaces:**
- Produces: `steering.record(cid: str, sid: str, text: str) -> None`, `steering.texts(cid: str, sid: str) -> list[str]`, `steering.MAX_STEERING_CHARS = 500`, `steering.STEERING_LIMIT = 100`, `scenes_paths._steering_path(cid, sid) -> Path`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_steering_store.py
"""The reroll-steering log: record/read, bounds, and tolerance."""
import json

from grimoire import store
from grimoire.store import steering
from grimoire.store.scenes import paths as scenes_paths


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = store.create_campaign("Saltmarch")["id"]
    sid = store.scenes.create_scene(cid, "Quay")["id"]
    return cid, sid


def test_record_appends_and_texts_reads_in_order(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "Mara already knows about the ledger")
    steering.record(cid, sid, "the east gate is barred at dusk")
    assert steering.texts(cid, sid) == [
        "Mara already knows about the ledger",
        "the east gate is barred at dusk"]


def test_empty_and_whitespace_record_nothing(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "")
    steering.record(cid, sid, "   ")
    assert steering.texts(cid, sid) == []
    assert not scenes_paths._steering_path(cid, sid).exists()


def test_consecutive_duplicate_is_one_entry(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "shorter")
    steering.record(cid, sid, "shorter")          # error-banner retry re-sends
    steering.record(cid, sid, "longer")
    steering.record(cid, sid, "shorter")          # non-consecutive: a new signal
    assert steering.texts(cid, sid) == ["shorter", "longer", "shorter"]


def test_clip_and_cap(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "x" * (steering.MAX_STEERING_CHARS + 50))
    assert steering.texts(cid, sid) == ["x" * steering.MAX_STEERING_CHARS]
    for i in range(steering.STEERING_LIMIT + 5):
        steering.record(cid, sid, f"note {i}")
    kept = steering.texts(cid, sid)
    assert len(kept) == steering.STEERING_LIMIT
    assert kept[-1] == f"note {steering.STEERING_LIMIT + 4}"   # newest kept
    assert kept[0] == "note 6"                                 # oldest dropped


def test_garbled_file_reads_empty_and_is_replaced(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    p = scenes_paths._steering_path(cid, sid)
    p.write_text("{not json", encoding="utf-8")
    assert steering.texts(cid, sid) == []
    steering.record(cid, sid, "fresh start")
    assert steering.texts(cid, sid) == ["fresh start"]
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == 1


def test_record_is_failsoft_on_oserror(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise OSError("disk says no")
    monkeypatch.setattr(steering.atomic, "write_text", boom)
    steering.record(cid, sid, "lost, and that is fine")   # must not raise
    assert steering.texts(cid, sid) == []


def test_sid_taken_counts_an_orphan_steering_sidecar(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "orphan-to-be")
    scenes_paths._scene_path(cid, sid).unlink()
    assert scenes_paths._sid_taken(cid, sid)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/test_steering_store.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'steering'`.

- [ ] **Step 3: Implement**

`backend/src/grimoire/store/scenes/paths.py` — after `_review_path`:

```python
def _steering_path(cid: str, sid: str) -> Path:
    """The scene's reroll-steering log (`store/steering.py`).

    The third per-scene sidecar, in `_review_path`'s classification: per-scene
    JSON a deleted scene must take with it, keyed by filename, reachable only
    through this resolver. Enumeration is unaffected — every scan of this
    directory globs `*.md`.
    """
    return _scenes_dir(cid) / f"{sid}.steering.json"
```

and extend `_sid_taken`'s return (its docstring's orphan argument covers the
new name: an adopted steering log would feed a fresh scene's absorb another
scene's corrections):

```python
    return (_scene_path(cid, sid).exists() or _alts_path(cid, sid).exists()
            or _review_path(cid, sid).exists()
            or _steering_path(cid, sid).exists())
```

`backend/src/grimoire/store/scene_ids.py` — extend the constant and its
comment (`.steering.json` is now the longest name an id wears):

```python
_LONGEST_SUFFIX = max(len(".alts.json"), len(".review.json"),
                      len(".steering.json"))
```

`backend/src/grimoire/store/steering.py` (new):

```python
"""The scene's reroll-steering log: every hint a regenerate carried, kept.

``<campaign>/scenes/<sid>.steering.json`` — the third per-scene sidecar
(`scenes.paths._steering_path`), and the durable half of the reroll hint.
`store/alternates.py` keeps the same string as a display label on the variant
it produced, lifecycle-bound to the trailing generation's set: the anchor
moves and it is gone. This log is the other consumer the spec names — the
end-of-scene absorb — and its lifetime is the scene's: every steering prompt
marks a place where the written lore and the player's intent disagreed hard
enough to interrupt play, which stays true long after the variant it steered
is dropped, superseded, or promoted away.

Append-only in use (`record` never rewrites an entry), consecutive-deduped
(the error banner's Retry re-sends a reroll with the same guidance, and a
player hammering reroll with one instruction is one signal), and bounded both
ways: entries clip at ``MAX_STEERING_CHARS`` for `alternates.MAX_GUIDANCE_CHARS`'s
wire-input reason, and the list trims oldest past ``STEERING_LIMIT`` — a
structural backstop against a pathological writer, not a packing policy, to
be tuned against real prompts if it ever bites.

``record`` is failsoft on ``OSError``: a steering row is an absorb hint, and
losing one must never fail the reroll that carries it. The concrete case is a
pre-cap store whose ``<sid>.md`` fits its directory entry and whose steering
sidecar name does not (ENAMETOOLONG — the tolerance
`scenes.lifecycle._unlink_sidecar` documents from the delete side). A garbled
file is replaced rather than raised on: whatever corrupted it already lost
its entries, and refusing to log new ones on top serves nobody.

Entries carry no transcript index or anchor on purpose: the absorb consumes
text and order, an index would renumber under cuts, and an anchor would drag
in the alternates' slot mathematics for a consumer that does not exist.
Readers treat entry keys as open, so a later field needs no migration.

The log is never cleared — not by a chronicle save, so a ``force`` re-absorb
is primed with the same notes the first absorb saw: a re-absorb redoes the
extraction, it does not forget the extraction's inputs. Only scene deletion
(and the rename fan-out) touch the file from outside.
"""

from __future__ import annotations

import json

from . import atomic, locks
from .paths import now_iso
from .scenes import paths as scenes_paths

SCHEMA = 1

#: How much of one steering prompt is kept. Same bound, same reason as
#: `alternates.MAX_GUIDANCE_CHARS`: `guidance` is an unbounded string on the
#: wire, and nothing else stops one request from parking megabytes in a file
#: the absorb reads whole. Clipped, not rejected — prose keeps most of its
#: meaning cut short.
MAX_STEERING_CHARS = 500

#: How many entries one scene keeps, oldest dropped first. A backstop against
#: a runaway file and a runaway prompt block (500 chars x 100 entries bounds
#: both), not a measured ceiling — an ordinary scene holds a handful.
STEERING_LIMIT = 100


def _read_raw(cid: str, sid: str) -> list[dict]:
    """The stored entries, or [] for absent/unreadable/malformed — the sidecar
    is a convenience beside the transcript and must never make a scene
    unopenable, or fail the absorb that reads it."""
    p = scenes_paths._steering_path(cid, sid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return []
    return [e for e in data["entries"]
            if isinstance(e, dict)
            and isinstance(e.get("text"), str) and e["text"]]


def record(cid: str, sid: str, text: str) -> None:
    """Log one reroll's guidance. No-op on empty text and on a repeat of the
    newest entry; failsoft on OSError (module docstring)."""
    text = (text or "").strip()[:MAX_STEERING_CHARS]
    if not text:
        return
    try:
        with locks.campaign_lock(cid):
            entries = _read_raw(cid, sid)
            if entries and entries[-1]["text"] == text:
                return
            entries.append({"text": text, "created": now_iso()})
            atomic.write_text(
                scenes_paths._steering_path(cid, sid),
                json.dumps({"v": SCHEMA, "entries": entries[-STEERING_LIMIT:]},
                           indent=2) + "\n")
    except OSError:
        pass


def texts(cid: str, sid: str) -> list[str]:
    """Entry texts, oldest first. [] for a scene with no log."""
    return [e["text"] for e in _read_raw(cid, sid)]
```

(If `now_iso` does not live in `store/paths.py`, import it from wherever
`pending_reviews.py`'s `from .paths import now_iso` resolves — copy that
exact import.)

`backend/src/grimoire/store/locks.py` — add to `DOMAIN_MODULES`, with the
reason in the file's own style:

```python
    # The steering log is a read-modify-write of one whole file, appended
    # beside `alternates.archive` inside the regenerate route's lock hold
    # (reentrant, so its own acquire is free) — two unserialized rerolls
    # would lose one of the two appends.
    "store.steering",
```

`backend/src/grimoire/store/__init__.py` — add `steering` to the module
import block (alphabetical, near `scene_refs`) and to the module-name list
that mirrors it (the list containing `"alternates"`, `"pending_reviews"`, …).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/test_steering_store.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_import_guard.py tests/test_paths_guard.py -q`
Expected: PASS (the guards accept the new module).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/steering.py backend/src/grimoire/store/scenes/paths.py backend/src/grimoire/store/scene_ids.py backend/src/grimoire/store/locks.py backend/src/grimoire/store/__init__.py backend/tests/test_steering_store.py
git commit -m "Steering log: the durable half of the reroll hint"
```

---

### Task 2: Lifecycle — delete, rename fan-out

**Files:**
- Modify: `backend/src/grimoire/store/steering.py` (add `drop_scene`, `repoint_scenes`)
- Modify: `backend/src/grimoire/store/scenes/lifecycle.py` (`delete_scene` unlinks the third sidecar; "The two per-scene sidecars" comment becomes "three")
- Modify: `backend/src/grimoire/store/scene_refs.py` (fan-out + docstring census)
- Test: `backend/tests/test_steering_store.py` (extend), `backend/tests/test_scene_refs.py` (extend `test_repoint_updates_every_store_that_holds_scene_ids`)

**Interfaces:**
- Consumes: Task 1's `steering.record`/`texts`/`_steering_path`.
- Produces: `steering.drop_scene(cid, sid) -> None`, `steering.repoint_scenes(cid, mapping: dict[str, str]) -> None` (same signatures as `alternates`' pair, which is what `scene_refs.repoint` calls).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_steering_store.py`:

```python
def test_delete_scene_takes_the_log_with_it(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "gone with the scene")
    store.scenes.delete_scene(cid, sid)
    assert not scenes_paths._steering_path(cid, sid).exists()


def test_rename_carries_the_log(monkeypatch, tmp_path):
    cid, sid = _campaign(monkeypatch, tmp_path)
    steering.record(cid, sid, "follows the rename")
    new_sid = store.scenes.rename_scene(cid, sid, "Quay At Night")
    assert new_sid != sid
    assert steering.texts(cid, new_sid) == ["follows the rename"]
    assert not scenes_paths._steering_path(cid, sid).exists()
```

In `backend/tests/test_scene_refs.py::test_repoint_updates_every_store_that_holds_scene_ids`,
seed and assert alongside the existing stores (import `steering` at the top
with the others):

```python
    steering.record(cid, old, "kept the correction")
    ...
    assert steering.texts(cid, "001--2026-07-04--s") == ["kept the correction"]
    assert steering.texts(cid, old) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/test_steering_store.py tests/test_scene_refs.py -x -q`
Expected: FAIL — rename leaves the file behind / `repoint_scenes` missing.

- [ ] **Step 3: Implement**

`steering.py` additions (mirror `pending_reviews`' pair, the simpler of the
two existing sidecar movers — read every source as **bytes** before writing
any target, clear every destination, tolerate missing files; read the actual
bodies of `pending_reviews.drop_scene`/`repoint_scenes` first and keep the
same structure, including moving unreadable content verbatim):

```python
def drop_scene(cid: str, sid: str) -> None:
    """Forget a deleted scene's log. `scenes.lifecycle.delete_scene` also
    unlinks the file directly (the sidecar ordering there is load-bearing);
    this is the fan-out's spelling for callers that only know the store."""
    with locks.campaign_lock(cid):
        try:
            scenes_paths._steering_path(cid, sid).unlink(missing_ok=True)
        except OSError:
            pass    # ENAMETOOLONG: a name that long cannot exist to delete


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids: carry the sidecar to its scene's new id.
    Bytes, not text, and every destination is cleared — `alternates.
    repoint_scenes` documents both reasons and this keeps its structure."""
    with locks.campaign_lock(cid):
        moving: dict[str, bytes] = {}
        for old, new in mapping.items():
            if old == new:
                continue
            p = scenes_paths._steering_path(cid, old)
            if p.exists():
                moving[new] = p.read_bytes()
        for old, new in mapping.items():
            if old != new:
                scenes_paths._steering_path(cid, new).unlink(missing_ok=True)
        for new, blob in moving.items():
            atomic.write_bytes(scenes_paths._steering_path(cid, new), blob)
        for old, new in mapping.items():
            if old != new and new not in mapping:
                scenes_paths._steering_path(cid, old).unlink(missing_ok=True)
```

**Adjust the code above to the real shape of the existing movers before
committing** — if `atomic` has no `write_bytes`, use what
`alternates.repoint_scenes` actually writes with; if the existing movers
handle the source-also-a-destination case differently, copy their handling
verbatim. The tests, not this sketch, are the contract.

`scenes/lifecycle.py` — in `delete_scene`, with the other two unlinks (before
the transcript's `p.unlink()`), and update the "two per-scene sidecars"
comment to three:

```python
    _unlink_sidecar(paths._steering_path(cid, sid))
```

`scene_refs.py` — `steering` joins the import block and the `repoint` tuple;
the module docstring's census gains it in the moved-not-rewritten group
("alternates and pending_reviews" becomes "alternates, pending_reviews and
steering", with the count updated to match however the sentence counts).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/test_steering_store.py tests/test_scene_refs.py tests/test_lock_domain_guard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/steering.py backend/src/grimoire/store/scenes/lifecycle.py backend/src/grimoire/store/scene_refs.py backend/tests/test_steering_store.py backend/tests/test_scene_refs.py
git commit -m "Steering log follows the scene: delete and rename"
```

---

### Task 3: Capture at the regenerate route

**Files:**
- Modify: `backend/src/grimoire/routes/scenes.py` (`_regenerate_run`, immediately after `store.alternates.archive(...)`)
- Test: extend the route-level regenerate coverage (find the existing guidance-bearing regenerate test — `grep -rn "guidance" backend/tests/test_routes.py backend/tests/test_alternates_store.py` — and add assertions in the same file/style)

**Interfaces:**
- Consumes: `steering.record(cid, sid, text)`.

- [ ] **Step 1: Write the failing test**

In the file that already drives `POST .../regenerate` with a `guidance` body
(same client/fake-LLM fixtures as its neighbors):

```python
def test_regenerate_guidance_lands_in_the_steering_log(client, ...):
    # ... existing scaffolding: campaign, scene, one landed turn ...
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/regenerate",
                    json={"guidance": "Mara already knows about the ledger"})
    assert r.status_code == 200
    # drain the stream the way the file's other regenerate tests do
    assert store.steering.texts(cid, sid) == [
        "Mara already knows about the ledger"]


def test_regenerate_without_guidance_logs_nothing(client, ...):
    # ... same scaffolding, body omitted ...
    assert store.steering.texts(cid, sid) == []
```

- [ ] **Step 2: Run to verify failure**

Expected: first test FAILS — `texts` returns `[]`.

- [ ] **Step 3: Implement**

In `_regenerate_run`, directly after the `store.alternates.archive(cid, sid,
guidance, ran_on)` line (inside the same lock hold):

```python
        # The durable half of the hint (store/steering.py): the alternates
        # copy above is a display label that dies with the set, this one is
        # what the end-of-scene absorb reads. Unconditional — a stream that
        # dies does not un-say the correction, and the error banner's Retry
        # re-sends the same guidance, which consecutive-dedupe absorbs.
        if guidance:
            store.steering.record(cid, sid, guidance)
```

- [ ] **Step 4: Run to verify pass**

Run the touched test file plus `tests/test_scene_freeze.py` (no new door was
opened — `record` mutates no scene shape — but the suite is one case per door
and cheap to confirm).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes/scenes.py backend/tests/<touched test file>
git commit -m "A reroll's guidance survives the reroll"
```

---

### Task 4: Absorb prompt input

**Files:**
- Modify: `backend/src/grimoire/store/absorb/snapshots.py` (`steering_snapshot`)
- Modify: `backend/src/grimoire/store/absorb/prompt.py` (`build_prompt` kwarg)
- Modify: `backend/src/grimoire/store/absorb/__init__.py` (re-export)
- Modify: `templates/absorb/user.j2` (head block + var docs comment)
- Modify: `templates/absorb/system.j2` (signal-never-evidence paragraph)
- Modify: `backend/src/grimoire/routes/scenes.py` (`_absorb_start` passes it)
- Test: `backend/tests/test_absorb_store.py` (extend)

**Interfaces:**
- Consumes: `steering.texts(cid, sid)`.
- Produces: `absorb.steering_snapshot(cid: str, sid: str) -> str`; `build_prompt(..., steering_snapshot: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_absorb_store.py`, following its existing
`build_prompt` test style:

```python
def test_build_prompt_with_steering_renders_the_block():
    msgs = absorb.build_prompt("You: hi", {},
                               steering_snapshot="- Mara already knows about the ledger")
    user = msgs[1]["content"]
    assert "Player steering notes" in user
    assert "- Mara already knows about the ledger" in user
    assert "Player steering notes" in msgs[0]["content"]   # the system contract


def test_build_prompt_without_steering_is_byte_identical():
    assert absorb.build_prompt("You: hi", {}) == \
        absorb.build_prompt("You: hi", {}, steering_snapshot=None)
    assert "Player steering notes" not in absorb.build_prompt("You: hi", {})[1]["content"]


def test_steering_snapshot_reads_the_log(monkeypatch, tmp_path):
    # campaign+scene scaffolding as the file's other snapshot tests do
    steering.record(cid, sid, "the east gate is barred at dusk")
    steering.record(cid, sid, "Winifred limps on the left side")
    assert absorb.steering_snapshot(cid, sid) == (
        "- the east gate is barred at dusk\n"
        "- Winifred limps on the left side")
    assert absorb.steering_snapshot(cid, "does-not-exist") == ""
```

And a route-level test beside the existing absorb route tests (fake LLM via
`llm_fakes`): drive a regenerate with guidance, then `POST .../absorb`, and
assert the captured request body (the fake records what it was sent) contains
`"Player steering notes"` and the guidance text.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/test_absorb_store.py -x -q`
Expected: FAIL — unexpected keyword / missing name.

- [ ] **Step 3: Implement**

`snapshots.py` (import `steering` in the existing `from .. import (...)`
block):

```python
def steering_snapshot(cid: str, sid: str) -> str:
    """Rendered reroll-steering notes, oldest first — feeds the prompt so the
    model checks the lore the player had to correct mid-scene. "" for a scene
    with no log; `steering.texts` is already tolerant of a garbled file, so
    this cannot fail the extraction."""
    return "\n".join(f"- {t}" for t in steering.texts(cid, sid))
```

`prompt.py` — add the trailing kwarg and pass it through:

```python
def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None, plot_snapshot: str | None = None,
                 group_snapshot: str | None = None,
                 commitment_snapshot: str | None = None,
                 fact_snapshot: str | None = None,
                 steering_snapshot: str | None = None) -> list[dict]:
    return [{"role": "system", "content": prompts.render("absorb/system.j2")},
            {"role": "user", "content": prompts.render(
                "absorb/user.j2", facts=facts, state_snapshot=state_snapshot,
                rel_snapshot=rel_snapshot, plot_snapshot=plot_snapshot,
                group_snapshot=group_snapshot,
                commitment_snapshot=commitment_snapshot,
                fact_snapshot=fact_snapshot,
                steering_snapshot=steering_snapshot, transcript=transcript)}]
```

`templates/absorb/user.j2` — document the var in the header comment
(`steering_snapshot — rendered "- <text>" reroll-steering lines,
"\n"-joined (see absorb.steering_snapshot) | None`) and add, after the
`fact_snapshot` block:

```jinja
{%- if steering_snapshot -%}{%- set _ = head.append("Player steering notes (what the player typed to regenerate replies in this scene, oldest first):\n" ~ steering_snapshot) -%}{%- endif -%}
```

`templates/absorb/system.j2` — insert this paragraph between the "Who said a
thing decides where it can go." paragraph and the final "Write in third
person" line:

```
The context block may include "Player steering notes" — the instructions the player typed to regenerate a reply mid-scene. Each one marks a place where the written record and the player's intent disagreed: a reply was corrected because a lore item was wrong, or was steered toward something no record covers. Treat them as pointers, not as story: check whether an existing record should be sharpened ("lore_edits"), a missing one written ("new_lore", "new_locations"), or a standing truth recorded ("facts"). A note about tone, pacing or length is not a lore signal and gets no edit. Steering notes are not part of the transcript: never cite one — "quote" and "speaker" always name the transcript itself, and an edit the transcript cannot support is left uncited rather than credited to the player's own instruction.
```

`absorb/__init__.py` — add `steering_snapshot` to the `from .snapshots
import (...)` list.

`routes/scenes.py` `_absorb_start` — add the ninth argument:

```python
        messages=store.absorb.build_prompt(
            transcript, facts,
            store.absorb.state_snapshot(cid, sid),
            store.absorb.relationships_snapshot(cid, sid),
            store.absorb.plot_snapshot(cid), store.absorb.group_snapshot(cid),
            store.absorb.commitment_snapshot(cid), store.absorb.fact_snapshot(cid, sid),
            store.absorb.steering_snapshot(cid, sid)),
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/test_absorb_store.py tests/test_llm_fakes.py tests/test_frozen_campaign.py -q`
(`test_llm_fakes` proves the cassette matchers still match the reworded
prompt; frozen campaign proves the sweep — which does not render the absorb
prompt — is untouched.)
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/absorb/ templates/absorb/ backend/src/grimoire/routes/scenes.py backend/tests/test_absorb_store.py
git commit -m "Absorb reads the steering log: signal, never evidence"
```

---

### Task 5: Template verifier, evals, docs

**Files:**
- Modify: `scripts/verify_templates.py` (absorb loop gains the ninth var + head-line assertion)
- Modify: `evals/cases.py` (`build_absorb` seeds steering; `_absorb_prompt` passes it; `grade_absorb` requires the instruction verbatim)
- Modify: `templates/README.md` (absorb var table)
- Test: the verifier and eval harness are the tests

**Interfaces:**
- Consumes: `steering.record`, `absorb.steering_snapshot`, `build_prompt(..., steering_snapshot=...)`.

- [ ] **Step 1: Extend the verifier (it fails first by construction)**

In `scripts/verify_templates.py`'s absorb loop, widen the tuples —
`("bare", {}, None, None, None, None, None, None, None)` and the `full` row
gains `"- Seraphine was told about the tail at the Night Dock"` — then:

```python
    exp = absorb.build_prompt(transcript, facts, st, rel, plt, grp, cmt, fct, strg)
    ...
          render("absorb/user.j2", facts=facts, state_snapshot=st, rel_snapshot=rel,
                 plot_snapshot=plt, group_snapshot=grp, commitment_snapshot=cmt,
                 fact_snapshot=fct, steering_snapshot=strg, transcript=transcript))
    if strg:
        assert "Player steering notes" in exp[1]["content"], \
            f"absorb user ({label}) missing Player steering notes head line"
```

- [ ] **Step 2: Extend the evals**

`evals/cases.py`:
- Import `steering` alongside the other store modules.
- `build_absorb`: after the transcript is seeded, add
  `steering.record(cid, sid, "Seraphine already knows Winifred took the ledger — she saw her.")`
- `_absorb_prompt`: append `absorb_store.steering_snapshot(cid, sid)` to the
  `build_prompt` call.
- `grade_absorb`: extend the `grade_prompt` dict with
  `{"asks_steering_contract": "Player steering notes"}` (merged into the
  existing comprehension's dict), so replay mode fails the day the system
  paragraph is dropped.

- [ ] **Step 3: Update `templates/README.md`**

Add `steering_snapshot` to the absorb `user.j2` variable list (mirror the
sibling lines' phrasing), and mention the new system-prompt paragraph in the
absorb section if the README enumerates its instructions.

- [ ] **Step 4: Run the harnesses**

Run: `cd backend && PYTHONPATH=src python -m pytest tests/ -q -k "eval or template"`,
then `python scripts/verify_templates.py` from the repo root (or however
`make check-templates` invokes it — copy the Makefile's exact command).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_templates.py evals/cases.py templates/README.md
git commit -m "Verifier and evals hold the steering block to the prompt"
```

---

### Task 6: The full gate

- [ ] **Step 1: Run `make check-py check-lint check-mypy check-templates`** (the backend-side gates; `check-web`/`check-eslint` touch nothing here but run them if time allows — CI runs everything).
- [ ] **Step 2: Fix anything red.** If a ratcheted gate reports an improvement (count went *down*), run `make baseline` and commit the smaller baseline with the fix.
- [ ] **Step 3: Commit any stragglers.**

---

## Verification checklist (spec → tasks)

- Durable per-scene log, third sidecar → Tasks 1–2
- Capture beside `alternates.archive`, unconditional, deduped, failsoft → Tasks 1, 3
- All of the scene's guidance, in order → Task 1 (`texts`)
- Absorb block + signal-never-evidence instruction, byte-identical when empty → Task 4
- Guards (`_sid_taken`, `_LONGEST_SUFFIX`, locks, fan-out, delete ordering) → Tasks 1–2
- Verifier, evals, README → Task 5
- No UI change, no parse/routing change, no director-note change → absent by design; Task 4 touches neither `parse.py` nor `chronicle.transcript_text`
