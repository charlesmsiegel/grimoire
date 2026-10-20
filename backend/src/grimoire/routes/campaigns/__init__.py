"""Campaign routes, grouped by domain with stable endpoint imports.

Import order is route-registration order. FastAPI matches first-fit;
moving this registration order can silently change matched routes.

Theory: the campaign HTTP surface is one ordered router over store-owned
operations. Leaves group responsibilities without owning a second store or
lock domain; a new endpoint belongs with its domain, before any overlapping
pattern. The facade preserves imports, including helpers; assignment patches
must target the owning leaf, because aliases do not share a globals dict.
"""

from __future__ import annotations

import contextlib
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ... import store
from ...llm import LLMClient
from ...store import campaign_settings, compiled, overview
from ...store import paths as library_paths
from ...store.continuity import doc as continuity_doc
from ...store.continuity import effective as continuity_effective
from ...store.inference import settings as inference_settings
from .. import characters as character_routes
from .. import ledger as ledger_routes
from .. import runs
from ..common import (
    PC_HISTORY_MISSES,
    computes_only,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    leaves_campaign_unchanged,
    pc_history_404,
    record_art_mutation,
    require_inference,
    stamps_campaign_write,
)
from ..common import _campaign_root_or_404 as _campaign_root_or_404
from ..common import _content_fields as _content_fields
from ..common import _display_name_or_400 as _display_name_or_400
from ..common import _dump as _dump
from ..common import _page_of as _page_of
from ..common import _page_window as _page_window
from ..common import _response_body as _response_body
from ..common import _serve_image as _serve_image
from ..common import _serve_image_file as _serve_image_file
from ..common import _sheet_failure_status as _sheet_failure_status
from ..common import _upload_image_ext as _upload_image_ext
from ..common import _with_descriptions as _with_descriptions
from ..common import _write_response as _write_response
from ..models import (
    AdvanceTime,
    AvatarFocus,
    CalendarConfig,
    CampaignClimate,
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    CopyFromGreeting,
    DefaultVersion,
    ForkCampaign,
    GroupStateSave,
    ImageDescription,
    ImagePromote,
    NameBody,
    NewCampaign,
    NoticeMark,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    PickBody,
    PinRule,
    RefList,
    ResponseSettings,
    ScheduledEventCreate,
    ScheduledEventEdit,
    SyncPin,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)
from ..overview_log import overview_read

# Every former binding stays importable. The blocks below execute
# in declaration order; isort may sort names inside a block only.
# isort: split
from ._names import _record_name as _record_name
from ._router import router

# isort: split
from ._settings import _clock_friendly as _clock_friendly
from ._settings import (
    get_calendar_config,
    get_campaign_climate,
    get_campaign_clock,
    get_campaign_settings,
    get_campaigns,
    get_group_state,
    post_advance,
    post_advance_preview,
    put_calendar_config,
    put_campaign_climate,
    put_campaign_settings,
    put_group_state,
)

# isort: split
from ._calendar import _event_provider as _event_provider
from ._calendar import (
    delete_campaign_event,
    get_campaign_events,
    get_campaign_notices,
    post_campaign_event,
    post_campaign_event_unfire,
    post_campaign_notices,
    post_campaign_notices_forget,
    put_campaign_event,
)

# isort: split
from ._core import (
    export_campaign_epub,
    export_campaign_html,
    export_campaign_json,
    export_campaign_markdown,
    export_campaign_text,
    get_calendar_months,
    get_campaign,
    post_campaign,
)

# isort: split
from ._images import _campaign_image_url as _campaign_image_url
from ._images import _campaign_record_name_and_versions as _campaign_record_name_and_versions
from ._images import (
    delete_campaign_cover,
    delete_campaign_library_image,
    get_campaign_cover,
    get_campaign_library_image,
    list_campaign_library,
    list_campaign_undescribed_images,
    post_campaign_library_description_draft,
    post_restore_campaign_library_image,
    put_campaign_cover,
    put_campaign_library_image,
    put_campaign_library_image_description,
)

# isort: split
from ._management import (
    delete_campaign,
    get_campaign_response,
    post_campaign_fork,
    put_campaign,
    put_campaign_response,
)

# isort: split
from ._sync import (
    JOURNAL_PAGE,
    get_changes,
    get_composition,
    get_incoming,
    get_journal,
    post_accept,
    post_journal_undo,
    post_reject,
    put_composition_pin,
)
from ._sync import _journal_row as _journal_row

# isort: split
from ._ledger import (
    FEELING_AXES,
    LEDGER_RECENT,
    RELATIONSHIP_HISTORY_PAGE,
    get_ledger,
    get_provenance,
    get_relationship_history,
)
from ._ledger import _effective_or_physical as _effective_or_physical
from ._ledger import _ledger_relationships as _ledger_relationships
from ._ledger import _ledger_text as _ledger_text

# isort: split
from ._timeline import _absorbed_cast_refs as _absorbed_cast_refs
from ._timeline import _pc_name_reader as _pc_name_reader
from ._timeline import _pin_name as _pin_name
from ._timeline import _pin_posts as _pin_posts
from ._timeline import _pin_row as _pin_row
from ._timeline import _pin_rows as _pin_rows
from ._timeline import delete_pin, get_pins, get_timeline, post_pin

# isort: split
from ._actors import _ENTRANCE_LEAD as _ENTRANCE_LEAD
from ._actors import _campaign_char_version_or_404 as _campaign_char_version_or_404
from ._actors import _campaign_wroot as _campaign_wroot
from ._actors import _first_scene_transcript as _first_scene_transcript
from ._actors import _shown_capsule as _shown_capsule
from ._actors import (
    delete_campaign_character,
    delete_campaign_character_version,
    delete_campaign_image,
    delete_campaign_pc,
    get_appearances,
    get_campaign_character,
    get_campaign_character_tagline,
    get_campaign_characters,
    get_campaign_image,
    get_campaign_pcs,
    get_campaign_voice_anchor,
    get_diverged,
    post_campaign_capsule_generate,
    post_campaign_character,
    post_campaign_character_version,
    post_campaign_pc,
    post_campaign_voice_anchor_generate,
    post_copy_campaign_image_from_greeting,
    promote_campaign_image,
    put_campaign_avatar_focus,
    put_campaign_character,
    put_campaign_character_birthdate,
    put_campaign_character_name,
    put_campaign_character_tagline,
    put_campaign_character_tags,
    put_campaign_character_version,
    put_campaign_image,
    put_campaign_image_description,
    put_campaign_voice_anchor,
)

# isort: split
from ._pcs import _campaign_pc_version_or_404 as _campaign_pc_version_or_404
from ._pcs import (
    delete_campaign_pc_image,
    delete_campaign_pc_version,
    get_campaign_pc,
    get_campaign_pc_image,
    get_campaign_pc_revision,
    get_campaign_pc_revisions,
    list_campaign_pc_images,
    post_campaign_instantiate,
    post_campaign_pc_revision_restore,
    post_campaign_pc_version,
    post_import_version,
    post_pick_version,
    promote_campaign_pc_image,
    put_campaign_pc,
    put_campaign_pc_avatar_focus,
    put_campaign_pc_image,
    put_campaign_pc_image_description,
    put_campaign_pc_version,
)

# Match the monoliths star-import surface, including renamed dependency imports.
__all__ = [
    "FEELING_AXES",
    "JOURNAL_PAGE",
    "LEDGER_RECENT",
    "PC_HISTORY_MISSES",
    "RELATIONSHIP_HISTORY_PAGE",
    "APIRouter",
    "AdvanceTime",
    "AvatarFocus",
    "CalendarConfig",
    "CampaignClimate",
    "CharacterBirthdate",
    "CharacterCreate",
    "CharacterTags",
    "CopyFromGreeting",
    "DefaultVersion",
    "Depends",
    "File",
    "ForkCampaign",
    "GroupStateSave",
    "HTTPException",
    "Header",
    "ImageDescription",
    "ImagePromote",
    "JSONResponse",
    "LLMClient",
    "NameBody",
    "NewCampaign",
    "NoticeMark",
    "PCCreate",
    "PCUpdate",
    "PersonaVersionCreate",
    "PersonaVersionUpdate",
    "PickBody",
    "PinRule",
    "RefList",
    "Request",
    "Response",
    "ResponseSettings",
    "ScheduledEventCreate",
    "ScheduledEventEdit",
    "SyncPin",
    "TaglineSave",
    "UploadFile",
    "VersionCreate",
    "VersionUpdate",
    "VoiceAnchorSave",
    "annotations",
    "campaign_settings",
    "character_routes",
    "compiled",
    "computes_only",
    "contextlib",
    "continuity_doc",
    "continuity_effective",
    "delete_campaign",
    "delete_campaign_character",
    "delete_campaign_character_version",
    "delete_campaign_cover",
    "delete_campaign_event",
    "delete_campaign_image",
    "delete_campaign_library_image",
    "delete_campaign_pc",
    "delete_campaign_pc_image",
    "delete_campaign_pc_version",
    "delete_pin",
    "draft_completion",
    "export_campaign_epub",
    "export_campaign_html",
    "export_campaign_json",
    "export_campaign_markdown",
    "export_campaign_text",
    "get_appearances",
    "get_calendar_config",
    "get_calendar_months",
    "get_campaign",
    "get_campaign_character",
    "get_campaign_character_tagline",
    "get_campaign_characters",
    "get_campaign_climate",
    "get_campaign_clock",
    "get_campaign_cover",
    "get_campaign_events",
    "get_campaign_image",
    "get_campaign_library_image",
    "get_campaign_notices",
    "get_campaign_pc",
    "get_campaign_pc_image",
    "get_campaign_pc_revision",
    "get_campaign_pc_revisions",
    "get_campaign_pcs",
    "get_campaign_response",
    "get_campaign_settings",
    "get_campaign_voice_anchor",
    "get_campaigns",
    "get_changes",
    "get_composition",
    "get_diverged",
    "get_group_state",
    "get_incoming",
    "get_journal",
    "get_ledger",
    "get_llm",
    "get_pins",
    "get_provenance",
    "get_relationship_history",
    "get_timeline",
    "image_draft_prompt",
    "image_id_field",
    "image_promote_guards",
    "inference_settings",
    "leaves_campaign_unchanged",
    "ledger_routes",
    "library_paths",
    "list_campaign_library",
    "list_campaign_pc_images",
    "list_campaign_undescribed_images",
    "overview",
    "overview_read",
    "pc_history_404",
    "post_accept",
    "post_advance",
    "post_advance_preview",
    "post_campaign",
    "post_campaign_capsule_generate",
    "post_campaign_character",
    "post_campaign_character_version",
    "post_campaign_event",
    "post_campaign_event_unfire",
    "post_campaign_fork",
    "post_campaign_instantiate",
    "post_campaign_library_description_draft",
    "post_campaign_notices",
    "post_campaign_notices_forget",
    "post_campaign_pc",
    "post_campaign_pc_revision_restore",
    "post_campaign_pc_version",
    "post_campaign_voice_anchor_generate",
    "post_copy_campaign_image_from_greeting",
    "post_import_version",
    "post_journal_undo",
    "post_pick_version",
    "post_pin",
    "post_reject",
    "post_restore_campaign_library_image",
    "promote_campaign_image",
    "promote_campaign_pc_image",
    "put_calendar_config",
    "put_campaign",
    "put_campaign_avatar_focus",
    "put_campaign_character",
    "put_campaign_character_birthdate",
    "put_campaign_character_name",
    "put_campaign_character_tagline",
    "put_campaign_character_tags",
    "put_campaign_character_version",
    "put_campaign_climate",
    "put_campaign_cover",
    "put_campaign_event",
    "put_campaign_image",
    "put_campaign_image_description",
    "put_campaign_library_image",
    "put_campaign_library_image_description",
    "put_campaign_pc",
    "put_campaign_pc_avatar_focus",
    "put_campaign_pc_image",
    "put_campaign_pc_image_description",
    "put_campaign_pc_version",
    "put_campaign_response",
    "put_campaign_settings",
    "put_campaign_voice_anchor",
    "put_composition_pin",
    "put_group_state",
    "quote",
    "record_art_mutation",
    "require_inference",
    "router",
    "runs",
    "stamps_campaign_write",
    "store",
]
