"""Campaign routes, grouped by domain with stable endpoint imports.

Import order is route-registration order. FastAPI matches first-fit;
moving this registration order can silently change matched routes.
"""

from ._router import router
from ._names import _record_name

from ._settings import (
    get_campaign_settings,
    put_campaign_settings,
    get_group_state,
    put_group_state,
    get_campaign_climate,
    put_campaign_climate,
    get_campaigns,
    get_calendar_config,
    put_calendar_config,
    _clock_friendly,
    get_campaign_clock,
    post_advance_preview,
    post_advance,
)

from ._calendar import (
    _event_provider,
    get_campaign_events,
    post_campaign_event,
    put_campaign_event,
    post_campaign_event_unfire,
    delete_campaign_event,
    get_campaign_notices,
    post_campaign_notices,
    post_campaign_notices_forget,
)

from ._core import (
    get_calendar_months,
    post_campaign,
    get_campaign,
    export_campaign_epub,
    export_campaign_markdown,
    export_campaign_html,
    export_campaign_text,
    export_campaign_json,
)

from ._images import (
    get_campaign_cover,
    put_campaign_cover,
    delete_campaign_cover,
    post_campaign_library_description_draft,
    put_campaign_library_image_description,
    list_campaign_library,
    list_campaign_undescribed_images,
    _campaign_record_name_and_versions,
    _campaign_image_url,
    get_campaign_library_image,
    put_campaign_library_image,
    post_restore_campaign_library_image,
    delete_campaign_library_image,
)

from ._management import (
    put_campaign,
    post_campaign_fork,
    delete_campaign,
    get_campaign_response,
    put_campaign_response,
)

from ._sync import (
    get_incoming,
    post_accept,
    post_reject,
    get_composition,
    put_composition_pin,
    get_changes,
    JOURNAL_PAGE,
    _journal_row,
    get_journal,
    post_journal_undo,
)

from ._ledger import (
    LEDGER_RECENT,
    FEELING_AXES,
    _ledger_text,
    _ledger_relationships,
    RELATIONSHIP_HISTORY_PAGE,
    get_relationship_history,
    get_provenance,
    _effective_or_physical,
    get_ledger,
)

from ._timeline import (
    get_timeline,
    _pin_name,
    _pin_row,
    _pc_name_reader,
    _pin_rows,
    _pin_posts,
    get_pins,
    post_pin,
    delete_pin,
    _absorbed_cast_refs,
)

from ._actors import (
    get_appearances,
    get_campaign_pcs,
    post_campaign_pc,
    _campaign_char_version_or_404,
    get_campaign_image,
    put_campaign_image,
    delete_campaign_image,
    promote_campaign_image,
    put_campaign_avatar_focus,
    put_campaign_image_description,
    post_copy_campaign_image_from_greeting,
    _campaign_wroot,
    get_campaign_characters,
    post_campaign_character,
    get_diverged,
    get_campaign_character,
    get_campaign_voice_anchor,
    put_campaign_voice_anchor,
    post_campaign_voice_anchor_generate,
    _ENTRANCE_LEAD,
    _first_scene_transcript,
    post_campaign_capsule_generate,
    get_campaign_character_tagline,
    _shown_capsule,
    put_campaign_character_tagline,
    put_campaign_character,
    delete_campaign_character,
    delete_campaign_pc,
    put_campaign_character_name,
    put_campaign_character_birthdate,
    put_campaign_character_tags,
    post_campaign_character_version,
    put_campaign_character_version,
    delete_campaign_character_version,
)

from ._pcs import (
    get_campaign_pc,
    put_campaign_pc,
    post_campaign_pc_version,
    put_campaign_pc_version,
    get_campaign_pc_revisions,
    get_campaign_pc_revision,
    post_campaign_pc_revision_restore,
    delete_campaign_pc_version,
    _campaign_pc_version_or_404,
    put_campaign_pc_image_description,
    list_campaign_pc_images,
    get_campaign_pc_image,
    put_campaign_pc_image,
    delete_campaign_pc_image,
    promote_campaign_pc_image,
    put_campaign_pc_avatar_focus,
    post_pick_version,
    post_import_version,
    post_campaign_instantiate,
)

