"""REST contract tests for the library/mechanics/plugins routes.

Each test injects a fake service into the app's :class:`ServiceContainer` and
verifies the router dispatches correctly and shapes the response as expected.
The fakes use ``async def`` to mirror the real service signatures so tests
fail if a handler forgets to ``await``.
"""

from __future__ import annotations

from typing import Any

from grimoire.types.composition import LibraryEntity, SettingMeta


class FakeLibrary:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    async def list_settings(self) -> list[SettingMeta]:
        return [SettingMeta(id="wod-london", name="WoD London", version=1)]

    async def get_setting(self, setting_id: str) -> SettingMeta:
        if setting_id != "wod-london":
            raise KeyError(setting_id)
        return SettingMeta(id="wod-london", name="WoD London", version=1)

    async def list_in_setting(self, setting_id: str, kind: str) -> list[LibraryEntity]:
        return [
            LibraryEntity(
                id="settings/wod-london/characters/alistair",
                setting_id=setting_id,
                kind="character",
                asset_id="alistair",
                name="Alistair",
                path="settings/wod-london/characters/alistair.md",
                frontmatter={"name": "Alistair"},
                body="",
            )
        ]

    async def list_style_guides(self) -> list[Any]:
        return []

    async def list_image_presets(self) -> list[Any]:
        return []

    async def list_greetings(self, setting_id: str) -> list[Any]:
        return []

    async def variants_of(self, asset_id: str, kind: str) -> list[Any]:
        return []

    async def dependents(self, setting_id: str, kind: str, entity_id: str) -> list[Any]:
        return []

    async def create_entity(self, *args: Any, **kwargs: Any) -> Any:
        self.created.append((args[1], args[2]))
        return LibraryEntity(
            id=f"settings/{args[0]}/{args[1]}s/{args[2]}",
            setting_id=args[0],
            kind=args[1],
            asset_id=args[2],
            name=args[2],
            path="",
            frontmatter=args[3],
            body=args[4],
        )


class FakeMechanics:
    def installed(self) -> list[Any]:
        return []

    async def rescan(self) -> dict[str, Any]:
        return {"added": [], "removed": [], "errors": []}


class FakePlugins:
    def __init__(self) -> None:
        self.manifests: dict[str, Any] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        self.saved: list[tuple[str, dict[str, Any]]] = []
        self.llm_providers: dict[str, Any] = {}

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


def test_list_settings(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/settings")
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
            "atmosphere": {},
            "defaults": {},
            "version": 1,
        }
    ]


def test_get_setting_404_when_unknown(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/settings/missing")
    assert response.status_code == 404


def test_list_setting_entities(client, container) -> None:
    container.library = FakeLibrary()
    response = client.get("/api/library/settings/wod-london/character")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["asset_id"] == "alistair"


def test_create_setting_entity(client, container) -> None:
    fake = FakeLibrary()
    container.library = fake
    response = client.post(
        "/api/library/settings/wod-london/character",
        json={"id": "new-pc", "frontmatter": {"name": "New PC"}, "body": "body"},
    )
    assert response.status_code == 201
    assert fake.created == [("character", "new-pc")]


def test_mechanics_installed(client, container) -> None:
    container.mechanics = FakeMechanics()
    response = client.get("/api/mechanics/installed")
    assert response.status_code == 200
    assert response.json() == []


def test_plugins_rescan(client, container) -> None:
    container.plugins = FakePlugins()
    response = client.post("/api/plugins/rescan")
    assert response.status_code == 200


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
            ModelInfo(id="anthropic/claude-opus-4-7", name="Claude Opus 4.7", context_window=200000),
            ModelInfo(id="openai/gpt-4o", name="GPT-4o", context_window=128000),
        ]
    )
    container.plugins = plugins
    response = client.get("/api/plugins/llm-x/models")
    assert response.status_code == 200
    body = response.json()
    assert [m["id"] for m in body] == ["anthropic/claude-opus-4-7", "openai/gpt-4o"]


def test_plugin_models_404_when_not_llm_provider(client, container) -> None:
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
    response = client.get("/api/library/settings")
    assert response.status_code == 503
    assert "library" in response.json()["detail"]
