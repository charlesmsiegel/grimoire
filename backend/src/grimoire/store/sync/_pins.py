"""sync: pins responsibilities."""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic, locks
from ..campaigns import paths as campaigns_paths
from . import _read_shapes

# ---------------------------------------------------------------------------
# Per-ref pins and the composition overview (#71)
# ---------------------------------------------------------------------------
#
# A pin freezes one ref against the sync engine: the campaign stops being
# offered world updates for it without rejecting each one (a reject advances
# the base; a pin holds it still, so unpinning restores exactly the offer that
# was waiting). Stored at <campaign>/sync_pins.json as {"<kind>/<id>": true} --
# a sidecar rather than a value in sync.md, which stays pure hashes. Not to be
# confused with store/pins.py (#129), which pins records into the *prompt*;
# this pins a ref against the *library*.


def _pins_path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "sync_pins.json"


def pinned_refs(cid: str) -> set[str]:
    """Every pinned ref, as "<kind>/<id>" strings.

    A damaged or malformed file reads as no pins at all, deliberately: the
    fail-open direction here is "updates are offered again", which loses a
    convenience, where fail-closed would silently freeze refs nobody chose to.
    """
    p = _pins_path(cid)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, dict):
        return set()
    return {ref for ref, v in data.items() if v is True}


def set_pin(cid: str, kind: str, eid: str, pin: bool) -> set[str]:
    """Pin or unpin one ref, returning the pinned set as it now stands.

    A pure filter over the engine: nothing here touches sync.md or a base
    hash, which is what keeps accept/reject working unchanged after an unpin.
    """
    ref = _read_shapes._ref_str(kind, eid)
    with locks.campaign_lock(cid):
        pins = dict.fromkeys(pinned_refs(cid), True)
        if pin:
            pins[ref] = True
        else:
            pins.pop(ref, None)
        atomic.write_text(_pins_path(cid), json.dumps(pins, indent=2, sort_keys=True) + "\n")
    return set(pins)
