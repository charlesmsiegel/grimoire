"""Frozen scene evidence, fitted after macros and shed without another draw.

Theory: retrieval chooses usable scenes outside compose; this module consumes
only those choices. Jinja owns the frame and item wording. Frame runs and item
blocks expand once in document order, then every fit/profile/shed uses strings.
Reused: history.budget renderers and the generic packer's per-unit shed hook.
Assumes: editable templates keep macros inside fixed runs or inside item blocks.
Cost: one render/expansion per selected block, logarithmic prefix fitting, and
one frame render per fit/shed. No transcript expansion, retrieval or network.
Watch: summary omission must name an enabled carrier before depth selection;
composition can only refit supplied choices, never retrieve a replacement.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from ... import prompts
from .. import campaign_settings
from ..appearances import cast as appearances_cast
from ..history import budget, model, query, retrieval, settings
from ..scenes import identity as scenes_identity
from . import archive, epistemic, layout, story

log = logging.getLogger(__name__)
_SLOT = re.compile(r"\x00(\d+)\x00")
_SPAN = re.compile(r"\x03([eps]\d+(?:_\d+)?)([se])\x03")


#: The four sections a view renders into (spec 11 §6.2), each pair in its
#: order: the first of a pair keeps the row when it carries nothing, and the
#: second's share of the ceiling is reserved first.
NARRATOR_SECTIONS = ("history_recall", "history_narrator_only")
ACTOR_SECTIONS = ("history_known", "history_suspected")
FRAMES = {
    "history_recall": budget.FRAME,
    "history_narrator_only": "scene/sections/history_narrator_only.j2",
    "history_known": "scene/sections/history_actor_known.j2",
    "history_suspected": "scene/sections/history_actor_suspected.j2",
}
#: The data key each section's template reads its items from.
DATA_KEYS = {"history_recall": "history_evidence", "history_narrator_only": "history_withheld",
             "history_known": "history_known", "history_suspected": "history_suspected"}


@dataclass(frozen=True)
class Part:
    """One of a view's sections, before macros and fitting: its items as
    renderable evidence, each item's note (`history_item.j2`), its frame and
    the classification rows its inspector row carries."""
    id: str
    items: tuple[model.EvidenceItem, ...]
    notes: Mapping[str, dict] = field(default_factory=dict)
    frame: str = budget.FRAME
    frame_vars: Mapping[str, object] = field(default_factory=dict)
    rows: Mapping[str, object] = field(default_factory=dict)
    #: Shared by the two sections of a pair: what the first one spent, for
    #: the second to fit inside what is left. The first renders before the
    #: second (the catalog, and `layout.COUPLED`, keep them in that order).
    spent: dict = field(default_factory=dict, compare=False)

    @property
    def second(self) -> bool:
        return self.id in (NARRATOR_SECTIONS[1], ACTOR_SECTIONS[1])

    def room(self, cap: int) -> int:
        """This section's ceiling: 09's for the first of a pair, what the
        first left for the second -- so the second gives way first, each
        item keeps 09's share, and the pair never spends more than 09 would."""
        return max(0, cap - self.spent.get("first", 0)) if self.second else cap

    def spend(self, tokens: int) -> None:
        if not self.second:
            self.spent["first"] = tokens


def _excerpts(slices) -> tuple[model.Excerpt, ...]:
    return tuple(model.Excerpt(s.source, s.text) for s in slices if s.part == "posts" and s.text)


#: An NPC's own sections say this for an unstamped player post: the
#: transcript's `You` (`appearances.cast.UNSTAMPED_PLAYER_LABEL`), directly
#: under "You were there:", would read as the NPC itself.
ACTOR_PLAYER_LABEL = "The player"


def _actor_excerpts(slices) -> tuple[model.Excerpt, ...]:
    """`_excerpts` for an NPC: the same posts, an unstamped player post
    relabelled so its speaker cannot be mistaken for the NPC."""
    out = []
    for s in slices:
        if s.part != "posts" or not s.text:
            continue
        if any(p.speaker == appearances_cast.UNSTAMPED_PLAYER_LABEL for p in s.source):
            posts = tuple(replace(p, speaker=ACTOR_PLAYER_LABEL)
                          if p.speaker == appearances_cast.UNSTAMPED_PLAYER_LABEL else p
                          for p in s.source)
            out.append(budget._excerpt(posts))
        else:
            out.append(model.Excerpt(s.source, s.text))
    return tuple(out)


def _labels(slices) -> list[str]:
    """Each excerpt's access, said only where it changes."""
    out, last = [], ""
    for s in slices:
        if s.part == "posts" and s.text:
            out.append(s.access if s.access != last else "")
            last = s.access
    return out


def parts(view: epistemic.EpistemicView, names: Mapping[str, str],
          actor_name: str = "") -> dict[str, Part]:
    """`view` as its sections (11-S3). The narrator's: every attributed or
    unattributed item in `history_recall` (an attributed one with its
    `Known to:` / `Suspected by:` lines, names in present order) and every
    withheld item in `history_narrator_only`, never both. An actor's, from
    `visible()` alone: witnessed and known slices in `history_known`,
    suspected ones in `history_suspected`. `names` maps a ref to the name the
    prompt calls it by."""
    detail = view.detail()
    rows = {id(c): row for c, row in zip(view.items, detail["items"], strict=True)}
    shown = view.visible_pairs()
    out: dict[str, list] = {}
    notes: dict[str, dict[str, dict]] = {}
    if view.perspective.actor is None:
        first, second = NARRATOR_SECTIONS
        for origin, c in shown:
            sid = second if c.state == "withheld" else first
            item = replace(c.item, excerpts=_excerpts(c.slices), evidence_ids=())
            out.setdefault(sid, []).append((origin, item))
            if c.state == "attributed":
                notes.setdefault(sid, {})[item.scene.key] = {
                    "known_to": [names[r] for r in c.known_to if r in names],
                    "suspected_by": [names[r] for r in c.suspected_by if r in names]}
        frame_vars: dict[str, object] = {}
    else:
        first, second = ACTOR_SECTIONS
        for origin, c in shown:
            for sid, kinds in ((first, ("witnessed", "known")), (second, ("suspected",))):
                slices = [s for s in c.slices if s.access in kinds]
                if not slices:
                    continue
                summary = next((s for s in slices if s.part == "summary"), None)
                item = replace(c.item, summary=summary.text if summary else "",
                               excerpts=_actor_excerpts(slices), evidence_ids=())
                out.setdefault(sid, []).append((origin, item))
                notes.setdefault(sid, {})[item.scene.key] = {
                    "labels": _labels(slices),
                    "summary_label": summary.access if summary else ""}
        frame_vars = {"history_actor_name": actor_name}
    spent: dict = {}
    # The classification rides the first section's row alone, every item of
    # the view in it: listing the second's items again would show each part
    # (and its accept) twice.
    first_rows = {"perspective": detail["perspective"], "decided": detail["decided"],
                  **({"round": detail["round"]} if "round" in detail else {}),
                  # Who the rows' refs are, for the inspector and the accept.
                  "names": {r: names[r] for r in view.perspective.present if r in names},
                  "items": [rows[id(c)] for c in view.items]}
    return {sid: Part(sid, tuple(i for _c, i in out.get(sid, [])), notes.get(sid, {}),
                      FRAMES[sid], frame_vars, first_rows if sid == first else {}, spent)
            for sid in (first, second)}


def summary_omissions(cid: str, sid: str, archive_scan_text: str, full_recap: int = 0) -> dict[str, str]:
    """Enabled carriers over the exact un-clipped legacy archive activation scan."""
    enabled = layout.enabled_ids(("archive", "story_so_far"))
    recap = story._recap_ids(cid, full_recap or None)
    result = {entry["id"]: "omitted:archive"
              for entry in archive._archive_entries(cid, archive_scan_text, recap, before=sid)} if "archive" in enabled else {}
    if full_recap and "story_so_far" in enabled:
        result.update(dict.fromkeys(recap, "omitted:recap"))
    return result


def preview(cid: str, sid: str, resolved=None) -> model.Evidence | None:
    """The inspector's synchronous, provider-free preview; off reads no history."""
    cfg = campaign_settings.effective_config(cid)
    if settings.read(cfg) is None or "history_recall" not in layout.enabled_ids(("history_recall",)):
        return None
    try:
        prepared = query.prepare(cid, sid)
        omissions = summary_omissions(cid, sid, prepared.archive_scan_text)
        return retrieval.retrieve_preview(cid, sid, prepared.query, budget.ceiling(cfg, resolved),
                                          summary_omissions=omissions)
    except Exception:  # noqa: BLE001 -- a garbled history file costs the row, never the inspector
        log.warning("History preview failed for campaign %s scene %s", cid, sid)
        return model.Evidence(coverage=model.Coverage("error", semantic="skipped:preview"))


def preview_view(cid: str, sid: str, resolved=None) -> epistemic.EpistemicView | None:
    """The live inspector's view: `preview`, classified for the narrator over
    everyone on stage (11-S3). An NPC's own view appears only in its turn's
    capture. A classification that fails costs the annotations, not the row."""
    evidence = preview(cid, sid, resolved)
    if evidence is None:
        return None
    identity = scenes_identity.scene_identity(cid, sid)
    try:
        if evidence.coverage.verdict == "error":
            raise ValueError("nothing to classify")
        perspective = epistemic.Perspective.of(cid, sid, None, epistemic.present_roster(cid, sid),
                                               identity=identity)
        return epistemic.classify_evidence(perspective, evidence)
    except Exception:  # noqa: BLE001 -- the narrator keeps 09's items
        return epistemic.narrator_view(cid, sid, evidence, identity=identity)


def _frame(blocks: dict[str, str], order: list[str], expand, frame: str = budget.FRAME,
           frame_vars=None):
    """Freeze template runs by their neighbouring slots, preserving occurrences."""
    def skeleton(kept):
        return _SLOT.split(budget.render_blocks(
            [f"\x00{n}\x00" for n, key in enumerate(order) if key in kept], frame, frame_vars))

    def neighbours(pieces, n):
        return (order[int(pieces[n-1])] if n else None,
                order[int(pieces[n+1])] if n+1 < len(pieces) else None)

    pieces = skeleton(frozenset(order))
    runs = {}
    for n, piece in enumerate(pieces):
        if n % 2:
            key = order[int(piece)]
            blocks[key] = expand(blocks[key])
        else:
            runs[(piece.strip(), *neighbours(pieces, n))] = expand(piece.strip())
    suffixes = {pieces[-1].strip()} - {pieces[0].strip()}

    def render(kept):
        if not kept:
            return ""
        out = []
        pieces = skeleton(kept)
        for n, piece in enumerate(pieces):
            if n % 2:
                out.append(blocks[order[int(piece)]])
                continue
            core = piece.strip()
            before, after = neighbours(pieces, n)
            key = (core, before, after)
            got = runs.get(key)
            if got is None:
                # A disappearing slot closes two runs up. The full render
                # determines ownership: suffix text belongs to the preceding
                # body; other fixed text to the following body. Never redraw.
                got = next((value for (text, left, right), value in runs.items()
                            if text == core and ((left == before) if core in suffixes else (right == after))), core)
            lead = piece[:len(piece)-len(piece.lstrip())]
            out.append(lead + got + piece[len(lead)+len(core):])
        return "".join(out).strip()
    return render


def _fit_block(block: str, fits, boundary: int | None) -> str:
    """Keep a bounded prefix, including content beyond the header or nothing."""
    if fits(block):
        return block
    # The header is not evidence. Preserve it whole; trimming may shorten only
    # following summary/excerpt text and must leave some actual content.
    if boundary is None:
        return ""
    lo, hi, best = boundary + 1, len(block)-1, ""
    while lo <= hi:
        mid = (lo+hi)//2
        trial = block[:mid].rstrip() + "\u2026"
        if fits(trial):
            best, lo = trial, mid+1
        else:
            hi = mid-1
    return best


def _marked_item(item: model.EvidenceItem, note=None) -> str:
    """Mark exact post content inside the existing authoritative excerpt text.

    S1's posts are the literal text snippets/transcript.j2 rendered. Locate its
    whole line first so a post saying only its speaker's name cannot mark the
    label instead. Hand-built evidence may use another label format; the
    literal content still has to be present or that excerpt is unusable.
    """
    excerpts = []
    for e, excerpt in enumerate(item.excerpts):
        pieces: list[str] = []
        cursor = 0
        for p, post in enumerate(excerpt.posts):
            line = prompts.render("snippets/transcript.j2", messages=[{
                "role": "assistant", "speaker": post.speaker, "content": post.text}])
            position = excerpt.text.find(line, cursor)
            start = (position + line.rfind(post.text) if position >= 0
                     else excerpt.text.find(post.text, cursor))
            if start < 0:
                break
            end = start + len(post.text)
            pieces.extend((excerpt.text[cursor:start], f"\x03p{e}_{p}s\x03",
                           post.text, f"\x03p{e}_{p}e\x03"))
            cursor = end
        else:
            pieces.append(excerpt.text[cursor:])
            excerpts.append(replace(excerpt, text=f"\x03e{e}s\x03" + "".join(pieces) + f"\x03e{e}e\x03"))
    # Admission after macro fitting uses the summary content actually sent,
    # not a non-empty macro expression that may have expanded to nothing.
    summary = ("\x03s0s\x03" + item.summary + "\x03s0e\x03"
               if item.summary and not item.summary_omission else item.summary)
    marked = replace(item, summary=summary, excerpts=tuple(excerpts))
    if excerpts:
        marked = replace(marked, excerpts=(replace(excerpts[0], text="\x01" + excerpts[0].text), *excerpts[1:]))
    return budget.render_item(marked, note)


def _unmark(block: str) -> tuple[str, dict[str, tuple[int, int]] | None]:
    """Expanded markers become offsets in the exact frozen, unmarked block.

    None when a marker lost its partner: a `{{` in one part closed by a `}}`
    in a later one (another post, an excerpt, or the summary after a title) is
    one macro to the expander, which rewrites the text between -- markers
    included. The posts it covered can no longer be named, so the item cannot
    be frozen as evidence; the caller drops it rather than guess."""
    out, cursor, length = [], 0, 0
    starts: dict[str, int] = {}
    spans: dict[str, tuple[int, int]] = {}
    broken = False
    for match in _SPAN.finditer(block):
        piece = block[cursor:match.start()]
        out.append(piece)
        length += len(piece)
        name, edge = match.groups()
        if edge == "s":
            starts[name] = length
        elif name in starts:
            spans[name] = (starts.pop(name), length)
        else:
            broken = True
        cursor = match.end()
    out.append(block[cursor:])
    return "".join(out), (None if broken or starts else spans)


def _retained(item: model.EvidenceItem, original: str, fitted: str,
              spans: dict[str, tuple[int, int]]) -> tuple[model.EvidenceItem, str]:
    """Project the surviving expanded prefix back into posts and evidence IDs."""
    clipped = fitted != original
    cut = len(fitted) - 1 if clipped else len(original)
    # A prefix cut in the join or next speaker's label has not included that
    # post. Remove its dangling frame; a cut inside content keeps its prefix.
    ends = [min(end, cut) for name, (start, end) in spans.items()
            if name.startswith("p") and start < cut and original[start:min(end, cut)].strip()]
    if clipped and ends:
        cut = max(ends)
        fitted = original[:cut].rstrip() + "\u2026"
    excerpts = []
    for e, excerpt in enumerate(item.excerpts):
        posts = []
        for p, post in enumerate(excerpt.posts):
            start, end = spans.get(f"p{e}_{p}", (cut, cut))
            text = original[start:min(end, cut)] if start < cut else ""
            if text.strip():
                posts.append(replace(post, text=text + ("\u2026" if end > cut else "")))
        if posts:
            start, end = spans[f"e{e}"]
            text = original[start:min(end, cut)]
            excerpts.append(model.Excerpt(tuple(posts), text + ("\u2026" if end > cut else "")))
    start, end = spans.get("s0", (0, 0))
    summary = item.summary if item.summary_omission else original[start:min(end, cut)].strip()
    return replace(item, summary=summary, excerpts=tuple(excerpts), evidence_ids=()), fitted


def section(evidence: model.Evidence, expand, count, omissions: dict[str, str], cfg: dict,
            part: Part | None = None) -> tuple[str, dict]:
    """Expanded text and explicit inspector metadata plus the packer's shed hook.

    `part` is one of 11's sections (`parts`): its frame, its items' notes,
    its share of the ceiling and its classification rows. None is 09's
    section, rendered from `evidence` exactly as 09 rendered it."""
    cap = min(evidence.ceiling, budget.ceiling(cfg, None))
    if part is None:
        part = Part(NARRATOR_SECTIONS[0], evidence.items)
    notes = part.notes
    evidence = replace(evidence, items=part.items)
    items = [replace(item, summary_omission=omissions.get(item.scene.sid, "")) for item in evidence.items]
    items = [item for item in items if (item.summary and not item.summary_omission)
             or any(excerpt.text for excerpt in item.excerpts)]
    blocks = {item.scene.key: _marked_item(item, notes.get(item.scene.key)) for item in items}
    order = [item.scene.key for item in items]
    render = _frame(blocks, order, expand, part.frame, part.frame_vars)
    boundaries, spans = {}, {}
    for key in order:
        before, marker, after = blocks[key].partition("\x01")
        blocks[key], spans[key] = _unmark(before + after)
        boundaries[key] = len(_SPAN.sub("", before)) if marker else None
    # The share retrieval fitted each item to: the selection's own depth when
    # the evidence says it, else the setting's.
    configured = settings.read(cfg)
    depth = evidence.depth or (configured.depth if configured else len(items))
    share = max(budget.MIN_ITEM_TOKENS, cap // max(depth, 1))
    cap = part.room(cap)
    kept: set[str] = set()
    fitted = []
    for item in items:
        key = item.scene.key
        original = blocks[key]
        def fits(text, key=key):
            blocks[key] = text
            return count(text) <= share and count(render(frozenset(kept | {key}))) <= cap
        marks = spans[key]
        usable = cap >= budget.MIN_ITEM_TOKENS and marks is not None
        block = _fit_block(original, fits, boundaries[key]) if usable else ""
        retained, block = _retained(item, original, block, marks) if block and marks else (item, "")
        if block and (retained.excerpts or (retained.summary and not retained.summary_omission)) and fits(block):
            kept.add(key)
            fitted.append(replace(retained, tokens=count(block)))
        else:
            blocks[key] = ""
    detail = replace(evidence, items=tuple(fitted), ceiling=cap).detail()
    for row, item in zip(detail["items"], fitted, strict=True):
        row["posts"] = [{"index": p.index, "key": p.key, "part": p.part,
                         "speaker": p.speaker, "text": p.text}
                        for excerpt in item.excerpts for p in excerpt.posts]
    part.spend(count(render(frozenset(kept))) if kept else 0)
    if part.rows:
        detail["epistemic"] = dict(part.rows)
    detail["budget_from"] = ["config"]
    if settings.integer(cfg.get("context_budget"), 0):
        detail["budget_from"].append("context_budget")
    if evidence.ceiling < budget.ceiling(cfg, None):
        detail["budget_from"].append("context_window")
    units = [{"ref": item.scene.key, "priority": len(fitted)-pos, "keep": False,
              "pinned": False, "direct": True, "age": -1, "pos": pos}
             for pos, item in enumerate(fitted)]
    return render(frozenset(kept)), {"history": detail,
            "shed": {"units": units, "render": render}}
