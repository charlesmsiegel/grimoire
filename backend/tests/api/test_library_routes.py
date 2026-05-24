"""REST contract tests for the library/mechanics/plugins routes.

Each test injects a fake service into the app's :class:`ServiceContainer` and
verifies the router dispatches correctly and shapes the response as expected.
The fakes use ``async def`` to mirror the real service signatures so tests
fail if a handler forgets to ``await``.
"""

from __future__ import annotations

from typing import Any

from grimoire.types.composition import LibraryEntity, WorldMeta


class FakeLibrary:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    async def list_worlds(self) -> list[WorldMeta]:
        return [WorldMeta(id="wod-london", name="WoD London", version=1)]

    async def get_world(self, world_id: str) -> WorldMeta:
        if world_id != "wod-london":
            raise KeyError(world_id)
        return WorldMeta(id="wod-london", name="WoD London", version=1)

    async def list_in_world(self, world_id: str, kind: str) -> list[LibraryEntity]:
        return [
            LibraryEntity(
                id="worlds/wod-london/characters/alistair",
                world_id=world_id,
                kind="character",
                asset_id="alistair",
                name="Alistair",
                path="worlds/wod-london/characters/alistair.md",
                frontmatter={"name": "Alistair"},
                body="",
            )
        ]

    async def list_style_guides(self) -> list[Any]:
        return []

    async def list_image_presets(self) -> list[Any]:
        return []

    async def list_greetings(self, world_id: str) -> list[Any]:
        return []

    async def variants_of(self, asset_id: str, kind: str) -> list[Any]:
        if asset_id == "alistair" and kind == "character":
            return [
                LibraryEntity(
                    id="worlds/wod-london/characters/alistair",
                    world_id="wod-london",
                    kind="character",
                    asset_id="alistair",
                    name="Alistair",
                    path="worlds/wod-london/characters/alistair.md",
                    frontmatter={"name": "Alistair", "clan": "Ventrue"},
                    body="A polished elder in a Savile Row suit.",
                ),
                LibraryEntity(
                    id="worlds/wod-paris/characters/alistair",
                    world_id="wod-paris",
                    kind="character",
                    asset_id="alistair",
                    name="Alistair",
                    path="worlds/wod-paris/characters/alistair.md",
                    frontmatter={"name": "Alistair", "clan": "Toreador"},
                    body="A salon-haunting aesthete.",
                ),
            ]
        return []

    async def dependents(self, world_id: str, kind: str, entity_id: str) -> list[Any]:
        return []

    async def create_entity(self, *args: Any, **kwargs: Any) -> Any:
        self.created.append((args[1], args[2]))
        return LibraryEntity(
            id=f"worlds/{args[0]}/{args[1]}s/{args[2]}",
            world_id=args[0],
            kind=args[1],
            asset_id=args[2],
            name=args[2],
            path="",
            frontmatter=args[3],
            body=args[4],
        )

    async def create_style_guide(self, id: str, **kwargs: Any) -> LibraryEntity:
        self.created.append(("style_guide", id))
        body_parts = [f"# {kwargs.get('name') or id}"]
        for heading_key, heading in (
            ("pacing", "Pacing"),
            ("voice", "Voice"),
            ("themes", "Themes"),
            ("avoid", "Avoid"),
        ):
            items = [b.strip() for b in (kwargs.get(heading_key) or []) if b and b.strip()]
            if items:
                bullets = "\n".join(f"- {b}" for b in items)
                body_parts.append(f"## {heading}\n{bullets}")
        return LibraryEntity(
            id=f"style-guides/{id}",
            world_id=None,
            kind="style_guide",
            asset_id=id,
            name=kwargs.get("name") or id,
            path=f"style-guides/{id}.md",
            frontmatter={"id": id, "name": kwargs.get("name") or id},
            body="\n\n".join(body_parts) + "\n",
            tags=list(kwargs.get("tags") or []),
        )

    async def update_style_guide(self, id: str, **kwargs: Any) -> LibraryEntity:
        self.created.append(("style_guide_update", id))
        return await self.create_style_guide(id, **kwargs)

    async def parse_style_guide(self, id: str) -> dict[str, Any]:
        return {
            "id": id,
            "name": id.title(),
            "description": "",
            "tags": [],
            "intro": "",
            "pacing": ["one"],
            "voice": [],
            "themes": [],
            "avoid": [],
            "extra_sections": [],
        }

    async def update_entity(
        self,
        world_id: str,
        kind: str,
        entity_id: str,
        frontmatter_patch: dict | None = None,
        body: str | None = None,
        *,
        source: str = "user",
    ) -> LibraryEntity:
        self.created.append(("entity_update", f"{world_id}/{kind}/{entity_id}"))
        merged_fm = dict(frontmatter_patch or {})
        return LibraryEntity(
            id=f"worlds/{world_id}/{kind}s/{entity_id}",
            world_id=world_id,
            kind=kind,
            asset_id=entity_id,
            name=entity_id,
            path="",
            frontmatter=merged_fm,
            body=body or "",
        )

    async def get_image_preset(self, id: str) -> LibraryEntity:
        return LibraryEntity(
            id=f"image-presets/{id}",
            world_id=None,
            kind="image_preset",
            asset_id=id,
            name=id.title(),
            path=f"image-presets/{id}.yaml",
            frontmatter={
                "id": id,
                "name": id.title(),
                "style_preamble": "cinematic, dramatic lighting",
                "default_negative_prompt": "blurry",
                "default_params": {"steps": 18, "width": 256, "height": 256},
            },
            body="",
        )

    async def create_image_preset(self, id: str, **kwargs: Any) -> LibraryEntity:
        self.created.append(("image_preset", id))
        fm: dict[str, Any] = {"id": id, "name": kwargs.get("name") or id}
        if kwargs.get("description"):
            fm["description"] = kwargs["description"]
        if kwargs.get("tags"):
            fm["tags"] = list(kwargs["tags"])
        if kwargs.get("style_preamble"):
            fm["style_preamble"] = kwargs["style_preamble"]
        if kwargs.get("default_negative_prompt"):
            fm["default_negative_prompt"] = kwargs["default_negative_prompt"]
        if kwargs.get("default_params"):
            fm["default_params"] = dict(kwargs["default_params"])
        return LibraryEntity(
            id=f"image-presets/{id}",
            world_id=None,
            kind="image_preset",
            asset_id=id,
            name=kwargs.get("name") or id,
            path=f"image-presets/{id}.yaml",
            frontmatter=fm,
            body="",
            tags=list(kwargs.get("tags") or []),
        )

    async def update_image_preset(self, id: str, **kwargs: Any) -> LibraryEntity:
        self.created.append(("image_preset_update", id))
        return await self.create_image_preset(id, **kwargs)

    async def parse_image_preset(self, id: str) -> dict[str, Any]:
        return {
            "id": id,
            "name": id.title(),
            "description": "",
            "tags": [],
            "style_preamble": "cinematic",
            "default_negative_prompt": "blurry",
            "default_params": {"steps": 18},
        }

    async def delete_image_preset(self, id: str, **kwargs: Any) -> None:
        self.created.append(("image_preset_delete", id))


class FakeMechanics:
    def __init__(self) -> None:
        self.schemas: dict[tuple[str, str], dict[str, Any]] = {}
        self.themes: dict[str, str] = {}

    def installed(self) -> list[Any]:
        return []

    async def rescan(self) -> dict[str, Any]:
        return {"added": [], "removed": [], "errors": []}

    def sheet_schema_for_module(self, module_id: str, kind: str) -> dict[str, Any] | None:
        return self.schemas.get((module_id, kind))

    def theme_css_for_module(self, module_id: str) -> str:
        return self.themes.get(module_id, "")


class FakePlugins:
    def __init__(self) -> None:
        self.manifests: dict[str, Any] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.llm_providers: dict[str, Any] = {}
        self.embedding_providers: dict[str, Any] = {}

    async def list_installed(self) -> list[Any]:
        return []

    async def rescan(self) -> dict[str, Any]:
        return {"added": [], "removed": [], "errors": []}

    async def get_manifest(self, plugin_id: str) -> Any:
        return self.manifests.get(plugin_id)

    async def get_config(self, plugin_id: str) -> dict[str, Any]:
        return dict(self.configs.get(plugin_id, {}))

    async def set_config(self, plugin_id: str, config: dict[str, Any]) -> None:
        if plugin_id not in self.manifests:
            raise KeyError(plugin_id)
        self.configs[plugin_id] = dict(config)
        self.saved.append((plugin_id, dict(config)))

    def get_llm_provider(self, plugin_id: str) -> Any:
        return self.llm_providers.get(plugin_id)

    def get_embedding_provider(self, plugin_id: str) -> Any:
        return self.embedding_providers.get(plugin_id)

    def discovery_errors(self) -> list[Any]:
        return getattr(self, "_discovery_errors", [])


def test_list_worlds(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/worlds")
    assert response.status_code == 200
    body = response.json()
    assert body == [
        {
            "id": "wod-london",
            "name": "WoD London",
            "description": "",
            "tags": [],
            "genre": "",
            "calendar": {},
            "calendar_ids": [],
            "holiday_set_ids": [],
            "display_calendar_id": None,
            "atmosphere": {},
            "defaults": {},
            "version": 1,
        }
    ]


def test_get_world_404_when_unknown(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/worlds/missing")
    assert response.status_code == 404


def test_list_world_entities(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/worlds/wod-london/character")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["asset_id"] == "alistair"


def test_create_world_entity(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.post(
        "/api/library/worlds/wod-london/character",
        json={"id": "new-pc", "frontmatter": {"name": "New PC"}, "body": "body"},
    )
    assert response.status_code == 201
    assert fake.created == [("character", "new-pc")]


def test_create_style_guide(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.post(
        "/api/library/style-guides",
        json={
            "id": "cozy-mystery",
            "name": "Cozy Mystery",
            "description": "Low stakes.",
            "tags": ["cozy"],
            "pacing": ["Unhurried."],
            "voice": ["Warm."],
            "themes": ["Community."],
            "avoid": ["Gore."],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["asset_id"] == "cozy-mystery"
    assert "## Pacing\n- Unhurried." in body["body"]
    assert ("style_guide", "cozy-mystery") in fake.created


def test_update_style_guide(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.patch(
        "/api/library/style-guides/cozy-mystery",
        json={"voice": ["Brisker."]},
    )
    assert response.status_code == 200
    assert response.json()["asset_id"] == "cozy-mystery"
    assert ("style_guide_update", "cozy-mystery") in fake.created


def test_get_style_guide_edit_returns_parsed_shape(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/style-guides/cozy-mystery/edit")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "cozy-mystery"
    assert body["pacing"] == ["one"]
    assert "extra_sections" in body


def test_variants_returns_frontmatter_and_body_for_diff(client, container) -> None:
    """The variants endpoint must surface the full body and frontmatter so the
    frontend's cross-world diff preview (spec 14 §12) can compute a key/value
    comparison and a body-length delta entirely on the client."""
    container.library = FakeLibrary()
    response = client.get("/api/library/variants/character/alistair")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert {row["world_id"] for row in body} == {"wod-london", "wod-paris"}
    for row in body:
        assert "frontmatter" in row
        assert "body" in row
        assert row["frontmatter"].get("name") == "Alistair"
        assert row["body"]
    # The frontmatter differs on `clan` — the diff UI keys off this row.
    clans = {row["world_id"]: row["frontmatter"]["clan"] for row in body}
    assert clans == {"wod-london": "Ventrue", "wod-paris": "Toreador"}


def test_variants_returns_empty_list_for_unique_asset(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/variants/character/nobody")
    assert response.status_code == 200
    assert response.json() == []


def test_mechanics_installed(client, container) -> None:
    container.mechanics = FakeMechanics()
    response = client.get("/api/mechanics/installed")
    assert response.status_code == 200
    assert response.json() == []


def test_mechanics_installed_strips_live_instance(client, container) -> None:
    # Regression: the live ``MechanicsModule`` instance (a dynamically imported
    # class such as ``grimoire_mechanics._loaded.adnd2e.Mechanics``) is not
    # JSON-serializable; the route must drop it before encoding.
    from pathlib import Path

    from grimoire.mechanics.registry import RegisteredModule
    from grimoire.types.mechanics import ModuleManifest

    class _OpaqueInstance:  # deliberately not JSON-serializable
        pass

    record = RegisteredModule(
        manifest=ModuleManifest(id="adnd2e", name="AD&D 2e", version="1", api_version="1"),
        instance=_OpaqueInstance(),  # type: ignore[arg-type]
        module_dir=Path("/modules/adnd2e"),
        sheet_schemas={"character": {"type": "object"}},
        content_schemas={},
        theme_css="body{}",
    )
    mech = FakeMechanics()
    mech.installed = lambda: [record]  # type: ignore[method-assign]
    container.mechanics = mech

    response = client.get("/api/mechanics/installed")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "instance" not in body[0]
    assert body[0]["manifest"]["id"] == "adnd2e"
    assert body[0]["theme_css"] == "body{}"
    assert body[0]["sheet_schemas"] == {"character": {"type": "object"}}
    assert isinstance(body[0]["module_dir"], str)


def test_mechanics_sheet_schema_returns_schema(client, container) -> None:
    mech = FakeMechanics()
    mech.schemas[("vamp", "character")] = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
    container.mechanics = mech
    response = client.get("/api/mechanics/vamp/sheets/character")
    assert response.status_code == 200
    body = response.json()
    assert body["properties"]["name"]["type"] == "string"


def test_mechanics_sheet_schema_404_when_missing(client, container) -> None:
    container.mechanics = FakeMechanics()
    response = client.get("/api/mechanics/unknown/sheets/character")
    assert response.status_code == 404


def test_mechanics_theme_css_serves_text(client, container) -> None:
    mech = FakeMechanics()
    mech.themes["vamp"] = ".sheet { color: red; }"
    container.mechanics = mech
    response = client.get("/api/mechanics/vamp/theme.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.text == ".sheet { color: red; }"


def test_mechanics_theme_css_empty_when_unset(client, container) -> None:
    container.mechanics = FakeMechanics()
    response = client.get("/api/mechanics/nope/theme.css")
    assert response.status_code == 200
    assert response.text == ""


def test_plugins_rescan(client, container) -> None:
    container.plugins = FakePlugins()
    response = client.post("/api/plugins/rescan")
    assert response.status_code == 200


class _FakeFileWatcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def scan_now(self, *, scope: str = "all") -> dict[str, Any]:
        self.calls.append(scope)
        return {"scope": scope, "library_files": 3, "campaign_files": 2}


def test_rescan_worlds_invokes_file_watcher_with_library_scope(client, container) -> None:
    fw = _FakeFileWatcher()
    container.file_watcher = fw
    response = client.post("/api/library/worlds/rescan")
    assert response.status_code == 200
    assert response.json() == {"scope": "library", "library_files": 3, "campaign_files": 2}
    assert fw.calls == ["library"]


def test_rescan_worlds_returns_503_when_watcher_not_configured(client, container) -> None:
    container.file_watcher = None
    response = client.post("/api/library/worlds/rescan")
    assert response.status_code == 503


def test_plugins_discovery_errors_returns_recent_failures(client, container) -> None:
    """The endpoint exposes parse errors from the last discovery pass so the
    UI can render an actionable message instead of a bare ``failed`` entry."""
    from pathlib import Path

    from grimoire.plugins.discovery import DiscoveryError

    plugins = FakePlugins()
    plugins._discovery_errors = [
        DiscoveryError(
            plugin_dir=Path("/data/plugins/oops"),
            message="manifest.yaml is empty",
        )
    ]
    container.plugins = plugins
    response = client.get("/api/plugins/discovery-errors")
    assert response.status_code == 200
    body = response.json()
    assert body == [{"plugin_dir": "/data/plugins/oops", "message": "manifest.yaml is empty"}]


def test_plugins_discovery_errors_empty_when_unsupported(client, container) -> None:
    """Plugins backends without ``discovery_errors`` return an empty list rather
    than 500."""

    class _NoErrors:
        pass

    container.plugins = _NoErrors()
    response = client.get("/api/plugins/discovery-errors")
    assert response.status_code == 200
    assert response.json() == []


def _fake_manifest(plugin_id: str, schema: dict[str, Any]) -> Any:
    class _Manifest:
        id = plugin_id
        config_schema = schema

    return _Manifest()


def test_get_plugin_config_redacts_secrets(client, container) -> None:
    plugins = FakePlugins()
    schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "secret": True},
            "default_model": {"type": "string"},
        },
        "required": ["api_key"],
    }
    plugins.manifests["llm-x"] = _fake_manifest("llm-x", schema)
    plugins.configs["llm-x"] = {"api_key": "sk-secret", "default_model": "claude-opus-4-7"}
    container.plugins = plugins

    response = client.get("/api/plugins/llm-x/config")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "plugin_id": "llm-x",
        "values": {"default_model": "claude-opus-4-7"},
        "secrets_set": {"api_key": True},
        "configured": True,
    }


def test_get_plugin_config_reports_missing_secret(client, container) -> None:
    plugins = FakePlugins()
    schema = {
        "type": "object",
        "properties": {"api_key": {"type": "string", "secret": True}},
        "required": ["api_key"],
    }
    plugins.manifests["llm-x"] = _fake_manifest("llm-x", schema)
    plugins.configs["llm-x"] = {}
    container.plugins = plugins

    response = client.get("/api/plugins/llm-x/config")
    assert response.status_code == 200
    body = response.json()
    assert body["secrets_set"] == {"api_key": False}
    assert body["configured"] is False


def test_get_plugin_config_404_for_unknown(client, container) -> None:
    container.plugins = FakePlugins()
    response = client.get("/api/plugins/does-not-exist/config")
    assert response.status_code == 404


class _FakeProvider:
    def __init__(self, models: list[Any] | Exception) -> None:
        self._models = models

    async def list_models(self) -> list[Any]:
        if isinstance(self._models, Exception):
            raise self._models
        return list(self._models)


def test_plugin_models_returns_list(client, container) -> None:
    from grimoire.types.llm import ModelInfo

    plugins = FakePlugins()
    plugins.llm_providers["llm-x"] = _FakeProvider(
        [
            ModelInfo(
                id="anthropic/claude-opus-4-7",
                name="Claude Opus 4.7",
                context_window=200000,
            ),
            ModelInfo(id="openai/gpt-4o", name="GPT-4o", context_window=128000),
        ]
    )
    container.plugins = plugins
    response = client.get("/api/plugins/llm-x/models")
    assert response.status_code == 200
    body = response.json()
    assert [m["id"] for m in body] == ["anthropic/claude-opus-4-7", "openai/gpt-4o"]


def test_plugin_models_404_when_no_catalog(client, container) -> None:
    container.plugins = FakePlugins()
    response = client.get("/api/plugins/llm-x/models")
    assert response.status_code == 404


def test_plugin_models_409_when_provider_unconfigured(client, container) -> None:
    plugins = FakePlugins()
    plugins.llm_providers["llm-x"] = _FakeProvider(RuntimeError("api_key is not configured"))
    container.plugins = plugins
    response = client.get("/api/plugins/llm-x/models")
    assert response.status_code == 409
    assert "api_key" in response.json()["detail"]


def test_plugin_models_serves_embedding_provider(client, container) -> None:
    from grimoire.types.llm import ModelInfo

    plugins = FakePlugins()
    plugins.embedding_providers["embed-x"] = _FakeProvider(
        [
            ModelInfo(id="text-embedding-3-small", name="3-small", dimensions=1536),
            ModelInfo(id="text-embedding-3-large", name="3-large", dimensions=3072),
        ]
    )
    container.plugins = plugins
    response = client.get("/api/plugins/embed-x/models")
    assert response.status_code == 200
    body = response.json()
    assert [m["id"] for m in body] == ["text-embedding-3-small", "text-embedding-3-large"]
    assert body[0]["dimensions"] == 1536


def test_configure_plugin_persists_via_service(client, container) -> None:
    plugins = FakePlugins()
    plugins.manifests["llm-x"] = _fake_manifest("llm-x", {"type": "object"})
    container.plugins = plugins
    response = client.post(
        "/api/plugins/llm-x/config",
        json={"api_key": "sk-1", "default_model": "m"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert plugins.saved == [("llm-x", {"api_key": "sk-1", "default_model": "m"})]


def test_library_503_when_unset(client, container) -> None:
    # Lifespan auto-wires a LibraryService; clear it so we can verify the
    # 503 branch in api/deps.py:_require for any service that goes missing.
    container.library = None
    response = client.get("/api/library/worlds")
    assert response.status_code == 503
    assert "library" in response.json()["detail"]


class FakeLibraryWithDiff(FakeLibrary):
    async def world_diff(
        self,
        world_id: str,
        from_version: int,
        to_version: int | None = None,
    ) -> dict[str, Any]:
        if world_id != "wod-london":
            raise KeyError(world_id)
        effective_to = to_version if to_version is not None else 5
        return {
            "world_id": world_id,
            "from_version": from_version,
            "to_version": effective_to,
            "added": [],
            "removed": [],
            "changed": [
                {
                    "path": "character/alistair",
                    "before": None,
                    "after": {
                        "name": "Alistair",
                        "frontmatter": {"name": "Alistair", "clan": "Ventrue"},
                        "body": "Updated body.",
                        "version": effective_to,
                    },
                }
            ],
        }


def test_world_diff_returns_flat_diff(client, container) -> None:
    container.library = FakeLibraryWithDiff()
    response = client.get("/api/library/worlds/wod-london/diff?from=2&to=5")
    assert response.status_code == 200
    body = response.json()
    assert body["world_id"] == "wod-london"
    assert body["from_version"] == 2
    assert body["to_version"] == 5
    assert body["added"] == []
    assert body["removed"] == []
    assert len(body["changed"]) == 1
    changed = body["changed"][0]
    assert changed["path"] == "character/alistair"
    assert changed["before"] is None
    assert changed["after"]["frontmatter"]["clan"] == "Ventrue"


def test_world_diff_defaults_to_latest_when_no_to(client, container) -> None:
    container.library = FakeLibraryWithDiff()
    response = client.get("/api/library/worlds/wod-london/diff?from=0")
    assert response.status_code == 200
    body = response.json()
    assert body["from_version"] == 0
    assert body["to_version"] == 5


def test_world_diff_404_when_world_missing(client, container) -> None:
    container.library = FakeLibraryWithDiff()
    response = client.get("/api/library/worlds/unknown/diff?from=0")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# §14 image preset create/edit/delete parity
# --------------------------------------------------------------------------- #


def test_create_image_preset(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.post(
        "/api/library/image-presets",
        json={
            "id": "noir-portraits",
            "name": "Noir portraits",
            "description": "High-contrast B&W.",
            "tags": ["noir"],
            "style_preamble": "stark shadows, 35mm film",
            "default_negative_prompt": "blurry, low quality",
            "default_params": {"width": 512, "height": 768, "steps": 24},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["asset_id"] == "noir-portraits"
    assert body["frontmatter"]["style_preamble"] == "stark shadows, 35mm film"
    assert ("image_preset", "noir-portraits") in fake.created


def test_update_image_preset(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.patch(
        "/api/library/image-presets/noir-portraits",
        json={"style_preamble": "softer film grain"},
    )
    assert response.status_code == 200
    assert response.json()["asset_id"] == "noir-portraits"
    assert ("image_preset_update", "noir-portraits") in fake.created


def test_get_image_preset_edit_returns_parsed_shape(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/image-presets/noir-portraits/edit")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "noir-portraits"
    assert body["style_preamble"] == "cinematic"
    assert body["default_params"] == {"steps": 18}


def test_delete_image_preset(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.delete("/api/library/image-presets/noir-portraits")
    assert response.status_code == 204
    assert ("image_preset_delete", "noir-portraits") in fake.created


# --------------------------------------------------------------------------- #
# §5 nested-frontmatter patch on world entity (image.* save-to-card)
# --------------------------------------------------------------------------- #


def test_update_entity_accepts_nested_image_patch(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.patch(
        "/api/library/worlds/wod-london/character/alistair",
        json={
            "frontmatter_patch": {
                "image": {
                    "base_prompt": "tall vampire in a London alley",
                    "negative_prompt": "blurry",
                    "canonical_seed": 14201,
                }
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["frontmatter"]["image"]["base_prompt"] == "tall vampire in a London alley"
    assert ("entity_update", "wod-london/character/alistair") in fake.created
