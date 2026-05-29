# UI for Creating Mechanics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a UI to scaffold a new mechanics module and edit the declarative parts (manifest, sheet/content JSON Schemas, theme CSS) of new or existing modules, while generating a `mechanics.py` stub the author fills in on disk.

**Architecture:** A new `MechanicsAuthor` (owned by the Mechanics module) writes files under `data/mechanics/<id>/` through the `MechanicsService`, validating manifests and JSON Schemas before writing and calling `rescan()` after. Five new write routes in `api/library.py` expose it. The frontend turns the read-only library mechanics view into a tabbed editor with a data-driven visual schema builder (all 14 widgets) plus a raw-JSON escape hatch and a live `SheetRenderer` preview.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest; TypeScript, React 18, React Router, Vitest + React Testing Library.

**Spec:** `docs/superpowers/specs/2026-05-28-ui-for-mechanics-design.md`

---

## File Structure

**Backend**
- Create `backend/src/grimoire/mechanics/authoring.py` — `MechanicsAuthor` + typed errors + stub generator.
- Modify `backend/src/grimoire/mechanics/service.py` — add `author` property.
- Modify `backend/src/grimoire/mechanics/__init__.py` — export `MechanicsAuthor` and errors.
- Modify `backend/src/grimoire/api/library.py` — add 5 write routes + error mapping.
- Create `backend/tests/mechanics/test_authoring.py` — unit tests.
- Create `backend/tests/scenario/test_mechanics_authoring_api.py` — scenario test.

**Frontend**
- Modify `frontend/src/api/library/mechanics.ts` — write methods + types.
- Create `frontend/src/routes/library/mechanics/widgetConfig.ts` — widget config descriptor table.
- Create `frontend/src/routes/library/mechanics/SchemaBuilder.tsx` — visual + raw schema editor with live preview.
- Create `frontend/src/routes/library/mechanics/ManifestForm.tsx` — manifest field form.
- Create `frontend/src/routes/library/mechanics/MechanicsEditor.tsx` — tabbed editor.
- Create `frontend/src/routes/library/mechanics/ModuleCreateForm.tsx` — "new module" form.
- Modify `frontend/src/routes/library/MechanicsView.tsx` — wire create button + editor.
- Create tests under `frontend/src/routes/library/mechanics/__tests__/`.

**Docs**
- Modify `CLAUDE.md` — note mechanics authoring write path.

---

## Key reference facts (verified against the codebase)

- `MechanicsConfig.root` is the `data/mechanics/` dir; `MechanicsConfig.for_data_root(data_root)` builds it.
- `MechanicsService` has `config` (property → `MechanicsConfig`), `async rescan() -> RescanReport`, `installed() -> list[RegisteredModule]`.
- `RescanReport` (frozen dataclass): `discovered: list[str]`, `loaded: list[str]`, `failed: list[tuple[str,str]]`, `removed: list[str]`.
- `validate_mechanics_manifest(manifest: dict) -> ValidationResult` and `check_schema(schema) -> ValidationResult` both from validation layer; `ValidationResult` (`grimoire/validation/errors.py`) has `.ok: bool`, `.errors: tuple[ValidationError, ...]`; `ValidationError.message` and `.to_dict()`.
- The `MechanicsModule` protocol (`types/protocols.py:341`) requires: attrs `id,name,version,api_version`; methods `sheet_schema, validate_sheet, initialize_sheet, list_content_kinds, content_schema, capabilities_of, power_definitions, power_definition, evaluate_pre_roll, resolve_roll, validate_narrated_event, character_creation_steps, time_tick, system_summary`.
- `DiskBackedMechanicsModule` (`mechanics/base.py`) already supplies `sheet_schema`, `list_content_kinds`, `content_schema`.
- Frontend `request<T>(method, path, body?)` (`api/library/request.ts`) JSON-encodes `body` for non-GET and clears the library cache after writes.
- `SheetSchema`/`SchemaProperty`/`WidgetName` live in `frontend/src/sheets/types.ts`; `SheetRenderer` in `frontend/src/sheets/SheetRenderer.tsx`; `scopeCss` in `frontend/src/sheets/scopeCss.ts`.
- Test fixtures: `backend/tests/mechanics/conftest.py` exposes `write_module`, `mechanics_root`, `store`, `service`.

---

## Task 1: Stub generator

**Files:**
- Create: `backend/src/grimoire/mechanics/authoring.py`
- Test: `backend/tests/mechanics/test_authoring.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/mechanics/test_authoring.py
from __future__ import annotations

from grimoire.mechanics.authoring import generate_mechanics_py


def test_generated_stub_contains_identity_and_subclass():
    src = generate_mechanics_py(
        module_id="my-system",
        name="My System",
        version="1.2.3",
        api_version="1",
        description="A test system.",
    )
    assert "class Mechanics(DiskBackedMechanicsModule):" in src
    assert 'id = "my-system"' in src
    assert 'version = "1.2.3"' in src
    # All behavioral protocol methods the base class does NOT provide are present.
    for method in [
        "def validate_sheet",
        "def initialize_sheet",
        "def capabilities_of",
        "def power_definitions",
        "def power_definition",
        "def evaluate_pre_roll",
        "def resolve_roll",
        "def validate_narrated_event",
        "def character_creation_steps",
        "def time_tick",
        "def system_summary",
    ]:
        assert method in src


def test_generated_stub_is_valid_python():
    src = generate_mechanics_py(
        module_id="x", name="X", version="1.0.0", api_version="1", description=""
    )
    compile(src, "<stub>", "exec")  # raises SyntaxError if malformed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py -v`
Expected: FAIL — `ModuleNotFoundError: grimoire.mechanics.authoring`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/src/grimoire/mechanics/authoring.py
"""Authoring write path for mechanics modules.

The Mechanics module owns every write into ``data/mechanics/``. This module
generates a ``mechanics.py`` stub and persists author-provided declarative
files (manifest, sheet/content JSON Schemas, theme CSS). Behavioral Python is
hand-edited on disk; the stub is generated once and never re-touched here.
"""

from __future__ import annotations

import textwrap


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/mechanics/authoring.py backend/tests/mechanics/test_authoring.py
git commit -m "feat(mechanics): generate mechanics.py stub for authoring"
```

---

## Task 2: Typed authoring errors + `MechanicsAuthor.scaffold`

**Files:**
- Modify: `backend/src/grimoire/mechanics/authoring.py`
- Test: `backend/tests/mechanics/test_authoring.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/mechanics/test_authoring.py`:

```python
import pytest

from grimoire.mechanics.authoring import (
    MechanicsAuthor,
    ModuleExistsError,
    ManifestValidationError,
)


async def test_scaffold_creates_module_that_loads_green(service):
    author = MechanicsAuthor(service)
    report = await author.scaffold(
        {
            "id": "acme",
            "name": "Acme System",
            "version": "1.0.0",
            "api_version": "1",
            "sheet_kinds": ["character"],
            "content_kinds": ["spells"],
        }
    )
    root = service.config.root
    assert (root / "acme" / "manifest.yaml").is_file()
    assert (root / "acme" / "mechanics.py").is_file()
    assert (root / "acme" / "sheets" / "character.json").is_file()
    assert (root / "acme" / "content" / "spells.json").is_file()
    # Loads without error: appears in loaded, not failed.
    assert "acme" in report.loaded
    assert all(mid != "acme" for mid, _ in report.failed)


async def test_scaffold_refuses_existing_id(service):
    author = MechanicsAuthor(service)
    spec = {"id": "dup", "name": "Dup", "version": "1.0.0", "api_version": "1"}
    await author.scaffold(spec)
    with pytest.raises(ModuleExistsError):
        await author.scaffold(spec)


async def test_scaffold_rejects_invalid_manifest(service):
    author = MechanicsAuthor(service)
    with pytest.raises(ManifestValidationError) as exc:
        await author.scaffold({"id": "Bad Id!", "name": "", "version": "x"})
    assert exc.value.errors  # carries human-readable messages
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py -k scaffold -v`
Expected: FAIL — `ImportError: cannot import name 'MechanicsAuthor'`.

- [ ] **Step 3: Write minimal implementation**

Add to the top of `authoring.py` (after the existing imports):

```python
import json
import re
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
```

Add the class:

```python
class MechanicsAuthor:
    """Writes mechanics module files under ``service.config.root``.

    Every write validates first, persists exactly the intended file(s), and
    triggers a rescan so callers can surface load errors.
    """

    def __init__(self, service: "MechanicsService") -> None:
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

    async def scaffold(self, manifest_spec: dict[str, Any]) -> "RescanReport":
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
            (module_dir / theme_rel).write_text("", encoding="utf-8")
        return await self._service.rescan()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py -v`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/mechanics/authoring.py backend/tests/mechanics/test_authoring.py
git commit -m "feat(mechanics): MechanicsAuthor.scaffold writes a green-loading module"
```

---

## Task 3: `MechanicsAuthor` edit methods + guards

**Files:**
- Modify: `backend/src/grimoire/mechanics/authoring.py`
- Test: `backend/tests/mechanics/test_authoring.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/mechanics/test_authoring.py`:

```python
from grimoire.mechanics.authoring import (
    ModuleNotFoundError,
    SchemaValidationError,
    InvalidIdentifierError,
)


async def _scaffolded(service):
    author = MechanicsAuthor(service)
    await author.scaffold(
        {
            "id": "edit-me",
            "name": "Edit Me",
            "version": "1.0.0",
            "api_version": "1",
            "sheet_kinds": ["character"],
            "content_kinds": ["spells"],
            "ui": {"theme_css": "theme.css"},
        }
    )
    return author


async def test_write_sheet_schema_persists_and_keeps_mechanics_py(service):
    author = await _scaffolded(service)
    root = service.config.root
    before = (root / "edit-me" / "mechanics.py").read_text(encoding="utf-8")
    schema = {"type": "object", "properties": {"hp": {"type": "integer"}}}
    await author.write_sheet_schema("edit-me", "character", schema)
    import json

    on_disk = json.loads((root / "edit-me" / "sheets" / "character.json").read_text())
    assert on_disk == schema
    assert (root / "edit-me" / "mechanics.py").read_text(encoding="utf-8") == before


async def test_write_content_schema_persists(service):
    author = await _scaffolded(service)
    schema = {"type": "object", "properties": {"level": {"type": "integer"}}}
    await author.write_content_schema("edit-me", "spells", schema)
    import json

    on_disk = json.loads(
        (service.config.root / "edit-me" / "content" / "spells.json").read_text()
    )
    assert on_disk == schema


async def test_write_theme_css_persists(service):
    author = await _scaffolded(service)
    await author.write_theme_css("edit-me", ".sheet { color: red; }")
    assert (
        service.config.root / "edit-me" / "theme.css"
    ).read_text(encoding="utf-8") == ".sheet { color: red; }"


async def test_write_manifest_updates_name(service):
    author = await _scaffolded(service)
    await author.write_manifest(
        "edit-me",
        {"id": "edit-me", "name": "Renamed", "version": "2.0.0", "api_version": "1"},
    )
    import yaml

    data = yaml.safe_load(
        (service.config.root / "edit-me" / "manifest.yaml").read_text()
    )
    assert data["name"] == "Renamed"
    assert data["version"] == "2.0.0"


async def test_edit_missing_module_raises(service):
    author = MechanicsAuthor(service)
    with pytest.raises(ModuleNotFoundError):
        await author.write_theme_css("ghost", "x {}")


async def test_invalid_schema_rejected(service):
    author = await _scaffolded(service)
    with pytest.raises(SchemaValidationError):
        await author.write_sheet_schema("edit-me", "character", {"type": 123})


async def test_bad_kind_rejected(service):
    author = await _scaffolded(service)
    with pytest.raises(InvalidIdentifierError):
        await author.write_sheet_schema("edit-me", "../escape", {"type": "object"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py -k "write_ or missing or invalid_schema or bad_kind" -v`
Expected: FAIL — `AttributeError: 'MechanicsAuthor' object has no attribute 'write_sheet_schema'`.

- [ ] **Step 3: Write minimal implementation**

Add these methods to `MechanicsAuthor` (after `scaffold`):

```python
    def _require_dir(self, module_id: str) -> Path:
        module_dir = self._module_dir(module_id)
        if not module_dir.is_dir():
            raise ModuleNotFoundError(f"module {module_id!r} not found")
        return module_dir

    async def write_manifest(
        self, module_id: str, manifest_spec: dict[str, Any]
    ) -> "RescanReport":
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
    ) -> "RescanReport":
        module_dir = self._require_dir(module_id)
        self._check_kind(kind)
        self._validate_schema(schema)
        sheets = module_dir / "sheets"
        sheets.mkdir(exist_ok=True)
        (sheets / f"{kind}.json").write_text(
            json.dumps(schema, indent=2), encoding="utf-8"
        )
        return await self._service.rescan()

    async def write_content_schema(
        self, module_id: str, kind: str, schema: dict[str, Any]
    ) -> "RescanReport":
        module_dir = self._require_dir(module_id)
        self._check_kind(kind)
        self._validate_schema(schema)
        content = module_dir / "content"
        content.mkdir(exist_ok=True)
        (content / f"{kind}.json").write_text(
            json.dumps(schema, indent=2), encoding="utf-8"
        )
        return await self._service.rescan()

    async def write_theme_css(self, module_id: str, css: str) -> "RescanReport":
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
            raise InvalidIdentifierError("theme path escapes module directory")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/mechanics/authoring.py backend/tests/mechanics/test_authoring.py
git commit -m "feat(mechanics): MechanicsAuthor edit methods with path guards"
```

---

## Task 4: Expose author on the service and package

**Files:**
- Modify: `backend/src/grimoire/mechanics/service.py`
- Modify: `backend/src/grimoire/mechanics/__init__.py`
- Test: `backend/tests/mechanics/test_authoring.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/mechanics/test_authoring.py`:

```python
def test_service_exposes_author(service):
    from grimoire.mechanics import MechanicsAuthor as ExportedAuthor

    assert isinstance(service.author, ExportedAuthor)
    assert service.author is service.author  # cached
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/mechanics/test_authoring.py::test_service_exposes_author -v`
Expected: FAIL — `AttributeError: 'MechanicsService' object has no attribute 'author'`.

- [ ] **Step 3: Write minimal implementation**

In `service.py`, add inside `MechanicsService.__init__` (after `self._null = NullMechanicsModule()`):

```python
        self._author: MechanicsAuthor | None = None
```

Add this property (near the `config` property):

```python
    @property
    def author(self) -> "MechanicsAuthor":
        """Lazily-constructed authoring write path for this service."""
        if self._author is None:
            from grimoire.mechanics.authoring import MechanicsAuthor

            self._author = MechanicsAuthor(self)
        return self._author
```

Add the import for typing at the top of `service.py` (under the existing `if TYPE_CHECKING:` block, or add one):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grimoire.mechanics.authoring import MechanicsAuthor
```

In `__init__.py`, extend the import from `authoring` and `__all__`:

```python
from grimoire.mechanics.authoring import (
    AuthoringError,
    ManifestValidationError,
    MechanicsAuthor,
    ModuleExistsError,
    ModuleNotFoundError,
    SchemaValidationError,
)
```

Add `"MechanicsAuthor"`, `"AuthoringError"`, `"ModuleExistsError"`, `"ModuleNotFoundError"`, `"ManifestValidationError"`, `"SchemaValidationError"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/mechanics/ -v && cd backend && uv run ruff check`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/mechanics/service.py backend/src/grimoire/mechanics/__init__.py backend/tests/mechanics/test_authoring.py
git commit -m "feat(mechanics): expose MechanicsAuthor via service.author"
```

---

## Task 5: REST write routes

**Files:**
- Modify: `backend/src/grimoire/api/library.py`
- Test: `backend/tests/scenario/test_mechanics_authoring_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/scenario/test_mechanics_authoring_api.py
import pytest

pytestmark = pytest.mark.scenario


async def test_create_then_edit_module(client):
    # Create.
    resp = await client.post(
        "/api/library/mechanics",
        json={
            "id": "scn",
            "name": "Scenario System",
            "version": "1.0.0",
            "api_version": "1",
            "sheet_kinds": ["character"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "scn" in body["report"]["loaded"]

    # Appears in installed list.
    installed = (await client.get("/api/mechanics/installed")).json()
    assert any(m["manifest"]["id"] == "scn" for m in installed)

    # Write a sheet schema, then read it back.
    schema = {"type": "object", "properties": {"hp": {"type": "integer"}}}
    put = await client.put("/api/library/mechanics/scn/sheets/character", json=schema)
    assert put.status_code == 200, put.text
    got = (await client.get("/api/mechanics/scn/sheets/character")).json()
    assert got == schema


async def test_create_duplicate_is_conflict(client):
    spec = {"id": "dupapi", "name": "Dup", "version": "1.0.0", "api_version": "1"}
    assert (await client.post("/api/library/mechanics", json=spec)).status_code == 201
    assert (await client.post("/api/library/mechanics", json=spec)).status_code == 409


async def test_invalid_schema_is_422(client):
    spec = {"id": "valapi", "name": "Val", "version": "1.0.0", "api_version": "1",
            "sheet_kinds": ["character"]}
    assert (await client.post("/api/library/mechanics", json=spec)).status_code == 201
    bad = await client.put(
        "/api/library/mechanics/valapi/sheets/character", json={"type": 123}
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]
```

> Note: this uses the existing scenario `client` fixture. Confirm its name/location with `grep -rn "def client" backend/tests/scenario/conftest.py backend/tests/conftest.py`; adapt the fixture name if the suite uses e.g. `api_client`. The data root the app uses must point at a temp dir so `data/mechanics/scn` is writable in tests — reuse whatever the scenario suite already configures.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/scenario/test_mechanics_authoring_api.py -v`
Expected: FAIL — POST returns 405/404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `api/library.py`, add request models near the other Pydantic models:

```python
class MechanicsManifestBody(BaseModel):
    model_config = {"extra": "allow"}

    id: str
    name: str
    version: str
    api_version: str
```

Add an error-mapping helper (near the top-level helpers):

```python
def _map_authoring_error(exc: Exception) -> HTTPException:
    from grimoire.mechanics import (
        ManifestValidationError,
        ModuleExistsError,
        ModuleNotFoundError,
        SchemaValidationError,
    )
    from grimoire.mechanics.authoring import InvalidIdentifierError

    if isinstance(exc, ModuleExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ModuleNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (ManifestValidationError, SchemaValidationError)):
        return HTTPException(status_code=422, detail=exc.errors)
    if isinstance(exc, InvalidIdentifierError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc
```

Add the routes (after the existing mechanics routes):

```python
@router.post("/library/mechanics", status_code=201)
async def create_mechanics_module(
    body: dict[str, Any],
    mechanics: MechanicsDep,
) -> Any:
    try:
        report = await mechanics.author.scaffold(body)
    except Exception as exc:  # noqa: BLE001 - mapped to HTTP below
        raise _map_authoring_error(exc) from exc
    return {"id": body.get("id"), "report": to_payload(report)}


@router.put("/library/mechanics/{module_id}/manifest")
async def update_mechanics_manifest(
    module_id: str,
    body: dict[str, Any],
    mechanics: MechanicsDep,
) -> Any:
    try:
        report = await mechanics.author.write_manifest(module_id, body)
    except Exception as exc:  # noqa: BLE001
        raise _map_authoring_error(exc) from exc
    return to_payload(report)


@router.put("/library/mechanics/{module_id}/sheets/{kind}")
async def put_mechanics_sheet_schema(
    module_id: str,
    kind: str,
    body: dict[str, Any],
    mechanics: MechanicsDep,
) -> Any:
    try:
        report = await mechanics.author.write_sheet_schema(module_id, kind, body)
    except Exception as exc:  # noqa: BLE001
        raise _map_authoring_error(exc) from exc
    return to_payload(report)


@router.put("/library/mechanics/{module_id}/content/{kind}")
async def put_mechanics_content_schema(
    module_id: str,
    kind: str,
    body: dict[str, Any],
    mechanics: MechanicsDep,
) -> Any:
    try:
        report = await mechanics.author.write_content_schema(module_id, kind, body)
    except Exception as exc:  # noqa: BLE001
        raise _map_authoring_error(exc) from exc
    return to_payload(report)


@router.put("/library/mechanics/{module_id}/theme.css")
async def put_mechanics_theme_css(
    module_id: str,
    mechanics: MechanicsDep,
    body: str = Body(..., media_type="text/plain"),
) -> Any:
    try:
        report = await mechanics.author.write_theme_css(module_id, body)
    except Exception as exc:  # noqa: BLE001
        raise _map_authoring_error(exc) from exc
    return to_payload(report)
```

> `Body` is already imported in `library.py`. If `MechanicsManifestBody` is unused after switching to `dict[str, Any]` bodies, omit it — the dict body keeps the route permissive (the manifest schema validates server-side).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/scenario/test_mechanics_authoring_api.py -v && uv run ruff check`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/library.py backend/tests/scenario/test_mechanics_authoring_api.py
git commit -m "feat(api): mechanics authoring write routes (create + edit)"
```

---

## Task 6: Frontend API client write methods

**Files:**
- Modify: `frontend/src/api/library/mechanics.ts`
- Test: `frontend/src/api/library/__tests__/mechanics.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/api/library/__tests__/mechanics.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { mechanicsApi } from "../mechanics";

afterEach(() => vi.restoreAllMocks());

function mockFetch(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

describe("mechanicsApi writes", () => {
  it("createModule POSTs the manifest spec", async () => {
    const f = mockFetch({ id: "x", report: { discovered: [], loaded: ["x"], failed: [], removed: [] } });
    const res = await mechanicsApi.createModule({
      id: "x", name: "X", version: "1.0.0", api_version: "1",
    });
    expect(res.report.loaded).toContain("x");
    expect(f).toHaveBeenCalledWith(
      "/api/library/mechanics",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("putSheetSchema PUTs to the sheet route", async () => {
    const f = mockFetch({ discovered: [], loaded: ["x"], failed: [], removed: [] });
    await mechanicsApi.putSheetSchema("x", "character", { type: "object", properties: {} });
    expect(f).toHaveBeenCalledWith(
      "/api/library/mechanics/x/sheets/character",
      expect.objectContaining({ method: "PUT" }),
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- mechanics.test`
Expected: FAIL — `mechanicsApi.createModule is not a function`.

- [ ] **Step 3: Write minimal implementation**

Add to `frontend/src/api/library/mechanics.ts`:

```ts
export interface ManifestSpec {
  id: string;
  name: string;
  version: string;
  api_version: string;
  author?: string;
  homepage?: string;
  description?: string;
  sheet_kinds?: string[];
  content_kinds?: string[];
  capabilities?: string[];
  ui?: Record<string, unknown>;
}

export interface CreateModuleResponse {
  id: string;
  report: RescanReport;
}
```

Extend the `mechanicsApi` object:

```ts
  createModule: (spec: ManifestSpec) =>
    request<CreateModuleResponse>("POST", `/library/mechanics`, spec),
  updateManifest: (moduleId: string, spec: ManifestSpec) =>
    request<RescanReport>(
      "PUT",
      `/library/mechanics/${encodeURIComponent(moduleId)}/manifest`,
      spec,
    ),
  putSheetSchema: (moduleId: string, kind: string, schema: Record<string, unknown>) =>
    request<RescanReport>(
      "PUT",
      `/library/mechanics/${encodeURIComponent(moduleId)}/sheets/${encodeURIComponent(kind)}`,
      schema,
    ),
  putContentSchema: (moduleId: string, kind: string, schema: Record<string, unknown>) =>
    request<RescanReport>(
      "PUT",
      `/library/mechanics/${encodeURIComponent(moduleId)}/content/${encodeURIComponent(kind)}`,
      schema,
    ),
  putThemeCss: async (moduleId: string, css: string): Promise<RescanReport> => {
    const res = await fetch(
      `${API_BASE}/library/mechanics/${encodeURIComponent(moduleId)}/theme.css`,
      { method: "PUT", headers: { "Content-Type": "text/plain" }, body: css },
    );
    if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => ""));
    return (await res.json()) as RescanReport;
  },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- mechanics.test && pnpm typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/library/mechanics.ts frontend/src/api/library/__tests__/mechanics.test.ts
git commit -m "feat(frontend): mechanics authoring API client methods"
```

---

## Task 7: Widget config descriptor table

**Files:**
- Create: `frontend/src/routes/library/mechanics/widgetConfig.ts`
- Test: `frontend/src/routes/library/mechanics/__tests__/widgetConfig.test.ts`

This table drives both the visual config forms and the schema serialization, so all 14 widgets are covered by one data structure (DRY).

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/routes/library/mechanics/__tests__/widgetConfig.test.ts
import { describe, expect, it } from "vitest";
import { WIDGET_CONFIG, WIDGET_NAMES } from "../widgetConfig";

describe("WIDGET_CONFIG", () => {
  it("covers all 14 widgets", () => {
    expect(WIDGET_NAMES).toHaveLength(14);
    for (const name of WIDGET_NAMES) {
      expect(WIDGET_CONFIG[name]).toBeDefined();
    }
  });

  it("each config field has a key, label, and input kind", () => {
    for (const name of WIDGET_NAMES) {
      for (const field of WIDGET_CONFIG[name].fields) {
        expect(field.key).toBeTruthy();
        expect(field.label).toBeTruthy();
        expect(["text", "number", "boolean", "string-list", "json"]).toContain(field.input);
      }
    }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- widgetConfig.test`
Expected: FAIL — cannot find module `../widgetConfig`.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/routes/library/mechanics/widgetConfig.ts
import type { WidgetName } from "../../../sheets/types";

export type ConfigInput = "text" | "number" | "boolean" | "string-list" | "json";

export interface ConfigFieldDef {
  /** Key written into the SchemaProperty. */
  key: string;
  label: string;
  input: ConfigInput;
  help?: string;
}

export interface WidgetConfigDef {
  /** JSON Schema `type` implied by this widget (used as the property default). */
  schemaType: "string" | "number" | "integer" | "boolean" | "array" | "object";
  /** Extra SchemaProperty keys this widget understands, beyond title/description. */
  fields: ConfigFieldDef[];
}

export const WIDGET_NAMES: WidgetName[] = [
  "text",
  "textarea",
  "number",
  "select",
  "multi-select",
  "boolean",
  "dot-rating",
  "dice-pool",
  "health-track",
  "power-list",
  "grid-rating",
  "slot-list",
  "keyword-list",
  "nested-section",
];

export const WIDGET_CONFIG: Record<WidgetName, WidgetConfigDef> = {
  text: { schemaType: "string", fields: [] },
  textarea: { schemaType: "string", fields: [] },
  number: {
    schemaType: "number",
    fields: [
      { key: "min", label: "Minimum", input: "number" },
      { key: "max", label: "Maximum", input: "number" },
    ],
  },
  select: {
    schemaType: "string",
    fields: [{ key: "enum", label: "Options", input: "string-list" }],
  },
  "multi-select": {
    schemaType: "array",
    fields: [{ key: "enum", label: "Options", input: "string-list" }],
  },
  boolean: { schemaType: "boolean", fields: [] },
  "dot-rating": {
    schemaType: "integer",
    fields: [
      { key: "min", label: "Min dots", input: "number" },
      { key: "max", label: "Max dots", input: "number" },
      { key: "halves", label: "Allow half dots", input: "boolean" },
    ],
  },
  "dice-pool": {
    schemaType: "object",
    fields: [
      { key: "currentField", label: "Current field", input: "text" },
      { key: "maxField", label: "Max field", input: "text" },
    ],
  },
  "health-track": {
    schemaType: "object",
    fields: [
      { key: "rows", label: "Rows (number or JSON)", input: "json" },
      { key: "severity_levels", label: "Severity levels (JSON)", input: "json" },
    ],
  },
  "power-list": {
    schemaType: "array",
    fields: [{ key: "items", label: "Item schema (JSON)", input: "json" }],
  },
  "grid-rating": {
    schemaType: "object",
    fields: [
      { key: "cols", label: "Columns", input: "string-list" },
      { key: "rowLabels", label: "Row labels", input: "string-list" },
    ],
  },
  "slot-list": {
    schemaType: "array",
    fields: [{ key: "size", label: "Slots", input: "number" }],
  },
  "keyword-list": { schemaType: "array", fields: [] },
  "nested-section": {
    schemaType: "object",
    fields: [{ key: "properties", label: "Nested properties (JSON)", input: "json" }],
  },
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- widgetConfig.test && pnpm typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/mechanics/widgetConfig.ts frontend/src/routes/library/mechanics/__tests__/widgetConfig.test.ts
git commit -m "feat(frontend): widget config descriptor table for schema builder"
```

---

## Task 8: SchemaBuilder serialization helpers

**Files:**
- Create: `frontend/src/routes/library/mechanics/schemaModel.ts`
- Test: `frontend/src/routes/library/mechanics/__tests__/schemaModel.test.ts`

Pure functions converting between an editable field-list model and a `SheetSchema`. Separating this from the React component keeps the logic testable.

- [ ] **Step 1: Write the failing test**

```ts
// frontend/src/routes/library/mechanics/__tests__/schemaModel.test.ts
import { describe, expect, it } from "vitest";
import { fieldsToSchema, schemaToFields, type FieldModel } from "../schemaModel";

describe("schemaModel", () => {
  it("serializes a field list to a SheetSchema", () => {
    const fields: FieldModel[] = [
      { key: "name", widget: "text", required: true, config: { title: "Name" } },
      { key: "str", widget: "dot-rating", required: false, config: { min: 1, max: 5 } },
    ];
    const schema = fieldsToSchema(fields, "Character");
    expect(schema).toEqual({
      type: "object",
      title: "Character",
      properties: {
        name: { type: "string", widget: "text", title: "Name" },
        str: { type: "integer", widget: "dot-rating", min: 1, max: 5 },
      },
      required: ["name"],
    });
  });

  it("round-trips schema -> fields -> schema", () => {
    const schema = fieldsToSchema(
      [{ key: "hp", widget: "number", required: false, config: { max: 10 } }],
      "S",
    );
    const back = fieldsToSchema(schemaToFields(schema), "S");
    expect(back).toEqual(schema);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- schemaModel.test`
Expected: FAIL — cannot find module `../schemaModel`.

- [ ] **Step 3: Write minimal implementation**

```ts
// frontend/src/routes/library/mechanics/schemaModel.ts
import type { SchemaProperty, SheetSchema, WidgetName } from "../../../sheets/types";
import { WIDGET_CONFIG } from "./widgetConfig";

export interface FieldModel {
  key: string;
  widget: WidgetName;
  required: boolean;
  /** Extra SchemaProperty keys (title, description, and widget-specific). */
  config: Record<string, unknown>;
}

const RESERVED = new Set(["type", "widget"]);

export function fieldsToSchema(fields: FieldModel[], title: string): SheetSchema {
  const properties: Record<string, SchemaProperty> = {};
  const required: string[] = [];
  for (const field of fields) {
    const def = WIDGET_CONFIG[field.widget];
    const prop: Record<string, unknown> = {
      type: def.schemaType,
      widget: field.widget,
    };
    for (const [k, v] of Object.entries(field.config)) {
      if (RESERVED.has(k)) continue;
      if (v === undefined || v === "" || (Array.isArray(v) && v.length === 0)) continue;
      prop[k] = v;
    }
    properties[field.key] = prop as SchemaProperty;
    if (field.required) required.push(field.key);
  }
  const schema: SheetSchema = { type: "object", title, properties };
  if (required.length > 0) schema.required = required;
  return schema;
}

export function schemaToFields(schema: SheetSchema): FieldModel[] {
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([key, prop]) => {
    const { type: _type, widget, ...rest } = prop as Record<string, unknown>;
    return {
      key,
      widget: (widget as WidgetName) ?? "text",
      required: required.has(key),
      config: rest,
    };
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- schemaModel.test && pnpm typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/mechanics/schemaModel.ts frontend/src/routes/library/mechanics/__tests__/schemaModel.test.ts
git commit -m "feat(frontend): schema <-> field-model serialization"
```

---

## Task 9: SchemaBuilder component (visual + raw + preview)

**Files:**
- Create: `frontend/src/routes/library/mechanics/SchemaBuilder.tsx`
- Test: `frontend/src/routes/library/mechanics/__tests__/SchemaBuilder.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/library/mechanics/__tests__/SchemaBuilder.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SchemaBuilder } from "../SchemaBuilder";

describe("SchemaBuilder", () => {
  it("adds a field and emits an updated schema", () => {
    const onChange = vi.fn();
    render(
      <SchemaBuilder
        title="Character"
        value={{ type: "object", title: "Character", properties: {} }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /add field/i }));
    const keyInput = screen.getByLabelText(/field key/i);
    fireEvent.change(keyInput, { target: { value: "hp" } });
    const emitted = onChange.mock.calls.at(-1)?.[0];
    expect(emitted.properties).toHaveProperty("hp");
  });

  it("toggles to raw JSON and back", () => {
    render(
      <SchemaBuilder
        title="C"
        value={{ type: "object", title: "C", properties: {} }}
        onChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /raw json/i }));
    expect(screen.getByRole("textbox", { name: /schema json/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- SchemaBuilder.test`
Expected: FAIL — cannot find module `../SchemaBuilder`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/routes/library/mechanics/SchemaBuilder.tsx
import { useState } from "react";

import { SheetRenderer } from "../../../sheets/SheetRenderer";
import type { SheetSchema } from "../../../sheets/types";
import { FieldEditor } from "./FieldEditor";
import { fieldsToSchema, schemaToFields, type FieldModel } from "./schemaModel";
import { WIDGET_NAMES } from "./widgetConfig";

interface Props {
  title: string;
  value: SheetSchema;
  onChange: (next: SheetSchema) => void;
}

export function SchemaBuilder({ title, value, onChange }: Props) {
  const [raw, setRaw] = useState(false);
  const [rawText, setRawText] = useState(() => JSON.stringify(value, null, 2));
  const [rawError, setRawError] = useState<string | null>(null);
  const fields = schemaToFields(value);

  function emit(next: FieldModel[]) {
    onChange(fieldsToSchema(next, title));
  }

  function addField() {
    emit([
      ...fields,
      { key: `field_${fields.length + 1}`, widget: WIDGET_NAMES[0]!, required: false, config: {} },
    ]);
  }

  function updateField(index: number, next: FieldModel) {
    emit(fields.map((f, i) => (i === index ? next : f)));
  }

  function removeField(index: number) {
    emit(fields.filter((_, i) => i !== index));
  }

  function enterRaw() {
    setRawText(JSON.stringify(value, null, 2));
    setRawError(null);
    setRaw(true);
  }

  function applyRaw(text: string) {
    setRawText(text);
    try {
      const parsed = JSON.parse(text) as SheetSchema;
      setRawError(null);
      onChange(parsed);
    } catch (err) {
      setRawError(err instanceof Error ? err.message : "invalid JSON");
    }
  }

  return (
    <div className="schema-builder">
      <div className="schema-builder-toolbar">
        {raw ? (
          <button type="button" onClick={() => setRaw(false)}>
            Visual editor
          </button>
        ) : (
          <button type="button" onClick={enterRaw}>
            Raw JSON
          </button>
        )}
      </div>

      <div className="schema-builder-body">
        <div className="schema-builder-edit">
          {raw ? (
            <>
              <label htmlFor="schema-json">Schema JSON</label>
              <textarea
                id="schema-json"
                aria-label="Schema JSON"
                rows={20}
                value={rawText}
                onChange={(e) => applyRaw(e.target.value)}
              />
              {rawError && (
                <p className="library-error" role="alert">
                  {rawError}
                </p>
              )}
            </>
          ) : (
            <>
              {fields.map((field, i) => (
                <FieldEditor
                  key={i}
                  field={field}
                  onChange={(next) => updateField(i, next)}
                  onRemove={() => removeField(i)}
                />
              ))}
              <button type="button" onClick={addField}>
                Add field
              </button>
            </>
          )}
        </div>

        <div className="schema-builder-preview">
          <h5>Preview</h5>
          <SheetRenderer schema={value} value={{}} onChange={() => {}} readOnly />
        </div>
      </div>
    </div>
  );
}
```

> Confirm `SheetRenderer`'s prop names with `grep -n "interface .*Props\|export function SheetRenderer" frontend/src/sheets/SheetRenderer.tsx` and adjust the preview props (`schema`, `value`, `onChange`, `readOnly`) to match its actual signature.

- [ ] **Step 4: Run test to verify it fails on missing FieldEditor**

Run: `cd frontend && pnpm test -- SchemaBuilder.test`
Expected: FAIL — cannot find module `./FieldEditor` (built in Task 10). This task's commit waits until Task 10 compiles; proceed to Task 10, then run both tests.

- [ ] **Step 5: Commit (after Task 10 compiles)**

```bash
git add frontend/src/routes/library/mechanics/SchemaBuilder.tsx frontend/src/routes/library/mechanics/__tests__/SchemaBuilder.test.tsx
git commit -m "feat(frontend): SchemaBuilder with raw-JSON toggle and live preview"
```

---

## Task 10: FieldEditor (per-widget config + per-property raw)

**Files:**
- Create: `frontend/src/routes/library/mechanics/FieldEditor.tsx`
- Test: `frontend/src/routes/library/mechanics/__tests__/FieldEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/library/mechanics/__tests__/FieldEditor.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FieldEditor } from "../FieldEditor";
import type { FieldModel } from "../schemaModel";

const base: FieldModel = { key: "str", widget: "dot-rating", required: false, config: {} };

describe("FieldEditor", () => {
  it("edits a numeric widget config field", () => {
    const onChange = vi.fn();
    render(<FieldEditor field={base} onChange={onChange} onRemove={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/max dots/i), { target: { value: "5" } });
    const next = onChange.mock.calls.at(-1)?.[0] as FieldModel;
    expect(next.config.max).toBe(5);
  });

  it("changing widget resets to that widget's type", () => {
    const onChange = vi.fn();
    render(<FieldEditor field={base} onChange={onChange} onRemove={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/widget/i), { target: { value: "boolean" } });
    const next = onChange.mock.calls.at(-1)?.[0] as FieldModel;
    expect(next.widget).toBe("boolean");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- FieldEditor.test`
Expected: FAIL — cannot find module `../FieldEditor`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/routes/library/mechanics/FieldEditor.tsx
import { useState } from "react";

import type { FieldModel } from "./schemaModel";
import { WIDGET_CONFIG, WIDGET_NAMES, type ConfigFieldDef } from "./widgetConfig";

interface Props {
  field: FieldModel;
  onChange: (next: FieldModel) => void;
  onRemove: () => void;
}

export function FieldEditor({ field, onChange, onRemove }: Props) {
  const [raw, setRaw] = useState(false);
  const def = WIDGET_CONFIG[field.widget];

  function setConfig(key: string, value: unknown) {
    onChange({ ...field, config: { ...field.config, [key]: value } });
  }

  function renderConfigInput(cf: ConfigFieldDef) {
    const value = field.config[cf.key];
    const id = `cfg-${field.key}-${cf.key}`;
    switch (cf.input) {
      case "number":
        return (
          <input
            id={id}
            type="number"
            value={value === undefined ? "" : String(value)}
            onChange={(e) =>
              setConfig(cf.key, e.target.value === "" ? undefined : Number(e.target.value))
            }
          />
        );
      case "boolean":
        return (
          <input
            id={id}
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => setConfig(cf.key, e.target.checked)}
          />
        );
      case "string-list":
        return (
          <input
            id={id}
            type="text"
            value={Array.isArray(value) ? value.join(", ") : ""}
            placeholder="comma,separated"
            onChange={(e) =>
              setConfig(
                cf.key,
                e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
          />
        );
      case "json":
        return (
          <textarea
            id={id}
            rows={3}
            value={value === undefined ? "" : JSON.stringify(value)}
            onChange={(e) => {
              try {
                setConfig(cf.key, e.target.value === "" ? undefined : JSON.parse(e.target.value));
              } catch {
                /* keep typing; ignore parse errors until valid */
              }
            }}
          />
        );
      default:
        return (
          <input
            id={id}
            type="text"
            value={value === undefined ? "" : String(value)}
            onChange={(e) => setConfig(cf.key, e.target.value || undefined)}
          />
        );
    }
  }

  return (
    <fieldset className="field-editor">
      <div className="field-editor-row">
        <label htmlFor={`key-${field.key}`}>Field key</label>
        <input
          id={`key-${field.key}`}
          aria-label="Field key"
          value={field.key}
          onChange={(e) => onChange({ ...field, key: e.target.value })}
        />
        <label htmlFor={`widget-${field.key}`}>Widget</label>
        <select
          id={`widget-${field.key}`}
          aria-label="Widget"
          value={field.widget}
          onChange={(e) =>
            onChange({ ...field, widget: e.target.value as FieldModel["widget"], config: {} })
          }
        >
          {WIDGET_NAMES.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={field.required}
            onChange={(e) => onChange({ ...field, required: e.target.checked })}
          />
          Required
        </label>
        <button type="button" onClick={() => setRaw((r) => !r)}>
          {raw ? "Form" : "Raw JSON"}
        </button>
        <button type="button" onClick={onRemove}>
          Remove
        </button>
      </div>

      {raw ? (
        <textarea
          aria-label={`raw config for ${field.key}`}
          rows={4}
          value={JSON.stringify(field.config, null, 2)}
          onChange={(e) => {
            try {
              onChange({ ...field, config: JSON.parse(e.target.value) });
            } catch {
              /* ignore until valid JSON */
            }
          }}
        />
      ) : (
        <>
          <label htmlFor={`title-${field.key}`}>Label</label>
          <input
            id={`title-${field.key}`}
            value={(field.config.title as string) ?? ""}
            onChange={(e) => setConfig("title", e.target.value || undefined)}
          />
          {def.fields.map((cf) => (
            <div className="field-editor-config" key={cf.key}>
              <label htmlFor={`cfg-${field.key}-${cf.key}`}>{cf.label}</label>
              {renderConfigInput(cf)}
            </div>
          ))}
        </>
      )}
    </fieldset>
  );
}
```

- [ ] **Step 4: Run tests (FieldEditor + SchemaBuilder)**

Run: `cd frontend && pnpm test -- FieldEditor.test SchemaBuilder.test && pnpm typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/mechanics/FieldEditor.tsx frontend/src/routes/library/mechanics/__tests__/FieldEditor.test.tsx frontend/src/routes/library/mechanics/SchemaBuilder.tsx frontend/src/routes/library/mechanics/__tests__/SchemaBuilder.test.tsx
git commit -m "feat(frontend): FieldEditor with per-widget config and raw toggle"
```

---

## Task 11: ManifestForm

**Files:**
- Create: `frontend/src/routes/library/mechanics/ManifestForm.tsx`
- Test: `frontend/src/routes/library/mechanics/__tests__/ManifestForm.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/library/mechanics/__tests__/ManifestForm.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManifestForm } from "../ManifestForm";
import type { ManifestSpec } from "../../../../api/library/mechanics";

const spec: ManifestSpec = {
  id: "acme",
  name: "Acme",
  version: "1.0.0",
  api_version: "1",
  sheet_kinds: ["character"],
};

describe("ManifestForm", () => {
  it("edits name and emits", () => {
    const onChange = vi.fn();
    render(<ManifestForm value={spec} onChange={onChange} idEditable={false} />);
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "Renamed" } });
    expect(onChange.mock.calls.at(-1)?.[0].name).toBe("Renamed");
  });

  it("disables id when not editable", () => {
    render(<ManifestForm value={spec} onChange={vi.fn()} idEditable={false} />);
    expect(screen.getByLabelText(/^id/i)).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- ManifestForm.test`
Expected: FAIL — cannot find module `../ManifestForm`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/routes/library/mechanics/ManifestForm.tsx
import type { ManifestSpec } from "../../../api/library/mechanics";

interface Props {
  value: ManifestSpec;
  onChange: (next: ManifestSpec) => void;
  idEditable: boolean;
}

function listValue(v: string[] | undefined): string {
  return (v ?? []).join(", ");
}

function parseList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function ManifestForm({ value, onChange, idEditable }: Props) {
  function set<K extends keyof ManifestSpec>(key: K, v: ManifestSpec[K]) {
    onChange({ ...value, [key]: v });
  }

  return (
    <div className="manifest-form">
      <label htmlFor="m-id">ID</label>
      <input
        id="m-id"
        value={value.id}
        disabled={!idEditable}
        onChange={(e) => set("id", e.target.value)}
      />

      <label htmlFor="m-name">Name</label>
      <input id="m-name" value={value.name} onChange={(e) => set("name", e.target.value)} />

      <label htmlFor="m-version">Version</label>
      <input
        id="m-version"
        value={value.version}
        onChange={(e) => set("version", e.target.value)}
      />

      <label htmlFor="m-api">API version</label>
      <input
        id="m-api"
        value={value.api_version}
        onChange={(e) => set("api_version", e.target.value)}
      />

      <label htmlFor="m-author">Author</label>
      <input
        id="m-author"
        value={value.author ?? ""}
        onChange={(e) => set("author", e.target.value)}
      />

      <label htmlFor="m-homepage">Homepage</label>
      <input
        id="m-homepage"
        value={value.homepage ?? ""}
        onChange={(e) => set("homepage", e.target.value)}
      />

      <label htmlFor="m-desc">Description</label>
      <textarea
        id="m-desc"
        value={value.description ?? ""}
        onChange={(e) => set("description", e.target.value)}
      />

      <label htmlFor="m-sheets">Sheet kinds</label>
      <input
        id="m-sheets"
        value={listValue(value.sheet_kinds)}
        placeholder="character, item"
        onChange={(e) => set("sheet_kinds", parseList(e.target.value))}
      />

      <label htmlFor="m-content">Content kinds</label>
      <input
        id="m-content"
        value={listValue(value.content_kinds)}
        placeholder="spells, disciplines"
        onChange={(e) => set("content_kinds", parseList(e.target.value))}
      />

      <label htmlFor="m-caps">Capabilities</label>
      <input
        id="m-caps"
        value={listValue(value.capabilities)}
        placeholder="dice, combat"
        onChange={(e) => set("capabilities", parseList(e.target.value))}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- ManifestForm.test && pnpm typecheck`
Expected: PASS; typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/mechanics/ManifestForm.tsx frontend/src/routes/library/mechanics/__tests__/ManifestForm.test.tsx
git commit -m "feat(frontend): ManifestForm for mechanics metadata"
```

---

## Task 12: MechanicsEditor (tabs) + create form, wired into MechanicsView

**Files:**
- Create: `frontend/src/routes/library/mechanics/MechanicsEditor.tsx`
- Create: `frontend/src/routes/library/mechanics/ModuleCreateForm.tsx`
- Modify: `frontend/src/routes/library/MechanicsView.tsx`
- Test: `frontend/src/routes/library/mechanics/__tests__/MechanicsEditor.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/library/mechanics/__tests__/MechanicsEditor.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MechanicsEditor } from "../MechanicsEditor";
import { mechanicsApi } from "../../../../api/library/mechanics";

const manifest = {
  id: "acme",
  name: "Acme",
  version: "1.0.0",
  api_version: "1",
  author: "",
  homepage: "",
  description: "",
  sheet_kinds: ["character"],
  content_kinds: [],
  capabilities: [],
  ui: {},
};

describe("MechanicsEditor", () => {
  it("shows tabs and saves the manifest", async () => {
    const spy = vi
      .spyOn(mechanicsApi, "updateManifest")
      .mockResolvedValue({ discovered: [], loaded: ["acme"], failed: [], removed: [] });
    render(<MechanicsEditor manifest={manifest} themeCss={null} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("tab", { name: /manifest/i }));
    fireEvent.click(screen.getByRole("button", { name: /save manifest/i }));
    expect(spy).toHaveBeenCalledWith("acme", expect.objectContaining({ id: "acme" }));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- MechanicsEditor.test`
Expected: FAIL — cannot find module `../MechanicsEditor`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// frontend/src/routes/library/mechanics/MechanicsEditor.tsx
import { useState } from "react";

import {
  mechanicsApi,
  type ManifestSpec,
  type ModuleManifest,
  type RescanReport,
} from "../../../api/library/mechanics";
import { ApiError } from "../../../api/library";
import type { SheetSchema } from "../../../sheets/types";
import { ManifestForm } from "./ManifestForm";
import { SchemaBuilder } from "./SchemaBuilder";

type Tab = "manifest" | "sheets" | "content" | "theme";

interface Props {
  manifest: ModuleManifest;
  themeCss: string | null;
  onSaved: (report: RescanReport) => void;
}

function emptySchema(title: string): SheetSchema {
  return { type: "object", title, properties: {} };
}

function manifestToSpec(m: ModuleManifest): ManifestSpec {
  return { ...m };
}

export function MechanicsEditor({ manifest, themeCss, onSaved }: Props) {
  const [tab, setTab] = useState<Tab>("manifest");
  const [spec, setSpec] = useState<ManifestSpec>(() => manifestToSpec(manifest));
  const [error, setError] = useState<string[] | null>(null);

  async function run(fn: () => Promise<RescanReport>) {
    setError(null);
    try {
      onSaved(await fn());
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          const detail = JSON.parse(err.body) as { detail?: unknown };
          const d = detail.detail;
          setError(Array.isArray(d) ? d.map(String) : [String(d ?? err.message)]);
        } catch {
          setError([err.message]);
        }
      } else {
        setError([String(err)]);
      }
    }
  }

  return (
    <div className="mechanics-editor">
      <div role="tablist" className="mechanics-tabs">
        {(["manifest", "sheets", "content", "theme"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      {error && (
        <ul className="library-error" role="alert">
          {error.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}

      {tab === "manifest" && (
        <>
          <ManifestForm value={spec} onChange={setSpec} idEditable={false} />
          <button onClick={() => run(() => mechanicsApi.updateManifest(manifest.id, spec))}>
            Save manifest
          </button>
        </>
      )}

      {tab === "sheets" && (
        <SchemaTabs
          moduleId={manifest.id}
          kinds={manifest.sheet_kinds}
          load={(k) => mechanicsApi.sheetSchema(manifest.id, k)}
          save={(k, s) => run(() => mechanicsApi.putSheetSchema(manifest.id, k, s))}
          emptyTitle="Sheet"
        />
      )}

      {tab === "content" && (
        <SchemaTabs
          moduleId={manifest.id}
          kinds={manifest.content_kinds}
          load={(k) => mechanicsApi.contentSchema(manifest.id, k)}
          save={(k, s) => run(() => mechanicsApi.putContentSchema(manifest.id, k, s))}
          emptyTitle="Content"
        />
      )}

      {tab === "theme" && (
        <ThemeEditor
          initial={themeCss ?? ""}
          moduleId={manifest.id}
          save={(css) => run(() => mechanicsApi.putThemeCss(manifest.id, css))}
        />
      )}
    </div>
  );
}

function SchemaTabs(props: {
  moduleId: string;
  kinds: string[];
  load: (kind: string) => Promise<Record<string, unknown>>;
  save: (kind: string, schema: Record<string, unknown>) => void;
  emptyTitle: string;
}) {
  const { kinds, save, emptyTitle } = props;
  const [drafts, setDrafts] = useState<Record<string, SheetSchema>>({});

  if (kinds.length === 0) {
    return <p className="library-status">Declare a kind in the manifest first.</p>;
  }

  return (
    <>
      {kinds.map((kind) => {
        const value = drafts[kind] ?? emptySchema(emptyTitle);
        return (
          <section key={kind} className="schema-tab">
            <h4>{kind}</h4>
            <SchemaBuilder
              title={emptyTitle}
              value={value}
              onChange={(next) => setDrafts((d) => ({ ...d, [kind]: next }))}
            />
            <button onClick={() => save(kind, value as unknown as Record<string, unknown>)}>
              Save {kind}
            </button>
          </section>
        );
      })}
    </>
  );
}

function ThemeEditor(props: {
  initial: string;
  moduleId: string;
  save: (css: string) => void;
}) {
  const [css, setCss] = useState(props.initial);
  return (
    <div className="theme-editor">
      <textarea
        aria-label="theme css"
        rows={16}
        value={css}
        onChange={(e) => setCss(e.target.value)}
      />
      <button onClick={() => props.save(css)}>Save theme.css</button>
    </div>
  );
}
```

> Two adjustments to confirm while implementing: (1) add `sheetSchema(moduleId, kind)` to `mechanicsApi` mirroring `contentSchema` (the GET route `/api/mechanics/{id}/sheets/{kind}` already exists); add it in this task's edit to `mechanics.ts`. (2) `ApiError` must expose the response body — verify the field name in `frontend/src/api/client.ts` (used here as `err.body`) and adjust if it differs.

Create the create-form:

```tsx
// frontend/src/routes/library/mechanics/ModuleCreateForm.tsx
import { useState } from "react";

import { mechanicsApi, type ManifestSpec } from "../../../api/library/mechanics";
import { ApiError } from "../../../api/library";

export function ModuleCreateForm({ onCreated }: { onCreated: (id: string) => void }) {
  const [spec, setSpec] = useState<ManifestSpec>({
    id: "",
    name: "",
    version: "1.0.0",
    api_version: "1",
    sheet_kinds: ["character"],
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const res = await mechanicsApi.createModule(spec);
      onCreated(res.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="module-create-form">
      <label htmlFor="new-id">ID</label>
      <input
        id="new-id"
        value={spec.id}
        onChange={(e) => setSpec({ ...spec, id: e.target.value })}
      />
      <label htmlFor="new-name">Name</label>
      <input
        id="new-name"
        value={spec.name}
        onChange={(e) => setSpec({ ...spec, name: e.target.value })}
      />
      {error && (
        <p className="library-error" role="alert">
          {error}
        </p>
      )}
      <button onClick={submit} disabled={busy || !spec.id || !spec.name}>
        {busy ? "Creating…" : "Create module"}
      </button>
    </div>
  );
}
```

Wire both into `MechanicsView.tsx`:
- In `MechanicsList`, add a **"New module"** button that toggles a `ModuleCreateForm`; its `onCreated` navigates to `/library/mechanics/${id}` (use `useNavigate` from `react-router-dom`).
- In `ModuleDetailCard`, render `<MechanicsEditor manifest={manifest} themeCss={m.theme_css ?? null} onSaved={() => reload()} />` below the existing read-only sections (pass `reload` down from `MechanicsDetail`, or call `mechanicsApi` then re-fetch). Keep `MechanicsRequirements` and the character-creation preview.

- [ ] **Step 4: Run tests + full checks**

Run:
```
cd frontend && pnpm test -- MechanicsEditor.test && pnpm typecheck && pnpm lint
```
Expected: PASS; typecheck and lint clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/library/mechanics/MechanicsEditor.tsx frontend/src/routes/library/mechanics/ModuleCreateForm.tsx frontend/src/routes/library/MechanicsView.tsx frontend/src/api/library/mechanics.ts frontend/src/routes/library/mechanics/__tests__/MechanicsEditor.test.tsx
git commit -m "feat(frontend): tabbed mechanics editor + create flow wired into library view"
```

---

## Task 13: Styles for the editor

**Files:**
- Modify: the library stylesheet that defines `.mechanics-detail` / `.library-card` (find with `grep -rln "mechanics-detail" frontend/src`).

- [ ] **Step 1: Add minimal styles**

Add rules for `.mechanics-editor`, `.mechanics-tabs`, `.schema-builder`, `.schema-builder-body` (two-column: edit + preview), `.field-editor`, `.field-editor-row`, `.manifest-form` (label/input grid), `.module-create-form`, `.theme-editor`. Match the existing library CSS conventions (spacing variables, card borders). Keep it functional, not elaborate.

- [ ] **Step 2: Visual check**

Run the app (`scripts/run.sh`), open Library → Mechanics, create a module, add fields, confirm the preview renders and tabs switch.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/<library-stylesheet>.css
git commit -m "style(frontend): styles for mechanics editor and schema builder"
```

---

## Task 14: Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a note**

Under "Common Pitfalls" (or near the mechanics description), add:

```markdown
- **Mechanics authoring is the sanctioned write path into `data/mechanics/`.** The
  app can scaffold and edit the *declarative* parts of a mechanics module
  (manifest, sheet/content JSON Schemas, theme CSS) via the Library → Mechanics
  UI, which writes through `MechanicsAuthor` (`backend/src/grimoire/mechanics/authoring.py`).
  The `mechanics.py` behavioral logic is generated once as a stub and then
  hand-edited on disk — the UI never rewrites it. This does not contradict
  "modules are read-only at runtime": authoring is a deliberate dev-time action.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: note mechanics authoring write path in CLAUDE.md"
```

---

## Task 15: Full verification pass

- [ ] **Step 1: Backend**

Run: `cd backend && uv run pytest tests/mechanics/ tests/scenario/test_mechanics_authoring_api.py && uv run ruff check && uv run ruff format --check`
Expected: all PASS, clean.

- [ ] **Step 2: Frontend**

Run: `cd frontend && pnpm test && pnpm typecheck && pnpm lint && pnpm build`
Expected: all PASS, clean build.

- [ ] **Step 3: Manual smoke test**

Run `scripts/run.sh`. In Library → Mechanics: create a module → confirm it appears and loads green (no failure in the rescan report); open it → edit a sheet schema with the visual builder → save → reload → confirm persistence; toggle raw JSON → confirm round-trip; edit theme.css → confirm scoped preview.

- [ ] **Step 4: Commit any fixes, then finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to open the PR for issue #443.

---

## Self-review notes

- **Spec coverage:** scaffold + stub (Tasks 1–2), edit methods + guards (Task 3), service/package wiring (Task 4), all five REST routes (Task 5), API client (Task 6), visual builder for all 14 widgets via descriptor table (Tasks 7–10), manifest form (Task 11), tabbed editor + create flow + edit-existing (Task 12), styles (13), docs incl. boundary note (14), tests at every layer, full verification (15). The two parked follow-ups (no visual `hud_widgets` builder; warn-don't-block on active-campaign edits — surfaced via rescan report) are intentionally not implemented.
- **Unverified-at-write-time items flagged inline for the implementer:** scenario `client` fixture name + temp data root (Task 5), `SheetRenderer` prop names (Task 9), `ApiError` body field name + `sheetSchema` client method (Task 12), exact library stylesheet path (Task 13). Each step says how to confirm.
- **Type consistency:** `FieldModel`, `WIDGET_CONFIG`/`WIDGET_NAMES`, `fieldsToSchema`/`schemaToFields`, `ManifestSpec`, `RescanReport` names are used identically across tasks.
