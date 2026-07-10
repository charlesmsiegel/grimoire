"""Ingest one rewritten campaign-log scene into a grimoire campaign, running it through
the real absorb pipeline. Built for the ingest-campaign-log skill — see
.claude/skills/ingest-campaign-log/SKILL.md for the end-to-end workflow this drives.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from grimoire.openrouter import OpenRouterClient  # noqa: E402
from grimoire.store import (  # noqa: E402
    absorb, appearances, campaigns, characters, chronicle, overlay, read_config, scenes,
)
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


def ensure_character(campaign_id: str, spec: dict) -> str:
    """Overlay-aware: a thin campaign's world may already have this character
    (by slug), so dedupe against the world/campaign union — never blank-card
    shadow a character that already exists in the world."""
    target = slugify(spec["name"])
    if target in overlay.character_refs(campaign_id):
        return target
    card = characters.blank_card(spec["name"])
    card["data"]["personality"] = spec.get("personality", "")
    card["data"]["description"] = spec.get("description", "")
    aid, _ = overlay.create_character(campaign_id, spec["name"], "main", card)
    return aid


def ensure_location(campaign_id: str, spec: dict) -> str:
    target = slugify(spec["name"])
    existing = {e["id"] for e in overlay.list_entities(campaign_id, "locations")}
    if target in existing:
        return target
    return overlay.create_entity(campaign_id, "locations", spec["name"], body=spec.get("notes", ""))


def resolve_version(cid: str, kind: str, actor_id: str) -> str:
    """Overlay-aware: a thin campaign's cast is usually still inherited (never
    appeared/materialized), so this must resolve across the world/campaign
    union, not just the campaign's own copy."""
    if kind == "pcs":
        from grimoire.store import pcs
        return pcs.read_pc(overlay.pc_root(cid, actor_id), actor_id)["meta"]["default_version"]
    return characters.read_character(overlay.char_root(cid, actor_id), actor_id)["meta"]["default_version"]


def build_scene(cid: str, scene: dict) -> str:
    for spec in scene.get("new_characters", []):
        ensure_character(cid, spec)
    for spec in scene.get("new_locations", []):
        ensure_location(cid, spec)

    sid = scenes.create_scene(cid, scene["title"])
    if scene.get("date"):
        sid = scenes.set_datetime(cid, sid, scene["date"])["id"]
    if scene.get("location"):
        scenes.set_location(cid, sid, scene["location"])
    for turn in scene["turns"]:
        scenes.append_message(cid, sid, turn["role"], turn["content"], speaker=turn.get("speaker"))
    for actor in scene["characters"]:
        kind, aid = actor["kind"], actor["id"]
        vid = resolve_version(cid, kind, aid)
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


async def ingest_one_scene(cid: str, scene: dict, client, cfg: dict) -> dict:
    manifest = load_manifest(cid)
    key = scene["key"]
    entry = manifest.get(key)
    if entry and entry.get("status") == "done":
        return {"key": key, **entry, "status": "skipped"}

    # build_scene runs at most once per key: an "in_progress" entry means a prior
    # attempt already minted the scene and died before absorb/apply completed, so
    # this retry resumes with that sid instead of creating a duplicate scene. There
    # remains a narrow window — a crash between apply_scene succeeding and the
    # manifest save below — where a retry re-absorbs an already-applied scene; see
    # SKILL.md for that residual risk.
    if entry and entry.get("status") == "in_progress" and entry.get("sid"):
        sid = entry["sid"]
    else:
        sid = build_scene(cid, scene)
        manifest[key] = {"status": "in_progress", "sid": sid}
        save_manifest(cid, manifest)

    result = await run_absorb(cid, sid, client, cfg)
    applied = apply_scene(cid, sid, result["parsed"], result["edits"])
    manifest[key] = {"status": "done", "sid": sid, "one_line": result["parsed"]["one_line"],
                     "applied": applied}
    save_manifest(cid, manifest)
    return {"key": key, **manifest[key], "status": "done"}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest a rewritten campaign-log scene into a grimoire campaign.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="create (or find) the target campaign")
    p_setup.add_argument("--world", required=True)
    p_setup.add_argument("--name", required=True)

    p_ingest = sub.add_parser("ingest", help="ingest one scene JSON file")
    p_ingest.add_argument("--campaign", required=True)
    p_ingest.add_argument("--input", required=True, type=Path)

    p_status = sub.add_parser("status", help="print the ingest manifest")
    p_status.add_argument("--campaign", required=True)

    args = ap.parse_args()
    if args.cmd == "setup":
        print(ensure_campaign(args.name, args.world))
        return 0
    if args.cmd == "status":
        print(json.dumps(load_manifest(args.campaign), indent=2, sort_keys=True))
        return 0

    scene = json.loads(args.input.read_text(encoding="utf-8"))
    cfg = read_config()
    if not cfg["openrouter_key"]:
        print("error: OpenRouter key not configured (set it in grimoire's Configuration page)",
              file=sys.stderr)
        return 1
    client = OpenRouterClient()
    result = asyncio.run(ingest_one_scene(args.campaign, scene, client, cfg))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
