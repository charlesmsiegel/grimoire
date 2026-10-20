"""Compose opener, normal and director messages with their paired inspection."""

from __future__ import annotations

from .... import model_guidance
from ... import turn_plan
from .. import epistemic, macros
from .. import history as history_section
from . import _packing, _snapshot

OPENER_RECAP_DEPTH = 5  # opener recap: full summaries of the last N scenes


def compose_opener(cid: str, sid: str, prompt: str, *,
                   describe: bool = True, model: str = "",
                   actor_ref: str = "grimoire", prior: list[dict] | None = None,
                   adapt: bool = False) -> tuple[list[dict], dict | None]:
    """A full-turn-context opener: the instruction plus every assembled system section
    (cast, plot threads, date, current setting, world-info, a full 5-scene recap, …),
    then the prompt as the user turn. The prompt seeds world-info activation, since a new
    scene has no history. No conversation history is included — the opener is for a scene
    with no messages. Ephemeral: the caller does not persist the result.

    `adapt` makes the prompt a greeting's body to rewrite for the campaign's
    current state rather than a premise to write from (#91): only the opener
    instruction's variant and the breakdown's label for the prompt change, so
    the adaptation sees exactly the context a from-scratch opener would.

    Returns the messages and the breakdown describing them — see `compose_turn`
    for why those two must come out of one pass."""
    a = _snapshot._assemble(cid, sid, wi_seed=prompt, full_recap=OPENER_RECAP_DEPTH,
                  actor_ref=actor_ref, opening_narrator=actor_ref == "grimoire",
                  opener=True)
    a["data"]["opener_adapt"] = adapt
    # Both trailing messages are rendered before packing so their tokens can be
    # reserved: neither is droppable, so neither may go uncounted.
    user_text = macros.expand_macros(prompt, macros.scene_substitutions(cid, sid), cid, sid,
                                     datetime_subs=a["datetime_subs"])
    prior_text = "\n\n".join(f"{part['speaker']}: {part['content']}" for part in (prior or []))
    # The opener is written one speaker per call, and the adapt instruction is
    # the same system text for all of them -- so what each call keeps of the
    # greeting has to be said here: the narrator its setting and situation,
    # each NPC its own part of it.
    if actor_ref == "grimoire":
        instruction = ("Set the scene as narrator, from the greeting's setting and situation. "
                       "Do not write any NPC or PC actions or dialogue." if adapt else
                       "Set the scene as narrator. Do not write any NPC or PC actions or dialogue.")
    else:
        instruction = ("Write only this assigned NPC's own part of the greeting, adapted, after the "
                       "preceding opening. Do not write for other actors." if adapt else
                       "Write only this assigned NPC's contribution after the preceding opening. "
                       "Do not write for other actors.")
    # Author's notes: an opener has no history to place them in, so the ones
    # that apply (every-turn notes only -- an opener is turn 0) follow the
    # prompt and any prior contributions, ahead of the instruction, and are
    # reserved like the rest of what is appended.
    note_rows = tuple((f"Author's note — {level}", text) for level, text in a["opener_notes"])
    extra = (("Greeting to adapt" if adapt else "Opener prompt", user_text),
             ("Previous opening contributions", prior_text),
             *note_rows,
             ("Assigned opener instruction", instruction))
    before = [{"role": "user", "content": user_text}]
    if prior_text:
        before.append({"role": "assistant", "content": prior_text})
    before += [{"role": "system", "content": text} for _level, text in a["opener_notes"]]
    return _packing._prepare(a, cid, sid, model=model, describe=describe, opener=True,
                    before_post=tuple(before),
                    after_post=({"role": "system", "content": instruction},), extra=extra)



def build_opener_messages(cid: str, sid: str, prompt: str, model: str = "") -> list[dict]:
    """`compose_opener` without the breakdown — see there."""
    return compose_opener(cid, sid, prompt, describe=False, model=model)[0]


#: One message a caller wants appended after the system message, as
#: ``(inspector label, role, content)``. See `compose_turn`.
Appended = tuple[str, str, str]


def compose_turn(cid: str, sid: str, turn: dict | None = None,
                 appended: tuple[Appended, ...] = (),
                 describe: bool = True, model: str = "", actor_ref: str | None = None,
                 eligible_speakers: list[dict] | None = None,
                 successor_by_decision: bool = False,
                 images: int = 0,
                 plan: dict | None = None,
                 history_view: epistemic.EpistemicView | None = None) -> tuple[model_guidance.PreparedMessages, dict | None]:
    """One turn's messages, and the breakdown describing them.

    `history_view` is frozen selected evidence, gathered and classified for
    this step's perspective before compose (11-S3). This pass fits and sheds
    what the view allows only; it never retrieves, classifies or fills a slot,
    and it refuses a view built for another scene or another speaker.

    `images` is how many of the newest post images to keep as references for a
    route that reads them (#377, `store.post_images.images_for`); 0 composes
    today's text-only prompt, byte for byte.

    `describe=False` returns `None` for the breakdown and skips building it.
    That is not a micro-optimisation: on the DEFAULT unbounded budget `pack`
    skips tokenising entirely and deliberately says so, while `_breakdown`
    counts every section, every history message and the composed system prompt.
    Building one for a caller that will throw it away -- `build_messages`, or
    any route while `prompt_log_depth` is 0 -- would put a full tokenizer pass
    on every turn of a feature the user has turned off.

    BOTH out of a single `_assemble` + `_prepare` pass, which is what lets a
    snapshot of this turn be trusted later (#157). Running the two entry points
    separately would reintroduce exactly the disagreement `SECTIONS` was
    restructured to remove — and worse across time than within a request, since
    `macros.expand_macros` resolves `{{random}}` and `{{roll}}` at render time,
    so a second pass over an *identical* store still produces different text.

    `turn` is a one-shot, unpersisted response-preset override (a pending
    per-turn length chip) that beats the scene/campaign/global cascade for this
    call only -- see response_presets.resolve. Callers that need it to survive a
    failed generation (retry, regenerate) must re-pass it themselves; nothing
    here remembers it.

    `appended` is every message the caller wants AFTER the system one — the
    regenerate-guidance block, a roll-result block, the declined-roll block.
    Naming it here rather than appending it afterwards is deliberate: it has
    three consequences that must agree (the packer must reserve its tokens or
    the request silently overspends the budget, the message must actually be
    sent, and the record must report it), and three call sites used to spell
    the first two out separately with nothing holding them together.

    `plan` is a play decision's record for this contribution (the response's
    `intent`, roadmap 02 §6.5). What it chose is sent as one system message
    after the history (`_plan_block`), reserved and reported like an appended
    block. None -- or a record with nothing to say -- adds nothing, so the
    prompt is byte-identical to one composed without it.
    """
    a = _snapshot._assemble(cid, sid, turn=turn, actor_ref=actor_ref, eligible_speakers=eligible_speakers,
                  successor_by_decision=successor_by_decision,
                  images=images, history_view=history_view)
    intent = _plan_block(a, plan)
    return _packing._prepare(a, cid, sid, model=model, describe=describe,
                    after_post=tuple({"role": role, "content": content}
                                     for _label, role, content in appended),
                    extra=(*((label, content) for label, _role, content in appended),
                           *_plan_rows(intent)),
                    after_history=_plan_messages(intent))


def _plan_block(a: dict, plan: dict | None) -> str:
    """The intent block's text for this composition, or "" (no plan, a plan
    with nothing read, or a call that is not one actor's). Built from the
    actor `_assemble` resolved, so the name is the one the prompt uses."""
    if not plan:
        return ""
    actor_entry = a["data"].get("response_actor")
    if not actor_entry or actor_entry.get("ref") == "grimoire":
        return ""
    return turn_plan.block(plan, actor_entry["name"])


def _plan_rows(text: str) -> tuple[tuple[str, str], ...]:
    """The intent block's inspector row (and its reservation): last among the
    appended rows, so the rows a prompt without one carries keep their ids."""
    return (("Turn intent", text),) if text else ()


def _plan_messages(text: str) -> tuple[dict, ...]:
    return ({"role": "system", "content": text},) if text else ()



def build_messages(cid: str, sid: str, turn: dict | None = None,
                   appended: tuple[Appended, ...] = (), model: str = "",
                   images: int = 0) -> list[dict]:
    """`compose_turn` without the breakdown — see there.

    The old `reserve=` parameter is gone. It charged the budget for a message
    the caller then appended itself, which left the two halves free to drift
    and gave `compose_turn` no way to report the appended block; `appended`
    does all three jobs at once.
    """
    return compose_turn(cid, sid, turn=turn, appended=appended, describe=False, model=model,
                        images=images)[0]


def compose_director_turn(cid: str, sid: str, note: str, turn: dict | None = None,
                          describe: bool = True, model: str = "", actor_ref: str | None = None,
                          eligible_speakers: list[dict] | None = None,
                          successor_by_decision: bool = False,
                          appended: tuple[Appended, ...] = (),
                          images: int = 0,
                          plan: dict | None = None,
                          history_view: epistemic.EpistemicView | None = None) -> tuple[model_guidance.PreparedMessages, dict | None]:
    """One director turn: full system + history, then the note as the final user
    message. The note rides only this call — never persisted. `turn` is the same
    one-shot response-preset override as `compose_turn`, and the messages and
    breakdown come out of one pass for the same reason.

    Not offscreen-only, and never was: an empty send takes this path in an
    ordinary scene too, and since #83 so does a note typed in the composer's
    Direct mode. Nothing here reads `pcless` — the "Offscreen scene" section
    comes from `_assemble`, off the scene's own flag — so an ordinary scene gets
    its ordinary prompt with the note appended, and that is the whole
    difference. What it does NOT do is tell the model the final user turn is
    direction rather than the player speaking; in a pcless scene the offscreen
    section says so, and in an ordinary one the note reads as the player's own
    contribution, minus the persisting. That is the feature as offered ("steers
    the reply · never posted"), not an oversight — framing it differently is a
    prompt change, and prompt changes are answered by evals, not here."""
    # The note is this turn's actual input, and it is never persisted -- so it
    # seeds retrieval the same way the opener's prompt does, or naming an old
    # scene in a director note could not recall it (nothing else in the scan
    # window has said the word yet).
    a = _snapshot._assemble(cid, sid, wi_seed=note, turn=turn, actor_ref=actor_ref,
                  eligible_speakers=eligible_speakers, successor_by_decision=successor_by_decision,
                  images=images, history_view=history_view)
    # expanded up front so the note's tokens are reserved before packing: it is
    # a mandatory message, so the budget has to know about it
    note_text = macros.expand_macros(note, a["subs"], cid, sid, datetime_subs=a["datetime_subs"])
    # `plan` as `compose_turn`'s: after the history, so before the note.
    intent = _plan_block(a, plan)
    return _packing._prepare(a, cid, sid, model=model, describe=describe,
                    before_post=({"role": "user", "content": note_text},),
                    after_post=tuple({"role": role, "content": content}
                                     for _label, role, content in appended),
                    extra=(("Director note", note_text),
                           *((label, content) for label, _role, content in appended),
                           *_plan_rows(intent)),
                    after_history=_plan_messages(intent))



def build_director_messages(cid: str, sid: str, note: str, turn: dict | None = None,
                            model: str = "", images: int = 0) -> list[dict]:
    """`compose_director_turn` without the breakdown — see there."""
    return compose_director_turn(cid, sid, note, turn=turn, describe=False, model=model,
                                 images=images)[0]


def context_breakdown(cid: str, sid: str, model: str = "", images: int = 0,
                      resolved=None) -> dict:
    """The LIVE inspector view: compose the turn as it would be sent right now
    and describe it. Runs the same render and pack `compose_turn` runs.

    The live view has no appended blocks — those belong to a specific turn
    (regenerate guidance, a roll result), and this composes a hypothetical one.
    """
    view = history_section.preview_view(cid, sid, resolved)
    _messages, detail = compose_turn(cid, sid, model=model, images=images, history_view=view)
    assert detail is not None  # describe=True always builds the inspector
    return detail


def context_sections(cid: str, sid: str, model: str = "", images: int = 0) -> list[dict]:
    """Just the rows of `context_breakdown` — see there."""
    return context_breakdown(cid, sid, model=model, images=images)["sections"]
