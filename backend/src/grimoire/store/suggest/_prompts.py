"""suggest: prompts responsibilities."""

from __future__ import annotations

from ... import prompts, schemas
from .. import default_prompts
from ..context import layout, macros
from . import _contracts, _snapshot, _view

DIRECTION_LIMIT = 500


def creation_defaults(cid: str) -> dict:
    """Capture shared defaults for one metadata call, with no scene binding.

    Reuse play's field resolver, layout policy, templates and macro engine,
    not its assembly (which would invent a scene/actor/date and read focus).
    The capture belongs to the generated batch, not to any later live preview.
    """
    fields = default_prompts.resolve(cid)
    requested = ("world_default_prompt", "campaign_default_prompt")
    enabled = layout.enabled_ids(requested)
    texts = {requested[0]: fields["world"]["text"], requested[1]: fields["text"]}
    sections = []
    for section_id, text in texts.items():
        if text and section_id in enabled:
            rendered = prompts.render(f"scene/sections/{section_id}.j2", **{section_id: text})
            expanded = macros.expand_macros(rendered, {}, cid, "", datetime_subs={}).strip()
            if expanded:
                sections.append(expanded)
    return {"sections": sections,
            "statuses": {requested[0]: fields["world"]["status"], requested[1]: fields["status"]}}


def build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None,
                 offscreen: bool = False, direction: str = "",
                 controls: _contracts.Controls | None = None, *, defaults: dict | None = None) -> list[dict]:
    # the templates pick the instruction variant and addenda from the same vars
    vars = {"s": snapshot, "offscreen": offscreen,
            "greeting_candidates": greeting_candidates,
            "direction": direction.strip()[:DIRECTION_LIMIT],
            "drivers": True, "view": _view.driver_view(snapshot, controls or _contracts.NO_CONTROLS),
            "default_sections": defaults["sections"] if defaults is not None else []}
    return [{"role": "system", "content": prompts.render("scene_suggestions/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_suggestions/user.j2", **vars)}]


INTENT_LIMIT = 2000


def _intent_shape(locations: list[str], tokens: list[str]) -> dict:
    """The intent reply's schema with these enums: `location` one of `""` and
    `locations`, `cast` an array of `tokens` -- or, for an empty list, a
    plain string (array of strings), since an empty `enum` is refused
    (`schemas.check`)."""
    location: dict = ({"type": "string", "enum": ["", *locations]} if locations
                      else {"type": "string"})
    member: dict = {"type": "string", "enum": tokens} if tokens else {"type": "string"}
    return {"type": "object", "additionalProperties": False,
            "required": ["title", "date", "location", "cast", "focus"],
            "properties": {"title": {"type": "string"}, "date": {"type": "string"},
                           "focus": {"type": "string"},
                           "location": location,
                           "cast": {"type": "array", "items": member}}}


def intent_schema(snapshot: dict) -> dict:
    """The JSON Schema of the scene-intent reply (spec 01f 3.8, the pilot),
    over the SAME snapshot the prompt renders, so the ids it offers are the
    ones the prompt lists: `title` and `date` strings, `location` an enum of
    `""` (the prompt's "implies none") and the available location ids, `cast`
    an array of the available cast tokens. What the mode buys is the enums: a
    provider that enforces them cannot invent an id. A field with nothing to
    offer is a plain string (array); a campaign whose enums would break a
    strict-mode budget drops the cast enum, then the location enum, until the
    schema is inside the portable subset. `parse_intent` drops an unknown id
    either way."""
    locations = list(dict.fromkeys(loc["id"] for loc in snapshot.get("available_locations") or []))
    tokens = list(dict.fromkeys(c["token"] for c in snapshot.get("cast") or []))
    for keep_locations, keep_cast in ((True, True), (True, False), (False, True)):
        shape = _intent_shape(locations if keep_locations else [],
                              tokens if keep_cast else [])
        try:
            schemas.check(shape)
        except schemas.SchemaError:
            continue
        return shape
    return _intent_shape([], [])


def build_intent_request(cid: str, typed: str,
                         offscreen: bool = False, *,
                         defaults: dict | None = None) -> tuple[list[dict], dict]:
    """The scene-intent prompt and the schema its reply is asked for in
    (`intent_schema`), from one snapshot: the system message renders the
    schema (`schema_json`), which is what `inference.generate(schema=)`
    checks for.

    Over the FULL snapshot, story-so-far included: "the morning after the
    funeral" is exactly the kind of phrase this has to resolve, and only the
    recent chronicle can resolve it. The legacy (`drivers=False`) snapshot and
    render: the intent prompt keeps its Upcoming line and does no pressure or
    driver work (spec §15) -- its only change since is the schema paragraph
    at the end of the system message (01f-S4)."""
    snapshot = _snapshot.build_snapshot(cid, offscreen=offscreen, drivers=False)
    captured_defaults = creation_defaults(cid) if defaults is None else defaults
    schema = intent_schema(snapshot)
    # `direction`, `drivers` and `view` are here because scene_intent/user.j2
    # INCLUDES scene_suggestions/user.j2, which reads them — and both this env
    # and verify_templates render with StrictUndefined, so omitting one is a
    # hard failure, not a silently-empty block.
    vars = {"s": snapshot, "schema": schema,
            "offscreen": offscreen, "greeting_candidates": None, "direction": "",
            "drivers": False, "view": None, "typed": typed.strip()[:INTENT_LIMIT],
            "default_sections": captured_defaults["sections"]}
    return ([{"role": "system", "content": prompts.render("scene_intent/system.j2", **vars)},
             {"role": "user", "content": prompts.render("scene_intent/user.j2", **vars)}],
            schema)


def build_intent_prompt(cid: str, typed: str, offscreen: bool = False) -> list[dict]:
    """Prompt for extracting metadata from the user's own scene description:
    `build_intent_request`'s messages."""
    return build_intent_request(cid, typed, offscreen)[0]
