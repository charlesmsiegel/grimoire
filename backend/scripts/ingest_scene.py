"""Ingest one rewritten campaign-log scene into a grimoire campaign, running it through
the real absorb pipeline. Built for the ingest-campaign-log skill — see
.claude/skills/ingest-campaign-log/SKILL.md for the end-to-end workflow this drives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import campaigns  # noqa: E402


def ensure_campaign(name: str, world_id: str) -> str:
    for c in campaigns.list_campaigns():
        if c["name"] == name and c["world"] == world_id:
            return c["id"]
    return campaigns.create_campaign(name, world_id)


def _manifest_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "ingest_manifest.json"


def load_manifest(cid: str) -> dict:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_manifest(cid: str, data: dict) -> None:
    _manifest_path(cid).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
