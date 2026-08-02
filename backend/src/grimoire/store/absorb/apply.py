"""Applying approved StagedEdits back into the campaign copies.

One branch per edit kind, each writing through the store module that owns that
record. Sheet edits are handed to `audit/apply.py`, which owns the sheet
conflict contract; weather rows go to `weather._apply_weather`.
"""

from __future__ import annotations

from .. import (cards, changes, characters, dossiers, groupstate, overlay,
                playstate, plot, relationships)
from ..appearances import (paths as appearances_paths,
                           transitions as appearances_transitions,
                           versions as appearances_versions)
from ..audit import apply as audit_apply
from ..campaigns import paths as campaigns_paths
from ..scenes import moment as scenes_moment, read as scenes_read
from ..sheets import paths as sheets_paths
from . import conflicts, materializer, weather

_BROWSABLE_KINDS = ("character_state", "dossier", "lore", "authored", "new_character",
                    "new_location", "new_lore")


def apply_edits(cid: str, edits: list[dict],
                sid: str | None = None) -> tuple[list[str], list[dict]]:
    """Apply each approved StagedEdit to the campaign copies. Best-effort: a missing or
    broken non-sheet target is skipped. Returns (applied ids, failures) -- some edits
    have an error contract and are never silently skipped: each failure is
    {"id", "reason", "kind": "conflict"|"error"}. Sheet edits report through it, and so
    does a dossier whose stored text moved since it was staged -- the reviewer approved
    that paragraph and must not be told the save succeeded while it was dropped.

    Every other kind whose write REPLACES a stored value is held to the same rule by
    `conflicts` (#111): an edit whose target no longer matches its staged `before` is
    reported as a conflict rather than applied, unless the row carries the reviewer's
    `resolve` (replace/merge). Callers that want the reviewer to choose before anything
    lands should run `conflicts.check_conflicts` first -- by the time a conflict is a
    failure here, the chronicle around it has already been written. When
    `sid` is given, the before/after
    of each applied *browsable* edit (characters/lore/locations) is captured into
    changes.json (the latest write-back delta per record); sheet edits are never
    browsable and never land there -- the sheet itself is the record."""
    croot = campaigns_paths.campaign_root(cid)
    applied: list[str] = []
    failures: list[dict] = []
    recorded: dict[str, list[dict]] = {}
    # One pass over the whole batch before the first write (#111). Applying one
    # edit can move the record the next one was staged against, so a check
    # interleaved with the writes would report the batch as contradicting itself.
    verdicts = conflicts.batch_verdicts(cid, edits)
    for e, verdict in zip(edits, verdicts):
        if not isinstance(e, dict):
            continue  # malformed batch item: skip, best-effort
        if verdict is not None:
            # Reported, never silently dropped: the reviewer approved this row
            # and would otherwise read the save as a success (same contract the
            # dossier branch below has had since #235).
            failures.append({"id": e.get("id", ""), "kind": "conflict",
                             "reason": verdict["reason"]})
            continue
        if e.get("kind") == "sheet":
            eid = e.get("id", "")
            if not isinstance(eid, str) or not eid:
                failures.append({"id": "", "kind": "error",
                                       "reason": "sheet edit missing id"})
                continue  # rejected before apply_delta runs: a nameless mutation can never land
            if not sid:
                failures.append({"id": eid, "kind": "error",
                                       "reason": "sheet edits need a scene id"})
                continue
            try:
                audit_apply.apply_delta(cid, sid, e)
                applied.append(eid)
            except sheets_paths.SheetConflict as exc:
                failures.append({"id": eid, "kind": "conflict",
                                       "reason": str(exc)})
            except sheets_paths.SheetError as exc:
                failures.append({"id": eid, "kind": "error",
                                       "reason": str(exc)})
            continue
        try:
            kind, target, after = e["kind"], e["target"], e.get("after", "")
            extra_fields: list[dict] = []
            if kind == "weather":
                if not weather._apply_weather(cid, e, after):
                    continue  # skipped, not applied: nothing was written
            elif kind == "character_state":
                playstate.write_state(croot, target["id"], after)
            elif kind == "group_state":
                groupstate.write_state(croot, target["id"], after)
            elif kind == "dossier":
                # This row reaches us from a client-supplied PUT body, and
                # dossiers.write() creates its parent dir -- so the target has to
                # name a character that actually exists, or a forged row conjures
                # a dossier-only phantom under characters/.
                if not after.strip():
                    continue  # a blank reply must not erase a good dossier
                if target.get("kind") != "characters":
                    continue
                # Staging the dossier (#235) moved the write from absorb time to
                # save time, so the write order is now the SAVE order and can
                # invert the absorb order: two reviews open on the same NPC, the
                # newer saved first, and this one would overwrite it with
                # earlier-scene state. The staged `before` dates the proposal --
                # if it no longer matches, a newer dossier already landed and
                # this one is stale. (Replaying one save twice lands here too.)
                #
                # The existence check, the read and the write share ONE handler:
                # anything that escaped it would fall through to the generic
                # per-edit skip below, and by then the chronicle is recorded and
                # the reviewer's panel closes on a 200 -- so a swallowed failure
                # loses an approved dossier without ever saying so.
                try:
                    try:
                        characters.read_character(
                            overlay.char_root(cid, target["id"]), target["id"])
                    except characters.CharacterNotFound:
                        raise materializer._DossierTargetGone(
                            "that character no longer exists in this campaign") from None
                    if dossiers.read(croot, target["id"]) != e.get("before", ""):
                        failures.append({
                            "id": e.get("id", ""), "kind": "conflict",
                            "reason": "this dossier changed since the scene was absorbed"})
                        continue
                    dossiers.write(croot, target["id"], after)
                except materializer._DossierTargetGone as exc:
                    failures.append({"id": e.get("id", ""), "kind": "error",
                                     "reason": str(exc)})
                    continue
                except Exception as exc:  # noqa: BLE001 -- full disk, permissions, ...
                    failures.append({"id": e.get("id", ""), "kind": "error",
                                     "reason": f"could not update the dossier: {exc}"})
                    continue
            elif kind == "lore":
                overlay.update_entity(cid, target["kind"], target["id"], body=after)
            elif kind == "authored":
                if e["field"] not in materializer._CARD_FIELDS:
                    continue  # re-guard: PUT edits are client-supplied, not re-materialized
                vid = appearances_versions.locked_version(cid, "characters", target["id"])
                aroot = appearances_paths.locked_actor_root(cid)   # locked -> materialized
                card = characters.read_card(aroot, target["id"], vid)
                card["data"][e["field"]] = after
                characters.update_version(aroot, target["id"], vid, card)
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
            elif kind == "new_character":
                p = e["payload"]
                card = characters.blank_card(p["name"])
                after = materializer._new_character_provenance(after, p)
                card["data"]["description"] = after
                card["data"]["personality"] = p.get("personality", "")
                card["data"]["mes_example"] = p.get("mes_example", "")
                card["data"]["extensions"]["sd_prompt"] = p.get("sd_prompt", "")
                # Generated example dialogue uses the {{char}} macro; scene assembly
                # expands a stored {{char}} to the whole present cast, so bake the
                # card's own name in now (same as card import).
                cards.bake_char_name(card)
                # personality/mes_example are card writes too — log them (post-bake) so
                # the Changes panel shows the whole new card, not just the description.
                extra_fields = [
                    {"field": f, "label": f"{e.get('label', '')} — {noun}",
                     "before": "", "after": card["data"][f]}
                    for f, noun in (("personality", "personality"),
                                    ("mes_example", "example dialogue"))
                    if card["data"][f]]
                new_cid, new_vid = overlay.create_character(cid, p["name"], "default", card)
                try:
                    dossiers.write(croot, new_cid, materializer._new_character_dossier(p["name"], p))
                except Exception:  # noqa: BLE001 — seed is best-effort: a failure here
                    pass  # must not strand the created character (retry would duplicate it)
                if sid:
                    appearances_transitions.appear(cid, sid, "characters", new_cid, new_vid, "npc", narrate=False)
                target = {"kind": "characters", "id": new_cid}
            elif kind == "new_location":
                p = e["payload"]
                new_eid = overlay.create_entity(cid, "locations", p["name"], after,
                                                p.get("keys", ""), sd_prompt=p.get("sd_prompt", ""))
                if sid and p.get("current_setting") and not scenes_read.get_location_history(cid, sid):
                    scenes_moment.set_location(cid, sid, new_eid)
                target = {"kind": "locations", "id": new_eid}
            elif kind == "new_lore":
                p = e["payload"]
                new_eid = overlay.create_entity(cid, "lore", p["name"], after, p.get("keys", ""))
                target = {"kind": "lore", "id": new_eid}
            else:
                continue
            applied.append(e["id"])
            if sid and kind in _BROWSABLE_KINDS:
                ref = f"{target['kind']}/{target['id']}"
                recorded.setdefault(ref, []).append(
                    {"field": e.get("field", ""), "label": e.get("label", ""),
                     "before": e.get("before", ""), "after": after})
                recorded[ref].extend(extra_fields)
        except Exception:  # noqa: BLE001 — best-effort per edit
            continue
    if sid:
        changes.record(cid, sid, recorded)
    return applied, failures
