"""Render captured system sections and preserve contiguous shared headings."""

from __future__ import annotations

from .... import prompts
from .. import actor, layout, macros, source_text
from . import _catalog, _lore_render


def _render_sections(a: dict, cid: str, sid: str, *, opener: bool = False,
                     keep_guidance_slot: bool = False, describe_sources: bool = False
                     ) -> list[dict]:
    """Every applicable section, rendered and macro-expanded once, in order.

    THE render path: `build_messages` joins what survives packing into the
    system message and `context_sections` reports the same list, so a section
    the inspector shows as sent is a section that was sent. Empty sections drop
    out here, exactly as system.j2's per-section `if s.strip()` used to.

    `layout.apply` is what makes the order the READER's (#29) — the catalog
    while they have no layout or have switched theirs off, their merge of it
    otherwise. It sits here rather than beside the catalog for the same reason
    the catalog is walked here at all: this is the one render, so a section the
    layout dropped is dropped from the inspector too, and the two cannot
    disagree about what went out.

    `pinned` rides on the section rather than changing its `tier`: the tier says
    what KIND of content it is, which a reader's pin does not alter, and the
    inspector shows both. It is matched on the section's `id`, never its label,
    for the reason `Section.id` exists at all — from #29 the label is the
    reader's to edit and two sections may legitimately share one, so a pin
    keyed on the label could hold up the wrong section or, after a rename,
    none. `.get` on the key so a hand-built `a` (several tests) is still
    renderable.
    """
    data = {"model_guidance": "", "history_evidence": [], "history_withheld": [],
            "history_known": [], "history_suspected": [], "history_actor_name": "",
            **a["data"], "opener": opener}
    pinned = a.get("pinned_sections") or frozenset()
    # Resolved once by `_assemble`; `None` (a hand-built `a`) resolves per call.
    dt_subs = a.get("datetime_subs")
    # The activated entries as hits (`_assemble`). Absent from a hand-built `a`,
    # which then renders World info whole, as every section renders.
    lore = a.get("lore")

    def expand(text: str, capture: source_text.Capture | None = None) -> str:
        return macros.expand_macros(text, a["subs"], cid, sid, datetime_subs=dt_subs,
                                    **({"capture": capture} if capture else {}))

    out = []
    #: The heading of the last section actually EMITTED, which is what makes
    #: the rule below about contiguous runs rather than the whole message.
    last_heading = ""
    for section in layout.apply(_catalog.SECTIONS):
        if not _catalog._applies(section, pcless=data["pcless"], opener=opener):
            continue
        # Keep the enabled layout position without rendering a profile yet.
        # Empty guidance must not split a shared-heading run; a selected profile
        # can split it later using the already frozen heading text.
        if section.id == "model_guidance" and keep_guidance_slot:
            out.append({"id": section.id, "label": section.label, "text": "",
                        "tier": section.tier, "pinned": False,
                        "heading": "", "heading_text": ""})
            continue
        capture = source_text.Capture() if describe_sources else None
        section_data = _lore_render._source_data(section, data, lore, capture)
        body, capture, section_data = _lore_render._section_body(section, data, capture, section_data)
        # Heading eligibility sees the original body, even if an annotated
        # custom template emitted only whitespace. A phantom heading could
        # otherwise consume a macro draw that belongs to the next section.
        # A shared heading (`Section.heading`) opens each contiguous RUN of the
        # sections that name it, and this is the only place that can tell: it
        # holds the reader's merged layout and the rendered text at once, so
        # "which of these comes first", "which of these has anything to say"
        # and "are they still next to each other" are all answerable here and
        # nowhere else.
        #
        # Per run, not once per message, because the heading frames a BLOCK. A
        # layout that puts another section between the two halves has made two
        # blocks, and that section's own `# Heading` closes the first -- so a
        # suppressed second copy leaves the later half reading as part of
        # whatever came between, which is the unframed list #423 is about.
        # The catalog keeps them adjacent, so the default prompt has one run
        # and is unchanged.
        #
        # A section that rendered EMPTY does not break a run: it puts nothing
        # between the halves, so `last_heading` moves only when something is
        # actually appended.
        #
        # The heading is expanded FIRST and separately, and both halves of that
        # matter. Separately, because the exact string that goes on the front
        # has to be the exact string `_dedupe_runs` can take back off, and
        # expanding the joined text leaves no handle on where the prefix ends.
        # First, because `{{random:...}}` and `{{roll:...}}` are draws off a
        # shared RNG, so expansion order is the order the draws land in: the
        # heading is printed above the body, and expanding the body first would
        # hand the body's position the heading's draw. Templates are editable
        # on disk, so a heading with a macro in it is a thing a reader can
        # have. Each half is still expanded exactly once, in the order it is
        # read.
        head = ""
        if body and section.heading and section.heading != last_heading:
            head = expand(prompts.render(section.heading, **data)).strip()
        text, extra = _lore_render._expanded_section(section, section_data, body, head, expand, a,
                                        capture=capture)
        if not text:
            # `last_heading` stays put: an empty section breaks no run.
            out.extend(r for r in (_lore_render._held_back_only(section, pinned, extra),) if r)
            continue
        out.append({"id": section.id, "label": section.label,
                    "text": text, "tier": section.tier,
                    "pinned": section.id in pinned,
                    "heading": section.heading, "heading_text": head, **extra})
        last_heading = section.heading
    # Openers have their own final actor instruction and no handoff protocol.
    # The turn contract asks for a fenced handoff, which is invalid in a draft.
    if not opener:
        actor.add_contract(out, data)
    return out


def _dedupe_runs(sections: list[dict]) -> None:
    """Strip a shared heading that PACKING made adjacent to its own copy.

    Runs are decided while sections render, and the packer edits the list
    afterwards -- so a layout that put a droppable section between two members
    of a group renders two correctly-framed runs, and dropping that section
    closes them up into one block carrying its heading twice.

    In place, on the packed sections, so `_system_text` and `_breakdown` read
    the same strings: an inspector row still showing a heading the prompt does
    not have would be describing a different message from the one that went
    out, which is the split `_render_sections` exists to prevent.

    Safe in the direction packing cares about, which is why this can live after
    `pack.pack` while the mirror-image problem (reassigning a heading to a
    survivor when its carrier was dropped) cannot: `pack` measured the prompt
    WITH both copies, so removing one only ever lowers the total. The cost is a
    fit computed one heading pessimistically; adding tokens after the fit would
    be a ceiling already spent.
    """
    last = ""
    for s in sections:
        if s.get("dropped") or not s["text"]:
            continue   # sent nothing, so it closes no run (a held-back-only row)
        head = s.get("heading") or ""
        dupe = s.get("heading_text")
        if head and head == last and dupe and s["text"].startswith(dupe):
            s["text"] = s["text"][len(dupe):].lstrip("\n")
            s["heading_text"] = ""
        # Set even when stripped: the section is still in the group, so a THIRD
        # member closing up behind it must dedupe against the same heading.
        last = head


def _compose_system(texts: list[str]) -> str:
    """The system message as it will be sent, from section texts."""
    # Only sections with text: a held-back-only World info row is in the list
    # for the inspector and has nothing to say to the model.
    return prompts.render("scene/system.j2", sections=[t for t in texts if t]).strip()


def _system_text(packed_sections: list[dict]) -> str:
    """Join the sections that survived packing into the system message."""
    return _compose_system([s["text"] for s in packed_sections if not s["dropped"]])
