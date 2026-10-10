"""Per-(provider, model) facts, stored beside the provider connection.

`<home>/llm_connections/<id>.facts.json` maps a model name to what is known of
THAT model on THAT provider (the preset table in `providers.py` holds what is
true of every model behind a preset; this holds what is true of one):

    {"<model>": {"vision": "on"|"off", "prefill": bool, "post_process": str,
                 "rates": {...}, "context_window": int, "max_output": int,
                 "embedding": {"input": "prefix", "query_prefix": str, ...},
                 "verified": {"rev": "<connection rev>", "caps": {cap: {...}}},
                 "overrides": {cap: "yes"|"no"}}}

Two kinds of fact, and they age differently:

- **Verified** results come from probing the endpoint, so they describe the
  endpoint as it was configured then. They are tagged with the connection's
  `rev` (the same value `llm_connections.cached_models` gates its sidecar on)
  and `of` hides them once the rev has moved on. The write is gated too:
  `record_verified` files results only while their rev is still the
  connection's own, checked and written under the connection lock every
  connection write holds -- otherwise a run that started on an old rev would
  replace the new rev's results, or recreate a deleted connection's file.
- **Stated** facts (`overrides`, `vision`, `prefill`, `post_process`, `rates`,
  the model's size, `context_window` / `max_output`, 01i, and its embedding
  options, `embedding`, 01h) are the user's own word about the model and
  survive a rev change. Embedding options are never derived: only the user's
  write (`state(..., embedding=)`) puts a block here, because their document
  side is part of the Embedding role's vector space id.

Reads never raise: the file is one a sync or a hand can mangle into any JSON,
and it is read on the path of a turn. No write replaces a file it could not
read: one it could not open (a sharing violation, a file a sync client holds)
raises `FactsUnreadableError`, and one it opened and could not parse (empty,
truncated, hand-mangled, not an object of models) raises its subclass
`FactsMangledError`. Either way nothing is written, since what a write would
store is the one entry it was changing, and every other model's rates,
verdicts and overrides -- which one fixed comma would have recovered -- would
be gone. The first clears on its own; the second needs a person, so the facts
route answers them differently (503 against 409 `facts_unreadable`). The
migration's copy (`adopt_legacy`) holds the same rule, and the run that asked
retries on the next start. Writes
are read-merge-write under one module lock, because two probes of different
models on one provider would otherwise each rewrite the file from a copy that
lacks the other's result. Global to the provider, so it takes no campaign lock.
Every write -- verified results and the user's own word alike -- is made only
while the provider exists, checked under `llm_connections.LOCK`, so none can
recreate the file of a connection deleted meanwhile.
"""

from __future__ import annotations

import dataclasses
import json
import re
import threading
from collections.abc import Callable, Mapping

from ... import wire
from .. import atomic, config, llm_connections, pricing
from ..paths import now_iso, safe_id
from .providers import CAPABILITIES

OVERRIDE_VALUES: tuple[str, ...] = ("yes", "no")
_VISION = ("", "on", "off")

#: The file's read-merge-write. Taken INSIDE `llm_connections.LOCK` where both
#: are held (`record_verified`), never around it.
_lock = threading.Lock()


def _load(provider_id: str) -> dict[str, dict]:
    """The file as `{model: entry}`; anything unreadable or malformed is empty."""
    if not safe_id(provider_id):
        return {}
    try:
        raw = json.loads(
            llm_connections.facts_path(provider_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {m: e for m, e in raw.items() if isinstance(e, dict)}


class FactsUnreadableError(OSError):
    """A provider's facts file that a WRITE could not read: there, but held by
    another program, or not text. Nothing is written; rewriting it from an
    empty read would drop every other model's rates, verified results and
    overrides. Usually clears on its own (a sync client lets go)."""


class FactsMangledError(FactsUnreadableError):
    """A facts file that was read and does not parse as a JSON object of
    objects: empty, truncated, or hand-mangled. Refused like its parent, but
    it will not clear on its own -- a person has to fix or remove the file --
    so a caller that answers a person says so (409 `facts_unreadable`)."""


def _load_for_write(provider_id: str) -> dict[str, dict]:
    """The file as a writer merges onto it, or a refusal: absent is empty, a
    read that fails raises `FactsUnreadableError`, and a file that is not a
    JSON object of objects raises `FactsMangledError`. Never `_load`'s
    fail-soft `{}` -- a write merged onto that replaces the file."""
    path = llm_connections.facts_path(provider_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        raise FactsUnreadableError(
            f"the model facts of {provider_id} could not be read: {exc}") from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise FactsMangledError(
            f"the model facts of {provider_id} are not readable JSON: {exc}") from exc
    if not isinstance(raw, dict) or not all(isinstance(e, dict) for e in raw.values()):
        raise FactsMangledError(
            f"the model facts of {provider_id} are not an object of models")
    return raw


def _store(provider_id: str, doc: dict[str, dict]) -> None:
    p = llm_connections.facts_path(provider_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(doc, indent=2) + "\n")


def model_of(conn: dict) -> str:
    """The model `conn` runs, which is the key its facts are stored under: its
    own, `opus` for an unset Claude model, "" for any other unset one.

    `llm.effective_model`'s rule, restated because the store never imports
    `llm` (a test holds the two equal). The migration writes a connection's
    facts under this key and the resolver reads them back under it,
    so the two cannot disagree about which model a fact belongs to."""
    model = str(conn.get("model") or "")
    if not model and conn.get("kind") == "claude":
        return config.DEFAULT_CLAUDE_MODEL
    return model


def read(provider_id: str) -> dict[str, dict]:
    """Every model's raw entry for a provider; `{}` when there is none to read."""
    return _load(provider_id)


def _clean_results(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    return {c: dict(r) for c, r in raw.items()
            if c in CAPABILITIES and isinstance(r, dict)}


def of(provider_id: str, model: str, rev: str, *, strict: bool = False) -> dict:
    """What is known of `model`, in the one shape callers read.

    Verified results recorded under another `rev` are dropped; everything the
    user stated is kept. `strict` reads as a writer does (`_load_for_write`):
    a file that exists and cannot be read -- held by a sync client
    (`FactsUnreadableError`), or not an object of models (`FactsMangledError`)
    -- raises instead of reading as a model nothing was said of; an absent
    file still reads as empty. The facts panel's GET flags either
    (`unreadable`), since a save would be refused, and the migration's
    Embedding check (`legacy_plan.legacy_embeds`) fails the run on it rather
    than deciding anything for good from a file it could not read.
    """
    if strict and safe_id(provider_id):
        return _view(_load_for_write(provider_id).get(model, {}), rev)
    return _view(_load(provider_id).get(model, {}), rev)


#: A stated size no int32 field on any wire could carry is a typo.
_LIMIT_CEILING = 2**31

#: The model's size, as the user may state it (01i): its context window and
#: the most a reply may be asked for. Read by `limits.of` before the catalog's.
LIMIT_FIELDS: tuple[str, ...] = ("context_window", "max_output")


def check_limit(name: str, value: object) -> int | None:
    """A limit as a write may state it: None leaves it, 0 removes it, and a
    positive int below `2**31` sets it. Anything else -- a bool, a float, a
    string, a negative number -- is a `ValueError`. 0 removes for the reason a
    catalog's 0 says nothing: no model has a zero window, so zero cannot be a
    statement."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value == 0:
        return 0
    found = stated_limit(value)
    if found is None:
        raise ValueError(f"{name} must be a whole number of tokens from 1 to {_LIMIT_CEILING - 1}"
                         f" (0 to remove it), not {value!r}")
    return found


def _apply_sizes(entry: dict, sized: dict[str, int]) -> None:
    """Lay `sized` (`check_limit`'s answers: 0 removes, else sets) over
    `entry`, then judge the merged entry (`_check_sizes`) -- only when this
    write states a size, so a pair a hand left inconsistent does not refuse
    an unrelated write."""
    for name, size in sized.items():
        if size:
            entry[name] = size
        else:
            entry.pop(name, None)
    if sized:
        _check_sizes(entry)


def _check_sizes(entry: dict) -> None:
    """Refuse an entry that would state a max output above its own stated
    window -- the merged entry, since a request may state one of the two
    while the other is already on file. Disagreeing with the CATALOG is
    allowed: the user's word may be the correction."""
    window = stated_limit(entry.get("context_window"))
    output = stated_limit(entry.get("max_output"))
    if window is not None and output is not None and output > window:
        raise ValueError(f"max output ({output:,} tokens) cannot be above the context window "
                         f"({window:,} tokens): lower the max output, or clear it, first")


def stated_limit(value: object) -> int | None:
    """A stated size as it is kept: a positive int (never a bool) below
    `2**31`, else None -- what a hand-edited file holding anything else reads
    as. 0 is never a size (no model has a zero window), so it is not one."""
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < _LIMIT_CEILING:
        return value
    return None


def _view(entry: dict, rev: str) -> dict:
    """`of`'s shape for one raw entry, its verified results kept only under
    `rev`."""
    vision = entry.get("vision")
    prefill = entry.get("prefill")
    post_process = entry.get("post_process")
    rates = entry.get("rates")      # usable or None: `pricing.entry`, the reader's rule
    verified = entry.get("verified")
    overrides = entry.get("overrides")
    return {
        "vision": vision if vision in _VISION else "",
        "prefill": prefill if isinstance(prefill, bool) else None,
        "post_process": post_process if isinstance(post_process, str) else "",
        "rates": pricing.entry(rates),
        "verified": (_clean_results(verified.get("caps"))
                     if isinstance(verified, dict) and verified.get("rev") == rev
                     else {}),
        "overrides": ({c: v for c, v in overrides.items()
                       if c in CAPABILITIES and v in OVERRIDE_VALUES}
                      if isinstance(overrides, dict) else {}),
        **{name: stated_limit(entry.get(name)) for name in LIMIT_FIELDS},
        # Raw: judged by its readers (`embed_options`), strictly for the
        # Embedding role and fail-soft elsewhere, never here.
        "embedding": dict(block) if isinstance(block := entry.get("embedding"), dict)
        else block,
    }


def _require_safe(provider_id: str) -> None:
    if not safe_id(provider_id):
        raise ValueError(f"unsafe provider id: {provider_id!r}")


def record_verified(provider_id: str, model: str, rev: str,
                    results: dict[str, dict]) -> bool:
    """Merge probe `results` (`{cap: {"ok", "at"?, "error"?}}`) into the model,
    if `rev` is still the connection's own. Whether it wrote is the answer.

    `rev` is the one the probes STARTED on. When the connection has moved past
    it (an edit landed while they were out, so they describe a different
    endpoint) or is gone, nothing is written and the answer is False: writing
    would replace the new rev's results, or recreate the facts file of a
    deleted connection; and on a store a newer build switched meanwhile
    (`config.format_hold`), whose facts are not this build's to rewrite.
    The compare and the write are one step, under
    `llm_connections.LOCK` -- which every connection write holds -- and then
    this module's `_lock`, always in that order; a check made before taking
    them is no check, because an edit can land between it and the write.

    Results already held under the same `rev` are kept and overlaid; results
    under any other rev are replaced, never blended with the new ones.
    """
    _require_safe(provider_id)
    for cap, result in results.items():
        if cap not in CAPABILITIES:
            raise ValueError(f"unknown capability: {cap!r}")
        if not isinstance(result, dict):
            raise ValueError(f"result for {cap!r} must be an object")
    stamped = {c: {**r, "at": r.get("at") or now_iso()} for c, r in results.items()}
    try:
        # The whole compare-and-write in the cross-process hold, from the rev
        # check through the store (`llm_connections.LOCK` is that hold's lock).
        with llm_connections.LOCK, config.format_hold():
            try:
                current = llm_connections.read_connection_raw(provider_id)["rev"]
            except llm_connections.ConnectionNotFound:
                return False
            if not rev or current != rev:
                return False
            with _lock:
                doc = _load_for_write(provider_id)
                entry = doc.setdefault(model, {})
                old = entry.get("verified")
                caps = (_clean_results(old.get("caps"))
                        if isinstance(old, dict) and old.get("rev") == rev else {})
                caps.update(stamped)
                entry["verified"] = {"rev": rev, "caps": caps}
                _store(provider_id, doc)
    except config.NewerFormatError:
        # A newer build switched the store while the probes were out: its
        # facts are not this build's to rewrite. Not filed.
        return False
    return True


#: A facts write's check, `guard(before, after)`: `model`'s facts as `of`
#: reads them now and as it will read them once written. Whatever it raises
#: refuses the write.
Guard = Callable[[dict, dict], None]


def _write_existing(provider_id: str, model: str,
                    change: Callable[[dict], None], guard: Guard | None = None) -> None:
    """Apply `change` to `model`'s entry and store the file -- only while the
    provider exists, checked under `llm_connections.LOCK` and then `_lock`
    (`record_verified`'s order). A write that lost a race to the delete would
    otherwise recreate the facts file of a connection that is gone. Raises
    `ConnectionNotFound` then, and writes nothing. An entry `change` leaves
    empty is dropped.

    The whole span -- existence check, load, change, store -- is in
    `config.format_hold` (cross-process; `llm_connections.LOCK` is its lock,
    and `_lock` is taken under it), so a store a newer build switched is
    refused (`config.NewerFormatError`) rather than written, and another
    server's write to the same file cannot land between the load and the
    store. `guard` (`Guard`) runs in that hold, so a guard reading
    `config.md` -- the Embedding role's confirmation,
    `routes.config.put_connection_facts` -- reads the value no settings write
    can change before this one lands."""
    # The cross-process hold from the existence check through the store, so
    # two servers' read-merge-writes of one facts file never lose one.
    with llm_connections.LOCK, config.format_hold():
        rev = llm_connections.read_connection_raw(provider_id).get("rev", "")
        rev = rev if isinstance(rev, str) else ""
        with _lock:
            doc = _load_for_write(provider_id)
            before = _view(doc.get(model, {}), rev)
            entry = doc.setdefault(model, {})
            change(entry)
            after = _view(entry, rev)
            if not entry:
                doc.pop(model, None)
            if guard is not None:
                guard(before, after)
            _store(provider_id, doc)


def _check_overrides(overrides: object, *, blank: bool) -> dict[str, str]:
    """`overrides` checked: `{cap: "yes"|"no"}`, plus "" (remove) when `blank`."""
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be an object")
    allowed = (*OVERRIDE_VALUES, "") if blank else OVERRIDE_VALUES
    for cap, value in overrides.items():
        if cap not in CAPABILITIES:
            raise ValueError(f"unknown capability: {cap!r}")
        if value not in allowed:
            raise ValueError(f"override for {cap!r} must be "
                             + ("'', " if blank else "") + "'yes' or 'no'")
    return dict(overrides)


def set_overrides(provider_id: str, model: str, overrides: dict[str, str]) -> None:
    """Replace the model's overrides (`{cap: "yes"|"no"}`); an empty dict clears.
    `ConnectionNotFound` when the provider is gone (`_write_existing`)."""
    _require_safe(provider_id)
    checked = _check_overrides(overrides, blank=False)

    def change(entry: dict) -> None:
        if checked:
            entry["overrides"] = checked
        else:
            entry.pop("overrides", None)

    _write_existing(provider_id, model, change)


#: The `post_process` values a model may be stated to need.
POST_PROCESS_VALUES: tuple[str, ...] = ("none", "strict")


def _check_stated(vision: object, prefill: object, post_process: object) -> dict:
    """The stated fields that are not None, each checked."""
    if vision is not None and vision not in _VISION:
        raise ValueError(f"vision must be '', 'on' or 'off', not {vision!r}")
    if prefill is not None and not isinstance(prefill, bool):
        raise ValueError(f"prefill must be true or false, not {prefill!r}")
    if post_process is not None and post_process not in POST_PROCESS_VALUES:
        raise ValueError(f"post_process must be one of {POST_PROCESS_VALUES}, "
                         f"not {post_process!r}")
    return {k: v for k, v in (("vision", vision), ("prefill", prefill),
                              ("post_process", post_process)) if v is not None}


def set_stated(provider_id: str, model: str, *, vision: str | None = None,
               prefill: bool | None = None, post_process: str | None = None) -> None:
    """Merge the user's word about `model` into its facts; `None` leaves a
    field as it is.

    `vision` is "" (auto), "on" or "off" -- the post-image preference, which
    `off` is and nothing more (spec 4.2); `prefill` is a bool; `post_process`
    is one of `POST_PROCESS_VALUES`. Anything else raises `ValueError` before
    the file is touched. Verified results and overrides are left as they are.
    `ConnectionNotFound` when the provider is gone (`_write_existing`).
    """
    state(provider_id, model, vision=vision, prefill=prefill, post_process=post_process)


def state(provider_id: str, model: str, *, vision: object = None, prefill: object = None,
          post_process: object = None, overrides: object = None,
          rates: object = None, context_window: object = None, max_output: object = None,
          embedding: object = None, guard: Guard | None = None) -> None:
    """The facts panel's write: `set_stated`'s fields, and `overrides` MERGED
    per capability -- `{cap: "yes"|"no"}` sets one, `{cap: ""}` removes it,
    and a capability the dict does not name is left as it is. Everything is
    checked (`ValueError`) before the file is touched, and lands as one write.
    `ConnectionNotFound` when the provider is gone (`_write_existing`);
    `guard` is checked in the hold that writes (see there).

    `rates` is the model's own per-token price: `None` leaves it as it is, `{}`
    removes it, and anything else replaces it after `pricing.check_entry` --
    both base rates, no unknown field -- so a partial entry is refused rather
    than half-stored. A stated fact like the rest: it survives a `rev` change.

    `context_window` and `max_output` are the model's size on this provider
    (01i, `check_limit`): None leaves one, 0 removes it, a positive int sets
    it. A write that would leave a stated output above a stated window is
    refused (`ValueError`) on the MERGED entry, inside the hold that writes,
    so nothing is stored.

    `embedding` is the model's embedding options (01h, `_check_embedding`):
    `None` leaves them, and anything else is checked and normalised before
    the file is touched -- a block that normalises to nothing (`{}`, or
    `{"input": "none"}`) removes them, and any other replaces them. Their
    document side is part of the Embedding role's vector space id, so the
    facts route confirms a write that moves it (`embed_space.facts_moved`).
    """
    _require_safe(provider_id)
    stated = _check_stated(vision, prefill, post_process)
    embedded = None if embedding is None else _check_embedding(embedding)
    changed = {} if overrides is None else _check_overrides(overrides, blank=True)
    priced = (None if rates is None
              else {} if isinstance(rates, dict) and not rates
              else pricing.check_entry(rates))
    sized = {name: value for name, value in (
        ("context_window", check_limit("context_window", context_window)),
        ("max_output", check_limit("max_output", max_output))) if value is not None}
    if not stated and not changed and priced is None and not sized and embedded is None:
        return

    def change(entry: dict) -> None:
        entry.update(stated)
        _apply_sizes(entry, sized)
        _apply_embedding(entry, embedded)
        if priced is not None:
            if priced:
                entry["rates"] = priced
            else:
                entry.pop("rates", None)
        if not changed:
            return
        old = entry.get("overrides")
        kept = ({c: v for c, v in old.items() if c in CAPABILITIES and v in OVERRIDE_VALUES}
                if isinstance(old, dict) else {})
        for cap, value in changed.items():
            if value:
                kept[cap] = value
            else:
                kept.pop(cap, None)
        if kept:
            entry["overrides"] = kept
        else:
            entry.pop("overrides", None)

    _write_existing(provider_id, model, change, guard)


# ---- embedding options (01h) ----

#: The longest prefix or input-type value a model may be stated to take, in
#: characters (01h §3.2): room for a published query instruction, and well
#: inside what the embed callers' byte bounds leave free under an 8k window.
EMBED_TEXT_MAX = 200

#: A request field the options may name: a literal top-level key of the
#: request body, never a nested path (so no `.`).
EMBED_FIELD_RE = re.compile(r"[a-z_][a-z0-9_]{0,40}")

#: The request's own fields, which an option may never overwrite.
EMBED_RESERVED_FIELDS = frozenset({"model", "input", "encoding_format", "user"})

#: The keys a block may hold: `wire.EmbedOptions`' fields.
_EMBED_FIELDS = tuple(f.name for f in dataclasses.fields(wire.EmbedOptions))
#: The option fields that are text sent as part of an input or a request.
_EMBED_TEXTS = ("query_prefix", "document_prefix", "query_value", "document_value")
#: Which mode each mode-specific field belongs to.
_EMBED_MODE_OF = {"query_prefix": "prefix", "document_prefix": "prefix",
                  "param_field": "param", "query_value": "param",
                  "document_value": "param"}

#: Every refusal below is one of these fixed sentences: a message never
#: quotes the value it refuses (it may be prompt text, or a control byte).
EMBED_NOT_OBJECT = "embedding options must be an object"
EMBED_BAD_INPUT = "embedding input must be 'none', 'prefix' or 'param'"
EMBED_NOT_TEXT = "embedding prefixes, values and field names must be text"
EMBED_TOO_LONG = f"embedding prefixes and values are at most {EMBED_TEXT_MAX} characters"
EMBED_CONTROL = ("embedding prefixes and values may not contain control characters "
                 "(other than a newline or a tab) or unpaired surrogates")
EMBED_BAD_FIELD = ("an embedding request field must be lowercase letters, digits and "
                   "underscores, start with a letter or underscore, and be at most 41 "
                   "characters")
EMBED_RESERVED = ("an embedding request field cannot be model, input, encoding_format "
                  "or user")
EMBED_SAME_FIELD = "the input-type field and the dimensions field must differ"
EMBED_WRONG_MODE = "a prefix needs input 'prefix', and a request field needs input 'param'"
EMBED_NO_PREFIX = "the prefix input type needs a query prefix or a document prefix"
PARAM_NOT_YET = "the request-field input type is not supported by this build"
DIMENSIONS_NOT_YET = "requested dimensions are not supported by this build"


def _embed_text(value: str) -> None:
    """Refuse a prefix or value that is too long, or carries a C0 control
    other than a newline or a tab, a C1 control, or a lone surrogate (which
    could not be encoded to send)."""
    if len(value) > EMBED_TEXT_MAX:
        raise ValueError(EMBED_TOO_LONG)
    for ch in value:
        code = ord(ch)
        if ((code < 0x20 and ch not in "\n\t") or 0x80 <= code < 0xa0
                or 0xd800 <= code <= 0xdfff):
            raise ValueError(EMBED_CONTROL)


def _embed_field(value: str) -> None:
    """Refuse a request field name that is not a literal top-level key, or
    is one of the request's own."""
    if not EMBED_FIELD_RE.fullmatch(value):
        raise ValueError(EMBED_BAD_FIELD)
    if value in EMBED_RESERVED_FIELDS:
        raise ValueError(EMBED_RESERVED)


def _embed_fields(raw: Mapping) -> dict[str, object]:
    """The block's known fields, each type-checked: absent or null reads as
    the default, a key the block does not define is ignored."""
    got: dict[str, object] = {}
    for f in _EMBED_FIELDS:
        value = raw.get(f)
        if value is None:
            continue
        if f == "dimensions":
            got[f] = value          # judged by the caller (refused in this build)
            continue
        if not isinstance(value, str):
            raise ValueError(EMBED_NOT_TEXT)
        got[f] = value
    return got


def _embed_shape(got: Mapping[str, object], mode: str) -> None:
    """Refuse a block whose texts, field names or mode fields are wrong, in
    the order a person fixes them: each text, each field name, then a field
    belonging to another mode."""
    for name in _EMBED_TEXTS:
        _embed_text(str(got.get(name, "")))
    for name in ("param_field", "dimensions_field"):
        if got.get(name):
            _embed_field(str(got[name]))
    if (mode == "param" and got.get("dimensions") is not None
            and got.get("param_field") == got.get("dimensions_field", "dimensions")):
        raise ValueError(EMBED_SAME_FIELD)
    if any(got.get(name) and _EMBED_MODE_OF[name] != mode for name in _EMBED_MODE_OF):
        raise ValueError(EMBED_WRONG_MODE)


def _check_embedding(raw: object) -> dict:
    """An `embedding` block as a write stores it, or a `ValueError` naming the
    problem in a fixed sentence (01h §3.2). Shared by every reader, so a
    block on disk is judged by the rule a write is.

    Only the fields the mode uses are kept, and a field that belongs to
    another mode is refused rather than dropped; mode `none` stores nothing
    (`{}`). A lone `dimensions_field` is checked and dropped: with no
    `dimensions` it says nothing (§5.1). This build sends neither the `param`
    mode nor `dimensions`, so both are refused (the 01h-S2 interim rule)."""
    if not isinstance(raw, Mapping):
        raise ValueError(EMBED_NOT_OBJECT)
    got = _embed_fields(raw)
    mode = str(got.get("input", "none"))
    if mode not in wire.EMBED_INPUT_MODES:
        raise ValueError(EMBED_BAD_INPUT)
    _embed_shape(got, mode)
    if mode == "param":
        raise ValueError(PARAM_NOT_YET)
    if got.get("dimensions") is not None:
        raise ValueError(DIMENSIONS_NOT_YET)
    if mode == "prefix" and not (got.get("query_prefix") or got.get("document_prefix")):
        raise ValueError(EMBED_NO_PREFIX)
    if mode == "none":
        return {}
    return {"input": mode, **{name: got[name] for name in ("query_prefix", "document_prefix")
                              if got.get(name)}}


def embed_options(model_facts: Mapping) -> wire.EmbedOptions | None:
    """The embedding options `model_facts` (`of`'s shape) state, or None when
    they state none. Raises `ValueError` for a block `_check_embedding`
    refuses -- the Embedding role then names no space, and a fail-soft reader
    reads it as none."""
    block = model_facts.get("embedding")
    if block is None:
        return None
    checked = _check_embedding(block)
    return wire.EmbedOptions(
        input=str(checked.get("input", "none")),
        query_prefix=str(checked.get("query_prefix", "")),
        document_prefix=str(checked.get("document_prefix", "")))


def _apply_embedding(entry: dict, block: dict | None) -> None:
    """Lay a checked block (`_check_embedding`) over `entry`: None leaves the
    entry's, `{}` removes it, anything else replaces it."""
    if block is None:
        return
    if block:
        entry["embedding"] = block
    else:
        entry.pop("embedding", None)


#: The stated fields a connection's legacy record carried (`vision`, `prefill`,
#: `post_process`), which the format-2 migration copies into its model's facts.
LEGACY_FIELDS: tuple[str, ...] = ("vision", "prefill", "post_process")

#: The key, in a model's entry, recording what the migration copied there
#: (`{field: value}`): what makes its copy re-doable. Never read as a fact.
MIGRATED_KEY = "migrated"


def _checked_legacy(stated: dict[str, object]) -> tuple[dict[str, object], dict[str, str]]:
    """`(the fields of `stated` the writer takes, {field: why} for the rest)`."""
    valid: dict[str, object] = {}
    refused: dict[str, str] = {}
    for field, value in stated.items():
        if field not in LEGACY_FIELDS:
            raise ValueError(f"not a legacy model field: {field!r}")
        try:
            valid.update(_check_stated(**{f: (value if f == field else None)
                                          for f in LEGACY_FIELDS}))
        except ValueError as exc:
            refused[field] = str(exc)
    return valid, refused


def _take_back_copies(doc: dict[str, dict]) -> None:
    """Undo, in place, every copy an earlier migration run recorded: each
    copied field still holding the value copied is removed (one holding
    anything else is somebody's own word, and stays), and so is the record.
    An entry left with nothing is dropped."""
    for name in list(doc):
        entry = doc[name]
        copied = entry.pop(MIGRATED_KEY, None)
        if copied is None:
            continue
        if isinstance(copied, dict):
            for field, value in copied.items():
                if field in LEGACY_FIELDS and entry.get(field) == value:
                    entry.pop(field)
        if not entry:
            doc.pop(name)


def adopted(provider_id: str, model: str, rev: str, *, adopting: str,
            stated: dict[str, object], strict: bool = False) -> dict:
    """`of(provider_id, model, rev)` as it will read once the migration's
    step 3 has run `adopt_legacy(provider_id, adopting, stated)`: every copy an
    earlier, interrupted run recorded on this provider taken back first
    (`_take_back_copies`), then the fields of `stated` the writer takes laid
    over `adopting`'s entry. A read: nothing is written, and a file that
    cannot be read is empty, as for `of`.

    What play reads at format 1 (`resolve`, through the planner's
    `Overlay.facts`), so a field the user set back to its default since an
    interrupted run copied it is not sent as the stale copy -- the in-memory
    answer is the one the migration persists.

    `strict` reads as `of(strict=True)` does: a file that exists and cannot be
    read raises (`FactsUnreadableError`, `FactsMangledError`) -- what the
    Embedding role's read needs (`resolve.embed_attempt`)."""
    loaded = (_load_for_write(provider_id) if strict and safe_id(provider_id)
              else _load(provider_id))
    doc = {name: dict(entry) for name, entry in loaded.items()}
    _take_back_copies(doc)
    valid, _refused = _checked_legacy(stated)
    if valid:
        doc.setdefault(adopting, {}).update(valid)
    return _view(doc.get(model, {}), rev)


def adopt_legacy(provider_id: str, model: str, stated: dict[str, object]) -> dict[str, str]:
    """The migration's copy of a connection's legacy model fields: `stated`
    (`{field: value}`, the fields of `LEGACY_FIELDS` the record sets to
    something other than the default) become `model`'s stated facts. Returns
    `{field: why}` for each value refused; the others still land, as one write.

    Re-doable, because a run can stop after copying and resume after the user
    changed the record at format 1 -- set a field back to its default, or
    pointed the connection at another model. What was copied is recorded in
    the entry under `MIGRATED_KEY`, and a re-run takes exactly that back
    first, on every model of the provider: each copied field still holding the
    value copied is removed, and one that holds anything else is left -- a
    value somebody wrote over the copy is theirs. Then `stated` is copied
    afresh. So the facts end up saying what the record says now, and nothing
    the migration did not write is touched.

    A file it cannot read, or one that is empty, truncated or mangled, raises
    `FactsUnreadableError` (`FactsMangledError` for the latter) and nothing
    is written, as for every write -- an unattended rewrite from an empty read
    would drop every other model's verified results and overrides. `ConnectionNotFound` when the
    provider is gone, `config.NewerFormatError` on a store a newer build
    switched; both in `_write_existing`'s hold and lock order."""
    _require_safe(provider_id)
    valid, refused = _checked_legacy(stated)
    with llm_connections.LOCK, config.format_hold():
        llm_connections.read_connection_raw(provider_id)
        with _lock:
            doc = _load_for_write(provider_id)
            before = json.dumps(doc, sort_keys=True)
            _take_back_copies(doc)
            if valid:
                entry = doc.setdefault(model, {})
                entry.update(valid)
                entry[MIGRATED_KEY] = dict(valid)
            if json.dumps(doc, sort_keys=True) != before:
                _store(provider_id, doc)
    return refused
