"""Campaign API routes — composed from sub-modules."""

from fastapi import APIRouter

from .composition import router as composition_router
from .continuity import router as continuity_router
from .core import router as core_router
from .entities import router as entities_router
from .export import router as export_router
from .fork import router as fork_router
from .images import router as images_router
from .new_scene import router as new_scene_router
from .pcs import router as pcs_router
from .retcon import router as retcon_router
from .reviews import router as reviews_router
from .scenes import router as scenes_router
from .settings import router as settings_router
from .sheets import router as sheets_router
from .turns import router as turns_router

router = APIRouter()

_PREFIX = "/campaigns"
_TAGS: list[str] = ["campaigns"]

router.include_router(core_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(settings_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(composition_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(pcs_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(turns_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(retcon_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(fork_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(scenes_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(entities_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(sheets_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(continuity_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(new_scene_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(images_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(export_router, prefix=_PREFIX, tags=_TAGS)
router.include_router(reviews_router, prefix=_PREFIX, tags=_TAGS)

__all__ = ["router"]
