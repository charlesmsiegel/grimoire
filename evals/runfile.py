"""The `eval-run` file (`--out`) and the comparison table (`--compare`), spec
01a section 8.

A run file holds what a run measured and nothing it read: no prompt, no
reply, no provider error text. Errors are `{"kind", "status"}`, and a check's
`detail` -- a grader may quote model output -- is cut to `MAX_DETAIL_CHARS`.
It does hold the user's provider ids and model names, so it goes where
`--out` says and nowhere by default; the repo never ships one.

`--compare` reads run files and nothing else -- no store, no settings, no
rates -- which is what makes its table readable without real store data. So
every figure it shows was folded when the file was written (each case's
`bucket`, each call's `bucket`), and the table only adds buckets
(`costs.merge`). A cell for a case a config did not run is blank, never
zero.

`configs[].axes` is open: a `{name: value}` dict the table prints and
otherwise treats as opaque, so a later flag adds a configuration by adding an
axis, with no version bump. A v1 reader ignores keys it does not know.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import costs
from .cases import BASELINE

if TYPE_CHECKING:
    from .runner import Result

FORMAT = "eval-run"
VERSION = 1

#: How much of a check's detail the file keeps. Graders quote model output
#: and prompt needles; every fixture is placeholder-only, so this is
#: synthetic text, and it is still cut.
MAX_DETAIL_CHARS = 200

#: An empty cell: a case this config did not run. Never a zero.
BLANK = ""


class RunFileError(ValueError):
    """A file `--compare` cannot read, in one sentence."""


def config(config_id: str, label: str, axes: dict[str, Any]) -> dict:
    """One configuration of a run: its id (`c1`, ...), its label, and its open
    `axes`."""
    return {"id": config_id, "label": label, "axes": dict(axes)}


def _error(kind: str, status: int | None) -> dict | None:
    return {"kind": kind, "status": status} if kind else None


def _stamped(row: dict, run_id: str, case_id: str) -> dict:
    return {**row, "scope": "eval", "eval_run": run_id, "case": case_id}


def _call(record: Any, rates: Any, run_id: str, case_id: str) -> dict:
    """One `decisions.CallRecord` as the file keeps it: its row (the eval
    copy) and that one row folded with the run's rates, so a native item's
    money can be shown without reading rates again. A structured call's
    bucket is the call's, never its items'."""
    row = _stamped(record.row, run_id, case_id) if record.row is not None else None
    return {"stage": record.stage, "mode": record.mode, "items": list(record.items),
            "hop": record.hop, "row": row,
            "error": _error(record.error_kind, record.error_status),
            "bucket": costs.fold([record.row], rates) if record.row is not None else None}


def _case(result: Result, run_id: str) -> dict:
    case = result.case
    return {
        "configs": list(result.configs), "case": case.id, "variant": result.variant,
        "repeat": result.repeat, "task": case.task,
        "operation": "decide" if case.schema is not None else "generate",
        "passed": result.passed,
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail[:MAX_DETAIL_CHARS]}
                   for c in result.checks],
        "wall_ms": result.wall_ms,
        "error": _error(result.error_kind or ("error" if result.error else ""),
                        result.error_status),
        "partial": result.partial,
        "ledger_unreadable": bool(result.ledger_error),
        "items": [dict(item) for item in result.items],
        "calls": [_call(record, result.rates, run_id, case.id) for record in result.calls],
        "rows": list(result.rows or ()),
        "bucket": result.bucket}


def build(results: Sequence[Result], configs: Sequence[dict], *, run_id: str,
          started: str) -> dict:
    """The `eval-run` v1 document for `results`. Every figure is folded here,
    at each case's own rates (`Result.rates`)."""
    costed = [r for r in results if r.rows is not None and not r.ledger_error]
    return {
        "format": FORMAT, "version": VERSION, "run": run_id, "started": started,
        "configs": [dict(c) for c in configs],
        "cases": [_case(r, run_id) for r in results],
        "aggregates": {"by_route_backend_hop": costs.aggregate(
            (r.rows or (), r.rates) for r in costed)}}


def write(path: Path, doc: dict) -> None:
    """`doc` to `path`, its folder made if it is missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def read(path: Path) -> dict:
    """A run file, or `RunFileError` for one this reader does not know."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RunFileError(f"{path}: not a readable run file ({exc})") from exc
    version = doc.get("version") if isinstance(doc, dict) else None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT \
            or type(version) is not int or version != VERSION:
        got = (doc.get("format"), doc.get("version")) if isinstance(doc, dict) else None
        raise RunFileError(f"{path}: not an {FORMAT} v{VERSION} file "
                           f"(format and version {got!r})")
    if not isinstance(doc.get("configs"), list) or not isinstance(doc.get("cases"), list) \
            or not all(isinstance(c, dict) and isinstance(c.get("case"), str)
                       and isinstance(c.get("configs"), list) for c in doc["cases"]) \
            or not all(isinstance(c, dict) for c in doc["configs"]):
        raise RunFileError(f"{path}: an {FORMAT} v{VERSION} file whose configs or "
                           f"cases are malformed")
    return doc


# ---------------------------------------------------------------- compare

#: The bucket fields a figure is read from; a bucket where any of them is
#: not a number (a hand edit) is not costed, rather than read as zero.
_FIGURES = ("calls", "priced_calls", "subscription_calls", "modelled_calls",
            "unpriced_calls", "cost_usd", "estimated_usd", "modelled_usd",
            "prompt_tokens", "completion_tokens", *costs.COVERAGE)


def _costed(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(key, 0), (int, float))
        and not isinstance(value.get(key, 0), bool) for key in _FIGURES)

def _row_key(entry: dict) -> str:
    variant = entry.get("variant", BASELINE)
    return entry["case"] if variant == BASELINE else f"{entry['case']}.{variant}"


def _tokens(bucket: dict) -> str:
    text = costs.tokens_text(bucket)
    if text == f"tokens: {costs.UNPRICED}":
        return "- / -"
    return text.removeprefix("tokens ").replace(costs.UNPRICED, "-")


def _passes(entries: list[dict]) -> str:
    ok = sum(1 for e in entries if e.get("passed"))
    return f"{ok}/{len(entries)} {'ok' if ok == len(entries) else 'FAIL'}"


def _wall(entries: list[dict]) -> str:
    walls = [e["wall_ms"] for e in entries if isinstance(e.get("wall_ms"), int)]
    return costs.seconds(statistics.median(walls)) if walls else "-"


def _escalated(entries: list[dict], index: int | None = None) -> str:
    items = [i for e in entries for i in e.get("items", [])
             if index is None or i.get("index") == index]
    up = sum(1 for i in items if i.get("escalated"))
    return f"escalated {up}/{len(items)}" if up else ""


def _money(buckets: list[dict], missing: int) -> list[str]:
    """Tokens and money summed across repeats, or `-` when nothing was
    costed; `(N not fully costed)` when some repeats could not be."""
    out = (["- / -", "-"] if not buckets
           else [_tokens(merged := costs.merge(buckets)), costs.cost_text(merged)])
    if missing:
        out.append(f"({missing} not fully costed)")
    return out


def _short(entry: dict) -> bool:
    """Whether a live case's cost is not the whole of it: rows read while
    its follow-ups still ran (`partial`), a ledger that could not be read, or
    a bucket a hand edit broke. A replay entry (no bucket, nothing to read)
    is not short; it simply has no cost."""
    bucket = entry.get("bucket")
    return bool(entry.get("partial") or entry.get("ledger_unreadable")
                or (bucket is not None and not _costed(bucket)))


def _case_cell(entries: list[dict]) -> str:
    buckets = [e["bucket"] for e in entries if _costed(e.get("bucket"))]
    parts = [_passes(entries), _wall(entries), _escalated(entries),
             *_money(buckets, sum(1 for e in entries if _short(e)))]
    return "  ".join(p for p in parts if p)


def _item_cell(entries: list[dict], index: int) -> str:
    """An item's cell: the case's passes and wall, and -- an item one native
    call answered -- that call's own figures. A structured chunk's figures
    are never apportioned to its items: `(chunk)`, on the case's row."""
    buckets: list[dict] = []
    chunked = False
    missing = 0
    for entry in entries:
        item = next((i for i in entry.get("items", []) if i.get("index") == index), None)
        call = item.get("call") if item is not None else None
        calls = entry.get("calls", [])
        if not isinstance(call, int) or not 0 <= call < len(calls):
            continue
        if calls[call].get("mode") != "native":
            chunked = True
        elif _costed(calls[call].get("bucket")):
            buckets.append(calls[call]["bucket"])
        else:
            # A native call refused before it was sent filed no row: not
            # costed, and never a chunk.
            missing += 1
    # A repeat a structured chunk carried shows as `(chunk)` beside what the
    # native repeats cost, never in place of it.
    money = ((_money(buckets, missing) if buckets or missing or not chunked else [])
             + (["(chunk)"] if chunked else []))
    parts = [_passes(entries), _wall(entries), _escalated(entries, index), *money]
    return "  ".join(p for p in parts if p)


def _totals(entries: list[dict]) -> str:
    if not entries:
        return BLANK
    buckets = [e["bucket"] for e in entries if _costed(e.get("bucket"))]
    short = sum(1 for e in entries if _short(e))
    text = costs.cost_text(costs.merge(buckets)) if buckets else "-"
    if short:
        text += f"  (incomplete: {short} case(s) not fully costed)"
    return text


def _header(name: str, conf: dict) -> str:
    """A column's header: `<file>:<config id> <label>`, then every axis as
    `name=value` -- two configs that share a label and differ only in an axis
    (a feature switch, an escalation threshold) must not read alike."""
    axes = conf.get("axes")
    shown = (" ".join(f"{key}={value}" for key, value in axes.items())
             if isinstance(axes, dict) else "")
    return " ".join(part for part in (f"{name}:{conf.get('id', '')}",
                                      str(conf.get("label", "")), f"({shown})" if shown else "")
                    if part)


def _columns(docs: Sequence[tuple[str, dict]]) -> list[tuple[str, list[dict]]]:
    """Every config of every file: its header and the case entries it stands
    for. A file named twice gets `#2`, so no two columns merge."""
    seen: dict[str, int] = {}
    out = []
    for stem, doc in docs:
        seen[stem] = seen.get(stem, 0) + 1
        name = stem if seen[stem] == 1 else f"{stem}#{seen[stem]}"
        for conf in doc.get("configs", []):
            cid = conf.get("id", "")
            entries = [e for e in doc.get("cases", []) if cid in e.get("configs", [])]
            out.append((_header(name, conf), entries))
    return out


def _row_keys(columns: Iterable[tuple[str, list[dict]]]) -> list[tuple[str, int | None]]:
    """`(case key, item index)` rows in first-seen order: each case, then for
    a decide case each of its items."""
    keys: dict[tuple[str, int | None], None] = {}
    for _header, entries in columns:
        for entry in entries:
            keys.setdefault((_row_key(entry), None), None)
            for item in entry.get("items", []):
                if isinstance(item.get("index"), int):
                    keys.setdefault((_row_key(entry), item["index"]), None)
    return list(keys)


def compare(docs: Sequence[tuple[str, dict]]) -> str:
    """One table across `docs` (`(file stem, document)`), one column per
    config, built from the files alone."""
    columns = _columns(docs)
    rows: list[list[str]] = [["case.item", *(header for header, _ in columns)]]
    for key, index in _row_keys(columns):
        label = key if index is None else f"{key}.{index}"
        cells = []
        for _header, entries in columns:
            mine = [e for e in entries if _row_key(e) == key]
            if index is not None:
                # Only the runs that had this item: a config whose case ran
                # without it (an older item set) is blank, not a cell for an
                # item nobody evaluated.
                mine = [e for e in mine if any(i.get("index") == index
                                               for i in e.get("items", []))]
            if not mine:
                cells.append(BLANK)
            elif index is None:
                cells.append(_case_cell(mine))
            else:
                cells.append(_item_cell(mine, index))
        rows.append([label, *cells])
    rows.append(["totals", *(_totals(entries) for _header, entries in columns)])
    widths = [max(len(row[n]) for row in rows) for n in range(len(rows[0]))]
    return "\n".join(" | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True))
                     .rstrip() for row in rows)
