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

import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import statcache

try:
    import tiktoken
except Exception:  # noqa: BLE001 - an unimportable build must degrade, not break module import
    # `except Exception`, not `except ImportError`: this import used to sit
    # inside _encoder(), where count_tokens' broad except turned *any* failure
    # into the heuristic. Narrowing it here would let a broken-but-installed
    # build escape a module-level import and take the store facade with it.
    tiktoken = None

_log = logging.getLogger(__name__)

#: The one encoding `count_tokens` loads. OpenAI's GPT-4-era tokenizer: there is
#: no portable way to load another model's, so every count in the app -- the
#: packer's budget, the inspector's rows, the per-record badge -- is this one.
ENCODING = "cl100k_base"

#: What `counting` names the characters/4 fallback.
HEURISTIC = "heuristic"

#: How long a failed encoder load is believed before it is tried again.
#: Ten minutes: long enough that a turn's assembly -- hundreds of counts --
#: pays for at most one attempt, short enough that a laptop which was offline
#: at its first turn gets exact counts back without a restart.
RETRY_AFTER_S = 600.0


class _Loader:
    """The encoder, loaded once -- and a failure to load it, remembered.

    `get_encoding` fetches its BPE file over the network the first time and
    caches it on disk, so the failure worth planning for is the offline first
    run. `functools.lru_cache`, which this replaced, caches a return value and
    never a raise: every `count_tokens` call re-attempted the download, and
    assembling one turn makes hundreds of them -- seconds added to every turn
    offline, and a connect timeout per call on a network that drops packets
    rather than refusing them. Now a failure is paid once, answered by the
    heuristic until `RETRY_AFTER_S` has passed, and then tried again.

    The lock makes "once" true under the threadpool as well: concurrent first
    counters wait for the one load rather than each starting their own.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._encoding: Any = None
        self._failed_at: float | None = None

    def get(self) -> Any:
        if self._encoding is not None or tiktoken is None:
            return self._encoding
        with self._lock:
            if self._encoding is not None:
                return self._encoding
            if (self._failed_at is not None
                    and time.monotonic() - self._failed_at < RETRY_AFTER_S):
                return None
            try:
                self._encoding = tiktoken.get_encoding(ENCODING)
            except Exception as exc:  # noqa: BLE001 - any failure means the heuristic, see count_tokens
                self._failed_at = time.monotonic()
                _log.warning("tiktoken could not load its encoding -- %s; counting "
                             "tokens by length for the next %d minutes",
                             exc, RETRY_AFTER_S // 60)
            return self._encoding

    def peek(self) -> Any:
        """The encoder if it has already loaded, else None -- WITHOUT loading.
        For describing counts that were already made: asking `get` there could
        start a download (or wait on another thread's) after the fact."""
        return self._encoding


_loader = _Loader()


def _loaded():
    """The encoder if one is loaded right now, never starting a load. The seam
    `counting` reads and a test patches."""
    return _loader.peek()


def _encoder():
    """The loaded encoder, or None -- which `count_tokens` answers with the
    heuristic. The seam a test patches (`context.tokens._encoder`) to force
    that path."""
    return _loader.get()


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


def _native_encoding(model: str) -> str:
    """The encoding tiktoken says `model` itself uses, or "" when that cannot
    be known -- which is every model not named as OpenAI's.

    Only a model spelled with the `openai/` prefix (OpenRouter's spelling) is
    looked up. A bare `gpt-4` is NOT taken at its word: an OpenAI-compatible
    endpoint is free to serve anything under that name, and several do by
    default (LocalAI's all-in-one images, gateways that alias `gpt-4` to
    whatever they route to). The cost is that a direct api.openai.com
    connection reads as an estimate too, which is the safe direction. Any other
    prefix names somebody else's model, and stripping it would let
    `someorg/gpt-4-tune` claim GPT-4's tokenizer through tiktoken's prefix
    table.

    Never raises, and never touches the network -- the lookup is tiktoken's own
    static table, not an encoding load.
    """
    if tiktoken is None or not model.startswith("openai/"):
        return ""
    try:
        return str(tiktoken.encoding_name_for_model(model.removeprefix("openai/")))
    except Exception:  # noqa: BLE001 - KeyError for an unknown model; anything else means the same
        return ""


def counting(model: str) -> dict:
    """How the token counts reported beside `model` were made, and whether
    the tokenizer was that model's own.

    `tokenizer` is `ENCODING` or `HEURISTIC`; `native` is true only when the
    encoder is loaded AND the model is one tiktoken maps to that same encoding.
    Everything else -- a Claude model, a llama on a local server, an OpenAI
    model on the newer `o200k_base`, an Android build with no tiktoken, a
    desktop that could not fetch the encoding -- is an estimate, and the
    context inspector says so rather than presenting a GPT-4 count as what the
    provider will bill. `native` is not "matches the bill" either: the packer
    counts text, not the chat format's per-message framing, so even a native
    count sits a little under the provider's `prompt_tokens`.

    Reads the loader's state and never loads (`_loaded`), so it cannot start a
    download or wait on one -- `prompt_log.record` calls it on a path that must
    not wait. It describes the counter as it stands now, which is the one the
    compose that just ran used, with one blind spot: `count_tokens` falls back
    to the heuristic per string when `encode` raises, so a section whose text
    trips the encoder is counted by length under a `cl100k_base` label.

    It is a description of the counting, not a correction of it. Counting each
    model with its own tokenizer would change what the packer keeps per
    connection, and for most backends there is no tokenizer to load at all.
    """
    if _loaded() is None:
        return {"tokenizer": HEURISTIC, "native": False}
    return {"tokenizer": ENCODING, "native": _native_encoding(model) == ENCODING}


def record_tokens(path: Path, body: str) -> int:
    """What one record's body costs when it reaches a prompt, memoized on the
    file's stat signature.

    The BODY only, stripped: frontmatter never enters the context, and
    `context.world_state._world_info` hands the assembler `body.strip()`, so
    that is the string measured here. What the badge therefore reports is a
    *per-activation* cost — a keyed entry pays it on the turns its keys hit,
    not on every turn.

    What it is NOT is a term in the section total the context inspector
    reports. That section is the activated bodies joined with blank lines, and
    BPE is not additive: these counts summed land about a token per join below
    the inspector's row. Same tokenizer, different string -- the two are
    comparable in scale, and reconciling them arithmetically is a mistake the
    numbers invite (`test_a_record_count_is_not_a_term_in_the_section_total`
    pins it so nobody re-derives the equality).

    It counts the STORED text, macros unexpanded, and that is a real ceiling
    rather than a rounding error. `_render_sections` runs every section through
    `macros.expand_macros`, so `{{user}}` becomes a name and
    `{{random:a,b,c,d,e}}` collapses to ONE option -- a macro-heavy body can
    cost half what it measures. Nothing here can do better: the expansion is
    per scene and per turn, so no single number is the answer, and a ceiling is
    the right direction for a figure someone is using to decide what to trim.

    `body` must be the body parsed from `path` -- the memo persists, so a
    caller that pairs one file's path with another's text poisons that
    signature for every later reader, not just for itself. Given that, passing
    the text in is what keeps an unchanged file from being re-encoded without
    making any caller read it twice, and it is safe in both interleavings: a
    write between the caller's read and this stat moves mtime to ~now, and
    `statcache.memo` refuses to cache anything inside its racy window, so a
    stale body can never be stored under the signature that replaced it.

    The memo kind names the MEASURE, not the caller: a future counter over a
    different slice of the same file (a card's description+personality, say)
    must pick its own kind, or it would read this one's answer back.

    One capacity note, since `statcache` is shared and capped at MAX_ENTRIES:
    an entity file now occupies two slots, this and `entities.entity_hash`, so
    a world large enough to fill the cache starts evicting sooner than before.
    Both derivations are cheap to recompute, so the failure mode is a slower
    sweep rather than a wrong answer, and the cap is left alone deliberately --
    raising it is a judgement about the cache, not about this measure.
    """
    return statcache.memo("body_tokens", statcache.signature(path),
                          lambda: count_tokens(body.strip()))
