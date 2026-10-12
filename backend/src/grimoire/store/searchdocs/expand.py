"""Transcript expansion (roadmap 08-C3b): from a selected scene to bounded,
keyed excerpts of its transcript, in the regex phase the caller names.

Retrieval finds a scene by its search document, which holds no transcript text
(`searchdocs.scene`); the evidence it then shows is read from the transcript
here. Four properties are the point, and each has a reason:

- **The caller names the phase, and there is no default.** `prompt` for
  anything that reaches a model, `display` only for a surface that shows a
  reader what retrieval found. `test_regex_prompt_guard.py` holds every
  `expand(...)` call in a function that renders a prompt to the constant
  `phase="prompt"`, because nothing else can see such a reader: it never reads
  a scene itself.
- **The whole transcript is viewed before anything is matched or cut**, so a
  depth-ranged rule counts depth exactly as the context builder does, and a term
  a prompt rule strips cannot select a post whose sent text no longer holds it.
- **Bounded by construction**: at most `max_excerpts` excerpts, `max_posts`
  posts and `max_bytes` bytes of rendered text (speaker labels included),
  whatever the transcript's length. The read is a whole-file parse; what is
  bounded is what reaches the caller.
- **Keyed by post, not by index**: `r-<response_id>`, else `p-<post_id>`, else
  "" -- the tracker's precedence in the spelling 11 uses -- so a cut that
  renumbers the transcript leaves every key where it was.

Hidden posts and director notes are never returned, in either phase. Live and
never cached (the answer depends on the terms and the rule files), read-only,
no lock, and no log line carrying content. Rendering a sub-range of an excerpt
is the consumer's, from the per-post `content` it already holds: a free-standing
renderer over arbitrary posts would be a `transcript_text` call the regex guard
cannot tie to a view.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .. import chronicle, export
from ..appearances import cast as appearances_cast
from ..campaigns import paths as campaigns_paths
from ..regex import view as regex_view
from ..scenes import identity as scenes_identity
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize

PHASES = ("prompt", "display")
FALLBACKS = ("none", "tail")
RADIUS = 1                  # neighbouring posts on each side of a hit
MAX_EXCERPTS = 3
MAX_POSTS = 8               # posts across all excerpts
MAX_BYTES = 4000            # speaker labels plus contents, UTF-8
MESSAGE_BYTES = 1500        # one post (semsearch.PASSAGE_BYTES)
MIN_POST_BYTES = 120        # a hit cut shorter than this is not worth sending
MAX_TERMS = 16
#: Occurrences of one term counted toward a post's score, so a long post that
#: repeats a word cannot outrank one that holds more of the terms.
OCCURRENCE_CAP = 5

#: What `snippets/transcript.j2` puts around one post: `**`, `:** ` and the
#: blank line that joins two. Counted for every post, the first included, so
#: the budget is an upper bound on the rendered text rather than an estimate.
_FRAME_BYTES = len(b"**:** ") + len(b"\n\n")
_ROLE_LABELS = {"user": "You", "assistant": "Grimoire"}
_ELLIPSIS = "…"
_ELLIPSIS_BYTES = len(_ELLIPSIS.encode())


@dataclass(frozen=True)
class ExcerptPost:
    index: int          # absolute transcript index at read time
    key: str            # "r-<response_id>", else "p-<post_id>", else ""
    part: int | None    # response_part, for a reply split across posts
    role: str
    speaker: str        # as rendered (transition lines: "")
    content: str        # in the named phase
    clipped: bool


@dataclass(frozen=True)
class Excerpt:
    start: int                        # index of the first post
    end: int                          # exclusive
    posts: tuple[ExcerptPost, ...]
    matched: tuple[str, ...]          # terms this excerpt contains
    text: str                         # the posts rendered as history lines


@dataclass(frozen=True)
class Expansion:
    sid: str
    identity: str
    phase: str
    mode: str                         # "matched" | "whole" | "tail" | "empty"
    budget_exhausted: bool            # matches existed but none fit
    total: int
    eligible: int
    excerpts: tuple[Excerpt, ...]     # transcript order
    bytes: int


# --- terms and scoring --------------------------------------------------------

def normalize_terms(terms: Sequence[str]) -> list[str]:
    """Case-folded, whitespace-collapsed, de-duplicated terms of at least two
    characters, at most `MAX_TERMS`, in the order given -- `search.query_terms`'s
    normalization, over terms a caller has already split."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        if not isinstance(raw, str):
            continue
        term = " ".join(raw.split()).casefold()
        if len(term) >= 2 and term not in seen:
            seen.add(term)
            out.append(term)
            if len(out) == MAX_TERMS:
                break
    return out


def _haystack(post: dict) -> str:
    return f"{post['speaker']}\n{post['content']}".casefold()


def _score(post: dict, terms: list[str]) -> tuple[int, int]:
    """(distinct terms present, occurrences capped per term)."""
    hay = _haystack(post)
    distinct = occurrences = 0
    for term in terms:
        n = hay.count(term)
        if n:
            distinct += 1
            occurrences += min(n, OCCURRENCE_CAP)
    return distinct, occurrences


# --- byte-bounded cuts --------------------------------------------------------

def _nbytes(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


def _fit_end(text: str, start: int, budget: int) -> int:
    """The largest `end` such that `text[start:end]` fits `budget` bytes, ending
    at a whitespace boundary unless the window holds a single token longer than
    itself, which is cut at a character boundary."""
    used, end = 0, start
    while end < len(text):
        step = _nbytes(text[end])
        if used + step > budget:
            break
        used += step
        end += 1
    if end < len(text) and not text[end].isspace():
        space = max((i for i in range(end - 1, start - 1, -1) if text[i].isspace()), default=-1)
        if space > start:
            end = space
    return end


def _fit_start(text: str, end: int, budget: int) -> int:
    """The mirror of `_fit_end`: the smallest `start` such that `text[start:end]`
    fits `budget` bytes, starting at a whitespace boundary where one exists."""
    used, start = 0, end
    while start > 0:
        step = _nbytes(text[start - 1])
        if used + step > budget:
            break
        used += step
        start -= 1
    if start > 0 and not text[start - 1].isspace():
        space = next((i for i in range(start, end) if text[i].isspace()), -1)
        if 0 <= space < end - 1:
            start = space + 1
    return start


def _wrap(text: str, start: int, end: int) -> str:
    body = text[start:end].strip()
    return (_ELLIPSIS if start > 0 else "") + body + (_ELLIPSIS if end < len(text) else "")


def _folded(text: str) -> tuple[str, list[int]]:
    """`text.casefold()`, and for each character of it the index in `text` of
    the character it folded from. Folding can change the length ("ß" folds to
    "ss"), so an offset into the folded text is not an offset into the
    original; this map is what makes the two agree."""
    out: list[str] = []
    starts: list[int] = []
    for i, ch in enumerate(text):
        folded = ch.casefold()
        out.append(folded)
        starts.extend([i] * len(folded))
    return "".join(out), starts


def _rarest(text: str, terms: Sequence[str]) -> int | None:
    """Where, in `text`, the rarest term that occurs in it first does, ties to
    the earliest (`search.snippet`'s rule). Matched with the same `casefold`
    the score used, so a post scored as a hit is always framed on its term;
    `None` when no term occurs."""
    folded, starts = _folded(text)
    hits = [(folded.count(term), folded.find(term)) for term in terms if term in folded]
    return starts[min(hits)[1]] if hits else None


def _cut(text: str, budget: int, *, keep: str, terms: Sequence[str] = ()) -> tuple[str, bool]:
    """`text` within `budget` bytes, and whether it was cut. `keep` is `head`
    (the start survives), `tail` (the end survives) or `frame` (a window on the
    rarest term that occurs in it; the head when none does). Every cut ends at
    a whitespace boundary; an elided end is marked."""
    if _nbytes(text) <= budget:
        return text, False
    room = budget - 2 * _ELLIPSIS_BYTES
    if room <= 0:
        return "", True
    if keep == "tail":
        return _wrap(text, _fit_start(text, len(text), room), len(text)), True
    first = _rarest(text, terms) if keep == "frame" else None
    # A quarter of the window before the term, so it reads in context.
    anchor = 0 if first is None else _fit_start(text, first, room // 4)
    end = _fit_end(text, anchor, room)
    if end <= anchor:      # nothing fits after the anchor: keep the head instead
        anchor, end = 0, _fit_end(text, 0, room)
    return _wrap(text, anchor, end), True


# --- the transcript as eligible posts -------------------------------------------

def _key(m: dict) -> str:
    if m.get("response_id"):
        return f"r-{m['response_id']}"
    if m.get("post_id"):
        return f"p-{m['post_id']}"
    return ""


def _part(m: dict) -> int | None:
    try:
        return int(m["response_part"]) if m.get("response_part") not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _speaker(m: dict, player_label: str) -> str:
    """The speaker as `transcript_text` renders it: a transition line has none,
    an unstamped user post takes the seated player's name."""
    if m.get("speaker") == scenes_serialize.TRANSITION_SPEAKER:
        return ""
    if m.get("role") == "user" and not m.get("speaker"):
        return player_label
    return str(m.get("speaker") or "")


def _label(post: dict) -> str:
    return post["speaker"] or _ROLE_LABELS.get(post["role"], post["role"])


def _cost(post: dict, content: str) -> int:
    return _FRAME_BYTES + _nbytes(_label(post)) + _nbytes(content)


# --- planning windows -----------------------------------------------------------

@dataclass
class _Window:
    lo: int                     # eligible positions, inclusive
    hi: int
    contents: dict[int, tuple[str, bool]]


class _FloorUnmetError(Exception):
    """A hit that cannot get `MIN_POST_BYTES` ends the run."""


@dataclass
class _Plan:
    posts: list[dict]
    terms: list[str]
    max_excerpts: int
    max_posts: int
    remaining: int
    windows: list[_Window]

    def placed(self) -> set[int]:
        return {pos for w in self.windows for pos in w.contents}

    def contents_for(self, hit: int, new: list[int]) -> tuple[dict[int, tuple[str, bool]], int]:
        """The hit framed on its rarest term within what is left, and each
        neighbour cut from the end nearer the hit; with their cost."""
        room = min(MESSAGE_BYTES, self.remaining - _cost(self.posts[hit], ""))
        text = self.posts[hit]["content"]
        if _nbytes(text) > room and room < MIN_POST_BYTES:
            raise _FloorUnmetError
        contents = {hit: _cut(text, max(room, 0), keep="frame", terms=self.terms)}
        for p in new:
            if p != hit:
                contents[p] = _cut(self.posts[p]["content"], MESSAGE_BYTES,
                                   keep="tail" if p < hit else "head")
        return contents, sum(_cost(self.posts[p], c[0]) for p, c in contents.items())

    def place(self, hit: int, lo: int, hi: int) -> bool:
        """Add the window `lo..hi` around `hit`, merging any it touches, if it
        fits every limit; whether it did."""
        taken = self.placed()
        new = [p for p in range(lo, hi + 1) if p not in taken]
        touching = [w for w in self.windows if w.lo - 1 <= hi and lo <= w.hi + 1]
        if len(taken) + len(new) > self.max_posts \
                or len(self.windows) - len(touching) + 1 > self.max_excerpts:
            return False
        contents, spent = self.contents_for(hit, new)
        if spent > self.remaining:
            return False
        self.remaining -= spent
        for w in touching:
            contents.update(w.contents)
            self.windows.remove(w)
        self.windows.append(_Window(min([lo] + [w.lo for w in touching]),
                                    max([hi] + [w.hi for w in touching]), contents))
        return True


def _plan_matched(posts: list[dict], ranked: list[int], terms: list[str], *,
                  radius: int, max_excerpts: int, max_posts: int,
                  max_bytes: int) -> list[_Window]:
    """Hits in score order each claim `radius` eligible posts either side;
    overlapping or adjacent windows merge. A window that does not fit is shrunk
    to its hit alone, and a hit that cannot get `MIN_POST_BYTES` stops the run."""
    plan = _Plan(posts, terms, max_excerpts, max_posts, max_bytes, [])
    try:
        for hit in ranked:
            if hit in plan.placed():
                continue
            lo, hi = max(0, hit - radius), min(len(posts) - 1, hit + radius)
            if not plan.place(hit, lo, hi) and not plan.place(hit, hit, hit) \
                    and (len(plan.placed()) >= max_posts or len(plan.windows) >= max_excerpts):
                break      # those limits only tighten: nothing later fits either
    except _FloorUnmetError:
        pass
    return plan.windows


def _plan_tail(posts: list[dict], *, radius: int, max_posts: int,
               max_bytes: int) -> list[_Window]:
    """The last `2 * radius + 1` eligible posts, newest first into the budget."""
    want = min(2 * radius + 1, max_posts, len(posts))
    contents: dict[int, tuple[str, bool]] = {}
    remaining = max_bytes
    for p in range(len(posts) - 1, len(posts) - 1 - want, -1):
        room = min(MESSAGE_BYTES, remaining - _cost(posts[p], ""))
        text = posts[p]["content"]
        if _nbytes(text) > room and room < MIN_POST_BYTES:
            break
        cut = _cut(text, max(room, 0), keep="head")
        contents[p] = cut
        remaining -= _cost(posts[p], cut[0])
    if not contents:
        return []
    return [_Window(min(contents), max(contents), contents)]


def _whole(posts: list[dict], *, max_posts: int, max_bytes: int) -> list[_Window]:
    if not posts or len(posts) > max_posts:
        return []
    if sum(_cost(p, p["content"]) for p in posts) > max_bytes:
        return []
    return [_Window(0, len(posts) - 1, {i: (p["content"], False) for i, p in enumerate(posts)})]


# --- the call -------------------------------------------------------------------

def _current_sid(cid: str, sid: str, identity: str | None) -> str | None:
    if identity is None:
        return sid
    if scenes_identity.scene_identity(cid, sid) == identity:
        return sid
    return scenes_identity.find_by_identity(cid, identity)


def _read(cid: str, sid: str, identity: str | None) -> tuple[str, dict, str, str] | None:
    """(current sid, scene, player label, identity after the read), or None for
    a scene that is gone, no longer carries `identity`, or will not read."""
    try:
        current = _current_sid(cid, sid, identity)
        if current is None:
            return None
        scene = scenes_read.read_scene(cid, current)
        player_label = appearances_cast.player_label(cid, current)
        after = scenes_identity.scene_identity(cid, current)
    except (scenes_paths.SceneNotFound, campaigns_paths.CampaignNotFound, OSError):
        return None
    if identity is not None and after != identity:
        return None
    return current, scene, player_label, after or ""


def _eligible(shown: list[dict], phase: str, player_label: str) -> list[dict]:
    """The posts that may be returned, in transcript order: not hidden, not a
    director note; transitions and rolls kept. Images are alt text in the
    prompt phase."""
    posts = []
    for i, m in enumerate(shown):
        if scenes_serialize.is_excluded(m) or scenes_serialize.is_director_note(m):
            continue
        raw = m.get("content")
        content = raw if isinstance(raw, str) else ""
        if phase == "prompt":
            content = export.drop_images(content)
        posts.append({"index": i, "role": str(m.get("role") or ""),
                      "speaker": _speaker(m, player_label), "content": content})
    return posts


def _windows(posts: list[dict], wanted: list[str], *, radius: int, max_excerpts: int,
             max_posts: int, max_bytes: int, fallback: str,
             allow_whole: bool) -> tuple[list[_Window], str, bool]:
    """(windows, mode, budget_exhausted)."""
    if max_excerpts <= 0 or max_posts <= 0 or max_bytes <= 0:
        return [], "empty", False
    if allow_whole:
        whole = _whole(posts, max_posts=max_posts, max_bytes=max_bytes)
        if whole:
            return whole, "whole", False
    scored = [(_score(p, wanted), pos) for pos, p in enumerate(posts)] if wanted else []
    ranked = [pos for (distinct, _occ), pos in
              sorted(scored, key=lambda s: (-s[0][0], -s[0][1], s[1])) if distinct]
    if ranked:
        found = _plan_matched(posts, ranked, wanted, radius=radius, max_excerpts=max_excerpts,
                              max_posts=max_posts, max_bytes=max_bytes)
        return (found, "matched", False) if found else ([], "empty", True)
    if fallback == "tail":
        tail = _plan_tail(posts, radius=radius, max_posts=max_posts, max_bytes=max_bytes)
        return tail, ("tail" if tail else "empty"), False
    return [], "empty", False


def expand(cid: str, sid: str, *, phase: str, terms: Sequence[str],
           identity: str | None = None, radius: int = RADIUS,
           max_excerpts: int = MAX_EXCERPTS, max_posts: int = MAX_POSTS,
           max_bytes: int = MAX_BYTES, fallback: str = "none",
           allow_whole: bool = False) -> Expansion | None:
    """Bounded excerpts of scene `sid` around the posts that match `terms`.

    `identity`, when given, is checked before the read (a renamed scene is
    followed to its current id) and again after it; a scene that is gone or no
    longer carries it answers `None`, as does any failure to read. `fallback`
    `"tail"` returns the last posts when nothing matched; `allow_whole` returns
    the whole eligible transcript when every post of it fits the budgets.

    Raises `ValueError` for a `phase` other than `prompt` or `display` (the
    store phase included) and a `fallback` other than `none` or `tail`.
    """
    if phase not in PHASES:
        raise ValueError(f"expand: phase must be one of {PHASES}, not {phase!r}")
    if fallback not in FALLBACKS:
        raise ValueError(f"expand: fallback must be one of {FALLBACKS}, not {fallback!r}")
    read = _read(cid, sid, identity)
    if read is None:
        return None
    sid, scene, player_label, after = read
    # The whole transcript, viewed once, so depth counts as the context builder
    # counts it. One constant per branch rather than `phase=phase`: the list
    # each binds is what every excerpt below is rendered from.
    if phase == "prompt":
        shown = regex_view.view(scene["messages"], cid=cid, phase="prompt")
    else:
        shown = regex_view.view(scene["messages"], cid=cid, phase="display")
    posts = _eligible(shown, phase, player_label)
    wanted = normalize_terms(terms)
    windows, mode, exhausted = _windows(
        posts, wanted, radius=max(radius, 0), max_excerpts=max_excerpts,
        max_posts=max_posts, max_bytes=max_bytes, fallback=fallback, allow_whole=allow_whole)
    excerpts = []
    for w in sorted(windows, key=lambda w: w.lo):
        chosen = [posts[pos] | {"content": w.contents[pos][0], "clipped": w.contents[pos][1]}
                  for pos in sorted(w.contents)]
        for post in chosen:
            # `shown` holds this call's own copies (`view` copies), so each post
            # takes the text the caller is handed: phased, images reduced in the
            # prompt phase, and cut to the budget.
            shown[post["index"]]["content"] = post["content"]
        start, end = chosen[0]["index"], chosen[-1]["index"] + 1
        # Every eligible post between `start` and `end` is in the window (a
        # window is contiguous over eligible posts), and `transcript_text` drops
        # the hidden posts and director notes that sit between them.
        text = chronicle.transcript_text(shown[start:end], player_label)
        hay = "\n".join(_haystack(post) for post in chosen)
        excerpts.append(Excerpt(
            start=start, end=end, text=text,
            matched=tuple(t for t in wanted if t in hay),
            posts=tuple(ExcerptPost(index=post["index"], key=_key(shown[post["index"]]),
                                    part=_part(shown[post["index"]]), role=post["role"],
                                    speaker=post["speaker"], content=post["content"],
                                    clipped=post["clipped"]) for post in chosen)))
    return Expansion(sid=sid, identity=after, phase=phase, mode=mode,
                     budget_exhausted=exhausted, total=len(shown), eligible=len(posts),
                     excerpts=tuple(excerpts),
                     bytes=sum(_nbytes(e.text) for e in excerpts))
