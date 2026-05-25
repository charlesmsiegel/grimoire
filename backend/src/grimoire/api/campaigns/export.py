"""Campaign export routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from grimoire.api.deps import ExportDep
from grimoire.api.util import map_lookup_errors, to_payload

from .schemas import ExportPayload

router = APIRouter()


@router.post("/{campaign_id}/export")
async def export_campaign(
    campaign_id: str,
    payload: ExportPayload,
    export: ExportDep,
) -> Any:
    from grimoire.types.export import ExportOptions, ExportSelection

    try:
        selection = ExportSelection.model_validate(payload.selection)
        options = ExportOptions.model_validate(payload.options)
        result = await export.export(campaign_id, payload.adapter_id, selection, options)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(result)


@router.get("/{campaign_id}/exports/adapters")
async def list_export_adapters(
    campaign_id: str,
    export: ExportDep,
) -> Any:
    del campaign_id
    out: list[dict[str, Any]] = []
    for adapter in export.list_adapters():
        capabilities = getattr(adapter, "capabilities", None)
        try:
            option_schema = adapter.option_schema()
        except Exception:
            option_schema = {}
        out.append(
            {
                "id": adapter.id,
                "name": getattr(adapter, "name", adapter.id),
                "extensions": list(getattr(adapter, "extensions", []) or []),
                "mime_type": getattr(adapter, "mime_type", "application/octet-stream"),
                "capabilities": (capabilities.model_dump() if capabilities is not None else None),
                "option_schema": option_schema,
            }
        )
    return {"adapters": out}


@router.post("/{campaign_id}/exports/preview")
async def preview_export(
    campaign_id: str,
    payload: ExportPayload,
    export: ExportDep,
) -> Any:
    from grimoire.types.export import ExportOptions, ExportSelection

    try:
        selection = ExportSelection.model_validate(payload.selection)
        options = ExportOptions.model_validate(payload.options)
        preview = await export.preview(campaign_id, payload.adapter_id, selection, options)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return to_payload(preview)


@router.get("/{campaign_id}/exports")
async def list_export_history(
    campaign_id: str,
    export: ExportDep,
    limit: int | None = None,
) -> Any:
    try:
        records = await export.history(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    if limit is not None and limit >= 0:
        records = records[-limit:] if limit > 0 else []
    return {"records": [to_payload(record) for record in records]}
