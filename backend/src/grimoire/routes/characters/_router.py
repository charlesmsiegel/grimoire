"""One eager router; preserve original route objects and registration order."""

from typing import cast

from fastapi import APIRouter
from fastapi.routing import APIRoute

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (('GET',), '/worlds/{wid}/characters', 'get_world_characters'),
    (('POST',), '/worlds/{wid}/characters', 'post_world_character'),
    (('GET',), '/worlds/{wid}/characters/chub-unlinked', 'get_world_characters_chub_unlinked'),
    (('POST',), '/worlds/{wid}/characters/parse-text', 'post_character_parse_text'),
    (('POST',), '/worlds/{wid}/characters/refine-text', 'post_character_refine_text'),
    (('GET',), '/worlds/{wid}/characters/{cid}', 'get_world_character'),
    (('PUT',), '/worlds/{wid}/characters/{cid}', 'put_world_character'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/name', 'put_world_character_name'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/birthdate', 'put_world_character_birthdate'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/tags', 'put_world_character_tags'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source', 'post_world_character_chub_source'),
    (('DELETE',), '/worlds/{wid}/characters/{cid}/versions/{vid}/chub-source', 'delete_world_character_chub_source'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/chub-gallery', 'post_world_character_chub_gallery'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/chub-lorebooks', 'post_world_character_chub_lorebooks'),
    (('DELETE',), '/worlds/{wid}/characters/{cid}', 'delete_world_character'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions', 'post_world_version'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/versions/{vid}', 'put_world_version'),
    (('DELETE',), '/worlds/{wid}/characters/{cid}/versions/{vid}', 'delete_world_version'),
    (('GET',), '/worlds/{wid}/characters/{cid}/tagline', 'get_character_tagline'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/tagline', 'put_character_tagline'),
    (('POST',), '/worlds/{wid}/characters/{cid}/tagline/generate', 'post_character_tagline_generate'),
    (('POST',), '/worlds/{wid}/characters/taglines/generate', 'post_world_taglines_generate'),
    (('GET',), '/worlds/{wid}/characters/{cid}/voice-anchor', 'get_character_voice_anchor'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/voice-anchor', 'put_character_voice_anchor'),
    (('POST',), '/worlds/{wid}/characters/{cid}/voice-anchor/generate', 'post_character_voice_anchor_generate'),
    (('POST',), '/worlds/{wid}/characters/import', 'post_character_import'),
    (('POST',), '/worlds/{wid}/characters/import/chub', 'post_character_import_chub'),
    (('GET',), '/worlds/{wid}/characters/{cid}/versions/{vid}/export', 'get_character_export'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/localize', 'post_character_localize'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import', 'post_character_lorebook_import'),
    (('GET',), '/worlds/{wid}/images/undescribed', 'list_undescribed_images'),
    (('GET',), '/worlds/{wid}/gallery', 'list_world_gallery'),
    (('GET',), '/campaigns/{cid}/gallery', 'list_campaign_gallery'),
    (('GET',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images', 'list_world_images'),
    (('GET',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}', 'get_world_image'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}', 'put_world_image'),
    (('DELETE',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}', 'delete_world_image'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote', 'promote_world_image'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus', 'put_world_avatar_focus'),
    (('POST',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/description/draft', 'post_world_image_description_draft'),
    (('PUT',), '/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/description', 'put_world_image_description'),
)


def _route_key(route: APIRoute) -> tuple[tuple[str, ...], str, str]:
    return tuple(sorted(route.methods or ())), route.path, route.name


def finalize() -> None:
    """Refuse an incomplete roster before reordering the same route list."""
    # This router contains HTTP declarations, never mounts or websockets.
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
