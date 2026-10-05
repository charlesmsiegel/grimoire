"""Publishing a reviewed new record to the world, as a step of the chronicle
commit (spec §9.4).

A `new_lore` or `new_location` row the reviewer marked *World library* is
created campaign-local by `apply_edits`, exactly as any other, and then handed
to `sync.promote`. That is one more non-idempotent write in `PUT /chronicle`,
so it is journalled like the others (`store/commits.py`, #271) rather than
called after the commit has recorded: a response lost after promote wrote the
world file would otherwise send the retry into promote again, and promote would
refuse the commit's own record as somebody else's.

The journal lives at ``progress["publish"][str(i)]`` -- the edit's position,
for the reason `apply_edits` keys its outcomes by position. ``"pending"`` is
written before promote is called, and the outcome after it returns.

A publish that fails never fails the save. The record is already in the
campaign, which is where the reviewer's change was always going to land first,
and the response says why it went no further.

Not listed in `locks.DOMAIN_MODULES`, for the reason `store.tracker.walk` is
not: every write here goes through `sync.promote`, so the lock-domain guard
does not count this module as a mutator. It takes the lock all the same.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeGuard

from .. import locks, sync

#: The staged kinds whose review row offers a destination (spec §9.2).
#: `new_character` creates an actor, whose promotion is a different move with
#: a different review, and is deliberately not offered one.
PUBLISHABLE = frozenset({"new_lore", "new_location"})


def _wants_world(e) -> bool:
    if not isinstance(e, dict) or e.get("kind") not in PUBLISHABLE:
        return False
    payload = e.get("payload")
    return isinstance(payload, dict) and payload.get("destination") == "world"


def _created(outcome) -> tuple[str, str] | None:
    """The record an applied slot created, or None when it created nothing."""
    if not isinstance(outcome, dict) or outcome.get("state") != "applied":
        return None
    created = outcome.get("created")
    if not isinstance(created, dict):
        return None
    kind, eid = created.get("kind"), created.get("id")
    if not isinstance(kind, str) or not isinstance(eid, str) or not kind or not eid:
        return None
    return kind, eid


def _reason(exc: Exception, e: dict, eid: str) -> str:
    """What the review shows for a publish that did not land.

    The collision gets a sentence of its own because it is the one a reviewer
    can act on, and `PromoteConflictError`'s own message names a ref
    (``the library already has lore/lantern``) rather than the record they
    approved.

    An `OSError` gets a fixed sentence instead of its own text, which carries
    the absolute path into the store -- this reason is shown on screen and
    persisted in the commit journal. Anything else falls back to the class
    name rather than an empty reason.
    """
    if isinstance(exc, sync.PromoteConflictError):
        name = e.get("payload", {}).get("name")
        label = name if isinstance(name, str) and name else eid
        return f"the world already has a record named {label}"
    if isinstance(exc, OSError):
        return "could not write the world record"
    return str(exc) or type(exc).__name__


def _settled(slot) -> TypeGuard[dict]:
    return isinstance(slot, dict) and slot.get("state") in ("published", "failed")


def _step(cid: str, e: dict, kind: str, eid: str, slots: dict, slot: str,
          checkpoint: Callable[[], None] | None) -> dict:
    """Settle one record's publish and journal the outcome. See `publish_created`."""
    attempted = slots.get(slot) is not None
    if not attempted:
        slots[slot] = "pending"
        if checkpoint:
            checkpoint()
    try:
        if not (attempted and sync.promoted_intact(cid, kind, eid)):
            sync.promote(cid, kind, eid)
    except Exception as exc:  # noqa: BLE001 — a publish never fails the save
        outcome = {"state": "failed", "reason": _reason(exc, e, eid)}
    else:
        outcome = {"state": "published"}
    slots[slot] = outcome
    if checkpoint:
        checkpoint()
    return outcome


def publish_created(cid: str, edits: list[dict], progress: dict,
                    checkpoint: Callable[[], None] | None,
                    ) -> tuple[list[dict], list[dict]]:
    """Promote every record this commit created for the world. Returns
    ``(published, failed)``: ``[{"kind", "id"}]`` and ``[{"kind", "id", "reason"}]``.

    Runs after `apply_edits` over the same `progress`, and reads which records
    were made from its outcomes -- a row whose create failed or never ran has
    nothing to publish. Each step resolves one of three ways:

    - **settled** in the journal: reused, and promote is not called again;
    - **pending** (attempted, outcome never journalled): `sync.promoted_intact`
      says whether the world already holds exactly what promote would have
      written. If so it is `published`; otherwise promote is called again, and
      its own precondition (no world record under that id) is what turns a
      record somebody else put there into `failed` rather than an overwrite;
    - **unseen**: journalled `pending`, then promoted.

    Under the campaign lock, which `PUT /chronicle` already holds -- it is
    re-entrant, so this and promote's own acquisition cost nothing there.
    """
    slots: dict = progress.setdefault("publish", {})
    outcomes = progress.get("edits")
    outcomes = outcomes if isinstance(outcomes, dict) else {}
    published: list[dict] = []
    failed: list[dict] = []
    with locks.campaign_lock(cid):
        for i, e in enumerate(edits):
            made = _created(outcomes.get(str(i))) if _wants_world(e) else None
            if made is None:
                continue
            kind, eid = made
            prior = slots.get(str(i))
            outcome = (prior if _settled(prior)
                       else _step(cid, e, kind, eid, slots, str(i), checkpoint))
            if outcome["state"] == "published":
                published.append({"kind": kind, "id": eid})
            else:
                failed.append({"kind": kind, "id": eid,
                               "reason": str(outcome.get("reason", ""))})
    return published, failed
