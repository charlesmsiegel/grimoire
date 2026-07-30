"""Token counting for the context breakdown: tiktoken when it works, a
characters/4 heuristic when anything goes wrong.

tiktoken is a Rust wheel and lives in the `desktop` extra, so Android runs on
the heuristic; the broad `except` also covers an installed build that fails to
load or encode.
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=1)
def _encoder():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        return len(_encoder().encode(text))
    except Exception:  # noqa: BLE001 - token counting must never fail a turn; heuristic instead
        return len(text) // 4
