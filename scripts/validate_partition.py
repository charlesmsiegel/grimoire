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
            continue   # main() turns a missing section into a hard failure
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


def consumer_imports(spec: dict[str, dict[str, str]]) -> int:
    """For every module outside the split packages that reaches into one,
    print the submodules it must import and every name it needs.

    Hand-listing these is what produced NameError-shaped gaps in the plan:
    `chronicle.transcript_text` reads `scenes.TRANSITION_SPEAKER`, which lives
    in `scenes/serialize.py`, not the `scenes/read.py` the table mentioned.
    """
    import collections
    absorbed = {m for extra in EXTRA_SOURCES.values() for m in extra}
    bad = 0
    for p in sorted(STORE.rglob("*.py")):
        rel = p.relative_to(STORE)
        owner_pkg = rel.parts[0] if len(rel.parts) > 1 else None
        # Files inside a package being split are handled by the partition
        # tables, not here. Everything else is a consumer -- including
        # weather/__init__.py, which root-level globbing missed entirely
        # despite Task 13 modifying it.
        if owner_pkg in spec or p.stem in spec or p.stem in absorbed:
            continue
        label = str(rel)
        depth = len(rel.parts) - 1          # how many dots the import needs
        tree = ast.parse(p.read_text(encoding="utf-8"))
        # Only names this file actually imports. `_type_scope(sheets: dict)`
        # shadows the store module with a parameter, and counting `sheets.get`
        # there reported a store function that does not exist.
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                imported |= {a.asname or a.name for a in n.names}
            elif isinstance(n, ast.Import):
                imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
        need = collections.defaultdict(dict)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id in spec and n.value.id in imported):
                need[n.value.id][n.attr] = spec[n.value.id].get(n.attr)
        if not need:
            continue
        print(f"--- {label}")
        for pkg, names in sorted(need.items()):
            missing = sorted(k for k, v in names.items() if v is None)
            files = sorted({v for v in names.values() if v})
            print(f"    from {'.' * (depth + 1)}{pkg} import {', '.join(files)}")
            for k, v in sorted(names.items()):
                print(f"        {pkg}.{k} -> {v or 'UNPLACED'}")
            if missing:
                bad += 1
                print(f"    !! unplaced in spec: {', '.join(missing)}")
    return bad


def _local_attrs(node: ast.AST, imported: set[str]) -> set[str]:
    """Attributes taken on *local* names inside `node`.

    `for mod in (appearances, audit, ...): mod.repoint_scenes(...)` reaches the
    target through a loop variable, so the module names carry no attribute of
    their own. What the loop calls is the only clue to which submodule the
    edge lands on.
    """
    out: set[str] = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                and sub.value.id not in imported):
            out.add(sub.attr)
    return out


def store_graph(spec: dict[str, dict[str, str]]) -> int:
    """Cycles in the WHOLE post-refactor store graph, not just inside packages.

    The per-package check cannot see a loop that leaves a package and comes
    back: `repad` placed in scenes/serialize.py calls scene_refs.repoint, and
    scene_refs imports chronicle, which must import scenes.serialize for
    TRANSITION_SPEAKER -- a three-package cycle every intra-package run
    reported clean.
    """
    import collections
    absorbed = {m for extra in EXTRA_SOURCES.values() for m in extra}
    place: dict[str, str] = {}          # "pkg.name" -> "pkg.file"
    for pkg, mapping in spec.items():
        for name, fname in mapping.items():
            place[f"{pkg}.{name}"] = f"{pkg}.{fname}"

    def node_for(mod: str, attr: str | None) -> str | None:
        if mod in spec:
            return place.get(f"{mod}.{attr}") if attr else None
        return mod

    graph: dict[str, set[str]] = collections.defaultdict(set)
    for p in sorted(STORE.rglob("*.py")):
        rel = p.relative_to(STORE)
        top = rel.parts[0] if len(rel.parts) > 1 else p.stem
        src_pkg = top if top in spec else None
        if p.stem in absorbed:
            src_pkg = next(k for k, v in EXTRA_SOURCES.items() if p.stem in v)
        tree = ast.parse(p.read_text(encoding="utf-8"))
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                imported |= {a.asname or a.name for a in n.names}
            elif isinstance(n, ast.Import):
                imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
        names = toplevel_names(tree)
        for fname, node in names.items():
            home = (place.get(f"{src_pkg}.{fname}") if src_pkg else
                    (p.stem if len(rel.parts) == 1 else f"{top}.{p.stem}"))
            if home is None:
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name)
                        and sub.value.id in imported):
                    dest = node_for(sub.value.id, sub.attr)
                    if dest and dest != home:
                        graph[home].add(dest)
                elif isinstance(sub, ast.Name) and src_pkg and sub.id in names:
                    dest = place.get(f"{src_pkg}.{sub.id}")
                    if dest and dest != home:
                        graph[home].add(dest)
                elif isinstance(sub, ast.Name) and sub.id in imported:
                    # A bare module reference -- `for mod in (appearances,
                    # audit, changes, chronicle, ...)` in scene_refs.py:21 --
                    # is a real edge that attribute-only walking missed. The
                    # attribute is reached through the loop variable, so
                    # resolve using the attributes taken on locals in this
                    # same function; edging to every file in the package
                    # instead over-approximates into one useless SCC.
                    if sub.id in spec:
                        for attr in _local_attrs(node, imported):
                            dest = place.get(f"{sub.id}.{attr}")
                            if dest and dest != home:
                                graph[home].add(dest)
                    elif sub.id != home and (STORE / f"{sub.id}.py").exists():
                        graph[home].add(sub.id)
    cycles = sccs(graph)
    for c in cycles:
        # Members of the SCC, not a path -- joining them with arrows implied an
        # edge order Tarjan never computed.
        print(f"  CYCLE among: {', '.join(c)}")
        for a in c:
            for b in sorted(graph.get(a, ())):
                if b in c:
                    print(f"      {a} -> {b}")
    if not cycles:
        print("[OK    ] post-refactor store graph is acyclic across packages")
    return len(cycles)


PLAN = pathlib.Path("docs/superpowers/plans/2026-07-30-store-import-layering.md")


def plan_agrees(spec: dict[str, dict[str, str]]) -> int:
    """Every `<pkg>/<file>.py: \`name\`` claim in the plan matches the spec.

    Corrections that landed in the spec but not in a task interface were the
    single largest defect source across review rounds six to nine --
    world_root_of, _lock, repoint_scenes, RESPONSE_FIELDS. Prose review kept
    missing them; this does not.
    """
    if not PLAN.exists():
        print(f"  !! no plan at {PLAN}")
        return 1
    bad = 0
    for m in re.finditer(r"`([a-z_]+)/([a-z_]+)\.py`:([^\n]*)", PLAN.read_text(encoding="utf-8")):
        pkg, fname, rest = m.group(1), m.group(2), m.group(3)
        if pkg not in spec:
            continue
        # Only the backticked list before the em dash. Everything after it is
        # explanatory prose that legitimately names functions living elsewhere
        # -- reading it as placement produced five false positives.
        rest = rest.split("—")[0]
        for name in re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)", rest):
            want = spec[pkg].get(name)
            if want is not None and want != fname:
                bad += 1
                print(f"  !! plan puts {pkg}.{name} in {fname}.py, spec says {want}.py")
    if not bad:
        print("[OK    ] plan interfaces agree with the spec's placement tables")
    return bad


def main() -> int:
    spec = parse_spec()
    if len(spec) != len(SECTIONS):
        # A renamed or deleted heading silently removed a whole package from
        # the check; printing a warning and exiting 0 let Task 0 report a clean
        # partition for something that was never examined.
        print(f"  !! parsed {len(spec)} of {len(SECTIONS)} spec sections -- "
              f"missing: {', '.join(sorted(set(SECTIONS) - set(spec)))}")
        return 1
    if "--imports" in sys.argv:
        return consumer_imports(spec)
    if "--plan" in sys.argv:
        return plan_agrees(spec)
    if "--graph" in sys.argv:
        return store_graph(spec)
    bad = 0
    for mod, mapping in spec.items():
        sources = [mod] + EXTRA_SOURCES.get(mod, [])
        owner: dict[str, str] = {}
        trees = {}
        unreadable = False
        for src in sources:
            p = STORE / f"{src}.py"
            if not p.exists():
                if (STORE / src / "__init__.py").exists():
                    continue      # already split; the partition is done
                print(f"  !! missing source {p} and no {src}/ package -- "
                      f"cannot validate this partition")
                bad += 1
                unreadable = True
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
        # Never print OK for a partition nothing was read for -- that is the
        # same "status means less than it claims" failure this tool exists to
        # stop.
        ok = not cycles and not unassigned and not unreadable
        status = ("UNREAD" if unreadable else
                  "OK    " if ok else ("CYCLE " if cycles else "GAPS  "))
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
