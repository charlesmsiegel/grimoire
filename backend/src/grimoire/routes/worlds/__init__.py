"""World-scoped routes: the world record itself, its bound mechanics module
and sheets, tags, player characters (personas and their per-version images),
calendar, and the two import flows that populate a world wholesale — a
lorebook, and a scenario card (#217).

Characters and greetings have their own modules; the generic
``/worlds/{wid}/{kind}`` entity surface lives in ``entities``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

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
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ... import store
from ...llm import LLMClient
from ...store import compiled, overview
from ...store import paths as library_paths
from .. import runs
from ..common import (
    PC_HISTORY_MISSES,
    UsableInference,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    pc_history_404,
    record_art_mutation,
    require_inference,
)
from ..common import (
    _content_fields as _content_fields,
)
from ..common import (
    _dump as _dump,
)
from ..common import (
    _serve_image as _serve_image,
)
from ..common import (
    _sheet_failure_status as _sheet_failure_status,
)
from ..common import (
    _spooled_upload as _spooled_upload,
)
from ..common import (
    _upload_image_ext as _upload_image_ext,
)
from ..common import (
    _world_root_or_404 as _world_root_or_404,
)
from ..models import (
    AvatarFocus,
    CalendarConfig,
    ImageDescription,
    ImagePromote,
    LorebookCommit,
    ModuleSetting,
    NameBody,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    ScenarioProposal,
    ScenarioUrlBody,
    SheetBody,
    SheetCreationBody,
    WorldCreate,
    WorldUpdate,
)
from ..overview_log import overview_read

# Explicit compatibility aliases retain the original binding and star surface.
from . import _router as _router
from ._bundles import get_world_export as get_world_export
from ._bundles import post_world_import as post_world_import
from ._calendar import get_world_calendar_config as get_world_calendar_config
from ._calendar import get_world_calendar_months as get_world_calendar_months
from ._calendar import put_world_calendar_config as put_world_calendar_config
from ._imports import _detach_scenario as _detach_scenario
from ._imports import _reserve_scenario as _reserve_scenario
from ._imports import _scenario_proposal as _scenario_proposal
from ._imports import post_lorebook_import as post_lorebook_import
from ._imports import post_lorebook_parse as post_lorebook_parse
from ._imports import post_scenario_import as post_scenario_import
from ._imports import post_scenario_parse as post_scenario_parse
from ._imports import post_scenario_parse_url as post_scenario_parse_url
from ._mechanics import delete_world_sheet as delete_world_sheet
from ._mechanics import get_world_sheet as get_world_sheet
from ._mechanics import get_world_sheets as get_world_sheets
from ._mechanics import get_world_sheets_index as get_world_sheets_index
from ._mechanics import post_world_instantiate as post_world_instantiate
from ._mechanics import put_world_module as put_world_module
from ._mechanics import put_world_sheet as put_world_sheet
from ._mechanics import put_world_sheet_creation as put_world_sheet_creation
from ._pc_images import _world_pc_version_or_404 as _world_pc_version_or_404
from ._pc_images import delete_world_pc_image as delete_world_pc_image
from ._pc_images import get_world_pc_image as get_world_pc_image
from ._pc_images import list_world_pc_images as list_world_pc_images
from ._pc_images import (
    post_world_pc_image_description_draft as post_world_pc_image_description_draft,
)
from ._pc_images import promote_world_pc_image as promote_world_pc_image
from ._pc_images import put_world_pc_avatar_focus as put_world_pc_avatar_focus
from ._pc_images import put_world_pc_image as put_world_pc_image
from ._pc_images import put_world_pc_image_description as put_world_pc_image_description
from ._pcs import delete_pc_version as delete_pc_version
from ._pcs import delete_world_pc as delete_world_pc
from ._pcs import get_campaign_pc_name as get_campaign_pc_name
from ._pcs import get_pc_revision as get_pc_revision
from ._pcs import get_pc_revisions as get_pc_revisions
from ._pcs import get_world_pc as get_world_pc
from ._pcs import get_world_pcs as get_world_pcs
from ._pcs import post_pc_revision_restore as post_pc_revision_restore
from ._pcs import post_pc_version as post_pc_version
from ._pcs import post_world_pc as post_world_pc
from ._pcs import put_pc_version as put_pc_version
from ._pcs import put_world_pc as put_world_pc
from ._record import delete_world as delete_world
from ._record import get_world as get_world
from ._record import get_world_campaigns as get_world_campaigns
from ._record import get_worlds as get_worlds
from ._record import post_world as post_world
from ._record import post_world_fork as post_world_fork
from ._record import put_world as put_world
from ._router import router as router
from ._tags import _validate_tags as _validate_tags
from ._tags import delete_world_tag as delete_world_tag
from ._tags import get_world_tags as get_world_tags
from ._tags import post_world_tag as post_world_tag
from ._tags import put_world_tag as put_world_tag

_router.finalize()

# Captured from the original module before any responsibility leaves existed.
__all__ = [
    'PC_HISTORY_MISSES',
    'APIRouter',
    'AvatarFocus',
    'CalendarConfig',
    'Depends',
    'File',
    'Form',
    'HTTPException',
    'Header',
    'ImageDescription',
    'ImagePromote',
    'LLMClient',
    'LorebookCommit',
    'ModuleSetting',
    'NameBody',
    'PCCreate',
    'PCUpdate',
    'Path',
    'PersonaVersionCreate',
    'PersonaVersionUpdate',
    'Request',
    'ScenarioProposal',
    'ScenarioUrlBody',
    'SheetBody',
    'SheetCreationBody',
    'StreamingResponse',
    'UploadFile',
    'UsableInference',
    'WorldCreate',
    'WorldUpdate',
    'annotations',
    'compiled',
    'contextlib',
    'delete_pc_version',
    'delete_world',
    'delete_world_pc',
    'delete_world_pc_image',
    'delete_world_sheet',
    'delete_world_tag',
    'draft_completion',
    'get_campaign_pc_name',
    'get_llm',
    'get_pc_revision',
    'get_pc_revisions',
    'get_world',
    'get_world_calendar_config',
    'get_world_calendar_months',
    'get_world_campaigns',
    'get_world_export',
    'get_world_pc',
    'get_world_pc_image',
    'get_world_pcs',
    'get_world_sheet',
    'get_world_sheets',
    'get_world_sheets_index',
    'get_world_tags',
    'get_worlds',
    'image_draft_prompt',
    'image_id_field',
    'image_promote_guards',
    'library_paths',
    'list_world_pc_images',
    'os',
    'overview',
    'overview_read',
    'pc_history_404',
    'post_lorebook_import',
    'post_lorebook_parse',
    'post_pc_revision_restore',
    'post_pc_version',
    'post_scenario_import',
    'post_scenario_parse',
    'post_scenario_parse_url',
    'post_world',
    'post_world_fork',
    'post_world_import',
    'post_world_instantiate',
    'post_world_pc',
    'post_world_pc_image_description_draft',
    'post_world_tag',
    'promote_world_pc_image',
    'put_pc_version',
    'put_world',
    'put_world_calendar_config',
    'put_world_module',
    'put_world_pc',
    'put_world_pc_avatar_focus',
    'put_world_pc_image',
    'put_world_pc_image_description',
    'put_world_sheet',
    'put_world_sheet_creation',
    'put_world_tag',
    'record_art_mutation',
    'require_inference',
    'router',
    'run_in_threadpool',
    'runs',
    'store',
    'tempfile',
]
