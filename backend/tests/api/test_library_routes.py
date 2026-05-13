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
    async def list_installed(self) -> list[Any]:
        return []

    async def rescan(self) -> dict[str, Any]:
        return {"added": [], "removed": [], "errors": []}


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


def test_library_503_when_unset(client, container) -> None:
    # Lifespan auto-wires a LibraryService; clear it so we can verify the
    # 503 branch in api/deps.py:_require for any service that goes missing.
    container.library = None
    response = client.get("/api/library/settings")
    assert response.status_code == 503
    assert "library" in response.json()["detail"]
