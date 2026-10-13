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

import re
from dataclasses import replace

from ... import prompts
from .. import config
from ..history import budget, model, query, settings
from ..history import retrieve as retrieval
from . import archive, layout, story

_SLOT = re.compile(r"\x00(\d+)\x00")
_SPAN = re.compile(r"\x03([ep]\d+(?:_\d+)?)([se])\x03")


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
    cfg = config.read_config()
    if settings.read(cfg) is None or "history_recall" not in layout.enabled_ids(("history_recall",)):
        return None
    prepared = query.prepare(cid, sid)
    omissions = summary_omissions(cid, sid, prepared.archive_scan_text)
    return retrieval.retrieve_preview(cid, sid, prepared.query, budget.ceiling(cfg, resolved),
                                      summary_omissions=omissions)


def _frame(blocks: dict[str, str], order: list[str], expand):
    """Freeze template runs by their neighbouring slots, preserving occurrences."""
    def skeleton(kept):
        return _SLOT.split(budget.render_blocks([f"\x00{n}\x00" for n, key in enumerate(order) if key in kept]))

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


def _marked_item(item: model.EvidenceItem) -> str:
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
    marked = replace(item, excerpts=tuple(excerpts))
    if excerpts:
        marked = replace(marked, excerpts=(replace(excerpts[0], text="\x01" + excerpts[0].text), *excerpts[1:]))
    return budget.render_item(marked)


def _unmark(block: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """Expanded markers become offsets in the exact frozen, unmarked block."""
    out, cursor, length = [], 0, 0
    starts = {}
    spans = {}
    for match in _SPAN.finditer(block):
        piece = block[cursor:match.start()]
        out.append(piece)
        length += len(piece)
        name, edge = match.groups()
        if edge == "s":
            starts[name] = length
        else:
            spans[name] = (starts[name], length)
        cursor = match.end()
    out.append(block[cursor:])
    return "".join(out), spans


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
    return replace(item, excerpts=tuple(excerpts), evidence_ids=()), fitted


def section(evidence: model.Evidence, expand, count, omissions: dict[str, str], cfg: dict) -> tuple[str, dict]:
    """Expanded text and explicit inspector metadata plus the packer's shed hook."""
    cap = min(evidence.ceiling, budget.ceiling(cfg, None))
    items = [replace(item, summary_omission=omissions.get(item.scene.sid, "")) for item in evidence.items]
    items = [item for item in items if (item.summary and not item.summary_omission)
             or any(excerpt.text for excerpt in item.excerpts)]
    blocks = {item.scene.key: _marked_item(item) for item in items}
    order = [item.scene.key for item in items]
    render = _frame(blocks, order, expand)
    boundaries, spans = {}, {}
    for key in order:
        before, marker, after = blocks[key].partition("\x01")
        blocks[key], spans[key] = _unmark(before + after)
        boundaries[key] = len(before) if marker else None
    depth = settings.read(cfg)
    share = max(budget.MIN_ITEM_TOKENS, cap // (depth.depth if depth else max(len(items), 1)))
    kept: set[str] = set()
    fitted = []
    for item in items:
        key = item.scene.key
        original = blocks[key]
        def fits(text, key=key):
            blocks[key] = text
            return count(text) <= share and count(render(frozenset(kept | {key}))) <= cap
        block = _fit_block(original, fits, boundaries[key]) if cap >= budget.MIN_ITEM_TOKENS else ""
        retained, block = _retained(item, original, block, spans[key]) if block else (item, "")
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
