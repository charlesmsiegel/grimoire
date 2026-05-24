"""Standard pagination dependency for list endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query


@dataclass
class PaginationParams:
    limit: int = Query(default=50, ge=1, le=200)
    offset: int = Query(default=0, ge=0)


PaginationDep = Annotated[PaginationParams, Depends()]
