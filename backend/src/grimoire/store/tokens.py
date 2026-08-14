"""Token counting: tiktoken when it works, a characters/4 heuristic when
anything goes wrong.

tiktoken is a Rust wheel and lives in the `desktop` extra, which Android does
not install (android/app/build.gradle.kts mirrors the *base* deps only), so
Android runs on the heuristic; the two broad `except` clauses also cover an
installed build that fails to import (below) or to encode (in count_tokens).

It lives at the top of `store/` rather than inside `store/context/`, where it
was written, because both sides of "what does this cost" need it and only one
of them can import the other. `context` reads entities (through `overlay`), so
an `entities` that imported `context.tokens` would close a cycle -- and the
per-record badge (#51) is exactly the measure the context breakdown already
reports, so the two must be the same function rather than two that agree for
now. A leaf that imports nothing but `statcache` is what lets both have it;
`context` re-exports `count_tokens` so its own callers are unchanged.
"""

from __future__ import annotations

import functools
from pathlib import Path

from . import statcache

try:
    import tiktoken
except Exception:  # noqa: BLE001 - an unimportable build must degrade, not break module import
    # `except Exception`, not `except ImportError`: this import used to sit
    # inside _encoder(), where count_tokens' broad except turned *any* failure
    # into the heuristic. Narrowing it here would let a broken-but-installed
    # build escape a module-level import and take the store facade with it.
    tiktoken = None


@functools.lru_cache(maxsize=1)
def _encoder():
    if tiktoken is None:
        return None
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception:  # noqa: BLE001 - token counting must never fail a turn; heuristic instead
        # Round UP, and never to zero. The heuristic is applied per string, and
        # the packer counts each history message separately -- with floor
        # division every message under four characters cost nothing at all, so
        # a scene of short alternating turns ("yes." / "go on.") summed to zero
        # and the packer saw no history to trim however small the budget. A
        # non-empty string is at least one token; overestimating slightly is
        # the safe direction for a ceiling.
        return -(-len(text) // 4)


def record_tokens(path: Path, body: str) -> int:
    """What one record's body costs when it reaches a prompt, memoized on the
    file's stat signature.

    The BODY only, stripped: frontmatter never enters the context, and
    `context.world_state._world_info` hands the assembler `body.strip()`, so
    that is the string measured here. What the badge therefore reports is a
    *per-activation* cost — a keyed entry pays it on the turns its keys hit,
    not on every turn.

    `body` is the text the caller has already read, so a list sweep costs one
    encode per changed file rather than a second read of every file. Passing it
    in rather than re-reading is safe in both interleavings: a write between
    the caller's read and this stat moves mtime to ~now, and `statcache.memo`
    refuses to cache anything inside its racy window, so a stale body can never
    be stored under the signature that replaced it.
    """
    return statcache.memo("record_tokens", statcache.signature(path),
                          lambda: count_tokens(body.strip()))
