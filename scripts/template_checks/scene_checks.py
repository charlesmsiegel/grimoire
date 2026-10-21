"""The checks over the fixture store: the context builders against the mirror,
the store-level prompts against their templates, and the effective ledger."""

from __future__ import annotations

from grimoire.store import (
    absorb,
    audit,
    campaigns,
    chronicle,
    commitments,
    config,
    context,
    default_prompts,
    playstate,
    plot,
    response_targets,
    scenes,
    steering,
    suggest,
)
from grimoire.store import appearances as ap
from grimoire.store import facts as fstore
from grimoire.store.context import epistemic
from grimoire.store.context import history as history_section
from grimoire.store.continuity import doc as continuity_doc
from grimoire.store.continuity import effective
from grimoire.store.history import model as history_model

from .fixture import Fixture
from .harness import check, check_messages, render
from .mirror import gather, rendered_messages

GRIMOIRE_ACTOR = {"ref": "grimoire", "name": "Grimoire"}
TYPED = "the morning after, back at the marsh house"


# --------------------------------------------------- context builder checks

def _check_frozen_history(fx: Fixture, data: dict) -> None:
    """Non-empty frozen history follows the same section catalog and template
    input contract. It is deliberately handed in: ordinary turns do not
    retrieve yet."""
    history_item = history_model.EvidenceItem(
        history_model.SceneRef("001--saltmarch", None), "Saltmarch", date="2026-10-13",
        summary="Mara kept the silver ledger.")
    config.write_config(history_recall_depth="1")
    check_messages("chat frozen history",
                   context.compose_turn(fx.cid, fx.sid, history_view=epistemic.narrator_view(
                       fx.cid, fx.sid,
                       history_model.Evidence((history_item,), ceiling=1200)))[0],
                   rendered_messages(fx, fx.sid, {**data, "history_evidence": [history_item]}))
    # 11-S3's sections, and the narrator's annotation lines: each renders the same
    # text through its template's data key as through `history.section`, the
    # fitted path a compose takes.
    annotated = {history_item.scene.key: {"known_to": ["Mara", "Seraphine"],
                                          "suspected_by": ["Winifred"]}}
    labelled = {history_item.scene.key: {"labels": [], "summary_label": "known"}}
    for section_id, notes in (("history_recall", annotated), ("history_narrator_only", {}),
                              ("history_known", labelled), ("history_suspected", {})):
        part = history_section.Part(section_id, (history_item,), notes,
                                    history_section.FRAMES[section_id],
                                    {"history_actor_name": "Mara"})
        fitted, _ = history_section.section(
            history_model.Evidence((history_item,), ceiling=1200),
            lambda text: text, len, {}, config.read_config(), part)
        check(f"history section {section_id}", fitted,
              render(history_section.FRAMES[section_id], history_actor_name="Mara",
                     history_notes=notes,
                     **{history_section.DATA_KEYS[section_id]: [history_item]}).strip())
    config.write_config(history_recall_depth="0")


def _check_model_profiles(fx: Fixture, data: dict) -> None:
    profile_data = {**data, "model_guidance": render("scene/model_guidance/glm-5.3.j2", **data)}
    for model_id in ("glm-5.3", "z-ai/glm-5.3"):
        check_messages("chat model profile " + model_id,
                       context.build_messages(fx.cid, fx.sid, model=model_id),
                       rendered_messages(fx, fx.sid, profile_data))
    check_messages("chat unknown model",
                   context.build_messages(fx.cid, fx.sid, model="vendor/unknown"),
                   rendered_messages(fx, fx.sid, data))


def _assert_live_sections(fx: Fixture) -> None:
    # The fixture's present NPC is both anchored and flagged, so the voice corrective
    # really is inside the post-history the comparison above covers. Asserted rather
    # than assumed: without an anchor the corrective renders "", and comparing "" to
    # "" passes while proving nothing about scene/voice_correction.j2.
    assert any("drifted out of voice" in m["content"]
               for m in context.build_messages(fx.cid, fx.sid)), \
        "the voice corrective is missing from the assembled prompt -- check the fixture (#59)"
    # Same reasoning for the Scene state section: a byte-for-byte check over an
    # empty section proves nothing, and its private-value branch is the one a leak
    # would hide in.
    assert any("# Scene state" in m["content"] and "(private: never state or imply in narration)"
               in m["content"] for m in context.build_messages(fx.cid, fx.sid)), \
        "the Scene state section is missing from the assembled prompt -- check the tracker fixture"


def check_chat(fx: Fixture) -> None:
    data = gather(fx, fx.sid, pcless=False)
    # A byte-for-byte check over an empty section proves nothing, and the archive
    # section is empty unless the fixture keeps a keyed record outside the recap
    # window — so say so here rather than let the check quietly go vacuous.
    assert data["archive_entries"], "fixture no longer exercises the archive section"
    check_messages("chat", context.build_messages(fx.cid, fx.sid),
                   rendered_messages(fx, fx.sid, data))
    _check_frozen_history(fx, data)
    _check_model_profiles(fx, data)
    _assert_live_sections(fx)
    note = render("scene/director_note.j2")
    check_messages("director", context.build_director_messages(fx.cid, fx.sid, note),
                   rendered_messages(fx, fx.sid, data, note=note))


def _opener_data(fx: Fixture, scene_id: str, pcless: bool, seed: str) -> dict:
    """An opener's data: the scene seeded with its prompt, the full recap, the
    opening budget, and the narrator as its actor."""
    return {**gather(fx, scene_id, pcless=pcless, wi_seed=seed,
                     full_recap=context.OPENER_RECAP_DEPTH),
            "opener": True, "budget": response_targets.resolve()["opening"],
            "response_actor": GRIMOIRE_ACTOR}


def check_openers(fx: Fixture) -> None:
    opener_prompt = "A storm rolls in while they reach the warehouse ledger."
    odata = _opener_data(fx, fx.sid, False, opener_prompt)
    check_messages("opener", context.build_opener_messages(fx.cid, fx.sid, opener_prompt),
                   rendered_messages(fx, fx.sid, odata, opener_prompt=opener_prompt))
    # The adapted greeting (#91) is the same opener with the greeting's body as its
    # prompt and the instruction's `adapt_` variant: nothing else may differ.
    greeting_body = "The warehouse door stands open; the ledger waits on the desk."
    adata = {**_opener_data(fx, fx.sid, False, greeting_body), "opener_adapt": True}
    check_messages("adapted opener",
                   context.compose_opener(fx.cid, fx.sid, greeting_body, describe=False,
                                          adapt=True)[0],
                   rendered_messages(fx, fx.sid, adata, opener_prompt=greeting_body))


def check_offscreen(fx: Fixture) -> None:
    sid_off = scenes.create_scene(fx.cid, "Offscreen", pcless=True)
    ap.appear(fx.cid, sid_off, "characters", fx.sera, "default", "npc")
    ap.appear(fx.cid, sid_off, "characters", fx.kessler, "default", "npc")
    scenes.append_message(fx.cid, sid_off, "assistant", "Kessler locks the clinic door.",
                          speaker="Doc Kessler")
    off_data = gather(fx, sid_off, pcless=True)
    check_messages("offscreen chat", context.build_messages(fx.cid, sid_off),
                   rendered_messages(fx, sid_off, off_data))
    off_odata = _opener_data(fx, sid_off, True, "The pact at the pier.")
    check_messages("offscreen opener",
                   context.build_opener_messages(fx.cid, sid_off, "The pact at the pier."),
                   rendered_messages(fx, sid_off, off_odata, opener_prompt="The pact at the pier."))
    check_messages("offscreen adapted opener",
                   context.compose_opener(fx.cid, sid_off, "The pact at the pier.",
                                          describe=False, adapt=True)[0],
                   rendered_messages(fx, sid_off, {**off_odata, "opener_adapt": True},
                                     opener_prompt="The pact at the pier."))


# ------------------------------------------------ store-level simple prompts

def check_store_suggestions(fx: Fixture) -> None:
    cid = fx.cid
    snap = suggest.build_snapshot(cid)
    exp = suggest.build_prompt(snap, None)
    store_view = suggest.driver_view(snap, suggest.NO_CONTROLS)
    check("suggestions system (store)", exp[0]["content"],
          render("scene_suggestions/system.j2", s=snap, offscreen=False,
                 greeting_candidates=None, direction="", drivers=True, view=store_view,
                 default_sections=[]))
    check("suggestions user (store)", exp[1]["content"],
          render("scene_suggestions/user.j2", s=snap, offscreen=False,
                 greeting_candidates=None, direction="", drivers=True, view=store_view))

    # The intent prompt renders the legacy snapshot with `drivers` off (spec §15).
    iexp = suggest.build_intent_prompt(cid, TYPED)
    isnap = suggest.build_snapshot(cid, drivers=False)
    check("intent system (store)", iexp[0]["content"],
          render("scene_intent/system.j2", s=isnap, offscreen=False,
                 greeting_candidates=None, direction="", drivers=False, view=None, typed=TYPED,
                 schema=suggest.intent_schema(isnap), default_sections=[]))
    check("intent user (store)", iexp[1]["content"],
          render("scene_intent/user.j2", s=isnap, offscreen=False,
                 greeting_candidates=None, direction="", drivers=False, view=None, typed=TYPED))

    ioff_exp = suggest.build_intent_prompt(cid, TYPED, offscreen=True)
    ioff_snap = suggest.build_snapshot(cid, offscreen=True, drivers=False)
    check("intent user (store, offscreen)", ioff_exp[1]["content"],
          render("scene_intent/user.j2", s=ioff_snap, offscreen=True,
                 greeting_candidates=None, direction="", drivers=False, view=None, typed=TYPED))


def check_store_absorb(fx: Fixture) -> None:
    cid, sid = fx.cid, fx.sid
    scene_msgs = scenes.read_scene(cid, sid)["messages"]
    tr = render("snippets/transcript.j2", messages=scene_msgs)
    check("transcript (store)", chronicle.transcript_text(scene_msgs), tr)
    facts = chronicle.scene_facts(cid, sid)
    st_snap = absorb.state_snapshot(cid, sid)
    snaps = {"state_snapshot": st_snap,
             "rel_snapshot": absorb.relationships_snapshot(cid, sid),
             "plot_snapshot": absorb.plot_snapshot(cid),
             "group_snapshot": absorb.group_snapshot(cid),
             "commitment_snapshot": absorb.commitment_snapshot(cid),
             "fact_snapshot": absorb.fact_snapshot(cid)}
    steering.record(cid, sid, "Seraphine was told about the tail at the Night Dock")
    snaps["steering_snapshot"] = absorb.steering_snapshot(cid, sid)
    exp = absorb.build_prompt(tr, facts, **snaps)
    check("absorb user (store)", exp[1]["content"],
          render("absorb/user.j2", facts=facts, **snaps, tracked_snapshot=[], transcript=tr))
    for name, line in st_snap.items():
        st = playstate.read_state(fx.croot, fx.sera)
        check(f"state snapshot line (store, {name})", line,
              render("snippets/state_snapshot_line.j2", st=st))

    blocks, _excluded = audit.sheet_blocks(cid, sid)
    roll_log = audit.roll_lines(cid, sid)
    audit_exp = audit.build_prompt(tr, blocks, roll_log)
    check("audit system (store)", audit_exp[0]["content"], render("audit/system.j2"))
    check("audit user (store)", audit_exp[1]["content"],
          render("audit/user.j2", sheet_blocks=blocks, roll_lines=roll_log, transcript=tr))


def check_store_lines(fx: Fixture) -> None:
    cid = fx.cid
    threads = plot.open_threads(cid)
    check("plot lines (context form)", "\n".join(plot.render_open(cid, with_id=False)),
          "\n".join(render("snippets/plot_thread_line/context.j2", t=t) for t in threads))
    check("plot lines (absorb form)", "\n".join(plot.render_open(cid, with_id=True)),
          "\n".join(render("snippets/plot_thread_line/absorb.j2", t=t) for t in threads))

    owed = commitments.open_commitments(cid)
    check("commitment lines (context form)",
          "\n".join(commitments.render_open(cid, with_id=False)),
          "\n".join(render("snippets/commitment_line/context.j2", c=c) for c in owed))
    check("commitment lines (absorb form)", "\n".join(commitments.render_open(cid, with_id=True)),
          "\n".join(render("snippets/commitment_line/absorb.j2", c=c) for c in owed))

    # The identity law (capstone spec §7.1) on the full fixture: with no alias, the
    # effective renders the prompts now read ARE the physical ones.
    for w in (False, True):
        form = "absorb" if w else "context"
        check(f"plot lines (effective == physical, no aliases, {form})",
              "\n".join(effective.render_threads(cid, w)), "\n".join(plot.render_open(cid, w)))
        check(f"commitment lines (effective == physical, no aliases, {form})",
              "\n".join(effective.render_commitments(cid, w)),
              "\n".join(commitments.render_open(cid, w)))

    standing = fstore.active(cid)
    check("fact lines", "\n".join(fstore.render_active(cid)),
          "\n".join(render("snippets/fact_line.j2", f=f) for f in standing))


def check_merged(fx: Fixture) -> None:
    """A merged campaign, created after every other check so nothing else sees it.
    The expectations are written out by hand rather than rendered over
    `effective.threads` -- that is what the helper does, so the comparison could
    not fail. The canonical's beat is the earlier scene and the SOURCE's the later
    one, so the merged latest beat must come from the source; the canonical's
    status and title are its own, so a source's winning fails too."""
    cid_merged = campaigns.create_campaign("Saltmarch", fx.wid)
    plot.set_movement(cid_merged, "winifred-s-chart", "Winifred's chart", "advanced",
                      "Winifred inks the coastline", "001--gate")
    plot.set_movement(cid_merged, "mara-s-map", "Mara's map", "open",
                      "The map names the Saltmarch causeway", "002--causeway")
    commitments.set_movement(cid_merged, "winifred-s-promise", "Winifred's promise", "promise",
                             "open", None, "Winifred swore it at dawn", "001--gate")
    commitments.set_movement(cid_merged, "mara-s-oath", "Mara's oath", "promise",
                             "open", None, "Mara swore it at the gate", "002--causeway")
    for src, to in (("thread:mara-s-map", "thread:winifred-s-chart"),
                    ("commitment:mara-s-oath", "commitment:winifred-s-promise")):
        continuity_doc.put_alias(cid_merged, src,
                                 {"to": to, "created": "", "source": "manual", "note": ""})
    check("plot lines (effective, merged, absorb)",
          "winifred-s-chart: Winifred's chart (advanced) — The map names the Saltmarch causeway",
          "\n".join(effective.render_threads(cid_merged, True)))
    check("plot lines (effective, merged, context)",
          "Winifred's chart (advanced): The map names the Saltmarch causeway",
          "\n".join(effective.render_threads(cid_merged, False)))
    check("commitment lines (effective, merged, absorb)",
          "winifred-s-promise: Winifred's promise (promise, open) — Mara swore it at the gate",
          "\n".join(effective.render_commitments(cid_merged, True)))
    check("commitment lines (effective, merged, context)",
          "Winifred's promise (promise, open): Mara swore it at the gate",
          "\n".join(effective.render_commitments(cid_merged, False)))
    check("merged source hidden (threads)", "False",
          str(any("mara-s-map" in line for line in effective.render_threads(cid_merged, True))))
    check("merged source hidden (commitments)", "False",
          str(any("mara-s-oath" in line
                  for line in effective.render_commitments(cid_merged, True))))
    check("merged ids are canonical", "winifred-s-chart",
          ",".join(t["id"] for t in effective.threads(cid_merged)))


# ---------------------------------------------------------------------------

def check_standing_fields(fx: Fixture) -> None:
    """The earlier comparisons exercised the empty baseline. Nonempty settings
    now prove all three variable bindings and their catalog position, rather
    than allowing an absent section to make the wiring check vacuous."""
    default_prompts.set_world(fx.wid, "The river freezes each winter.")
    default_prompts.set_campaign(fx.cid, "This winter the harbor stays open.")
    scenes.focus.set(fx.cid, fx.sid, "Offer Mara a choice without promising agreement.")
    standing_data = gather(fx, fx.sid, pcless=False)
    check_messages("chat with standing fields", context.build_messages(fx.cid, fx.sid),
                   rendered_messages(fx, fx.sid, standing_data))
    for field_name in ("world_default_prompt", "campaign_default_prompt", "scene_focus"):
        assert standing_data[field_name], f"fixture no longer exercises {field_name}"
        check("empty " + field_name, "", render("scene/sections/" + field_name + ".j2",
                                                 **{field_name: ""}).strip())


def _messages(template_dir: str, variables: dict) -> list[dict]:
    """A one-call prompt's system and user messages, rendered from its templates."""
    return [{"role": "system", "content": render(f"{template_dir}/system.j2", **variables)},
            {"role": "user", "content": render(f"{template_dir}/user.j2", **variables)}]


def check_creation(fx: Fixture) -> None:
    """Creation reuses the same defaults, but no scene focus, actor or date binding."""
    cid = fx.cid
    creation_capture = suggest.creation_defaults(cid)
    for creation_offscreen in (False, True):
        creation_snap = suggest.build_snapshot(cid, offscreen=creation_offscreen)
        creation_vars = {"s": creation_snap, "offscreen": creation_offscreen,
                         "greeting_candidates": None, "direction": "", "drivers": True,
                         "view": suggest.driver_view(creation_snap, suggest.NO_CONTROLS),
                         "default_sections": creation_capture["sections"]}
        check_messages(f"suggestions with standing fields ({creation_offscreen})",
                       suggest.build_prompt(creation_snap, offscreen=creation_offscreen,
                                            defaults=creation_capture),
                       _messages("scene_suggestions", creation_vars))
        creation_vars.update(s=suggest.build_snapshot(cid, offscreen=creation_offscreen,
                                                      drivers=False),
                             drivers=False, view=None, typed=TYPED)
        creation_vars["schema"] = suggest.intent_schema(creation_vars["s"])
        check_messages(f"intent with standing fields ({creation_offscreen})",
                       suggest.build_intent_request(cid, TYPED, creation_offscreen,
                                                    defaults=creation_capture)[0],
                       _messages("scene_intent", creation_vars))


CHECKS = (check_chat, check_openers, check_offscreen, check_store_suggestions,
          check_store_absorb, check_store_lines, check_merged, check_standing_fields,
          check_creation)
