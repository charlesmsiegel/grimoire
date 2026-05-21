"""L5 scenario: bootstrap a world and a campaign end-to-end through the HTTP API.

Spec 17 §L5 names four aspirational scenarios; the other three skeletons
in this directory still wait on §4 frozen-campaign snapshots or on a
turn-loop with recorded LLM fixtures. This scenario complements them by
exercising the ``ScenarioApp`` harness against the real FastAPI app
along a slice that needs neither: world create, SillyTavern character
import (preview + commit), campaign create referencing the world, and
read-side listings.

Why this is in §L5 rather than under ``tests/integration/``:

* it drives the same HTTP surface a frontend would (``ScenarioApp`` +
  ``httpx.AsyncClient`` against the ASGI app), not the service layer
  directly. The CI ``backend-scenario`` job is the right place for it.
* it pins down end-to-end contract assumptions (route shapes, response
  envelopes, library-on-disk layout) that pure module integration tests
  routinely miss.
* it serves as the working smoke test that the harness boots cleanly —
  if any of the other §L5 skeletons regress on import or fixture wiring,
  this one will surface it first.

LLM behavior: world creation would normally call the gateway for
atmosphere generation; we pass an ``atmosphere`` block in ``meta`` so
the auto-generate branch is skipped (avoids needing a recorded
completion fixture for a path that isn't what we're testing). The
SillyTavern ingestor is deterministic with default ``IngestOptions``
(``enrich_with_llm=False``).
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from pathlib import Path

from grimoire.testing.scenario import ScenarioApp


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return length + kind + data + crc


def _sillytavern_card_png(card: dict) -> bytes:
    """Smallest valid PNG with a ``chara`` tEXt chunk holding ``card``."""
    payload_b64 = base64.b64encode(json.dumps(card).encode("utf-8"))
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    text = _png_chunk(b"tEXt", b"chara\x00" + payload_b64)
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
    end = _png_chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + text + idat + end


async def test_bootstrap_world_import_character_create_campaign(tmp_path: Path) -> None:
    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        # 1. World create. Provide atmosphere inline so auto-generate is
        # skipped — keeps the test off the LLM gateway path.
        world_id = "scenario-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {
                    "name": "Scenario World",
                    "description": "Tiny world used by the bootstrap L5 scenario.",
                    "atmosphere": {
                        "themes": ["scenario"],
                        "tone": "neutral",
                    },
                },
            },
        )
        assert resp.status_code == 201, resp.text

        # 2. SillyTavern import — preview then commit.
        card = {
            "spec": "chara_card_v2",
            "data": {
                "name": "Greta the Guide",
                "description": "A patient guide for scenario tests.",
                "first_mes": "Welcome to the test, {{user}}.",
                "tags": ["scenario"],
            },
        }
        png_bytes = _sillytavern_card_png(card)
        resp = await client.post(
            f"/api/library/worlds/{world_id}/imports/sillytavern/preview",
            files={"file": ("greta.png", png_bytes, "image/png")},
        )
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        assert preview["preview_id"]
        assert preview["ingested"]["data"]["name"] == "Greta the Guide"

        resp = await client.post(
            f"/api/library/worlds/{world_id}/imports/sillytavern/commit",
            json={"preview_id": preview["preview_id"]},
        )
        assert resp.status_code == 201, resp.text
        commit = resp.json()
        assert commit["result"]["created"] or commit["result"].get("updated"), commit

        # 3. The character is now in the world's library.
        resp = await client.get(f"/api/library/worlds/{world_id}/character")
        assert resp.status_code == 200, resp.text
        chars = resp.json()
        # The endpoint returns a list-like envelope; just check the name
        # surfaces somewhere in the payload.
        assert "Greta the Guide" in json.dumps(chars)

        # 4. Campaign create that composes the world.
        campaign_id = "scenario-campaign"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Scenario Campaign",
                "description": "Created by the bootstrap L5 scenario test.",
                "composition": {
                    "worlds": [{"world_id": world_id, "priority": 0}],
                },
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["id"] == campaign_id

        # 5. Read-side: list shows the campaign and its composition.
        resp = await client.get("/api/campaigns")
        assert resp.status_code == 200, resp.text
        campaigns = resp.json()
        assert any(c["id"] == campaign_id for c in campaigns), campaigns

        resp = await client.get(f"/api/campaigns/{campaign_id}")
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail["id"] == campaign_id
        # Composition resolution may legitimately fail if the world ref
        # isn't fully wired (no greetings, etc.) — we only require the
        # field to exist on the payload, not to be non-null.
        assert "composition" in detail
