"""World characters: versions, taglines, voice anchors, Chub links, card
import/export, localization and per-version images."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ... import inference as operations
from ... import store
from ...llm import LLMClient
from ...llm_errors import LLMError
from ...store import _plaintext as _plaintext
from ...store.record_art import read as art_read
from .. import runs
from ..common import (
    _bounded_call as _bounded_call,
)
from ..common import (
    _campaign_root_or_404 as _campaign_root_or_404,
)
from ..common import (
    _card_data as _card_data,
)
from ..common import (
    _display_name_or_400 as _display_name_or_400,
)
from ..common import (
    _serve_image as _serve_image,
)
from ..common import (
    _upload_image_ext as _upload_image_ext,
)
from ..common import (
    _world_char_version_or_404 as _world_char_version_or_404,
)
from ..common import (
    _world_root_or_404 as _world_root_or_404,
)
from ..common import (
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    record_art_mutation,
    require_inference,
    thumb_query,
)
from ..models import (
    AvatarFocus,
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    CharacterTextParse,
    CharacterTextRefine,
    ChubImportBody,
    ChubSourceBody,
    DefaultVersion,
    ImageDescription,
    ImagePromote,
    NameBody,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)

# Explicit compatibility aliases retain the original binding and star surface.
from . import _router as _router
from ._drafts import _commit_tagline as _commit_tagline
from ._drafts import _tagline_target as _tagline_target
from ._drafts import get_character_tagline as get_character_tagline
from ._drafts import get_character_voice_anchor as get_character_voice_anchor
from ._drafts import post_character_parse_text as post_character_parse_text
from ._drafts import post_character_refine_text as post_character_refine_text
from ._drafts import post_character_tagline_generate as post_character_tagline_generate
from ._drafts import post_character_voice_anchor_generate as post_character_voice_anchor_generate
from ._drafts import post_world_taglines_generate as post_world_taglines_generate
from ._drafts import put_character_tagline as put_character_tagline
from ._drafts import put_character_voice_anchor as put_character_voice_anchor
from ._exchange import _EXPORT_MEDIA as _EXPORT_MEDIA
from ._exchange import _import_name_check as _import_name_check
from ._exchange import delete_world_character_chub_source as delete_world_character_chub_source
from ._exchange import get_character_export as get_character_export
from ._exchange import get_world_characters_chub_unlinked as get_world_characters_chub_unlinked
from ._exchange import post_character_import as post_character_import
from ._exchange import post_character_import_chub as post_character_import_chub
from ._exchange import post_character_localize as post_character_localize
from ._exchange import post_character_lorebook_import as post_character_lorebook_import
from ._exchange import post_world_character_chub_gallery as post_world_character_chub_gallery
from ._exchange import post_world_character_chub_lorebooks as post_world_character_chub_lorebooks
from ._exchange import post_world_character_chub_source as post_world_character_chub_source
from ._framing import _sse as _sse
from ._galleries import _campaign_library_rows as _campaign_library_rows
from ._galleries import list_campaign_gallery as list_campaign_gallery
from ._galleries import list_undescribed_images as list_undescribed_images
from ._galleries import list_world_gallery as list_world_gallery
from ._gallery_sources import GALLERY_BASES as GALLERY_BASES
from ._gallery_sources import UNDESCRIBED_BASES as UNDESCRIBED_BASES
from ._gallery_sources import _actor_image_url as _actor_image_url
from ._gallery_sources import _describable as _describable
from ._gallery_sources import _gallery_art_fields as _gallery_art_fields
from ._gallery_sources import _gallery_image_id as _gallery_image_id
from ._gallery_sources import _gallery_urls as _gallery_urls
from ._gallery_sources import _GreetingSubjects as _GreetingSubjects
from ._gallery_sources import _overlay_name_and_versions as _overlay_name_and_versions
from ._gallery_sources import _record_name_and_versions as _record_name_and_versions
from ._gallery_sources import _undescribed_url as _undescribed_url
from ._images import delete_world_image as delete_world_image
from ._images import get_world_image as get_world_image
from ._images import list_world_images as list_world_images
from ._images import post_world_image_description_draft as post_world_image_description_draft
from ._images import promote_world_image as promote_world_image
from ._images import put_world_avatar_focus as put_world_avatar_focus
from ._images import put_world_image as put_world_image
from ._images import put_world_image_description as put_world_image_description
from ._record import delete_world_character as delete_world_character
from ._record import delete_world_version as delete_world_version
from ._record import get_world_character as get_world_character
from ._record import get_world_characters as get_world_characters
from ._record import post_world_character as post_world_character
from ._record import post_world_version as post_world_version
from ._record import put_world_character as put_world_character
from ._record import put_world_character_birthdate as put_world_character_birthdate
from ._record import put_world_character_name as put_world_character_name
from ._record import put_world_character_tags as put_world_character_tags
from ._record import put_world_version as put_world_version
from ._router import router as router

_router.finalize()

# Captured from the original module before any responsibility leaves existed.
__all__ = [
    'GALLERY_BASES',
    'UNDESCRIBED_BASES',
    'APIRouter',
    'AvatarFocus',
    'CharacterBirthdate',
    'CharacterCreate',
    'CharacterTags',
    'CharacterTextParse',
    'CharacterTextRefine',
    'ChubImportBody',
    'ChubSourceBody',
    'DefaultVersion',
    'Depends',
    'File',
    'Form',
    'HTTPException',
    'Header',
    'ImageDescription',
    'ImagePromote',
    'JSONResponse',
    'LLMClient',
    'LLMError',
    'NameBody',
    'Request',
    'Response',
    'StreamingResponse',
    'TaglineSave',
    'UploadFile',
    'VersionCreate',
    'VersionUpdate',
    'VoiceAnchorSave',
    'annotations',
    'art_read',
    'delete_world_character',
    'delete_world_character_chub_source',
    'delete_world_image',
    'delete_world_version',
    'draft_completion',
    'get_character_export',
    'get_character_tagline',
    'get_character_voice_anchor',
    'get_llm',
    'get_world_character',
    'get_world_characters',
    'get_world_characters_chub_unlinked',
    'get_world_image',
    'image_draft_prompt',
    'image_id_field',
    'image_promote_guards',
    'json',
    'list_campaign_gallery',
    'list_undescribed_images',
    'list_world_gallery',
    'list_world_images',
    'operations',
    'post_character_import',
    'post_character_import_chub',
    'post_character_localize',
    'post_character_lorebook_import',
    'post_character_parse_text',
    'post_character_refine_text',
    'post_character_tagline_generate',
    'post_character_voice_anchor_generate',
    'post_world_character',
    'post_world_character_chub_gallery',
    'post_world_character_chub_lorebooks',
    'post_world_character_chub_source',
    'post_world_image_description_draft',
    'post_world_taglines_generate',
    'post_world_version',
    'promote_world_image',
    'put_character_tagline',
    'put_character_voice_anchor',
    'put_world_avatar_focus',
    'put_world_character',
    'put_world_character_birthdate',
    'put_world_character_name',
    'put_world_character_tags',
    'put_world_image',
    'put_world_image_description',
    'put_world_version',
    'quote',
    'record_art_mutation',
    'require_inference',
    'router',
    'run_in_threadpool',
    'runs',
    'store',
    'thumb_query',
]
