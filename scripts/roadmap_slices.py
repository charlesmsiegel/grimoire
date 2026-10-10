"""Check and trace the roadmap's slice graph. Delete with ROADMAP-CHECKLIST.md.

Each roadmap spec (docs/superpowers/specs/2026-10-09-roadmap-*.md) has a
"Slices" section. Each slice names the contract items it delivers, what it
needs at contract level ("Needs (this spec)", "Needs (other specs)") and the
exact slices that resolve those needs ("Needs (slices)"). This script reads
those lines and nothing else.

    python3 scripts/roadmap_slices.py check            # the graph is consistent
    python3 scripts/roadmap_slices.py trace 07-S4      # hard prerequisites, landing order
    python3 scripts/roadmap_slices.py trace 07-S4 --soft   # soft prerequisites too
    python3 scripts/roadmap_slices.py ready            # hard needs all ticked in the checklist

`check` also holds ROADMAP-CHECKLIST.md's "Slice graph" to the specs: every
slice listed once, with the needs its spec gives it.
"""

from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(HERE, "docs", "superpowers", "specs", "2026-10-09-roadmap-*.md")
SLICE_HDR = re.compile(r"^### (\d\d[a-z]?-S\d+)\s*[—:-]+\s*(.*)$")
ID_C = re.compile(r"(\d\d[a-z]?-C\d+[a-z]?)\s*\(")
ID_S = re.compile(r"(\d\d[a-z]?-S\d+)\s*\(")
CHECKLIST = os.path.join(HERE, "ROADMAP-CHECKLIST.md")
LISTED = re.compile(r"^- \[([ x~])\] \*\*(\d\d[a-z]?-S\d+)\*\* .*? ← (.*)$", re.MULTILINE)


def _balanced(rx: re.Pattern[str], text: str) -> list[tuple[str, str]]:
    out = []
    for m in rx.finditer(text):
        i, depth = m.end(), 1
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        out.append((m.group(1), text[m.end():i - 1].strip()))
    return out


def _field(block: str, name: str) -> str:
    m = re.search(r"^- \*\*" + re.escape(name) + r":\*\*(.*?)(?=^- \*\*|^\s*$|\Z)",
                  block, re.DOTALL | re.MULTILINE)
    return " ".join(m.group(1).split()) if m else ""


def load() -> dict[str, dict]:
    slices: dict[str, dict] = {}
    for path in sorted(glob.glob(SPECS)):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        m = re.search(r"^## (?:\d+\. )?Slices\s*\n(.*?)(?=^## )", text, re.DOTALL | re.MULTILINE)
        if not m:
            raise SystemExit(f"no Slices section in {os.path.basename(path)}")
        for part in re.split(r"(?=^### )", m.group(1), flags=re.MULTILINE):
            h = SLICE_HDR.match(part.splitlines()[0]) if part.strip() else None
            if not h:
                continue
            slices[h.group(1)] = {
                "title": h.group(2),
                "file": os.path.basename(path),
                "delivers": _balanced(ID_C, _field(part, "Delivers")),
                "own": _balanced(ID_S, _field(part, "Needs (this spec)")),
                "other": _balanced(ID_C, _field(part, "Needs (other specs)")),
                "needs": [(s, a[:1]) for s, a in
                          _balanced(ID_S, _field(part, "Needs (slices)"))],
                "size": _field(part, "Size"),
            }
    return slices


def _key(sid: str) -> tuple[str, int]:
    spec, n = sid.split("-S")
    return spec, int(n)


def _deliveries(slices: dict[str, dict]) -> tuple[dict[str, set[str]], list[str]]:
    """Map each contract item to the slices delivering any of it; check one is full."""
    full: dict[str, list[str]] = {}
    by_contract: dict[str, set[str]] = {}
    for sid, s in slices.items():
        for c, a in s["delivers"]:
            by_contract.setdefault(c, set()).add(sid)
            if a.lower().startswith("full"):
                full.setdefault(c, []).append(sid)
    problems = [f"{c}: delivered in full by {full.get(c, [])}, want one slice"
                for c in sorted(by_contract) if len(full.get(c, [])) != 1]
    return by_contract, problems


def _slice_problems(sid: str, s: dict, slices: dict[str, dict],
                    by_contract: dict[str, set[str]]) -> list[str]:
    """Every need of one slice is resolved by a 'Needs (slices)' entry."""
    spec = sid.split("-S")[0]
    needs = dict(s["needs"])
    problems = [f"{sid}: needs unknown slice {t}" for t in needs if t not in slices]
    problems += [f"{sid}: {t} is neither (H) nor (S)"
                 for t, hs in needs.items() if hs not in ("H", "S")]
    for r, a in s["own"]:
        if not r.startswith(spec + "-S") or _key(r) >= _key(sid):
            problems.append(f"{sid}: 'Needs (this spec)' names {r}, not an earlier slice of {spec}")
        elif r not in needs:
            problems.append(f"{sid}: needs {r} but 'Needs (slices)' omits it")
        elif a[:1] == "H" and needs[r] != "H":
            problems.append(f"{sid}: hard need {r} marked soft in 'Needs (slices)'")
    for c, a in s["other"]:
        hits = [t for t in needs if t in by_contract.get(c, ())]
        if c not in by_contract:
            problems.append(f"{sid}: needs {c}, which no slice delivers")
        elif not hits:
            problems.append(f"{sid}: needs {c}, but no slice in 'Needs (slices)' delivers it")
        elif a[:1] == "H" and all(needs[t] != "H" for t in hits):
            problems.append(f"{sid}: hard need {c} resolved only by soft slices {hits}")
    return problems


def listed() -> dict[str, tuple[bool, str]]:
    """The checklist's slice lines: slice -> (ticked, arrow text)."""
    with open(CHECKLIST, encoding="utf-8") as f:
        text = f.read()
    out: dict[str, tuple[bool, str]] = {}
    for m in LISTED.finditer(text):
        out[m.group(2)] = (m.group(1) == "x", m.group(3).strip())
    return out


def _arrow(s: dict) -> str:
    return ", ".join(t if h == "H" else f"{t} (S)" for t, h in s["needs"]) or "none"


def _checklist_problems(slices: dict[str, dict]) -> list[str]:
    rows = listed()
    problems = [f"checklist lists {sid}, which no spec has" for sid in rows if sid not in slices]
    for sid, s in slices.items():
        if sid not in rows:
            problems.append(f"checklist does not list {sid}")
        elif rows[sid][1] != _arrow(s):
            problems.append(f"checklist gives {sid} needs '{rows[sid][1]}', spec says '{_arrow(s)}'")
    return problems


def check(slices: dict[str, dict]) -> list[str]:
    by_contract, problems = _deliveries(slices)
    problems += _checklist_problems(slices)
    for sid, s in slices.items():
        problems += _slice_problems(sid, s, slices, by_contract)
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 1:
            problems.append("cycle: " + " -> ".join([*path, n]))
            return
        if state.get(n) == 2 or n not in slices:
            return
        state[n] = 1
        for t, _ in slices[n]["needs"]:
            visit(t, [*path, n])
        state[n] = 2

    for n in slices:
        visit(n, [])
    return problems


def closure(slices: dict[str, dict], roots: list[str], soft: bool) -> list[str]:
    """Prerequisites of `roots` (excluded), in an order that can land."""
    out: list[str] = []
    seen: set[str] = set()

    def visit(n: str) -> None:
        for t, hs in sorted(slices[n]["needs"], key=lambda x: _key(x[0])):
            if (hs == "H" or soft) and t not in seen:
                seen.add(t)
                visit(t)
                out.append(t)

    for r in roots:
        visit(r)
    return [s for s in out if s not in roots]


def main(argv: list[str]) -> int:
    slices = load()
    cmd = argv[1] if len(argv) > 1 else "check"
    if cmd == "check":
        problems = check(slices)
        print("\n".join(problems) or f"ok: {len(slices)} slices, "
              f"{sum(len(s['needs']) for s in slices.values())} edges")
        return 1 if problems else 0
    if cmd == "trace":
        sid = argv[2]
        if sid not in slices:
            raise SystemExit(f"unknown slice {sid}")
        soft = "--soft" in argv
        for t in [*closure(slices, [sid], soft), sid]:
            s = slices[t]
            print(f"{t:8} [{s['size'] or '?'}] {s['title']}")
        return 0
    if cmd == "ready":
        done = {sid for sid, (ticked, _) in listed().items() if ticked}
        for sid in sorted(slices, key=_key):
            if sid not in done and all(t in done for t, hs in slices[sid]["needs"] if hs == "H"):
                print(f"{sid:8} {slices[sid]['title']}")
        return 0
    raise SystemExit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
