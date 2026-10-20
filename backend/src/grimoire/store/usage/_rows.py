"""Accounting row vocabulary, absent-count interpretation and bucket formatting."""

from __future__ import annotations

from ... import decisions

#: The row kinds this ledger holds. ``llm`` is the only one written today;
#: the field exists from the first row so image generation (#159) can share the
#: file rather than needing a second one, and so a reader can filter for the
#: kind it understands instead of assuming every row is a chat completion.
KIND_LLM = "llm"

#: The `operation` an embeddings request's row carries (spec 9.3). A row with
#: none is a generation. The estimator reads it: an embed row's absent
#: completion count is a structural zero (`_completion_count`).
EMBED_OPERATION = "embed"

#: The attribute an exception sets to say "I am not a provider failure".
#:
#: `Meter` cannot tell these apart by type -- the store does not import the
#: routes that define them (#239) -- and it cannot tell them apart by looking
#: either: `BudgetRefused` is an `LLMError` subclass carrying `kind="timeout"`,
#: which is indistinguishable from a provider that really did time out. But the
#: two mean opposite things. A refusal means the call was NEVER ISSUED because
#: the clock was already gone, and `Abandoned` means the person waiting closed
#: the review. Neither is a failure of anything, and recording them would put
#: rows in the #156 error store for work that was deliberately not done -- with
#: the budget one wearing the same kind as a real timeout, which is the worst
#: possible place for a false positive.
#:
#: So the exception declares it, as a class attribute, and the meter treats it
#: exactly as it treats a cancellation. Absent means True: an ordinary
#: exception IS a failure, which is the safe default for anything new.
NOT_A_FAILURE = "llm_call_failed"

#: The facade's private holder keys for a local estimate, restated from
#: `llm_usage` so the ledger needs no gateway import; a test holds the
#: spellings equal. `ESTIMATE_KEY` holds the attempt's prompt and
#: reply text, `ENDED_KEY` marks a stream that ended on its own, and
#: `ESTIMATED` says a count in the holder was estimated. `Meter.done` pops the
#: first two once it has filed, so a meter that outlives its call does not
#: pin a whole prompt.
ESTIMATE_KEY = "_estimate"
ENDED_KEY = "_ended"
ESTIMATED = "tokens_estimated"

#: The run a meter's holder belongs to (01g-C3), restated from
#: `llm_capture.RUN_KEY` for the same reason, and pinned equal by the same
#: test. A `Meter` given a `run_id` seeds its holder with it, before anything
#: is sent, so the facade's incoming-response capture can name the run on
#: every attempt (`llm._stamp` keeps the key across its per-attempt clear).
RUN_KEY = "_run_id"
#: The display reasoning buffer's key, restated from `llm_reasoning.KEY`: a
#: streaming caller installs the buffer before the facade call.
REASONING_KEY = "_reasoning_display"
#: The tool-call collector's key, restated from `tool_calls.KEY` (01g-S1): a
#: caller offering tools installs it before the facade call.
TOOL_CALLS_KEY = "_tool_calls"
#: Holder keys a caller may place BEFORE the facade is called, which say
#: nothing about whether a request went out (`sent`).
PRE_SEND_KEYS = (RUN_KEY, REASONING_KEY, TOOL_CALLS_KEY)


def sent(holder: dict | None) -> bool:
    """Whether a request went out for this holder: whether the facade (or an
    embed's `_stamp`) stamped it, which is any key outside `PRE_SEND_KEYS`.

    The one emptiness test in the package (`test_usage_guard.py` refuses the
    shapes it can see): a holder seeded with a run id, or carrying a
    reasoning buffer or a tool-call collector, is not a sent call, and no holder at all (None) is not
    one either. Guarded: a holder whose keys cannot be iterated counts as
    sent, which is what plain truthiness answered for it before."""
    if holder is None:
        return False
    try:
        return any(key not in PRE_SEND_KEYS for key in holder)
    except Exception:  # noqa: BLE001 - see the docstring
        return True

#: A row saying one scene id became another (#153). Not a call, and every
#: rollup skips it -- see `_is_call`. A scene's id is its filename stem, so the
#: first date set on a scene renames it and every store holding that id has to
#: follow (`store.scene_refs`). This ledger cannot follow the way the others do:
#: they rewrite a JSON file under the campaign lock, and rewriting a month file
#: would race the unlocked `O_APPEND` writes that are the whole reason `record`
#: never blocks a turn. So the rename is APPENDED like everything else, and the
#: read side walks the trail (`_scene_now`) instead. That also keeps the file what
#: it claims to be: a log of what happened, in the order it happened, that
#: nothing rewrites after the fact.
KIND_RENAME = "rename"

#: The zeroed bucket, and the field list every rollup shares. ``total_tokens``
#: is stored rather than derived so a bucket is self-describing to the frontend
#: reading it, and ``calls``/``errors`` are separate because a failed call still
#: costs time and may still have been billed for tokens it generated.
#:
#: `cache_read_tokens`/`cache_write_tokens` are inside `prompt_tokens` and so
#: are deliberately absent from `total_tokens` -- see `_add`.
#:
#: **Three money columns, and no two of them may ever be added.** `cost_usd` is
#: what an account was charged. `estimated_usd` is what a subscription-billed
#: call would have cost at the provider's own API rates and did not
#: (`cost_basis: "equivalent"`). `modelled_usd` is this side's arithmetic over
#: rates the *user* typed (`store.pricing`, #158), for the calls whose provider
#: named no price at all -- the weakest of the three, and the only one grimoire
#: computes rather than receives. Each carries its own call count so a view can
#: say how much of a total is which, and `unpriced_calls` keeps counting only
#: what none of them covers -- and `unmetered_calls` is the slice of THAT which
#: no rate could ever cover, because the provider reported no token counts
#: either. The split exists so a view can tell a reader whether typing a rate
#: would help: for an unmetered call it would not, and sending them to do it is
#: sending them to an action that cannot resolve the warning. A native decision
#: no rate may model (`_modellable`) is the other such slice, counted apart
#: (the lazy `unpriced_native_calls`) and never in `unmetered_calls`, because
#: its reason is not missing counts.
_ZERO = {"calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "total_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "cost_usd": 0.0, "estimated_usd": 0.0, "modelled_usd": 0.0,
         "priced_calls": 0, "unpriced_calls": 0, "subscription_calls": 0,
         "modelled_calls": 0, "unmetered_calls": 0, "duration_ms": 0}

#: Cents-of-a-cent. Provider costs run to eight decimal places on a cheap model,
#: and summing raw floats surfaces as `0.30000000000000004` in a UI that renders
#: the number straight. Rounded on the way OUT only -- rows keep what the
#: provider said.
_CENTS = 6


def _text(value: object) -> str:
    """A row's string field, or "" for anything else a hand edit left there."""
    return value if isinstance(value, str) else ""


def _counts(row: dict) -> tuple[int | None, int | None]:
    """The row's (prompt, completion) counts as a rate would price them.

    Each is `_count`'s answer -- None when nobody counted it -- except an
    `embed` row's absent completion, which is the structural zero
    `_completion_count` reads (ruling 8): an embedding generates nothing. That
    is a fact about the operation rather than a guess, and it is the only one:
    every other operation still needs both counts, no estimate ever fills an
    embed row's completion, and an embed row whose prompt nobody counted is
    still unmetered (`_metered`).
    """
    return _count(row.get("prompt_tokens")), _completion_count(row)


def _modellable(row: dict) -> bool:
    """Whether any rate may model this row: every row but a native decision.

    A native decisions endpoint is not billed like a chat call -- OpenAI bills
    its decisions on input tokens only, and neither provider documents a
    per-token price for both sides -- so a chat rate times its counts would be
    a figure for a call nobody sold that way (slice H, ruling 10). Such a row
    is spend when its provider reported a cost and unpriced when it did not;
    its counts are filed as reported and still sum into the token totals.
    Decided at read time, so a rate the user sets later cannot model one
    either. The mode is the backend's own name (`decisions.BACKENDS`), which
    is what the decide chain stamps on the row."""
    return row.get("decision_mode") != decisions.NATIVE_BACKEND


def _metered(row: dict) -> bool:
    """True when a rate could price this row: both counts are known (`_counts`)."""
    prompt, completion = _counts(row)
    return prompt is not None and completion is not None


def _count(value: object) -> int | None:
    """A token count as the estimator needs it: the number, or None when the
    field was absent or unusable. Deliberately not `_int`, which floors both of
    those to 0 — a rollup adding zero is harmless, but pricing zero tokens
    produces `$0.00` for a call nobody measured."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)


def _completion_count(row: dict) -> int | None:
    """`completion_tokens` as the estimator needs it, with one structural zero.

    An embedding request generates nothing, so an `operation: "embed"` row has
    no completion count because there is nothing to count -- not because
    nobody counted (`embeddings._Spend.fold` never writes one). Its prompt
    count is then the whole call, and a rate prices it completely; reading the
    absent field as "unmeasured" left every unpriced embed row unmodellable and
    told the Costs card no rate could ever help.

    This is arithmetic over a count somebody did report, never a price: the
    prompt count is still required (`pricing.estimate`), so an embed row whose
    provider counted nothing stays unmetered, and the figure a rate makes of it
    lands in `modelled_usd` like any other. Only *absent* reads as zero -- a
    count the row does carry is used as it is -- and only for embed rows: a
    generation missing its completion count is still half a call counted.
    """
    count = _count(row.get("completion_tokens"))
    if count is None and row.get("operation") == EMBED_OPERATION:
        return 0
    return count


def _int(value: object) -> int:
    """A non-negative count, or 0. Negatives are floored rather than summed: a
    row is a line in a file a human can edit, and one hand-typed `-999999` that
    subtracts from a month's total is a rollup nobody can reconcile against a
    provider's invoice. `bool` is excluded because it is an `int` subclass and
    `True` would otherwise count as one token."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _float(value: object) -> float | None:
    """A finite number, or None for anything else — including the absent field
    that means "this call was never priced", which is the distinction the whole
    `unpriced_calls` count rests on."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _money(value: float) -> float:
    """One money column, rounded — but never rounded THROUGH zero.

    `_CENTS` puts the floor at $0.0000005, and a real figure can sit under it:
    twenty tokens at $0.00002/1k is 4e-7. Rounded that becomes `0.0` while the
    call count beside it stays positive, and every cost surface then renders a
    priced call as `$0.00` — the one claim this module exists to prevent, made
    by the rounding rather than by the ledger. A positive value that would
    round away keeps its own precision instead; the views already have a
    `<$0.0001` rendering for figures that small.
    """
    rounded = round(value, _CENTS)
    return value if rounded == 0.0 and value > 0.0 else rounded


def _rounded(bucket: dict) -> dict:
    return {**bucket, "cost_usd": _money(bucket["cost_usd"]),
            "estimated_usd": _money(bucket["estimated_usd"]),
            "modelled_usd": _money(bucket["modelled_usd"])}


def _ranked(buckets: dict[str, dict]) -> list[dict]:
    """Buckets as a list, busiest first — by calls, then by name so the order is
    stable across two summaries of the same data."""
    return [{"key": key, **_rounded(bucket)}
            for key, bucket in sorted(buckets.items(),
                                      key=lambda kv: (-kv[1]["calls"], kv[0]))]


def _label(value: object) -> str:
    """A bucket key that is safe to render. A hand-edited row can hold anything,
    and a dict reaching the frontend as a key would take the panel down the way
    `prompt_log`'s validation exists to prevent."""
    return value if isinstance(value, str) and value else "unknown"


def _post(row: dict) -> int | None:
    """The player post this row was answering, or None when it names none.
    `_int` is deliberately not used: it floors an absent field to 0, and 0 is a
    real post index."""
    post = row.get("post")
    if isinstance(post, bool) or not isinstance(post, int) or post < 0:
        return None
    return post
