# Store Import Layering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `backend/src/grimoire/store/` an acyclic module graph with every import at module scope, by splitting ten oversized modules into layered subpackages.

**Architecture:** Each tangled record kind becomes a subpackage with a leaf *core* (paths, reads, primitive writes) and a *lifecycle* layer above it that owns create/delete cascades. Every back-edge in today's graph is a lifecycle function reaching sideways, so cutting on that seam removes all cycles without adding indirection. `store/__init__.py` keeps its exact current exports, so no caller changes.

**Tech Stack:** Python 3.11+, pytest, FastAPI (untouched here). Refactor only — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-30-store-import-layering-design.md`

## Global Constraints

- **Behavior must not change.** Every task is a move plus an import rewrite plus monkeypatch retargeting. A diff that alters logic is a mistake.
- **Public API is frozen.** `store/__init__.py` keeps its current `from . import (...)` list, its `from .x import Y` lines, and its `__all__`. All 338 `from grimoire.store import x` call sites stay untouched.
- **Import form inside `store/`:** cross-package imports bind a *submodule* and keep it as a module object — `from ..campaigns import read` then `read.world_refs()`. Never `from ..campaigns import world_refs`. `from .. import campaigns` is legal.
- **All imports at module scope.** No import inside a function body, grimoire or third-party.
- **Android:** `pyproject.toml` base deps must stay Android-installable; no `model_dump()`, `Field`, validators, or `ConfigDict`. Filesystem access goes through `store.paths`. This refactor adds no dependencies, so it only has to avoid regressing these.
- **Privacy:** never use a real world/campaign/character name in a test fixture, commit message, or doc. Reuse existing placeholders (Seraphine, Mara, Winifred, Realm, Saltmarch).
- **Test command:** `backend/.venv/bin/python -m pytest backend -q` (on Windows, `backend/.venv/Scripts/python.exe`).
- **Known-failing baseline:** after Task 1, expect **2713 passed, 1 failed** on a container running as uid 0 — measured, not estimated. The one failure is `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`, which cannot hold as root because chmod 0444 does not stop uid 0 writing. On a normal user account the suite is fully green. Any *other* failure is a real regression.

  Do not repeat the earlier mistake of filing `test_assets_store.py::test_lookup_survives_a_sibling_vanishing_mid_scan` as a root artifact — it is a genuine test bug, fixed in Task 1.

---

### Task 1: Repair the four broken `stat` stubs

Two independent test bugs stand between the suite and a trustworthy baseline.

**`test_atomic.py`** — three stubs accept one positional argument, but Python 3.11.15's `pathlib.Path.stat()` calls `os.stat(path, follow_symlinks=...)`. When pytest's `tmp_path` cleanup runs during teardown while the patch is live, it raises `TypeError` and pytest aborts the whole session with an INTERNALERROR instead of reporting results.

**`test_assets_store.py`** — `test_lookup_survives_a_sibling_vanishing_mid_scan` replaces `assets.Path.stat` with a stub that raises `FileNotFoundError` for *every* path except `avatar.png`. But `image_path` opens with `if not d.exists()` on the `default` **directory**, whose name is not `avatar.png`, so the stub raises there and `image_path` returns `None` before it ever reaches the scan the test is exercising. This fails regardless of uid.

**Files:**
- Modify: `backend/tests/test_atomic.py:150`, `:379`, `:402`
- Modify: `backend/tests/test_assets_store.py:190-197`

**Interfaces:**
- Consumes: nothing.
- Produces: a test suite that runs to completion and prints a pass/fail summary.

- [ ] **Step 1: Reproduce the abort**

Run: `backend/.venv/bin/python -m pytest backend -q`

Expected: output ends in `INTERNALERROR> TypeError: ...<lambda>() got an unexpected keyword argument 'follow_symlinks'`, with **no** `N passed` summary line.

- [ ] **Step 2: Widen the three stubs**

In `backend/tests/test_atomic.py`, change each of the three `os.stat` stubs to accept keyword arguments. Line 150:

```python
    monkeypatch.setattr(atomic.os, "stat", lambda _p, **_kw: os.stat_result(
        (0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0)))
```

Line 379:

```python
    monkeypatch.setattr(atomic.os, "stat", lambda _p, **_kw: fake)
```

Line 402:

```python
    monkeypatch.setattr(atomic.os, "stat",
                        lambda _p, **_kw: os.stat_result((0o100644, 0, 0, 1, 4242, 8484, 0, 0, 0, 0)))
```

Leave the `listxattr` and `getxattr` stubs alone — nothing calls those with keywords.

- [ ] **Step 3: Narrow the assets stub to the path that actually vanished**

Confirm the failure first:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_assets_store.py::test_lookup_survives_a_sibling_vanishing_mid_scan -q
```

Expected: `FileNotFoundError: .../characters/sera/assets/default` — the *directory*, not an image.

In `backend/tests/test_assets_store.py`, invert the stub so only the unlinked sibling is missing:

```python
    def vanishing(self, *a, **kw):
        # Only the sibling that put_image just unlinked is gone. Raising for
        # every other path also broke `d.exists()` on the directory itself,
        # which made image_path bail before it ever reached the scan.
        if self.name == "avatar.jpg":
            raise FileNotFoundError(self)
        return real_stat(self, *a, **kw)
```

Re-run that test: expected PASS. The assertion it was always meant to make — `p.name == "avatar.png"`, newest-wins rather than `sorted()[0]` — is now actually exercised.

- [ ] **Step 4: Confirm the suite now completes**

Run: `backend/.venv/bin/python -m pytest backend -q`

Expected: a real summary line — `2713 passed, 1 failed` as root (the remaining failure being the chmod-based `test_a_read_only_record_is_not_silently_replaced`), fully green as a normal user. No INTERNALERROR.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/test_atomic.py backend/tests/test_assets_store.py
git commit -m "Repair four stat stubs that never exercised their assertions

The os.stat stubs took one positional arg while pathlib passes
follow_symlinks on 3.11, so tmp_path cleanup aborted the whole pytest
session. The assets stub raised for every path but avatar.png, including
the directory image_path checks first, so the scan under test never ran."
```

---

### Task 2: Land the import guard with a ratchet baseline

The guard is what makes every later task verifiable. It reports 58 violations today; each later task deletes its own baseline lines, and the guard fails if a line is stale, so no task can silently skip its work.

**Files:**
- Create: `backend/tests/test_import_guard.py`
- Create: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Consumes: `backend/tests/guard_markers.py` — `marker_reason(marker: str, src: str, node: ast.AST, others=()) -> str | None`.
- Produces: `_violations() -> list[str]`, returning sorted strings in three shapes:
  - `deferred <module>::<func>::<target>`
  - `form <module>::<package>.<name>`
  - `cycle <mod1>,<mod2>,...` (SCC members, sorted, comma-joined)

  Later tasks reference these exact strings when deleting baseline lines.

- [ ] **Step 1: Write the guard**

Create `backend/tests/test_import_guard.py`:

```python
"""Every import sits at module scope, and the module graph is acyclic.

Three rules, one scan:

1. No import inside a function body. Deferred imports were how this package
   held a 15-module cycle together; each one moves an ImportError from load
   time to call time and hides the coupling from every static reader.
2. No cycle in the module-level import graph.
3. Inside ``store/``, never bind a non-module name off a package's
   ``__init__``. ``from ..campaigns import read`` binds a submodule and is
   fine; ``from ..campaigns import world_refs`` reads a name that exists only
   once ``campaigns/__init__.py`` has run. Rule 2 cannot catch this -- the
   name binding raises at import time while the *file* graph stays perfectly
   acyclic, so the load-bearing order just moves from ``store/__init__.py``
   into each package's ``__init__.py``.

Ratchet, not a cliff: `import_guard_baseline.txt` lists the violations that
existed when this guard landed. A violation missing from the baseline fails,
and a baseline entry that no longer occurs *also* fails -- so the file cannot
rot into a permanent exemption list, and every removal is recorded.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent
BASELINE = pathlib.Path(__file__).parent / "import_guard_baseline.txt"

MARKER = "import-ok:"


def _modname(path: pathlib.Path) -> str:
    rel = path.relative_to(PACKAGE.parent).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _sources() -> dict[str, tuple[pathlib.Path, str, ast.Module]]:
    out = {}
    for p in sorted(PACKAGE.rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        out[_modname(p)] = (p, src, ast.parse(src))
    return out


SOURCES = _sources()
MODULES = set(SOURCES)
PACKAGES = {m for m, (p, _, _) in SOURCES.items() if p.name == "__init__.py"}


def _resolve(node, mod: str, is_pkg: bool) -> set[str]:
    """Absolute grimoire module names this import depends on."""
    pkg = mod if is_pkg else mod.rsplit(".", 1)[0]
    out: set[str] = set()
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.startswith("grimoire"):
                out.add(a.name)
        return out
    if node.level:
        base = pkg.split(".")
        if node.level > 1:
            base = base[: len(base) - (node.level - 1)]
        target = ".".join(base) + ("." + node.module if node.module else "")
    else:
        target = node.module or ""
    if not target.startswith("grimoire"):
        return out
    for a in node.names:
        cand = f"{target}.{a.name}"
        out.add(cand if cand in MODULES else target)
    return out


class _Walker(ast.NodeVisitor):
    """Collects module-scope edges and in-function imports separately."""

    def __init__(self, mod: str, is_pkg: bool):
        self.mod, self.is_pkg = mod, is_pkg
        self.stack: list[str] = []
        self.edges: set[str] = set()
        self.deferred: list[tuple[str, str, ast.stmt]] = []
        self.form: list[tuple[str, ast.stmt]] = []

    def visit_FunctionDef(self, n):
        self.stack.append(n.name)
        self.generic_visit(n)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Import(self, n):
        self._record(n, [a.name for a in n.names])

    def visit_ImportFrom(self, n):
        self._record(n, [n.module or ""])
        self._check_form(n)

    def _record(self, n, raw_names):
        targets = _resolve(n, self.mod, self.is_pkg)
        if self.stack:
            where = ".".join(self.stack)
            for name in sorted(targets) or sorted(raw_names):
                self.deferred.append((where, name, n))
            return
        self.edges |= targets

    def _check_form(self, n: ast.ImportFrom):
        """Rule 3. ``__init__.py`` files are exempt: re-exporting is their job,
        and they import their own submodules before anything reads through
        them."""
        if not self.mod.startswith("grimoire.store") or self.is_pkg:
            return
        pkg = self.mod.rsplit(".", 1)[0]
        if n.level:
            base = pkg.split(".")
            if n.level > 1:
                base = base[: len(base) - (n.level - 1)]
            target = ".".join(base) + ("." + n.module if n.module else "")
        else:
            # `from grimoire.store.campaigns import world_refs` does the same
            # package-attribute lookup as the relative form and fails the same
            # way; spelling it absolutely must not buy an exemption.
            target = n.module or ""
            if not target.startswith("grimoire.store"):
                return
        if target not in PACKAGES or target == pkg:
            return
        for a in n.names:
            if f"{target}.{a.name}" not in MODULES:
                self.form.append((f"{target}.{a.name}", n))


def _collect():
    deferred, form, graph = [], [], {}
    for mod, (path, src, tree) in SOURCES.items():
        w = _Walker(mod, path.name == "__init__.py")
        w.visit(tree)
        graph[mod] = w.edges
        flagged = [n for _, _, n in w.deferred] + [n for _, n in w.form]

        def others(node):
            """Every *other* flagged node. The identity exclusion is load-bearing:
            passing the node itself makes `_shares_first_line(node, node)` true,
            so `_sole_owner` rejects the node's own inline marker and no
            `# import-ok:` exemption can ever be honoured."""
            return [o for o in flagged if o is not node]

        for where, name, node in w.deferred:
            if guard_markers.marker_reason(MARKER, src, node, others(node)):
                continue
            deferred.append(f"deferred {mod}::{where}::{name}")
        for target, node in w.form:
            if guard_markers.marker_reason(MARKER, src, node, others(node)):
                continue
            form.append(f"form {mod}::{target}")
    return deferred, form, graph


def _cycles(graph: dict[str, set[str]]) -> list[str]:
    """Tarjan. Reports every strongly connected component above size one."""
    index, low, onstk, stack, out = {}, {}, {}, [], []
    counter = [0]

    def strong(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        onstk[v] = True
        for w in sorted(graph.get(v, ())):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif onstk.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                onstk[w] = False
                comp.append(w)
                if w == v:
                    break
            # A self-import (`read.py` doing `from . import read`) forms a
            # one-node component and exposes the same partially-initialized
            # module a larger cycle does, so size alone is not the test.
            if len(comp) > 1 or comp[0] in graph.get(comp[0], ()):
                out.append("cycle " + ",".join(sorted(comp)))

    for n in sorted(set(graph) | {t for v in graph.values() for t in v}):
        if n not in index:
            strong(n)
    return out


def _violations() -> list[str]:
    deferred, form, graph = _collect()
    return sorted(deferred + form + _cycles(graph))


def _baseline() -> list[str]:
    if not BASELINE.exists():
        return []
    return sorted(line.strip() for line in BASELINE.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.startswith("#"))


def test_no_import_violations_outside_the_baseline():
    """A new deferred import, bad import form, or cycle fails here."""
    new = [v for v in _violations() if v not in _baseline()]
    assert not new, (
        "new import-graph violations:\n  " + "\n  ".join(new)
        + f"\n\nFix them, or -- only with a stated reason -- add a "
          f"`# {MARKER} <reason>` comment on the import.")


def test_the_baseline_has_no_stale_entries():
    """Every baseline line still describes a real violation.

    Without this the file becomes a permanent exemption list: entries for code
    that was already fixed would sit there licensing a future reintroduction
    under the same name.
    """
    live = _violations()
    stale = [b for b in _baseline() if b not in live]
    assert not stale, (
        "baseline entries no longer occur -- delete these lines:\n  "
        + "\n  ".join(stale))
```

- [ ] **Step 2: Run it and watch it fail with no baseline**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_import_guard.py -q`

Expected: `test_no_import_violations_outside_the_baseline` FAILS listing 58 violations (57 `deferred`, 1 `cycle`). `test_the_baseline_has_no_stale_entries` passes vacuously.

- [ ] **Step 3: Generate the baseline**

```bash
cd backend && .venv/bin/python -c "
import sys; sys.path.insert(0,'src'); sys.path.insert(0,'.')
from tests import test_import_guard as g
g.BASELINE.write_text('\n'.join(g._violations()) + '\n')
print(len(g._violations()), 'violations recorded')
"
```

Expected: `58 violations recorded`.

- [ ] **Step 4: Verify both tests pass**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_import_guard.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Prove the guard is not vacuous**

Temporarily add a deliberately bad import to `backend/src/grimoire/store/weather/settings.py`, directly under `from __future__ import annotations`:

```python
from ..calendars import CalendarError  # deliberately bad form
```

Run: `backend/.venv/bin/python -m pytest backend/tests/test_import_guard.py -q`

Expected: FAIL, reporting `form grimoire.store.weather.settings::grimoire.store.calendars.CalendarError`.

Now revert that line: `git checkout backend/src/grimoire/store/weather/settings.py`, and re-run to confirm `2 passed`.

- [ ] **Step 6: Prove the marker exemption actually works**

A guard whose documented escape hatch silently never fires is worse than one with no escape hatch, because the first `# import-ok:` someone writes will look honoured and will not be. Test it directly.

In `backend/src/grimoire/store/suggest.py:157`, temporarily replace the existing comment on the deferred import:

```python
    from . import playing  # import-ok: testing the marker
```

(Note the real line is indented 4 spaces and already carries a `# lazy: ...` comment — replace the whole comment, do not append.)

```bash
cd backend && .venv/bin/python -c "
import sys; sys.path.insert(0,'src'); sys.path.insert(0,'.')
from tests import test_import_guard as g
hit = [v for v in g._violations() if 'store.suggest' in v]
assert not hit, f'marker did not suppress: {hit}'
print('marker exemption works')
"
```

Expected: `marker exemption works`. Then `git checkout backend/src/grimoire/store/suggest.py`.

- [ ] **Step 7: Prove a self-import is reported as a cycle**

```bash
cd backend && .venv/bin/python -c "
import sys; sys.path.insert(0,'src'); sys.path.insert(0,'.')
from tests import test_import_guard as g
assert g._cycles({'a': {'a'}, 'b': set()}) == ['cycle a'], 'self-edge not reported'
assert g._cycles({'a': {'b'}, 'b': set()}) == [], 'false positive on a plain edge'
print('self-import detection works')
"
```

- [ ] **Step 8: Capture the public-API baseline**

Tasks 3-15 each assert the store's public surface has not drifted. That assertion needs a snapshot taken *before* any split, and it must cover `dir()` as well as `__all__` — `module_display` is reachable today only as a side-effect binding that never appears in `__all__`, so an `__all__`-only snapshot would not notice it disappearing.

```bash
cd backend && .venv/bin/python -c "
import json, pathlib, grimoire.store as s
b = {'all': sorted(s.__all__),
     'dir': sorted(n for n in dir(s) if not n.startswith('_'))}
pathlib.Path('tests/store_api_baseline.json').write_text(json.dumps(b, indent=2) + '\n')
print(len(b['all']), 'exported,', len(b['dir']), 'public attributes captured')
"
```

Expected: `81 exported, 101 public attributes captured`. Confirm `module_display` is present in the captured `dir` list — if it is not, the snapshot was taken against an already-modified tree.

- [ ] **Step 9: Commit**

```bash
git add backend/tests/test_import_guard.py backend/tests/import_guard_baseline.txt \
       backend/tests/store_api_baseline.json
git commit -m "Guard the import graph with a ratchet baseline

Fails on any in-function import, any module-level cycle, and any name bound
off a package __init__ inside the store. The baseline records the 58 existing
violations; a stale line fails too, so it cannot become a standing exemption
list."
```

---

## Tasks 3-9: the per-kind splits

These seven tasks share one mechanical recipe. **Read this section before starting any of them**; each task below states only what is specific to it.

### The recipe

For a module `foo.py` being split into package `foo/` with parts `a.py`, `b.py`, …:

1. `git mv backend/src/grimoire/store/foo.py backend/src/grimoire/store/foo_tmp.py` — keeps git rename detection useful.
2. `mkdir backend/src/grimoire/store/foo`.
3. Create each part file. Move the listed functions **verbatim** — no reformatting, no renaming, no logic edits. Carry each function's docstring and its inline comments with it.
4. Each part imports what it needs at the top. Within the package use `from . import b` / `from .b import helper`; across packages use `from ..other import submodule` and call `submodule.thing()`.
5. Write `foo/__init__.py` re-exporting every name the old module exposed, so `store.foo.anything` still resolves. Import submodules first, then names:

```python
"""<carry over the original module docstring>"""

from __future__ import annotations

from . import a, b
from .a import PublicThing, public_fn
from .b import other_fn
```

6. Delete `foo_tmp.py`.
7. Hoist any deferred import that is now legal, and delete its `from . import ...` line inside the function body.
8. Retarget any monkeypatch listed for this task.
9. Delete this task's lines from `import_guard_baseline.txt`.

### Verifying a split task

Every one of Tasks 3-9 ends with these four commands. They are not repeated per task.

```bash
# 1. nothing outside the store changed its view of the world
backend/.venv/bin/python -c "
import json, pathlib, grimoire.store as s
expected = json.loads(pathlib.Path('backend/tests/store_api_baseline.json').read_text())
actual = {'all': sorted(s.__all__), 'dir': sorted(n for n in dir(s) if not n.startswith('_'))}
assert actual == expected, (
    'public API drifted:\n  missing: %s\n  added:   %s' % (
        sorted(set(expected['dir']) - set(actual['dir'])),
        sorted(set(actual['dir']) - set(expected['dir']))))
print('public API unchanged')
"
# 2. the guard agrees this task removed exactly its own violations
backend/.venv/bin/python -m pytest backend/tests/test_import_guard.py -q
# 3. the kind's own tests
backend/.venv/bin/python -m pytest backend/tests/<the kind's test files> -q
# 4. everything
backend/.venv/bin/python -m pytest backend -q
```

Command 2 catches both halves: a violation you introduced (fails the first test) and a baseline line you deleted without actually fixing, or fixed without deleting (fails the second).

### Placement follows the call graph, not the name

A first pass at these tables derived each file from what its functions are *about*, and put four sets of mutually-dependent helpers in separate files — which would have made the guard reject the very task that created them. The spec's tables now reflect the corrected placement; these four are called out because they are the ones where the intuitive home is the wrong one:

| Package | Helper | Lives with | Why |
|---|---|---|---|
| `campaigns` | `world_root_of` | `read.py` | calls `read_campaign`, which calls `campaign_meta_path` — otherwise `paths ↔ read` |
| `modules` | `_load_rules`, `_load_content` | `pack.py` | `load_pack_at` calls them while `read_content` calls `pack_root` — otherwise `pack ↔ content` |
| `appearances` | `_lock` | `versions.py` | calls `actor_hash`, `_copy_actor`, `_purge_other_versions`, `_set_default`, `_drop_manifest_ref` |
| `scenes` | `_block` **and** `_append_block` | `serialize.py` | `_serialize_messages` calls `_append_block` while `append_message` calls `_block` — otherwise `serialize ↔ write` |

**The rule, and the check to run before writing any package's files:** a helper belongs in the file whose other functions call it, not in the file its name suggests. Sketch the intra-package call graph first; if two proposed files call into each other, merge them or pull the shared helper down into a third.

### Retarget consumers in the same task that creates the package

Splitting `campaigns` is not enough on its own. Ten modules currently do `from . import campaigns` (`appearances.py:16`, `sheets.py:18`, and eight more). The moment `campaigns/__init__.py` imports `lifecycle`, and `lifecycle` imports `appearances`/`modules`/`sheets`, those flat modules' imports of the `campaigns` *package* close a cycle — `campaigns → lifecycle → appearances → campaigns`.

So each split task must, in the same commit, retarget every existing consumer of the module it splits. Find them with:

```bash
grep -rn "from \. import .*\b<kind>\b\|from \.\. import .*\b<kind>\b" backend/src/grimoire/store/
```

and rewrite each to name the submodule it actually needs (`from .campaigns import paths as campaign_paths`, etc.), driven by what attributes it uses. The verification step's guard run is what confirms you found them all: a missed consumer shows up as a reported cycle.

### Retargeting a monkeypatch depends on the caller, not the function

A patch must land on **the binding the caller actually reads**. Two cases, and getting them backwards silently disables fault injection — the test still passes, but no longer proves anything.

- **Caller uses the facade** — e.g. `routes/scenes.py:299` calls `store.audit.materialize(...)`, an attribute lookup on the package at call time. Patching `store.audit.materialize` works, before *and* after the split, because the package attribute is what gets read. Patching `audit.apply.materialize` would **not** be seen. **Leave these alone.**
- **Caller imports the submodule** — once a store module is rewritten to `from .modules import binding`, it holds its own reference and only `modules.binding.resolve` is patchable.

It follows that a retarget belongs in **the task that rewrites the caller's import**, not the task that moves the function. Corrected assignment:

| Test site | Patched | Action |
|---|---|---|
| `test_routes.py:2853` | `audit.materialize` | **keep** — `routes/scenes.py:299` uses the facade |
| `test_locks_store.py:865` | `scenes.append_message` | **keep** — the test drives an HTTP route, and `routes/mechanics.py:40`,`:202` use the facade |
| `test_locks_store.py:911`,`:912` | `chronicle.absorb`, `chronicle.append_timeline` | **keep** — chronicle's own functions (`chronicle.py:39`,`:69`), unrelated to the `absorb` module, and chronicle is never split |
| `test_module_display.py:361` | `module_display._load_theme` | **keep** — the Task 5 alias binds the same module object |
| `test_sheets_store.py:799`,`:1134` | `modules.resolve` | retarget in **Task 7**, when `sheets`' imports are rewritten — not Task 5 |
| `test_audit_store.py:100` | `modules.load_pack` | retarget in **Task 8**, when `audit`'s imports are rewritten |
| `test_response_presets.py:601` | `campaigns.read_campaign` | retarget in **Task 13**, when `response_presets`' import is hoisted — not Task 3 |
| `test_context.py:1157` | `context._drift_roster` | retarget in **Task 12** (caller is inside the same package) |
| `test_module_edit.py:627`,`:631`,`:914` | `_run_migration`, `_campaign_locks` | retarget in **Task 10** (caller is inside the same package) |
| `test_scene_store.py:996` | `scenes.parse_frontmatter` | retarget in **Task 9** — see that task, it needs every submodule patched |

**Whenever you retarget one, prove it still injects.** Temporarily break the patched function's replacement (make it a no-op instead of raising, or drop the patch) and confirm the test fails. A fault-injection test that no longer injects is indistinguishable from a passing one.

### Task ordering is a dependency order

`campaigns` → `worlds` → `modules` → `appearances` → `sheets` → `audit` → `scenes`. Do not reorder. While one side of a mutually-deferring pair is still flat, its deferred import stays in place and stays on the baseline; the later task removes both sides.

---

### Task 3: Split `campaigns`

Highest-value task: ten modules import `campaigns` for nothing but path helpers.

**Files:**
- Create: `backend/src/grimoire/store/campaigns/__init__.py`, `paths.py`, `read.py`, `lifecycle.py`
- Delete: `backend/src/grimoire/store/campaigns.py`
- Modify: `backend/tests/test_response_presets.py:601`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `campaigns/paths.py`: `CampaignNotFound`, `_campaigns_dir()`, `campaign_root(cid) -> Path`, `campaign_meta_path(cid) -> Path`, `world_root_of(cid) -> Path`, `campaign_exists(cid) -> bool`, `_manifest_path(cid)`, `read_manifest(cid) -> dict`, `write_manifest(cid, data) -> None`
  - `campaigns/read.py`: `read_campaign(cid) -> dict`, `list_campaigns() -> list`, `world_refs() -> list[tuple[str, str | None]]`, `touch(cid) -> None`
  - `campaigns/lifecycle.py`: `create_campaign(name, world_id, region=None, calendar=None, module=None, climate=None) -> str`, `delete_campaign`, `rename_campaign`, `ensure_campaign_slim`, `_tombstone_deleted_copied_assets`, `_prune_duplicate_files`, `set_campaign_response`
- Consumes: Task 2's guard.

- [ ] **Step 1: Apply the recipe**

Follow the recipe above. Function placement is in the spec's `campaigns/` table. `lifecycle.py` is the only part that imports `worlds`, `campaign_climate`, `climates`, `modules`, `sheets`, `overlay`, `appearances`, `scenes`.

`worlds` is still a flat module at this point, so `lifecycle.py` writes `from .. import worlds` and calls `worlds.world_exists(...)`. That is the legal module-object form.

- [ ] **Step 2: Hoist the six deferred imports in `lifecycle.py`**

These move to the top of `campaigns/lifecycle.py` and their in-function `from . import ...` lines are deleted:

`campaign_climate`, `climates`, `modules` (was `create_campaign`), `sheets` (was `create_campaign`), `appearances` + `overlay` (was `ensure_campaign_slim`), `overlay` (was `_tombstone_deleted_copied_assets`), `scenes` (was `set_campaign_response`).

Keep the comment on each explaining *why* the seed step exists; only the import placement changes.

- [ ] **Step 3: Leave `test_response_presets.py:601` alone**

Do **not** retarget it here. `response_presets.usage` still does `from . import campaigns` inside the function at this point, so `campaigns.read_campaign` remains a call-time lookup on the package and the existing patch keeps working. Task 13 hoists that import and retargets the patch together.

- [ ] **Step 4: Delete this task's baseline lines**

Remove these 8 lines from `backend/tests/import_guard_baseline.txt`:

```
deferred grimoire.store.campaigns::create_campaign::grimoire.store.campaign_climate
deferred grimoire.store.campaigns::create_campaign::grimoire.store.climates
deferred grimoire.store.campaigns::create_campaign::grimoire.store.modules
deferred grimoire.store.campaigns::create_campaign::grimoire.store.sheets
deferred grimoire.store.campaigns::ensure_campaign_slim::grimoire.store.appearances
deferred grimoire.store.campaigns::ensure_campaign_slim::grimoire.store.overlay
deferred grimoire.store.campaigns::_tombstone_deleted_copied_assets::grimoire.store.overlay
deferred grimoire.store.campaigns::set_campaign_response::grimoire.store.scenes
```

- [ ] **Step 5: Verify**

Run the four verification commands. Kind-specific tests: `backend/tests/test_campaigns_store.py backend/tests/test_response_presets.py backend/tests/test_overlay.py`.

- [ ] **Step 6: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split campaigns into paths, read and lifecycle

Ten modules import campaigns for path helpers alone; only create/delete/seed
reach sideways. Separating them lets every one of those ten import a leaf."
```

---

### Task 4: Split `worlds`

**Files:**
- Create: `backend/src/grimoire/store/worlds/__init__.py`, `paths.py`, `read.py`, `lifecycle.py`
- Delete: `backend/src/grimoire/store/worlds.py`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `worlds/paths.py`: `WorldNotFound`, `_worlds_dir()`, `world_root(wid) -> Path`, `world_meta_path(wid) -> Path`, `world_exists(wid) -> bool`, `names_its_directory(root) -> bool`, `canonical_id(wid) -> str`, `references_world(w, root) -> bool`
  - `worlds/read.py`: `read_world(wid) -> dict`, `world_name(wid) -> str`, `list_worlds() -> list`
  - `worlds/lifecycle.py`: `WorldInUse`, `create_world`, `rename_world`, `delete_world(wid) -> None`
- Consumes: `campaigns/read.py::world_refs` from Task 3.

- [ ] **Step 1: Apply the recipe**

`worlds/lifecycle.py` imports campaigns in the required form:

```python
from ..campaigns import read as campaigns_read
```

and `delete_world` calls `campaigns_read.world_refs()`. Keep the existing comment explaining why it is `world_refs` and not `list_campaigns`.

- [ ] **Step 2: Hoist the deferred import and update Task 3's placeholder**

Delete `from . import campaigns  # function-level: campaigns imports worlds at module level` from inside `delete_world` — the comment is now false and the import is at the top.

In `campaigns/lifecycle.py`, change `from .. import worlds` to the submodule form now that `worlds` is a package:

```python
from ..worlds import paths as worlds_paths, read as worlds_read
```

and update its call sites (`worlds.world_exists` → `worlds_paths.world_exists`, and so on).

- [ ] **Step 3: Delete this task's baseline line**

```
deferred grimoire.store.worlds::delete_world::grimoire.store.campaigns
```

- [ ] **Step 4: Verify**

Four verification commands. Kind-specific: `backend/tests/test_worlds_store.py backend/tests/test_campaigns_store.py`.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split worlds into paths, read and lifecycle

delete_world's cascade into campaigns was the only reason worlds could not be
a leaf; it now lives above the core with a top-level import."
```

---

### Task 5: Split `modules` and fold in `module_display`

**Files:**
- Create: `backend/src/grimoire/store/modules/__init__.py`, `fields.py`, `pack.py`, `validate.py`, `content.py`, `display.py`, `binding.py`, `admin.py`
- Delete: `backend/src/grimoire/store/modules.py`, `backend/src/grimoire/store/module_display.py`
- Modify: `backend/src/grimoire/store/__init__.py` (add the `module_display` alias)
- Modify: `backend/tests/test_sheets_store.py:799`, `:1134`; `backend/tests/test_audit_store.py:100`; `backend/tests/test_module_display.py:3`, `:361`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `modules/fields.py`: `assembled_fields(sheets, tid) -> list[dict]`, `numeric_names(...)`, `_pool_group_fields(...)`
  - `modules/pack.py`: `ModuleError`, `ModuleNotFound`, `ContentNotFound`, `builtin_dir()`, `user_dir()`, `pack_root(mid) -> Path`, `load_pack(mid) -> dict`, `load_pack_at(root) -> dict`, `list_modules() -> list`
  - `modules/binding.py`: `set_world_module(wid, mid)`, `set_campaign_module(cid, value)`, `resolve(cid) -> str | None`
  - `modules/display.py`: `load_display(root, sheets) -> tuple`, `_load_theme`, `_load_layout`, `_type_scope`, `_union_scope`
- Consumes: `campaigns/paths.py`, `campaigns/read.py`, `worlds/paths.py`, `worlds/read.py`.

`modules/fields.py` is the cut that unlocks this: both deferred imports in `module_display.py` (`:65`, `:98`) want only `assembled_fields`, so moving it to a leaf lets `display.py` import it at the top while `pack.py` also imports it.

- [ ] **Step 1: Apply the recipe**

`display.py` starts with `from .fields import assembled_fields` and the two in-function imports go away. `pack.py` imports `from . import display` and calls `display.load_display(...)` at line ~681 of the old file — keep it a module-object call so `test_module_display.py:361` can still patch `_load_theme`.

- [ ] **Step 2: Add the `module_display` compatibility alias**

`store/__init__.py` never exported `module_display`; it was only ever bound as a side effect of `modules.py:18`. `backend/tests/test_module_display.py:3` imports it. Add an explicit alias so that keeps working:

```python
from .modules import display as module_display
```

Add `"module_display"` to `__all__`. This binds the *same module object*, so `monkeypatch.setattr(module_display, "_load_theme", boom)` at `test_module_display.py:361` still intercepts the call made from `pack.py`.

- [ ] **Step 3: Leave the sheets and audit patches alone**

`test_sheets_store.py:799`/`:1134` (`modules.resolve`) and `test_audit_store.py:100` (`modules.load_pack`) must **not** be retargeted here. `sheets` and `audit` still do `from . import modules`, so both names remain call-time lookups on the package and the existing patches keep working. They move in Tasks 7 and 8, alongside those modules' import rewrites.

`test_module_display.py:361` also needs no change — the Step 2 alias binds the same module object that `pack.py` calls through.

- [ ] **Step 4: Delete this task's baseline lines**

```
deferred grimoire.store.module_display::_type_scope::grimoire.store.modules
deferred grimoire.store.module_display::_union_scope::grimoire.store.modules
deferred grimoire.store.modules::set_campaign_module::grimoire.store.campaigns
deferred grimoire.store.modules::resolve::grimoire.store.campaigns
deferred grimoire.store.modules::set_world_module::grimoire.store.worlds
deferred grimoire.store.modules::resolve::grimoire.store.worlds
```

- [ ] **Step 5: Verify**

Four verification commands. Kind-specific: `backend/tests/test_modules_store.py backend/tests/test_module_display.py backend/tests/test_module_references.py backend/tests/test_sheets_store.py backend/tests/test_audit_store.py`.

Additionally confirm the alias: `backend/.venv/bin/python -c "from grimoire.store import module_display, modules; assert module_display is modules.display; print('alias ok')"`

- [ ] **Step 6: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split modules and fold module_display in as modules.display

Both of module_display's deferred imports wanted one function. Moving
assembled_fields to a leaf lets the pair import downward at module scope.
store/__init__ gains an explicit module_display alias, which it never had --
the name was only ever bound as a side effect of modules importing it."
```

---

### Task 6: Split `appearances`

**Files:**
- Create: `backend/src/grimoire/store/appearances/__init__.py`, `paths.py`, `versions.py`, `cast.py`, `transitions.py`
- Delete: `backend/src/grimoire/store/appearances.py`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `appearances/paths.py`: `AppearError`, `_ref`, `_split`, `_path`, `locked_actor_root(cid) -> Path`, `record(cid) -> dict`, `_write`, `_lock`
  - `appearances/cast.py`: `_actor_name`, `players_in_scene(cid, sid) -> list[dict]`, `player_names(cid, sid) -> list[str]`, `scene_cast(cid, sid) -> list[dict]`, `cast_detail`, `roster`, `roster_names`, `is_appeared`
  - `appearances/transitions.py`: `appear`, `leave`, `repoint_scenes`, `suggestions`
- Consumes: `campaigns/paths.py`, `overlay`.

`cast.py` is the whole point: `player_names`, `scene_cast` and `players_in_scene` read only the appearances record and actor roots — they touch no scene state. Only `appear`/`leave`/`suggestions` do. That is what lets `scenes` import `cast` at module scope in Task 9.

- [ ] **Step 1: Apply the recipe**

`transitions.py` still needs `scenes`, which is flat until Task 9, so it keeps `from .. import scenes` at module top — legal module-object form, and `scenes.py` does not import `appearances` at module scope, so no cycle. The three in-function `from . import scenes` lines in `appear`, `leave` and `suggestions` are deleted.

- [ ] **Step 2: Delete this task's baseline lines**

```
deferred grimoire.store.appearances::appear::grimoire.store.scenes
deferred grimoire.store.appearances::leave::grimoire.store.scenes
deferred grimoire.store.appearances::suggestions::grimoire.store.scenes
```

- [ ] **Step 3: Verify**

Four verification commands. Kind-specific: `backend/tests/test_appearances_store.py backend/tests/test_scene_store.py backend/tests/test_character_sync.py`.

- [ ] **Step 4: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split appearances, isolating cast from transitions

The cast readers touch no scene state; only appear/leave/suggestions do.
Separating them is what lets scenes import the cast side at module scope."
```

---

### Task 7: Split `sheets`

**Files:**
- Create: `backend/src/grimoire/store/sheets/__init__.py`, `paths.py`, `schema.py`, `read.py`, `pools.py`, `write.py`, `creation.py`, `coverage.py`, `advance.py`
- Delete: `backend/src/grimoire/store/sheets.py`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `sheets/paths.py`: `SheetError`, `SheetConflict`, `sheet_kind`, `_campaign_dir`, `_campaign_path`, `_world_dir`, `_world_path`, `_next_gen`, `_atomic_write_json`
  - `sheets/schema.py`: `_MUTABLE_TYPES`, `default_fields`, `_compute_derived`, `expression_scope`, `instance_errors`, `canonical_field_value`
  - `sheets/read.py`: `read(cid, kind, eid) -> dict`, `read_world(...)`, `list_refs(cid) -> list[tuple[str, str]]`, `world_list_refs`, `world_sheet_modules`
  - `sheets/write.py`: `write(...)`, `write_world(...)`, `delete(...)`, `set_field(...)`, `_set_field_locked(...)`
  - `sheets/coverage.py`: `seed(cid) -> None`, `coverage`, `world_coverage`
- Consumes: `modules/pack.py`, `modules/fields.py`, `modules/binding.py`, `campaigns/paths.py`, `worlds/paths.py`, `overlay`.

Note the two intentional name collisions from the spec: `sheets.delete_world` (deletes a world's sheets) is unrelated to `worlds.delete_world`, and `_pool_group_fields` exists separately in `modules/fields.py` and `sheets/pools.py`. Both keep their names.

`audit` currently reaches into `sheets._MUTABLE_TYPES` and `sheets._set_field_locked`. After this task those are ordinary exports of `schema.py` and `write.py`; Task 8 imports them normally.

- [ ] **Step 1: Apply the recipe**

- [ ] **Step 2: Verify**

Four verification commands. Kind-specific: `backend/tests/test_sheets_store.py backend/tests/test_audit_store.py backend/tests/test_checks_store.py`.

There are no baseline lines to delete in this task — `sheets` has no deferred imports of its own. Its split exists to unbundle `audit`'s access to its privates and to cut a 744-line file. `test_the_baseline_has_no_stale_entries` should stay green untouched.

- [ ] **Step 3: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split sheets into paths, schema, read, write and the rest

audit reached into _MUTABLE_TYPES and _set_field_locked; both are now named
exports of the files that own them rather than privates borrowed across a
module boundary."
```

---

### Task 8: Split `audit`

**Files:**
- Create: `backend/src/grimoire/store/audit/__init__.py`, `baselines.py`, `prompt.py`, `apply.py`
- Delete: `backend/src/grimoire/store/audit.py`
- Modify: `backend/tests/test_routes.py:2853`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `audit/baselines.py`: `read_baselines(cid) -> dict`, `schema_stamp(mid) -> str`, `capture_baseline(cid, sid) -> None`, `baseline_entry_valid`, `baseline_field`, `clear_baselines`, `repoint_scenes`
  - `audit/prompt.py`: `sheet_scope`, `render_value`, `sheet_blocks`, `roll_lines`, `build_prompt`
  - `audit/apply.py`: `AuditParseError`, `parse_output`, `apply_delta`, `materialize`
- Consumes: `sheets/read.py`, `sheets/schema.py`, `sheets/write.py`, `modules/pack.py`, `modules/binding.py`, `campaigns/paths.py`, `overlay`, `rolls`, `appearances/cast.py`.

This is the split that breaks the one cycle live in the graph today. `capture_baseline` uses `modules.resolve`, `campaigns.campaign_root`, `sheets.list_refs` and `locks` — never `scenes`. Only `prompt.py` reads `scenes.get_location_history`. So `scenes/lifecycle.py → audit/baselines.py` and `audit/prompt.py → scenes/read.py` never meet.

- [ ] **Step 1: Apply the recipe**

Preserve `capture_baseline`'s `locks.StoreBusy` re-raise and its long comment verbatim — that behavior is load-bearing (#234) and the comment explains why the one exception to "never raises" exists.

- [ ] **Step 2: Leave `test_routes.py:2853` alone, and retarget the modules patch**

`routes/scenes.py:299` calls `store.audit.materialize(...)` — a call-time lookup on the package — so the existing facade patch keeps working and retargeting it to `audit.apply` would silently stop injecting the crash. Leave it untouched.

`test_audit_store.py:100` patches `modules.load_pack`, and this task *does* rewrite audit's imports to name the submodule, so it moves here:

```python
from grimoire.store.modules import pack as modules_pack
...
    monkeypatch.setattr(modules_pack, "load_pack", _boom)
```

Then prove it still injects: drop the patch line and confirm the test fails.

- [ ] **Step 3: Delete this task's baseline line**

```
deferred grimoire.store.absorb::apply_edits::grimoire.store.audit
```

Hoist that import to the top of `absorb.py` in this task — `audit` is now importable without closing a loop.

- [ ] **Step 4: Verify**

Four verification commands. Kind-specific: `backend/tests/test_audit_store.py backend/tests/test_routes.py backend/tests/test_scene_refs.py`.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split audit into baselines, prompt and apply

capture_baseline never needed scenes; only the prompt builder does. Separating
them is what removes the audit/scene_refs/scenes cycle."
```

---

### Task 9: Split `scenes`

The largest split, and the one that closes the last cycle.

**Files:**
- Create: `backend/src/grimoire/store/scenes/__init__.py`, `paths.py`, `locking.py`, `serialize.py`, `read.py`, `turns.py`, `write.py`, `moment.py`, `lifecycle.py`
- Delete: `backend/src/grimoire/store/scenes.py`
- Modify: `backend/tests/test_locks_store.py:865`; `backend/tests/test_scene_store.py:996`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces:
  - `scenes/paths.py`: `SceneNotFound`, `_scenes_dir`, `_scene_path`, `_require_campaign`
  - `scenes/locking.py`: `_serialized(fn)` — the campaign-lock decorator
  - `scenes/serialize.py`: `match_name`, `_parse_messages`, `_serialize_messages`, `_numbering`, `repad`, `TRANSITION_SPEAKER`, `RESPONSE_FIELDS`
  - `scenes/read.py`: `read_scene(cid, sid) -> dict`, `read_scene_meta`, `list_scenes`, `is_pcless`, `get_dismissed`, `get_location_history`, `get_time_history`, `get_suggested_date`, `trailing_transitions`
  - `scenes/write.py`: `append_message`, `append_reply`, `split_reply`, `edit_message`, `remove_trailing_assistant_run`, `trim_continuation`, `mark_absorbed`, `RollMessageImmutable`
  - `scenes/lifecycle.py`: `create_scene`, `rename_scene`, `delete_scene`
- Consumes: `audit/baselines.py::capture_baseline`, `appearances/cast.py::player_names`, `scene_refs`, `campaigns/paths.py`, `overlay`.

`_serialized` gets its own `locking.py` rather than sharing `serialize.py`: the names are a coincidence — `_serialized` runs a mutation under the campaign lock, `serialize.py` is transcript marshalling. It decorates 19 functions landing in four files, all of which import it. Its lock is reentrant, so spreading the call sites changes no locking behavior.

- [ ] **Step 1: Apply the recipe**

`read.py` and `write.py` both import `from ..appearances import cast` and call `cast.player_names(...)`; the two in-function imports in `read_scene` and `edit_message` go away, along with their `# lazy: appearances lazily imports scenes too` comments.

`lifecycle.py` imports `from ..audit import baselines` and calls `baselines.capture_baseline(...)`; the in-function import in `_create_scene` goes away.

- [ ] **Step 2: Retarget the monkeypatches**

`test_locks_store.py:865` patches `scenes.append_message`:

```python
from grimoire.store.scenes import write as scenes_write
...
    monkeypatch.setattr(scenes_write, "append_message", spy_msg)
```

`test_scene_store.py:996` is inside the `_slow_frontmatter` helper, which wraps `parse_frontmatter` in a `time.sleep` to widen the lost-update race window — without it the concurrency tests for #254 are coin flips that pass on broken code. It patches a name `scenes.py` imported from `frontmatter`, and after the split every scenes submodule holds its own binding, so patching one is not enough. Patch every submodule that imports it:

```python
    real = frontmatter.parse_frontmatter

    def slow(text):
        parsed = real(text)
        time.sleep(delay)
        return parsed

    for mod in (scenes_read, scenes_write, scenes_moment, scenes_lifecycle):
        monkeypatch.setattr(mod, "parse_frontmatter", slow)
```

Then prove the widening still works: temporarily narrow it to only `scenes_read` and confirm at least one `test_scene_store.py` concurrency test becomes flaky or fails. Restore the full loop afterwards. A race-window helper that silently stops widening turns these tests green permanently, which is worse than deleting them.

- [ ] **Step 3: Delete this task's baseline lines**

```
deferred grimoire.store.scenes::read_scene::grimoire.store.appearances
deferred grimoire.store.scenes::edit_message::grimoire.store.appearances
deferred grimoire.store.scenes::_create_scene::grimoire.store.audit
cycle grimoire.store.audit,grimoire.store.scene_refs,grimoire.store.scenes
```

- [ ] **Step 4: Verify**

Four verification commands. Kind-specific: `backend/tests/test_scene_store.py backend/tests/test_scene_refs.py backend/tests/test_locks_store.py backend/tests/test_appearances_store.py backend/tests/test_routes.py`.

The `cycle` line disappearing is the headline: after this task the store's module graph is acyclic.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split scenes into paths, read, write, turns, moment and lifecycle

The last cycle goes with it: only lifecycle needs audit, and only the cast
readers are needed by read/write, so nothing imports in both directions."
```

---

### Task 10: Split `module_edit`

Pure size. 1297 lines holding the five concerns the codebase atlas names, plus `rename` — a single 229-line function with its own helper cluster.

**Files:**
- Create: `backend/src/grimoire/store/module_edit/__init__.py`, `staging.py`, `journal.py`, `packs.py`, `migrate.py`, `layout.py`, `rename.py`, `edits.py`
- Delete: `backend/src/grimoire/store/module_edit.py`
- Modify: `backend/tests/test_module_edit.py:627`, `:631`, `:914`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces: function placement is in the spec's `module_edit/` table. Public entry points (`create_module`, `delete_module`, `duplicate_module`, `import_module`, `export_module`, `rename`, `recover`, the `upsert_*`/`delete_*` family, `set_layout`, `set_theme`, `set_manifest`, `check_proposal_guard`) all stay re-exported from `module_edit/__init__.py`.
- Consumes: `modules/pack.py`, `modules/validate.py`, `locks`, `atomic`.

- [ ] **Step 1: Apply the recipe**

Carry the `# atomic-ok:` annotation at old `module_edit.py:218-223` with the code it exempts. `test_atomic_guard.py` discovers files with `rglob`, so the new location is scanned automatically — but the marker must stay attached to its own call or the guard will flag it.

- [ ] **Step 2: Retarget the monkeypatches**

All three patch names landing in `migrate.py`:

```python
from grimoire.store.module_edit import migrate as me_migrate
...
    monkeypatch.setattr(me_migrate, "_run_migration", ...)   # :627, :631
    monkeypatch.setattr(me_migrate, "_campaign_locks", _boom)  # :914
```

- [ ] **Step 3: Delete this task's baseline line**

```
deferred grimoire.store.module_edit::rename.mutate::grimoire.store.frontmatter
```

`frontmatter` is an L0 leaf, so this import simply moves to the top of `rename.py`.

- [ ] **Step 4: Verify**

Four verification commands. Kind-specific: `backend/tests/test_module_edit.py backend/tests/test_modules_store.py backend/tests/test_atomic_guard.py`.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split module_edit along its five concerns

Staging, journalling, pack admin, migration, layout editing and rename each
get a file; rename alone was 229 lines with its own helper cluster."
```

---

### Task 11: Split `absorb`

**Files:**
- Create: `backend/src/grimoire/store/absorb/__init__.py`, `prompt.py`, `parse.py`, `materialize.py`, `weather.py`, `apply.py`, `snapshots.py`
- Delete: `backend/src/grimoire/store/absorb.py`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces: `absorb/prompt.py::build_prompt`, `absorb/parse.py::{extract_object, parse_output}`, `absorb/materialize.py::materialize`, `absorb/apply.py::apply_edits`, `absorb/snapshots.py::{relationships_snapshot, plot_snapshot, group_snapshot, state_snapshot}`.
- Consumes: the eighteen store modules `absorb` reads, now via their new submodules.

- [ ] **Step 1: Apply the recipe**

The `absorb` eval case requires every section to be present and to materialize into applicable edits; `evals/recordings/absorb.truncated.json` is the counterexample that must keep failing. Do not change `parse_output`'s section handling.

- [ ] **Step 2: Delete this task's baseline lines**

```
deferred grimoire.store.absorb::_apply_weather::grimoire.store.calendars
deferred grimoire.store.absorb::_apply_weather::grimoire.store.weather
deferred grimoire.store.absorb::_weather_edits::grimoire.store.scenes
deferred grimoire.store.absorb::_weather_edits::grimoire.store.weather
```

- [ ] **Step 3: Verify**

Four verification commands, plus the eval suite (it runs inside `pytest backend`, but run it explicitly here since `absorb` is what it covers):

```bash
backend/.venv/bin/python -m pytest backend/tests/test_absorb_store.py backend/tests/test_evals.py -q
```

- [ ] **Step 4: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split absorb into prompt, parse, materialize, weather and apply

One model response fanned out into eighteen store modules from a single file;
each edit domain now has its own, so the blast radius of a change is visible."
```

---

### Task 12: Split `context`

**Files:**
- Create: `backend/src/grimoire/store/context/__init__.py`, `macros.py`, `cast.py`, `world_state.py`, `mechanics.py`, `story.py`, `assemble.py`, `tokens.py`
- Delete: `backend/src/grimoire/store/context.py`
- Modify: `backend/tests/test_context.py:1157`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Produces: `context/assemble.py::{build_messages, build_director_messages, build_opener_messages, context_sections, activate}`, `context/macros.py::expand_macros`, `context/tokens.py::count_tokens`.
- Consumes: the twenty-odd store modules `context` reads.

This is the app's widest reader (fan-out 57) and sits on the play path, so the verification here matters more than elsewhere.

- [ ] **Step 1: Apply the recipe**

- [ ] **Step 2: Retarget the monkeypatch**

`test_context.py:1157` patches `context._drift_roster`, which lands in `cast.py`:

```python
from grimoire.store.context import cast as context_cast
...
    monkeypatch.setattr(context_cast, "_drift_roster", counted)
```

`_assemble` in `assemble.py` must call it as `cast._drift_roster(...)` for the patch to bite.

- [ ] **Step 3: Leave the baseline alone**

This task deletes **no** baseline lines. `tokens.py` keeps its in-function `tiktoken` import for now; Task 13 hoists it.

One line does change *shape*, though, because its module was renamed: `deferred grimoire.store.context::_encoder::tiktoken` becomes `deferred grimoire.store.context.tokens::_encoder::tiktoken`. Edit that line in place to match — `test_the_baseline_has_no_stale_entries` will fail until you do, and its message prints the exact stale string.

- [ ] **Step 4: Verify**

Four verification commands. Kind-specific: `backend/tests/test_context.py backend/tests/test_routes.py backend/tests/test_evals.py`.

Also run the template harnesses, since `context` assembles prompts:

```bash
backend/.venv/bin/python scripts/verify_templates.py
backend/.venv/bin/python evals/run.py
```

Expected: both pass. Neither makes network calls; `evals/run.py --live` is the opt-in one and is not run here.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Split context into macros, cast, world_state, mechanics and assemble

The widest reader in the app, on the play path. Section builders were the
natural seam; assemble keeps the ordering that produces the prompt."
```

---

### Task 13: Hoist the deferred imports in the modules that were never split

Tasks 3-12 clear 28 of the 58 baseline lines. Of the 30 left, 4 are the third-party imports Task 14 owns; the other 26 sit in modules this refactor does not restructure — deferred to dodge cycles that no longer exist once `scenes`, `campaigns` and `modules` are packages. This task is a pure import-hoisting sweep, no file moves.

It must come **after** Task 9. Hoisting `chronicle → scenes` today would close `scenes → scene_refs → chronicle → scenes`; once `scenes` is split, `chronicle` imports `scenes.read` while `scenes.lifecycle` reaches `scene_refs`, and the two never meet.

**Files:**
- Modify: `backend/src/grimoire/store/chronicle.py`, `proposals.py`, `response_presets.py`, `export.py`, `entity_schema.py`, `suggest.py`, `weather/__init__.py`, `checks.py`, `calendars/base.py`, `plot.py`, `relationships.py`, `epub.py`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Consumes: the packages created in Tasks 3-9. No public API changes.
- Produces: nothing new — same functions, imports relocated.

- [ ] **Step 1: Hoist, one module at a time**

For each function-body import below, move it to the module top in the required form and delete the in-function line plus any comment explaining the deferral (those comments become false).

| Module | Was deferred inside | Now imports at top |
|---|---|---|
| `chronicle.py` | `scene_facts`, `transcript_text` | `from .. import prompts`; `from .appearances import cast`; `from . import entities, overlay`; `from .scenes import read as scenes_read` |
| `proposals.py` | `project`, `commit_narration` | `from . import checks, rolls`; `from .scenes import read as scenes_read, write as scenes_write` |
| `response_presets.py` | `usage`, `resolve`, `validity` | `from . import config, styles`; `from .campaigns import read as campaigns_read`; `from .scenes import read as scenes_read` |
| `export.py` | `_resolve_image`, `_avatar` | `from . import assets` |
| `entity_schema.py` | `_valid_climate` | `from . import climates` |
| `suggest.py` | `greeting_candidates` | `from . import playing` |
| `weather/__init__.py` | `sweep` | `from ..scenes import read as scenes_read` |
| `checks.py` | `available_checks` | `from .scenes import read as scenes_read` |
| `calendars/base.py` | `get_provider`, `list_providers` | `from . import plugins` |
| `plot.py` | `render_open` | `from .. import prompts` |
| `relationships.py` | `render_present` | `from .. import prompts` |
| `epub.py` | `_env` | `from .. import prompts` |

`calendars/base.py` needs care: hoisting `from . import plugins` must **not** cause user plugins to load at import time. `plugins.load_custom_providers()` stays a call inside `get_provider`/`list_providers`. Only the module import moves. `plugins.py` imports nothing from `base`, so this closes no loop — user plugin files import `base` themselves, at call time, when `base` is fully initialized.

- [ ] **Step 2: Verify the plugin loader is still lazy**

Prove `calendars/base.py` did not start executing user code at import:

```bash
backend/.venv/bin/python -c "
import grimoire.store.calendars.plugins as p
import grimoire.store.calendars.base as b
print('loaded set after import:', p._loaded)
assert p._loaded == set(), 'plugins loaded at import time -- hoist went too far'
print('lazy ok')
"
```

Expected: `loaded set after import: set()` then `lazy ok`.

- [ ] **Step 3: Delete this task's baseline lines**

All 26 remaining `deferred` lines except the four third-party ones (which Task 14 owns):

```
deferred grimoire.store.calendars.base::get_provider::grimoire.store.calendars.plugins
deferred grimoire.store.calendars.base::list_providers::grimoire.store.calendars.plugins
deferred grimoire.store.checks::available_checks::grimoire.store.scenes
deferred grimoire.store.chronicle::scene_facts::grimoire.store.appearances
deferred grimoire.store.chronicle::scene_facts::grimoire.store.entities
deferred grimoire.store.chronicle::scene_facts::grimoire.store.overlay
deferred grimoire.store.chronicle::scene_facts::grimoire.store.scenes
deferred grimoire.store.chronicle::transcript_text::grimoire.prompts
deferred grimoire.store.chronicle::transcript_text::grimoire.store.scenes
deferred grimoire.store.entity_schema::_valid_climate::grimoire.store.climates
deferred grimoire.store.epub::_env::grimoire.prompts
deferred grimoire.store.export::_avatar::grimoire.store.assets
deferred grimoire.store.export::_resolve_image::grimoire.store.assets
deferred grimoire.store.plot::render_open::grimoire.prompts
deferred grimoire.store.proposals::commit_narration::grimoire.store.scenes
deferred grimoire.store.proposals::project::grimoire.store.checks
deferred grimoire.store.proposals::project::grimoire.store.rolls
deferred grimoire.store.proposals::project::grimoire.store.scenes
deferred grimoire.store.relationships::render_present::grimoire.prompts
deferred grimoire.store.response_presets::resolve::grimoire.store.styles
deferred grimoire.store.response_presets::usage::grimoire.store.campaigns
deferred grimoire.store.response_presets::usage::grimoire.store.config
deferred grimoire.store.response_presets::usage::grimoire.store.scenes
deferred grimoire.store.response_presets::validity::grimoire.store.styles
deferred grimoire.store.suggest::greeting_candidates::grimoire.store.playing
deferred grimoire.store.weather::sweep::grimoire.store.scenes
```

Note `epub::_env::grimoire.prompts` is hoisted here, but `epub::_env::jinja2` belongs to Task 14 — `_env()` loses its grimoire import now and its `jinja2` import next task.

After this the baseline holds exactly the four third-party lines.

- [ ] **Step 4: Verify**

```bash
backend/.venv/bin/python -m pytest backend/tests/test_import_guard.py -q
backend/.venv/bin/python -m pytest backend/tests/test_chronicle_store.py backend/tests/test_proposals_store.py \
  backend/tests/test_response_presets.py backend/tests/test_export_store.py backend/tests/test_checks_store.py \
  backend/tests/test_calendar_plugins.py backend/tests/test_weather_blocks.py -q
backend/.venv/bin/python -m pytest backend -q
```

Expected: guard down to the four third-party violations; everything else at the Global Constraints baseline.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src/grimoire/store backend/tests
git commit -m "Hoist the deferred imports in the unsplit modules

Twelve modules deferred imports to dodge cycles that the package splits have
already removed. The calendars plugin loader stays lazy -- only its module
import moves, not load_custom_providers()."
```

---

### Task 14: Hoist the four third-party imports

**Files:**
- Modify: `backend/src/grimoire/prompts.py:29-32`
- Modify: `backend/src/grimoire/store/epub.py:32-33`
- Modify: `backend/src/grimoire/store/context/tokens.py`
- Modify: `backend/src/grimoire/claude_agent.py:35`
- Modify: `backend/tests/import_guard_baseline.txt`

**Interfaces:**
- Consumes: `context/tokens.py` from Task 12.
- Produces: no API change. `count_tokens` keeps its heuristic fallback; `claude_agent.stream` keeps raising the same error when the SDK is absent.

`jinja2` is a base dependency, so it hoists plainly. `tiktoken` and `claude_agent_sdk` are optional extras (`desktop` and `claude`) and are absent on Android, so they need a module-level sentinel.

- [ ] **Step 1: Hoist `jinja2` in both places**

In `backend/src/grimoire/prompts.py`, move the import to the top and simplify `_env()`:

```python
import jinja2
```

Do the same in `backend/src/grimoire/store/epub.py`. Keep `_env()`'s caching behavior exactly as it is; only the import moves.

- [ ] **Step 2: Give `tiktoken` a sentinel**

At the top of `backend/src/grimoire/store/context/tokens.py`:

```python
try:
    import tiktoken
except ImportError:      # optional `desktop` extra; absent on Android
    tiktoken = None
```

`_encoder()` returns `None` when `tiktoken is None`, and `count_tokens` takes its existing heuristic path. Do not change the heuristic.

- [ ] **Step 3: Give `claude_agent_sdk` a sentinel**

At the top of `backend/src/grimoire/claude_agent.py`:

```python
try:
    import claude_agent_sdk
except ImportError:      # optional `claude` extra
    claude_agent_sdk = None
```

`stream()` raises the same error it raises today when the SDK is missing — read the existing `except ImportError` path and preserve its message exactly.

- [ ] **Step 3b: Rework how `test_claude_agent.py` installs its fake**

A module-level import caches the SDK in a global, which breaks the existing test harness. `install_fake_sdk` (`tests/test_claude_agent.py:27`) does `monkeypatch.setitem(sys.modules, "claude_agent_sdk", mod)` at line 47 — but `grimoire.claude_agent` was already imported at collection time, so its global still holds `None` or the real SDK and the fake is never observed. Every test routed through `install_fake_sdk` (lines 56, 63, 90, 98, 107, 121) would stop testing what it claims.

Patch the module global instead of the `sys.modules` entry:

```python
    monkeypatch.setattr(claude_agent, "claude_agent_sdk", mod)
```

And the missing-SDK test at line 82, which currently sets `sys.modules["claude_agent_sdk"] = None` to force an `ImportError`, becomes:

```python
    monkeypatch.setattr(claude_agent, "claude_agent_sdk", None)
```

- [ ] **Step 3c: Prove the fake is actually observed**

An SDK double that silently stops being installed leaves these tests green and meaningless. Make the fake raise a sentinel and confirm the test sees it:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_claude_agent.py -q
```

Expected: all pass. Then temporarily change `install_fake_sdk` to patch nothing at all and re-run — the success and exception-normalization tests must **fail**. Restore afterwards.

- [ ] **Step 4: Verify the optional paths still degrade**

Prove the sentinels work rather than assuming it:

```bash
backend/.venv/bin/python -c "
import sys
sys.modules['tiktoken'] = None
import grimoire.store.context.tokens as t
print('heuristic path:', t.count_tokens('hello world' * 50))
"
```

Expected: a number, no traceback.

Run: `backend/.venv/bin/python -m pytest backend/tests/test_claude_agent.py backend/tests/test_context.py backend/tests/test_epub_store.py -q`

Expected: all pass.

- [ ] **Step 5: Delete this task's baseline lines**

```
deferred grimoire.claude_agent::stream::claude_agent_sdk
deferred grimoire.prompts::_env::jinja2
deferred grimoire.store.context::_encoder::tiktoken
deferred grimoire.store.epub::_env::jinja2
```

The `context` line's module name will have changed to `grimoire.store.context.tokens::_encoder::tiktoken` after Task 12. Delete whichever form is present — `test_the_baseline_has_no_stale_entries` will tell you if you got it wrong.

- [ ] **Step 6: Verify and commit**

Run: `backend/.venv/bin/python -m pytest backend -q` and `backend/.venv/bin/python scripts/verify_templates.py`

```bash
git add -A backend/src/grimoire backend/tests
git commit -m "Hoist the four lazy third-party imports

jinja2 is a base dep and moves plainly. tiktoken and claude_agent_sdk are
optional extras, so they get a module-level None sentinel that the existing
fallback paths already check -- Android keeps working without them."
```

---

### Task 15: Retire the baseline and document the rule

**Files:**
- Delete: `backend/tests/import_guard_baseline.txt`
- Modify: `backend/tests/test_import_guard.py`
- Modify: `backend/src/grimoire/store/__init__.py` (docstring)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: a fully clean violation list from Tasks 3-14.

- [ ] **Step 1: Confirm the baseline is empty**

```bash
cd backend && .venv/bin/python -c "
import sys; sys.path.insert(0,'src'); sys.path.insert(0,'.')
from tests import test_import_guard as g
v = g._violations()
print(len(v), 'violations remaining')
for x in v: print(' ', x)
"
```

Expected: `0 violations remaining`. If not, the listed items are unfinished work from an earlier task — go finish it rather than exempting it here.

- [ ] **Step 2: Remove the baseline machinery**

Delete `backend/tests/import_guard_baseline.txt`. In `test_import_guard.py`, delete `BASELINE`, `_baseline()` and `test_the_baseline_has_no_stale_entries`, and simplify the remaining test:

```python
def test_no_import_violations():
    """Every import is at module scope and the module graph is acyclic."""
    found = _violations()
    assert not found, (
        "import-graph violations:\n  " + "\n  ".join(found)
        + f"\n\nFix them, or -- only with a stated reason -- add a "
          f"`# {MARKER} <reason>` comment on the import.")
```

Update the module docstring's last paragraph: the ratchet is gone, the rule is absolute.

- [ ] **Step 3: Record the rule where it will be read**

In `backend/src/grimoire/store/__init__.py`, replace any claim that import order here is load-bearing with the truth: the graph is acyclic and enforced by `tests/test_import_guard.py`, and cross-package imports inside the store bind submodules.

Add to `CLAUDE.md` under "Working notes":

```markdown
- **Imports in `backend/src/grimoire/` are all at module scope and the module
  graph is acyclic**, enforced by `backend/tests/test_import_guard.py`. Inside
  `store/`, a cross-package import binds a *submodule* and keeps it as a module
  object — `from ..campaigns import read` then `read.world_refs()`, never
  `from ..campaigns import world_refs`. Binding a name off a package that is
  still initializing raises at import time while the file graph stays acyclic,
  so the cycle check alone would not catch it.
```

- [ ] **Step 4: Verify the guard still bites without a baseline**

Re-run the Step 5 probe from Task 2: add `from ..calendars import CalendarError` to `backend/src/grimoire/store/weather/settings.py`, confirm the guard fails, then `git checkout` the file and confirm it passes.

- [ ] **Step 5: Full verification**

```bash
backend/.venv/bin/python -m pytest backend -q
backend/.venv/bin/python scripts/verify_templates.py
backend/.venv/bin/python evals/run.py
cd frontend && npx vitest run && npx tsc -b
```

Expected: backend at the Global Constraints baseline; templates and evals pass; frontend untouched and green.

- [ ] **Step 6: Commit**

```bash
git add -A backend CLAUDE.md
git commit -m "Retire the import baseline; the rule is now absolute

Every violation is fixed, so the ratchet file goes and the guard simply
requires zero. CLAUDE.md records the submodule import form, which the cycle
check alone cannot enforce."
```

---

## Codex review gates

Per `CLAUDE.md`, before considering this work done:

- `/codex:review` against the full diff.
- `/codex:adversarial-review` against the diff *and* `docs/superpowers/specs/2026-07-30-store-import-layering-design.md`, asking specifically whether the changes implement the spec — gaps, drift and quietly-dropped requirements are the target.

`codex` is not installed in the remote container this plan was written in; these must be run wherever it is available. The plan-stage gate was substituted with a manual adversarial pass, which found the package-`__init__` hazard now covered by rule 3.
