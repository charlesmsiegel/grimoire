"""What went wrong, aggregated per module (#156).

`LLMError.kind` (`missing_key|auth|rate_limit|network|bad_response|
missing_dependency|timeout`) is the only structured failure taxonomy the app
has, and until now it was never *kept*: it was relayed to the browser as one
SSE frame or one HTTP status and then gone. So "the provider has been rate-
limiting me all evening" and "one dossier has failed silently for a week" were
both unanswerable -- not because the information was hard to get, but because
nothing wrote it down.

**Errors live once.** #156 asks explicitly whether they should be their own
store or a view over #155's log, and this is the answer: one writer
(`store.logs`), one file, and this module is a *reader* over its ERROR rows.
Two stores would need the same row written twice, and two appends are two
things that can disagree -- the second one failing leaves a log that saw an
error the error store never did, and the reconciliation for that is a feature
nobody wants to own. Aggregation is cheap enough on read (`store.usage` makes
the same argument for its rollups) that the second file buys nothing.

`record` exists anyway, rather than leaving call sites to write
`logs.record("error", ...)` by hand, because an error row has a *shape*: a
module, a kind from a small vocabulary, and a detail. Spelling that at twenty
call sites is how half of them end up with no `kind` and drop out of the
per-kind counts -- which is #156's actual complaint about the code as it
stands, one level up.

The `kind` vocabulary is deliberately **not validated here**, and not imported
either. The store does not import the gateway (#239, the same rule
`store.usage` follows), so this module cannot see `llm_errors.KINDS`; and this
runs on the failure path, where raising over an unrecognized kind would replace
the error a caller needs to report with one about our own bookkeeping. An
unknown kind is simply a bucket of its own in the rollup, which is how a
non-LLM failure gets counted at all -- those have no kind but their exception's
class name.
"""

from __future__ import annotations

from . import logs

#: Rows one read hands back. Errors are rare, and a page of the most recent
#: hundred alongside counts over the whole window is what the view needs; the
#: counts are computed over every row either way (`logs.scan`).
DEFAULT_ROWS = 100
MAX_ROWS = 1000

#: The default window, matching the usage rollup's so the two halves of the
#: stats page describe the same span unless asked otherwise.
DEFAULT_DAYS = 30


def record(module: str, kind: str, detail: str, *, campaign: str = "",
           scene: str = "", task: str = "", trace: str = "") -> dict | None:
    """Record one failure. Returns the row, or None if nothing was written.

    **Never raises** -- it delegates to `logs.record`, whose whole contract is
    that it cannot fail the operation it is describing, and every call site
    here is inside an `except` block that has something better to do.

    ``module`` is the subsystem a *reader* would name, not necessarily a python
    module: `dossier`, `absorb`, `voice_drift`, `llm`. That is the axis #156
    aggregates on, and the axis a user thinks in ("dossiers keep failing"),
    which a file path is not.
    """
    return logs.record("error", module, detail or kind, kind=kind, campaign=campaign,
                       scene=scene, task=task, trace=trace)


def summary(days: int = DEFAULT_DAYS, *, module: str = "", campaign: str = "",
            rows: int = DEFAULT_ROWS) -> dict:
    """Errors over a window: the recent ones, and what they add up to.

    Counted over every row in the window and *listed* only up to ``rows`` --
    the split `logs.read` makes for the same reason. A rollup that only saw one
    page would report the smallest number exactly when the real one mattered.

    Three groupings, because they answer three different questions: `modules`
    is "which part of the app is unhealthy", `kinds` is "is this me or the
    provider" (a wall of `rate_limit` is a different evening from a wall of
    `bad_response`), and `daily` is "is it getting worse" -- the trend half
    #154 asks for, over the same rows rather than a second pass.

    Each module carries its own kind breakdown as well as a total, since the
    two flat lists cannot answer "which kind is failing in *that* module": a
    library with `rate_limit` in `llm` and `bad_response` in `dossier` produces
    exactly the same two top-level lists as one with them the other way round.
    """
    since, until = logs.window(days)
    by_module: dict[str, dict] = {}
    by_kind: dict[str, int] = {}
    daily: dict[str, int] = {}
    recent: list[dict] = []
    cap = max(1, min(int(rows or DEFAULT_ROWS), MAX_ROWS))
    total = 0
    for row in logs.scan(level="error", module=module, campaign=campaign,
                         since=since, until=until):
        total += 1
        name = str(row.get("module") or "app")
        kind = str(row.get("kind") or "unspecified")
        bucket = by_module.setdefault(name, {"module": name, "count": 0, "kinds": {},
                                             "last": "", "last_detail": ""})
        bucket["count"] += 1
        bucket["kinds"][kind] = bucket["kinds"].get(kind, 0) + 1
        ts = str(row.get("ts", ""))
        # Rows arrive oldest first, so the last one to touch a bucket is its
        # most recent -- but only if the file is in order, and a store synced
        # between two machines with different clocks is not guaranteed to be.
        if ts >= bucket["last"]:
            bucket["last"], bucket["last_detail"] = ts, str(row.get("message", ""))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        daily[ts[:10]] = daily.get(ts[:10], 0) + 1
        recent.append(row)
        if len(recent) > cap:
            # Drop from the FRONT: `scan` yields oldest first, and the page is
            # the newest `cap`. Trimming as we go keeps a pathological window
            # from being materialized whole.
            recent.pop(0)
    return {
        "since": since, "until": until, "days": _span(days),
        "total": total,
        "modules": sorted(
            ({**b, "kinds": _ranked(b["kinds"])} for b in by_module.values()),
            key=lambda b: (-b["count"], b["module"])),
        "kinds": _ranked(by_kind),
        "daily": [{"day": day, "count": count} for day, count in sorted(daily.items())],
        "rows": list(reversed(recent)),   # newest first, like every other log view
        "truncated": total > len(recent),
    }


def _span(days: int) -> int:
    """The window actually read, after `logs.window`'s clamp -- so a caller
    that asked for 100000 days is told it got 366, rather than being echoed
    its own number back."""
    return max(1, min(int(days or 1), logs.MAX_DAYS))


def _ranked(counts: dict[str, int]) -> list[dict]:
    """A count map as a list, biggest first, ties broken by name so the order
    is stable between two reads of the same window."""
    return [{"kind": kind, "count": count}
            for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
