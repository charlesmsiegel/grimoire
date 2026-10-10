"""Check, trace and track the roadmap's slices. Delete with ROADMAP-CHECKLIST.md.

Each roadmap spec (docs/superpowers/specs/2026-10-09-roadmap-*.md) has a
"Slices" section. Each slice names the contract items it delivers, what it
needs at contract level ("Needs (this spec)", "Needs (other specs)") and the
exact slices that resolve those needs ("Needs (slices)"). ROADMAP-CHECKLIST.md
tracks each slice through its stages in the "Slice checklist" table.

    python3 scripts/roadmap_slices.py check            # graph and checklist consistent
    python3 scripts/roadmap_slices.py sync             # rewrite the table, keeping ticks
    python3 scripts/roadmap_slices.py trace 07-S4      # hard prerequisites, landing order
    python3 scripts/roadmap_slices.py trace 07-S4 --soft   # soft prerequisites too
    python3 scripts/roadmap_slices.py ready            # not landed, hard needs all landed
    python3 scripts/roadmap_slices.py status           # each spec's slices, per stage

`check` first checks the graph: every contract item in the checklist's
Contracts section delivered in full by exactly one slice, every need
resolved, no slice defined twice or named without its spec, no cycle. Then
it holds the table to the specs (run `sync` after a spec's slices change),
and holds the ticks to two rules: a slice's stages are ticked in
order, and a slice is landed only after every slice it hard-needs. It also
holds the Status table's "Slices" count and "Landed" box to the table.
"""

from __future__ import annotations

import glob
import itertools
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(HERE, "docs", "superpowers", "specs", "2026-10-09-roadmap-*.md")
SLICE_HDR = re.compile(r"^### (\d\d[a-z]?-S\d+)\s*[—:-]+\s*(.*)$")
ID_C = re.compile(r"(\d\d[a-z]?-C\d+[a-z]?)\s*\(")
ID_S = re.compile(r"(\d\d[a-z]?-S\d+)\s*\(")
BARE_S = re.compile(r"(?<![\w-])(S\d+)\s*\(")
CONTRACTS = re.compile(r"^## Contracts\n.*?(?=^## )", re.DOTALL | re.MULTILINE)
CONTRACT_ID = re.compile(r"\*\*(\d\d[a-z]?-C\d+[a-z]?)\*\*")
CHECKLIST = os.path.join(HERE, "ROADMAP-CHECKLIST.md")
SECTION = re.compile(r"^## Slice checklist\n.*?(?=^## )", re.DOTALL | re.MULTILINE)
ROW = re.compile(r"^\| (\d\d[a-z]?-S\d+) \|(.*)\|$", re.MULTILINE)
STATUS_ROW = re.compile(r"^\| (\d\d[a-z]?) \|(.*)\|$", re.MULTILINE)
STAGES = ("Plan", "Plan gate", "Code", "Review", "Final gate", "Landed")
LANDED = len(STAGES) - 1


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
            if h.group(1) in slices:
                where = {slices[h.group(1)]["file"], os.path.basename(path)}
                raise SystemExit(f"{h.group(1)} is defined twice, in {' and '.join(sorted(where))}")
            own = _field(part, "Needs (this spec)")
            slices[h.group(1)] = {
                "title": h.group(2),
                "bare": BARE_S.findall(own),
                "file": os.path.basename(path),
                "delivers": _balanced(ID_C, _field(part, "Delivers")),
                "own": _balanced(ID_S, own),
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
    problems += [f"{sid}: 'Needs (this spec)' names {b} without its spec; write {spec}-{b}"
                 for b in s["bare"]]
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


def _checklist() -> str:
    with open(CHECKLIST, encoding="utf-8") as f:
        return f.read()


def ticks(text: str | None = None) -> dict[str, list[bool]]:
    """The table's stage boxes: slice -> one bool per stage."""
    m = SECTION.search(text if text is not None else _checklist())
    out: dict[str, list[bool]] = {}
    for row in ROW.finditer(m.group(0) if m else ""):
        cells = [c.strip() for c in row.group(2).split("|")]
        out[row.group(1)] = [c == "[x]" for c in cells[-len(STAGES):]]
    return out


def waves(slices: dict[str, dict]) -> dict[str, int]:
    """1 for a slice with no hard needs, else one more than its latest hard need."""
    memo: dict[str, int] = {}

    def wave(n: str) -> int:
        if n not in memo:
            memo[n] = 1 + max((wave(t) for t, hs in slices[n]["needs"] if hs == "H"),
                              default=0)
        return memo[n]

    for n in slices:
        wave(n)
    return memo


def _specs(slices: dict[str, dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for sid in sorted(slices, key=_key):
        out.setdefault(sid.split("-S")[0], []).append(sid)
    return out


def render(slices: dict[str, dict], kept: dict[str, list[bool]]) -> str:
    """The checklist's slice table, with the ticks in `kept`."""
    wave = waves(slices)
    lines = ["## Slice checklist", "", *INTRO, ""]
    for spec, sids in sorted(_specs(slices).items(), key=lambda kv: _key(kv[0] + "-S0")):
        lines += [f"### {spec}", "",
                  "| Slice | Title | Size | Wave | Needs | " + " | ".join(STAGES) + " |",
                  "|---|---|:-:|:-:|---|" + ":-:|" * len(STAGES)]
        for sid in sids:
            s = slices[sid]
            needs = ", ".join(t if h == "H" else f"{t} (S)" for t, h in s["needs"]) or "—"
            boxes = " | ".join("[x]" if b else "[ ]"
                               for b in kept.get(sid, [False] * len(STAGES)))
            lines.append(f"| {sid} | {s['title']} | {s['size']} | {wave[sid]} | {needs} | {boxes} |")
        lines.append("")
    return "\n".join(lines) + "\n"


INTRO = [
    "One row per slice, each roughly one PR. **Needs** lists the slices it",
    "needs directly, marked (S) when soft; `trace` gives the rest. **Wave** is",
    "how deep it sits behind hard needs: wave 1 needs nothing, and a slice can",
    "start once every slice it hard-needs has landed, whatever spec that is in.",
    "",
    "Tick each stage in the PR that completes it, in order: the plan written",
    "(`superpowers:writing-plans`), the plan gate (`/codex:adversarial-review`",
    "on the plan), the code, `/codex:review` on the diff, the final gate (the",
    "diff against the spec), and landed. A slice's plan covers that slice",
    "alone. This table is generated: after a spec's slices change, run",
    "`python3 scripts/roadmap_slices.py sync`, which keeps the ticks.",
]


def _tick_problems(slices: dict[str, dict], boxes: dict[str, list[bool]]) -> list[str]:
    problems = []
    for sid, b in boxes.items():
        if sid not in slices:
            continue
        if any(later and not earlier for earlier, later in itertools.pairwise(b)):
            problems.append(f"{sid}: a stage is ticked before an earlier one")
        if b[LANDED]:
            problems += [f"{sid}: landed before its hard need {t}"
                         for t, hs in slices[sid]["needs"]
                         if hs == "H" and not boxes.get(t, [False] * len(STAGES))[LANDED]]
    return problems


def _status_problems(slices: dict[str, dict], text: str, boxes: dict[str, list[bool]]) -> list[str]:
    """The Status table's Slices count and Landed box follow the slice table."""
    problems = []
    specs = _specs(slices)
    seen = set()
    for m in STATUS_ROW.finditer(text):
        spec, cells = m.group(1), [c.strip() for c in m.group(2).split("|")]
        if spec not in specs:
            continue
        seen.add(spec)
        sids = specs[spec]
        if cells[-2] != str(len(sids)):
            problems.append(f"Status: {spec} gives {cells[-2]} slices, the specs have {len(sids)}")
        landed = all(boxes.get(t, [False])[-1] for t in sids)
        if (cells[-1] == "[x]") != landed:
            problems.append(f"Status: {spec}'s Landed box disagrees with its slices")
    problems += [f"Status: no row for {spec}" for spec in specs if spec not in seen]
    return problems


def _checklist_problems(slices: dict[str, dict]) -> list[str]:
    text = _checklist()
    m = SECTION.search(text)
    if not m:
        return ["checklist has no 'Slice checklist' section"]
    boxes = ticks(text)
    problems = [f"checklist lists {sid}, which no spec has" for sid in boxes if sid not in slices]
    if m.group(0) != render(slices, boxes):
        problems.append("the slice checklist is out of step with the specs: run `sync`")
    return problems + _tick_problems(slices, boxes) + _status_problems(slices, text, boxes)


def _inventory_problems(slices: dict[str, dict], by_contract: dict[str, set[str]]) -> list[str]:
    """Every contract item the checklist declares is delivered, and only those."""
    m = CONTRACTS.search(_checklist())
    declared = set(CONTRACT_ID.findall(m.group(0))) if m else set()
    specs = set(_specs(slices))
    problems = [f"{c}: declared in the checklist's Contracts, delivered by no slice"
                for c in sorted(declared - set(by_contract)) if c.split("-C")[0] in specs]
    problems += [f"{c}: delivered by {sorted(by_contract[c])}, missing from the checklist's Contracts"
                 for c in sorted(set(by_contract) - declared)]
    return problems


def check(slices: dict[str, dict]) -> list[str]:
    """Graph problems first: the checklist is rendered from the graph, so it is
    only compared once the graph is sound."""
    problems = graph_problems(slices)
    return problems or _checklist_problems(slices)


def graph_problems(slices: dict[str, dict]) -> list[str]:
    by_contract, problems = _deliveries(slices)
    problems += _inventory_problems(slices, by_contract)
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


def _check(slices: dict[str, dict], argv: list[str]) -> int:
    problems = check(slices)
    print("\n".join(problems) or f"ok: {len(slices)} slices, "
          f"{sum(len(s['needs']) for s in slices.values())} edges")
    return 1 if problems else 0


def _trace(slices: dict[str, dict], argv: list[str]) -> int:
    sid = argv[2] if len(argv) > 2 else ""
    if sid not in slices:
        raise SystemExit(f"unknown slice {sid!r}")
    for t in [*closure(slices, [sid], "--soft" in argv), sid]:
        s = slices[t]
        print(f"{t:8} [{s['size'] or '?'}] {s['title']}")
    return 0


def _ready(slices: dict[str, dict], argv: list[str]) -> int:
    done = {sid for sid, b in ticks().items() if b[LANDED]}
    wave = waves(slices)
    for sid in sorted(slices, key=_key):
        if sid not in done and all(t in done for t, hs in slices[sid]["needs"] if hs == "H"):
            print(f"{sid:8} wave {wave[sid]}  {slices[sid]['title']}")
    return 0


def _sync(slices: dict[str, dict], argv: list[str]) -> int:
    problems = graph_problems(slices)
    if problems:
        print("not syncing; fix the specs first:\n" + "\n".join(problems))
        return 1
    text = _checklist()
    table = render(slices, ticks(text))
    text = SECTION.sub(lambda _: table, text) if SECTION.search(text) else text + "\n" + table
    with open(CHECKLIST, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {len(slices)} rows")
    return 0


def _status(slices: dict[str, dict], argv: list[str]) -> int:
    boxes = ticks()
    print(f"{'spec':5} {'slices':>6} " + " ".join(f"{st:>10}" for st in STAGES))
    for spec, sids in sorted(_specs(slices).items(), key=lambda kv: _key(kv[0] + "-S0")):
        counts = [sum(boxes.get(t, [False] * len(STAGES))[i] for t in sids)
                  for i in range(len(STAGES))]
        print(f"{spec:5} {len(sids):>6} " + " ".join(f"{c:>10}" for c in counts))
    return 0


COMMANDS = {"check": _check, "trace": _trace, "ready": _ready, "sync": _sync, "status": _status}


def main(argv: list[str]) -> int:
    cmd = COMMANDS.get(argv[1] if len(argv) > 1 else "check")
    if cmd is None:
        raise SystemExit(__doc__)
    return cmd(load(), argv)

if __name__ == "__main__":
    sys.exit(main(sys.argv))
