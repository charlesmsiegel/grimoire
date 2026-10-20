"""Render captured lore and recalled history with exact source attribution."""

from __future__ import annotations

import random
import re
from copy import deepcopy

from .... import prompts
from ... import config, entities, tokens
from .. import activation, source_text
from .. import history as history_section
from . import _catalog, _inputs


def _is_secret(hit: activation.Hit) -> bool:
    """`world_state.secrecy_split`'s rule, for one hit."""
    return entities.normalize_secrecy(hit.entry.get("secrecy")) == entities.SECRET


def _template_order(hits: list[activation.Hit]) -> list[activation.Hit]:
    """Hits in the order a world-info template prints them: every public entry,
    then every secret one, each half in activation order."""
    return ([h for h in hits if not _is_secret(h)]
            + [h for h in hits if _is_secret(h)])


def _reason_refs(reason: dict) -> list[str]:
    """Every ref a reason names: the owner that opened its gate, how that owner
    is present, and the entry that pulled it in."""
    presence = reason.get("owner_presence")
    found = [reason.get("owner"), reason.get("via"),
             presence.get("via") if isinstance(presence, dict) else None]
    return [r for r in found if isinstance(r, str) and r]


def _reason_names(hits, catalog: dict[str, str]) -> dict[str, str]:
    """A display name for every ref `hits`' reasons name; a ref nothing
    resolves (a record deleted since, a hand-edited owner) names itself."""
    return {ref: catalog.get(ref, ref) for h in hits for ref in _reason_refs(h.reason)}


def _entry_name(entry: dict) -> str:
    return str(entry.get("name") or entry.get("id") or "")


def _lore_source(hit: activation.Hit, body: str,
                 capture: source_text.Capture | None) -> str:
    """Frame an already-expanded lore body, in the first render or a shed."""
    return capture.mark(_entry_name(hit.entry), body, hit.ref) if capture else body


def _lore_detail(hits: list[activation.Hit], catalog: dict[str, str]) -> dict:
    """What an inspector row says about the entries in its section (spec §10),
    JSON-safe and never part of a prompt: `_breakdown` copies it onto the row
    and nothing renders it."""
    entries = []
    for h in hits:
        c = activation.controls(h.entry)
        entries.append({"ref": h.ref, "name": _entry_name(h.entry), "kind": h.entry.get("kind"),
                        "secrecy": entities.normalize_secrecy(h.entry.get("secrecy")),
                        "priority": c.priority, "keep": c.keep, "level": h.level,
                        "reason": deepcopy(h.reason)})
    return {"entries": entries, "names": _reason_names(hits, catalog)}


#: Stands in for one entry body while World info's template is rendered, so
#: the fixed text around the bodies can be told apart from them. NUL never
#: appears in a template, and the slot holds no `{{`, so it is not a macro.
_BODY_SLOT = re.compile("\x00(\\d+)\x00")


#: A run of World info's fixed text, by occurrence: (text, slot before, slot after).
_RunKey = tuple[str, str | None, str | None]


def _recall_run(runs: dict[_RunKey, str], suffixes: set[str], key: _RunKey, expand) -> str:
    """The expansion a re-rendered run of World info's fixed text keeps, by
    occurrence: `key` is (text, slot before, slot after). A run whose neighbour
    was shed takes the draw of the occurrence that shares its text and the slot
    it still has -- before for a suffix, after for anything else (see
    `_world_info_section`); text never seen is expanded. Remembered either way,
    so the next re-render is not a new draw."""
    if key not in runs:
        core, before, after = key
        sides = ((1, before), (2, after)) if core in suffixes else ((2, after), (1, before))
        found = next((got for side, ref in sides if ref is not None
                      for k, got in runs.items() if k[0] == core and k[side] == ref), None)
        runs[key] = expand(core) if found is None else found
    return runs[key]


def _held_back_only(section: _catalog.Section, pinned, extra: dict) -> dict | None:
    """A text-less World info row for an empty section that still held entries
    back -- the turn where cooldown suppresses the only relevant entry is the
    turn the reader most needs to see why -- and the same for Recalled history
    when recall is on and nothing was selected, so the row still says whether
    nothing existed or retrieval failed. It puts nothing in the prompt
    (`_compose_system` joins only what has some) and, like any section that
    rendered empty, breaks no heading run."""
    if not (extra.get("lore") or {}).get("held_back") and "history" not in extra:
        return None
    return {"id": section.id, "label": section.label, "text": "", "tier": section.tier,
            "pinned": section.id in pinned, "heading": section.heading, "heading_text": "",
            **extra}


def _world_info_section(section: _catalog.Section, data: dict, lore: dict, head: str,
                        expand, capture: source_text.Capture | None = None) -> tuple[str, dict]:
    """World info's text, plus what the packer needs to shed it an entry at a
    time and what its inspector row reports.

    The template is rendered with a slot in place of each body, and the result
    is expanded piece by piece in DOCUMENT order -- a run of the template's own
    text, then the body in the next slot, then the next run -- which is the
    left-to-right order expanding the whole rendered section always drew
    `{{random}}` in. So the unbounded section is the section-level expansion,
    the template's own text (the secret heading a reader may have edited)
    included, and a body that expands to nothing still leaves the gap it
    always left. Byte for byte, that holds for `{{random}}` under a seeded
    `random`, and only that far: the whole-text expansion drew every
    `{{random}}` before any `{{roll}}` and piece by piece they interleave,
    which is harmless only because a `{{roll}}` seeds itself from the OS
    (`dice.roll`) rather than from `random` -- so no seed reproduces a roll,
    whole or piecewise. A macro a hand-edited template splits across a slot's
    edge would also expand whole and not piecewise.

    Every expansion is remembered: a body by its entry's ref, a run of fixed
    text by OCCURRENCE -- its text less its edge whitespace (removing an entry
    only changes the joins around a run) together with the slots either side of
    it. The same fixed text twice is two draws, so the text alone cannot name
    one: a template that wraps every body in `{{random}}` would hand every
    re-rendered occurrence the first one's. `render(kept)` re-renders the slots
    for the kept entries and reuses those, so shedding one entry cannot re-draw
    another's macros or the template's, nor give one occurrence another's.

    A shed entry takes its slot out, and the runs either side of it close up
    into one, so a surviving run can find a neighbour gone. Which of the two
    draws it keeps depends on whom the text belonged to: text a template puts
    AHEAD of each body belongs to the body after it, text put BEHIND each body
    to the body before. The full render says which -- the leading edge of a
    prefixed list is that text, the trailing edge of a suffixed one -- so a
    run whose text closes the full render keeps the occurrence that shares its
    slot before, and any other the one that shares its slot after. Only text
    the full render never produced -- possible only in a hand-edited template
    -- is expanded, once.

    `head` is the section's already-expanded shared heading. World info declares
    none in the catalog, so it is "" today; `render` closes over it so a heading
    added later would still lead every re-render."""
    hits = _template_order(lore["world_info"])
    # Only a non-empty body takes a slot, as the template's `select` keeps only
    # those -- a raw-empty body never rendered anything.
    slotted = [i for i, h in enumerate(hits) if h.entry.get("body")]
    template = _catalog._section_template(section, data)
    bodies: dict[str, str] = {}
    runs: dict[_RunKey, str] = {}

    def skeleton(kept: frozenset) -> list[str]:
        shown = [i for i in slotted if hits[i].ref in kept]
        rendered = prompts.render(template, **{
            **data,
            "world_info_bodies": [f"\x00{i}\x00" for i in shown if not _is_secret(hits[i])],
            "secret_world_info_bodies": [f"\x00{i}\x00" for i in shown if _is_secret(hits[i])],
        }).strip()
        return _BODY_SLOT.split(rendered)

    def finish(body: str) -> str:
        body = body.strip()
        return head + "\n\n" + body if (head and body) else body

    def neighbours(pieces: list[str], n: int) -> tuple[str | None, str | None]:
        """The refs of the slots either side of run `n` (None at an edge)."""
        before = hits[int(pieces[n - 1])].ref if n else None
        after = hits[int(pieces[n + 1])].ref if n + 1 < len(pieces) else None
        return before, after

    def run(piece: str, got: str) -> str:
        # Keyed on the run WITHOUT its edge whitespace: removing an entry only
        # adds or drops the joins around a run (and the outer strip), and
        # whitespace holds no macro, so the run itself is the same text.
        core = piece.strip()
        lead = piece[:len(piece) - len(piece.lstrip())]
        return lead + got + piece[len(lead) + len(core):]

    # The first render draws, in document order, every occurrence afresh -- the
    # same fixed text twice is two expansions, as it was in the joined section.
    out = []
    pieces = skeleton(frozenset(h.ref for h in hits))
    for n, piece in enumerate(pieces):
        if n % 2:
            hit = hits[int(piece)]
            got = bodies.setdefault(hit.ref, expand(hit.entry["body"]))
            out.append(_lore_source(hit, got, capture))
        else:
            core = piece.strip()
            runs[(core, *neighbours(pieces, n))] = got = expand(core)
            out.append(run(piece, got))
    body, sources = capture.extract("".join(out)) if capture else ("".join(out), [])
    text = finish(body)
    # Text that ends the full render but does not start it: each body's suffix.
    suffixes = {pieces[-1].strip()} - {pieces[0].strip()}

    def describe(kept: frozenset) -> tuple[str, list[dict]]:
        # A surviving body may now touch the section's outer strip. Capture
        # its new slice DURING this re-render, never by rendering again later:
        # the packer and inspector must describe the same frozen string.
        current = source_text.Capture() if capture else None
        pieces = skeleton(kept)
        joined = "".join(
            _lore_source(hits[int(piece)], bodies[hits[int(piece)].ref], current) if n % 2
            else run(piece, _recall_run(runs, suffixes, (piece.strip(), *neighbours(pieces, n)),
                                       expand))
            for n, piece in enumerate(pieces))
        text, sources = current.extract(joined) if current else (joined, [])
        return finish(text), sources

    def render(kept: frozenset) -> str:
        return describe(kept)[0]

    units = []
    for pos, h in enumerate(hits):
        c = activation.controls(h.entry)
        units.append({"ref": h.ref, "priority": c.priority, "keep": c.keep,
                      "pinned": h.reason.get("type") == "pinned",
                      "direct": h.direct, "age": h.age, "pos": pos})
    detail = _lore_detail(hits, lore["names"])
    detail["held_back"] = [{"ref": h.ref, "name": _entry_name(h.entry),
                            "reason": deepcopy(h.reason)} for h in lore["held_back"]]
    return text, {"shed": {"units": units, "render": render,
                           **({"describe": describe} if capture else {})}, "lore": detail,
                  **({"sources": sources} if sources else {})}


def _expanded_section(section: _catalog.Section, data: dict, body: str, head: str, expand, a: dict, *,
                      capture: source_text.Capture | None = None) -> tuple[str, dict]:
    """One rendered section's text after macro expansion, with `head` (already
    expanded) on the front, and any keys it adds to its section dict.

    World info is expanded piece by piece instead, so the packer can shed one
    entry at a time (spec §6) without re-drawing anything: see
    `_world_info_section`. Recalled history is fitted from its frozen view
    (`_recalled_history`). Recalled lore is expanded whole, as every other
    section is, and only gains its inspector detail."""
    if section.id in history_section.FRAMES:
        return _recalled_history(section, a, expand)
    # Absent from a hand-built `a`, which then renders World info whole (see
    # `_render_sections`).
    lore = a.get("lore")
    if lore is not None and section.id == _catalog._WORLD_INFO_SECTION:
        return _world_info_section(section, data, lore, head, expand, capture)
    if capture:
        expanded = expand(body, capture)
        body, sources = capture.finish(expanded)
    else:
        body, sources = expand(body).strip(), []
    text = head + "\n\n" + body if (head and body) else body
    extra: dict = {"sources": sources} if sources else {}
    if lore is not None and section.id == _catalog._RECALLED_SECTION:
        extra["lore"] = _lore_detail(_template_order(lore["recalled"]), lore["names"])
    return text, extra


def _recalled_history(section: _catalog.Section, a: dict, expand) -> tuple[str, dict]:
    """One of the recalled-history sections, fitted from the view `_assemble`
    froze (`history_section.section`) -- or nothing, when that view has no
    part for it or the part cannot be rendered."""
    view = a.get("history_view")
    part = (a.get("history_parts") or {}).get(section.id)
    if view is None or part is None:
        return "", {}
    # The first of a pair keeps its row when it carries nothing, so the
    # inspector can say why; the second has nothing to say then.
    if not part.items and section.id not in (history_section.NARRATOR_SECTIONS[0],
                                             history_section.ACTOR_SECTIONS[0]):
        return "", {}
    cfg = a.get("config")
    try:
        return history_section.section(view.evidence, expand, tokens.count_tokens,
                                       a.get("history_omissions") or {},
                                       config.read_config() if cfg is None else cfg, part)
    except Exception:  # noqa: BLE001 -- recalled history is additive; it never costs the turn
        # Per section: one that cannot render drops alone (spec 11 §6.4).
        _inputs.log.warning("Recalled history section %s dropped from a compose: it could not "
                    "be rendered", section.id)
        return "", {}


def _source_data(section: _catalog.Section, data: dict, lore: dict | None,
                 capture: source_text.Capture | None) -> dict:
    """Annotate this section's render inputs without changing its selection.

    Recall receives source identities BESIDE its bodies: a custom template
    may slice or filter those strings, and replacing them with annotations
    would change its prompt. Templates opt into capture after formatting.
    World info already expands bodies once for its shedding hook.
    """
    if capture is None:
        return data
    annotated = {**data, "capture_source": capture.mark}
    if lore is not None and section.id == _catalog._RECALLED_SECTION:
        hits = _template_order(lore["recalled"])
        for key, secret in (("recalled_lore_sources", False),
                            ("secret_recalled_lore_sources", True)):
            annotated[key] = [{"label": _entry_name(h.entry), "ref": h.ref}
                              for h in hits if _is_secret(h) == secret]
    return annotated


def _section_body(section: _catalog.Section, data: dict, capture: source_text.Capture | None,
                  section_data: dict) -> tuple[str, source_text.Capture | None, dict]:
    """One section's rendered body, with its capture and the data it rendered with.

    The plain render is the one that is sent, exactly as `describe=False` sends
    it, and it is the only render that draws from the process-wide `random`.
    The annotated render is detached: it draws from a private copy of the state
    the plain render started from, so a template using Jinja's `random` filter
    makes the same choices without advancing (or rewinding) a generator that
    another request's compose may be drawing from. Its unmarked body is kept
    only when it equals the plain one; a template that formatted a marked
    contribution (`| upper`, a slice) breaks its marks, and the section is then
    inspected whole.
    """
    template = _catalog._section_template(section, data)
    if capture is None:
        return prompts.render(template, **data).strip(), None, data
    rng = random.Random()
    rng.setstate(random.getstate())
    plain = prompts.render(template, **data).strip()
    body = capture.unmark(prompts.render_detached(_catalog._section_template(section, section_data), rng,
                                                  **section_data).strip())
    if body == plain:
        return body, capture, section_data
    return plain, None, data
