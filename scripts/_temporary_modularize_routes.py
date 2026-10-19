#!/usr/bin/env python3
"""One-shot, source-preserving conversion of two large route modules to packages.

Input is the unchanged routes/models.py and routes/campaigns.py of this branch.
The script does not rewrite endpoint logic, schemas, or route decorators.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "backend" / "src" / "grimoire" / "routes"


def _shift_relative_imports(src: str) -> str:
    """A former routes/foo.py import is one package level deeper in foo/*.py."""
    return re.sub(
        r"(?m)^from (\.+)([\w.]*)(\s+import)",
        lambda m: "from " + m.group(1) + "." + m.group(2) + m.group(3),
        src,
    )


def _written(path: Path, src: str) -> None:
    ast.parse(src, filename=str(path))
    path.write_text(src, encoding="utf-8")


def _exports(tree: ast.Module, start: int, end: int, omitted: set[str] = frozenset()) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if not start <= node.lineno <= end:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name not in omitted:
                names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.extend(n.id for n in targets if isinstance(n, ast.Name) and n.id not in omitted)
    return names


def _facade_imports(module: str, names: list[str]) -> str:
    return "from ." + module + " import (\n" + "".join("    " + n + ",\n" for n in names) + ")\n"


def split_models() -> None:
    path = ROUTES / "models.py"
    s = path.read_text(encoding="utf-8")
    lines = s.splitlines(keepends=True)
    tree = ast.parse(s)
    types = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    assert len(types) == 156 and len(lines) >= 1489, "models.py changed since assessment"
    doc = "".join(lines[:tree.body[0].end_lineno])
    header = _shift_relative_imports("".join(lines[tree.body[0].end_lineno:types[0].lineno-1]))
    segments = [
        ("_settings", 28, 426), ("_worlds", 427, 549),
        ("_mechanics", 550, 641), ("_actors", 642, 833),
        ("_scenes", 834, 1239), ("_continuity", 1240, 1426),
        ("_extras", 1427, len(lines)),
    ]
    directory = ROUTES / "models"
    directory.mkdir()
    init = doc + "\n" + header + "\n"
    all_names: list[str] = []
    for module, a, b in segments:
        names = _exports(tree, a, b)
        assert names, module
        extra = "\nfrom ._settings import ResponseSettings\n" if module == "_scenes" else ""
        body = (
            '"""Request models: ' + module[1:] + ' domain."""\n\n'
            + header + extra + "\n" + "".join(lines[a-1:b])
        )
        _written(directory / (module + ".py"), body)
        init += _facade_imports(module, names) + "\n"
        all_names.extend(names)
        print(f"models/{module}.py: {len(body.splitlines())} lines, {len(names)} exported classes", flush=True)
    assert len(all_names) == 156 and len(set(all_names)) == len(all_names)
    _written(directory / "__init__.py", init)
    path.unlink()


def split_campaigns() -> None:
    path = ROUTES / "campaigns.py"
    s = path.read_text(encoding="utf-8")
    lines = s.splitlines(keepends=True)
    tree = ast.parse(s)
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    assert len(funcs) == 126 and len(lines) >= 2830, "campaigns.py changed since assessment"
    doc = "".join(lines[:tree.body[0].end_lineno])
    header = _shift_relative_imports("".join(lines[tree.body[0].end_lineno:98]))
    assert "from ... import store" in header
    # Each slice is a complete sequence of top-level definitions and their
    # decorators, with source order matching the old FastAPI registration order.
    slices = [
        ("_settings", 100, 342), ("_calendar", 343, 558),
        ("_core", 559, 653), ("_images", 654, 982),
        ("_management", 983, 1112), ("_sync", 1113, 1352),
        ("_ledger", 1353, 1794), ("_timeline", 1795, 1950),
        ("_actors", 1951, 2513), ("_pcs", 2514, len(lines)),
    ]
    directory = ROUTES / "campaigns"
    directory.mkdir()
    _written(directory / "_router.py",
             '"""Shared APIRouter; endpoints register in original module order."""\n\n'
             'from fastapi import APIRouter\n\nrouter = APIRouter()\n')
    # This one display-name helper is called by the earlier settings routes as
    # well as synchronization and timeline. Extract it rather than importing
    # the later sync module early (which changes route precedence).
    name_def = next(n for n in funcs if n.name == "_record_name")
    assert name_def.lineno == 1176
    name_body = "".join(lines[name_def.lineno-1:name_def.end_lineno])
    _written(directory / "_names.py",
             '"""Source-aware campaign record display names."""\n\n'
             'from __future__ import annotations\n\nfrom ... import store\n\n\n' + name_body)
    extra = {
        "_settings": "from ._names import _record_name\n",
        "_calendar": "from ._settings import _clock_friendly\n",
        "_core": "", "_images": "", "_management": "",
        "_sync": "from ._names import _record_name\n",
        "_ledger": "",
        "_timeline": "from ._names import _record_name\n",
        "_actors": "from ._timeline import _absorbed_cast_refs\n",
        "_pcs": "from ._actors import _campaign_wroot\n",
    }
    init = (
        '"""Campaign routes, grouped by domain with stable endpoint imports.\n\n'
        'Import order is route-registration order. FastAPI matches first-fit;\n'
        'moving this registration order can silently change matched routes.\n'
        '"""\n\n'
        'from ._router import router\nfrom ._names import _record_name\n\n'
    )
    names_seen: list[str] = ["_record_name"]
    for module, a, b in slices:
        names = _exports(tree, a, b, {"_record_name"})
        body_src = "".join(lines[a-1:b])
        if module == "_sync":
            assert name_body in body_src
            body_src = body_src.replace(name_body, "", 1)
        body = (
            '"""Campaign HTTP endpoints: ' + module[1:] + ' domain."""\n\n'
            + header + "\nfrom ._router import router\n" + extra[module]
            + "\n" + body_src
        )
        _written(directory / (module + ".py"), body)
        init += _facade_imports(module, names) + "\n"
        names_seen.extend(names)
        print(f"campaigns/{module}.py: {len(body.splitlines())} lines, {len(names)} exports", flush=True)
    assert len(set(names_seen)) == len(names_seen)
    # Make sure every original public and private defined name is exported.
    all_def_names = [
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    all_def_names += [n.id for v in tree.body if isinstance(v, (ast.Assign, ast.AnnAssign))
                      for n in (v.targets if isinstance(v, ast.Assign) else [v.target])
                      if isinstance(n, ast.Name) and n.id != "router"]
    assert set(all_def_names) == set(names_seen), (set(all_def_names)-set(names_seen), set(names_seen)-set(all_def_names))
    _written(directory / "__init__.py", init)
    path.unlink()


def update_status_guard() -> None:
    path = ROOT / "backend" / "tests" / "test_llm_error_status.py"
    src = path.read_text(encoding="utf-8")
    old = '''    names = {p.name for p in SRC.glob("*.py")}
    assert {"campaigns.py", "characters.py", "config.py", "scenes.py",
            "streaming.py", "worlds.py"} <= names


@pytest.mark.parametrize("path", sorted(SRC.glob("*.py")), ids=lambda p: p.name)
def test_no_route_spells_out_its_own_llm_status(path):
    assert scan(path.read_text(encoding="utf-8")) == [], \\
        f"{path.name} decides its own status instead of calling _llm_http_error()"
'''
    new = '''    # A domain may be a single module or a package of endpoint modules.
    names = {p.stem for p in SRC.glob("*.py")}
    names |= {p.name for p in SRC.iterdir()
              if p.is_dir() and (p / "__init__.py").is_file()}
    assert {"campaigns", "characters", "config", "scenes",
            "streaming", "worlds"} <= names


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")),
                         ids=lambda p: str(p.relative_to(SRC)))
def test_no_route_spells_out_its_own_llm_status(path):
    assert scan(path.read_text(encoding="utf-8")) == [], \\
        f"{path.relative_to(SRC)} decides its own status instead of calling _llm_http_error()"
'''
    assert old in src, "LLM status guard changed; review rather than silently bypassing it"
    _written(path, src.replace(old, new, 1))


if __name__ == "__main__":
    split_models()
    split_campaigns()
    update_status_guard()
    print("Converted routes/models.py and routes/campaigns.py into real packages.", flush=True)
