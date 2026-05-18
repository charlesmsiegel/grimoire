"""L5 scenario: create campaign, import SillyTavern character, run 5 turns,
export to EPUB, verify EPUB validates (spec 17 §L5).

Status: skipped end-to-end because the SillyTavern-import HTTP endpoint
is not yet exposed (the underlying service method
``CharactersService.import_sillytavern`` exists, but no REST route).
The test is written against the documented spec endpoints so it picks
up automatically once the route lands. EPUBCheck integration is also
gated on the ``epubcheck`` binary being installed locally.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from grimoire.testing.scenario import ScenarioApp


@pytest.mark.skip(
    reason=(
        "HTTP endpoint POST /api/library/characters/import-sillytavern "
        "not yet implemented (only CharactersService.import_sillytavern "
        "exists at the service layer); written against the documented "
        "spec endpoint so it activates once the route lands."
    )
)
async def test_create_import_run_export_epub(tmp_path: Path) -> None:
    if shutil.which("epubcheck") is None:
        pytest.skip("epubcheck binary not available; skipping EPUB validation step")

    async with ScenarioApp(tmp_path) as app:
        assert app.client is not None

        # 1. Create the campaign.
        resp = await app.client.post(
            "/api/campaigns",
            json={
                "id": "scenario-epub-1",
                "name": "Scenario: import + export",
                "description": "L5 scenario",
            },
        )
        assert resp.status_code == 201, resp.text

        # 2. Import a SillyTavern v2 character card.
        card_bytes = b"<placeholder character-card bytes>"
        resp = await app.client.post(
            "/api/library/characters/import-sillytavern",
            params={"target_setting_id": "default"},
            content=card_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 201, resp.text

        # 3. Run 5 turns.
        for i in range(5):
            resp = await app.client.post(
                "/api/campaigns/scenario-epub-1/turns",
                json={"pc_ref": "pc1", "text": f"player turn {i}"},
            )
            assert resp.status_code == 200, resp.text

        # 4. Export to EPUB.
        resp = await app.client.post(
            "/api/campaigns/scenario-epub-1/export",
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
        import subprocess

        check = subprocess.run(
            ["epubcheck", str(epub_path)],
            capture_output=True,
            text=True,
        )
        assert check.returncode == 0, check.stdout + check.stderr
