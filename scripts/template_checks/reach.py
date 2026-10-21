"""The reach check: every template under templates/ is rendered by some check
here, or `UNRENDERED` says why not."""

from __future__ import annotations

from .harness import REPORT, loader

#: The templates no check above renders, each with why. A backlog that may only
#: shrink, the way `store/locks.py`'s `UNREVIEWED` is: the reach check below
#: fails on a template that is neither rendered by a check nor named here, so a
#: new template arrives with its check or its reason -- and on an entry a check
#: now renders, or that no longer exists, so a closed gap leaves the list.
#: "Renders" means this harness's own environment loaded it; see `_Recording`.
UNRENDERED = {
    "capsule/system.j2": "`taglines.build_capsule_prompt` (#62) has no check yet",
    "capsule/user.j2": "`taglines.build_capsule_prompt` (#62) has no check yet",
    "image_description/system.j2": (
        "`image_drafts.build_prompt` has no check yet; it inlines an image file read "
        "from disk"),
    "image_description/user.j2": (
        "`image_drafts.build_prompt` has no check yet; it inlines an image file read "
        "from disk"),
    "tracker/update_system.j2": "`tracker.prompt.build_messages` has no check yet",
    "tracker/update_user.j2": "`tracker.prompt.build_messages` has no check yet",
    "character_from_passage/system.j2": (
        "rendered inline in `routes/passage_characters.draft_character`; this harness "
        "imports no route module"),
    "character_from_passage/user.j2": (
        "rendered inline in `routes/passage_characters.draft_character`; this harness "
        "imports no route module"),
    "investigation/continuity/seed.j2": (
        "rendered by `routes/investigation.continuity_seed`; this harness imports no "
        "route module"),
    "investigation/continuity/system.j2": (
        "rendered by `routes/investigation.continuity_seed`; this harness imports no "
        "route module"),
    "investigation/question/seed.j2": (
        "rendered by `routes/investigation.question_seed`; this harness imports no route "
        "module"),
    "investigation/question/system.j2": (
        "rendered by `routes/investigation.question_seed`; this harness imports no route "
        "module"),
    "scene/extend_instruction.j2": (
        "rendered by `routes/character_turns._extend_messages` (Keep writing); this "
        "harness imports no route module"),
    "scene/response_steer.j2": (
        "rendered by `routes/character_turns.regenerate_response` and `_extend_messages`; "
        "this harness imports no route module"),
    "scene/regenerate_guidance.j2": (
        "rendered by `routes/scenes._regenerate_run`; the routes-source assertion above "
        "holds only that the call is there"),
    "scene/roll_result.j2": (
        "rendered by `routes/mechanics._continuation_messages`; the routes-source "
        "assertion above holds only that the call is there"),
    "scene/roll_declined.j2": (
        "rendered by `routes/mechanics._declined_continuation_messages` and "
        "`routes/character_turns.resume_roll`; this harness imports no route module"),
    "scene/length_correction.j2": (
        "`context.assemble` renders it only for an assigned speaker whose recent prose "
        "drifted off its length budget, and the mirror has no assigned speaker "
        "(`rendered_messages` passes `length_correction=\"\"`)"),
    "scene/response_actor.j2": (
        "`rendered_system` renders it only for a non-opener turn with an assigned "
        "speaker, and the only assigned speaker here is the openers' narrator"),
    "scene/response_selector_none.j2": (
        "`response_protocol.selector_item`'s `none_text`; the selector checks compare "
        "the item's context, question and options, not that"),
    "scene/sections/available_art.j2": (
        "missing from `rendered_system`'s section list; the chat checks pass only "
        "because the fixture gives the app nothing to put in it"),
    "scene/sections/birthdates.j2": (
        "missing from `rendered_system`'s section list; the chat checks pass only "
        "because the fixture gives the app nothing to put in it"),
    "snippets/bond_line.j2": (
        "`gather` takes its relationship lines from `relationships.render_present`, the "
        "app's own call, so this renders through the app's environment on both sides"),
    "snippets/feeling_line.j2": (
        "`gather` takes its relationship lines from `relationships.render_present`, the "
        "app's own call, so this renders through the app's environment on both sides"),
}


def check_reach() -> None:
    """Run last: it holds what every check before it rendered to the directory."""
    templates = {name for name in loader.list_templates() if name.endswith(".j2")}
    for name in sorted(templates - loader.reached - UNRENDERED.keys()):
        REPORT.note(f"reach ({name})", "no check renders it, and UNRENDERED does not say why")
    for name in sorted(UNRENDERED.keys() & loader.reached):
        REPORT.note(f"reach ({name})", "a check renders it now: take it out of UNRENDERED")
    for name in sorted(UNRENDERED.keys() - templates):
        REPORT.note(f"reach ({name})", "UNRENDERED names a template that does not exist")
    for name, why in UNRENDERED.items():
        if not why.strip():
            REPORT.note(f"reach ({name})", "UNRENDERED gives no reason for it")


CHECKS = (check_reach,)
