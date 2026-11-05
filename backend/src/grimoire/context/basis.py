"""The selection a pack is built over (031), so a brief can refuse a pack from another chat or snapshot.

A pack is disposable and cheap; reusing one across chats or across an attach, detach or re-pin is
the mistake this guards. So is reusing one across a **hand edit**: user-owned definition files may
be edited in place (docs/STORAGE.md), which changes a revision's content but not its reference,
so an entry reclassified from ``common`` to ``director`` would otherwise still show, and count as
exposed, from a pack built before the edit. The packer records the basis of what it read, content
revisions included, and a consumer computes the basis of the snapshot it is about to render for
and compares the two.
"""

from __future__ import annotations

import hashlib

from grimoire.core.entity import EntityError
from grimoire.core.identity import InstanceRef, SourceRef, validate_segment
from grimoire.core.index import MissingDefinition
from grimoire.core.portrayal import PortrayalError
from grimoire.core.snapshot import EffectiveSnapshot, Resolution

from .lore import LORE_ROLES
from .model import ContextError, SelectionBasis


def _content(snapshot: EffectiveSnapshot, ref: SourceRef, role: str, etag: str | None = None) -> str:
    """A revision's content identity: its stored etag, else a digest of the definition as indexed.

    A definition with no etag (one built in memory) still gets a fingerprint, so an edited copy at
    the same reference never reads as the same content. The digest is of ``repr``: a value that does
    not repr stably can only make two equal contents differ, which refuses a pack, never admits one.
    """
    found = etag if etag is not None else snapshot.etag(ref, role)
    if found is not None:
        return found
    raw = repr(snapshot.resolve(ref, role)).encode("utf-8", "surrogatepass")
    return "repr-sha256:" + hashlib.sha256(raw).hexdigest()


def _contents(snapshot: EffectiveSnapshot, resolution: Resolution) -> tuple[str, ...]:
    """The content identities a pack read through one binding: a lore source's selected revision, or
    every layer an actor's portrayal composes (an ancestor's hand edit changes the card too)."""
    role, resolved = resolution.source.role, resolution.resolved
    if resolved is None:
        return ()
    if role in LORE_ROLES:
        return (_content(snapshot, resolved, role),)
    try:
        chain = snapshot.effective(resolved, role).chain
    except (PortrayalError, MissingDefinition, EntityError):
        return (_content(snapshot, resolved, role),)  # the packer refuses such an actor anyway
    return tuple(_content(snapshot, node.ref, role, node.etag) for node in chain)


def selection_basis(snapshot: EffectiveSnapshot, campaign_id: str, actor: InstanceRef | None = None
                    ) -> SelectionBasis:
    """The ``SelectionBasis`` a pack built over ``snapshot`` for ``campaign_id`` (and ``actor``) has.

    Every lore binding counts, whatever it held, and for an actor its own bindings and its captured
    instance's etag: exactly the inputs ``pack_actor_context`` and ``director_lore`` read from the
    snapshot, each with the content revisions read through it. Other actors' bindings do not count,
    since no pack reads them. ``actor`` must be captured in ``snapshot`` (``unknown_instance`` from
    the snapshot otherwise) and be of the campaign (``campaign_mismatch``).
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
    bindings = sorted(((r.source.role, r.source.ref, r.resolved, _contents(snapshot, r)) for r in snapshot.sources()
                       if r.source.role in LORE_ROLES or (actor is not None and r.source.actor == actor)),
                      key=lambda binding: (binding[0], str(binding[1]), str(binding[2]), binding[3]))
    etag = snapshot.instance_etag(actor) if actor is not None else None
    return SelectionBasis(campaign_id, actor, tuple(bindings), etag)


__all__ = ["selection_basis"]
