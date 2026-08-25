"""Append-only per-campaign relationship timeline at
``<campaign>/relationship_history.json``: every feeling and bond delta that
landed, the scene that drove it, and the standing it replaced (#63).

``relationships.json`` holds only where two people stand *now* --
``set_feeling`` and ``set_bond`` overwrite their key in place -- so the arc that
got them there was thrown away the moment it was applied. That arc is the thing
a reader wants: "she has trusted him less every scene since the crypt" is a
sentence the current-value store cannot say, and no amount of reading it twice
will recover it.

Shape, per entry::

    {"id": "rh12", "ts": ..., "scene": "003--the-crypt", "source": "absorb"|"undo",
     "kind": "feeling"|"bond", "a": "characters:mara", "b": "pcs:seraphine",
     "label": "Mara → Seraphine", "before": "...", "after": "..."}

``a`` and ``b`` are actor tokens (``"<kind>:<id>"``), and their *order carries
meaning for a feeling and none for a bond* -- the same asymmetry
``relationships.feeling_key`` and ``relationships.bond_key`` encode. So the
entry keeps the pair unjoined rather than storing either key: a reader asking
"what has passed between these two" wants both directions and the bond, and
splitting a key back apart to answer that is a parse of a string this module
never had to build. `for_pair` compares the pair as a set for exactly that
reason.

``before``/``after`` are the rendered standings the reviewer approved
(``relationships._render_feeling`` for a feeling, the bond type for a bond) --
display text, like ``journal.py``'s. Nothing reads them back as values; the
current value lives in ``relationships.json``, and this file is the account of
how it got there.

**Append-only, following ``chronicle.append_timeline`` rather than
``changes.py``.** A row is never rewritten and never dropped when a later delta
lands on the same pair -- that overwrite is precisely the loss this exists to
stop. Two consequences worth stating:

- A reversal (``store/undo.py``) appends a row of its own rather than deleting
  the one it put back. "This was undone" is part of the history, and a ledger
  that quietly loses its last entry is one nobody can reconcile against
  ``relationships.json``.
- A cut scene keeps its rows, like ``journal.py`` and unlike
  ``changes.forget_scene``. That log is rolling, so a row describing a reverted
  write had no earlier row to fall back on and *had* to go; here the earlier
  rows are all still present, and the reversal appended beside them says what
  happened. ``scene_refs.repoint`` keeps the ``scene`` field pointing at the
  right scene while it exists.

Retention is bounded the two ways ``journal.py``'s is, and for a weaker version
of the same reason. A row here is small by construction -- two rendered meters,
two tokens, a scene id -- with one exception: the ``note`` on a feeling is
model-authored and nothing upstream caps it. So a row cap alone bounds nothing,
and ``MAX_BYTES`` is what actually binds. The caps are higher than the journal's
because the rows are smaller and the value of an old one is greater: the point
of a timeline is its far end.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, locks
from .campaigns import paths as campaigns_paths
from .paths import now_iso

#: How many entries are kept. Everything older is dropped on the next append.
RETENTION = 2000

#: And how many bytes they may occupy once serialized -- the cap that actually
#: binds, since a feeling's `note` is model-authored and uncapped upstream.
MAX_BYTES = 1_000_000

#: The two shapes a row can describe, normalized off the staged-edit kinds
#: (`relationship` and `bond`) at the writer. "feeling" rather than
#: "relationship" because the file is *about* relationships: a `kind` that
#: repeats the noun distinguishes nothing.
KINDS: tuple[str, ...] = ("feeling", "bond")


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "relationship_history.json"


def _high_water(entries: list[dict]) -> int:
    """The largest id in `entries`, as a number. Ids are ``rh<n>``; anything
    else is a hand edit and contributes nothing rather than raising."""
    best = 0
    for entry in entries:
        rid = entry.get("id")
        if isinstance(rid, str) and rid.startswith("rh") and rid[2:].isdigit():
            best = max(best, int(rid[2:]))
    return best


def _load(cid: str) -> dict:
    """The whole document, normalized. Tolerant of a garbled or hand-edited file
    for the reason `journal._load` is: this backs a display view, and one bad
    byte must cost the timeline rather than the page.

    `seq` is taken as the HIGHER of what the file claims and what its entries
    show, so a truncated `seq` cannot hand out an id already in use -- the one
    thing a React key and a reader citing a row both depend on.
    """
    empty: dict = {"seq": 0, "entries": []}
    p = _path(cid)
    if not p.exists():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty
    if not isinstance(data, dict):
        return empty
    raw = data.get("entries")
    entries = [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    seq = data.get("seq")
    seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else 0
    return {"seq": max(seq, _high_water(entries)), "entries": entries}


def read(cid: str) -> list[dict]:
    """Every recorded delta, oldest first."""
    return _load(cid)["entries"]


def for_pair(cid: str, a: str, b: str) -> list[dict]:
    """Everything that has passed between two actors, oldest first.

    The pair is matched UNORDERED even though a feeling is directed: "what
    stands between these two" is one question, and answering it with only A's
    half leaves the reader to ask it again backwards. A directed row still says
    which way it ran -- that is what `a` and `b` are for.

    A row whose tokens are not both strings matches nothing rather than raising:
    this file is hand-editable, and a dict where a token belongs is unhashable
    -- comparing it as part of a set would turn one garbled row into a 500 for
    the whole request, which is not the trade every other reader here makes.
    """
    want = {a, b}
    return [e for e in read(cid)
            if isinstance(e.get("a"), str) and isinstance(e.get("b"), str)
            and {e["a"], e["b"]} == want]


def _write(cid: str, doc: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(doc, indent=2) + "\n")


def row(kind: str, a: str, b: str, *, label: str, before: str, after: str,
        scene: str = "", source: str = "absorb") -> dict:
    """One entry, minus the id and timestamp `append` stamps on.

    A constructor rather than a dict literal at each of the two call sites: they
    are in different modules (`absorb.apply` and `store.undo`), and a field one
    of them forgot would be a row the view renders as a blank rather than an
    error anybody sees.
    """
    return {"scene": scene, "source": source, "kind": kind, "a": a, "b": b,
            "label": label, "before": before, "after": after}


def append(cid: str, rows: list[dict]) -> list[dict]:
    """Append each row, stamped with a fresh id and timestamp. Returns what was
    written. No-op for an empty list.

    Takes the campaign lock for `journal.append`'s reason: this is a
    read-modify-write of one whole file, and the append that loses the race
    loses the only record of a standing that has already changed.
    """
    written: list[dict] = []
    if not rows:
        return written
    with locks.campaign_lock(cid):
        doc = _load(cid)
        ts = now_iso()
        for entry in rows:
            doc["seq"] += 1
            # The allocated id and stamp go LAST, so a row carrying its own
            # `id` cannot take one already in use.
            stamped = {**entry, "id": f"rh{doc['seq']}", "ts": ts}
            doc["entries"].append(stamped)
            written.append(stamped)
        doc["entries"] = _trim(doc["entries"], keep=len(written))
        _write(cid, doc)
    # Outside the hold, and `written` is bound before it for the same reason:
    # `campaign_lock` is a @contextmanager, so its `__exit__` is typed as able
    # to swallow -- a return that only exists inside the block is one mypy
    # cannot prove happens.
    return written


def _trim(entries: list[dict], keep: int) -> list[dict]:
    """Drop the oldest entries until both caps hold, never below `keep`.

    `keep` is what this append just wrote, and the floor is `journal._trim`'s:
    one absorb can carry more relationship deltas than either cap allows, and
    trimming to satisfy the cap would then delete the rows for the writes that
    just happened. The caps bound accumulation, not the present.

    Sizes are measured once per entry rather than by re-serializing the list on
    each pop, which would be quadratic in exactly the case that reaches here.
    """
    room = max(RETENTION, keep)
    entries = entries[-room:] if len(entries) > room else entries
    sizes = [len(json.dumps(e, default=str)) for e in entries]
    total, first = sum(sizes), 0
    while total > MAX_BYTES and len(entries) - first > keep:
        total -= sizes[first]
        first += 1
    return entries[first:]


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids in each entry's scene field."""
    with locks.campaign_lock(cid):
        doc = _load(cid)
        hit = False
        for entry in doc["entries"]:
            if entry.get("scene") in mapping:
                entry["scene"] = mapping[entry["scene"]]
                hit = True
        if hit:
            _write(cid, doc)
