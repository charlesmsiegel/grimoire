"""Authoring write path for mechanics modules.

The Mechanics module owns every write into ``data/mechanics/``. This module
generates a ``mechanics.py`` stub and persists author-provided declarative
files (manifest, sheet/content JSON Schemas, theme CSS). Behavioral Python is
hand-edited on disk; the stub is generated once and never re-touched here.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from grimoire.validation.manifests import validate_mechanics_manifest
from grimoire.validation.validator import check_schema

if TYPE_CHECKING:
    from grimoire.mechanics.service import MechanicsService, RescanReport

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_KIND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class AuthoringError(Exception):
    """Base class for mechanics authoring failures."""


class ModuleExistsError(AuthoringError):
    """Raised when scaffolding an id that already exists on disk."""


class ModuleNotFoundError(AuthoringError):
    """Raised when editing a module directory that does not exist."""


class InvalidIdentifierError(AuthoringError):
    """Raised for a malformed module id or content/sheet kind."""


class ValidationFailed(AuthoringError):
    """Base for validation failures; carries a list of messages."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors) or "validation failed")


class ManifestValidationError(ValidationFailed):
    """The manifest did not satisfy the mechanics manifest schema."""


class SchemaValidationError(ValidationFailed):
    """A sheet/content JSON Schema was not a valid JSON Schema."""


def generate_mechanics_py(
    *,
    module_id: str,
    name: str,
    version: str,
    api_version: str,
    description: str,
) -> str:
    """Return source for a ``mechanics.py`` that loads green immediately.

    Subclasses :class:`DiskBackedMechanicsModule` (which supplies
    ``sheet_schema`` / ``list_content_kinds`` / ``content_schema`` from disk)
    and provides safe-default bodies for the remaining protocol methods with
    ``# TODO`` markers for the author to implement.
    """
    summary = description or name
    return textwrap.dedent(
        f'''\
        """Generated mechanics stub for {module_id}.

        Sheet/content schemas load from the sheets/ and content/ directories
        via DiskBackedMechanicsModule. Implement the behavioral methods below.
        """

        from grimoire.mechanics.base import DiskBackedMechanicsModule
        from grimoire.types.common import ValidationResult


        class Mechanics(DiskBackedMechanicsModule):
            id = "{module_id}"
            name = "{name}"
            version = "{version}"
            api_version = "{api_version}"

            def validate_sheet(self, entity_kind, sheet):
                return ValidationResult(valid=True)

            def initialize_sheet(self, entity_kind, entity_id):
                # TODO: return a starting sheet for this entity kind.
                return {{}}

            def capabilities_of(self, entity_ref, sheet):
                # TODO: derive capabilities from the sheet.
                return []

            def power_definitions(self):
                return []

            def power_definition(self, power_id):
                return None

            def evaluate_pre_roll(self, player_input, scene):
                # TODO: propose rolls based on player input.
                return []

            def resolve_roll(self, roll, rng_seed):
                # TODO: resolve the roll deterministically from rng_seed.
                return {{"roll_id": roll.id, "outcome": "", "narration_hint": ""}}

            def validate_narrated_event(self, event, scene):
                return ValidationResult(valid=True)

            def character_creation_steps(self):
                return []

            def time_tick(self, entity_ref, sheet, duration, context):
                return []

            def system_summary(self):
                return {summary!r}
        '''
    )


class MechanicsAuthor:
    """Writes mechanics module files under ``service.config.root``.

    Every write validates first, persists exactly the intended file(s), and
    triggers a rescan so callers can surface load errors.
    """

    def __init__(self, service: MechanicsService) -> None:
        self._service = service

    @property
    def _root(self) -> Path:
        return self._service.config.root

    def _module_dir(self, module_id: str) -> Path:
        if not _ID_RE.match(module_id):
            raise InvalidIdentifierError(f"invalid module id: {module_id!r}")
        target = (self._root / module_id).resolve()
        root = self._root.resolve()
        if root not in target.parents and target != root:
            raise InvalidIdentifierError("module path escapes mechanics root")
        return target

    @staticmethod
    def _check_kind(kind: str) -> None:
        if not _KIND_RE.match(kind):
            raise InvalidIdentifierError(f"invalid kind: {kind!r}")

    @staticmethod
    def _validate_manifest(spec: dict[str, Any]) -> None:
        result = validate_mechanics_manifest(spec)
        if not result.ok:
            raise ManifestValidationError([e.message for e in result.errors])

    @staticmethod
    def _validate_schema(schema: dict[str, Any]) -> None:
        result = check_schema(schema)
        if not result.ok:
            raise SchemaValidationError([e.message for e in result.errors])

    @staticmethod
    def _placeholder_schema(kind: str) -> dict[str, Any]:
        return {"type": "object", "title": kind.title(), "properties": {}}

    async def scaffold(self, manifest_spec: dict[str, Any]) -> RescanReport:
        self._validate_manifest(manifest_spec)
        module_id = manifest_spec["id"]
        module_dir = self._module_dir(module_id)
        if module_dir.exists():
            raise ModuleExistsError(f"module {module_id!r} already exists")

        module_dir.mkdir(parents=True)
        (module_dir / "manifest.yaml").write_text(
            yaml.safe_dump(manifest_spec, sort_keys=False), encoding="utf-8"
        )
        (module_dir / "mechanics.py").write_text(
            generate_mechanics_py(
                module_id=module_id,
                name=manifest_spec["name"],
                version=manifest_spec["version"],
                api_version=manifest_spec["api_version"],
                description=manifest_spec.get("description", ""),
            ),
            encoding="utf-8",
        )
        for kind in manifest_spec.get("sheet_kinds", []):
            self._check_kind(kind)
            sheets = module_dir / "sheets"
            sheets.mkdir(exist_ok=True)
            (sheets / f"{kind}.json").write_text(
                json.dumps(self._placeholder_schema(kind), indent=2), encoding="utf-8"
            )
        for kind in manifest_spec.get("content_kinds", []):
            self._check_kind(kind)
            content = module_dir / "content"
            content.mkdir(exist_ok=True)
            (content / f"{kind}.json").write_text(
                json.dumps(self._placeholder_schema(kind), indent=2), encoding="utf-8"
            )
        ui = manifest_spec.get("ui") or {}
        theme_rel = ui.get("theme_css")
        if theme_rel:
            self._check_relative(theme_rel, module_dir)
            (module_dir / theme_rel).write_text("", encoding="utf-8")
        return await self._service.rescan()

    def _require_dir(self, module_id: str) -> Path:
        module_dir = self._module_dir(module_id)
        if not module_dir.is_dir():
            raise ModuleNotFoundError(f"module {module_id!r} not found")
        return module_dir

    async def write_manifest(self, module_id: str, manifest_spec: dict[str, Any]) -> RescanReport:
        module_dir = self._require_dir(module_id)
        if manifest_spec.get("id") != module_id:
            raise ManifestValidationError(["manifest id must match the module id"])
        self._validate_manifest(manifest_spec)
        (module_dir / "manifest.yaml").write_text(
            yaml.safe_dump(manifest_spec, sort_keys=False), encoding="utf-8"
        )
        return await self._service.rescan()

    async def write_sheet_schema(
        self, module_id: str, kind: str, schema: dict[str, Any]
    ) -> RescanReport:
        module_dir = self._require_dir(module_id)
        self._check_kind(kind)
        self._validate_schema(schema)
        sheets = module_dir / "sheets"
        sheets.mkdir(exist_ok=True)
        (sheets / f"{kind}.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
        return await self._service.rescan()

    async def write_content_schema(
        self, module_id: str, kind: str, schema: dict[str, Any]
    ) -> RescanReport:
        module_dir = self._require_dir(module_id)
        self._check_kind(kind)
        self._validate_schema(schema)
        content = module_dir / "content"
        content.mkdir(exist_ok=True)
        (content / f"{kind}.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
        return await self._service.rescan()

    async def write_theme_css(self, module_id: str, css: str) -> RescanReport:
        module_dir = self._require_dir(module_id)
        manifest_path = module_dir / "manifest.yaml"
        theme_rel = "theme.css"
        if manifest_path.is_file():
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            theme_rel = (data.get("ui") or {}).get("theme_css") or "theme.css"
        self._check_relative(theme_rel, module_dir)
        (module_dir / theme_rel).write_text(css, encoding="utf-8")
        return await self._service.rescan()

    @staticmethod
    def _check_relative(rel: str, module_dir: Path) -> None:
        target = (module_dir / rel).resolve()
        if module_dir.resolve() not in target.parents:
            raise InvalidIdentifierError("path escapes module directory")


__all__ = [
    "AuthoringError",
    "InvalidIdentifierError",
    "ManifestValidationError",
    "MechanicsAuthor",
    "ModuleExistsError",
    "ModuleNotFoundError",
    "SchemaValidationError",
    "generate_mechanics_py",
]
