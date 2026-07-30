"""Check a proposed function -> file mapping for intra-package import cycles.

Reads the partition tables straight out of the spec markdown, resolves every
top-level name in the source module to its proposed file, then builds the
file-level call graph and reports strongly connected components.

This is the check that three rounds of review kept doing by hand.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

STORE = pathlib.Path("backend/src/grimoire/store")
SPEC = pathlib.Path("docs/superpowers/specs/2026-07-30-store-import-layering-design.md")

# Which source module each spec section describes.
SECTIONS = ["campaigns", "worlds", "scenes", "appearances", "modules",
            "sheets", "audit", "module_edit", "absorb", "context"]
# modules/ absorbs module_display.py as display.py
EXTRA_SOURCES = {"modules": ["module_display"]}


def parse_spec() -> dict[str, dict[str, str]]:
    """{module: {function name: target file stem}} from the spec's tables."""
    text = SPEC.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    for sec in SECTIONS:
        m = re.search(rf"^### `{re.escape(sec)}/`\n(.*?)(?=^### |^## )", text, re.S | re.M)
        if not m:
            print(f"  !! no spec section for {sec}")
            continue
        mapping: dict[str, str] = {}
        for row in re.finditer(r"^\| `([a-z_]+)\.py` \|(.*)$", m.group(1), re.M):
            stem, rest = row.group(1), row.group(2)
            for name in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", rest):
                mapping[name] = stem
        out[sec] = mapping
    return out


def toplevel_names(tree: ast.Module) -> dict[str, ast.AST]:
    """Every top-level binding, annotated assignments included.

    `FILE_KINDS: tuple[str, ...] = (...)` is an AnnAssign, and missing it made
    a name shared by four proposed files invisible to this check.
    """
    got: dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            got[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    got[t.id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            got[n.target.id] = n
    return got


def sccs(graph: dict[str, set[str]]) -> list[list[str]]:
    index, low, onstk, stack, out, ctr = {}, {}, {}, [], [], [0]

    def strong(v):
        index[v] = low[v] = ctr[0]; ctr[0] += 1
        stack.append(v); onstk[v] = True
        for w in sorted(graph.get(v, ())):
            if w not in index:
                strong(w); low[v] = min(low[v], low[w])
            elif onstk.get(w):
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop(); onstk[w] = False; comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or comp[0] in graph.get(comp[0], ()):
                out.append(sorted(comp))
    for n in sorted(graph):
        if n not in index:
            strong(n)
    return out


def main() -> int:
    spec = parse_spec()
    bad = 0
    for mod, mapping in spec.items():
        sources = [mod] + EXTRA_SOURCES.get(mod, [])
        owner: dict[str, str] = {}
        trees = {}
        for src in sources:
            p = STORE / f"{src}.py"
            if not p.exists():
                print(f"  !! missing source {p}")
                continue
            t = ast.parse(p.read_text(encoding="utf-8"))
            trees[src] = t
            for name in toplevel_names(t):
                # One node per unassigned name, never a shared bucket: lumping
                # them together hid every cycle that ran through two different
                # unplaced names.
                owner[name] = mapping.get(name, f"<UNASSIGNED:{name}>")

        unassigned = sorted(k for k, v in owner.items() if v.startswith("<UNASSIGNED"))
        graph: dict[str, set[str]] = {}
        for src, t in trees.items():
            for name, node in toplevel_names(t).items():
                home = owner[name]
                graph.setdefault(home, set())
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and sub.id in owner:
                        dest = owner[sub.id]
                        if dest != home:
                            graph[home].add(dest)
        cycles = sccs(graph)
        # An unplaced name is not a clean partition. Where it lands can create
        # a cycle this graph cannot see -- SYNTHETIC_SPEAKERS is read by both
        # turns.py and write.py, so putting it in the wrong one closes a loop.
        ok = not cycles and not unassigned
        status = "OK   " if ok else ("CYCLE" if cycles else "GAPS ")
        print(f"[{status}] {mod}: {len(mapping)} names mapped, "
              f"{len(unassigned)} unassigned")
        if unassigned:
            bad += 1
            for name in unassigned:
                users = sorted({owner[u] for u, node in
                                ((u, n) for u, n in toplevel_names(trees[src]).items())
                                if any(isinstance(x, ast.Name) and x.id == name
                                       for x in ast.walk(node))} - {f"<UNASSIGNED:{name}>"})
                print(f"        UNPLACED {name}"
                      + (f" -- read by {', '.join(users)}" if users else " -- unread"))
        for c in cycles:
            bad += 1
            print(f"        CYCLE: {' <-> '.join(c)}")
            # name the edges so the fix is obvious
            for a in c:
                for b in c:
                    if a == b:
                        continue
                    who = [n for n, h in owner.items() if h == a
                           and any(isinstance(s, ast.Name) and owner.get(s.id) == b
                                   for s in ast.walk(toplevel_names(trees[[k for k in trees][0]]).get(n, ast.Pass())))]
                    if who:
                        print(f"          {a} -> {b} via {', '.join(sorted(who)[:6])}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
