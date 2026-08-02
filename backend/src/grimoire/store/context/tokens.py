"""Token counting for the context breakdown: tiktoken when it works, a
characters/4 heuristic when anything goes wrong.

tiktoken is a Rust wheel and lives in the `desktop` extra, which Android does
not install (android/app/build.gradle.kts mirrors the *base* deps only), so
Android runs on the heuristic; the two broad `except` clauses also cover an
installed build that fails to import (below) or to encode (in count_tokens).
"""

from __future__ import annotations

import functools

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
