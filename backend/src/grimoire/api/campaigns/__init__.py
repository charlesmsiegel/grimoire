"""Campaign API routes — composed from sub-modules."""

from fastapi import APIRouter

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

# Sub-routers will be included here as they're extracted
