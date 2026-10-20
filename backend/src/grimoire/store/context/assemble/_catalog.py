"""Stable section identities, layout selectors and content pinning."""

from __future__ import annotations

from typing import NamedTuple

from .. import layout, pack

#: Which sections a pinned cast member holds up, BY `Section.id`. Not by label:
#: from #29 the label is the reader's to edit and two sections may share one, so
#: a protection keyed on the label could hold up the wrong section or, after a
#: rename, none at all — silently, since a pin that protects nothing looks
#: exactly like a pin whose content did not activate.
#:
#: Everything else a pinned character feeds is already `lock-in` (their card,
#: their persona), so naming those here would say nothing; this one is the
#: droppable claim about that character, and a pin on someone is a request to
#: keep the model told who they currently are.
_CAST_SECTIONS = ("character_state", "tracker_state")

#: The voice sections, held up only by a pinned NPC. Per-character content like
#: the one above -- a reader who pinned a character and then watched the packer
#: drop that character's anchor would have been told their pin meant something
#: it did not -- but NPC-only, which `_CAST_SECTIONS` is not. `voice_policy` is
#: absent deliberately: it is LOCK_IN, so no pin can make it any safer.
_VOICE_CAST_SECTIONS = ("voice_anchors", "voice_examples")

#: What a pinned world-info entry holds up: the section its body renders into,
#: plus — for a group — the campaign state that activation pulls in beside it.
_WORLD_INFO_SECTION = "world_info"
_GROUP_STATE_SECTION = "group_state"
#: A pinned location that IS the current setting renders there, not in World
#: info (see `world_state._world_info`), so that is the section it protects.
_SETTING_SECTION = "current_setting"


def _section_on(section_id: str) -> bool:
    """Will `section_id` render at all, under the reader's prompt layout?

    Asked in `_campaign_view` only for data that is expensive to gather -- see
    the `available_art` key. `layout.apply` is what `_render_sections` will consult
    a moment later, so this cannot disagree with it; it costs one `read_config`
    and at most one small file read, which is what that function already
    promises per assemble pass.

    Never raises: `read_layout` answers every malformed file with "no layout",
    and a preference must not be able to take a generation down.
    """
    return any(sec.id == section_id for sec in layout.apply(SECTIONS))


def _pinned_sections(pinned_refs: frozenset, cast: list[dict], activated_wi: list[dict],
                     current_loc: str | None, voiced_ids: frozenset = frozenset()) -> frozenset:
    """The section ids a pin is holding up, for this assembly (#129).

    A pin is a promise about CONTENT, and the packer drops SECTIONS — so the
    promise has to be translated into the sections the pinned content actually
    landed in, once, here, where both are in hand. Two consequences worth
    stating: a pin protects the whole section it lands in, neighbours included
    (sections are dropped whole, so there is no finer unit to protect), and a
    pin on something that selected nothing this turn protects nothing, which is
    right — there is no content of the reader's in the prompt to defend.

    World info is the exception to the first: it sheds an entry at a time
    (spec §6), so the packer protects the pinned entry itself and still sheds
    its neighbours. The flag still matters there -- it keeps the section from
    ever being dropped whole, and the inspector shows it.
    """
    if not pinned_refs:
        return frozenset()
    out = set()
    pinned_cast = [a for a in cast if f"{a['kind']}:{a['id']}" in pinned_refs]
    if pinned_cast:
        out.update(_CAST_SECTIONS)
    # The voice sections carry NPC data ONLY, so a pinned PLAYER must not hold
    # them up. Sections are dropped whole, so pinning your own character would
    # otherwise make every NPC's anchor and examples undroppable -- and on a
    # large cast that is enough to push a budgeted prompt over on its own, for
    # a pin the reader made about themselves.
    # ...and only when that NPC actually PUT something in them. This function's
    # own rule is that a pin on something which selected nothing protects
    # nothing, and sections drop whole -- so pinning an anchorless NPC would
    # otherwise make every OTHER character's anchors and examples undroppable,
    # on the strength of content the pinned actor did not contribute.
    if any(a["role"] == "npc" and a["id"] in voiced_ids for a in pinned_cast):
        out.update(_VOICE_CAST_SECTIONS)
    for e in activated_wi:
        if f"{e['kind']}:{e['id']}" not in pinned_refs:
            continue
        out.add(_WORLD_INFO_SECTION)
        if e["kind"] == "groups":
            out.add(_GROUP_STATE_SECTION)
    if current_loc and f"locations:{current_loc}" in pinned_refs:
        out.add(_SETTING_SECTION)
    return frozenset(out)


#: The off-scene cast directory's shared heading: the line that says these
#: characters are NOT present. Both tiers name it and `_render_sections` emits
#: it once per contiguous run of them — one copy in the catalog's order, where
#: they are adjacent, and one per half a layout has separated. NOT "exactly one
#: of them emits it": collapsing it back to one per message is how the later
#: half ends up under an unrelated section's `# Heading` (#423).
#:
#: No leading underscore, and that is the convention rather than an oversight:
#: `templates/README.md` reserves `_` for macro libraries, and this file is
#: rendered as prompt text like `history_line.j2` or `post_history.j2`.
_OFF_SCENE_CAST_HEADING = "scene/off_scene_cast_heading.j2"


class Section(NamedTuple):
    """One system-message section: its stable id, its inspector label, its
    template, the tier the packer drops it at, and the three selectors that
    decide whether it renders at all."""
    #: Stable identity, and the reason it is not the label: from #29 the label
    #: is the user's to edit, so two rows may legitimately carry the same
    #: string. Everything that has to name a section across a store write --
    #: `layout.py`'s entries, `/api/prompt-layout`, the inspector's React keys
    #: -- names this instead. It never reaches the model.
    id: str
    label: str
    template: str
    tier: str
    pcless_only: bool = False
    opener_only: bool = False
    #: Rendered on every turn EXCEPT the opener. Only the scene state wants
    #: this: the opener is streamed unpersisted into a box the user reads and
    #: adopts by hand, for a scene with no tracked posts yet -- so whatever the
    #: tracker last recorded belongs to some earlier moment, and an opener
    #: draft is one of the side calls the tracker's state is not shown to.
    except_opener: bool = False
    #: A heading this section SHARES with every other section naming the same
    #: template. `_render_sections` opens each contiguous RUN of them with it —
    #: one copy while they are adjacent, another for a half the layout moved
    #: away, because what it frames is a block and a split layout has two, and
    #: `_dedupe_runs` takes the second back off when packing closes the gap. Not
    #: a section title: every other section opens with its own `# Heading`
    #: inside its own template, and only a block that was split into several
    #: sections for the token breakdown's sake needs this.
    #:
    #: It is a property of the SECTION rather than something a template works
    #: out, because "am I first?" is a question about the merged, enabled
    #: layout and a template can only see the data (#423). The off-scene cast
    #: directory's tier 3 used to answer it by testing whether tier 2 had
    #: content, which stopped being the same question the moment `layout.py`
    #: let a reader disable or reorder either half — and the heading it lost is
    #: the line saying the cast is not present.
    heading: str = ""


#: The section CATALOG: every section this build knows, with the order and the
#: labels it ships. `_render_sections` walks `layout.apply(SECTIONS)` -- the
#: catalog when the user has no layout or has switched theirs off, their merge
#: of it otherwise -- and system.j2 joins the result. This list, not the
#: template, is where the default order lives.
SECTIONS = [
    Section("opener_instruction", "Opener instruction", "scene/opener_instruction",
            pack.LOCK_IN, opener_only=True),
    Section("global_system_prompt", "Global system prompt",
            "scene/sections/global_system_prompt.j2", pack.LOCK_IN),
    # Compact authorial settings stay whole under pressure. Their protection
    # is a packing contract, not authority over player agency or actor POV.
    Section("world_default_prompt", "World default prompt",
            "scene/sections/world_default_prompt.j2", pack.LOCK_IN),
    Section("campaign_default_prompt", "Campaign default prompt",
            "scene/sections/campaign_default_prompt.j2", pack.LOCK_IN),
    Section("scene_focus", "Scene focus", "scene/sections/scene_focus.j2", pack.LOCK_IN),
    Section("prose_style", "Prose style", "scene/sections/prose_style.j2", pack.LOCK_IN),
    Section("natural_prose", "Natural prose", "scene/sections/natural_prose.j2", pack.LOCK_IN),
    Section("model_guidance", "Model guidance", "scene/sections/model_guidance.j2", pack.LOCK_IN),
    Section("card_system_prompts", "System prompt",
            "scene/sections/card_system_prompts.j2", pack.LOCK_IN),
    # What the world is (#38): genre, tone, themes and the author's description,
    # off `world.md`. BACKGROUND -- it frames, and the cards and world info
    # already carry most of it piecemeal, so it gives way before anything about
    # the scene in hand. Renders nothing for a world with no profile, so a
    # campaign whose world never had one sends the prompt it always sent.
    Section("world_overview", "World overview",
            "scene/sections/world_overview.j2", pack.BACKGROUND),
    Section("character_descriptions", "Character descriptions",
            "scene/sections/character_descriptions.j2", pack.LOCK_IN),
    # Three sections rather than one, because they are three kinds of thing and
    # pack.py's tiers already name the difference. The policy is fixed-length
    # instruction text -- LOCK_IN's own description, "the instructions that
    # define the reply itself" -- and is bounded by construction. The other two
    # are per-character information, which is SPOTLIGHT's description and is
    # cast-sized, so exactly what must not be pinned.
    #
    # Anchors are NOT guaranteed to outlive examples. `pack` drops the largest
    # ACTUAL section within a tier, not the one with the larger per-item cap,
    # and a pinned section never drops at all. Examples are usually the larger
    # and so usually go first; that is a tendency, and nothing may depend on it.
    Section("voice_policy", "Voice · the rule",
            "scene/sections/voice_policy.j2", pack.LOCK_IN),
    Section("voice_anchors", "Voice · how they sound",
            "scene/sections/voice_anchors.j2", pack.SPOTLIGHT),
    Section("voice_examples", "Voice · example dialogue",
            "scene/sections/voice_examples.j2", pack.SPOTLIGHT),
    Section("character_state", "Character state",
            "scene/sections/character_state.j2", pack.SPOTLIGHT),
    # What each present character looks like and is doing right now, as the
    # scene tracker recorded it, filtered for the reader (`_tracker_lines`).
    # Same tier and pin rule as the standing state above, which is the same
    # kind of claim on a shorter clock. Not in the opener: a new scene has no
    # tracked posts, and the opener is a draft the reader adopts by hand.
    Section("tracker_state", "Scene state", "scene/sections/tracker_state.j2",
            pack.SPOTLIGHT, except_opener=True),
    # Beside the state sections and at their tier, because it is the same kind
    # of claim: who is live right now. AFTER them, so the model reads what each
    # character is feeling before it reads which of them should carry the turn.
    Section("active_speaker", "Active speaker",
            "scene/sections/active_speaker.j2", pack.SPOTLIGHT),
    Section("relationships", "Relationships", "scene/sections/relationships.j2", pack.SPOTLIGHT),
    Section("player_personas", "Player personas",
            "scene/sections/player_personas.j2", pack.LOCK_IN),
    Section("offscreen_scene", "Offscreen scene", "scene/sections/offscreen_scene.j2",
            pack.LOCK_IN, pcless_only=True),
    Section("absent_players", "Absent player characters", "scene/sections/absent_players.j2",
            pack.LOCK_IN, pcless_only=True),
    Section("story_so_far", "Story so far", "scene/sections/story_so_far", pack.BACKGROUND),
    Section("archive", "Earlier scenes", "scene/sections/archive.j2", pack.ARCHIVE),
    Section("history_recall", "Recalled history", "scene/sections/history_recall.j2",
            pack.HISTORY_RECALL),
    # 11's three, coupled to "Recalled history" in the layout (`layout.COUPLED`):
    # the narrator's withheld items, and an NPC's own known and suspected ones.
    Section("history_narrator_only", "Recalled history · kept from the cast",
            "scene/sections/history_narrator_only.j2", pack.HISTORY_ASIDE),
    Section("history_known", "Recalled history · what the speaker knows",
            "scene/sections/history_actor_known.j2", pack.HISTORY_RECALL),
    Section("history_suspected", "Recalled history · what the speaker suspects",
            "scene/sections/history_actor_suspected.j2", pack.HISTORY_ASIDE),
    Section("plot_threads", "Plot threads", "scene/sections/plot_threads.j2", pack.SPOTLIGHT),
    Section("commitments", "Commitments", "scene/sections/commitments.j2", pack.SPOTLIGHT),
    Section("today", "Today", "scene/sections/today.j2", pack.SPOTLIGHT),
    Section("birthdates", "Birthdates", "scene/sections/birthdates.j2", pack.RECALLED),
    Section("weather", "Weather", "scene/sections/weather.j2", pack.SPOTLIGHT),
    Section("current_setting", "Current setting",
            "scene/sections/current_setting.j2", pack.SPOTLIGHT),
    Section("world_info", "World info", "scene/sections/world_info.j2", pack.SPOTLIGHT),
    # ARCHIVE, not SPOTLIGHT, and not folded into World info above: recalled
    # lore is retrieved *because* the conversation touched on it, exactly like
    # "Earlier scenes", and it is the first thing that should go when the
    # prompt does not fit. Sharing a section with the keyword hits would let a
    # recall drop them too.
    Section("recalled_lore", "Recalled lore", "scene/sections/recalled_lore.j2", pack.RECALLED),
    # RECALLED, beside recalled lore and for the same reason: this is content
    # retrieved *because* the conversation touched on it, so it is the first
    # thing that should give way when the prompt does not fit. It is also the
    # section a reader most plausibly wants gone entirely -- and `layout.py`
    # already makes any section switchable by id, which is this feature's whole
    # off switch rather than a second setting of its own.
    Section("available_art", "Available art", "scene/sections/available_art.j2", pack.RECALLED),
    Section("group_state", "Group state", "scene/sections/group_state.j2", pack.SPOTLIGHT),
    Section("mechanics_rules", "Mechanics rules",
            "scene/sections/mechanics_rules.j2", pack.SPOTLIGHT),
    Section("mechanics_sheets", "Mechanics sheets",
            "scene/sections/mechanics_sheets.j2", pack.SPOTLIGHT),
    # The off-scene cast directory is TWO sections, not one, so the token
    # breakdown can price its tiers apart (#2): tier 3 is the unbounded one and
    # folding it in with tier 2 hid exactly the number that decides whether it
    # needs bounding. Adjacent and in this order by DEFAULT, so system.j2 joins
    # them back into the single block they were split out of — but only by
    # default: `layout.py` lets a reader reorder or disable either half, which
    # is why the shared heading is `Section.heading` (emitted by
    # `_render_sections` on whichever half actually rendered first) rather than
    # something the templates work out between themselves (#423).
    #
    # Same tier, so under budget pressure the packer may drop one and keep the
    # other -- a directory that used to be taken or left whole is now divisible.
    # Both halves of that are pinned by tests; the second one is the one to know
    # about:
    #
    # - dropping tier 3 is the harmless case. The heading rides on tier 2, so
    #   what survives is a framed directory that simply names fewer people.
    # - dropping tier 2 leaves tier 3's list under a bare "## Known to exist"
    #   with the directory's "introduce them only if the story calls for it"
    #   gone. And this is the LIKELIER case in exactly the campaigns that feel
    #   budget pressure: `offscene_known_limit` bounds tier 3 to one line each
    #   while tier 2 is unbounded dossier paragraphs, one per roster NPC, so a
    #   mature campaign's tier 2 is the larger half and largest goes first.
    #
    # Which half is exposed follows the LAYOUT, because the heading rides on
    # whichever renders first: reorder the two and it is tier 3 that can be
    # dropped carrying the heading. Same defect, same cost, and pinned by
    # `test_a_reordered_layout_can_still_drop_the_half_holding_the_heading`.
    # #423 moved the heading's owner from data to the render path; it did not
    # move it past the packer, which runs after.
    #
    # Accepted rather than solved, and the options were weighed: dropping the
    # two together needs a section-grouping notion `pack` does not have, and
    # giving tier 3 its own lower tier means adding one to `DROP_ORDER` --
    # defensible (a directory of absent characters is worth less than the
    # recap) but a change to the packing model this issue did not ask for.
    # Reassigning the heading to a survivor is the third option and has the
    # same shape: done after `pack.pack` it adds tokens to a total already
    # measured against the ceiling, so to be honest it has to happen inside the
    # drop loop -- which is the grouping notion again.
    # Revisit if the unframed list turns out to cost anything in practice.
    Section("off_scene_cast_active", "Off-scene cast · active elsewhere",
            "scene/sections/off_scene_cast_active.j2", pack.BACKGROUND,
            heading=_OFF_SCENE_CAST_HEADING),
    Section("off_scene_cast_known", "Off-scene cast · known to exist",
            "scene/sections/off_scene_cast_known.j2", pack.BACKGROUND,
            heading=_OFF_SCENE_CAST_HEADING),
    Section("mechanics_response_format", "Mechanics response format",
            "scene/sections/mechanics_response_format.j2", pack.LOCK_IN),
    Section("response_format", "Response format",
            "scene/sections/response_format.j2", pack.LOCK_IN),
    Section("response_budget", "Response budget",
            "scene/sections/response_budget.j2", pack.LOCK_IN),
]

#: The two sections that pick a variant file from the assembled data. Everything
#: else in `SECTIONS` names its template outright.
_VARIANTS = {
    "scene/opener_instruction": lambda d: ("adapt_" if d.get("opener_adapt") else "")
                                          + ("offscreen" if d["pcless"] else "standard"),
    "scene/sections/story_so_far": lambda d: "full" if d["story_full"] else "compact",
}


def _section_template(section: Section, data: dict) -> str:
    pick = _VARIANTS.get(section.template)
    return f"{section.template}/{pick(data)}.j2" if pick else section.template


#: The section semantic recall renders into. Its rows carry entries and reasons
#: like World info's, but it never sheds: it drops whole, in its own tier.
_RECALLED_SECTION = "recalled_lore"


def _applies(section: Section, *, pcless: bool, opener: bool) -> bool:
    """Whether `section` belongs in this kind of prompt at all, by its three
    selectors: a pcless-only section in a scene with no player character, an
    opener-only one in an opener, and one the opener skips anywhere else."""
    return not ((section.pcless_only and not pcless)
                or (section.opener_only and not opener)
                or (section.except_opener and opener))
