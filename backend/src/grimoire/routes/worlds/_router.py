"""One eager router; preserve original route objects and registration order."""

from fastapi import APIRouter

from .._route_order import RouteKey, order_routes

router = APIRouter()

# These original keys order the existing APIRoutes, never rebuild them.
_ORDER: tuple[RouteKey, ...] = (
    (('GET',), '/worlds', 'get_worlds'),
    (('POST',), '/worlds', 'post_world'),
    (('GET',), '/worlds/{wid}', 'get_world'),
    (('PUT',), '/worlds/{wid}', 'put_world'),
    (('DELETE',), '/worlds/{wid}', 'delete_world'),
    (('POST',), '/worlds/{wid}/fork', 'post_world_fork'),
    (('GET',), '/worlds/{wid}/campaigns', 'get_world_campaigns'),
    (('GET',), '/worlds/{wid}/export.zip', 'get_world_export'),
    (('POST',), '/worlds/import', 'post_world_import'),
    (('PUT',), '/worlds/{wid}/module', 'put_world_module'),
    (('GET',), '/worlds/{wid}/sheets', 'get_world_sheets_index'),
    (('GET',), '/worlds/{wid}/sheets/{mid}', 'get_world_sheets'),
    (('GET',), '/worlds/{wid}/sheets/{mid}/{kind}/{eid}', 'get_world_sheet'),
    (('PUT',), '/worlds/{wid}/sheets/{mid}/{kind}/{eid}', 'put_world_sheet'),
    (('PUT',), '/worlds/{wid}/sheets/{mid}/{kind}/{eid}/creation', 'put_world_sheet_creation'),
    (('DELETE',), '/worlds/{wid}/sheets/{mid}/{kind}/{eid}', 'delete_world_sheet'),
    (('POST',), '/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}', 'post_world_instantiate'),
    (('GET',), '/worlds/{wid}/tags', 'get_world_tags'),
    (('POST',), '/worlds/{wid}/tags', 'post_world_tag'),
    (('PUT',), '/worlds/{wid}/tags/{tid}', 'put_world_tag'),
    (('DELETE',), '/worlds/{wid}/tags/{tid}', 'delete_world_tag'),
    (('GET',), '/worlds/{wid}/pcs', 'get_world_pcs'),
    (('GET',), '/worlds/{wid}/campaign-pc-name', 'get_campaign_pc_name'),
    (('POST',), '/worlds/{wid}/pcs', 'post_world_pc'),
    (('GET',), '/worlds/{wid}/pcs/{pid}', 'get_world_pc'),
    (('PUT',), '/worlds/{wid}/pcs/{pid}', 'put_world_pc'),
    (('DELETE',), '/worlds/{wid}/pcs/{pid}', 'delete_world_pc'),
    (('POST',), '/worlds/{wid}/pcs/{pid}/versions', 'post_pc_version'),
    (('PUT',), '/worlds/{wid}/pcs/{pid}/versions/{vid}', 'put_pc_version'),
    (('GET',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/revisions', 'get_pc_revisions'),
    (('GET',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/revisions/{rid}', 'get_pc_revision'),
    (('POST',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/revisions/{rid}/restore', 'post_pc_revision_restore'),
    (('DELETE',), '/worlds/{wid}/pcs/{pid}/versions/{vid}', 'delete_pc_version'),
    (('GET',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images', 'list_world_pc_images'),
    (('GET',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}', 'get_world_pc_image'),
    (('PUT',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}', 'put_world_pc_image'),
    (('DELETE',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}', 'delete_world_pc_image'),
    (('POST',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/promote', 'promote_world_pc_image'),
    (('PUT',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/avatar/focus', 'put_world_pc_avatar_focus'),
    (('POST',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/description/draft', 'post_world_pc_image_description_draft'),
    (('PUT',), '/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}/description', 'put_world_pc_image_description'),
    (('POST',), '/worlds/{wid}/lorebook/parse', 'post_lorebook_parse'),
    (('POST',), '/worlds/{wid}/lorebook/import', 'post_lorebook_import'),
    (('POST',), '/worlds/{wid}/scenario/parse', 'post_scenario_parse'),
    (('POST',), '/worlds/{wid}/scenario/parse-url', 'post_scenario_parse_url'),
    (('POST',), '/worlds/{wid}/scenario/import', 'post_scenario_import'),
    (('GET',), '/worlds/{wid}/calendar', 'get_world_calendar_config'),
    (('PUT',), '/worlds/{wid}/calendar', 'put_world_calendar_config'),
    (('GET',), '/worlds/{wid}/calendar/months', 'get_world_calendar_months'),
)


def finalize() -> None:
    """Refuse an incomplete roster before reordering the same route list."""
    order_routes(router, _ORDER)
