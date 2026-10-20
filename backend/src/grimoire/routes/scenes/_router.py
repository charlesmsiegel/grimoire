"""One eager router; restore its original order before the facade exposes it."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# FastAPI matches in declaration order. Responsibilities interleave in the
# original module, so leaf import order cannot preserve that contract.
# These are the original (methods, path, endpoint name) keys, not new routes.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('GET',), '/campaigns/{cid}/scenes', 'get_scenes'),
    (('POST',), '/campaigns/{cid}/scenes', 'post_scene'),
    (('POST',), '/campaigns/{cid}/scenes/import/parse', 'post_scene_import_parse'),
    (('POST',), '/campaigns/{cid}/scenes/import', 'post_scene_import'),
    (('POST',), '/campaigns/{cid}/scene-suggestions', 'post_scene_suggestions'),
    (('POST',), '/campaigns/{cid}/scene-intent', 'post_scene_intent'),
    (('GET',), '/campaigns/{cid}/scene-ideas', 'get_scene_ideas'),
    (('POST',), '/campaigns/{cid}/scene-ideas', 'post_scene_idea'),
    (('PUT',), '/campaigns/{cid}/scene-ideas/{lid}', 'put_scene_idea'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}', 'get_scene'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/rewrites', 'get_scene_rewrites'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/branch', 'post_branch'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}', 'put_scene'),
    (('DELETE',), '/campaigns/{cid}/scenes/{sid}', 'delete_scene'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/chat', 'post_chat'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/retry', 'post_retry'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/regenerate', 'post_regenerate'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/alternates', 'get_scene_alternates'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/alternates/{vid}', 'post_scene_alternate'),
    (('GET',), '/campaigns/{cid}/chronicle', 'get_chronicle'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/absorb', 'post_absorb'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/pending-review', 'get_pending_review'),
    (('DELETE',), '/campaigns/{cid}/scenes/{sid}/pending-review', 'delete_pending_review'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/audit', 'post_audit'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/rolling-summary', 'get_rolling_summary'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/rolling-summary', 'post_rolling_summary'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/scene-break', 'get_scene_break'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/scene-break', 'post_scene_break'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/scene-break/dismiss', 'post_scene_break_dismiss'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/dossiers', 'post_dossiers'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/chronicle', 'put_chronicle'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/briefing', 'get_scene_briefing'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/cast', 'get_scene_cast'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/cast', 'post_scene_cast'),
    (('DELETE',), '/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}', 'delete_scene_cast'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/cast/batch', 'post_scene_cast_batch'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/cast/emergent', 'post_emergent_cast'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/cast-changes', 'get_cast_changes'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/suggestions', 'get_scene_suggestions'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/suggestions/dismiss', 'post_dismiss'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/location', 'get_scene_location'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/location', 'put_scene_location'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/datetime', 'get_scene_datetime'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/datetime', 'put_scene_datetime'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/response', 'get_scene_response'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/response', 'put_scene_response'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/context', 'get_scene_context'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/prompts', 'get_scene_prompts'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/prompts/{eid}', 'get_scene_prompt'),
    (('GET',), '/campaigns/{cid}/prompts', 'get_campaign_prompts'),
    (('GET',), '/campaigns/{cid}/prompts/{eid}', 'get_campaign_prompt'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/prompts/{eid}/diff', 'get_scene_prompt_diff'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}/casefile', 'get_cast_casefile'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}', 'get_cast_detail'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/messages/{index}', 'put_scene_message'),
    (('PUT',), '/campaigns/{cid}/scenes/{sid}/messages/{index}/excluded', 'put_scene_message_excluded'),
    (('DELETE',), '/campaigns/{cid}/scenes/{sid}/messages/{index}', 'delete_scene_messages_from'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/messages/{index}/retcon', 'post_scene_retcon'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/replay/preview', 'get_replay_preview'),
    (('GET',), '/campaigns/{cid}/scenes/{sid}/replay', 'get_replay'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/replay', 'post_replay'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/replay/turn', 'post_replay_turn'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/replay/accept', 'post_replay_accept'),
    (('POST',), '/campaigns/{cid}/scenes/{sid}/replay/cancel', 'post_replay_cancel'),
)


def _route_key(route: APIRoute) -> tuple[tuple[str, ...], str, str]:
    return tuple(sorted(route.methods or ())), route.path, route.name


def finalize() -> None:
    """Order the original route objects in place, refusing an incomplete roster."""
    # This router contains only HTTP declarations, never mounts or websockets.
    routes = cast(list[APIRoute], router.routes)
    keys = [_route_key(route) for route in routes]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate route key during package initialization")
    expected, actual = set(_ORDER), set(keys)
    if actual != expected:
        raise RuntimeError(f"route roster moved: missing={expected - actual!r}, "
                           f"extra={actual - expected!r}")
    positions = {key: index for index, key in enumerate(_ORDER)}
    ordered = sorted(routes, key=lambda route: positions[_route_key(route)])
    router.routes[:] = ordered
