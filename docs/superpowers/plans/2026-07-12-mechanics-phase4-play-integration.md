# Mechanics Phase 4 — Play Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LLM-refereed, engine-resolved checks in play: mechanics context sections, the roll fence with durable/idempotent proposals, `store/checks.py` resolution with outcome tiers, the proposal chip, and manual sheet-driven checks.

**Architecture:** A `FenceWatcher` inside `_chat_stream` cuts generation at a ` ```roll ` fence and records a durable proposal (`store/proposals.py`, CAS state machine pending→resolving→resolved/declined→narrated, superseded) **before** the pre-fence narration persists. `store/checks.py` resolves checks purely (no writes); the accept route commits resolution then idempotently projects the roll log + 🎲 line. Three new context sections feed the LLM rules, sheet summaries, and the roll protocol. Spec: `docs/superpowers/specs/2026-07-12-mechanics-phase4-play-integration-design.md` (Codex-hardened; the state machine and commit/projection split are load-bearing — do not simplify them).

**Tech Stack:** FastAPI + pytest, Vite/React + vitest. Pure stdlib for new store modules.

## Global Constraints

- **Privacy:** invented names only (Mara, Seraphine, Realm, warden/medium fixtures).
- **Android rules:** new store modules pure stdlib; route models plain pydantic scalars dumped via `routes._dump`.
- **Never-raise:** `proposals` reads and `FenceWatcher` never raise on malformed content; `resolve_check` raises only `CheckError` (and propagated CampaignNotFound); `resolve_check` performs **no writes** (RNG draw only).
- **Proposal state machine (spec, binding):** claim is an atomic CAS `pending→resolving` under a per-campaign lock; `resolve_check` runs only after a won claim; any exception post-claim reverts to `pending`; commit writes `resolved`+resolution before any roll-log/transcript write; projection is idempotent via the proposal id on the roll entry; new sends/fences supersede every non-`narrated` state.
- **Route ordering:** new scene-scoped routes sit with the existing `/campaigns/{cid}/scenes/{sid}/...` handlers (near `post_scene_roll`, routes.py:2350), before the generic `{kind}` catch-alls (comment at routes.py:2369).
- **Context:** all three new sections render empty when `modules.resolve(cid)` is None; `scripts/verify_templates.py::gather()` must mirror every new `_assemble` data key or its byte-for-byte check fails.
- **Tests:** APPEND-only to shared test files; backend `PYTHONPATH=backend/src /c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q` (worktree); frontend FROM `frontend/`: `npx vitest run`, `npx tsc -b`.
- **Worktree:** branch `mechanics-phase4-play` at `.worktrees/mechanics-phase4-play` (branched from `mechanics-phase4-spec` so the spec/plan ride along); frontend needs its own `npm install`.
- **Codex gates (CLAUDE.md):** this plan passes `/codex:adversarial-review` before implementation; the finished diff passes `/codex:review` before merge.
- Commit per task; trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## GitHub issues (for landing)

- **Resolves #162** (pre-roll proposals with accept/modify/decline). Closing comment: durable idempotent proposals (codex-hardened), outcome tiers, manual Check mode.
- **Comment:** #163 (Phase 5 is now unblocked: roll log + tier labels + proposal records exist to validate narration against).

## File structure

- Create: `backend/src/grimoire/store/proposals.py`, `store/checks.py`, `store/fence.py`; tests `backend/tests/test_proposals_store.py`, `test_checks_store.py`, `test_fence.py`
- Modify: `store/modules.py` (outcomes/_defaults/reserved-names validation, `read_rule`), `store/rolls.py` (`proposal` tag), `store/context.py` (+3 sections), `templates/scene/system.j2` (+3 includes), new `templates/scene/sections/mechanics_{rules,sheets,response_format}.j2`, `templates/scene/roll_result.j2`, `templates/scene/roll_declined.j2`, `routes.py`, `scripts/verify_templates.py`, both `builtin_modules/*/checks.json`
- Frontend: create `components/RollProposal.tsx` (+test); modify `api/stream.ts`, `api/client.ts`, `routes/CampaignView.tsx` (+test)

---

### Task 1: `store/proposals.py`

**Files:**
- Create: `backend/src/grimoire/store/proposals.py`
- Test: `backend/tests/test_proposals_store.py`

**Interfaces (produced):**
- `NON_TERMINAL = ("pending", "resolving", "resolved", "declined")`
- `new(cid, sid, payload: dict) -> dict` (record; supersedes any existing non-terminal record for the scene)
- `get(cid, sid) -> dict | None`
- `transition(cid, sid, pid, from_states, to, resolution=None) -> bool` — **every state change is this CAS**: writes only when the record has that id AND current status ∈ `from_states`; `resolution`, when given, replaces the stored one in the same write. Returns False otherwise — **a lost transition means another actor moved the record (e.g. supersede mid-resolve); callers must stop dead.**
- `claim(cid, sid, pid) -> bool` = `transition(cid, sid, pid, ("pending",), "resolving")`
- `supersede(cid, sid) -> None` (any non-terminal → `superseded`; takes the same lock new sends and commits use)
- `commit_narration(cid, sid, pid, persist) -> bool` — holding the per-campaign lock: re-validate id + status ∈ (`resolved`,`declined`); **crash recovery**: if the record already has `narration_intent`, `scenes.truncate_messages(cid, sid, intent)` first (discard a previous attempt's partial continuation); write `narration_intent = <current message count>` (atomic replace); invoke `persist()`; write `narrated`. Returns False (nothing persisted, no trim of foreign text — the intent marker guarantees everything past it is our own) when validation fails — a supersede that landed mid-stream wins and the streamed text is dropped.
- `locked(cid)` — a reentrant contextmanager over the per-campaign lock (the locks are `threading.RLock`, so `transition`/`commit_narration` may be called inside it). **The route's whole projection sequence runs inside `locked(cid)`** so concurrent resolved-retries serialize.
- Ids: `"pr-" + uuid.uuid4().hex` (full 122 random bits — probabilistically unique; that is what lets a proposal-tag match on a roll entry be treated as proof; never phrase this as "impossible"). `_write` is atomic: temp file in the same directory + `os.replace`.
- Also modify: `backend/src/grimoire/store/scenes.py` gains `trim_continuation(cid, sid, from_index) -> None` — remove messages at index ≥ `from_index` **except `ROLL_SPEAKER` messages, which are preserved in order** (the trim-safety rule: the only non-superseding writers in a crash window are roll/check lines; our own continuation segments never carry `ROLL_SPEAKER`). Rewrite the transcript via the existing serialization round-trip (read scenes.py first). Test: build a scene with [intent-point, continuation-partial, 🎲 manual roll line], trim from the intent → the roll line survives, the partial is gone.
- Record: `{"id": "pr-000001", "status", "payload", "created", "resolution"}`; file `<campaign>/proposals.json` maps sid → record plus a reserved `"_counter"` key (scene ids are slugs; the underscore name cannot collide).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_proposals_store.py
import threading

import pytest

from grimoire.store import campaigns, proposals, worlds


def _scene(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid)
    return cid, "s1"


def test_new_get_roundtrip_and_unique_ids(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    r1 = proposals.new(cid, sid, {"check": "brawl"})
    assert r1["id"].startswith("pr-") and len(r1["id"]) == 35
    assert r1["status"] == "pending"
    assert proposals.get(cid, sid)["payload"] == {"check": "brawl"}
    r2 = proposals.new(cid, "s2", {"check": "stealth"})
    assert r2["id"] != r1["id"]


def test_new_supersedes_previous_pending(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    proposals.new(cid, sid, {"check": "brawl"})
    r2 = proposals.new(cid, sid, {"check": "stealth"})
    assert proposals.get(cid, sid)["id"] == r2["id"]


def test_claim_cas(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    assert proposals.claim(cid, sid, rec["id"]) is True
    assert proposals.get(cid, sid)["status"] == "resolving"
    assert proposals.claim(cid, sid, rec["id"]) is False      # not pending anymore
    assert proposals.claim(cid, sid, "pr-999999") is False    # wrong id


def test_claim_concurrent_single_winner(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    wins = []
    def racer():
        if proposals.claim(cid, sid, rec["id"]):
            wins.append(1)
    threads = [threading.Thread(target=racer) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1


def test_transition_cas_and_resolution(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    assert proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved",
                                {"tier": "success"}) is True
    got = proposals.get(cid, sid)
    assert got["status"] == "resolved" and got["resolution"]["tier"] == "success"
    # wrong expected state, wrong id: both lose without writing
    assert proposals.transition(cid, sid, rec["id"], ("pending",), "declined") is False
    assert proposals.transition(cid, sid, "pr-999999", ("resolved",), "narrated") is False
    assert proposals.get(cid, sid)["status"] == "resolved"


def test_supersede_during_resolve_wins(monkeypatch, tmp_path):
    """The critical race: a new send supersedes while an accept holds the
    claim — the commit CAS must lose and the record must stay superseded."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    assert proposals.claim(cid, sid, rec["id"]) is True
    proposals.supersede(cid, sid)                      # new send lands mid-resolve
    assert proposals.get(cid, sid)["status"] == "superseded"
    assert proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved",
                                {"tier": "success"}) is False
    assert proposals.get(cid, sid)["status"] == "superseded"
    assert proposals.get(cid, sid)["resolution"] is None


def test_supersede_covers_every_non_terminal_state(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    for status in ("pending", "resolving", "resolved", "declined"):
        rec = proposals.new(cid, sid, {})
        if status != "pending":
            proposals.claim(cid, sid, rec["id"])
            if status == "resolved":
                proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved")
            elif status == "declined":
                proposals.transition(cid, sid, rec["id"], ("resolving",), "pending")
                proposals.transition(cid, sid, rec["id"], ("pending",), "declined")
        proposals.supersede(cid, sid)
        assert proposals.get(cid, sid)["status"] == "superseded"
    # narrated is terminal: untouched
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved")
    proposals.transition(cid, sid, rec["id"], ("resolved",), "narrated")
    proposals.supersede(cid, sid)
    assert proposals.get(cid, sid)["status"] == "narrated"


def test_malformed_file_never_reuses_ids(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    old = proposals.new(cid, sid, {})
    (campaigns.campaign_root(cid) / "proposals.json").write_text("{nope", encoding="utf-8")
    assert proposals.get(cid, sid) is None
    fresh = proposals.new(cid, sid, {})
    assert fresh["id"] != old["id"]          # uuid ids: corruption can't re-mint


def test_commit_narration_atomicity(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved", {"tier": "success"})
    persisted = []
    assert proposals.commit_narration(cid, sid, rec["id"],
                                      lambda: persisted.append(1)) is True
    assert persisted == [1]
    assert proposals.get(cid, sid)["status"] == "narrated"


def test_commit_narration_drops_after_supersede(monkeypatch, tmp_path):
    """The continuation-vs-supersede race: text streamed for a proposal that
    got superseded mid-stream must never persist."""
    cid, sid = _scene(monkeypatch, tmp_path)
    rec = proposals.new(cid, sid, {})
    proposals.claim(cid, sid, rec["id"])
    proposals.transition(cid, sid, rec["id"], ("resolving",), "resolved", {})
    proposals.supersede(cid, sid)            # a new send lands mid-stream
    persisted = []
    assert proposals.commit_narration(cid, sid, rec["id"],
                                      lambda: persisted.append(1)) is False
    assert persisted == []                   # nothing written
    assert proposals.get(cid, sid)["status"] == "superseded"


def test_write_is_atomic_replace(monkeypatch, tmp_path):
    cid, sid = _scene(monkeypatch, tmp_path)
    proposals.new(cid, sid, {})
    # no temp litter and the file parses after every operation
    root = campaigns.campaign_root(cid)
    assert [p.name for p in root.glob("proposals.json*")] == ["proposals.json"]
```

- [ ] **Step 2: Run to verify failure** (`ImportError: proposals`).

Run: `PYTHONPATH=backend/src /c/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_proposals_store.py -q`

- [ ] **Step 3: Implement**

```python
# backend/src/grimoire/store/proposals.py
"""Durable roll proposals (#162, mechanics Phase 4).

One record per scene in ``<campaign>/proposals.json``. State machine:
pending -> resolving (claimed) -> resolved, or pending -> declined;
resolved/declined -> narrated; superseded is terminal. Every state change
is a compare-and-set under a per-campaign lock; ``commit_narration``
persists a continuation and marks narrated atomically so a supersede that
lands mid-stream drops the stale text. Ids are uuid-based (a rebuilt file
can never re-mint an old id); writes are atomic via temp-file + replace.
Reads never raise on malformed content.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase4-play-integration-design.md.
"""

from __future__ import annotations

import json
import os
import threading
import uuid

from . import campaigns
from .paths import now_iso

NON_TERMINAL = ("pending", "resolving", "resolved", "declined")

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(cid: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(cid, threading.RLock())


@contextmanager
def locked(cid: str):
    """Reentrant per-campaign lock; the route's projection sequence runs
    inside this so concurrent resolved-retries serialize."""
    with _lock(cid):
        yield


def _path(cid: str):
    return campaigns.campaign_root(cid) / "proposals.json"


def _read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(cid: str, data: dict) -> None:
    p = _path(cid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def new(cid: str, sid: str, payload: dict) -> dict:
    with _lock(cid):
        data = _read(cid)
        rec = {"id": f"pr-{uuid.uuid4().hex}", "status": "pending",
               "payload": payload, "created": now_iso(), "resolution": None}
        data[sid] = rec
        _write(cid, data)
        return rec


def get(cid: str, sid: str) -> dict | None:
    rec = _read(cid).get(sid)
    return rec if isinstance(rec, dict) else None


def transition(cid: str, sid: str, pid: str, from_states, to: str,
               resolution: dict | None = None) -> bool:
    """Atomic CAS: move the scene's proposal to ``to`` only if it carries
    exactly this id and its status is in ``from_states``. Every state
    change goes through here; a lost transition means another actor moved
    the record (e.g. a supersede mid-resolve) and the caller must stop."""
    with _lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if (not isinstance(rec, dict) or rec.get("id") != pid
                or rec.get("status") not in tuple(from_states)):
            return False
        rec["status"] = to
        if resolution is not None:
            rec["resolution"] = resolution
        _write(cid, data)
        return True


def claim(cid: str, sid: str, pid: str) -> bool:
    """CAS pending -> resolving; resolve_check may run only after a win."""
    return transition(cid, sid, pid, ("pending",), "resolving")


def supersede(cid: str, sid: str) -> None:
    """A new send or a newer fence retires any non-narrated proposal."""
    with _lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if isinstance(rec, dict) and rec.get("status") in NON_TERMINAL:
            rec["status"] = "superseded"
            _write(cid, data)


def commit_narration(cid: str, sid: str, pid: str, persist) -> bool:
    """Persist a streamed continuation and mark narrated, crash-recoverably.

    Holding the campaign lock: re-validate the record still carries this
    id in a committable state; if a previous attempt left a
    ``narration_intent``, trim the scene back to it (everything past the
    intent is our own partial continuation — the marker is written before
    any append); record a fresh intent; run ``persist()`` (the caller's
    _persist_reply closure); write narrated. A supersede that landed while
    the continuation streamed wins here: validation fails, nothing
    persists. The lock is held only around trim + persist — never while
    the LLM streams.
    """
    from . import scenes  # function-level: avoid import-order surprises
    with _lock(cid):
        data = _read(cid)
        rec = data.get(sid)
        if (not isinstance(rec, dict) or rec.get("id") != pid
                or rec.get("status") not in ("resolved", "declined")):
            return False
        intent = rec.get("narration_intent")
        if isinstance(intent, int):
            scenes.trim_continuation(cid, sid, intent)
        rec["narration_intent"] = len(scenes.read_scene(cid, sid)["messages"])
        _write(cid, data)
        persist()
        rec["status"] = "narrated"
        _write(cid, data)
        return True
```

(Verify `scenes.read_scene(cid, sid)` is the real read API — the research
notes name it; adjust if the signature differs. `from contextlib import
contextmanager` joins the imports.)

- [ ] **Step 4: Run tests** — all PASS. **Step 5: Commit**

```bash
git add backend/src/grimoire/store/proposals.py backend/tests/test_proposals_store.py
git commit -m "feat(proposals): durable per-scene roll proposals with CAS claim (#162)"
```

---

### Task 2: Pack format additions (modules.py) + `read_rule`

**Files:**
- Modify: `backend/src/grimoire/store/modules.py`
- Test: `backend/tests/test_modules_store.py` (APPEND only)

**Interfaces:**
- Produces: check-roll validation accepts reserved names `difficulty`/`modifier`; checks may carry `difficulty` (int) and `outcomes` (`[{"label": str, "when": expr}]`); `checks.json` reserved `_defaults` entry (`{"difficulty", "outcomes"}`) skipped by per-check validation but validated itself; `ROLL_SCOPE_NAMES = ("total", "natural", "margin", "successes", "ones", "dice")`; `read_rule(mid, rid) -> dict | None` (`{"meta": dict, "body": str}`; raises `ModuleNotFound` for unknown mid, `None` for unknown rid, never raises on content).
- Consumes: existing `_validate_checks`, `pack_root`, `parse_frontmatter`, `expressions.names`.

- [ ] **Step 1: Failing tests (append to test_modules_store.py)**

```python
def test_check_templates_accept_reserved_names(monkeypatch, tmp_path):
    checks = {"c": {"label": "C", "roll": "{vigor + modifier}d10 t{difficulty}",
                    "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert modules.load_pack("testmod")["errors"] == []


def test_check_outcomes_validated(monkeypatch, tmp_path):
    good = {"c": {"label": "C", "roll": "1d20 vs {difficulty}", "requires": [],
                  "difficulty": 12,
                  "outcomes": [{"label": "crit", "when": "natural == 20"},
                               {"label": "success", "when": "margin >= 0"}]}}
    make_pack(_home(monkeypatch, tmp_path), checks=good)
    assert modules.load_pack("testmod")["errors"] == []


@pytest.mark.parametrize("outcomes,frag", [
    ([{"label": "", "when": "total > 1"}], "label"),
    ([{"label": "x", "when": "a.b"}], "when"),
    ([{"label": "x", "when": "vigor > 1"}], "vigor"),   # sheet names not in roll scope
    ("nope", "outcomes"),
])
def test_check_outcomes_rejected(monkeypatch, tmp_path, outcomes, frag):
    checks = {"c": {"label": "C", "roll": "1d20", "requires": [],
                    "outcomes": outcomes}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    assert any(frag in e for e in modules.load_pack("testmod")["errors"])


def test_checks_defaults_entry(monkeypatch, tmp_path):
    checks = {"_defaults": {"difficulty": 6,
                            "outcomes": [{"label": "botch",
                                          "when": "successes == 0 and ones > 0"}]},
              "c": {"label": "C", "roll": "{vigor}d10 t{difficulty}",
                    "requires": ["attributes"]}}
    make_pack(_home(monkeypatch, tmp_path), checks=checks)
    pack = modules.load_pack("testmod")
    assert pack["errors"] == []
    assert pack["checks"]["_defaults"]["difficulty"] == 6
    bad = {"_defaults": {"outcomes": [{"label": "x", "when": "("}]}}
    import shutil
    shutil.rmtree(tmp_path / "modules")
    make_pack(tmp_path, checks=bad)
    assert any("_defaults" in e for e in modules.load_pack("testmod")["errors"])


def test_read_rule(monkeypatch, tmp_path):
    make_pack(_home(monkeypatch, tmp_path),
              rules={"core": "---\nalways: true\n---\nCore body.\n"})
    doc = modules.read_rule("testmod", "core")
    assert doc["body"].strip() == "Core body." and doc["meta"]["always"] == "true"
    assert modules.read_rule("testmod", "ghost") is None
    with pytest.raises(modules.ModuleNotFound):
        modules.read_rule("ghost", "core")
```

- [ ] **Step 2: Run — new tests fail.**

- [ ] **Step 3: Implement**

In `_validate_checks`: extend the placeholder-name scope with the reserved
names — where the requires-scope `scope` set is built, add
`scope |= {"difficulty", "modifier"}`. Validate `difficulty` when present:
`if "difficulty" in check and (not isinstance(check["difficulty"], int) or isinstance(check["difficulty"], bool)): errors.append(f"{where}: difficulty must be an integer")`.
Replace the opaque `outcomes` acceptance with:

```python
ROLL_SCOPE_NAMES = ("total", "natural", "margin", "successes", "ones", "dice")


def _validate_outcomes(outcomes, where: str, errors: list[str]) -> None:
    if not isinstance(outcomes, list):
        errors.append(f"{where}: outcomes must be a list")
        return
    for i, tier in enumerate(outcomes):
        w = f"{where}.outcomes[{i}]"
        if not isinstance(tier, dict):
            errors.append(f"{w}: must be an object")
            continue
        label = tier.get("label")
        if not isinstance(label, str) or not label.strip():
            errors.append(f"{w}: label must be a non-empty string")
        when = tier.get("when")
        if not isinstance(when, str):
            errors.append(f"{w}: when must be an expression string")
            continue
        try:
            unknown = expressions.names(when) - set(ROLL_SCOPE_NAMES)
        except expressions.ExpressionError as e:
            errors.append(f"{w}: when: {e}")
            continue
        if unknown:
            errors.append(f"{w}: when references non-roll-scope names {sorted(unknown)}")
```

Call `_validate_outcomes(check["outcomes"], where, errors)` where the old
opaque check was. In the checks.json loading block, pop `_defaults` before
the per-check loop and validate it:

```python
        defaults = checks.get("_defaults")
        if defaults is not None:
            if not isinstance(defaults, dict):
                errors.append("checks.json: _defaults must be an object")
            else:
                d = defaults.get("difficulty")
                if d is not None and (not isinstance(d, int) or isinstance(d, bool)):
                    errors.append("checks.json: _defaults.difficulty must be an integer")
                if "outcomes" in defaults:
                    _validate_outcomes(defaults["outcomes"], "checks.json: _defaults", errors)
```

and make `_validate_checks` skip the `_defaults` key in its loop
(`if cid == "_defaults": continue`).

`read_rule`:

```python
def read_rule(mid: str, rid: str) -> dict | None:
    """Frontmatter + body of one rules doc; load_pack keeps frontmatter only."""
    root, _source = pack_root(mid)  # raises ModuleNotFound
    if not isinstance(rid, str) or not _safe_mid(rid):
        return None
    p = root / "rules" / f"{rid}.md"
    if not p.exists():
        return None
    try:
        meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError):
        return None
    return {"meta": meta, "body": body}
```

(`_safe_mid` is the existing slug whitelist — rule ids are filename stems,
same shape.)

- [ ] **Step 4: Full module test file green, then full backend suite.** **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(modules): outcome tiers, _defaults, reserved roll names, read_rule (#162)"
```

---

### Task 3: Reference pack upgrades

**Files:**
- Modify: `backend/src/grimoire/store/builtin_modules/pool-basic/checks.json`, `backend/src/grimoire/store/builtin_modules/d20-basic/checks.json`
- Test: `backend/tests/test_modules_store.py` (APPEND)

New `pool-basic/checks.json` (exact full content):

```json
{
  "_defaults": {
    "difficulty": 6,
    "outcomes": [
      {"label": "botch", "when": "successes == 0 and ones > 0"},
      {"label": "exceptional success", "when": "successes >= 5"},
      {"label": "success", "when": "successes >= 1"},
      {"label": "failure", "when": "successes == 0"}
    ]
  },
  "brawl": {
    "label": "Vigor + Brawl",
    "roll": "{vigor + brawl + modifier}d10 t{difficulty}",
    "requires": ["attributes", "abilities"],
    "rules": ["combat"]
  },
  "perception": {
    "label": "Wits + Occult",
    "roll": "{wits + occult + modifier}d10 t{difficulty}",
    "requires": ["attributes", "abilities"]
  }
}
```

New `d20-basic/checks.json` (exact full content):

```json
{
  "_defaults": {
    "difficulty": 12,
    "outcomes": [
      {"label": "critical success", "when": "natural == 20"},
      {"label": "critical failure", "when": "natural == 1"},
      {"label": "success", "when": "margin >= 0"},
      {"label": "failure", "when": "margin < 0"}
    ]
  },
  "athletics": {
    "label": "Athletics",
    "roll": "1d20 + {athletics + str_mod + modifier} vs {difficulty}",
    "requires": ["attributes", "skills"],
    "rules": ["skill-checks"]
  },
  "stealth": {
    "label": "Stealth",
    "roll": "1d20 + {stealth + dex_mod + modifier} vs {difficulty}",
    "requires": ["attributes", "skills"]
  }
}
```

Test (append):

```python
def test_reference_packs_have_defaults_and_reserved_names(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    for mid, diff in (("pool-basic", 6), ("d20-basic", 12)):
        pack = modules.load_pack(mid)
        assert pack["errors"] == [], f"{mid}: {pack['errors']}"
        assert pack["checks"]["_defaults"]["difficulty"] == diff
        assert any("{difficulty}" in c["roll"]
                   for k, c in pack["checks"].items() if k != "_defaults")
```

Steps: failing test → write both JSON files exactly → module file green → full backend → commit:

```bash
git add backend/src/grimoire/store/builtin_modules backend/tests/test_modules_store.py
git commit -m "feat(modules): reference packs gain _defaults ladders and template difficulty/modifier (#162)"
```

---

### Task 4: `store/checks.py` + rolls proposal tag

**Files:**
- Create: `backend/src/grimoire/store/checks.py`
- Modify: `backend/src/grimoire/store/rolls.py` (append gains `proposal=None` keyword → entry field; add `find_by_proposal(cid, pid) -> dict | None` scanning entries)
- Test: `backend/tests/test_checks_store.py`

**Interfaces (produced):**
- `CheckError(Exception)` (user-facing message)
- `resolve_check(cid, check_id, actor_ref, difficulty=None, modifier=0, seed=None) -> dict` — **pure** (RNG draw only): `{"check", "check_label", "actor", "actor_label", "notation", "result", "tier", "difficulty", "modifier", "tier_warnings"}`
- `roll_scope(result: dict) -> dict` — `total`, `natural` (first die's first raw roll), `margin` (only when `result["vs"]` is not None and total present), `successes`, `ones` (count of raw 1s across kept+unkept), `dice` (die count); absent values omitted (not None) so tier `when`s referencing them skip.
- `evaluate_tier(check_def, defaults, scope) -> (label | None, warnings: list[str])`
- `available_checks(cid, sid) -> list[dict]` — `[{"ref", "label", "sheet_type", "checks": [[id, label], ...]}]` over present sheeted cast (via `appearances.scene_cast`) + the sheeted current location (reuse the same current-location source `context._assemble` uses — read context.py near line 352 (`locations:{current_loc}`) and call the same store accessor; do not invent a new one).
- `format_check_roll(resolution) -> str` — the 🎲 line: `🎲 **Mara — Vigor + Brawl (diff 6):** [7, 9, 2] → **2 successes** · *success*` (delegate the dice portion to `dice.format_roll` and append ` · *<tier>*` when a tier exists).
- Consumes: `modules.resolve/load_pack`, `sheets.read` + the numeric-scope/derived machinery (reuse `sheets`' helpers — import them; do not duplicate), `expressions.evaluate/names/ExpressionError`, `dice.roll/DiceError`, `appearances.scene_cast`.

- [ ] **Step 1: Failing tests**

```python
# backend/tests/test_checks_store.py
import pytest

from grimoire.store import appearances, campaigns, checks, rolls, scenes, sheets, worlds


def _play(monkeypatch, tmp_path, module="pool-basic"):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Run", wid, module=module)
    sid = scenes.create_scene(cid)["id"] if hasattr(scenes, "create_scene") else None
    return wid, cid, sid


# NOTE for the implementer: check scenes.py for the real scene-creation
# function name/shape (the routes create scenes via POST /scenes) and fix
# _play accordingly before writing further tests. appearances.appear(...)
# signature: see backend/tests/test_context.py for the established pattern
# (ap.appear(cid, sid, "characters", "seraphine", "default", "npc")).


def test_resolve_check_pool(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium",
                 {"vigor": 3, "brawl": 2, "wits": 2, "occult": 1,
                  "essence": {"current": 5, "max": 10}})
    res = checks.resolve_check(cid, "brawl", "characters:mara", seed=7)
    assert res["notation"] == "5d10 t6"          # (3+2+0)d10, default diff 6
    assert res["difficulty"] == 6 and res["modifier"] == 0
    assert res["tier"] in ("botch", "exceptional success", "success", "failure")
    res2 = checks.resolve_check(cid, "brawl", "characters:mara", seed=7)
    assert res2["result"] == res["result"]        # seeded determinism
    assert rolls.read(cid) == []                  # PURE: no log writes


def test_resolve_check_difficulty_ladder_and_modifier(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    sheets.write(cid, "characters", "mara", "medium", {"vigor": 3, "brawl": 1})
    res = checks.resolve_check(cid, "brawl", "characters:mara",
                               difficulty=8, modifier=2, seed=1)
    assert res["notation"] == "6d10 t8"


def test_resolve_check_d20_tiers(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path, module="d20-basic")
    sheets.write(cid, "characters", "mara", "warrior",
                 {"strength": 14, "athletics": 3})
    # scan seeds for a natural 20 and a natural 1 to prove tier evaluation
    tiers = set()
    for seed in range(200):
        res = checks.resolve_check(cid, "athletics", "characters:mara", seed=seed)
        tiers.add(res["tier"])
    assert {"critical success", "critical failure", "success", "failure"} <= tiers


def test_resolve_check_errors(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid, "ghost", "characters:mara")        # unknown check
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid, "brawl", "characters:mara")        # no sheet
    sheets.write(cid, "items", "moon-disc", "talisman", None)
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid, "brawl", "items:moon-disc")        # requires gating
    cid2 = campaigns.create_campaign("Freeform", worlds.create_world("R2"))
    with pytest.raises(checks.CheckError):
        checks.resolve_check(cid2, "brawl", "characters:mara")       # no module


def test_roll_scope_shapes():
    pool = {"dice": [{"value": 8, "rolls": [8], "kept": True},
                     {"value": 1, "rolls": [1], "kept": True}],
            "total": None, "successes": 1, "vs": None, "modifier": 0}
    s = checks.roll_scope(pool)
    assert s["successes"] == 1 and s["ones"] == 1 and s["dice"] == 2
    assert "margin" not in s and "total" not in s
    flat = {"dice": [{"value": 17, "rolls": [17], "kept": True}],
            "total": 20, "successes": None, "vs": 15, "modifier": 3}
    s = checks.roll_scope(flat)
    assert s["natural"] == 17 and s["margin"] == 5 and s["total"] == 20


def test_evaluate_tier_first_match_and_skip(monkeypatch, tmp_path):
    tiers = [{"label": "crit", "when": "natural == 20"},
             {"label": "ok", "when": "margin >= 0"}]
    label, warns = checks.evaluate_tier({"outcomes": tiers}, {}, {"natural": 20, "margin": 5})
    assert label == "crit"
    label, warns = checks.evaluate_tier({"outcomes": tiers}, {}, {"margin": 1})
    assert label == "ok" and warns == []          # first tier skipped (no `natural`)... 
    # NOTE: absent-name evaluation raises ExpressionError -> skip + warning:
    label, warns = checks.evaluate_tier({"outcomes": [{"label": "x", "when": "ghost > 1"}]},
                                        {}, {"total": 3})
    assert label is None and warns


def test_rolls_proposal_tag(monkeypatch, tmp_path):
    _, cid, _ = _play(monkeypatch, tmp_path)
    from grimoire.store import dice
    entry = rolls.append(cid, "s1", "test", dice.roll("1d6", seed=1), proposal="pr-000001")
    assert entry["proposal"] == "pr-000001"
    assert rolls.find_by_proposal(cid, "pr-000001")["id"] == entry["id"]
    assert rolls.find_by_proposal(cid, "pr-999999") is None
```

- [ ] **Step 2: Run — fails (no checks module).**

- [ ] **Step 3: Implement**

rolls.py: `def append(cid, scene, label, result, proposal=None)` — add
`**({"proposal": proposal} if proposal else {})` into the entry dict;
`find_by_proposal` scans `read(cid)` for `e.get("proposal") == pid`
(last match wins).

checks.py core (structure; reuse `sheets._numeric_scope`/`sheets._compute_derived`
via public wrappers — if they are private, add a public
`sheets.expression_scope(cid, kind, eid) -> (scope, errors)` helper that
returns numeric scope + derived merged, and use it here):

```python
# backend/src/grimoire/store/checks.py
"""Check resolution (#162): pure — RNG draw only, no writes."""

from __future__ import annotations

import re

from . import appearances, dice, expressions, modules, sheets


class CheckError(Exception):
    pass


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def roll_scope(result: dict) -> dict:
    scope: dict = {}
    dice_list = result.get("dice") if isinstance(result.get("dice"), list) else []
    scope["dice"] = len(dice_list)
    raw = [r for d in dice_list if isinstance(d, dict)
           for r in (d.get("rolls") or []) if isinstance(r, int)]
    scope["ones"] = raw.count(1)
    if dice_list and isinstance(dice_list[0], dict):
        first = dice_list[0].get("rolls") or []
        if first and isinstance(first[0], int):
            scope["natural"] = first[0]
    if isinstance(result.get("total"), int):
        scope["total"] = result["total"]
        if isinstance(result.get("vs"), int):
            scope["margin"] = result["total"] - result["vs"]
    if isinstance(result.get("successes"), int):
        scope["successes"] = result["successes"]
    return scope


def evaluate_tier(check_def: dict, defaults: dict, scope: dict):
    warnings: list[str] = []
    for source in (check_def.get("outcomes"), (defaults or {}).get("outcomes")):
        if not isinstance(source, list):
            continue
        for tier in source:
            if not isinstance(tier, dict):
                continue
            try:
                if expressions.evaluate(tier.get("when", ""), scope):
                    return tier.get("label"), warnings
            except expressions.ExpressionError as e:
                warnings.append(f"{tier.get('label')}: {e}")
        return None, warnings          # a present ladder that matched nothing
    return None, warnings
```

Wait — the fallback semantics: check-level ladder present → use ONLY it;
else `_defaults` ladder; else engine fallback (the dice result's own
`outcome` field). Implement exactly that (the loop above returns after the
first present source). Engine fallback happens in `resolve_check`:
`tier = tier or result.get("outcome")`.

```python
def resolve_check(cid, check_id, actor_ref, difficulty=None, modifier=0, seed=None) -> dict:
    mid = modules.resolve(cid)
    if mid is None:
        raise CheckError("no mechanics module is bound to this campaign")
    pack = modules.load_pack(mid)
    check = pack["checks"].get(check_id) if isinstance(pack["checks"], dict) else None
    if not isinstance(check, dict) or check_id == "_defaults":
        raise CheckError(f"unknown check {check_id!r}")
    defaults = pack["checks"].get("_defaults") if isinstance(pack["checks"].get("_defaults"), dict) else {}
    kind, sep, eid = (actor_ref or "").partition(":")
    if not sep or kind not in sheets.FILE_KINDS:
        raise CheckError(f"bad actor reference {actor_ref!r}")
    sheet = sheets.read(cid, kind, eid)
    if sheet is None:
        raise CheckError(f"{actor_ref} has no sheet")
    if sheet["errors"]:
        raise CheckError(f"{actor_ref}'s sheet is invalid: {sheet['errors'][0]}")
    st = pack["sheets"].get("sheet_types", {}).get(sheet["sheet_type"], {})
    st_groups = set(st.get("groups", []) if isinstance(st.get("groups"), list) else [])
    required = check.get("requires", []) if isinstance(check.get("requires"), list) else []
    missing = [g for g in required if g not in st_groups]
    if missing:
        raise CheckError(f"{actor_ref}'s sheet type lacks required groups: {missing}")
    if difficulty is None:
        difficulty = check.get("difficulty", defaults.get("difficulty"))
    scope = dict(sheets.expression_scope(sheet, pack["sheets"]))  # numeric + derived
    scope["modifier"] = modifier if isinstance(modifier, int) else 0
    if isinstance(difficulty, int):
        scope["difficulty"] = difficulty

    def sub(m):
        try:
            return str(int(expressions.evaluate(m.group(1), scope)))
        except expressions.ExpressionError as e:
            raise CheckError(f"check formula: {e}")

    notation = _PLACEHOLDER.sub(sub, check.get("roll", ""))
    try:
        result = dice.roll(notation, seed)
    except dice.DiceError as e:
        raise CheckError(f"bad roll notation {notation!r}: {e}")
    tier, tier_warnings = evaluate_tier(check, defaults, roll_scope(result))
    tier = tier or result.get("outcome")
    return {"check": check_id, "check_label": check.get("label", check_id),
            "actor": actor_ref, "actor_label": _actor_label(cid, kind, eid),
            "notation": notation, "result": result, "tier": tier,
            "difficulty": difficulty, "modifier": scope["modifier"],
            "tier_warnings": tier_warnings}
```

`sheet_scope_from` note: `sheets.read` already returns computed `derived`;
build the scope as `sheets`' numeric scope for the stored fields merged
with `sheet["derived"]` — add a small public helper in sheets.py:

```python
def expression_scope(sheet: dict, sheets_def: dict) -> dict:
    """Numeric scope + derived for an already-read sheet."""
    scope = _numeric_scope(sheets_def, sheet["sheet_type"], sheet["fields"])
    scope.update({k: v for k, v in sheet["derived"].items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)})
    return scope
```

`_actor_label(cid, kind, eid)`: name via `appearances`' actor-name helper
or the entity/character read — check what `scene_cast` uses
(`_actor_name`) and reuse the cheapest public path; fall back to `eid`.

`available_checks(cid, sid)`: per `scene_cast(cid, sid)` member with a
sheet (`sheets.read` non-None, no errors): list check ids whose
`requires` ⊆ the sheet type's groups; plus the current location (same
source as context.py:352) when sheeted. `format_check_roll(res)`:
`f"🎲 **{res['actor_label']} — {res['check_label']}" + (f" (diff {res['difficulty']})" if res['difficulty'] is not None else "") + f":** ..."`
reusing `dice.format_roll(res["result"])`'s dice segment and appending
` · *{tier}*` when tier is truthy — read `dice.format_roll` and compose
rather than reimplement.

- [ ] **Step 4: checks tests green; full backend green.** **Step 5: Commit**

```bash
git add backend/src/grimoire/store/checks.py backend/src/grimoire/store/rolls.py backend/src/grimoire/store/sheets.py backend/tests/test_checks_store.py
git commit -m "feat(checks): pure check resolution with outcome tiers + proposal-tagged rolls (#162)"
```

---

### Task 5: `store/fence.py`

**Files:**
- Create: `backend/src/grimoire/store/fence.py`
- Test: `backend/tests/test_fence.py`

**Interfaces (produced):**
- `class FenceWatcher`: `feed(chunk: str) -> str` (text safe to emit now), `finish() -> str` (any remaining safe text), then read-only properties `complete: bool` (a full fence closed), `truncated: bool` (opener seen, never closed), `narration: str` (all pre-fence text — what should persist), `body: str | None` (fence inner text).
- `parse_roll_body(text: str) -> tuple[dict, list[str]]` — tolerant parse: strict JSON → permissive normalization (single→double quotes outside strings is NOT attempted; instead: strip trailing commas, then regex key extraction for `check|actor|difficulty|modifier|reason` when JSON fails); returns `(fields, problems)`; never raises.
- Opener pattern: ```` ```roll ```` — three backticks, optional spaces/tabs (NOT newlines), then `roll` at a word boundary, case-insensitive: `re.compile(r"```[ \t]*roll\b", re.I)`. Closer: the next ```` ``` ```` at line start after the opener line. Hold-back is **prefix-state based**: retain the longest buffer suffix that could still extend into an opener (a suffix matching `` `{1,3}([ \t]*(r(o(l(l)?)?)?)?)? `` case-insensitively), never a fixed-length tail — `` ``` `` + eight spaces + a chunk boundary before `roll` must not leak the backticks.

- [ ] **Step 1: Failing tests**

```python
# backend/tests/test_fence.py
import json

from grimoire.store.fence import FenceWatcher, parse_roll_body


def run(chunks):
    w = FenceWatcher()
    out = "".join(w.feed(c) for c in chunks)
    out += w.finish()
    return w, out


def test_no_fence_passthrough():
    w, out = run(["Mara ", "leaps."])
    assert out == "Mara leaps." and w.narration == "Mara leaps."
    assert not w.complete and not w.truncated and w.body is None


def test_fence_mid_stream():
    body = '{"check": "athletics", "actor": "characters:mara"}'
    w, out = run(["She lunges—\n", "```roll\n", body, "\n```", "\nleftover ignored"])
    assert w.complete and w.body.strip() == body
    assert w.narration == "She lunges—\n"
    assert "```" not in out and "athletics" not in out


def test_fence_split_across_deltas():
    body = '{"check": "brawl", "actor": "characters:mara"}'
    w, out = run(["punch! ", "``", "`ro", "ll\n", body[:10], body[10:], "\n`", "``"])
    assert w.complete and json.loads(w.body)["check"] == "brawl"
    assert w.narration == "punch! "
    assert "`" not in out


def test_unclosed_fence_truncated():
    w, out = run(["text\n", "```roll\n", '{"check": "brawl"'])
    assert w.truncated and not w.complete
    assert w.narration == "text\n"
    assert w.body.strip() == '{"check": "brawl"'


def test_fence_at_start():
    w, out = run(['```roll\n{"check": "x"}\n```'])
    assert w.complete and w.narration == "" and out == ""


def test_second_fence_ignored():
    w, out = run(['a\n```roll\n{"check": "x"}\n```\n```roll\n{"check": "y"}\n```'])
    assert w.complete and json.loads(w.body)["check"] == "x"


def test_holdback_eventually_emitted():
    w, out = run(["end with backt", "icks ``ok``"])
    assert out == "end with backticks ``ok``"


def test_opener_with_spaces_split_never_leaks():
    body = '{"check": "brawl", "actor": "characters:mara"}'
    for gap in ("", " ", "        ", "\t"):
        for split_at in range(1, 4 + len(gap)):
            opener = f"```{gap}roll\n"
            w = FenceWatcher()
            out = w.feed("go! ") + w.feed(opener[:split_at]) + w.feed(opener[split_at:])
            out += w.feed(body + "\n```")
            out += w.finish()
            assert "`" not in out, f"leaked with gap={gap!r} split={split_at}"
            assert w.complete and w.narration == "go! "


def test_newline_after_backticks_is_not_an_opener():
    w, out = run(["```\ncode\n```", " done"])
    assert w.complete is False and w.body is None
    assert out == "```\ncode\n``` done"


def test_parse_roll_body_strict_and_tolerant():
    fields, problems = parse_roll_body('{"check": "brawl", "actor": "characters:mara", "difficulty": 6}')
    assert fields["check"] == "brawl" and problems == []
    fields, problems = parse_roll_body("{'check': 'brawl', 'difficulty': 6,}")
    assert fields.get("check") == "brawl" and fields.get("difficulty") == 6
    fields, problems = parse_roll_body("utter garbage !!!")
    assert fields == {} and problems
```

- [ ] **Step 2: Run — fails.**

- [ ] **Step 3: Implement**

```python
# backend/src/grimoire/store/fence.py
"""Incremental ```roll fence detection over a streamed reply (#162).

Pure: no I/O. The watcher withholds a small tail so a fence opener split
across deltas is never emitted, releases withheld text when it turns out
not to be a fence, and stops emitting entirely once an opener is seen.
"""

from __future__ import annotations

import json
import re

_OPENER = re.compile(r"```[ \t]*roll\b", re.IGNORECASE)
# A buffer suffix that could still grow into an opener: 1-3 backticks,
# then (only after all 3) optional spaces/tabs and a prefix of "roll".
_OPENER_PREFIX = re.compile(r"(`{1,2}|`{3}[ \t]*(r(o(l(l)?)?)?)?)$", re.IGNORECASE)


def _opener_prefix_len(buf: str) -> int:
    m = _OPENER_PREFIX.search(buf)
    return len(m.group(0)) if m else 0


class FenceWatcher:
    def __init__(self) -> None:
        self._buf = ""            # unemitted tail (pre-fence mode)
        self._emitted = ""        # text already returned to the caller
        self._after = ""          # everything from the opener onward
        self._open = False
        self.complete = False
        self.truncated = False
        self.body: str | None = None
        self._narration_prefix = ""

    def feed(self, chunk: str) -> str:
        if self._open:
            self._after += chunk
            self._try_close()
            return ""
        self._buf += chunk
        m = _OPENER.search(self._buf)
        if m:
            self._narration_prefix = self._buf[: m.start()]
            out = self._narration_prefix[len(self._emitted):]
            self._emitted = self._narration_prefix
            self._after = self._buf[m.start():]
            self._open = True
            self._buf = ""
            self._try_close()
            return out
        # prefix-state holdback: withhold the longest suffix that could
        # still extend into an opener (backticks + optional spaces/tabs +
        # a prefix of "roll"); a fixed-length tail leaks backticks when
        # the optional whitespace stretches the opener.
        safe_len = max(len(self._emitted),
                       len(self._buf) - _opener_prefix_len(self._buf))
        out = self._buf[len(self._emitted): safe_len]
        self._emitted = self._buf[:safe_len]
        return out

    def _try_close(self) -> None:
        if self.complete:
            return
        first_nl = self._after.find("\n")
        if first_nl < 0:
            return
        close = re.search(r"^```", self._after[first_nl + 1:], re.MULTILINE)
        if close:
            self.body = self._after[first_nl + 1: first_nl + 1 + close.start()]
            self.complete = True

    def finish(self) -> str:
        if self._open:
            if not self.complete:
                self.truncated = True
                first_nl = self._after.find("\n")
                self.body = self._after[first_nl + 1:] if first_nl >= 0 else ""
            return ""
        out = self._buf[len(self._emitted):]
        self._emitted = self._buf
        return out

    @property
    def narration(self) -> str:
        return self._narration_prefix if self._open else self._buf


_KEY_RE = {
    "check": re.compile(r'["\']?check["\']?\s*[:=]\s*["\']([^"\']+)["\']'),
    "actor": re.compile(r'["\']?actor["\']?\s*[:=]\s*["\']([^"\']+)["\']'),
    "reason": re.compile(r'["\']?reason["\']?\s*[:=]\s*["\']([^"\']+)["\']'),
    "difficulty": re.compile(r'["\']?difficulty["\']?\s*[:=]\s*(-?\d+)'),
    "modifier": re.compile(r'["\']?modifier["\']?\s*[:=]\s*(-?\d+)'),
}


def parse_roll_body(text: str) -> tuple[dict, list[str]]:
    text = (text or "").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data, []
    except (json.JSONDecodeError, ValueError):
        pass
    fields: dict = {}
    for key, rx in _KEY_RE.items():
        m = rx.search(text)
        if m:
            fields[key] = int(m.group(1)) if key in ("difficulty", "modifier") else m.group(1)
    problems = [] if fields else ["roll request was unparseable"]
    if fields and "check" not in fields:
        problems.append("roll request had no check id")
    return fields, problems
```

(The trailing-comma tolerant case in the test parses via the regex path —
`'{'check': 'brawl', ...}'` fails `json.loads` and the key regexes pick the
fields out. Verify each test's expectation against this implementation and
adjust the implementation — not the tests — if a case misses.)

- [ ] **Step 4: Green; full backend.** **Step 5: Commit**

```bash
git add backend/src/grimoire/store/fence.py backend/tests/test_fence.py
git commit -m "feat(fence): incremental roll-fence watcher with tolerant body parsing (#162)"
```

---

### Task 6: Context sections + verify_templates

**Files:**
- Modify: `backend/src/grimoire/store/context.py`, `templates/scene/system.j2`, `scripts/verify_templates.py`
- Create: `templates/scene/sections/mechanics_rules.j2`, `mechanics_sheets.j2`, `mechanics_response_format.j2`
- Test: `backend/tests/test_context.py` (APPEND)

**Interfaces:**
- `_assemble` gains data keys: `mechanics_rules: list[str]` (doc bodies, activation-ordered: always → sheet_types → keys, keys capped at 6), `mechanics_sheets: list[dict]` (`{"ref", "label", "type_label", "lines": [str]}`), `mechanics_checks: list[dict]` (the `available_checks` shape) — all `[]` when no module resolves.
- New `_SECTIONS` entries, in this order: `("Mechanics rules", "scene/sections/mechanics_rules.j2", False)` right after `("Group state", ...)`; `("Mechanics sheets", "scene/sections/mechanics_sheets.j2", False)` next; `("Mechanics response format", "scene/sections/mechanics_response_format.j2", False)` immediately **before** `("Response format", ...)`. Matching include blocks in `system.j2` at the same positions (two-line `{%- set s -%}` idiom).
- A private `_mechanics(cid, sid, cast, recent_text)` gatherer in context.py: resolves the module; builds rules via `modules.load_pack` (activation frontmatter) + `modules.read_rule` (bodies); summaries via `sheets.read` for present cast + current location; checks via `checks.available_checks(cid, sid)`.

Templates (exact):

```jinja
{#- mechanics_rules.j2: activated module rules docs. Vars: mechanics_rules ([str]). -#}
{%- if mechanics_rules %}# Mechanics rules
{{ mechanics_rules | join("\n\n") }}{% endif -%}
```

```jinja
{#- mechanics_sheets.j2: compact sheet summaries for present sheeted actors +
    the current location. Vars: mechanics_sheets ([{ref, label, type_label, lines}]). -#}
{%- set blocks = [] -%}
{%- for s in mechanics_sheets -%}
{%- set _ = blocks.append(s.ref ~ " — " ~ s.type_label ~ " (" ~ s.label ~ ")\n  " ~ s.lines | join("\n  ")) -%}
{%- endfor -%}
{%- if blocks %}# Sheets
{{ blocks | join("\n") }}{% endif -%}
```

```jinja
{#- mechanics_response_format.j2: the roll protocol + available checks.
    Vars: mechanics_checks ([{ref, label, sheet_type, checks: [[id, label]]}]). -#}
{%- if mechanics_checks %}# Dice checks
When a present character attempts something uncertain and consequential,
request a check by emitting exactly this fenced block mid-narration, at the
moment of the attempt, then STOP writing immediately after the closing fence:

```roll
{"check": "<check id>", "actor": "<kind:id>", "difficulty": <number, optional>, "reason": "<short phrase>"}
```

Rules: use only the check ids and actor references listed below; never
invent ids; never write dice results yourself — the engine rolls and you
will be told the outcome. Do not propose more than one check per reply.

Available checks:
{% for a in mechanics_checks %}{{ a.ref }} ({{ a.label }}): {% for c in a.checks %}{{ c[0] }} ({{ c[1] }}){% if not loop.last %}, {% endif %}{% endfor %}
{% endfor %}{% endif -%}
```

Summary `lines` construction (python, in `_mechanics`): for each field of
the sheet type's assembled fields, one `key value` entry — resources as
`key cur/max`; then derived as `key value`; joined into short lines of ~4
entries separated by ` · `. Invalid sheets contribute
`{"lines": ["(sheet invalid)"]}`.

verify_templates.py: mirror the three keys in `gather()` (same public
calls: `modules.resolve/load_pack/read_rule`, `sheets.read`,
`checks.available_checks`) and add them to its return dict; extend the
fixture so a module is bound and one character sheeted (making all three
sections non-empty during the byte-for-byte run) — follow how the fixture
seeds group state (lines ~146-213) for the pattern.

Tests (append to test_context.py, following its `_campaign`/`ap.appear`
idiom): module-bound campaign → `context_sections` labels include all
three mechanics sections, rules digest contains an `always` doc body and
excludes an unmatched `keys:` doc; sheet summary line shows `kind:id` ref
and a `cur/max` resource; response format lists a check id; unbound
campaign → none of the three labels appear; keyword cap: build a pack with
8 keyed docs all matching → only 6 bodies present.

Steps: failing tests → implement gatherer + templates + `_SECTIONS` +
system.j2 → context tests green → `python scripts/verify_templates.py`
passes → full backend → commit:

```bash
git add backend/src/grimoire/store/context.py templates/scene scripts/verify_templates.py backend/tests/test_context.py
git commit -m "feat(context): mechanics rules/sheets/roll-protocol sections (#162)"
```

---

### Task 7: Routes — fence integration, proposal lifecycle, checks

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py` (APPEND)

**Interfaces (produced for the frontend):**
- `_chat_stream` watches fences (persisted turns only; `_ephemeral_stream` untouched): on a complete/truncated fence — build the proposal payload (`_make_proposal(cid, sid, watcher)` helper: `parse_roll_body`, actor resolution against `checks.available_checks(cid, sid)` (exact ref then case-insensitive label match), `problems` per spec incl. `check unavailable to this actor` and truncation), then `store.proposals.new` **before** `_persist_reply(watcher.narration)`, then yield `{"proposal": {**payload, "id": rec["id"]}}` and `{"done": True}`.
- `post_chat`/`post_retry`/`post_regenerate`: `store.proposals.supersede(cid, sid)` right after `_require_scene`.
- `GET /campaigns/{cid}/scenes/{sid}/roll-proposal` → `{"record": rec | None}`.
- `POST /campaigns/{cid}/scenes/{sid}/roll-proposal` (SSE) — body model `ProposalAction(BaseModel): proposal: str; action: str; check: str | None = None; actor: str | None = None; difficulty: int | None = None; modifier: int | None = None`. Flow exactly per the spec's state walk, **every state change via `proposals.transition` and every lost transition = stop dead (no projection, no continuation), 409 when nothing has streamed yet**:
  - accept = `claim` (lost → 409) → `resolve_check` (catch-all except: `transition(..., ("resolving",), "pending")` + error frame) → commit `transition(..., ("resolving",), "resolved", resolution)` (**lost — e.g. superseded mid-resolve — → discard the roll result unlogged, 409**) → projection → stream continuation → **`proposals.commit_narration(cid, sid, pid, persist_closure)`** — False means a supersede won mid-stream: drop the streamed text (nothing persisted) and end the stream with `{"done": true}` (the chip refetches and finds `superseded`).
  - **Follow-up fence in the continuation** (the continuation stream also runs through a `FenceWatcher`): when it completes a fence, the handoff is one atomic `proposals.locked(cid)` block — `commit_narration(old_pid, persist_prefence_narration)` (which trim-recovers, persists the pre-fence text, marks the old record `narrated`), then `proposals.new(...)` for the new fence, then emit the proposal event. The old lifecycle always finishes with its narration persisted before the new pending record exists. Route test: fake LLM's continuation emits a fence → pre-fence text persisted, old record `narrated`, new record `pending` and recoverable via the GET.
  - Projection (idempotent per spec, each output independently recoverable) — **the whole sequence runs inside `proposals.locked(cid)`** so concurrent resolved-retries serialize (pure file I/O, no LLM under the lock). **Carry the updated resolution forward across CASes — never reuse a stale local** (a `{**resolution, ...}` from before the roll_id write would erase it):

    ```python
    res = dict(rec["resolution"])
    entry = rolls.find_by_proposal(cid, pid)      # uuid tag match is proof
    if entry is None:
        entry = rolls.append(cid, sid, label, res["result"], proposal=pid)
    res = {**res, "roll_id": entry["id"]}
    proposals.transition(cid, sid, pid, ("resolved",), "resolved", res)
    if "line_intent" not in res:
        res = {**res, "line_intent": len(scene_messages())}
        proposals.transition(cid, sid, pid, ("resolved",), "resolved", res)
    line = checks.format_check_roll(res)
    if not any(m.get("speaker") == ROLL_SPEAKER and m["content"] == line
               for m in scene_messages()[res["line_intent"]:]):
        scenes.append_message(cid, sid, "assistant", line, speaker=ROLL_SPEAKER)
    ```

    Tests must assert `roll_id` AND `line_intent` both survive on the narrated record (the erase-on-second-CAS bug is exactly what the carried-forward `res` prevents).
  - decline = `transition(..., ("pending",), "declined")` (lost → 409) → declined continuation → `commit_narration` (same drop-on-supersede semantics).
  - Retry of `resolved`/`declined` re-runs projection (each step self-deduping) + continuation + `commit_narration`; `narrated` → immediate done frame.
- Continuation messages: `store.context.build_messages(cid, sid)` + one system message rendered from new templates `templates/scene/roll_result.j2` (vars: `resolution`, `on_roll_docs: [str]`, `check_docs: [str]`) and `templates/scene/roll_declined.j2` (no vars) — write both templates (short, following `scene/director_note.j2` style; exact wording per the spec's Continuation section).
- `GET /campaigns/{cid}/scenes/{sid}/checks` → `{"actors": checks.available_checks(cid, sid)}`.
- `POST /campaigns/{cid}/scenes/{sid}/check` — body `CheckBody(BaseModel): check: str; actor: str; difficulty: int | None = None; modifier: int | None = None`; runs `resolve_check`, appends roll log (no proposal tag) + 🎲 line, returns `{"ok": True, "resolution": ..., "roll": entry, "message": line}`; `CheckError` → 400.
- All new routes beside `post_scene_roll` (routes.py:2350), before the generic-catch-all comment at 2369.

Tests (append; use the `client` fixture + `FakeOpenRouter` re-override pattern):

```python
def _mech_scene(client):
    _wid, cid = _campaign(client)
    client.put(f"/api/campaigns/{cid}/module", json={"module": "pool-basic"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    # create + sheet + cast a character; mirror how existing tests cast actors
    # (see the appearances/cast tests in this file for the exact POST shape)
    return cid, sid


def test_chat_fence_cuts_and_persists_proposal(client, monkeypatch):
    cid, sid = _mech_scene(client)
    fence = 'She lunges—\n```roll\n{"check": "brawl", "actor": "characters:mara"}\n```\ntrailing'
    # re-override get_llm with FakeOpenRouter streaming the fence in pieces
    ...
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "go"})
    frames = [json.loads(l[len("data: "):]) for l in resp.text.splitlines()
              if l.startswith("data: ")]
    deltas = "".join(f["delta"] for f in frames if "delta" in f)
    assert deltas == "She lunges—\n"                      # pre-fence narration only
    assert "`" not in deltas and "brawl" not in deltas    # no fence chars leaked
    kinds = [next(iter(f)) for f in frames]
    assert kinds.index("proposal") < kinds.index("done")  # proposal precedes done
    rec = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec["status"] == "pending" and rec["payload"]["check"] == "brawl"
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1]["content"].startswith("She lunges")      # fence stripped


def test_proposal_accept_walk_and_idempotency(client):
    cid, sid = _mech_scene(client)
    _emit_fence(client, cid, sid,
                '{"check": "brawl", "actor": "characters:mara", "difficulty": 6}')
    rec = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec["status"] == "pending"
    body = {"proposal": rec["id"], "action": "accept",
            "check": "brawl", "actor": "characters:mara", "difficulty": 6, "modifier": 0}

    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal",
                       json={**body, "proposal": "pr-999999"}).status_code == 409

    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=body)
    assert resp.status_code == 200 and 'data: {"done": true}' in resp.text
    entries = client.get(f"/api/campaigns/{cid}/rolls").json()
    tagged = [e for e in entries if e.get("proposal") == rec["id"]]
    assert len(tagged) == 1
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert any(m["content"].startswith("🎲") for m in msgs)
    rec2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal").json()["record"]
    assert rec2["status"] == "narrated"
    assert rec2["resolution"]["roll_id"] == tagged[0]["id"]

    # idempotent retry after narrated: immediate done, no new roll, no new 🎲 line
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll-proposal", json=body)
    assert 'data: {"done": true}' in resp.text
    entries2 = client.get(f"/api/campaigns/{cid}/rolls").json()
    assert len([e for e in entries2 if e.get("proposal") == rec["id"]]) == 1
    msgs2 = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert len([m for m in msgs2 if m["content"].startswith("🎲")]) == 1


# _emit_fence(client, cid, sid, body_json): helper that re-overrides get_llm
# with FakeOpenRouter streaming ["pre-fence text\n", "```roll\n", body_json,
# "\n```"] and POSTs an empty-... no — a normal chat turn (json={"content":
# "go"}); write it once above these tests, modeled on how existing tests
# swap get_llm overrides (test_routes.py ~line 2041).
def test_proposal_decline(client): ...
def test_new_send_supersedes(client): ...
def test_manual_check_and_availability(client): ...
def test_proposal_routes_without_module(client): ...  # 400/404
```

The `...` bodies above are sketches — the implementer writes them fully
following the file's established SSE-assertion style
(`test_chat_streams_and_persists`), asserting: exact SSE frames present,
roll-log entry count and `proposal` tag, message contents, and the
proposals record status after each step. Every assertion named in the
sketch comments is required; the spec's Testing section lists the full
required matrix. Failure injection is mandatory at EVERY side-effect
boundary, asserting exactly one tagged roll entry and exactly one 🎲
line survive:
- `monkeypatch` `store.checks.resolve_check` → `RuntimeError` → status
  back to `pending`, no roll entry, no line;
- supersede between claim and commit (call `store.proposals.supersede`
  from a monkeypatched `resolve_check` before returning normally) →
  commit loses, roll unlogged, status stays `superseded`;
- crash between roll append and roll_id backfill (`monkeypatch`
  `store.proposals.transition` to raise once after `rolls.append` ran) →
  retry accept → one tagged entry, backfilled roll_id, one line;
- crash between line append and `narrated` (monkeypatch the continuation
  builder to raise) → retry → no duplicate 🎲 line (line_intent +
  content dedup), continuation streams, `narrated`;
- **two proposals with byte-identical formatted lines** (same check,
  actor, difficulty, seed-forced identical result; first fully narrated,
  second accepted after a fresh fence) → BOTH 🎲 lines present (the
  intent index of the second is past the first's line);
- **continuation-vs-supersede race at the route level**: monkeypatch the
  fake LLM stream to call `POST /chat` (a new send) between the last
  delta and stream end — the continuation must not persist, the record
  ends `superseded`, and the transcript's final message is the new
  send's, never the stale continuation;
- **concurrent resolved-retries**: two threads POST accept for the same
  `resolved` record simultaneously (TestClient in threads) → exactly one
  tagged roll entry, one 🎲 line, one persisted continuation (the loser's
  `commit_narration` returns False);
- **crash mid-continuation-persist**: monkeypatch `_persist_reply` to
  append one message then raise → record keeps `narration_intent`,
  status stays `resolved`; retry → the partial message is trimmed, the
  full continuation persists once, `narrated`.

Steps: failing tests → implement (rewrite `_chat_stream` with an optional
fence watcher; helper functions `_make_proposal`, `_project_resolution`,
`_continuation_messages`) → routes tests green → full backend → commit:

```bash
git add backend/src/grimoire/routes.py templates/scene backend/tests/test_routes.py
git commit -m "feat(routes): roll fence cut, durable proposal lifecycle, manual checks (#162)"
```

---

### Task 8: Frontend client + stream types

**Files:**
- Modify: `frontend/src/api/stream.ts`, `frontend/src/api/client.ts`

**Interfaces (produced):**

In stream.ts:

```ts
export type RollProposalPayload = {
  id: string;
  check?: string; check_label?: string;
  actor?: string; actor_label?: string;
  difficulty?: number; modifier?: number; reason?: string;
  available?: Record<string, [string, string][]>;
  problems: string[];
};
export type ChatEvent = {
  delta?: string; done?: boolean;
  error?: { detail: string; kind: string };
  proposal?: RollProposalPayload;
};
```

In client.ts (types `ProposalRecord = { id: string; status: string; payload: RollProposalPayload; resolution: CheckResolution | null }`, `CheckResolution = { check: string; check_label: string; actor: string; actor_label: string; notation: string; tier: string | null; difficulty: number | null; modifier: number; roll_id?: string }`, `SceneCheckActor = { ref: string; label: string; sheet_type: string; checks: [string, string][] }`):

```ts
  getRollProposal: (cid: string, sid: string) =>
    request<{ record: ProposalRecord | null }>("GET", `/api/campaigns/${cid}/scenes/${sid}/roll-proposal`),
  resolveProposal: (cid: string, sid: string,
                    body: { proposal: string; action: "accept" | "decline";
                            check?: string; actor?: string;
                            difficulty?: number; modifier?: number },
                    onEvent: (e: ChatEvent) => void) =>
    streamPost(`/api/campaigns/${cid}/scenes/${sid}/roll-proposal`, body, onEvent),
  getSceneChecks: (cid: string, sid: string) =>
    request<{ actors: SceneCheckActor[] }>("GET", `/api/campaigns/${cid}/scenes/${sid}/checks`),
  rollCheck: (cid: string, sid: string,
              body: { check: string; actor: string; difficulty?: number; modifier?: number }) =>
    request<{ ok: boolean; resolution: CheckResolution; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/check`, body),
```

Steps: edits → `npx tsc -b` clean + full `npx vitest run` (additive) → commit:

```bash
git add frontend/src/api/stream.ts frontend/src/api/client.ts
git commit -m "feat(client): roll proposal + scene check API (#162)"
```

---

### Task 9: `RollProposal.tsx`

**Files:**
- Create: `frontend/src/components/RollProposal.tsx`
- Test: `frontend/src/components/RollProposal.test.tsx`

**Interfaces:**
- Props: `{ record: ProposalRecord; busy: boolean; onResolve: (body: { proposal: string; action: "accept" | "decline"; check?: string; actor?: string; difficulty?: number; modifier?: number }) => void; }`
- Renders per record status: `pending` → normal chip (`🎲 <check_label> — <actor_label>` + `· diff N` + reason hint; buttons **Roll it**, **Modify**, **Decline**); Modify state (toggled, or automatic when `payload.problems.length > 0`): check `<select aria-label="Check">` from `payload.available[actor]` (all actors' checks when the actor is unresolved, with an actor `<select aria-label="Actor">`), difficulty/modifier `<input type="number">`s; problems render as `.field-hint` warnings. `resolved` status → "roll made, narration pending" + single **Continue narration** button (`onResolve({proposal, action: "accept"})` — the route's idempotent retry path). Everything disabled when `busy`.
- Container: `.roll-proposal` styled block (add minimal CSS to index.css reusing existing chip/panel variables).

Test (mock nothing — pure props/callback component):

```tsx
const REC: ProposalRecord = {
  id: "pr-000001", status: "pending",
  payload: { id: "pr-000001", check: "brawl", check_label: "Vigor + Brawl",
             actor: "characters:mara", actor_label: "Mara", difficulty: 6,
             available: { "characters:mara": [["brawl", "Vigor + Brawl"], ["perception", "Wits + Occult"]] },
             problems: [] },
  resolution: null,
};

test("accept sends ids and numbers", () => {
  const onResolve = vi.fn();
  render(<RollProposal record={REC} busy={false} onResolve={onResolve} />);
  fireEvent.click(screen.getByText("Roll it"));
  expect(onResolve).toHaveBeenCalledWith(
    { proposal: "pr-000001", action: "accept", check: "brawl",
      actor: "characters:mara", difficulty: 6, modifier: 0 });
});

test("modify swaps check and difficulty", () => { /* open Modify, change check
  select to perception and difficulty to 8, Roll it -> body reflects both */ });

test("problems auto-open modify with actor select", () => { /* record with
  problems + no actor: actor select present, warning hints rendered */ });

test("decline and resolved-continue", () => { /* Decline sends action decline;
  a record with status resolved renders only Continue narration */ });
```

(The commented tests are required — write them fully with concrete
assertions mirroring the first test's style.)

Steps: failing tests → implement → targeted vitest + tsc → commit:

```bash
git add frontend/src/components/RollProposal.tsx frontend/src/components/RollProposal.test.tsx frontend/src/index.css
git commit -m "feat(frontend): roll proposal chip with modify/decline (#162)"
```

---

### Task 10: CampaignView integration + popover Check mode

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.test.tsx` (APPEND; extend the client mock additively with `getRollProposal`, `resolveProposal`, `getSceneChecks`, `rollCheck` safe defaults — `getRollProposal` resolves `{ record: null }`, `getSceneChecks` resolves `{ actors: [] }`)

**Wiring:**
1. State: `const [proposal, setProposal] = useState<ProposalRecord | null>(null);`
2. `runStream`'s event handler gains: `else if (e.proposal) { setProposal({ id: e.proposal.id, status: "pending", payload: e.proposal, resolution: null }); }`
3. `selectScene` additionally `api.getRollProposal(cid, id).then((r) => setProposal(r.record && r.record.status !== "superseded" && r.record.status !== "narrated" && r.record.status !== "declined" ? r.record : null)).catch(() => setProposal(null));`
4. `send()` clears the chip optimistically (`setProposal(null)`) — the backend supersedes durably.
5. Render `<RollProposal record={proposal} busy={busy} onResolve={resolve} />` between the streaming block (ends ~line 665) and `.inputbar` (line 667), when `proposal && activeId`.
6. `resolve(body)`: `runStream(activeId, (onEvent) => api.resolveProposal(cid, activeId!, body, onEvent))` — `runStream`'s finally already re-fetches the scene; also `setProposal(null)` on start and re-fetch the record on stream error (409 handling: catch → `api.getRollProposal` → `setProposal`).
7. **Popover Check mode**: `rollForm` state gains `mode: "dice" | "check"` plus `checkActor`, `checkId`, `difficulty`, `modifier` fields; on opening the popover also `api.getSceneChecks(...)` into local state; a two-button mode toggle at the top of `.roll-pop`; Check mode renders actor select (aria-label "Check actor"), check select (aria-label "Check"), difficulty/modifier inputs, and Roll ▸ calls `api.rollCheck` then `selectScene(activeId)`. Dice mode unchanged.

Tests (append): SSE proposal event renders the chip (drive `runStream` via a mocked `api.chat` that invokes `onEvent({proposal: {...}})`); chip resolve pipes through `api.resolveProposal` and clears; scene select re-hydrates a pending record; popover Check mode lists actors/checks and posts `rollCheck`. Follow the file's existing mock/interaction patterns exactly.

Steps: failing tests → implement → full `npx vitest run` + `npx tsc -b` → commit:

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(frontend): proposal chip lifecycle + popover check mode (#162)"
```

---

### Task 11: Full verification + end-state

- [ ] Backend full suite; frontend full vitest + tsc.
- [ ] `python scripts/verify_templates.py` (or however the repo invokes it — check the script header) passes.
- [ ] End-state per spec under the `verify` skill's mocked OpenRouter: script the mock to emit a roll fence; accept via the UI; confirm roll-log entry + 🎲 line + streamed continuation; a module-less campaign shows no mechanics sections (assert via the context endpoint or prompt preview) and no Check mode actors.
- [ ] Update `.claude/skills/create-mechanics-module/SKILL.md`: checks can now carry `difficulty` + `outcomes` tiers and templates should use `{difficulty}`/`{modifier}`; `_defaults` documented (one short subsection with the pool-basic ladder as the example).
- [ ] Commit stragglers. The branch then goes through `/codex:review` (CLAUDE.md gate) before finishing-a-development-branch.
