"""Prompt template HTTP API.

All LLM/image prompts in Grimoire are rendered from Jinja2 templates that
live under ``grimoire/templates/<name>/<variant>.j2``. Users can drop
additional variants into ``{data_root}/templates/<name>/<variant>.j2``;
those are registered with the global :class:`TemplateRegistry` on
startup and take precedence over bundled defaults.

Endpoints:

* ``GET    /api/templates``                        — list templates with variants
* ``GET    /api/templates/{name}/{variant}``       — read raw template text
* ``PUT    /api/templates/{name}/{variant}``       — create or update a user variant
* ``DELETE /api/templates/{name}/{variant}``       — remove a user variant
* ``POST   /api/templates/{name}/active``          — pin active variant

Only user-supplied variants under ``{data_root}/templates`` are writable;
the bundled defaults are read-only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException

from grimoire import config as _config
from grimoire.templates import DEFAULT_VARIANT, TEMPLATE_SUFFIX
from grimoire.templates import registry as template_registry

router = APIRouter()

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def _user_root() -> Path:
    # Look up settings dynamically so tests that swap config.settings see the
    # new value (the API tests do this via a monkeypatched env + a fresh
    # Settings() instance).
    return _config.settings.data_root / "templates"


def _validate_name(name: str, *, what: str = "template name") -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"invalid {what}: must match [A-Za-z0-9][A-Za-z0-9_-]*",
        )


def _list_template_names() -> list[str]:
    seen: dict[str, None] = {}
    for base in template_registry.search_paths:
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.is_dir() and _NAME_RE.match(entry.name):
                seen.setdefault(entry.name, None)
    return list(seen.keys())


def _resolve_user_file(name: str, variant: str) -> Path:
    """Resolve a user-supplied template file path with traversal checks."""
    _validate_name(name)
    _validate_name(variant, what="variant name")
    root = _user_root().resolve()
    candidate = (root / name / f"{variant}{TEMPLATE_SUFFIX}").resolve()
    # Ensure the resolved path stays under the user root (defense-in-depth;
    # the regex above already forbids slashes, but this catches future regressions).
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path traversal rejected") from exc
    return candidate


def _read_first_variant(name: str, variant: str) -> tuple[str, Path] | None:
    """Find the file backing ``<name>/<variant>`` in any search path."""
    relative = Path(name) / f"{variant}{TEMPLATE_SUFFIX}"
    for base in template_registry.search_paths:
        candidate = base / relative
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), candidate
    return None


@router.get("/templates")
async def list_templates() -> Any:
    """Return all template names with their variants and active variant."""
    user_root = _user_root().resolve()
    items: list[dict[str, Any]] = []
    for name in _list_template_names():
        variants = template_registry.list_variants(name)
        # Mark which variants are user-supplied (writable) vs bundled.
        editable: list[str] = []
        for variant in variants:
            candidate = user_root / name / f"{variant}{TEMPLATE_SUFFIX}"
            if candidate.is_file():
                editable.append(variant)
        items.append(
            {
                "name": name,
                "variants": variants,
                "active": template_registry.get_variant(name),
                "editable": editable,
            }
        )
    return {
        "templates": items,
        "user_dir": str(user_root),
        "default_variant": DEFAULT_VARIANT,
    }


@router.get("/templates/{name}/{variant}")
async def read_template(name: str, variant: str) -> Any:
    _validate_name(name)
    _validate_name(variant, what="variant name")
    result = _read_first_variant(name, variant)
    if result is None:
        raise HTTPException(status_code=404, detail=f"template {name}/{variant} not found")
    body, path = result
    user_root = _user_root().resolve()
    try:
        path.resolve().relative_to(user_root)
        editable = True
    except ValueError:
        editable = False
    return {
        "name": name,
        "variant": variant,
        "body": body,
        "editable": editable,
        "path": str(path),
    }


@router.put("/templates/{name}/{variant}")
async def write_template(
    name: str,
    variant: str,
    payload: Annotated[dict[str, Any], Body()],
) -> Any:
    """Create or replace a user variant. The body is rejected if it
    isn't a string under a reasonable size cap."""
    body = payload.get("body")
    if not isinstance(body, str):
        raise HTTPException(status_code=400, detail="body must be a string")
    if len(body) > 200_000:
        raise HTTPException(status_code=413, detail="template too large")
    target = _resolve_user_file(name, variant)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling tmp + rename so a crash mid-write can't leave a
    # half-baked file the registry would then try to render.
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(target)
    # Bust the cached jinja environment so subsequent renders see the new file.
    template_registry.register_search_path(_user_root(), prepend=True)
    return {"ok": True, "path": str(target)}


@router.delete("/templates/{name}/{variant}")
async def delete_template(name: str, variant: str) -> Any:
    target = _resolve_user_file(name, variant)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="variant not found in user dir")
    target.unlink()
    # If the active variant was deleted, fall back to default.
    if template_registry.get_variant(name) == variant:
        template_registry.set_variant(name, None)
    return {"ok": True}


@router.post("/templates/{name}/active")
async def set_active_variant(
    name: str,
    payload: Annotated[dict[str, Any], Body()],
) -> Any:
    _validate_name(name)
    variant = payload.get("variant")
    if variant is None:
        template_registry.set_variant(name, None)
        return {"ok": True, "active": template_registry.get_variant(name)}
    if not isinstance(variant, str):
        raise HTTPException(status_code=400, detail="variant must be a string or null")
    _validate_name(variant, what="variant name")
    if variant not in template_registry.list_variants(name):
        raise HTTPException(status_code=404, detail=f"variant {variant!r} not available")
    template_registry.set_variant(name, variant)
    return {"ok": True, "active": variant}
