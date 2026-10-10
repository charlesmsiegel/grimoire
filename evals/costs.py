"""What an eval cost, in the ledger's own vocabulary (spec 01a, §6-§7).

Two halves, kept apart:

- **Folding** is the production function's. A bucket is built with
  `usage._ZERO`, `usage._add` and `usage._rounded`, reached through the
  module on the same documented reasoning as `store/usage_rollup.py`: a
  private copy of `_add` would be a second opinion on what a call cost. Every
  rule comes with it -- the cache pair stays out of `total_tokens`, a billed
  price on a subscription provider stays `cost_usd`, a native decision is
  never modelled. Beside `_add`, never inside it, two eval-only tallies say
  how many calls reported each token count (`COVERAGE`): `_add` floors an
  absent count to 0, and a report that summed those zeros would print a
  count nobody reported.
- **Rendering** mirrors `frontend/src/components/cost.tsx`'s rules for
  console text: the three money columns each under their own label and never
  added together, a column with no calls omitted, and **a price nobody
  reported is never rendered as zero** -- nor a token count nobody counted.
"""

from __future__ import annotations

from collections.abc import Iterable

from grimoire.store import routing, usage

#: What a bucket, a side or a cost reads as when nothing reported it --
#: `cost.tsx`'s `UNPRICED`.
UNPRICED = "not reported"

#: The eval-only coverage tallies `fold` keeps beside `_add`: how many calls
#: reported a prompt count, and a completion count (an embed row's absent
#: completion is the structural zero `usage._completion_count` reads).
COVERAGE = ("prompt_counted_calls", "completion_counted_calls")

#: The three money columns, each with its label and its estimate mark. Never
#: added together, anywhere.
COLUMNS = (("cost_usd", "billed", ""),
           ("estimated_usd", "sub-equiv", "~"),
           ("modelled_usd", "modelled", "~"))

#: The `hop` a row with none aggregates under: every call of the unchanged
#: chain.
NO_HOP = "-"


def money(usd: float) -> str:
    """A dollar figure at the precision it is worth reading at, as
    `cost.tsx`'s `money`: two decimals at or above a cent (and for a real
    zero), four at or above $0.0001, else `<$0.0001`."""
    if usd >= 0.01 or usd == 0:
        return f"${usd:,.2f}"
    return f"${usd:.4f}" if usd >= 0.0001 else "<$0.0001"


def billed_calls(bucket: dict) -> int:
    """The calls in the billed column: priced, and not at a subscription's
    equivalent price (`cost.tsx`'s `billedAny`). The bucket keeps no count of
    its own for them."""
    return int(bucket.get("priced_calls", 0)) - int(bucket.get("subscription_calls", 0))


def _column_calls(bucket: dict, key: str) -> int:
    if key == "cost_usd":
        return billed_calls(bucket)
    return int(bucket.get("subscription_calls" if key == "estimated_usd"
                          else "modelled_calls", 0))


def column(bucket: dict, key: str) -> str | None:
    """One money column as `<label> <figure>`, or None when no call is in it:
    an empty column is omitted, never printed as `$0.00`. A zero in a column
    with calls is a real price somebody stated (a free model, a billed 0)."""
    label, mark = next((label, mark) for name, label, mark in COLUMNS if name == key)
    if _column_calls(bucket, key) <= 0:
        return None
    return f"{label} {mark}{money(float(bucket.get(key, 0.0)))}"


def cost_text(bucket: dict) -> str:
    """The bucket's money: each column present, then what is missing from it.
    Nothing priced at all is `cost: not reported`."""
    parts = [text for key, _label, _mark in COLUMNS
             if (text := column(bucket, key)) is not None]
    unpriced = int(bucket.get("unpriced_calls", 0))
    if not parts:
        parts = [f"cost: {UNPRICED}"]
    elif unpriced:
        parts.append(f"incomplete: {unpriced} unpriced")
    if unmetered := int(bucket.get("unmetered_calls", 0)):
        parts.append(f"{unmetered} had no token counts")
    if native := int(bucket.get("unpriced_native_calls", 0)):
        parts.append(f"{native} native, no rate applies")
    return "  ".join(parts)


def _side(total: int, counted: int, calls: int) -> str:
    if counted <= 0:
        return UNPRICED
    text = f"{total:,}"
    return text if counted >= calls else f"{text} ({counted} of {calls} counted)"


def tokens_text(bucket: dict) -> str:
    """`tokens <in> / <out>`, each side from the coverage tallies: a side no
    call counted is `not reported`, a partly counted one says how many were,
    and a bucket where neither side was counted is `tokens: not reported`.
    Never a `0` for an absent count."""
    calls = int(bucket.get("calls", 0))
    prompt, completion = (int(bucket.get(key, 0)) for key in COVERAGE)
    if not prompt and not completion:
        return f"tokens: {UNPRICED}"
    text = (f"tokens {_side(int(bucket.get('prompt_tokens', 0)), prompt, calls)}"
            f" / {_side(int(bucket.get('completion_tokens', 0)), completion, calls)}")
    if estimated := int(bucket.get("estimated_token_calls", 0)):
        text += f" ({estimated} estimated)"
    return text


def bucket_line(bucket: dict) -> str:
    """A bucket on one line: its calls, tokens and money."""
    return f"calls {int(bucket.get('calls', 0))}  {tokens_text(bucket)}  {cost_text(bucket)}"


def _empty() -> dict:
    return {**usage._ZERO, **dict.fromkeys(COVERAGE, 0)}


def _fold_into(bucket: dict, row: dict, rates: usage.Rates | None) -> None:
    """One row into `bucket` by the production `_add`, and the coverage
    tallies beside it."""
    usage._add(bucket, row, rates)
    if usage._count(row.get("prompt_tokens")) is not None:
        bucket["prompt_counted_calls"] += 1
    if usage._completion_count(row) is not None:
        bucket["completion_counted_calls"] += 1


def fold(rows: Iterable[dict], rates: usage.Rates | None = None) -> dict:
    """`rows` as one rounded bucket (`usage._rounded`) with the coverage
    tallies. `rates` models what no provider priced; None models nothing."""
    bucket = _empty()
    for row in rows:
        _fold_into(bucket, row, rates)
    return usage._rounded(bucket)


def merge(buckets: Iterable[dict]) -> dict:
    """Several folded buckets added together (repeats of a case, the cases of
    a config). Adds buckets, never rows: every numeric key is summed, the lazy
    counts `_add` keeps and the coverage tallies included, and the money is
    re-rounded the way a bucket is. Each column stays its own."""
    total = _empty()
    for bucket in buckets:
        for key, value in bucket.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                total[key] = total.get(key, 0) + value
    return usage._rounded(total)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def route_of(row: dict) -> str:
    """The route a row's task runs on (`routing.route(task).key`): `embed`
    for an embed task, and the task itself for one no route claims, so an
    unexpected row never breaks the aggregate."""
    task = _text(row.get("task")) or "unknown"
    if task in routing.EMBED_TASKS:
        return "embed"
    found = routing.route(task)
    return found.key if found is not None else task


def backend_of(row: dict) -> str:
    """A decide row's `decision_mode`, else the row's operation (a row with
    none is a generation, as every row before the field was)."""
    operation = _text(row.get("operation")) or "generate"
    mode = _text(row.get("decision_mode"))
    return mode if operation == "decide" and mode else operation


def hop_of(row: dict) -> str:
    """The row's `hop` (`escalation` for a call an escalation hop sent), or
    `NO_HOP` for a row with none -- never inferred from its stage."""
    return _text(row.get("hop")) or NO_HOP


def by_task(rows: Iterable[dict], rates: usage.Rates | None = None) -> dict[str, dict]:
    """One bucket per task, in the order each task first appears."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(_text(row.get("task")) or "unknown", []).append(row)
    return {task: fold(group, rates) for task, group in grouped.items()}


def aggregate(cases: Iterable[tuple[Iterable[dict], usage.Rates | None]]) -> list[dict]:
    """Every case's rows keyed by route, backend and hop, each row folded
    with its own case's rates: a list of `{"route", "backend", "hop",
    "bucket"}`, sorted by key, so no name can collide through a separator."""
    buckets: dict[tuple[str, str, str], dict] = {}
    for rows, rates in cases:
        for row in rows:
            key = (route_of(row), backend_of(row), hop_of(row))
            _fold_into(buckets.setdefault(key, _empty()), row, rates)
    return [{"route": route, "backend": backend, "hop": hop,
             "bucket": usage._rounded(bucket)}
            for (route, backend, hop), bucket in sorted(buckets.items())]


def seconds(ms: float) -> str:
    """Milliseconds as seconds, two decimals."""
    return f"{ms / 1000:.2f}s"
