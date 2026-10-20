"""ledger: movement responsibilities."""

from __future__ import annotations

from ... import store
from ...store.continuity import effective as continuity_effective
from ._router import router as router

# ------------------------------------------------------------- moving a record


def _stored_scene(record) -> str:
    """A record's stored ``last_scene``, as the PUT routes have always passed it
    back to `set_movement` when the payload names none."""
    return (record or {}).get("last_scene", "") if isinstance(record, dict) else ""


def _closure_scene(stored: str, scene: str | None, keep_later: bool) -> str | None:
    """The ``last_scene`` a move writes: None (the beat's own scene) unless a
    closure asked to keep the later of the evidence scene and the stored one."""
    if keep_later and scene is not None:
        return continuity_effective.later_scene(stored, scene)
    return None


def move_thread(cid: str, pid: str, *, title: str = "", status: str = "", beat: str = "",
                scene: str | None = None, keep_later_scene: bool = False,
                label: str) -> None:
    """Move one thread -- the physical record `pid`, no redirect -- in one
    journalled write. The caller holds `campaign_lock(cid)`.

    The ledger's PUT and the continuity review's closure both come through
    here, so a hand edit to a thread still has exactly one home (capstone
    §12.4). ``title=""`` and an unknown ``status`` keep what is stored, as
    `plot.set_movement` reads them. ``scene=None`` keeps the stored
    ``last_scene``. ``keep_later_scene`` is the closure's rule: the beat is
    filed under the evidence scene the reader chose, but ``last_scene`` stays
    at the later of that scene and the stored one by play order. Both land in
    the ONE write `set_movement` makes, so a failure leaves nothing behind --
    a second write to put ``last_scene`` forward could fail after the first
    had moved it backwards, outside any journal row.
    """
    stored = _stored_scene(store.plot.get(cid, pid))
    with store.undo.journalled(cid, {"w": "plot", "id": pid},
                               kind="plot", ref={"kind": "plot", "id": pid},
                               field="thread", label=label):
        store.plot.set_movement(cid, pid, title, status, beat,
                                scene if scene is not None else stored,
                                last_scene=_closure_scene(stored, scene, keep_later_scene))


def move_commitment(cid: str, mid: str, *, title: str = "", kind: str = "",
                    status: str = "", due: str | None = None, beat: str = "",
                    scene: str | None = None, keep_later_scene: bool = False,
                    label: str) -> None:
    """`move_thread` for a commitment. ``kind=""`` keeps the stored kind, and
    `due` is three-valued as `commitments.set_movement` documents: None keeps
    the stored deadline, ``""`` clears it, text sets it -- which is how the
    review's explicit due copy lands as its own journalled row (§5.1)."""
    stored = _stored_scene(store.commitments.get(cid, mid))
    with store.undo.journalled(cid, {"w": "commitment", "id": mid},
                               kind="commitment", ref={"kind": "commitment", "id": mid},
                               field="commitment", label=label):
        store.commitments.set_movement(cid, mid, title, kind, status, due, beat,
                                       scene if scene is not None else stored,
                                       last_scene=_closure_scene(stored, scene,
                                                                 keep_later_scene))
