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
and it is read on the path of a turn. A write replaces a mangled file. Writes
are read-merge-write under one module lock, because two probes of different
models on one provider would otherwise each rewrite the file from a copy that
lacks the other's result. Global to the provider, so it takes no campaign lock.
"""

from __future__ import annotations

import json
import threading

from .. import atomic, llm_connections
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


def _store(provider_id: str, doc: dict[str, dict]) -> None:
    p = llm_connections.facts_path(provider_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(doc, indent=2) + "\n")


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
    entry = _load(provider_id).get(model, {})
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
    deleted connection. The compare and the write are one step, under
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
    with llm_connections.LOCK:
        try:
            current = llm_connections.read_connection_raw(provider_id)["rev"]
        except llm_connections.ConnectionNotFound:
            return False
        if not rev or current != rev:
            return False
        with _lock:
            doc = _load(provider_id)
            entry = doc.setdefault(model, {})
            old = entry.get("verified")
            caps = (_clean_results(old.get("caps"))
                    if isinstance(old, dict) and old.get("rev") == rev else {})
            caps.update(stamped)
            entry["verified"] = {"rev": rev, "caps": caps}
            _store(provider_id, doc)
    return True


def set_overrides(provider_id: str, model: str, overrides: dict[str, str]) -> None:
    """Replace the model's overrides (`{cap: "yes"|"no"}`); an empty dict clears."""
    _require_safe(provider_id)
    for cap, value in overrides.items():
        if cap not in CAPABILITIES:
            raise ValueError(f"unknown capability: {cap!r}")
        if value not in OVERRIDE_VALUES:
            raise ValueError(f"override for {cap!r} must be 'yes' or 'no'")
    with _lock:
        doc = _load(provider_id)
        entry = doc.setdefault(model, {})
        if overrides:
            entry["overrides"] = dict(overrides)
        else:
            entry.pop("overrides", None)
            if not entry:
                doc.pop(model, None)
        _store(provider_id, doc)
