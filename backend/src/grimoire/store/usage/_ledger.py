"""Append-only monthly call and scene-rename ledger with bounded window reads."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from .. import atomic, paths
from . import _clock, _rows


def ledger_dir() -> Path:
    return paths.home() / "usage"


#: A well-formed ``YYYY-MM`` prefix, which is the whole of a ledger filename.
#: The check is not paranoia about this module's own ``_now()``: ``ts`` is a
#: parameter of a facade-exported function, so it is a value that names a file,
#: and every id-to-path resolver in this store is required to prove it cannot
#: name anything but a direct child (``paths.safe_id``). Two digits and a dash
#: in a fixed shape is the cheapest possible version of that proof.
_MONTH = re.compile(r"\d{4}-\d{2}$")


def month_path(ts: str) -> Path:
    """The ledger file a row stamped ``ts`` belongs in.

    Slices the timestamp rather than parsing it: the stamp is always this
    module's own ``YYYY-MM-DDTHH:MM:SSZ``, and a full parse would only add a
    way to fail. A prefix that is not a month falls back to the current one
    rather than opening a path of its own -- a misfiled row beats a filename
    built out of whatever was handed in.
    """
    month = ts[:7]
    return ledger_dir() / f"{month if _MONTH.match(month) else _clock._now()[:7]}.jsonl"


def record(*, task: str, kind: str = _rows.KIND_LLM, campaign: str = "", scene: str = "",
           model: str = "", connection: str = "", provider: str = "",
           prompt_tokens: int | None = None, completion_tokens: int | None = None,
           cache_read_tokens: int | None = None, cache_write_tokens: int | None = None,
           cost_usd: float | None = None, cost_basis: str = "",
           duration_ms: int = 0, status: str = "ok", error: str = "",
           attempts: int = 1, post: int | None = None,
           ts: str | None = None, round_id: str = "",
           response_id: str = "", images: int = 0, operation: str = "",
           provider_id: str = "", requested_model: str = "", role: str = "",
           preset: str = "", billing: str = "", decision_mode: str = "",
           hop: str = "", tokens_estimated: bool = False, run_id: str = "",
           loop_turn: int | None = None, tool_calls: int = 0) -> dict | None:
    """Append one call to the ledger. Returns the row, or None if nothing was
    written.

    `operation` is what the call did -- `"embed"` for an embeddings request
    (slice D, the first to write it; spec 9.3) -- and is omitted when empty,
    like the other identity fields: a row with none is a generation, which is
    what every row written before it was.

    **Never raises.** This runs on the generating path, inside the SSE
    finalizers, and a bookkeeping row that can fail a turn is a worse bug than
    the one it exists to diagnose — the judgement ``prompt_log.record`` already
    makes, for the same reason. A full disk, a read-only store, a home that
    cannot be created, or a field holding something that will not serialize:
    each costs the row and nothing else.

    Takes no lock, unlike every other writer here. There is nothing to
    serialize: the write is a single ``O_APPEND`` line (see
    ``atomic.append_line``), never a read-modify-write, so concurrent callers
    interleave rows instead of losing them — and taking the campaign lock would
    both stall a turn and exclude the calls that have no campaign to lock.

    What served the call (spec 9.1-9.3), each written only when it has a
    value, so an older build reads the row exactly as it did: ``operation``
    (``generate``, ``embed``, ``decide``, ...), ``provider_id`` (the provider's store id;
    ``provider`` stays the adapter kind it has always been), ``requested_model``
    (the model asked for, only when the answer named another -- a dated
    snapshot -- because a model's rates are stated under what was asked),
    ``role`` (the slot that supplied the resolution; none for a pin, or for
    an override that changed the provider or the model), ``preset`` (the sampler preset sent), ``billing`` (``metered``
    or ``subscription``: a label, which moves no figure between columns),
    ``decision_mode``, ``hop`` (``escalation`` on a call an escalation hop
    sent, roadmap 01d §5.7), and ``tokens_estimated`` (true only when a count
    was estimated locally rather than reported).

    Which run of a caller the call belonged to (01g-C3), under the same
    "written only when set" rule: ``run_id`` (the caller's id, normally a
    detached run's), ``loop_turn`` (the model turn of that run, from 1) and
    ``tool_calls`` (how many tool calls the turn asked for). Each is
    type-tested rather than coerced (`_run_fields`), so a value that is not
    one costs that field and never the row. No rollup reads them.
    """
    ts = ts or _clock._now()
    # The WHOLE body is inside the guard, the row's construction included. The
    # coercions can raise exactly as the write can -- `int()` on a value that is
    # not a number is a ValueError -- and this function's contract is that
    # nothing it does can fail the turn it is accounting for. Building the row
    # above the `try` left precisely that hole.
    try:
        # Annotated, so the numeric fields below are not checked against a
        # `dict[str, str]` inferred from these three string values -- a row is
        # mixed by construction, and the alternative is five type: ignores.
        row: dict = {"ts": ts, "kind": kind, "task": task}
        # Optional identity fields are omitted rather than written empty: a
        # tagline has no campaign and no scene, and `"campaign": ""` in the file
        # reads as a campaign whose id is the empty string.
        for key, value in (("campaign", campaign), ("scene", scene), ("model", model),
                           ("connection", connection), ("provider", provider),
                           ("round_id", round_id), ("response_id", response_id),
                           ("operation", operation), ("provider_id", provider_id),
                           ("requested_model", _differs(requested_model, model)),
                           ("role", role), ("preset", preset), ("billing", billing),
                           ("decision_mode", decision_mode), ("hop", hop)):
            if value:
                row[key] = value
        row.update(_estimated(tokens_estimated))
        row.update(_run_fields(run_id, loop_turn, tool_calls))
        # Absent, not zero, when the provider counted nothing -- the same rule
        # the price gets below, and for the same reason. A row saying zero
        # tokens is a row saying the call used none, which is a claim no
        # `openai_compatible` endpoint has made; `0` stays available for the
        # empty reply that genuinely completed none. A rollup adds an absent
        # count as zero either way, so what this buys is a file that can still
        # be re-read honestly later.
        # The cache pair sits under the same rule, and carries one more of its
        # own: both are slices OF `prompt_tokens` rather than counts beside it
        # (#148), so a reader adding them to a total would count a cached prefix
        # twice. `_add` is where that promise is kept for rollups.
        # `images` is how many post images the call sent (#377), absent when
        # none. A billed figure already includes what the provider charged for
        # them, and a MODELLED one prices them only as far as the provider
        # counted them into `prompt_tokens` -- which is how image input is
        # billed and reported by the providers that report usage at all, but
        # nothing here can check. So this is what tells a reader a turn carried
        # images, not a correction to any figure.
        for key, count in (("prompt_tokens", prompt_tokens),
                           ("completion_tokens", completion_tokens),
                           ("cache_read_tokens", cache_read_tokens),
                           ("cache_write_tokens", cache_write_tokens),
                           ("images", images or None)):
            if count is not None:
                row[key] = int(count)
        if cost_usd is not None:
            # The literal rather than `llm_usage.BILLED`: the store does not
            # import the gateway (#239), and one default spelled in two modules
            # is a cheaper price than that rule.
            row["cost_usd"] = float(cost_usd)
            row["cost_basis"] = cost_basis or "billed"
        # Which player post this generation was answering (#153): the index of
        # the last user-role message in the transcript when the turn was
        # claimed. Only the turn-producing routes pass one -- an absorb or a
        # rolling summary belongs to the scene, not to a post -- so an absent
        # field means "not attributable", never "post 0".
        #
        # An INDEX, and so only as stable as indices are: a cut or a retcon
        # renumbers what follows it, and the ledger is append-only and cannot
        # follow. The rows either dangle past the end of a shortened transcript
        # (invisible, which is the honest outcome for a turn that no longer
        # exists) or, after a retcon that removed a middle post, name the post
        # that took the index. That is the same trade `KIND_RENAME` makes for
        # scene ids, without a rename row to make it recoverable -- so the
        # per-post figure is documented as what the scene's OWN totals are not:
        # a breakdown, correct while the transcript only grows.
        # Tested rather than coerced: `int("x")` raises, and this whole body is
        # inside the guard that keeps a bookkeeping failure from costing a turn
        # -- so an `int()` here would answer a bad index by dropping the entire
        # row. A field that will not serialize is worth less than the call.
        if isinstance(post, int) and not isinstance(post, bool) and post >= 0:
            row["post"] = post
        row["duration_ms"] = int(duration_ms or 0)
        row["status"] = status
        if error:
            row["error"] = error
        if attempts and attempts != 1:
            row["attempts"] = int(attempts)
        return _append(row, ts)
    except (OSError, TypeError, ValueError):
        # All three, and each is a real escape from a function whose whole
        # contract is that it cannot fail a turn. OSError is the write. A field
        # holding something unserializable -- an object where a model name
        # belongs -- raises TypeError from `json.dumps`, not ValueError, which
        # is the sort of thing only a test finds. ValueError covers `allow_nan`
        # and every `int()`/`float()` above it.
        return None


def _differs(requested_model: str, model: str) -> str:
    """`requested_model`, or "" when it is the model that answered: the field
    is only worth a row's bytes when a provider named something else."""
    return requested_model if requested_model != model else ""


def _estimated(flag: bool) -> dict:
    """`tokens_estimated: true` when a count was estimated, else nothing: an
    absent flag is every reported count, and every older row."""
    return {"tokens_estimated": True} if flag is True else {}


def _positive(value: object) -> int | None:
    """`value` when it is an `int` of at least 1 (never a `bool`), else None."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _run_fields(run_id: object, loop_turn: object, tool_calls: object) -> dict:
    """The run attribution a row carries (01g-C3): each field only when it is
    what it says -- a non-empty `str` run id, a `loop_turn` and a
    `tool_calls` of at least 1 -- so a row with none of them is the row an
    older build wrote, and a malformed one costs its field, not the row."""
    fields: dict = {}
    if isinstance(run_id, str) and run_id:
        fields["run_id"] = run_id
    if (turn := _positive(loop_turn)) is not None:
        fields["loop_turn"] = turn
    if (calls := _positive(tool_calls)) is not None:
        fields["tool_calls"] = calls
    return fields


def _append(row: dict, ts: str) -> dict:
    """Write one row to its month file. Raises -- every caller is inside the
    guard `record` documents, and sharing this is what keeps a second writer
    from quietly acquiring different failure semantics."""
    path = month_path(ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    # `allow_nan=False` is the load-bearing argument. The default writes an
    # infinite float as the bare token `Infinity` and reads it back happily,
    # so a Python-only round trip never notices -- while every other JSON
    # reader rejects the line, and a ledger nothing else can parse is not a
    # ledger. Refusing at the encoder costs one row instead of a month.
    atomic.append_line(path, json.dumps(row, allow_nan=False))
    return row


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Record that scene ids were renamed, so the per-scene view can follow.

    `store.scene_refs.repoint`'s sixteenth store, and the only one that does not
    rewrite what it holds -- see `KIND_RENAME` for why an append-only ledger
    must not, and `_aliases` for how the read side follows the trail instead.

    Never raises, like `record`: this runs inside a rename that has already
    moved the scene file, and failing it would leave the campaign half-renamed
    over a bookkeeping row. A lost trail costs the old turns' cost history, not
    the scene.

    Takes no lock, for `record`'s reason: each line is a single `O_APPEND`
    write, never a read-modify-write.
    """
    ts = _clock._now()
    for old, new in mapping.items():
        if not (isinstance(old, str) and isinstance(new, str)) or old == new:
            continue
        try:
            _append({"ts": ts, "kind": _rows.KIND_RENAME, "campaign": cid,
                     "scene": new, "was": old}, ts)
        except (OSError, TypeError, ValueError):
            continue


# ---- rollups ----
def calls(days: int = 30, campaign: str = "", *,
          since: str = "", until: str = ""):
    """Every generation in the last ``days``, oldest first.

    The public half of `_read_rows`, for a reader that wants the rows rather
    than a rollup -- `store.metrics` needs each call's own `duration_ms` to
    take a percentile of, and a bucket's summed duration cannot be
    un-averaged back into a distribution.

    Same window arithmetic and the same clamp `summary` uses, so "the last 30
    days" means the same span on both halves of the stats page; and the same
    `_is_call` filter, so a rename row cannot enter a latency distribution as a
    call that took no time.

    ``since``/``until`` name the window outright, for a caller that reads more
    than one store into a single report. `store.metrics` does: it takes
    latencies from this ledger and errors from `store.errors`, and each
    resolving "the last 30 days" from its own clock meant a report generated
    across UTC midnight could quote a p90 from one window beside an error count
    from another -- silently, and only ever for the person awake at midnight.
    The caller resolves the pair once and hands the same one to both.
    """
    days = max(1, min(int(days), _clock.MAX_DAYS))
    if not (since and until):
        until = _clock._today()
        since = (date.fromisoformat(until) - timedelta(days=days - 1)).isoformat()
    for row in _read_rows(since, until, campaign):
        if _is_call(row):
            yield row


def _read_rows(since: str, until: str, campaign: str = "", kind: str = "",
               *, strict: bool = False):
    """Every well-formed row stamped in ``[since, until]``, oldest file first.

    Tolerant by design, in both directions. A file that cannot be read is
    skipped -- a rollup is a report, and refusing to draw it because one month
    is locked by a sync client is worse than drawing it short. A line that will
    not parse is skipped too: `atomic.append_line` documents the one way a torn
    line can be produced, and a hand-edited or half-synced file produces the
    rest.

    ``campaign`` and ``kind``, when given, keep only the rows whose field is
    exactly that -- and both are tested against the line's TEXT before it is
    parsed. That is where a scoped rollup's time went: the ledger is
    home-scoped, so asking what one campaign cost used to `json.loads` every
    campaign's rows and discard nearly all of them, which made the per-scene
    panel and the Costs page cost the whole library's traffic.

    The text test is exact, not a heuristic. A JSON string's characters appear
    in a line either literally or as an escape, and every escape starts with a
    backslash -- so a line holding neither the value nor a backslash cannot
    decode to a row carrying that value. `json.dumps` escapes nothing in a
    slug, so in practice only a hand edit takes the slow path. Whatever passes
    is parsed and judged on the decoded field, which is what keeps
    `saltmarch`'s rows out of a read for `salt`.
    """
    for path in _window_files(since, until):
        try:
            # A line at a time, not `read_text().splitlines()`. A year of heavy
            # play is a few tens of megabytes, and slurping a month file holds
            # the whole of it plus a list of every line in memory at once -- for
            # a report that never needs two rows at the same time.
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if campaign and campaign not in line and "\\" not in line:
                        continue
                    if kind and kind not in line and "\\" not in line:
                        continue
                    row = _row(line, since, until)
                    if row is None or (campaign and row.get("campaign") != campaign) \
                            or (kind and row.get("kind") != kind):
                        continue
                    yield row
        except FileNotFoundError:
            continue  # An untouched month has no ledger file yet.
        except (OSError, ValueError) as exc:    # ValueError covers invalid UTF-8
            # Mid-file as well as on open: a decode error surfaces on the read
            # that hits the bad bytes, and a report drawn short beats no report.
            if strict:
                raise OSError(f"cost ledger could not be read: {path.name}") from exc
            continue


def _row(line: str, since: str, until: str) -> dict | None:
    """One ledger line as a row inside the window, or None to skip it."""
    if not line.strip():
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    ts = row.get("ts")
    if not isinstance(ts, str) or not since <= ts[:10] <= until:
        return None
    return row


def _window_files(since: str, until: str) -> list[Path]:
    """The month files a ``[since, until]`` window can touch, in order.

    Derived from the window rather than by globbing the directory, so the read
    cost is the window's size and not the library's age: a 30-day summary opens
    one file or two whether the store holds three months or three hundred.
    """
    root = ledger_dir()
    months, cursor = [], date.fromisoformat(since).replace(day=1)
    end = date.fromisoformat(until).replace(day=1)
    while cursor <= end:
        months.append(root / f"{cursor.strftime('%Y-%m')}.jsonl")
        if cursor == end:
            break
        # First of the next month, without a calendar table: day 28 is in every
        # month, and +4 days from it is always in the next one.
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def _is_call(row: dict) -> bool:
    """True for a row that represents a generation, i.e. everything a rollup is
    allowed to count. Today the only other kind is `KIND_RENAME`, which carries
    a campaign and a scene and would otherwise land in every bucket keyed on
    either -- a free call, in a task named "unknown"."""
    return row.get("kind") != _rows.KIND_RENAME
