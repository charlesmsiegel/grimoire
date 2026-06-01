"""REST contract tests for the alternates (swipes) routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class _FakeAlternate:
    id: str
    post_id: str
    text: str
    delta_set_id: str
    pinned: bool = False
    is_primary: bool = False


@dataclass
class _FakePost:
    id: str
    primary_alternate_id: str | None = None
    alternates: list[_FakeAlternate] = field(default_factory=list)


@dataclass
class _FakeScene:
    id: str
    campaign_id: str


class FakeScenes:
    """Just enough of SceneManager to satisfy the alternates router lookups."""

    def __init__(self) -> None:
        self.scene = _FakeScene(id="s1", campaign_id="c1")
        self.post = _FakePost(
            id="p1",
            primary_alternate_id="a_primary",
            alternates=[
                _FakeAlternate(
                    id="a_primary",
                    post_id="p1",
                    text="primary",
                    delta_set_id="ds_p",
                    is_primary=True,
                ),
                _FakeAlternate(id="a_alt", post_id="p1", text="alt", delta_set_id="ds_b"),
            ],
        )

    async def get_scene(self, scene_id: str) -> _FakeScene:
        if scene_id != self.scene.id:
            raise KeyError(scene_id)
        return self.scene

    async def get_posts(self, scene_id: str) -> list[_FakePost]:
        if scene_id != self.scene.id:
            return []
        return [self.post]


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def regenerate_post(
        self,
        *,
        campaign_id: str,
        post_id: str,
        steering_hint: str | None = None,
        model_override: str | None = None,
    ) -> Any:
        self.calls.append(("regenerate", campaign_id, post_id, steering_hint, model_override))

        @dataclass
        class _Result:
            post_id: str
            new_alternate_id: str
            delta_set_id: str

        return _Result(post_id=post_id, new_alternate_id="a_new", delta_set_id="ds_new")

    async def switch_primary_alternate(
        self, *, campaign_id: str, post_id: str, alternate_id: str
    ) -> dict[str, Any]:
        self.calls.append(("switch", campaign_id, post_id, alternate_id))
        return {"unchanged": False, "post_id": post_id, "from": "a_primary", "to": alternate_id}

    async def pin_alternate(self, *, post_id: str, alternate_id: str, pinned: bool) -> None:
        self.calls.append(("pin", post_id, alternate_id, pinned))

    async def delete_alternate(self, *, post_id: str, alternate_id: str) -> None:
        self.calls.append(("delete", post_id, alternate_id))

    async def delete_post_cascade(self, campaign_id: str, scene_id: str, post_id: str) -> Any:
        self.calls.append(("cascade_delete", campaign_id, scene_id, post_id))

        @dataclass
        class _Result:
            deleted_post_ids: list
            reversed_turn_ids: list
            requeued_review_ids: list
            warnings: list

        return _Result(
            deleted_post_ids=[post_id],
            reversed_turn_ids=["T1"],
            requeued_review_ids=[],
            warnings=[],
        )


@pytest.fixture
def wire(container, client):
    container.scenes = FakeScenes()
    container.orchestrator = FakeOrchestrator()
    return container


def test_regenerate_route_calls_orchestrator(wire, client) -> None:
    response = client.post(
        "/api/campaigns/c1/scenes/s1/posts/p1/regenerate",
        json={"steering_hint": "darker"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["new_alternate_id"] == "a_new"
    assert wire.orchestrator.calls[0] == ("regenerate", "c1", "p1", "darker", None)


def test_regenerate_without_body(wire, client) -> None:
    response = client.post("/api/campaigns/c1/scenes/s1/posts/p1/regenerate")
    assert response.status_code == 200
    assert wire.orchestrator.calls[0] == ("regenerate", "c1", "p1", None, None)


def test_regenerate_rejects_wrong_campaign(wire, client) -> None:
    response = client.post("/api/campaigns/other/scenes/s1/posts/p1/regenerate")
    assert response.status_code == 404


def test_regenerate_rejects_unknown_post(wire, client) -> None:
    response = client.post("/api/campaigns/c1/scenes/s1/posts/p_nope/regenerate")
    assert response.status_code == 404


def test_list_alternates_returns_post_alternates(wire, client) -> None:
    response = client.get("/api/campaigns/c1/scenes/s1/posts/p1/alternates")
    assert response.status_code == 200
    body = response.json()
    assert body["post_id"] == "p1"
    assert body["primary_alternate_id"] == "a_primary"
    assert {a["id"] for a in body["alternates"]} == {"a_primary", "a_alt"}


def test_switch_primary_route(wire, client) -> None:
    response = client.post("/api/campaigns/c1/scenes/s1/posts/p1/alternates/a_alt/primary")
    assert response.status_code == 200
    body = response.json()
    assert body["to"] == "a_alt"
    assert wire.orchestrator.calls[0] == ("switch", "c1", "p1", "a_alt")


def test_pin_alternate_route(wire, client) -> None:
    response = client.post(
        "/api/campaigns/c1/scenes/s1/posts/p1/alternates/a_alt/pin",
        json={"pinned": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pinned"] is True
    assert wire.orchestrator.calls[0] == ("pin", "p1", "a_alt", True)


def test_delete_alternate_route_returns_204(wire, client) -> None:
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p1/alternates/a_alt")
    assert response.status_code == 204
    assert wire.orchestrator.calls[0] == ("delete", "p1", "a_alt")


def test_latest_post_only_violation_is_400(wire, client) -> None:
    from grimoire.orchestrator.errors import LatestPostOnlyError

    async def boom(**_kw):
        raise LatestPostOnlyError("p1")

    wire.orchestrator.regenerate_post = boom  # type: ignore[method-assign]
    response = client.post("/api/campaigns/c1/scenes/s1/posts/p1/regenerate")
    assert response.status_code == 400


def test_delete_primary_is_409(wire, client) -> None:
    from grimoire.orchestrator.errors import CannotDeletePrimaryError

    async def boom(**_kw):
        raise CannotDeletePrimaryError("p1", "a_alt")

    wire.orchestrator.delete_alternate = boom  # type: ignore[method-assign]
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p1/alternates/a_alt")
    assert response.status_code == 409


def test_unknown_alternate_is_404(wire, client) -> None:
    from grimoire.orchestrator.errors import AlternateNotFoundError

    async def boom(**_kw):
        raise AlternateNotFoundError("p1", "a_nope")

    wire.orchestrator.switch_primary_alternate = boom  # type: ignore[method-assign]
    response = client.post("/api/campaigns/c1/scenes/s1/posts/p1/alternates/a_nope/primary")
    assert response.status_code == 404


def test_delete_post_route_calls_orchestrator(wire, client) -> None:
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p1")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted_post_ids"] == ["p1"]
    assert body["reversed_turn_ids"] == ["T1"]
    assert wire.orchestrator.calls[0] == ("cascade_delete", "c1", "s1", "p1")


def test_delete_post_rejects_wrong_campaign(wire, client) -> None:
    response = client.delete("/api/campaigns/other/scenes/s1/posts/p1")
    assert response.status_code == 404


def test_delete_post_rejects_unknown_post(wire, client) -> None:
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p_nope")
    assert response.status_code == 404


def test_delete_post_closed_scene_is_409(wire, client) -> None:
    from grimoire.orchestrator.errors import SceneClosedError

    async def boom(**_kw):
        raise SceneClosedError("s1")

    wire.orchestrator.delete_post_cascade = boom  # type: ignore[method-assign]
    response = client.delete("/api/campaigns/c1/scenes/s1/posts/p1")
    assert response.status_code == 409
