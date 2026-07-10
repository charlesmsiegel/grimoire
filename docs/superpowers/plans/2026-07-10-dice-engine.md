# Dice Engine (Mechanics & Dice Phase 2, #824) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A seeded, replayable dice engine with an append-only per-campaign roll
log and a manual Roll affordance in the play view that writes results into the
scene.

**Architecture:** A pure notation-parser/roll-engine module (`store/dice.py`)
with no filesystem access; a JSON log store (`store/rolls.py`) at
`<campaign>/rolls.json` in the style of `changes.py`; three routes (roll into a
scene, list the log, replay an entry); a popover on the play-view input bar.
Every roll records its integer seed so `roll(notation, seed)` reproduces it
exactly — that is the replay guarantee the spec (#824) demands.

**Tech Stack:** Python stdlib only (`re`, `random`, `secrets`, `json`) on the
backend; existing React/vitest patterns on the frontend.

Spec: `docs/superpowers/specs/2026-07-05-mechanics-dice-roadmap-design.md`
(§ Dice engine, § Phase 2). Phase 2 is independent of Phases 0/1: the manual
roller is a table utility, deliberately **not** gated on a campaign `module:`
key (which doesn't exist yet). Module-driven outcome tiers arrive with
`checks.json` in later phases; the tiers here are the notation-intrinsic ones
(pool successes, sum vs target).

## Global Constraints

- Backend deps: stdlib only for this feature (Android base-dep rule).
- pydantic stays v1/v2-agnostic: plain `BaseModel` fields, dump via
  `routes._dump`, no `Field`/validators.
- Filesystem access only through `store.paths` / `campaigns.campaign_root`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend tests from the worktree root:
  `PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
  (the venv's editable install points at the main checkout; PYTHONPATH shadows it).
- Frontend: run `npx vitest run` and `npx tsc -b` **from** `frontend/`.
- Commit after every task.

## Notation grammar (fixed here, reused verbatim in code and docstrings)

```
[N]dM [khn|kln|dhn|dln] [!] [+k|-k] [tN | vs N]     (case-insensitive, spaces allowed between clauses)
```

- `NdM` — N dice (default 1) of M sides. 1 ≤ N ≤ 100, 2 ≤ M ≤ 1000.
- `khn`/`kln` keep the n highest/lowest; `dhn`/`dln` drop the n highest/lowest.
  kh/kl require n ≤ N; dh/dl require n < N; n ≥ 1.
- `!` exploding: a die showing its maximum rolls again. Sum rolls chain the
  extra roll onto the same die's value; pool rolls append a new die to the
  pool. A per-roll budget of 100 explosions bounds hostile notations.
- `+k`/`-k` — integer modifier on the sum. Rejected on pool rolls.
- `tN` — pool mode: count kept dice ≥ N as successes (no total).
- `vs N` — sum mode target: outcome `"success"` if total ≥ N else `"failure"`.

Result dict (the engine's single output shape, stored verbatim in the log):

```python
{"notation": str,          # input, stripped
 "seed": int,
 "dice": [{"value": int, "rolls": [int, ...], "kept": bool}, ...],
 "modifier": int,
 "pool_target": int | None,
 "vs": int | None,
 "total": int | None,      # sum mode
 "successes": int | None,  # pool mode
 "outcome": str | None}    # "success"/"failure", sum mode with vs only
```

---

### Task 1: Notation parser (`dice.parse`)

**Files:**
- Create: `backend/src/grimoire/store/dice.py`
- Test: `backend/tests/test_dice.py`

**Interfaces:**
- Produces: `dice.parse(notation: str) -> dict` with keys
  `count, sides, keep (tuple[str, int] | None), explode (bool), modifier (int),
  pool (int | None), vs (int | None)`; raises `dice.DiceError` (subclass of
  `ValueError`) on bad notation.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_dice.py
import pytest

from grimoire.store import dice


def test_parse_basic():
    assert dice.parse("2d6") == {"count": 2, "sides": 6, "keep": None, "explode": False,
                                 "modifier": 0, "pool": None, "vs": None}


def test_parse_count_defaults_to_one():
    assert dice.parse("d20")["count"] == 1


def test_parse_case_and_spaces():
    spec = dice.parse("  4D6 KH3 ! +2 VS 15 ")
    assert spec == {"count": 4, "sides": 6, "keep": ("kh", 3), "explode": True,
                    "modifier": 2, "pool": None, "vs": 15}


def test_parse_negative_modifier():
    assert dice.parse("2d8-3")["modifier"] == -3


def test_parse_pool():
    spec = dice.parse("7d10t6")
    assert spec["pool"] == 6 and spec["vs"] is None and spec["modifier"] == 0


def test_parse_drop_lowest():
    assert dice.parse("4d6dl1")["keep"] == ("dl", 1)


@pytest.mark.parametrize("bad", [
    "", "garbage", "2x6", "0d6", "101d6", "2d1", "2d1001",
    "4d6kh5",      # keep more than rolled
    "4d6dh4",      # drop every die
    "4d6kh0",      # zero keep
    "3d10t7+2",    # pool with modifier
])
def test_parse_rejects(bad):
    with pytest.raises(dice.DiceError):
        dice.parse(bad)
```

- [ ] **Step 2: Run to verify failure**

Run (worktree root): `PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_dice.py -q`
Expected: FAIL — `ImportError: cannot import name 'dice'` / module missing.

- [ ] **Step 3: Implement**

```python
# backend/src/grimoire/store/dice.py
"""Dice notation parser + seeded roll engine (Mechanics & Dice Phase 2, #824).

Grammar (case-insensitive; spaces allowed between clauses):

    [N]dM [khn|kln|dhn|dln] [!] [+k|-k] [tN | vs N]

Sum rolls: total = kept dice + modifier; `vs N` grades success/failure.
Pool rolls (`tN`): count kept dice >= N as successes; modifiers are rejected.
Exploding (`!`): a max-face die rolls again — sum rolls chain the extra onto
the same die's value, pool rolls append a new die to the pool.

Pure module: no filesystem access. Every roll records its integer seed and
`roll(notation, seed)` replays it exactly (rolls.py builds replay on this).
"""

from __future__ import annotations

import random
import re
import secrets

MAX_DICE = 100
MAX_SIDES = 1000
MAX_EXPLOSIONS = 100  # per roll — bounds hostile notations without changing honest ones


class DiceError(ValueError):
    """Unparseable or out-of-range dice notation."""


_GRAMMAR = re.compile(
    r"^(?P<count>\d*)d(?P<sides>\d+)"
    r"(?:\s*(?P<keep>kh|kl|dh|dl)(?P<keep_n>\d+))?"
    r"(?:\s*(?P<explode>!))?"
    r"(?:\s*(?P<mod_sign>[+-])\s*(?P<mod>\d+))?"
    r"(?:\s*(?:t(?P<pool>\d+)|vs\s*(?P<vs>\d+)))?$"
)


def parse(notation: str) -> dict:
    m = _GRAMMAR.match(notation.strip().lower())
    if not m:
        raise DiceError(
            f"can't read dice notation {notation.strip()!r} — try 2d6+3, 4d6kh3, 7d10t6, or 5d6!")
    count = int(m["count"] or 1)
    sides = int(m["sides"])
    if not 1 <= count <= MAX_DICE:
        raise DiceError(f"dice count must be 1-{MAX_DICE}")
    if not 2 <= sides <= MAX_SIDES:
        raise DiceError(f"dice must have 2-{MAX_SIDES} sides")
    keep = (m["keep"], int(m["keep_n"])) if m["keep"] else None
    if keep is not None:
        op, n = keep
        if n < 1:
            raise DiceError("keep/drop count must be at least 1")
        if op in ("kh", "kl") and n > count:
            raise DiceError(f"can't keep {n} of {count} dice")
        if op in ("dh", "dl") and n >= count:
            raise DiceError("can't drop every die")
    modifier = int(m["mod"]) * (-1 if m["mod_sign"] == "-" else 1) if m["mod"] else 0
    pool = int(m["pool"]) if m["pool"] else None
    if pool is not None and modifier:
        raise DiceError("pool rolls (tN) don't take a +/- modifier")
    return {"count": count, "sides": sides, "keep": keep, "explode": bool(m["explode"]),
            "modifier": modifier, "pool": pool, "vs": int(m["vs"]) if m["vs"] else None}
```

- [ ] **Step 4: Run to verify pass** — same command, expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/dice.py backend/tests/test_dice.py
git commit -m "feat(backend): dice notation parser"
```

---

### Task 2: Roll engine (`dice.roll`)

**Files:**
- Modify: `backend/src/grimoire/store/dice.py` (append)
- Test: `backend/tests/test_dice.py` (append)

**Interfaces:**
- Consumes: `dice.parse` from Task 1.
- Produces: `dice.roll(notation: str, seed: int | None = None) -> dict` — the
  result dict from the header; `dice._roll_dice(rng, spec) -> list[dict]`
  (internal, tested directly for the explosion cap).

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_dice.py`)

```python
def test_roll_is_reproducible_from_seed():
    a = dice.roll("4d6kh3+2 vs 15", seed=42)
    b = dice.roll("4d6kh3+2 vs 15", seed=42)
    assert a == b
    assert a["seed"] == 42


def test_roll_generates_seed_when_absent():
    r = dice.roll("2d6")
    assert isinstance(r["seed"], int)
    assert dice.roll("2d6", seed=r["seed"]) == r


def test_roll_values_in_range_and_total():
    r = dice.roll("10d6", seed=7)
    assert len(r["dice"]) == 10
    assert all(1 <= d["value"] <= 6 for d in r["dice"])
    assert r["total"] == sum(d["value"] for d in r["dice"])
    assert r["successes"] is None and r["outcome"] is None


def test_roll_keep_highest():
    r = dice.roll("4d6kh3", seed=3)
    kept = [d["value"] for d in r["dice"] if d["kept"]]
    dropped = [d["value"] for d in r["dice"] if not d["kept"]]
    assert len(kept) == 3 and len(dropped) == 1
    assert min(kept) >= max(dropped)
    assert r["total"] == sum(kept)


def test_roll_drop_highest():
    r = dice.roll("3d6dh1", seed=3)
    dropped = [d["value"] for d in r["dice"] if not d["kept"]]
    assert len(dropped) == 1
    assert dropped[0] == max(d["value"] for d in r["dice"])


def test_roll_modifier_and_vs_outcomes():
    always = dice.roll("2d6+3 vs 1", seed=1)
    assert always["outcome"] == "success" and always["total"] >= 5
    never = dice.roll("2d6 vs 999", seed=1)
    assert never["outcome"] == "failure"


def test_roll_pool_counts_successes():
    r = dice.roll("7d10t6", seed=11)
    assert r["total"] is None
    assert r["successes"] == sum(1 for d in r["dice"] if d["value"] >= 6)
    assert r["pool_target"] == 6


def test_roll_sum_explosions_chain_onto_die():
    r = dice.roll("20d2!", seed=5)
    exploded = [d for d in r["dice"] if len(d["rolls"]) > 1]
    assert exploded, "20 d2 dice at seed 5 must include at least one max face"
    for d in exploded:
        assert all(x == 2 for x in d["rolls"][:-1])
        assert d["value"] == sum(d["rolls"])
    assert len(r["dice"]) == 20


def test_roll_pool_explosions_add_dice():
    r = dice.roll("20d2!t2", seed=5)
    assert len(r["dice"]) > 20
    assert all(len(d["rolls"]) == 1 for d in r["dice"])


class _MaxRng:
    """Always rolls the maximum face — forces endless explosions."""
    def randint(self, lo, hi):
        return hi


def test_explosion_budget_caps_hostile_notation():
    spec = dice.parse("1d2!")
    chain = dice._roll_dice(_MaxRng(), spec)
    assert len(chain) == 1
    assert len(chain[0]["rolls"]) == dice.MAX_EXPLOSIONS + 1
    pool_spec = dice.parse("1d2!t2")
    pool = dice._roll_dice(_MaxRng(), pool_spec)
    assert len(pool) == dice.MAX_EXPLOSIONS + 1
```

- [ ] **Step 2: Run to verify failure** — `... -m pytest backend/tests/test_dice.py -q`; expected: new tests FAIL (`AttributeError: module ... has no attribute 'roll'`).

- [ ] **Step 3: Implement** (append to `dice.py`)

```python
def _roll_dice(rng, spec: dict) -> list[dict]:
    """Roll the dice for a parsed spec. Sum explosions chain onto the die that
    exploded (its value is the chain sum); pool explosions append a new die.
    A shared per-roll budget bounds explosion count."""
    sides, explode = spec["sides"], spec["explode"]
    is_pool = spec["pool"] is not None
    budget = MAX_EXPLOSIONS
    dice_out: list[dict] = []
    to_roll = spec["count"]
    while to_roll:
        to_roll -= 1
        chain = [rng.randint(1, sides)]
        if is_pool:
            if explode and chain[-1] == sides and budget:
                budget -= 1
                to_roll += 1
        else:
            while explode and chain[-1] == sides and budget:
                budget -= 1
                chain.append(rng.randint(1, sides))
        dice_out.append({"value": sum(chain), "rolls": chain, "kept": True})
    return dice_out


def _apply_keep(dice_out: list[dict], keep: tuple[str, int] | None) -> None:
    if keep is None:
        return
    op, n = keep
    order = sorted(range(len(dice_out)), key=lambda i: dice_out[i]["value"])
    if op == "kh":
        dropped = order[:-n]
    elif op == "kl":
        dropped = order[n:]
    elif op == "dh":
        dropped = order[len(dice_out) - n:]
    else:  # dl
        dropped = order[:n]
    for i in dropped:
        dice_out[i]["kept"] = False


def roll(notation: str, seed: int | None = None) -> dict:
    """Resolve `notation`. Omitted seed is drawn from the OS CSPRNG (secrets),
    then all dice come from one random.Random(seed) — reproducible by design."""
    spec = parse(notation)
    if seed is None:
        seed = secrets.randbits(64)
    rng = random.Random(seed)
    dice_out = _roll_dice(rng, spec)
    _apply_keep(dice_out, spec["keep"])
    result = {"notation": notation.strip(), "seed": seed, "dice": dice_out,
              "modifier": spec["modifier"], "pool_target": spec["pool"], "vs": spec["vs"],
              "total": None, "successes": None, "outcome": None}
    if spec["pool"] is not None:
        result["successes"] = sum(
            1 for d in dice_out if d["kept"] and d["value"] >= spec["pool"])
    else:
        result["total"] = sum(d["value"] for d in dice_out if d["kept"]) + spec["modifier"]
        if spec["vs"] is not None:
            result["outcome"] = "success" if result["total"] >= spec["vs"] else "failure"
    return result
```

Note: if `test_roll_sum_explosions_chain_onto_die` finds no exploded die at
seed 5, don't weaken the assertion — pick another literal seed that does
explode (probability a seed has zero max faces in 20 d2 rolls is 2⁻²⁰).

- [ ] **Step 4: Run to verify pass** — same command; all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/dice.py backend/tests/test_dice.py
git commit -m "feat(backend): seeded dice roll engine with keep/drop, pools, explosions"
```

---

### Task 3: Transcript line formatting (`dice.format_roll`)

**Files:**
- Modify: `backend/src/grimoire/store/dice.py` (append)
- Test: `backend/tests/test_dice.py` (append)

**Interfaces:**
- Consumes: result dict from `dice.roll`.
- Produces: `dice.format_roll(result: dict, label: str | None = None) -> str` —
  one markdown line for `scenes.append_message`.

- [ ] **Step 1: Write the failing tests** (append to `backend/tests/test_dice.py`)

```python
def test_format_sum_roll_strikes_dropped_dice():
    r = dice.roll("4d6kh3+2 vs 15", seed=42)
    line = dice.format_roll(r)
    assert line.startswith("🎲 `4d6kh3+2 vs 15`")
    assert f"**{r['total']}**" in line
    assert "vs 15" in line and f"**{r['outcome']}**" in line
    dropped = [d for d in r["dice"] if not d["kept"]]
    assert dropped and f"~~{dropped[0]['value']}~~" in line


def test_format_pool_roll_pluralizes():
    line = dice.format_roll({"notation": "2d10t6", "seed": 1, "modifier": 0,
                             "pool_target": 6, "vs": None, "total": None,
                             "successes": 1, "outcome": None,
                             "dice": [{"value": 7, "rolls": [7], "kept": True},
                                      {"value": 2, "rolls": [2], "kept": True}]})
    assert "**1 success**" in line
    line2 = dice.format_roll({"notation": "2d10t6", "seed": 1, "modifier": 0,
                              "pool_target": 6, "vs": None, "total": None,
                              "successes": 2, "outcome": None,
                              "dice": [{"value": 7, "rolls": [7], "kept": True},
                                       {"value": 9, "rolls": [9], "kept": True}]})
    assert "**2 successes**" in line2


def test_format_label_and_exploded_marker():
    r = {"notation": "1d6!", "seed": 1, "modifier": 0, "pool_target": None,
         "vs": None, "total": 10, "successes": None, "outcome": None,
         "dice": [{"value": 10, "rolls": [6, 4], "kept": True}]}
    line = dice.format_roll(r, label="Aveline — Stealth")
    assert line.startswith("🎲 **Aveline — Stealth** · `1d6!`")
    assert "[10!]" in line
```

- [ ] **Step 2: Run to verify failure** — expected FAIL (`no attribute 'format_roll'`).

- [ ] **Step 3: Implement** (append to `dice.py`)

```python
def _face(d: dict) -> str:
    marker = "!" if len(d["rolls"]) > 1 else ""
    face = f"{d['value']}{marker}"
    return face if d["kept"] else f"~~{face}~~"


def format_roll(result: dict, label: str | None = None) -> str:
    """One markdown transcript line: dropped dice struck through, sum-exploded
    dice marked `!`, outcome bolded."""
    faces = ", ".join(_face(d) for d in result["dice"])
    head = (f"🎲 **{label}** · `{result['notation']}`" if label
            else f"🎲 `{result['notation']}`")
    if result["successes"] is not None:
        n = result["successes"]
        return f"{head} → [{faces}] — **{n} success{'' if n == 1 else 'es'}**"
    line = f"{head} → [{faces}]"
    if result["modifier"]:
        line += f" {'+' if result['modifier'] > 0 else '−'} {abs(result['modifier'])}"
    line += f" = **{result['total']}**"
    if result["vs"] is not None:
        line += f" vs {result['vs']} — **{result['outcome']}**"
    return line
```

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/dice.py backend/tests/test_dice.py
git commit -m "feat(backend): markdown formatting for roll results"
```

---

### Task 4: Roll log store (`store/rolls.py`)

**Files:**
- Create: `backend/src/grimoire/store/rolls.py`
- Test: `backend/tests/test_rolls_store.py`

**Interfaces:**
- Consumes: `dice.roll`; `campaigns.campaign_root`; `paths.now_iso`.
- Produces: `rolls.read(cid) -> list[dict]`;
  `rolls.append(cid, scene: str | None, label: str | None, result: dict) -> dict`
  (the stored entry: `{"id", "ts", "scene", "label", "result"}`);
  `rolls.get(cid, rid) -> dict` (raises `rolls.RollNotFound`);
  `rolls.replay(cid, rid) -> dict` (`{"entry", "result", "match": bool}`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_rolls_store.py
import json

import pytest

from grimoire.store import campaigns, dice, rolls, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def test_read_missing_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert rolls.read(cid) == []


def test_read_garbled_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "rolls.json").write_text("{not json", encoding="utf-8")
    assert rolls.read(cid) == []
    (campaigns.campaign_root(cid) / "rolls.json").write_text('{"a": 1}', encoding="utf-8")
    assert rolls.read(cid) == []


def test_append_assigns_sequential_ids_and_persists(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    e1 = rolls.append(cid, "s1", "Perception", dice.roll("2d6", seed=1))
    e2 = rolls.append(cid, None, None, dice.roll("d20", seed=2))
    assert e1["id"] == "r1" and e2["id"] == "r2"
    assert e1["scene"] == "s1" and e1["label"] == "Perception" and e1["ts"]
    on_disk = json.loads((campaigns.campaign_root(cid) / "rolls.json").read_text(encoding="utf-8"))
    assert [e["id"] for e in on_disk] == ["r1", "r2"]


def test_get_and_missing(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    entry = rolls.append(cid, None, None, dice.roll("2d6", seed=1))
    assert rolls.get(cid, "r1") == entry
    with pytest.raises(rolls.RollNotFound):
        rolls.get(cid, "r99")


def test_replay_matches_stored_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rolls.append(cid, "s1", None, dice.roll("4d6kh3+2 vs 15", seed=42))
    out = rolls.replay(cid, "r1")
    assert out["match"] is True
    assert out["result"] == out["entry"]["result"]


def test_replay_detects_tampering(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    rolls.append(cid, None, None, dice.roll("2d6", seed=3))
    p = campaigns.campaign_root(cid) / "rolls.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data[0]["result"]["total"] = 999
    p.write_text(json.dumps(data), encoding="utf-8")
    assert rolls.replay(cid, "r1")["match"] is False
```

- [ ] **Step 2: Run to verify failure** — `... -m pytest backend/tests/test_rolls_store.py -q`; expected: ImportError on `rolls`.

- [ ] **Step 3: Implement**

```python
# backend/src/grimoire/store/rolls.py
"""Append-only per-campaign roll log at <campaign>/rolls.json (Phase 2, #824).

Each entry stores the full engine result — notation and seed included — so any
roll can be replayed bit-for-bit: `replay` re-runs the engine with the stored
seed and reports whether it still matches. Pure JSON IO in the style of
changes.py; entries are never rewritten or deleted, so ids are positional.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, dice
from .paths import now_iso


class RollNotFound(Exception):
    pass


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "rolls.json"


def read(cid: str) -> list[dict]:
    p = _path(cid)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def append(cid: str, scene: str | None, label: str | None, result: dict) -> dict:
    entries = read(cid)
    entry = {"id": f"r{len(entries) + 1}", "ts": now_iso(),
             "scene": scene, "label": label, "result": result}
    entries.append(entry)
    _path(cid).write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    return entry


def get(cid: str, rid: str) -> dict:
    for entry in read(cid):
        if entry.get("id") == rid:
            return entry
    raise RollNotFound(rid)


def replay(cid: str, rid: str) -> dict:
    """Re-run a logged roll from its stored seed; `match` is the replay guarantee."""
    entry = get(cid, rid)
    result = dice.roll(entry["result"]["notation"], entry["result"]["seed"])
    return {"entry": entry, "result": result, "match": result == entry["result"]}
```

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/rolls.py backend/tests/test_rolls_store.py
git commit -m "feat(backend): append-only replayable roll log"
```

---

### Task 5: Routes + store exports

**Files:**
- Modify: `backend/src/grimoire/store/__init__.py`
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py` (append)

**Interfaces:**
- Consumes: `dice.roll`, `dice.format_roll`, `dice.DiceError`, `rolls.append`,
  `rolls.read`, `rolls.replay`, `rolls.RollNotFound`, `scenes.append_message`,
  `routes._require_scene`.
- Produces:
  - `POST /api/campaigns/{cid}/scenes/{sid}/roll` body `{notation, label?}` →
    `{"ok": true, "roll": <entry>, "message": <markdown line>}`; 400 on bad
    notation, 404 on missing scene.
  - `GET /api/campaigns/{cid}/rolls` → entries newest-first; 404 on missing campaign.
  - `POST /api/campaigns/{cid}/rolls/{rid}/replay` → `{"ok": true, "entry", "result", "match"}`; 404 on missing roll.

- [ ] **Step 1: Wire the store package.** In
`backend/src/grimoire/store/__init__.py`: add `dice` (after `context`) and
`rolls` (after `relationships`) to the `from . import (...)` tuple; add

```python
from .dice import DiceError
from .rolls import RollNotFound
```

with the other exception imports (alphabetical), and add `"dice"`, `"rolls"`,
`"DiceError"`, `"RollNotFound"` to `__all__` following its existing ordering.

- [ ] **Step 2: Write the failing route tests** (append to `backend/tests/test_routes.py`)

```python
# ---- dice rolls ----
def _scene(client, cid, title="S"):
    return client.post(f"/api/campaigns/{cid}/scenes", json={"title": title}).json()["id"]


def test_scene_roll_logs_and_posts_to_scene(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll",
                    json={"notation": "2d6+3", "label": "Perception"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["roll"]["id"] == "r1" and body["roll"]["scene"] == sid
    assert "Perception" in body["message"] and "2d6+3" in body["message"]
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "assistant"
    assert "🎲" in msgs[0]["content"]


def test_scene_roll_bad_notation_is_400(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "garbage"})
    assert r.status_code == 400
    assert "dice notation" in r.json()["detail"]


def test_scene_roll_missing_scene_is_404(client):
    _, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scenes/nope/roll", json={"notation": "2d6"})
    assert r.status_code == 404


def test_rolls_listing_newest_first(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "2d6"})
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "d20"})
    listing = client.get(f"/api/campaigns/{cid}/rolls").json()
    assert [e["id"] for e in listing] == ["r2", "r1"]


def test_rolls_listing_missing_campaign_is_404(client):
    assert client.get("/api/campaigns/nope/rolls").status_code == 404


def test_roll_replay_roundtrip(client):
    _, cid = _campaign(client)
    sid = _scene(client, cid)
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/roll", json={"notation": "4d6kh3"})
    r = client.post(f"/api/campaigns/{cid}/rolls/r1/replay")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["match"] is True


def test_roll_replay_missing_is_404(client):
    _, cid = _campaign(client)
    assert client.post(f"/api/campaigns/{cid}/rolls/r9/replay").status_code == 404
```

- [ ] **Step 3: Run to verify failure** — `... -m pytest backend/tests/test_routes.py -q -k roll`; expected: 404/405 mismatches (routes absent).

- [ ] **Step 4: Implement the routes.** In `backend/src/grimoire/routes.py`:
add to the models section

```python
class RollBody(BaseModel):
    notation: str
    label: str | None = None
```

and add the routes after `put_scene_datetime` (keeps scene routes together):

```python
@router.post("/campaigns/{cid}/scenes/{sid}/roll")
def post_scene_roll(cid: str, sid: str, body: RollBody):
    """Manual dice roll: resolve, log to <campaign>/rolls.json, and write the
    result into the scene transcript as a narrator line."""
    _require_scene(cid, sid)
    try:
        result = store.dice.roll(body.notation)
    except store.dice.DiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    entry = store.rolls.append(cid, sid, body.label or None, result)
    line = store.dice.format_roll(result, body.label or None)
    store.scenes.append_message(cid, sid, "assistant", line)
    return {"ok": True, "roll": entry, "message": line}


@router.get("/campaigns/{cid}/rolls")
def get_rolls(cid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    return list(reversed(store.rolls.read(cid)))


@router.post("/campaigns/{cid}/rolls/{rid}/replay")
def post_roll_replay(cid: str, rid: str):
    try:
        return {"ok": True, **store.rolls.replay(cid, rid)}
    except store.rolls.RollNotFound:
        raise HTTPException(status_code=404, detail="roll not found")
```

Route ordering caveat: these paths must be registered **before** the generic
`GET/POST /campaigns/{cid}/{kind}` entity routes in the file (which match
`/campaigns/x/rolls` too) — placing them after `put_scene_datetime`
(~line 1988) satisfies this; do not append them at the end of the file.

- [ ] **Step 5: Run to verify pass** — `... -m pytest backend/tests/test_routes.py backend/tests/test_dice.py backend/tests/test_rolls_store.py -q`; then the full backend suite `... -m pytest backend -q`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/__init__.py backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): scene roll, roll log, and replay routes"
```

---

### Task 6: Play-view Roll popover (frontend)

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx` (append)

**Interfaces:**
- Consumes: `POST /api/campaigns/{cid}/scenes/{sid}/roll` from Task 5.
- Produces: `api.roll(cid, sid, notation, label?)`; a `🎲` button
  (aria-label "Roll dice") in `.inputbar` opening a `.roll-pop` popover.

- [ ] **Step 1: Write the failing tests** (append to
`frontend/src/routes/CampaignView.test.tsx`). Add `roll: vi.fn(),` to the
mocked `api` object (next to `chat`/`retry`), then reuse the file's existing
render helper (the pattern other tests in this file use to mount
`<CampaignView keySet />` under a `MemoryRouter` at `/campaigns/run`):

```tsx
describe("dice rolls", () => {
  it("rolls dice from the input bar popover and refreshes the scene", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    (api.roll as any).mockResolvedValue({ ok: true, roll: { id: "r1" }, message: "🎲" });
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));
    fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });
    fireEvent.change(screen.getByLabelText("Roll label"), { target: { value: "Perception" } });
    fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
    await waitFor(() => expect(api.roll).toHaveBeenCalledWith("run", "s1", "2d6+1", "Perception"));
    // popover closes and the scene re-fetches to show the roll line
    await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
    expect((api.getScene as any).mock.calls.length).toBeGreaterThan(1);
  });

  it("shows a roll error and keeps the popover open", async () => {
    (api.listScenes as any).mockResolvedValue(ONE_SCENE);
    (api.roll as any).mockRejectedValue({ detail: "can't read dice notation 'garbage'" });
    renderView();
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));
    fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "garbage" } });
    fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
    await screen.findByText(/can't read dice notation/);
    expect(screen.getByLabelText("Dice notation")).toBeInTheDocument();
  });
});
```

If the file's render helper has a different name than `renderView`, use that
name; if none exists, add one that mounts
`<Routes><Route path="/campaigns/:cid" element={<CampaignView keySet />} /></Routes>`
in a `MemoryRouter` with initial entry `/campaigns/run`.

- [ ] **Step 2: Run to verify failure** — from `frontend/`:
`npx vitest run src/routes/CampaignView.test.tsx`; expected: FAIL (no "Roll dice" button).

- [ ] **Step 3: Implement.**

`frontend/src/api/client.ts` — types near the other exported types, api
methods next to `chat`/`retry`/`regenerate`:

```ts
export type DieDetail = { value: number; rolls: number[]; kept: boolean };
export type RollResult = {
  notation: string; seed: number; dice: DieDetail[]; modifier: number;
  pool_target: number | null; vs: number | null;
  total: number | null; successes: number | null; outcome: string | null;
};
export type RollEntry = {
  id: string; ts: string; scene: string | null; label: string | null; result: RollResult;
};
```

```ts
  roll: (cid: string, sid: string, notation: string, label?: string) =>
    request<{ ok: boolean; roll: RollEntry; message: string }>(
      "POST", `/api/campaigns/${cid}/scenes/${sid}/roll`,
      { notation, ...(label ? { label } : {}) }),
  listRolls: (cid: string) => request<RollEntry[]>("GET", `/api/campaigns/${cid}/rolls`),
```

`frontend/src/routes/CampaignView.tsx` — state next to `rerollPrompt`:

```tsx
  // null = closed; open holds the in-progress notation/label/error
  const [rollForm, setRollForm] = useState<{ notation: string; label: string; error: string | null } | null>(null);
```

handler next to `reroll()`:

```tsx
  async function doRoll() {
    if (!activeId || busy || !rollForm) return;
    const notation = rollForm.notation.trim();
    if (!notation) return;
    try {
      await api.roll(cid, activeId, notation, rollForm.label.trim() || undefined);
      setRollForm(null);
      await selectScene(activeId);
    } catch (err: any) {
      setRollForm({ ...rollForm, error: err.detail ?? String(err) });
    }
  }
```

`.inputbar` JSX — add the button and popover before the `<textarea>`:

```tsx
        <div className="inputbar">
          <button className="roll-btn" title="Roll dice" aria-label="Roll dice"
                  disabled={!activeId || busy}
                  onClick={() => setRollForm((f) => (f ? null : { notation: "", label: "", error: null }))}>
            🎲
          </button>
          {rollForm && (
            <div className="roll-pop">
              <input
                autoFocus
                placeholder="2d6+3, 4d6kh3, 7d10t6…"
                aria-label="Dice notation"
                value={rollForm.notation}
                onChange={(e) => setRollForm({ ...rollForm, notation: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter") doRoll();
                  if (e.key === "Escape") setRollForm(null);
                }}
              />
              <input
                placeholder="Label (optional)"
                aria-label="Roll label"
                value={rollForm.label}
                onChange={(e) => setRollForm({ ...rollForm, label: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter") doRoll();
                  if (e.key === "Escape") setRollForm(null);
                }}
              />
              <button className="btn-chrome" onClick={doRoll}>Roll ▸</button>
              {rollForm.error && <span className="roll-error">{rollForm.error}</span>}
            </div>
          )}
          <textarea
```

(the rest of the input bar is unchanged).

`frontend/src/index.css` — `.inputbar` (line ~229) gains `position: relative;`,
and next to `.reroll-pop` (~line 599) add:

```css
.inputbar .roll-btn { border: none; border-right: var(--rw) solid var(--rule); background: none;
  padding: 0 12px; cursor: pointer; font-size: 15px; }
.inputbar .roll-btn:disabled { opacity: 0.4; cursor: default; }
.roll-pop { position: absolute; bottom: 100%; left: 0; margin-bottom: 8px; z-index: 5;
  display: flex; align-items: center; flex-wrap: wrap; background: var(--page);
  border: var(--rw) solid var(--rule); box-shadow: 0 4px 14px rgba(0,0,0,.18); }
.roll-pop input { border: none; border-right: var(--rw) solid var(--rule); background: none;
  padding: 7px 10px; font-size: 12px; width: 170px; }
.roll-pop .btn-chrome { border: none; padding: 7px 12px; box-shadow: none; font-size: 11px;
  white-space: nowrap; }
.roll-pop .roll-error { flex-basis: 100%; padding: 5px 10px; font-size: 11px;
  color: var(--danger, #b3261e); border-top: var(--rw) solid var(--rule); }
```

(match the file's existing variable names; if `--danger` doesn't exist, use the
color the error banner uses).

- [ ] **Step 4: Run to verify pass** — from `frontend/`:
`npx vitest run` (whole suite) and `npx tsc -b`. Expected: all pass, tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/CampaignView.tsx frontend/src/index.css frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(frontend): manual dice roll popover in the play view"
```

---

### Task 7: Full verification

- [ ] **Step 1: Backend suite** — worktree root:
`PYTHONPATH="$(cygpath -w "$PWD/backend/src")" ../../backend/.venv/Scripts/python.exe -m pytest backend -q` → 0 failures.
- [ ] **Step 2: Frontend suite** — from `frontend/`: `npx vitest run` and `npx tsc -b` → 0 failures, tsc clean.
- [ ] **Step 3: Commit anything outstanding** (there should be nothing).

## Self-review notes

- Spec coverage: notation parser (`NdM+k`, keep/drop, pools with TN, exploding)
  → Tasks 1–2; seeded `random.Random` per roll → Task 2; append-only
  `<campaign>/rolls.json` with seed+notation+result, replayable → Task 4;
  replay API → Tasks 4–5; manual Roll affordance writing into the scene →
  Tasks 5–6. Outcome tiers beyond success/failure/successes are Phase 1/3
  territory (checks.json) — deliberately out.
- Null fall-through: no context-builder or module-gated behavior is touched;
  the roller is a module-independent table utility (noted in the preamble).
