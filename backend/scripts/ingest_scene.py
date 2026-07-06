"""Ingest one rewritten campaign-log scene into a grimoire campaign, running it through
the real absorb pipeline. Built for the ingest-campaign-log skill — see
.claude/skills/ingest-campaign-log/SKILL.md for the end-to-end workflow this drives.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.store import absorb, appearances, campaigns, characters, chronicle, entities, scenes  # noqa: E402
from grimoire.store.paths import slugify  # noqa: E402


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


def ensure_character(croot: Path, spec: dict) -> str:
    target = slugify(spec["name"])
    if target in characters.character_refs(croot):
        return target
    card = characters.blank_card(spec["name"])
    card["data"]["personality"] = spec.get("personality", "")
    card["data"]["description"] = spec.get("description", "")
    cid, _ = characters.create_character(croot, spec["name"], "main", card)
    return cid


def ensure_location(croot: Path, spec: dict) -> str:
    target = slugify(spec["name"])
    existing = {e["id"] for e in entities.list_entities(croot, "locations")}
    if target in existing:
        return target
    return entities.create_entity(croot, "locations", spec["name"], body=spec.get("notes", ""))


def resolve_version(croot: Path, kind: str, actor_id: str) -> str:
    if kind == "pcs":
        from grimoire.store import pcs
        return pcs.read_pc(croot, actor_id)["meta"]["default_version"]
    return characters.read_character(croot, actor_id)["meta"]["default_version"]


def build_scene(cid: str, scene: dict) -> str:
    croot = campaigns.campaign_root(cid)
    for spec in scene.get("new_characters", []):
        ensure_character(croot, spec)
    for spec in scene.get("new_locations", []):
        ensure_location(croot, spec)

    sid = scenes.create_scene(cid, scene["title"])
    if scene.get("date"):
        sid = scenes.set_datetime(cid, sid, scene["date"])["id"]
    if scene.get("location"):
        scenes.set_location(cid, sid, scene["location"])
    for turn in scene["turns"]:
        scenes.append_message(cid, sid, turn["role"], turn["content"], speaker=turn.get("speaker"))
    for actor in scene["characters"]:
        kind, aid = actor["kind"], actor["id"]
        vid = resolve_version(croot, kind, aid)
        appearances.appear(cid, sid, kind, aid, vid, "player" if kind == "pcs" else "npc")
    return sid


async def run_absorb(cid: str, sid: str, client, cfg: dict) -> dict:
    scene = scenes.read_scene(cid, sid)
    facts = chronicle.scene_facts(cid, sid)
    transcript = chronicle.transcript_text(scene["messages"])
    messages = absorb.build_prompt(
        transcript, facts, absorb.state_snapshot(cid, sid),
        absorb.relationships_snapshot(cid, sid), absorb.plot_snapshot(cid))
    text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    parsed = absorb.parse_output(text)
    edits = absorb.materialize(cid, sid, parsed)
    return {"parsed": parsed, "edits": edits}


def apply_scene(cid: str, sid: str, parsed: dict, edits: list[dict]) -> list[str]:
    facts = chronicle.scene_facts(cid, sid)
    chronicle.absorb(cid, {"id": sid, "one_line": parsed["one_line"], "summary": parsed["summary"],
                           "keywords": parsed["keywords"], **facts})
    chronicle.append_timeline(cid, parsed["timeline_events"])
    scenes.mark_absorbed(cid, sid, parsed["one_line"], parsed["summary"])
    return absorb.apply_edits(cid, edits, sid)
