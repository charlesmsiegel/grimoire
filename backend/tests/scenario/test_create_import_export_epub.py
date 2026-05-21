"""L5 scenario: create a campaign, import a SillyTavern character, run 5
turns, export to EPUB, verify EPUB validates (spec 17 §L5).

Status: the bootstrap half (world create → SillyTavern preview/commit →
campaign create with composition) is exercised in the live ``test_bootstrap_world_campaign``
scenario. This test extends that by running 5 turns through the
orchestrator and then exporting the result to EPUB.

It still skips end-to-end because two pieces aren't checked in yet:

* recorded LLM completion fixtures for the turn loop — the orchestrator
  routes every model call through :class:`RecordReplayLLM` in scenario
  mode, and the per-turn prompts hash differently between runs without
  pinned completions on disk;
* the ``epubcheck`` binary, which the final validation step shells out
  to. We skip cleanly when it's missing so contributors without a Java
  install can still run the suite.

When the fixtures land, drop the fixtures-missing skip and the test
will run for real. The harness (:class:`ScenarioApp`) is already
verified by ``test_bootstrap_world_campaign``.
"""

from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from grimoire.testing.scenario import ScenarioApp


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return length + kind + data + crc


def _sillytavern_card_png(card: dict) -> bytes:
    payload_b64 = base64.b64encode(json.dumps(card).encode("utf-8"))
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    text = _png_chunk(b"tEXt", b"chara\x00" + payload_b64)
    idat = _png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
    end = _png_chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + text + idat + end


_TURN_LOOP_FIXTURES_MISSING = (
    "LLM completion fixtures for the 5-turn loop are not checked in yet; "
    "record them via `pytest --record` against the documented turn prompts. "
    "See `backend/tests/fixtures/llm/README.md`."
)


@pytest.mark.skip(reason=_TURN_LOOP_FIXTURES_MISSING)
async def test_create_import_run_export_epub(tmp_path: Path) -> None:
    if shutil.which("epubcheck") is None:
        pytest.skip("epubcheck binary not available; skipping EPUB validation step")

    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None
        client = app.client

        # 1. World + SillyTavern import (preview/commit per the real API).
        world_id = "scenario-epub-world"
        resp = await client.post(
            "/api/library/worlds",
            json={
                "id": world_id,
                "meta": {"name": "EPUB Scenario World", "atmosphere": {"tone": "neutral"}},
            },
        )
        assert resp.status_code == 201, resp.text

        card = {
            "spec": "chara_card_v2",
            "data": {
                "name": "Ada the Storyteller",
                "description": "Narrates patiently.",
                "first_mes": "Shall we begin, {{user}}?",
                "tags": ["scenario"],
            },
        }
        png_bytes = _sillytavern_card_png(card)
        resp = await client.post(
            f"/api/library/worlds/{world_id}/imports/sillytavern/preview",
            files={"file": ("ada.png", png_bytes, "image/png")},
        )
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        resp = await client.post(
            f"/api/library/worlds/{world_id}/imports/sillytavern/commit",
            json={"preview_id": preview["preview_id"]},
        )
        assert resp.status_code == 201, resp.text

        # 2. Create the campaign composing the world.
        campaign_id = "scenario-epub-1"
        resp = await client.post(
            "/api/campaigns",
            json={
                "id": campaign_id,
                "name": "Scenario: import + export",
                "description": "L5 scenario",
                "composition": {"worlds": [{"world_id": world_id, "priority": 0}]},
            },
        )
        assert resp.status_code == 201, resp.text

        # 3. Run 5 turns. SubmitTurnPayload takes ``pc_ref`` + ``text``.
        for i in range(5):
            resp = await client.post(
                f"/api/campaigns/{campaign_id}/turns",
                json={"pc_ref": "pc1", "text": f"player turn {i}"},
            )
            assert resp.status_code == 200, resp.text

        # 4. Export to EPUB via the global ``epub`` adapter (registered in
        # the lifespan; see :class:`EpubAdapter`).
        resp = await client.post(
            f"/api/campaigns/{campaign_id}/export",
            json={
                "adapter_id": "epub",
                "selection": {"include": "all"},
                "options": {},
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()
        epub_path = Path(result["path"])
        assert epub_path.is_file()

        # 5. Validate with epubcheck.
        check = subprocess.run(
            ["epubcheck", str(epub_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert check.returncode == 0, check.stdout + check.stderr
