"""A public historical stage, with a neutral cast fallback for legacy posts.

Recorded posts use only projection.at_post for story state. The tokenless legacy
prefix can display scene membership at locked versions, clearly marked as an
approximation, without inventing historical mood, clothing or location.
"""

from urllib.parse import quote

from .. import characters, entities, locks, overlay, pcs, responses
from ..appearances import paths as appearances
from ..expression_library import contracts, resolve
from ..expression_library import read as expressions
from ..record_art import read as art
from ..record_art import targets
from ..regex import view
from ..scene_history import projection, records
from ..scene_history.contracts import ActorAtPost
from ..scenes import identity, post_identity
from . import overrides


def scene(cid, sid):
    with locks.campaign_lock(cid):
        value = responses.migrate_if_needed(cid, sid)
        posts = view.view(value["messages"], cid=cid, phase="display")
        return {"scene_identity": identity.scene_identity(cid, sid), "title": value["meta"].get("title", "Scene"),
                "posts": [{"post_id": m["post_id"], "role": m["role"], "speaker": m.get("speaker"),
                           "content": m["content"], "excluded": bool(m.get("excluded"))} for m in posts]}


def _art(cid, kind, actor, version, notices):
    try:
        return art.read_campaign(cid, kind, actor, version)["images"]
    except (targets.ArtTargetNotFoundError, ValueError, OSError):
        notices.append("Some historical art is unavailable; using a neutral stage.")
        return []


def _name(cid, kind, actor, version):
    try:
        root = overlay.actor_root(cid, kind, actor)
        value = (characters.read_card(root, actor, version).get("data", {}) if kind == "characters"
                 else pcs.read_persona(root, actor, version))
        return value.get("name") or actor.replace("-", " ").title()
    except (characters.CharacterNotFound, characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound,
            OSError, ValueError, KeyError):
        return actor.replace("-", " ").title()


def _collection(cid, actor, notices):
    kind, aid = actor.ref.split(":", 1)
    target = contracts.ExpressionTarget("campaign", cid, kind, aid, actor.version_id)
    try:
        collection = expressions.effective(target)
    except (targets.ArtTargetNotFoundError, ValueError, OSError):
        notices.append("Some historical sprites are unavailable; using ordinary art or initials.")
        collection = {"outfits": {}, "default_outfit": None}
    return collection


def _actor(cid, actor, selected, speaker, notices):
    kind, aid = actor.ref.split(":", 1)
    collection = _collection(cid, actor, notices)
    outfits = collection["outfits"]
    clothing = (actor.clothing or "").casefold()
    automatic_outfit = next((oid for oid, row in outfits.items()
                             if any(alias and alias.casefold() in clothing for alias in row["clothing_aliases"])),
                            collection["default_outfit"])
    outfit_id = selected.get("outfit")
    if outfit_id and outfit_id not in outfits:
        notices.append("A selected outfit is unavailable; using automatic presentation.")
        outfit_id = None
    outfit_id = outfit_id or automatic_outfit
    expression = selected.get("expression")
    if expression and expression not in outfits.get(outfit_id, {}).get("expressions", {}):
        notices.append("A selected expression is unavailable; using automatic presentation.")
        expression = None
    prefix = (f"/api/campaigns/{quote(cid, safe='')}/{kind}/{quote(aid, safe='')}/versions/"
              f"{quote(actor.version_id, safe='')}/expressions")
    candidates = [{"url": f"{prefix}/{choice.outfit_id}/{choice.expression}/image?v={quote(choice.token or '', safe='')}",
                   "presentation": outfits[choice.outfit_id]["presentation"]}
                  for choice in resolve.candidates(collection, outfit_id, expression or actor.visible_mood,
                                                   explicit=expression is not None) if choice.image_id is not None]
    avatar = next((row for row in _art(cid, kind, aid, actor.version_id, notices) if row["name"] == "avatar"), None)
    if avatar:
        candidates.append({"url": avatar["url"], "presentation": contracts.PRESENTATION})
    return {"ref": actor.ref, "version_id": actor.version_id, "name": _name(cid, kind, aid, actor.version_id),
            "speaking": actor.ref == speaker, "automatic_outfit": automatic_outfit, "hidden": selected.get("hidden", False), "candidates": candidates,
            "outfits": [{"id": oid, "label": row["label"], "expressions": sorted(row["expressions"])}
                        for oid, row in outfits.items()]}


def post(cid, sid, post_id):
    with locks.campaign_lock(cid):
        current = post_identity.ensure_post_ids(cid, sid)
        at = next((i for i, m in enumerate(current["messages"]) if m["post_id"] == post_id), None)
        if at is None:
            raise FileNotFoundError("This post is no longer in the scene; refresh the reader")
        message = current["messages"][at]
        historic = projection.at_post(cid, sid, post_id)
        ident = historic.cursor.scene_identity
        saved = overrides.read(cid, ident, post_id)
        notices = []
        stage_actors = historic.actors
        # Imports predate per-post observations. Display their scene cast, but
        # never fill a missing modern checkpoint or a gap after recorded history.
        if not any("scene_state_token" in m for m in current["messages"][:at + 1]):
            stage_actors = tuple(ActorAtPost(ref.replace("/", ":", 1), row["version"])
                                 for ref, row in sorted(appearances.record(cid).items())
                                 if sid in row.get("scenes", []))
            notices.append("Per-post cast was not recorded; using scene cast and neutral expressions by default.")
        elif records.checkpoint(cid, ident, message.get("scene_state_token")) is None:
            notices.append("Historical stage information is unavailable for this post.")
        actors = [_actor(cid, actor, saved["actors"].get(actor.ref, {}), historic.speaker_ref, notices)
                  for actor in stage_actors]
        # Old imported speaker labels can highlight only an unambiguous stage actor.
        if not historic.speaker_ref and sum(a["name"] == message.get("speaker") for a in actors) == 1:
            for actor in actors:
                actor["speaking"] = actor["name"] == message.get("speaker")
        backgrounds, location_name = [], None
        if historic.location_id:
            try:
                location_name = overlay.read_entity(cid, "locations", historic.location_id)["meta"]["name"]
                backgrounds = [{"name": r["name"], "url": r["url"]}
                               for r in _art(cid, "locations", historic.location_id, None, notices)]
            except (entities.EntityNotFound, ValueError, OSError, LookupError):
                notices.append("The historical location is unavailable; using a neutral stage.")
        background = saved["background"]
        chosen = next((r for r in backgrounds if r["name"] == background), None)
        if background not in (None, "none") and chosen is None:
            notices.append("The selected background is unavailable; using the location default.")
        background_url = None if background == "none" else (chosen or next(iter(backgrounds), {})).get("url")
        return {"scene_identity": ident, "post_id": post_id, "revision": saved["revision"],
                "actors": actors, "backgrounds": backgrounds, "background_url": background_url,
                "location_name": location_name, "notices": list(dict.fromkeys(notices)),
                "override": {"actors": saved["actors"], "background": saved["background"]}}


def save(cid, sid, post_id, scene_identity, expected_revision, actors, background):
    with locks.campaign_lock(cid):
        if identity.scene_identity(cid, sid) != scene_identity:
            raise overrides.ConflictError("The scene was replaced; reload the reader")
        stage = post(cid, sid, post_id)
        if stage["revision"] != expected_revision:
            raise overrides.ConflictError("Presentation changed; reload before saving")
        allowed = {a["ref"]: a for a in stage["actors"]}
        if not isinstance(actors, dict) or len(actors) > len(allowed):
            raise overrides.InvalidError("Select only actors present in this historical post")
        for ref, selected in actors.items():
            _validate_actor(ref, selected, allowed)
        if background is not None and background != "none" and background not in {r["name"] for r in stage["backgrounds"]}:
            raise overrides.InvalidError("Choose an image from this historical location")
        overrides.write(cid, scene_identity, post_id, expected_revision, actors, background)
        return post(cid, sid, post_id)


def _validate_actor(ref, selected, allowed):
    if ref not in allowed or not isinstance(selected, dict) or set(selected) - {"outfit", "expression", "hidden"}:
        raise overrides.InvalidError("Unknown historical actor or presentation option")
    if "hidden" in selected and type(selected["hidden"]) is not bool:
        raise overrides.InvalidError("Visibility must be a boolean")
    outfits = {row["id"]: row for row in allowed[ref]["outfits"]}
    if "outfit" in selected:
        contracts.slug(selected["outfit"])
        if selected["outfit"] not in outfits:
            raise overrides.InvalidError("Selected outfit is unavailable")
    if "expression" in selected:
        contracts.slug(selected["expression"])
        oid = selected.get("outfit") or allowed[ref]["automatic_outfit"]
        choices = [outfits[oid]] if oid in outfits else []
        if not any(selected["expression"] in row["expressions"] for row in choices):
            raise overrides.InvalidError("Selected expression is unavailable in this outfit")
