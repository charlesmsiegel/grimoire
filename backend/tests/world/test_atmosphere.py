"""§3 LLM-driven atmosphere auto-generation."""

from __future__ import annotations

import json

from grimoire.world.atmosphere import generate_atmosphere


class _FakeGateway:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict] = []

    async def complete(self, task, request, *, campaign_id=None, turn_id=None):
        self.calls.append({"task": task, "request": request})
        return type("Resp", (), {"text": self.response_text})()


async def test_generate_atmosphere_returns_parsed_dict() -> None:
    response = json.dumps(
        {
            "default_register": "low-fantasy formal",
            "default_palette": "warm umber",
            "mood_tags": ["weary", "hopeful"],
        }
    )
    gateway = _FakeGateway(response)
    out = await generate_atmosphere(
        gateway=gateway,
        world_id="w1",
        name="Karthos",
        tags=["fantasy", "medieval"],
        description="A weary kingdom on the edge of collapse.",
    )
    assert out["default_register"] == "low-fantasy formal"
    assert out["default_palette"] == "warm umber"
    assert out["mood_tags"] == ["weary", "hopeful"]
    assert gateway.calls[0]["task"] == "world_atmosphere"


async def test_generate_atmosphere_invalid_json_returns_empty() -> None:
    gateway = _FakeGateway("not json")
    out = await generate_atmosphere(
        gateway=gateway, world_id="w1", name="X", tags=[], description=""
    )
    assert out == {}


async def test_generate_atmosphere_no_gateway_returns_empty() -> None:
    out = await generate_atmosphere(gateway=None, world_id="w1", name="X", tags=[], description="")
    assert out == {}


async def test_generate_atmosphere_passes_inputs_through_template() -> None:
    response = "{}"
    gateway = _FakeGateway(response)
    await generate_atmosphere(
        gateway=gateway,
        world_id="kar",
        name="Karthos",
        tags=["fantasy"],
        description="weary kingdom",
    )
    req = gateway.calls[0]["request"]
    rendered = (req.system or "") + "\n" + "\n".join(m.content for m in req.messages)
    assert "Karthos" in rendered
    assert "weary kingdom" in rendered


async def test_generate_atmosphere_gateway_error_returns_empty() -> None:
    class _ErrorGateway:
        async def complete(self, task, request, *, campaign_id=None, turn_id=None):
            raise RuntimeError("boom")

    out = await generate_atmosphere(
        gateway=_ErrorGateway(),
        world_id="w1",
        name="X",
        tags=[],
        description="",
    )
    assert out == {}
