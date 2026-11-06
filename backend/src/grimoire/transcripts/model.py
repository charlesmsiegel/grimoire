"""A chat's transcript: posts, their speakers and their variants (Sprint 036). Pure, frozen values.

A transcript is stored as numbered, create-only **pages** (``TranscriptRef``), each holding posts in
order. A post keeps every variant the source held (SillyTavern's swipes, an edited text) and which one
was shown (``active``); an unchosen variant is history, never canon. A post records who wrote it as the
source says (``speaker``): a role, the chat's actor when one was explicitly mapped, and the source's own
display label verbatim. It never records who heard it: ``audience`` is ``unknown`` in this schema,
because a transcript line is evidence that someone *wrote* it, not that anyone present observed it.

Nothing here reads files, clocks or randomness; nothing here creates events, beliefs or evidence.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.core.identity import InstanceRef, TranscriptRef
from grimoire.core.json_value import JsonObject, JsonValueError, freeze_object

TRANSCRIPT_SCHEMA = "grimoire.transcript/0"
#: who wrote a post, as the source distinguishes it. ``assistant`` is a generated or narrated post whose
#: source does not say which (V0.2.1); ``system`` is a source's own notice (a hidden note, a dice roll).
ROLES = frozenset({"user", "character", "narrator", "system", "assistant"})
#: the only audience this schema can state: nobody's observation is inferred from a transcript line
AUDIENCE_UNKNOWN = "unknown"
MAX_POSTS_PER_PAGE = 1000
MAX_VARIANTS = 1000
MAX_LABEL = 1000
MAX_DEPTH = 64
_POST_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


class TranscriptError(ValueError):
    """A transcript value this model refuses; ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str = "invalid_field") -> None:
        super().__init__(message)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason))


def _too_deep(value: object, limit: int) -> bool:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, Mapping):
            children = list(current.values())
        elif isinstance(current, (list, tuple)):
            children = list(current)
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def opaque(value: object, label: str) -> JsonObject:
    """Authored or source data kept verbatim and never interpreted."""
    if not isinstance(value, Mapping):
        raise TranscriptError(f"{label} must be a JSON object")
    if _too_deep(value, MAX_DEPTH):
        raise TranscriptError(f"{label} is nested more than {MAX_DEPTH} levels deep")
    try:
        return freeze_object(value)
    except JsonValueError as error:
        raise TranscriptError(f"{label}: {error}") from None


def _text(value: object, label: str) -> str:
    if type(value) is not str:
        raise TranscriptError(f"{label} must be text")
    return value


@dataclass(frozen=True, slots=True)
class Speaker:
    """Who wrote a post: ``role``, the chat actor it was mapped to (``None`` when unmapped or unknown)
    and the source's display ``label`` verbatim (``None`` when the source gave none). A label is display
    text: two speakers with one label are never assumed to be one individual."""

    role: str
    actor: InstanceRef | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise TranscriptError(f"speaker role must be one of {sorted(ROLES)}: {self.role!r}")
        if self.actor is not None and not isinstance(self.actor, InstanceRef):
            raise TranscriptError("a speaker's actor must be an InstanceRef")
        if self.label is not None and (type(self.label) is not str or len(self.label) > MAX_LABEL):
            raise TranscriptError(f"a speaker label must be text of at most {MAX_LABEL} characters")


@dataclass(frozen=True, slots=True)
class Variant:
    """One text a post held: the exact characters, the source's own timestamp text (``at``, verbatim,
    ``None`` if absent), where in the source it came from (``source``) and source fields kept verbatim."""

    text: str
    at: str | None = None
    source: JsonObject = field(default_factory=JsonObject)
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        _text(self.text, "variant text")
        if self.at is not None:
            _text(self.at, "variant at")
        object.__setattr__(self, "source", opaque(self.source, "variant source"))
        object.__setattr__(self, "extras", opaque(self.extras, "variant extras"))


@dataclass(frozen=True, slots=True)
class Post:
    """One post: its id (unique in its chat), speaker, every variant and the index of the one shown.

    ``active`` is ``None`` only when the source chose none. ``hidden`` is the source's own flag that the
    post was kept out of the conversation (SillyTavern's hidden messages). ``source`` is provenance (a
    file digest and line, a V0.2.1 post id and content hash); ``extras`` keeps source fields this
    version does not interpret, verbatim."""

    post_id: str
    speaker: Speaker
    variants: tuple[Variant, ...]
    active: int | None
    hidden: bool = False
    audience: str = AUDIENCE_UNKNOWN
    source: JsonObject = field(default_factory=JsonObject)
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        if type(self.post_id) is not str or not _POST_ID.fullmatch(self.post_id):
            raise TranscriptError(f"post_id must match {_POST_ID.pattern}: {self.post_id!r}")
        if not isinstance(self.speaker, Speaker):
            raise TranscriptError("speaker must be a Speaker")
        variants = tuple(self.variants)
        if not variants or len(variants) > MAX_VARIANTS or not all(isinstance(v, Variant) for v in variants):
            raise TranscriptError(f"a post needs 1 to {MAX_VARIANTS} variants")
        object.__setattr__(self, "variants", variants)
        if self.active is not None and (type(self.active) is not int or not 0 <= self.active < len(variants)):
            raise TranscriptError(f"active must index a variant: {self.active!r}")
        if type(self.hidden) is not bool:
            raise TranscriptError("hidden must be true or false")
        if self.audience != AUDIENCE_UNKNOWN:
            raise TranscriptError(f"this schema states no audience but {AUDIENCE_UNKNOWN!r}", "audience")
        object.__setattr__(self, "source", opaque(self.source, "post source"))
        object.__setattr__(self, "extras", opaque(self.extras, "post extras"))

    @property
    def text(self) -> str | None:
        """The text the source showed, or ``None`` if it chose no variant."""
        return None if self.active is None else self.variants[self.active].text

    @property
    def unchosen(self) -> tuple[Variant, ...]:
        """Every variant the source held but did not show: history, never canon."""
        return tuple(v for i, v in enumerate(self.variants) if i != self.active)


@dataclass(frozen=True, slots=True)
class TranscriptPage:
    """One stored page: posts in order. ``origin`` is the provenance shared by the page's posts (which
    import wrote it); ``header`` is the source's own chat-level data, verbatim, on the first page only."""

    ref: TranscriptRef
    posts: tuple[Post, ...]
    origin: JsonObject = field(default_factory=JsonObject)
    header: JsonObject | None = None
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        if not isinstance(self.ref, TranscriptRef):
            raise TranscriptError("a page's ref must be a TranscriptRef")
        posts = tuple(self.posts)
        if not posts or len(posts) > MAX_POSTS_PER_PAGE or not all(isinstance(p, Post) for p in posts):
            raise TranscriptError(f"a page holds 1 to {MAX_POSTS_PER_PAGE} posts")
        ids = [p.post_id for p in posts]
        if len(set(ids)) != len(ids):
            raise TranscriptError("a post id appears twice on one page", "duplicate_post")
        object.__setattr__(self, "posts", posts)
        object.__setattr__(self, "origin", opaque(self.origin, "page origin"))
        if self.header is not None:
            if self.ref.seq != 1:
                raise TranscriptError("only the first page carries the source header", "header_not_first")
            object.__setattr__(self, "header", opaque(self.header, "page header"))
        object.__setattr__(self, "extras", opaque(self.extras, "page extras"))


def paginate(posts: tuple[Post, ...] | list[Post], measure: Callable[[Post], int], budget: int,
             max_posts: int = MAX_POSTS_PER_PAGE, *, first_budget: int | None = None) -> list[tuple[Post, ...]]:
    """Posts split, in order, into page-sized groups: at most ``max_posts`` each, and at most ``budget``
    by ``measure`` (a post's stored size; ``first_budget`` for page 1, which also holds the header). A
    page always takes its first post, and one post larger than ``budget`` is refused (``post_too_large``),
    so page 1 holds at most ``budget`` beyond its header."""
    groups: list[list[Post]] = []
    size = 0
    for post in posts:
        measured = measure(post)
        if measured > budget:
            raise TranscriptError(f"{post.post_id} is too large for one transcript page", "post_too_large")
        limit = first_budget if len(groups) == 1 and first_budget is not None else budget
        if not groups or size + measured > limit or len(groups[-1]) >= max_posts:
            groups.append([])
            size = 0
        groups[-1].append(post)
        size += measured
    return [tuple(group) for group in groups]
