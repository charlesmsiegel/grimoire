"""The scene-absorption extraction: one deterministic-primed LLM call producing the
chronicle summary plus proposed state/lore/authored edits, their diff materialization,
and their application. Prompt/parse only here + pure materialize/apply; the LLM call
lives in the route layer and the prompt text in templates/absorb/.
"""

from __future__ import annotations

import json

from .. import prompts
from . import (appearances, campaigns, changes, characters, chronicle, entities, pcs,
               playstate, plot, relationships)
from .paths import slugify


def _int05(v) -> int:
    try:
        return max(0, min(5, int(v)))
    except (ValueError, TypeError):
        return 0


def build_prompt(transcript: str, facts: dict, state_snapshot: dict | None = None,
                 rel_snapshot: str | None = None, plot_snapshot: str | None = None) -> list[dict]:
    return [{"role": "system", "content": prompts.render("absorb/system.j2")},
            {"role": "user", "content": prompts.render(
                "absorb/user.j2", facts=facts, state_snapshot=state_snapshot,
                rel_snapshot=rel_snapshot, plot_snapshot=plot_snapshot,
                transcript=transcript)}]


def _obj(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    raw = text[start:end + 1] if start != -1 and end > start else ""
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        obj = {}
    return obj if isinstance(obj, dict) else {}


def parse_output(text: str) -> dict:
    obj = _obj(text)

    def _str(e, f):
        # A JSON `null` is a present-but-empty value the model uses interchangeably with
        # omitting the key (e.g. "no existing id, this is new"); str(e.get(f, "")) turns
        # that null into the literal text "None" instead of "", corrupting ids/titles
        # downstream. `.get(f) or ""` collapses null and absent to the same "".
        return str(e.get(f) or "").strip()

    def _list(key, fields):
        out = []
        for e in obj.get(key, []):
            if isinstance(e, dict):
                out.append({f: _str(e, f) for f in fields})
        return out

    # Preserve key PRESENCE for knowledge: a field the model omitted must be left
    # untouched at materialize time (keep-on-omit), while an explicit "" clears it. So we
    # only carry knows/suspects into the row when the model actually returned them.
    cs_edits = []
    for e in obj.get("character_state_edits", []):
        if not isinstance(e, dict):
            continue
        row = {"id": _str(e, "id"), "current_state": _str(e, "current_state")}
        for k in ("knows", "suspects"):
            if k in e:
                row[k] = _str(e, k)
        cs_edits.append(row)

    rel_deltas = []
    for e in obj.get("relationship_deltas", []):
        if isinstance(e, dict):
            rel_deltas.append({"from": _str(e, "from"), "to": _str(e, "to"),
                               "trust": _int05(e.get("trust")), "affection": _int05(e.get("affection")),
                               "tension": _int05(e.get("tension")), "note": _str(e, "note")})

    plot_moves = []
    for e in obj.get("plot_movements", []):
        if isinstance(e, dict):
            status = _str(e, "status").lower()
            plot_moves.append({"id": _str(e, "id"), "title": _str(e, "title"),
                               "status": status if status in plot.STATUSES else "open",
                               "beat": _str(e, "beat")})

    return {
        "one_line": str(obj.get("one_line", "")).strip(),
        "summary": str(obj.get("summary", "")).strip(),
        "keywords": [str(k).strip() for k in obj.get("keywords", []) if str(k).strip()],
        "timeline_events": _list("timeline_events", ("date", "text")),
        "character_state_edits": cs_edits,
        "lore_edits": _list("lore_edits", ("id", "append")),
        "authored_edits": _list("authored_edits", ("id", "field", "text")),
        "relationship_deltas": rel_deltas,
        "bond_changes": _list("bond_changes", ("a", "b", "type")),
        "plot_movements": plot_moves,
    }


_CARD_FIELDS = ("description", "personality", "scenario")


def _char_name(croot, cid: str) -> str:
    try:
        return characters.read_character(croot, cid)["meta"].get("name", cid)
    except characters.CharacterNotFound:
        return cid


def _actor_exists(croot, token: str) -> bool:
    kind, _, aid = token.partition(":")
    try:
        if kind == "pcs":
            pcs.read_pc(croot, aid)
        elif kind == "characters":
            characters.read_character(croot, aid)
        else:
            return False
        return True
    except (characters.CharacterNotFound, pcs.PCNotFound):
        return False


def _entity_kind(croot, eid: str) -> str | None:
    for kind in ("lore", "locations"):
        try:
            entities.read_entity(croot, kind, eid)
            return kind
        except entities.EntityNotFound:
            continue
    return None


def materialize(cid: str, sid: str, parsed: dict) -> list[dict]:
    """Turn the parsed edit lists into before/after StagedEdits against the campaign
    copies. Targets that don't exist are dropped (tolerated, not an error)."""
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []

    for e in parsed.get("character_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        # The model echoes ids from the "Present: <kind>/<id>, ..." context line (or,
        # less reliably, a bare id) — strip any "characters/" or "characters:" prefix so
        # both forms resolve. playstate.py only tracks "characters" (not "pcs"), matching
        # its own docstring scope, so a pcs-prefixed id is dropped rather than misfiled.
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        char_id = rest if kind in ("characters", "pcs") else raw_id
        if kind == "pcs":
            continue
        try:
            characters.read_character(croot, char_id)
        except characters.CharacterNotFound:
            continue
        st = playstate.read_state(croot, char_id)
        cur_knows = st["knows"] if st else ""
        cur_suspects = st["suspects"] if st else ""
        # Keep-on-omit: an omitted knows/suspects preserves the stored value; an explicit
        # "" clears it. Prevents an absorb that only touches current_state from silently
        # erasing established knowledge.
        knows = e["knows"] if "knows" in e else cur_knows
        suspects = e["suspects"] if "suspects" in e else cur_suspects
        after = playstate.compose_body(e.get("current_state", ""), knows, suspects)
        if not after:
            continue
        before = playstate.compose_body(st["current_state"], cur_knows, cur_suspects) if st else ""
        if before == after:
            continue
        out.append({"id": f"character_state:{char_id}", "kind": "character_state",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — current state",
                    "field": "current_state",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("lore_edits", []):
        eid, append = e.get("id", ""), (e.get("append", "") or "").strip()
        if not eid or not append:
            continue
        kind = _entity_kind(croot, eid)
        if not kind:
            continue
        ent = entities.read_entity(croot, kind, eid)
        before = ent["body"].strip()
        after = (before + "\n\n" + append).strip()
        out.append({"id": f"lore:{eid}", "kind": "lore", "target": {"kind": kind, "id": eid},
                    "label": f"{ent['meta'].get('name', eid)} — {kind}", "field": "body",
                    "before": before, "after": after, "authored": False})

    for e in parsed.get("authored_edits", []):
        char_id, field, text = e.get("id", ""), e.get("field", ""), (e.get("text", "") or "").strip()
        if not char_id or field not in _CARD_FIELDS or not text:
            continue
        vid = appearances.locked_version(cid, "characters", char_id)
        if not vid:
            continue
        try:
            before = characters.read_card(croot, char_id, vid)["data"].get(field, "").strip()
        except (characters.CharacterNotFound, characters.VersionNotFound):
            continue
        out.append({"id": f"authored:{char_id}:{field}", "kind": "authored",
                    "target": {"kind": "characters", "id": char_id},
                    "label": f"{_char_name(croot, char_id)} — {field} (card edit)",
                    "field": field, "before": before, "after": text, "authored": True})

    for e in parsed.get("relationship_deltas", []):
        frm, to = e.get("from", ""), e.get("to", "")
        if not _actor_exists(croot, frm) or not _actor_exists(croot, to):
            continue
        payload = {"from": frm, "to": to, "trust": e.get("trust", 0), "affection": e.get("affection", 0),
                   "tension": e.get("tension", 0), "note": e.get("note", "")}
        after = relationships._render_feeling(payload)
        cur = relationships.get_feeling(cid, frm, to)
        before = relationships._render_feeling(cur) if cur else ""
        if before == after:
            continue
        out.append({"id": f"feeling:{relationships.feeling_key(frm, to)}", "kind": "relationship",
                    "target": {"kind": "relationships", "id": relationships.feeling_key(frm, to)},
                    "label": f"{relationships.actor_name(croot, frm)} → {relationships.actor_name(croot, to)}",
                    "field": "feeling", "before": before, "after": after, "authored": False,
                    "payload": payload})

    for e in parsed.get("bond_changes", []):
        a_tok, b_tok, typ = e.get("a", ""), e.get("b", ""), (e.get("type", "") or "").strip()
        if not typ or not _actor_exists(croot, a_tok) or not _actor_exists(croot, b_tok):
            continue
        cur = relationships.get_bond(cid, a_tok, b_tok)
        before = cur["type"] if cur else ""
        if before == typ:
            continue
        out.append({"id": f"bond:{relationships.bond_key(a_tok, b_tok)}", "kind": "bond",
                    "target": {"kind": "relationships", "id": relationships.bond_key(a_tok, b_tok)},
                    "label": f"{relationships.actor_name(croot, a_tok)} & {relationships.actor_name(croot, b_tok)}",
                    "field": "bond", "before": before, "after": typ, "authored": False,
                    "payload": {"a": a_tok, "b": b_tok, "type": typ}})

    try:
        threads = plot.read(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json: skip plot movements, don't 500
        threads = {}
    seen_pids: set[str] = set()
    for e in parsed.get("plot_movements", []):
        beat = (e.get("beat", "") or "").strip()
        if not beat:
            continue
        mid = (e.get("id", "") or "").strip()
        title = (e.get("title", "") or "").strip()
        status = e.get("status", "open")
        if mid:
            pid = mid
        elif any(c.isalnum() for c in title):
            pid = slugify(title)  # new thread — needs a title with real content
        else:
            continue  # no id and no usable title -> drop
        if pid in seen_pids:
            continue  # one edit per thread per scene (avoids duplicate ids / double-apply)
        seen_pids.add(pid)
        cur = threads.get(pid)
        if isinstance(cur, dict):  # existing thread (by id, or a new title that collides)
            beats = cur.get("beats") or []
            before = (f"{cur.get('status', 'open')} — {beats[-1]['text']}"
                      if beats else cur.get("status", "open"))
            disp_title = cur.get("title") or title or pid  # keep the stored title
        else:
            before, disp_title = "", title or pid
        out.append({"id": f"plot:{pid}", "kind": "plot",
                    "target": {"kind": "plot", "id": pid},
                    "label": f"{disp_title} — {status}",
                    "field": "beat", "before": before, "after": beat, "authored": False,
                    "payload": {"id": pid, "title": disp_title, "status": status, "scene": sid}})

    return out


_BROWSABLE_KINDS = ("character_state", "lore", "authored")


def apply_edits(cid: str, edits: list[dict], sid: str | None = None) -> list[str]:
    """Apply each approved StagedEdit to the campaign copies. Best-effort: a missing or
    broken target is skipped. Returns the ids actually applied. When `sid` is given, the
    before/after of each applied *browsable* edit (characters/lore/locations) is captured
    into changes.json (the latest write-back delta per record)."""
    croot = campaigns.campaign_root(cid)
    applied: list[str] = []
    recorded: dict[str, list[dict]] = {}
    for e in edits:
        try:
            kind, target, after = e["kind"], e["target"], e.get("after", "")
            if kind == "character_state":
                playstate.write_state(croot, target["id"], after)
            elif kind == "lore":
                entities.update_entity(croot, target["kind"], target["id"], body=after)
            elif kind == "authored":
                if e["field"] not in _CARD_FIELDS:
                    continue  # re-guard: PUT edits are client-supplied, not re-materialized
                vid = appearances.locked_version(cid, "characters", target["id"])
                card = characters.read_card(croot, target["id"], vid)
                card["data"][e["field"]] = after
                characters.update_version(croot, target["id"], vid, card)
            elif kind == "relationship":
                p = e["payload"]
                relationships.set_feeling(cid, p["from"], p["to"], p["trust"], p["affection"],
                                          p["tension"], p.get("note", ""))
            elif kind == "bond":
                p = e["payload"]
                relationships.set_bond(cid, p["a"], p["b"], p["type"])
            elif kind == "plot":
                p = e["payload"]
                plot.set_movement(cid, p["id"], p["title"], p["status"], after, p["scene"])
            else:
                continue
            applied.append(e["id"])
            if sid and kind in _BROWSABLE_KINDS:
                ref = f"{target['kind']}/{target['id']}"
                recorded.setdefault(ref, []).append(
                    {"field": e.get("field", ""), "label": e.get("label", ""),
                     "before": e.get("before", ""), "after": after})
        except Exception:  # noqa: BLE001 — best-effort per edit
            continue
    if sid:
        changes.record(cid, sid, recorded)
    return applied


def relationships_snapshot(cid: str, sid: str) -> str:
    """Rendered present-cast feelings/bonds block (feeds the prompt). Tolerant of a
    garbled relationships.json (returns "" rather than failing the extraction)."""
    try:
        croot = campaigns.campaign_root(cid)
        tokens = [f"{a['kind']}:{a['id']}" for a in appearances.scene_cast(cid, sid)]
        return "\n".join(relationships.render_present(cid, tokens, lambda t: relationships.actor_name(croot, t)))
    except Exception:  # noqa: BLE001 — garbled relationships.json: omit, don't crash
        return ""


def plot_snapshot(cid: str) -> str:
    """Rendered open/advanced plot threads (id + title + status + latest beat) — feeds the
    prompt so the model advances the right thread. Campaign-wide (not scene-scoped);
    tolerant of a garbled plot.json."""
    return "\n".join(plot.render_open(cid, with_id=True))


def state_snapshot(cid: str, sid: str) -> dict:
    """Present NPCs' existing standing snapshot — current_state with any Knows/Suspects
    folded in (via _snapshot_line) — keyed by display name (feeds the prompt)."""
    croot = campaigns.campaign_root(cid)
    out: dict[str, str] = {}
    for a in appearances.scene_cast(cid, sid):
        if a["role"] != "npc" or a["kind"] != "characters":
            continue
        st = playstate.read_state(croot, a["id"])
        if st and (st["current_state"] or st["knows"] or st["suspects"]):
            try:
                name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            except characters.CharacterNotFound:
                name = a["id"]
            out[name] = _snapshot_line(st)
    return out


def _snapshot_line(st: dict) -> str:
    return prompts.render("snippets/state_snapshot_line.j2", st=st)
