"""Read-only membership projections in either entity scope (spec 07)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..store import group_refs, membership
from . import common, models

router = APIRouter()


def _validated_actor(actor: str | None) -> str | None:
    if actor is None:
        return None
    parsed = group_refs.actor_ref(actor)
    if parsed is None:
        raise HTTPException(status_code=400, detail="actor must be a characters:<id> or pcs:<id> reference")
    return parsed


def _response(index: membership.Index, actor: str | None
              ) -> models.MembershipResponse | models.ActorMembershipResponse:
    if actor is None:
        return models.MembershipResponse(digest=index.digest, groups=tuple(index.groups.values()), by_actor=index.by_actor)
    return models.ActorMembershipResponse(actor=actor, groups=tuple(
        models.ActorGroup(ref=entry.group, name=index.groups[entry.group].name,
            secrecy=index.groups[entry.group].secrecy, roles=entry.roles)
        for entry in membership.groups_for(index, actor)))


@router.get("/worlds/{wid}/membership", response_model=models.MembershipResponse | models.ActorMembershipResponse)
def world_membership(wid: str, actor: str | None = None):
    root = common._world_root_or_404(wid)
    parsed = _validated_actor(actor)
    return _response(membership.world_index(root, store_root=root.parent.parent), parsed)


@router.get("/campaigns/{cid}/membership", response_model=models.MembershipResponse | models.ActorMembershipResponse)
def campaign_membership(cid: str, actor: str | None = None):
    common._campaign_root_or_404(cid)
    parsed = _validated_actor(actor)
    return _response(membership.campaign_index(cid), parsed)
