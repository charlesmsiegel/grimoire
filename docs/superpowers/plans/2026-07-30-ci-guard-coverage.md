# CI Guard Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give grimoire a CI gate that automatically runs every guard the project already owns, plus the two Android conventions that were previously enforced by nothing.

**Architecture:** One `make` target per guard, and a thin `.github/workflows/ci.yml` that calls one target per job — so the same one-line command reproduces any CI failure locally, and `CLAUDE.md` stops being a second, drifting copy of the invocations. The two new convention guards are AST-walking pytest tests in `backend/tests/`, following `test_atomic_guard.py`, so they run in every pytest invocation rather than only in CI.

**Tech Stack:** GNU make (recipes pinned to `cmd.exe` on Windows), GitHub Actions, pytest, ruff 0.16.0, vitest, Gradle 8.7 / AGP 8.5.2 / Chaquopy 15.0.1.

**Spec:** `docs/superpowers/specs/2026-07-30-ci-guard-coverage-design.md`

## Global Constraints

- Python floor is **3.11** (`backend/pyproject.toml: requires-python = ">=3.11"`); CI matrix is **3.11 and 3.14**, Ubuntu only.
- `backend/pyproject.toml` **base** deps must stay Android-installable. `ruff==0.16.0` goes in the **`dev`** extra only.
- pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields, dump via `routes.common._dump`.
- Guard marker constants **must include the trailing colon** (`"paths-ok:"`), and exemption checks must test `if reason:` — `marker_reason` returns `""` for a bare marker, and `":"` if the colon is left out of the constant.
- No CI secrets. Offline replay evals only; never `evals/run.py --live`.
- No test may be skipped, `xfail`ed, or made `continue-on-error` to reach green.
- Never commit a real world/campaign/character name. Use existing placeholders (Seraphine, Mara, Winifred, Realm, Saltmarch).
- Local verification command (in a worktree, `PYTHONPATH` must shadow the editable install):
  `PYTHONPATH="$(pwd -W)/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`

---

### Task 1: Fix `scripts/verify_templates.py`

**Files:**
- Modify: `scripts/verify_templates.py:301-302`

**Interfaces:**
- Consumes: nothing.
- Produces: a `verify_templates.py` that exits 0, which Task 5's `check-templates` target depends on.

- [ ] **Step 1: Confirm the current failure**

Run: `../../backend/.venv/Scripts/python.exe scripts/verify_templates.py; echo "EXIT=$?"`
Expected: traceback ending `grimoire.store.campaigns.CampaignNotFound`, `EXIT=1`.

- [ ] **Step 2: Read the signature that changed**

`backend/src/grimoire/store/relationships.py:63` is `def actor_name(cid: str, token: str) -> str`. Commit `e1e7cb0b` changed the first parameter from a croot to a cid and updated the three `store/absorb.py` callers, but not this script.

- [ ] **Step 3: Pass `cid` instead of `croot`**

In `scripts/verify_templates.py`, change:

```python
    relationship_lines = relationships.render_present(
        cid, tokens, lambda t: relationships.actor_name(croot, t))
```

to:

```python
    relationship_lines = relationships.render_present(
        cid, tokens, lambda t: relationships.actor_name(cid, t))
```

- [ ] **Step 4: Run it again**

Run: `../../backend/.venv/Scripts/python.exe scripts/verify_templates.py; echo "EXIT=$?"`
Expected: `EXIT=0`. If a *different* failure appears, fix that too — the script's output has not been seen in 20 days and further findings are in scope (spec §6).

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_templates.py
git commit -m "Fix verify_templates.py against the actor_name signature change"
```

---

### Task 2: Ruff config, pin, and triage to zero

**Files:**
- Create: `ruff.toml`
- Modify: `backend/pyproject.toml` (`dev` extra)
- Modify: ~30 files across `backend/src`, `backend/tests`, `backend/scripts`

**Interfaces:**
- Consumes: nothing.
- Produces: a repo where `ruff check .` exits 0, which Task 5's `check-lint` target depends on.

- [ ] **Step 1: Write the config**

Create `ruff.toml`:

```toml
target-version = "py311"
line-length = 100                 # documentation only; E501 is not selected
exclude = ["backend/.venv", "node_modules", "build", "dist", ".worktrees", "OLD"]

[lint]
select = ["F", "BLE", "E9", "E402", "RUF100"]

[lint.per-file-ignores]
"backend/src/grimoire/store/__init__.py"  = ["F401"]  # 60-submodule aggregation
"backend/src/grimoire/routes/__init__.py" = ["F401"]  # router aggregation
```

- [ ] **Step 2: Add the pinned dev dependency**

In `backend/pyproject.toml`, the `dev` extra becomes:

```toml
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff==0.16.0",
]
```

- [ ] **Step 3: Confirm the baseline count**

Run: `../../backend/.venv/Scripts/python.exe -m ruff check . --statistics`
Expected: 58 errors — 17 `E402`, 17 `F401`, 10 `RUF100`, 7 `F841`, 6 `BLE001`, 1 `F541`.

- [ ] **Step 4: Delete the 10 stale `noqa` markers (`RUF100`)**

Run `ruff check . --select RUF100 --output-format concise` is **wrong** — a bare
`--select` overrides the config and changes RUF100's own verdicts. Use
`ruff check . --output-format concise | grep RUF100`.

Delete the marker (not the code) at each: `backend/scripts/ingest_scene.py:16,17,20`, `backend/scripts/redownload_chub.py:30`, `backend/tests/test_ingest_scene.py:7,8`, `backend/src/grimoire/routes/streaming.py:183` (`E731`), `backend/src/grimoire/store/cards.py:145` (`BLE001`, handler re-raises), `backend/tests/test_modules_store.py:758` (same), `backend/src/grimoire/store/climates/__init__.py:14` (`F401`).

- [ ] **Step 5: Fix the 17 `F401` unused imports**

Auto-fix the unambiguous ones: `../../backend/.venv/Scripts/python.exe -m ruff check . --select F401 --fix`

Then hand-check these three, where a submodule import may exist for a registration side-effect — read the module and confirm nothing depends on import order before removing: `store/absorb.py:12` (`.chronicle`), `store/overlay.py:37` (`.worlds`), `store/calendars/config.py:10` (`CalendarError`).

- [ ] **Step 6: Fix the 7 `F841` unused variables and 1 `F541` empty f-string**

Read each site and either remove the binding or use it. Do not blanket-`noqa`: an unused variable is a real smell, and the whole point of this branch is that suppressed findings rot.

- [ ] **Step 7: Annotate the 5 deliberate blind excepts, narrow the 1 test**

Each of `store/calendars/plugins.py:47`, `store/climates/__init__.py:35`, `store/context.py:732`, `store/module_display.py:364`, `store/overlay.py:140` already carries a prose comment explaining deliberate containment. Add `# noqa: BLE001` with that reason on the `except` line, matching the existing style, e.g.:

```python
    except Exception:  # noqa: BLE001 - garbled plugin: omit, don't crash the calendar list
```

`backend/tests/test_path_guard_store.py:530` gets its exception narrowed to what the test actually expects — a test has no containment argument.

- [ ] **Step 8: Add the 17 missing `E402` markers**

`test_llm.py` (6), `test_epub_store.py` (5), `test_calendars.py` (3), `test_changes_store.py` (2), `test_modules_store.py` (1). Each is the same deliberate late-import pattern as the 15 markers that already exist. Add `# noqa: E402` where the late import is deliberate; hoist the import instead where it is incidental.

- [ ] **Step 9: Verify zero, and that the suite still passes**

Run: `../../backend/.venv/Scripts/python.exe -m ruff check .`
Expected: `All checks passed!`

Run: `PYTHONPATH="$(pwd -W)/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: 2705 passed. (Step 5 removed imports — this is what catches a removal that mattered.)

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "Add a ruff gate and clear the 58 findings it reports"
```

---

### Task 3: `test_pydantic_guard.py`

**Files:**
- Create: `backend/tests/test_pydantic_guard.py`
- Modify: `backend/src/grimoire/routes/common.py` (add the exemption marker)

**Interfaces:**
- Consumes: `guard_markers.marker_reason(marker, src, node, others)` from `backend/tests/guard_markers.py`.
- Produces: `scan(src: str) -> list[tuple[ast.AST, str]]` — flagged `(node, api_name)` pairs, used by both the repo scan and the snippet tests in this task.

- [ ] **Step 1: Write the failing snippet tests**

Create `backend/tests/test_pydantic_guard.py`:

```python
"""Guard: pydantic usage stays v1/v2-agnostic, so the Android pydantic-1.10 pin
stays an install-time choice rather than a code change (CLAUDE.md, Android).

The rule is stricter than "no v2-only API": `Field`, `validator` and
`root_validator` all exist in pydantic 1.10, and are banned anyway because the
project's models are plain typed fields. Request parsing then behaves
identically on both lines.

Reach, stated honestly: a method-shaped call like `obj.model_dump()` can only be
matched on the attribute name, so an unrelated object with a same-named method is
a false positive — the exemption marker is the remedy. A fully dynamic call
(`getattr(m, "model_" + "dump")()`) is not matched at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import guard_markers

MARKER = "pydantic-ok:"          # the colon is load-bearing; see the module docstring in guard_markers
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"

V2_ONLY = {
    "model_dump", "model_dump_json", "model_validate", "model_validate_json",
    "model_json_schema", "model_copy", "model_construct", "model_rebuild",
    "model_fields", "model_fields_set", "model_config", "ConfigDict",
    "TypeAdapter", "RootModel", "field_validator", "model_validator",
    "field_serializer", "model_serializer", "computed_field", "validate_call",
}
PROJECT_BANNED = {"Field", "validator", "root_validator"}
BANNED = V2_ONLY | PROJECT_BANNED


def _pydantic_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """(local names bound to the pydantic module, {local name: pydantic name}).

    Name-only matching is trivially evaded by `from pydantic import ConfigDict as
    CD`, so bindings are resolved per module before anything is flagged.
    """
    modules: set[str] = set()
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "pydantic" or a.name.startswith("pydantic."):
                    modules.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "pydantic" or mod.startswith("pydantic."):
                for a in node.names:
                    aliases[a.asname or a.name] = a.name
    return modules, aliases


def scan(src: str) -> list[tuple[ast.AST, str]]:
    """Flagged (node, api name) pairs for one module's source."""
    tree = ast.parse(src)
    _modules, aliases = _pydantic_bindings(tree)
    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        # `pydantic.ConfigDict()`, `obj.model_dump()`, `m.model_fields`
        if isinstance(node, ast.Attribute) and node.attr in BANNED:
            found.append((node, node.attr))
        # a bare name that a pydantic import bound
        elif isinstance(node, ast.Name) and aliases.get(node.id) in BANNED:
            found.append((node, aliases[node.id]))
        # `model_config = {...}` in a class body mentions no pydantic name and
        # is not a call, so an import-aware matcher alone would miss it
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                targets = (stmt.targets if isinstance(stmt, ast.Assign)
                           else [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "model_config":
                        found.append((stmt, "model_config"))
    return found


PROHIBITED = [
    ("attribute call", "def f(m):\n    return m.model_dump()\n"),
    ("qualified", "import pydantic\nx = pydantic.ConfigDict()\n"),
    ("aliased import", "from pydantic import ConfigDict as CD\nx = CD()\n"),
    ("aliased field", "from pydantic import Field as F\nclass M:\n    a: int = F(1)\n"),
    ("bare decorator", "from pydantic import validator\n@validator\ndef f():\n    pass\n"),
    ("decorator call", "from pydantic import field_validator\n@field_validator('a')\ndef f():\n    pass\n"),
    ("class dict config", "class M:\n    model_config = {'extra': 'forbid'}\n"),
    ("annotated config", "class M:\n    model_config: dict = {'extra': 'forbid'}\n"),
]


@pytest.mark.parametrize("label,src", PROHIBITED, ids=[p[0] for p in PROHIBITED])
def test_prohibited_forms_are_flagged(label, src):
    assert scan(src), f"{label} slipped past the guard"


ALLOWED = [
    ("plain model", "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n"),
    ("unrelated validator function", "def validator(x):\n    return x\nvalidator(1)\n"),
    ("unrelated dict", "d = {'model_config': 1}\n"),
    ("module-level name", "model_config = 1\n"),
]


@pytest.mark.parametrize("label,src", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_allowed_forms_are_not_flagged(label, src):
    assert not scan(src), f"{label} was flagged and should not be"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `PYTHONPATH="$(pwd -W)/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_pydantic_guard.py -q`
Expected: collection error (`guard_markers` import is unused so far is fine) — the parametrized cases should be the only failures if `scan` has a bug. Fix `scan` until all 12 pass.

Note: `test_allowed_forms_are_not_flagged["unrelated validator function"]` is the case that proves matching is import-aware; if it fails, `scan` is matching bare names without consulting `aliases`.

- [ ] **Step 3: Add the marker tests**

Append:

```python
def _reason(src: str, node: ast.AST, others=()) -> str | None:
    return guard_markers.marker_reason(MARKER, src, node, others)


def test_a_valid_marker_exempts_the_call():
    src = "def f(m):\n    return m.model_dump()  # pydantic-ok: v1/v2 shim\n"
    node, _ = scan(src)[0]
    assert _reason(src, node) == "v1/v2 shim"


def test_a_reasonless_marker_does_not_exempt():
    src = "def f(m):\n    return m.model_dump()  # pydantic-ok:\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node), "a bare marker must not silence the guard"


def test_a_marker_inside_a_string_does_not_exempt():
    src = "def f(m):\n    msg = '# pydantic-ok: nope'\n    return m.model_dump()\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node)
```

- [ ] **Step 4: Add the repository scan, with the non-vacuity assertion**

Append:

```python
def test_the_package_uses_no_v2_pydantic_api():
    """The real scan. Every flagged call must carry a reasoned exemption."""
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 50, f"the scan found only {len(files)} files — glob broken?"

    violations, exempted = [], []
    for path in files:
        src = path.read_text(encoding="utf-8")
        found = scan(src)
        nodes = [n for n, _ in found]
        for node, api in found:
            others = [o for o in nodes if o is not node]
            if _reason(src, node, others):
                exempted.append((path.name, api))
            else:
                rel = path.relative_to(SRC.parents[1]).as_posix()
                violations.append(f"{rel}:{node.lineno} uses {api}")

    assert not violations, (
        "pydantic v2 API outside routes/common._dump (CLAUDE.md):\n  "
        + "\n  ".join(violations))
    # Non-vacuous: the one sanctioned site must still be found and exempted, so
    # a broken scanner cannot pass by finding nothing at all.
    assert exempted, "the scan found no exempted call — scanner or marker broken"
```

- [ ] **Step 5: Run it and read the violations**

Run: `PYTHONPATH="$(pwd -W)/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_pydantic_guard.py -q`
Expected: `test_the_package_uses_no_v2_pydantic_api` FAILS, listing the `model_dump` uses in `routes/common.py`.

- [ ] **Step 6: Mark the one sanctioned site**

In `backend/src/grimoire/routes/common.py`, add the marker to each flagged call inside `_dump`, e.g.:

```python
        return m.model_dump()  # pydantic-ok: the v1/v2 shim itself; every other module calls this
```

If any *other* file is listed, that is a real finding — fix the code, do not mark it.

- [ ] **Step 7: Verify green**

Run: `PYTHONPATH="$(pwd -W)/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_pydantic_guard.py -q`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add backend/tests/test_pydantic_guard.py backend/src/grimoire/routes/common.py
git commit -m "Guard the pydantic v1/v2-agnostic rule with an AST test"
```

---

### Task 4: `test_paths_guard.py`

**Files:**
- Create: `backend/tests/test_paths_guard.py`
- Modify: `backend/src/grimoire/store/paths.py` (4 markers), `store/proclock.py` (1), `main.py` (1), `prompts.py` (1), `store/epub.py` (1)

**Interfaces:**
- Consumes: `guard_markers.marker_reason`.
- Produces: `scan(src: str) -> list[tuple[ast.AST, str]]` — same shape as Task 3's, but a separate module-level function; the two guards do not share a scanner.

- [ ] **Step 1: Write the snippet tests and scanner**

Create `backend/tests/test_paths_guard.py`:

```python
"""Guard: filesystem access goes through the designated resolvers, so no
packaged resource or data path assumes a repo checkout or a desktop `~`
(docs/android-architecture.md, CLAUDE.md).

This is a *use-the-resolver* rule, not a ban on `Path.home()` — `store.paths`
builds `home()` out of it by design, and `store.proclock` uses it deliberately
for machine-local state. What the guard catches is a new caller reaching the
disk *around* the resolvers.

Reach, stated honestly, in the spirit of test_atomic_guard.py. This flags a
fixed list of idioms. It cannot see: a root assigned to an intermediate
variable and used later, `os.path.dirname(__file__)` chains, a path built from a
string literal, or anything reached through a library that takes an output path.
A guard that claimed otherwise would be worse than one that says where it stops.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import guard_markers

MARKER = "paths-ok:"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"
HOME_KEYS = {"HOME", "USERPROFILE"}


def scan(src: str) -> list[tuple[ast.AST, str]]:
    tree = ast.parse(src)
    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        # Path.home() / pathlib.Path.home(); and any .expanduser()
        if isinstance(node, ast.Attribute):
            if node.attr == "home":
                found.append((node, "Path.home()"))
            elif node.attr == "expanduser":
                found.append((node, "expanduser()"))
            elif node.attr == "parent" and isinstance(node.value, ast.Attribute) \
                    and node.value.attr == "parent":
                found.append((node, ".parent.parent"))
        # `from os.path import expanduser` used bare
        elif isinstance(node, ast.Name) and node.id == "expanduser":
            found.append((node, "expanduser()"))
        # Path(__file__).resolve().parents[N]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "parents":
            found.append((node, "parents[N]"))
        # os.environ["HOME"]
        elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ" \
                and isinstance(node.slice, ast.Constant) and node.slice.value in HOME_KEYS:
            found.append((node, "environ[HOME]"))
        # os.environ.get("HOME") / os.getenv("HOME")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in {"get", "getenv"} and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value in HOME_KEYS:
            found.append((node, "getenv(HOME)"))
    return found


PROHIBITED = [
    ("Path.home", "from pathlib import Path\np = Path.home()\n"),
    ("qualified home", "import pathlib\np = pathlib.Path.home()\n"),
    ("expanduser method", "from pathlib import Path\np = Path('~/x').expanduser()\n"),
    ("expanduser bare", "from os.path import expanduser\np = expanduser('~')\n"),
    ("environ subscript", "import os\np = os.environ['HOME']\n"),
    ("environ get", "import os\np = os.environ.get('HOME')\n"),
    ("getenv", "import os\np = os.getenv('HOME')\n"),
    ("parents index", "from pathlib import Path\np = Path(__file__).resolve().parents[2]\n"),
    ("parent chain", "from pathlib import Path\np = Path(__file__).parent.parent\n"),
]


@pytest.mark.parametrize("label,src", PROHIBITED, ids=[p[0] for p in PROHIBITED])
def test_prohibited_idioms_are_flagged(label, src):
    assert scan(src), f"{label} slipped past the guard"


ALLOWED = [
    ("resolver call", "from grimoire.store import paths\np = paths.home() / 'x'\n"),
    ("single parent", "from pathlib import Path\np = Path(__file__).parent\n"),
    ("unrelated env", "import os\np = os.environ.get('GRIMOIRE_HOME')\n"),
]


@pytest.mark.parametrize("label,src", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_allowed_idioms_are_not_flagged(label, src):
    assert not scan(src), f"{label} was flagged and should not be"
```

- [ ] **Step 2: Run and iterate until the 12 cases pass**

Run: `PYTHONPATH="$(pwd -W)/backend/src" ../../backend/.venv/Scripts/python.exe -m pytest backend/tests/test_paths_guard.py -q`
Expected: all 12 pass. `("single parent", ...)` is the case that proves `.parent` alone is not flagged — only a `.parent.parent` chain is.

- [ ] **Step 3: Add the marker tests**

Append the same three marker tests as Task 3 Step 3, with `MARKER = "paths-ok:"` and this source:

```python
def _reason(src: str, node: ast.AST, others=()) -> str | None:
    return guard_markers.marker_reason(MARKER, src, node, others)


def test_a_valid_marker_exempts_the_call():
    src = "from pathlib import Path\np = Path.home()  # paths-ok: the resolver itself\n"
    node, _ = scan(src)[0]
    assert _reason(src, node) == "the resolver itself"


def test_a_reasonless_marker_does_not_exempt():
    src = "from pathlib import Path\np = Path.home()  # paths-ok:\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node), "a bare marker must not silence the guard"


def test_a_marker_inside_a_string_does_not_exempt():
    src = "from pathlib import Path\nmsg = '# paths-ok: nope'\np = Path.home()\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node)
```

- [ ] **Step 4: Add the repository scan with non-vacuity**

Append the repo-scan test, mirroring Task 3 Step 4 but asserting the known site count:

```python
def test_the_package_reaches_the_disk_only_through_the_resolvers():
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 50, f"the scan found only {len(files)} files — glob broken?"

    violations, exempted = [], []
    for path in files:
        src = path.read_text(encoding="utf-8")
        found = scan(src)
        nodes = [n for n, _ in found]
        for node, idiom in found:
            others = [o for o in nodes if o is not node]
            if _reason(src, node, others):
                exempted.append((path.name, idiom))
            else:
                rel = path.relative_to(SRC.parents[1]).as_posix()
                violations.append(f"{rel}:{node.lineno} uses {idiom}")

    assert not violations, (
        "filesystem access outside the resolvers (docs/android-architecture.md):\n  "
        + "\n  ".join(violations))
    # Non-vacuous: the eight known sanctioned sites must still be found.
    assert len(exempted) >= 8, f"expected >=8 exempted sites, found {len(exempted)}"
```

- [ ] **Step 5: Run it and read the violations**

Expected failure listing exactly these eight, plus nothing else:
`store/paths.py:13`, `:23`, `:39`, `:80`, `store/proclock.py:87`, `main.py:19`, `prompts.py:14`, `store/epub.py:26`.

If a ninth appears, that is the guard working — investigate before marking it.

- [ ] **Step 6: Mark the eight sanctioned sites**

Per-site markers, never a module allowlist. Reasons, one per site:

```python
DEFAULT_HOME = Path.home() / ".grimoire"          # paths-ok: this IS the resolver's default
...
    return Path.home() / ".grimoire.json"          # paths-ok: the bootstrap pointer cannot live inside the dir it names
...
    return Path(raw).expanduser() if raw else None # paths-ok: expanding the user's own configured path is the feature
...
    resolved = Path(str(path).strip()).expanduser()  # paths-ok: same, for a path arriving from the API
```

`store/proclock.py:87` — `# paths-ok: machine-local lock state, deliberately outside the data root`
`main.py:19` — `# paths-ok: DEFAULT_DIST only; GRIMOIRE_DIST overrides it on Android`
`prompts.py:14` — `# paths-ok: DEFAULT_TEMPLATES_DIR only; GRIMOIRE_TEMPLATES overrides it on Android`
`store/epub.py:26` — `# paths-ok: package-relative, so the fonts ship inside the wheel`

- [ ] **Step 7: Verify green, then verify the guard can still fail**

Run the file: expected all pass.

Then prove it fails: temporarily add `x = Path.home()` to `backend/src/grimoire/store/scenes.py`, re-run, confirm the repo-scan test fails naming that line, and revert. (The snippet tests are the durable version of this; this step is a one-time sanity check that the *repo* scan is wired to real files.)

- [ ] **Step 8: Commit**

```bash
git add backend/tests/test_paths_guard.py backend/src/grimoire
git commit -m "Guard the resolver-only filesystem rule with an AST test"
```

---

### Task 5: Make targets and the frontend typecheck script

**Files:**
- Modify: `Makefile`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: Tasks 1-4 (each target must be green when added).
- Produces: `check`, `check-py`, `check-web`, `check-lint`, `check-templates`, `check-pydantic1`, `check-apk`, and the `PY` / `BUILD_PYTHON` variables the workflow overrides in Task 7.

- [ ] **Step 1: Add the typecheck script**

`frontend/package.json` scripts becomes:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "typecheck": "tsc -b",
    "preview": "vite preview",
    "test": "vitest run"
  },
```

- [ ] **Step 2: Add `PY` and `BUILD_PYTHON` to the platform split**

In the Windows branch of the existing `ifeq` block add:

```make
  PY ?= $(CURDIR)/backend/.venv/Scripts/python.exe
  VENV_PY = build/venv-pydantic1/Scripts/python.exe
```

and in the POSIX branch:

```make
  PY ?= python3
  VENV_PY = build/venv-pydantic1/bin/python
```

Then, outside the block: `BUILD_PYTHON ?=` — empty by default, so local builds keep using `local.properties`.

- [ ] **Step 3: Add the targets**

```make
check: check-lint check-py check-web check-templates check-pydantic1

check-py:
	"$(PY)" -m pytest backend -q

check-web:
	cd frontend && npm ci && npm run typecheck && npm test

check-lint:
	"$(PY)" -m ruff check .

check-templates:
	"$(PY)" scripts/verify_templates.py

check-apk: web-dist apk

web-dist:
	cd frontend && npm ci && npm run build
```

Every `$(PY)` use is double-quoted so a checkout under a path with spaces works
under `cmd.exe`. `check-apk` reuses the existing `apk` target, which already
supplies `cd android`, `--no-daemon`, and the copy to `$(APK_DEBUG)`.

- [ ] **Step 4: Pass `BUILD_PYTHON` through to gradle**

The existing `apk` recipe becomes:

```make
apk:
	$(GRADLEW) :app:assembleDebug $(if $(BUILD_PYTHON),-Pgrimoire.buildPython="$(BUILD_PYTHON)",)
```

- [ ] **Step 5: Extend `.PHONY`**

```make
.PHONY: apk apk-release apk-install android-bootstrap android-clean \
        check check-py check-web check-lint check-templates check-pydantic1 \
        check-apk web-dist
```

- [ ] **Step 6: Verify each target locally**

Run each and confirm green: `make check-lint`, `make check-templates`, `make check-web`, `make check-py`.
(`check-pydantic1` arrives in Task 6; `make check` will fail until then — that is expected and is fixed by Task 6.)

- [ ] **Step 7: Commit**

```bash
git add Makefile frontend/package.json
git commit -m "Add one make target per guard, plus a frontend typecheck script"
```

---

### Task 6: The `check-pydantic1` target

**Files:**
- Modify: `Makefile`
- Modify: `android/app/build.gradle.kts` (mirror the FastAPI bound)

**Interfaces:**
- Consumes: `VENV_PY` from Task 5.
- Produces: `check-pydantic1`, which Task 7's `pydantic1` job calls.

- [ ] **Step 1: Mirror the FastAPI bound into the Android pip block**

Constraining FastAPI only in CI would mean the job tests a known-good version
while the APK resolves a newer one, so the job would no longer reproduce the
dependency set it claims to. In `android/app/build.gradle.kts`:

```kotlin
            install("pydantic==1.10.*")
            // Upper bound shared with `make check-pydantic1`, which proves the
            // suite passes against exactly this set. Raise both together.
            install("fastapi>=0.110,<0.116")
```

- [ ] **Step 2: Add the target**

```make
check-pydantic1:
	rm -rf build/venv-pydantic1
	"$(PY)" -m venv build/venv-pydantic1
	"$(VENV_PY)" -m pip install -q --upgrade pip
	"$(VENV_PY)" -m pip install -q -e "./backend[dev]" "pydantic==1.10.*" "fastapi>=0.110,<0.116"
	"$(VENV_PY)" -m pip check
	"$(VENV_PY)" -c "import pydantic; assert pydantic.VERSION.startswith('1.10.'), pydantic.VERSION"
	"$(VENV_PY)" -m pytest backend -q
```

The venv is deleted first, so a stale package from a previous run cannot linger.
The install is one resolver invocation, so pip cannot satisfy the pin and then
upgrade past it. The assertion runs before pytest, so the job cannot silently
test pydantic 2. On Windows use `$(RM_RF)` — add
`RM_RF = rmdir /s /q` (Windows) / `RM_RF = rm -rf` (POSIX) to the platform split
rather than assuming `rm` exists under `cmd.exe`.

- [ ] **Step 3: Run it**

Run: `make check-pydantic1`
Expected: pydantic 1.10 installs, `pip check` is clean, the assertion passes, and the suite passes. Any failure here is a real Android-compatibility finding — fix the code, not the pin.

- [ ] **Step 4: Confirm the dev venv was untouched**

Run: `../../backend/.venv/Scripts/python.exe -c "import pydantic; print(pydantic.VERSION)"`
Expected: a 2.x version. If it prints 1.10, the target installed into the wrong environment — stop and fix.

- [ ] **Step 5: Commit**

```bash
git add Makefile android/app/build.gradle.kts
git commit -m "Prove the suite passes against the Android dependency set"
```

---

### Task 7: The workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: every make target from Tasks 5-6.
- Produces: the CI gate itself.

- [ ] **Step 1: Write the workflow**

Six job definitions. Each pins `runs-on: ubuntu-24.04`, pins every `uses:` to a
full commit SHA with the version in a trailing comment, and overrides `PY=python`
because no `backend/.venv` exists on a runner.

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend:
    runs-on: ubuntu-24.04
    strategy:
      fail-fast: false
      matrix:
        python: ["3.11", "3.14"]
    steps:
      - uses: actions/checkout@<sha>  # v4
      - uses: actions/setup-python@<sha>  # v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e "./backend[dev]"
      - run: pip check
      - run: make check-py PY=python
```

Write the remaining five jobs the same way: `frontend` (setup-node 24, cache
`npm` keyed on `frontend/package-lock.json`, `make check-web`); `lint`
(setup-python 3.12, `pip install ruff==0.16.0`, `make check-lint PY=python`);
`templates` (setup-python 3.12, `pip install -e "./backend[dev]"`,
`make check-templates PY=python`); `pydantic1` (setup-python 3.11,
`make check-pydantic1 PY=python`); `apk` (see Step 2).

`fail-fast: false` on the matrix so a 3.11 failure does not hide a 3.14 one.

- [ ] **Step 2: Write the `apk` job**

```yaml
  apk:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@<sha>  # v4
      - uses: actions/setup-java@<sha>  # v4
        with:
          distribution: temurin
          java-version: "17"
      - uses: actions/setup-node@<sha>  # v4
        with:
          node-version: "24"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - uses: actions/setup-python@<sha>  # v5
        with:
          python-version: "3.12"        # Chaquopy 15 buildPython ceiling
        id: buildpy
      - uses: android-actions/setup-android@<sha>  # v3
        with:
          packages: "platforms;android-34 build-tools;34.0.0"
      - run: make check-apk BUILD_PYTHON="${{ steps.buildpy.outputs.python-path }}"
      - uses: actions/upload-artifact@<sha>  # v4
        with:
          name: grimoire-debug-apk
          path: build/grimoire-debug.apk
          if-no-files-found: error
```

No NDK is installed: `ndkVersion` is unset, the only `ndk {}` block is
`abiFilters` (which selects prebuilt ABIs and compiles nothing), there is no
`externalNativeBuild`, Chaquopy consumes prebuilt wheels, and the local bootstrap
installs no NDK yet `make apk` succeeds. `if-no-files-found: error` is what stops
a build that produced no APK from passing green.

- [ ] **Step 3: Resolve the action SHAs**

For each action, get the SHA for the tag: `gh api repos/actions/checkout/git/ref/tags/v4 --jq .object.sha`. Substitute each `<sha>`. A mutable `@v4` tag would undercut the pinning claim in spec §3.

- [ ] **Step 4: Validate the YAML parses**

Run: `../../backend/.venv/Scripts/python.exe -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`. (If PyYAML is absent, `pip install pyyaml` into the dev venv.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Run every guard in CI"
```

---

### Task 8: Point `CLAUDE.md` at the targets, then open the PR

**Files:**
- Modify: `CLAUDE.md` ("Working notes" section)

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: the PR whose run is the acceptance evidence.

- [ ] **Step 1: Rewrite the Working-notes commands**

Replace the restated invocations with the targets, keeping the vitest warning
(which is a real trap worth preserving) and noting that CI runs the same targets:

```markdown
- Run the whole gate: `make check` (what CI runs, minus the APK job).
  Individually: `make check-py`, `check-web`, `check-lint`, `check-templates`,
  `check-pydantic1`; `make check-apk` needs `make android-bootstrap` first.
  (vitest must run **from** `frontend/` — `npx --prefix frontend vitest run`
  executes from the repo root, skips `frontend/vitest.config.ts`, disables
  `globals`, and fails every mock-based test. `make check-web` does this right.)
```

Also update the `templates/` note: `verify_templates.py` and the evals now run in
CI via `make check-lint` / `check-py` / `check-templates`, so the instruction
becomes "run `make check` after touching `templates/`".

- [ ] **Step 2: Run the full local gate one final time**

Run: `make check`
Expected: every target green.

- [ ] **Step 3: Commit and push**

```bash
git add CLAUDE.md
git commit -m "Point CLAUDE.md at the make targets"
git push -u origin cicd
```

- [ ] **Step 4: Open the PR — this is what triggers the first run**

`workflow_dispatch` cannot be used for a workflow that is not yet on `main`, and
the `push` trigger is scoped to `main`, so the PR is the only way this branch gets
a run.

```bash
gh pr create --base main --head cicd \
  --title "Add CI covering every existing guard, plus the Android conventions" \
  --body "..."
```

- [ ] **Step 5: Iterate to green**

Watch: `gh pr checks --watch`. The backend suite has never run on Linux, on 3.11,
or under pydantic 1.10 at 2705 tests, so failures here are expected work.
Fix causes, never symptoms: no `skip`, no `xfail`, no `continue-on-error`, and no
narrowing of what a job covers (spec §10.8). Report any job that cannot be made
green rather than weakening it.

---

## Corrections after the plan-stage Codex gate

The gate found 14 executable defects. These corrections **override** the task
text above wherever they conflict.

**C1 (critical, Task 4). The paths scanner inverted its own rule.** `if
node.attr == "home"` fires on *any* attribute named `home`, so
`paths.home()` — the resolver call the rule exists to encourage — was flagged,
and the plan's own allowed-case test would have failed. The scanner must resolve
`Path`/`pathlib` bindings and match only `Path.home` / `pathlib.Path.home`:

```python
def _path_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(local names bound to pathlib.Path, local names bound to the pathlib module)."""
    path_names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "pathlib":
                    modules.add(a.asname or "pathlib")
        elif isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for a in node.names:
                if a.name == "Path":
                    path_names.add(a.asname or "Path")
    return path_names, modules


def _is_path_home(node: ast.Attribute, path_names: set[str], modules: set[str]) -> bool:
    """`Path.home` where `Path` is really pathlib's, or `pathlib.Path.home`."""
    if node.attr != "home":
        return False
    base = node.value
    if isinstance(base, ast.Name):
        return base.id in path_names
    return (isinstance(base, ast.Attribute) and base.attr == "Path"
            and isinstance(base.value, ast.Name) and base.value.id in modules)
```

Add an allowed case `("unrelated home", "p = obj.home()\n")` alongside the
resolver-call case.

**C2 (critical, Task 5 Step 4). Do not replace the whole `apk` recipe** — only
its first line. The existing recipe also runs `$(MKDIR_BUILD)`, copies
`$(GRADLE_APK_DEBUG)` to `$(APK_DEBUG)`, and echoes the path. Dropping those
would leave `build/grimoire-debug.apk` absent and the artifact upload would fail
(correctly, thanks to `if-no-files-found: error`). Keep all four lines.

**C3 (high, Task 5). `PY`'s Windows default does not exist in a worktree.** The
venv lives in the main checkout, not in `.worktrees/cicd`. Keep
`$(CURDIR)/backend/.venv/Scripts/python.exe` as the default (correct in the main
checkout, the normal case) and document that worktree runs pass
`PY=../../backend/.venv/Scripts/python.exe`. Every command in this plan run from
the worktree must pass `PY` explicitly.

**C4 (high, Task 5). `check-py` must set `PYTHONPATH`.** Without it, a worktree
run imports the *main checkout's* `grimoire` through the editable install's
`.pth`, so the target would test the wrong source tree. `PYTHONPATH` entries sort
ahead of site-packages, so setting it to `$(CURDIR)/backend/src` is correct
everywhere: identical to the install path in the main checkout, shadowing in a
worktree, and a no-op on a runner. Needs the cmd.exe form on Windows
(`set "PYTHONPATH=..." && …`) and the prefix form on POSIX.

**C5 (high, Task 6). `rm -rf` is not a `cmd.exe` builtin**, and bare
`rmdir /s /q` fails when the directory is absent. Add an idempotent callable to
the platform block and use `$(call rm_rf,build/venv-pydantic1)`:

```make
  rm_rf = if exist "$(call fixpath,$(1))" rmdir /s /q "$(call fixpath,$(1))"   # Windows
  rm_rf = rm -rf "$(1)"                                                        # POSIX
```

**C6 (high, Task 2). Install ruff before using it.** Editing the `dev` extra does
not install anything; Step 3 would fail with "No module named ruff". Run
`python -m pip install "ruff==0.16.0"` (or reinstall the dev extra) first.

**C7 (medium, Tasks 5/6 ordering). Do not add `check-pydantic1` to the `check`
aggregate until Task 6 creates it**, or Task 5 commits a knowingly broken
aggregate target. Add `check` in Task 6, after its dependency exists.

**C8 (medium, Task 2 Step 5). Inspect the three side-effect-suspect imports
*before* running `--fix`**, not after — the fixer will already have removed them.

**C9 (medium, Task 5). `check-apk: web-dist apk` does not serialize under
parallel make.** Gradle could start before the frontend bundle exists. Use a
recipe action instead:

```make
check-apk: web-dist
	$(MAKE) apk
```

**C10 (medium). This plan's commands are Git Bash, not PowerShell.** Execute
them in Git Bash. PowerShell has no `$(pwd -W)`, and its `$?` is `True`/`False`
rather than an exit code.

**C11 (medium, Tasks 3/4 Step 2). The "expected: fails" claims are wrong.** Both
tasks supply `scan()` complete, so the snippet tests pass on first run (except
the C1 failure in Task 4, which is the one genuine red). An unused
`guard_markers` import does not cause a collection error. Expect: Task 3 all
pass; Task 4 one failure until C1 is applied.

**C12 (medium, Task 3). `_modules` is computed and never used**, so attribute
matching is receiver-agnostic: `unrelated.ConfigDict()` is flagged. That is
*accepted* for the method-shaped APIs (`obj.model_dump()` cannot be resolved
statically), but the docstring must say so plainly rather than claiming full
import-awareness, and an allowed test for an unrelated qualified attribute should
record the limit. Delete `_modules` or use it — do not leave dead code implying a
check that is not happening.

**C13 (medium, Task 6 Step 4). "Expect 2.x" is not a valid assertion** — FastAPI
coexists with pydantic 1.10, so a dev venv already on 1.10 would prove nothing.
Record `pip freeze` output for pydantic before the target and compare after.

**C14 (medium, Task 7 Step 4). `yaml.safe_load` only proves the file is YAML.**
It validates no Actions key, input, output, or expression. Add a pinned
`actionlint` run; the PR run remains the integration proof.

## Self-Review

**Spec coverage:** §3 command surface → Task 5; §3 workflow → Task 7; §4 guards →
Tasks 3-4; §5 ruff → Task 2; §6 templates → Task 1; §7 pydantic-1.10 → Task 6;
§9 APK → Task 7 Step 2; §10.7 CLAUDE.md → Task 8. Every success criterion in §10
maps to a step.

**Placeholders:** the `<sha>` tokens in Task 7 are resolved by Task 7 Step 3,
which gives the exact command. The `--body "..."` in Task 8 Step 4 is prose the
implementer writes from the branch's commits.

**Type consistency:** both guards expose `scan(src) -> list[tuple[ast.AST, str]]`
and a local `_reason(src, node, others=())`; deliberately separate copies, since
the flagged-idiom vocabularies differ and sharing would couple two unrelated
rules. `MARKER` includes the trailing colon in both, per the Global Constraints.
`VENV_PY` is defined in Task 5 Step 2 and used in Task 6 Step 2. `BUILD_PYTHON`
is defined in Task 5 Step 2, consumed in Task 5 Step 4, and supplied in Task 7
Step 2.
