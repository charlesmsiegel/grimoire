"""Turn boundaries: the `turn_sizes` frontmatter field and the block counting
every reader and repairer of it has to agree on.

`turn_sizes` records how many model blocks each generation contributed, so
reroll and drift measurement can find the last one. Nothing here writes a
file — `_set_turn_sizes` stages the field into the caller's `meta` so the
transcript and its boundaries always go out in a single write.
"""

from __future__ import annotations

from ..frontmatter import parse_frontmatter_head
from ..paths import safe_id
from . import paths, serialize


def _parse_turn_sizes(raw: str) -> list[int]:
    """Parse the boundary list STRICTLY: every token must be a positive whole
    number, or the whole field is discarded.

    Dropping only the bad tokens invents boundaries. "2,garbled,1" would parse
    to [2, 1] and a zero would pass through as a real turn — and both are then
    treated as authoritative by measurement AND by reroll, which deletes
    sizes[-1] blocks. A field that cannot be trusted end to end means "no
    tracking": no metrics, and reroll takes the untracked path.
    """
    sizes: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            n = int(token)
        except ValueError:
            return []
        if n <= 0:
            return []
        sizes.append(n)
    return sizes


def get_turn_sizes(cid: str, sid: str) -> list[int]:
    """Block counts for each recorded model generation, oldest first.

    Describes the LAST sum(turn_sizes) model blocks — a tracked suffix, not the
    whole transcript. Blocks written before turn tracking existed form an
    untracked prefix that measurement ignores, which is what lets an upgraded
    scene start being measured once new generations land. Empty on a scene with
    no tracking yet; such a scene is simply not measured.
    """
    p = paths._scene_path(cid, sid)
    if not safe_id(sid) or not p.exists():
        return []
    return _parse_turn_sizes(parse_frontmatter_head(p).get("turn_sizes", ""))


def _set_turn_sizes(meta: dict, sizes: list[int]) -> None:
    """Stage the boundary list into `meta` for the caller's single write.

    Every mutation that touches both the transcript and its boundaries writes
    them TOGETHER: a crash between two writes leaves turn_sizes describing a
    transcript that no longer exists, and the next reroll then trusts sizes[-1]
    and deletes blocks belonging to an older generation. Irreversible.

    A scene with no tracking keeps none: writing an empty key would stamp
    turn_sizes onto every legacy scene it touches for no gain.
    """
    if sizes or "turn_sizes" in meta:
        meta["turn_sizes"] = ",".join(str(n) for n in sizes)


def _model_blocks(messages: list[dict]) -> list[int]:
    """Indices of the messages that count as MODEL blocks — assistant-role and
    not synthetically authored (dice rolls, scene transitions). turn_sizes is
    expressed in these, so everything that reads or repairs it counts the same
    way."""
    return [i for i, m in enumerate(messages)
            if m["role"] == "assistant" and m.get("speaker") not in serialize.SYNTHETIC_SPEAKERS]


class TurnSizesDesynced(Exception):
    """The recorded boundaries can no longer describe the stored transcript.

    Raised INSTEAD of deleting anything. turn_sizes is what tells reroll how
    many blocks the last generation had; a list that doesn't fit the transcript
    would have it count back through blocks belonging to earlier generations,
    destroying transcript nobody asked to reroll. There is no safe guess to
    fall back on — the untracked path eats every consecutive generation — so
    the operation refuses and the scene is left exactly as it was.
    """


def _trailing_model_run(messages: list[dict]) -> int:
    """How many messages at the tail are contiguous model blocks."""
    n = 0
    while n < len(messages) and messages[-1 - n]["role"] == "assistant" \
            and messages[-1 - n].get("speaker") not in serialize.SYNTHETIC_SPEAKERS:
        n += 1
    return n


def _tracked_suffix_fits(messages: list[dict], sizes: list[int]) -> bool:
    """Whether `sizes` can still describe this transcript's trailing blocks.

    Two ways it can't: the list claims more model blocks than exist at all, or
    the last recorded generation isn't sitting contiguously at the tail (a user
    line or a dice roll has been spliced into the middle of what it claims).
    Either way the list is describing some other transcript.
    """
    return (sum(sizes) <= len(_model_blocks(messages))
            and sizes[-1] <= _trailing_model_run(messages))


def _reconciled_turn_sizes(sizes: list[int], before: list[int], after: list[int],
                           index: int) -> list[int]:
    """turn_sizes describing the transcript's real trailing model blocks after
    an edit changed how the body PARSES.

    Stored content is re-split at read time on blank-line-preceded
    `**Speaker:**` markers, so editing a message can add model blocks (a marker
    typed into the text) or remove one (a legacy `**Grimoire (Name):**` label
    is re-saved as plain `**Name:**`, which parses as a user line when Name is
    a seated player). turn_sizes knows nothing of either: left alone, reroll
    consumes sizes[-1] blocks of a reply that is no longer that long, and drift
    segmentation takes the last sum(sizes) blocks from the wrong place.

    The invariant restored: sum(turn_sizes) is the number of model blocks the
    tracked turns actually cover, and those turns are the transcript's true
    trailing blocks. Dropping older entries is acceptable — they merely stop
    being measured. Keeping an entry that describes the wrong blocks is not.
    """
    if not sizes:
        return sizes                             # untracked scene: nothing to keep in step
    delta = len(after) - len(before)
    if delta == 0:
        return sizes                             # the ordinary edit: content only
    sizes = list(sizes)
    while sizes and sum(sizes) > len(before):
        sizes.pop(0)                             # hand-edited: claims blocks that never existed
    prefix = len(before) - sum(sizes)             # untracked pre-tracking blocks
    starts, at = [], prefix                       # each tracked turn's first block position
    for n in sizes:
        starts.append(at)
        at += n

    pos = before.index(index) if index in before else None
    if pos is None:
        # Not a model block itself, but its text can still splice one in or out.
        # Before the tracked suffix that changes nothing (the suffix is anchored
        # at the end); inside it, keep only the turns lying wholly after the edit.
        blocks_before = sum(1 for i in before if i < index)
        if blocks_before > prefix:
            sizes = [n for n, s in zip(sizes, starts) if s >= blocks_before]
    elif pos >= prefix:
        turn = max(i for i, s in enumerate(starts) if s <= pos)
        sizes[turn] += delta
        if sizes[turn] <= 0:
            sizes = sizes[turn + 1:]              # conservative: never misattribute
    # pos < prefix: the edit landed in the untracked legacy prefix and the
    # tracked suffix still covers exactly the blocks it always did.
    while sizes and sum(sizes) > len(after):
        sizes.pop(0)
    return sizes
