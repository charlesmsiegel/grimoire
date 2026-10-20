"""config: responses responsibilities."""

from __future__ import annotations

from fastapi import HTTPException

from ... import llm_sampling, store
from ..common import _dump, _response_body, _write_response, refuse_newer
from ..models import (
    ResponseSettings,
    SamplerImportBody,
    SamplerPresetBody,
    StyleCreate,
    StyleUpdate,
)
from ._router import router as router


# ---- styles ----
@router.get("/styles")
def get_styles():
    return store.styles.list_styles()


@router.post("/styles")
def post_style(body: StyleCreate):
    return {"id": store.styles.create_style(body.name, body.description, body.tags, body.body)}


@router.get("/styles/{sid}")
def get_style(sid: str):
    try:
        return store.styles.read_style(sid)
    except store.styles.StyleNotFound as exc:
        raise HTTPException(status_code=404, detail="style not found") from exc


@router.put("/styles/{sid}")
def put_style(sid: str, body: StyleUpdate):
    try:
        store.styles.update_style(sid, name=body.name, description=body.description,
                                  tags=body.tags, body=body.body)
    except store.styles.StyleNotFound as exc:
        raise HTTPException(status_code=404, detail="style not found") from exc
    except store.styles.BuiltInStyleImmutable as exc:
        raise HTTPException(status_code=400, detail="built-in styles can't be edited — duplicate it first") from exc
    return {"ok": True}


@router.delete("/styles/{sid}")
def delete_style(sid: str):
    try:
        store.styles.delete_style(sid)
    except store.styles.StyleNotFound as exc:
        raise HTTPException(status_code=404, detail="style not found") from exc
    except store.styles.BuiltInStyleImmutable as exc:
        raise HTTPException(status_code=400, detail="built-in styles can't be deleted") from exc
    return {"ok": True}


@router.post("/styles/{sid}/duplicate")
def post_style_duplicate(sid: str):
    try:
        return {"id": store.styles.duplicate_style(sid)}
    except store.styles.StyleNotFound as exc:
        raise HTTPException(status_code=404, detail="style not found") from exc


# ---- phase-specific response controls ----
@router.get("/response")
def get_global_response():
    cfg = store.read_config()
    return _response_body({}, {}, cfg, cfg)


@router.put("/response")
def put_global_response(body: ResponseSettings):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    _write_response(lambda f: store.write_config(**f), fields,
                    style_key="default_style_id")
    return {"ok": True}


# ---- sampler presets ----
def _preset_or_404(pid: str) -> dict:
    got = store.sampler_presets.read_preset(pid)
    if got is None:
        raise HTTPException(status_code=404, detail="sampler preset not found")
    return got


@router.get("/sampler-presets")
def get_sampler_presets():
    """Every preset, plus the parameter table a form is rendered from -- so the
    labels and bounds have one source, `llm_sampling.PARAMS`."""
    return {"presets": store.sampler_presets.list_presets(), "params": llm_sampling.table()}


@router.post("/sampler-presets")
def post_sampler_preset(body: SamplerPresetBody):
    refuse_newer()
    fields = _dump(body)
    try:
        pid = store.sampler_presets.create_preset(fields["name"], fields.get("params"),
                                                  fields.get("notes") or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return store.sampler_presets.read_preset(pid)


@router.post("/sampler-presets/import")
def post_sampler_preset_import(body: SamplerImportBody):
    """A SillyTavern preset file, mapped where it maps, saved, and reported.

    Saved even when nothing mapped: an empty preset is a valid one, and the
    report says that is what happened rather than refusing a file that is
    exactly what SillyTavern wrote."""
    refuse_newer()
    fields = _dump(body)
    try:
        params, report = store.sampler_presets.from_sillytavern(
            fields.get("data"), include_max_tokens=bool(fields.get("include_max_tokens")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = (fields.get("name") or "").strip() or "Imported preset"
    try:
        pid = store.sampler_presets.create_preset(name[:store.sampler_presets.NAME_MAX],
                                                  params, source="sillytavern")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"preset": store.sampler_presets.read_preset(pid), "report": report}


@router.get("/sampler-presets/{pid}")
def get_sampler_preset(pid: str):
    return _preset_or_404(pid)


@router.put("/sampler-presets/{pid}")
def put_sampler_preset(pid: str, body: SamplerPresetBody):
    refuse_newer()
    fields = _dump(body)
    try:
        store.sampler_presets.update_preset(pid, fields["name"], fields.get("params"),
                                            fields.get("notes") or "")
    except store.sampler_presets.PresetNotFoundError:
        raise HTTPException(status_code=404, detail="sampler preset not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _preset_or_404(pid)


@router.delete("/sampler-presets/{pid}")
def delete_sampler_preset(pid: str):
    refuse_newer()
    try:
        store.sampler_presets.delete_preset(pid)
    except store.sampler_presets.PresetNotFoundError:
        raise HTTPException(status_code=404, detail="sampler preset not found") from None
    return {"ok": True}
