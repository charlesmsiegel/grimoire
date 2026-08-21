"""Diff one turn's composition against another's (#130).

`GET .../context` says what the prompt is *now* and `store.prompt_log` says what
it was at a past turn (#157), but the question a reader actually arrives with is
neither of those: it is "the last reply went wrong -- what is different about
this prompt from the one before it?". Two panels of a hundred sections each,
read side by side, do not answer that.

So this compares two `assemble._breakdown` payloads and reports the difference.
Both sides are the SAME shape -- the live route and a frozen snapshot already
agree on it, which is what lets the inspector render either with one component
-- so nothing here needs to know which side came from where.

Three deliberate choices, each of which had a cheaper alternative:

- **Full text, not token counts.** The issue's own note: a section whose
  content changed while its token count stayed flat is exactly the case a
  count-only diff hides, and it is a case that happens -- a swapped character
  state line, a different `{{random}}` roll, a world-info entry that activated
  in place of another of similar length. So the comparison is per line, through
  `changes.line_diff`, the same primitive (and the same tagged-row output)
  `ChangesPanel` already renders for record write-backs. That helper grew a
  common-prefix/suffix trim and a size bound for this caller (#130) -- see its
  docstring; a prompt section is two orders of magnitude longer than the record
  field it was written for, and both the precision and the cost of a naive
  difflib call go wrong at that size.

- **Matched by `id`, not by position.** Sections move: one dropping out shifts
  every row after it, and a positional pairing would then report every section
  in the prompt as changed. `id` is the stable identity #29 gave them. When a
  snapshot frozen before ids existed is one of the two, BOTH sides fall back to
  `label` -- keying each side by what it happens to have would compare
  `Character state` against `character_state` and match nothing (see
  `_key_field`). Labels predate editable labels too, so theirs are still
  unique, and the occurrence counter keeps a genuinely duplicated key from
  pairing two rows onto one.

- **Long unchanged runs are elided.** A record field is a paragraph; a prompt
  section is the whole transcript, and one appended exchange would otherwise
  ship several hundred `equal` rows to say so. Runs further than
  `CONTEXT_LINES` from any change collapse to a single `skip` row carrying its
  `count`. `skip` is an addition to `line_diff`'s vocabulary rather than a
  reinterpretation of it: every row still carries `op` and `text`, so a reader
  of the tagged format sees an op it does not know rather than a lie about
  content.

What this does NOT claim: that the two prompts were sent to the same model, or
under the same budget. Both are reported per side and can differ -- a snapshot
carries the ceiling it was packed to, which is the whole reason the frozen
panel measures against its own.

Nothing is truncated, and that is a decision rather than an oversight. The
answer is bounded by the two prompts -- worst case, comparing a scene's first
captured turn against the preview, the whole transcript ships as insertions --
which is the size of a payload `GET .../context` and `GET .../prompts/<id>`
already each serve on their own. A cap would put an ellipsis in the one view
whose entire job is showing what a reader could not otherwise see, and the
elision above already removes the part that is genuinely redundant.
"""

from __future__ import annotations

import difflib
from collections import Counter

from .. import changes

#: How many unchanged lines to keep either side of a change. Three is the
#: `diff -u` default, and the reason is the same: enough to place a hunk in its
#: section without reprinting the section.
CONTEXT_LINES = 3

#: An unchanged run this short is left alone. Replacing two lines with a row
#: reading "2 unchanged lines" costs the reader the lines and saves nothing.
MIN_ELIDED_RUN = 3


def _side(row: dict) -> dict:
    """One section as one side of a comparison -- everything except its text,
    which is what the diff rows carry.

    Every field is read with a default AND coerced: `prompt_log._well_formed`
    does not require `id` or `pinned` (a snapshot predating either is still
    servable), and a hand-edited file can hold anything at all. This module's
    promise is the one every `prompt_log` reader makes -- describe it, do not
    raise on it -- and `int("later")` would break that from the inside.
    """
    return {"label": str(row.get("label", "")),
            "tokens": _int(row.get("tokens")),
            "dropped": bool(row.get("dropped")),
            "trimmed": _int(row.get("trimmed")),
            "pinned": bool(row.get("pinned"))}


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _rows(sections: object) -> list[dict]:
    """The section rows worth looking at. A payload that is not a list, or a row
    that is not a dict, is a hand-edited file rather than a section."""
    if not isinstance(sections, list):
        return []
    return [row for row in sections if isinstance(row, dict)]


def _key_field(rows: list[dict]) -> str:
    """`id` only when EVERY row across BOTH sides carries one; `label` for all
    of them otherwise.

    Decided across both sides together, which review caught the first version
    getting wrong. Keying each side by whatever it happened to have looks right
    and is the exact case this module advertises handling -- a snapshot frozen
    before #29 against a composition made after it -- and it fails silently:
    `Character state` on one side and `character_state` on the other never
    match, so the panel reports every section of the prompt as removed and
    re-added rather than the one that changed. Falling back for both sides
    costs the newer side its stable identity for one comparison, which is the
    lesser of the two: labels were unique before they were editable, and the
    occurrence counter below covers the rest.
    """
    return "id" if all(row.get("id") for row in rows) else "label"


def _keyed(rows: list[dict], field: str) -> list[tuple[str, dict]]:
    """`(key, row)` per section, in prompt order.

    The key is `field` -- see `_key_field` -- plus how many rows with that same
    value came before it. The counter is not paranoia about hand-edited files:
    `_breakdown` numbers its appended rows precisely because two of them can
    share a label. Without it, difflib would pair the first occurrence on each
    side and silently drop the rest.
    """
    seen: Counter[str] = Counter()
    out: list[tuple[str, dict]] = []
    for row in rows:
        stem = str(row.get(field) or row.get("label") or "")
        seen[stem] += 1
        out.append((f"{stem}#{seen[stem]}", row))
    return out


def _elide(rows: list[dict]) -> list[dict]:
    """`line_diff` output with the far-from-anything runs collapsed. See the
    module docstring for why a prompt section needs this and a record field
    does not."""
    keep = [False] * len(rows)
    for i, row in enumerate(rows):
        if row["op"] == "equal":
            continue
        for j in range(max(0, i - CONTEXT_LINES), min(len(rows), i + CONTEXT_LINES + 1)):
            keep[j] = True
    out: list[dict] = []
    i = 0
    while i < len(rows):
        if keep[i]:
            out.append(rows[i])
            i += 1
            continue
        j = i
        while j < len(rows) and not keep[j]:
            j += 1
        if j - i < MIN_ELIDED_RUN:
            out += rows[i:j]
        else:
            out.append({"op": "skip", "text": "", "count": j - i})
        i = j
    return out


def _text(row: dict) -> str:
    value = row.get("text", "")
    return value if isinstance(value, str) else ""


def _row(key: str, before: dict | None, after: dict | None) -> dict:
    """One comparison row: the two sides, what happened, and the lines when
    that answer is "the text moved".

    `status` covers more than the text. A section the packer dropped this turn
    and kept last turn is a change a reader is hunting for, and its text is
    identical -- reporting that as `unchanged` because the lines match would
    hide the single most consequential thing the packer does. So `diff` being
    empty on a `changed` row is a real state, and it means the difference is in
    the flags rather than the words.

    An added or removed section carries its WHOLE text, as inserts or deletes.
    It costs the payload a section, and the alternative is worse: the reader is
    looking at one panel, so the side that no longer exists is nowhere else on
    screen. "Section X was dropped from the prompt" without saying what X said
    is the question restated, not answered.
    """
    shown = (after if after is not None else before) or {}
    row: dict = {"id": str(shown.get("id") or key.rsplit("#", 1)[0]),
                 "label": str(shown.get("label", "")),
                 "base": _side(before) if before is not None else None,
                 "head": _side(after) if after is not None else None,
                 "diff": []}
    if before is not None and after is not None:
        moved = _text(before) != _text(after)
        row["status"] = "changed" if moved or row["base"] != row["head"] else "unchanged"
        if not moved:
            # Identical words: emitting them as one long run of `equal` rows
            # would be a diff that says nothing, at the size of the section.
            return row
    else:
        row["status"] = "added" if before is None else "removed"
    row["diff"] = _elide(changes.line_diff(_text(before or {}), _text(after or {})))
    return row


def compare_breakdowns(base: dict, head: dict) -> dict:
    """How `head`'s composition differs from `base`'s.

    Both are `assemble._breakdown` payloads -- a live one from
    `context_breakdown`, a frozen one from `prompt_log.read_entry`, in any
    combination. Direction is the caller's to choose and is not second-guessed
    here: `base` is the "before" side of every row, whichever turn it came from.

    The section list is the two orders MERGED, not either one of them: rows are
    paired by key through difflib, so a section only `head` has lands where it
    sits in `head` and one only `base` has lands where it sat in `base`. Reading
    the result top to bottom is reading the new prompt with the old prompt's
    removals still in place -- which is what a diff is, and what neither order
    on its own can show.

    Nothing is summarised: no token delta and no "n changed" count. Both would
    be derived from what is already in the answer, and a figure a caller can
    recompute is a figure that can disagree with the rows beside it. The delta
    would be worse than redundant -- `_breakdown` is explicit that
    `total_tokens` is not the sum of its rows, so one computed here would
    contradict the two totals the caller already holds.
    """
    left_rows, right_rows = _rows(base.get("sections")), _rows(head.get("sections"))
    field = _key_field(left_rows + right_rows)
    left, right = _keyed(left_rows, field), _keyed(right_rows, field)
    keys_l, keys_r = [k for k, _ in left], [k for k, _ in right]
    rows: list[dict] = []
    # `autojunk=False`, and safe here in a way it is NOT in `line_diff`: this
    # sequence is one element per SECTION, so a customised layout crossing 200
    # is reachable but thousands are not, and the quadratic term that made the
    # per-line call need a bound has nothing to bite on. What it buys is that a
    # prompt is matched the same way either side of that threshold, which is not
    # one anyone would think to look for.
    matcher = difflib.SequenceMatcher(None, keys_l, keys_r, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            # `strict`: difflib's `equal` spans are equal-length by definition,
            # so a mismatch is a broken assumption rather than a short pair to
            # be silently truncated past.
            rows += [_row(keys_l[i], left[i][1], right[j][1])
                     for i, j in zip(range(i1, i2), range(j1, j2), strict=True)]
        else:
            # `replace` as well as `delete`/`insert`: a key that changed is a
            # different section, not a renamed one, so it is reported as the
            # removal and the addition it is. Removals first, so the row a
            # section was replaced BY reads after the one it replaced.
            rows += [_row(keys_l[i], left[i][1], None) for i in range(i1, i2)]
            rows += [_row(keys_r[j], None, right[j][1]) for j in range(j1, j2)]
    return {"sections": rows}
