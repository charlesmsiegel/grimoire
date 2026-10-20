"""Pack independent frozen model profiles from the same rendered inputs."""

from __future__ import annotations

from copy import deepcopy

from .... import content_parts, model_guidance, prompts
from ... import tokens
from .. import macros, pack
from . import _inspection, _render


def _packed(a: dict, cid: str, sid: str, opener: bool = False,
            reserve: tuple[str, ...] = ()) -> dict:
    """Render the sections and fit them, with the history, under the configured
    budget. The opener carries no history, so it packs against an empty one.

    `reserve` is every message the caller appends AFTER the system message --
    a director note, regenerate guidance, a roll-result block, the opener's
    prompt and shape rules. The packer cannot drop any of them, so leaving them
    uncounted would pack to a ceiling the real request then sails straight past,
    which is the provider-side truncation this exists to prevent. post_history
    is always reserved; callers name the rest.
    """
    budget = pack.budget_tokens(cid)
    # Only worth the tokeniser calls when there is a budget to charge against.
    reserved = (tokens.count_tokens(a["post_history"])
                + sum(tokens.count_tokens(t) for t in reserve)) if budget > 0 else 0
    # `compose` is the real renderer, so the string the packer measures is the
    # string `_system_text` then produces -- not an estimate of it.
    #
    # `budget` travels with the result. `_breakdown` used to re-read it from
    # config.md, so saving a new ceiling mid-compose made the breakdown report a
    # budget the packing pass never applied -- harmless drift in the live panel,
    # but a frozen snapshot would keep claiming it forever.
    packed = pack.pack(_render._render_sections(a, cid, sid, opener=opener),
                       [] if opener else a["history"], reserved, budget,
                       compose=_render._compose_system,
                       notes=frozenset() if opener else frozenset(
                           n["index"] for n in a.get("notes", ()) if n["index"] is not None))
    # After the fit, never before: what the packer DROPPED is what decides
    # whether two runs of a shared heading became one.
    _render._dedupe_runs(packed["sections"])
    return {**packed, "budget": budget}


def _profile_sections(base: list[dict], guidance: str) -> list[dict]:
    """Select guidance in the frozen layout, retaining shared-heading frames.

    The ordinary render's heading copies describe the layout WITHOUT guidance.
    Inserting it can split an off-scene-cast run. Give the newly separated half
    its frozen heading before packing, so the added tokens participate in fit.
    """
    headings = {s["heading"]: s["heading_text"] for s in base if s["heading_text"]}
    sections = []
    last = ""
    for original in base:
        section = dict(original)
        if section["id"] == "model_guidance":
            section["text"] = guidance
        if not section["text"]:
            # A held-back-only World info row, or an empty Recalled history
            # row that says why: nothing to frame, opens no run.
            if (section.get("lore") or {}).get("held_back") or "history" in section:
                sections.append(section)
            continue
        heading = section["heading"]
        frozen_heading = headings.get(heading, "")
        if frozen_heading and heading != last and not section["heading_text"]:
            section["heading_text"] = frozen_heading
            section["text"] = frozen_heading + "\n\n" + section["text"]
        sections.append(section)
        last = heading
    return sections


def _prepare(a: dict, cid: str, sid: str, *, model: str, describe: bool,
             opener: bool = False, before_post: tuple[dict, ...] = (),
             after_post: tuple[dict, ...] = (),
             extra: tuple[tuple[str, str], ...] = (),
             after_history: tuple[dict, ...] = ()) -> tuple[model_guidance.PreparedMessages, dict | None]:
    """Freeze one generation, including each available profile's packed variant.

    A fallback is the same scene evidence with different model advice. Reusing
    `_assemble` or the render pass here would reroll macros and read a newer
    scene; swapping strings after packing would make the token ceiling a lie.
    Capture the rendered inputs first, then reuse the existing packer and
    breakdown over independent section dictionaries. Pack the finite profile
    set now: a compiled Jinja template can still load includes dynamically,
    so retaining the template alone would not freeze a later fallback. This
    costs one packing pass per distinct profile text plus the empty profile;
    the unbounded, undescribed path still does no token counting.
    """
    base = _render._render_sections(a, cid, sid, opener=opener, keep_guidance_slot=True,
                            describe_sources=describe)
    profiles = {}
    if any(s["id"] == "model_guidance" for s in base):
        data = {**a["data"], "opener": opener}
        for name, text in model_guidance.freeze_profiles(**data).items():
            wrapped = prompts.render("scene/sections/model_guidance.j2",
                                     **{**data, "model_guidance": text})
            profiles[name] = macros.expand_macros(wrapped, a["subs"], cid, sid,
                                                  datetime_subs=a.get("datetime_subs")).strip()
    compose = _render._compose_system
    history = deepcopy([] if opener else a["history"])
    notes = deepcopy([] if opener else a.get("notes", []))
    # Which history messages are author's notes: the packer keeps them off its
    # floor, and the breakdown reports them in a row of their own.
    note_index = frozenset(n["index"] for n in notes if n["index"] is not None)
    post_history = a["post_history"]
    before_post, after_post = deepcopy(before_post), deepcopy(after_post)
    after_history = deepcopy(after_history)
    budget = pack.budget_tokens(cid)
    count = _token_memo()
    reserved = (count(post_history)
                + sum(count(text) for _label, text in extra)) if budget > 0 else 0

    def variant(guidance):
        sections = _profile_sections(base, guidance)
        packed = pack.pack(sections, history, reserved, budget, compose=compose, count=count,
                           notes=note_index)
        _render._dedupe_runs(packed["sections"])
        packed["budget"] = budget
        system = compose([s["text"] for s in packed["sections"] if not s["dropped"]])
        messages = [{"role": "system", "content": system}] if system or opener else []
        # A play decision's block (`after_history`, roadmap 02 §6.5) follows
        # the last post, as an author's note at depth 0 does, and precedes a
        # director note -- also one a trailing carrier is folded into (#377).
        messages += _merged_carrier(packed["history"], before_post, after_history)
        if post_history:
            messages.append({"role": "system", "content": post_history})
        messages += after_post
        detail = (_inspection._breakdown({"post_history": post_history, "notes": notes,
                              "prompt_field_statuses": a.get("prompt_field_statuses")}, packed,
                             list(extra), count=count)
                  if describe else None)
        return messages, detail

    # Aliases and profiles with identical text share the same packing work.
    # Selection and prompt-record notification stay lazy: only a dispatched
    # fallback is observed, even though every possible payload is ready.
    frozen = {text: variant(text) for text in dict.fromkeys(["", *profiles.values()])}

    def select(selected_model):
        return frozen[model_guidance.guidance_for(selected_model, profiles)]

    prepared = model_guidance.PreparedMessages(model, select, profiles={
        "": frozen[""], **{name: frozen[text] for name, text in profiles.items()}},
        campaign=cid)
    prepared.settings = deepcopy(a.get("response_settings"))
    return prepared, prepared.breakdown


def _merged_carrier(history: list[dict], before_post: tuple[dict, ...],
                    between: tuple[dict, ...] = ()) -> list[dict]:
    """`history + between + before_post`, with a trailing carrier folded into
    a user message the prompt appends right after it (a director note, #377),
    so the carrier never sits beside another user message -- `between` (a
    play decision's system block) included, which a provider that lifts
    system messages out of the list would otherwise leave the two beside.
    New list, new dicts: the packed history and `before_post` are shared by
    every guidance variant."""
    tail = history[-1] if history else None
    first = before_post[0] if before_post else None
    if not (tail and tail.get(content_parts.CARRIER) and first and first["role"] == "user"):
        return [*history, *between, *before_post]
    content = first["content"]
    note = {**first, "content": [*tail["content"],
                                 *([{"type": "text", "text": content}]
                                   if isinstance(content, str) else content)]}
    return [*history[:-1], *between, note, *before_post[1:]]


def _token_memo():
    """`tokens.count_tokens`, memoized for one `_prepare`.

    `_prepare` packs and describes one variant per distinct model profile, and
    the variants differ only in the guidance section: the history, the post-
    history block, the appended messages and every other section are the same
    strings each time, and within one variant the packer and `_breakdown`
    measure the same sections and history twice over. Counted afresh they were
    re-encoded at every one of those -- the whole transcript, several times per
    turn. Only a composed system message is new per variant, and it is counted
    once either way. Per call rather than process-wide, so it holds only strings
    this compose already holds and needs no eviction story; and the tokenizer
    is looked up on each miss, so a test patching `count_tokens` still sees
    every distinct string.
    """
    seen: dict[str, int] = {}
    used: set[str] = set()

    def count(text: str) -> int:
        n = seen.get(text)
        if n is None:
            n = seen[text] = tokens.count_tokens(text)
            if text and (which := tokens.last_counter()):
                used.add(which)
        return n

    def counted_with() -> str:
        """Which counter produced this compose's numbers: one name, `MIXED`
        when an encode fell back on some strings, "" when nothing was counted
        (or a test replaced `count_tokens`, which records no counter)."""
        if len(used) > 1:
            return tokens.MIXED
        return next(iter(used), "")

    count.counted_with = counted_with
    return count
