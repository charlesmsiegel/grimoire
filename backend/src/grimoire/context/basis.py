"""The selection a pack is built over (031), so a brief can refuse a pack from another chat or snapshot.

A pack is disposable and cheap; reusing one across chats or across an attach, detach or re-pin is
the mistake this guards. The packer records the basis of what it read, and a consumer computes the
basis of the snapshot it is about to render for and compares the two.
"""

from __future__ import annotations

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.snapshot import EffectiveSnapshot

from .lore import LORE_ROLES
from .model import ContextError, SelectionBasis


def selection_basis(snapshot: EffectiveSnapshot, campaign_id: str, actor: InstanceRef | None = None
                    ) -> SelectionBasis:
    """The ``SelectionBasis`` a pack built over ``snapshot`` for ``campaign_id`` (and ``actor``) has.

    Every lore binding counts, whatever it held, and for an actor its own bindings and its captured
    instance's etag: exactly the inputs ``pack_actor_context`` and ``director_lore`` read from the
    snapshot. Other actors' bindings do not count, since no pack reads them. ``actor`` must be
    captured in ``snapshot`` (``unknown_instance`` from the snapshot otherwise) and be of the
    campaign (``campaign_mismatch``).
    """
    if not isinstance(snapshot, EffectiveSnapshot):
        raise TypeError(f"expected an EffectiveSnapshot, not {type(snapshot).__name__}")
    try:
        validate_segment(campaign_id, "campaign_id")
    except ValueError:
        raise ContextError("campaign_id must be a campaign id segment", "invalid_campaign") from None
    if actor is not None:
        if not isinstance(actor, InstanceRef):
            raise ContextError("the audience must be an InstanceRef", "invalid_audience")
        if actor.campaign_id != campaign_id:
            raise ContextError("the audience belongs to another campaign", "campaign_mismatch")
    bindings = sorted(((r.source.role, r.source.ref, r.resolved) for r in snapshot.sources()
                       if r.source.role in LORE_ROLES or (actor is not None and r.source.actor == actor)),
                      key=lambda binding: (binding[0], str(binding[1]), str(binding[2])))
    etag = snapshot.instance_etag(actor) if actor is not None else None
    return SelectionBasis(campaign_id, actor, tuple(bindings), etag)


__all__ = ["selection_basis"]
