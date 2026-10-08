"""Per-(provider, model) facts, stored beside the provider connection.

`<home>/llm_connections/<id>.facts.json` maps a model name to what is known of
THAT model on THAT provider (the preset table in `providers.py` holds what is
true of every model behind a preset; this holds what is true of one):

    {"<model>": {"vision": "on"|"off", "prefill": bool, "post_process": str,
                 "rates": {...},
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
- **Stated** facts (`overrides`, `vision`, `prefill`, `post_process`, `rates`)
  are the user's own word about the model and survive a rev change.

Reads never raise: the file is one a sync or a hand can mangle into any JSON,
and it is read on the path of a turn. A write the user asked for replaces a
mangled file; no write replaces one it could not READ (a sharing violation, a
file a sync client holds) -- that raises `FactsUnreadableError` instead, since
what it would write is the one entry it was changing and every other model's
verdicts and overrides would be gone. The migration's copy
(`adopt_legacy`) is stricter still: unattended, it refuses a mangled, empty or
truncated file too, and the run that asked retries on the next start. Writes
are read-merge-write under one module lock, because two probes of different
models on one provider would otherwise each rewrite the file from a copy that
lacks the other's result. Global to the provider, so it takes no campaign lock.
Every write -- verified results and the user's own word alike -- is made only
while the provider exists, checked under `llm_connections.LOCK`, so none can
recreate the file of a connection deleted meanwhile.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from .. import atomic, config, llm_connections
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
    another program or (for the migration's strict read) empty, truncated or
    not a JSON object. Nothing is written; rewriting it from an empty read
    would drop every other model's verified results and overrides."""


def _load_for_write(provider_id: str, *, strict: bool = False) -> dict[str, dict]:
    """The file as a writer merges onto it. Absent is empty. A read that fails
    raises `FactsUnreadableError`; so, when `strict`, does a file that is not
    a JSON object of objects (empty, truncated, hand-mangled). Not `strict`, a
    mangled file is replaced, as `_load` reads it -- the user's own edit."""
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
        if strict:
            raise FactsUnreadableError(
                f"the model facts of {provider_id} are not readable JSON: {exc}") from exc
        return {}
    if not isinstance(raw, dict) or (strict and not all(isinstance(e, dict)
                                                        for e in raw.values())):
        if strict:
            raise FactsUnreadableError(
                f"the model facts of {provider_id} are not an object of models")
        return {}
    return {m: e for m, e in raw.items() if isinstance(e, dict)}


def _store(provider_id: str, doc: dict[str, dict]) -> None:
    p = llm_connections.facts_path(provider_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(doc, indent=2) + "\n")


def model_of(conn: dict) -> str:
    """The model `conn` runs, which is the key its facts are stored under: its
    own, `opus` for an unset Claude model, "" for any other unset one.

    `llm.effective_model`'s rule, restated because the store never imports
    `llm` (a test holds the two equal). The migration writes a connection's
    facts under this key and the format-2 lowering reads them back under it,
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


def of(provider_id: str, model: str, rev: str) -> dict:
    """What is known of `model`, in the one shape callers read.

    Verified results recorded under another `rev` are dropped; everything the
    user stated is kept.
    """
    return _view(_load(provider_id).get(model, {}), rev)


def _view(entry: dict, rev: str) -> dict:
    """`of`'s shape for one raw entry, its verified results kept only under
    `rev`."""
    vision = entry.get("vision")
    prefill = entry.get("prefill")
    post_process = entry.get("post_process")
    rates = entry.get("rates")
    verified = entry.get("verified")
    overrides = entry.get("overrides")
    return {
        "vision": vision if vision in _VISION else "",
        "prefill": prefill if isinstance(prefill, bool) else None,
        "post_process": post_process if isinstance(post_process, str) else "",
        "rates": rates if isinstance(rates, dict) else None,
        "verified": (_clean_results(verified.get("caps"))
                     if isinstance(verified, dict) and verified.get("rev") == rev
                     else {}),
        "overrides": ({c: v for c, v in overrides.items()
                       if c in CAPABILITIES and v in OVERRIDE_VALUES}
                      if isinstance(overrides, dict) else {}),
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
          guard: Guard | None = None) -> None:
    """The facts panel's write: `set_stated`'s fields, and `overrides` MERGED
    per capability -- `{cap: "yes"|"no"}` sets one, `{cap: ""}` removes it,
    and a capability the dict does not name is left as it is. Everything is
    checked (`ValueError`) before the file is touched, and lands as one write.
    `ConnectionNotFound` when the provider is gone (`_write_existing`);
    `guard` is checked in the hold that writes (see there)."""
    _require_safe(provider_id)
    stated = _check_stated(vision, prefill, post_process)
    changed = {} if overrides is None else _check_overrides(overrides, blank=True)
    if not stated and not changed:
        return

    def change(entry: dict) -> None:
        entry.update(stated)
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

    Strict, unlike the user's writes: a file it cannot read, or one that is
    empty, truncated or mangled, raises `FactsUnreadableError` and nothing is
    written -- an unattended rewrite from an empty read would drop every other
    model's verified results and overrides. `ConnectionNotFound` when the
    provider is gone, `config.NewerFormatError` on a store a newer build
    switched; both in `_write_existing`'s hold and lock order."""
    _require_safe(provider_id)
    valid, refused = _checked_legacy(stated)
    with llm_connections.LOCK, config.format_hold():
        llm_connections.read_connection_raw(provider_id)
        with _lock:
            doc = _load_for_write(provider_id, strict=True)
            before = json.dumps(doc, sort_keys=True)
            _take_back_copies(doc)
            if valid:
                entry = doc.setdefault(model, {})
                entry.update(valid)
                entry[MIGRATED_KEY] = dict(valid)
            if json.dumps(doc, sort_keys=True) != before:
                _store(provider_id, doc)
    return refused
