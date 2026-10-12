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
now. The acyclic compiled/statcache support leaves let both sides share it;
`context` re-exports `count_tokens` so its own callers are unchanged.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import compiled, statcache

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


#: Which counter this thread's latest `count_tokens` call used -- `ENCODING` or
#: `HEURISTIC`. Thread-local because composes run concurrently in the
#: threadpool. Read by `context.assemble._token_memo`, which is what lets a
#: breakdown say how ITS numbers were made rather than how the loader stands
#: later (the loader can come back from a failed load between the two).
_last = threading.local()

#: What a compose reports when its strings were counted by both -- an encode
#: that raised on one string falls back for that string alone.
MIXED = "mixed"


def last_counter() -> str:
    """The counter the latest `count_tokens` on this thread used, or "" when
    this thread has not counted anything (or a test replaced `count_tokens`)."""
    return getattr(_last, "counter", "")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    try:
        n = len(_encoder().encode(text))
        _last.counter = ENCODING
        return n
    except Exception:  # noqa: BLE001 - token counting must never fail a turn; heuristic instead
        _last.counter = HEURISTIC
        # Round UP, and never to zero. The heuristic is applied per string, and
        # the packer counts each history message separately -- with floor
        # division every message under four characters cost nothing at all, so
        # a scene of short alternating turns ("yes." / "go on.") summed to zero
        # and the packer saw no history to trim however small the budget. A
        # non-empty string is at least one token; overestimating slightly is
        # the safe direction for a ceiling.
        return -(-len(text) // 4)


def count_if_loaded(text: str) -> int:
    """`count_tokens`, never starting a load: the encoder if it has already
    loaded (`_loaded`), else the characters/4 heuristic, rounded up as
    `count_tokens` rounds it.

    For an estimate made on a request path that must not wait on an encoder
    download -- an embed call whose provider reported no prompt count
    (`inference.embed.estimate_prompt`). Does not touch `last_counter`, which
    describes a compose's counts, not this. Never raises."""
    if not text:
        return 0
    encoder = _loaded()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:  # noqa: BLE001 - a count never fails a call; heuristic instead
            pass
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


#: The only connection kind whose model ids name the model that actually
#: answers. An `openai_compatible` endpoint may serve anything under any id --
#: including OpenRouter-style ones like `openai/gpt-4` (a LiteLLM gateway, a
#: local server) -- and a Claude connection is never on tiktoken's table.
NATIVE_KINDS = ("openrouter",)


def counting(model: str, kind: str = "", counted_with: str = "") -> dict:
    """How the token counts reported beside `model` were made, and whether
    the tokenizer was that model's own.

    `counted_with` is the counter the compose actually used (`ENCODING`,
    `HEURISTIC` or `MIXED`, from `context.assemble._token_memo`), and it is
    what `tokenizer` reports when given. Only when it is absent -- a breakdown
    built outside that pass -- is the loader's CURRENT state read instead
    (`_loaded`, which never starts a load), and that can be wrong in one
    direction: a load that succeeded after the counting was done.

    `native` needs all three: counted with `ENCODING`, on a connection whose
    kind is in `NATIVE_KINDS`, for a model tiktoken maps to `ENCODING`. `kind`
    defaults to unknown, which is never native -- a fallback attempt's kind is
    not threaded through to its snapshot, so its counts read as estimates.
    Everything else -- a Claude model, a llama on a local server, an OpenAI
    model on the newer `o200k_base`, an Android build with no tiktoken, a
    desktop that could not fetch the encoding -- is an estimate, and the
    context inspector says so rather than presenting a GPT-4 count as what the
    provider will bill. `native` is not "matches the bill" either: the packer
    counts text, not the chat format's per-message framing, so even a native
    count sits a little under the provider's `prompt_tokens`.

    It is a description of the counting, not a correction of it. Counting each
    model with its own tokenizer would change what the packer keeps per
    connection, and for most backends there is no tokenizer to load at all.
    """
    tokenizer = counted_with or (HEURISTIC if _loaded() is None else ENCODING)
    native = (tokenizer == ENCODING and kind in NATIVE_KINDS
              and _native_encoding(model) == ENCODING)
    return {"tokenizer": tokenizer, "native": native}


def record_tokens(path: Path, body: str) -> int:
    """What one record's body costs when it reaches a prompt, memoized on the
    supplied text and the actual encoder.

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

    The supplied body is authoritative: a caller can hold old text beside a
    new file stamp, or pass different text for the same path. Exact text and the actual encoder therefore prove the memory measure;
    the text hash and encoding select its persistent counterpart. File currency
    is not an input to this supplied-text measure. A heuristic never enters
    either cache, so recovery of an
    offline encoder takes effect on the next call. Empty text clears ambiguous
    attribution here; count_tokens keeps its existing public behavior.

    The memo kind names the MEASURE, not the caller: a future counter over a
    different slice of the same file (a card's description+personality, say)
    must pick its own kind, or it would read this one's answer back.

    The entry- and byte-bounded pool belongs to this measure, so badge scans do not evict the
    sync hashes. Persistent entries contain only exact encoder counts, selected
    by supplied text hash and encoding; no file is reopened to make that key.
    A count is not a materialization written under the supplied path, so no
    file-currency history is claimed for it.
    """
    text = body.strip()
    _last.counter = ''
    if not text:
        return 0
    try:
        encoder = _encoder()
    except Exception:  # noqa: BLE001 -- the existing count fallback owns failures
        encoder = None
    if encoder is None:
        return count_tokens(text)
    # The path selects a bounded memory slot, not file currency. Exact body
    # equality plus a retained encoder object proves the supplied inputs. A
    # freshly parsed equal string can then hit without hashing it again.
    key = (str(path), id(encoder))
    with _RECORD_LOCK:
        entry = _RECORD_POOL.get(key)
        if entry is not None and entry[1] is encoder and entry[0] == text:
            _last.counter = ENCODING
            return entry[2]
    try:
        digest = _record_digest(text)
    except (UnicodeEncodeError, MemoryError):
        # The existing counter repairs lone surrogates and handles allocation
        # failures. Optional UTF8 hashing must not turn those into failed reads.
        return count_tokens(text)
    value = _record_measure(text, digest)
    if last_counter() == ENCODING:
        _remember_record(key, text, encoder, value)
    return value


def _remember_record(key: tuple[str, int], text: str, encoder: object, value: int) -> None:
    size = sys.getsizeof(text)
    if size > MAX_RECORD_MEMO_BYTES:
        return
    try:
        with _RECORD_LOCK:
            # A running total, not a re-sum: a sweep wider than the pool
            # inserts on every call, and summing 4,096 sizes each time made
            # that quadratic.
            if not _RECORD_POOL:
                _RECORD_RETAINED[0] = 0   # an emptied pool (a clear()) owes nothing
            replaced = _RECORD_POOL.pop(key, None)
            if replaced is not None:
                _RECORD_RETAINED[0] -= replaced[3]
            _RECORD_POOL[key] = (text, encoder, value, size)
            _RECORD_RETAINED[0] += size
            while _RECORD_POOL and (_RECORD_RETAINED[0] > MAX_RECORD_MEMO_BYTES
                                    or len(_RECORD_POOL) > statcache.MAX_ENTRIES):
                oldest = next(iter(_RECORD_POOL))
                _RECORD_RETAINED[0] -= _RECORD_POOL.pop(oldest)[3]
    except MemoryError:
        # Cache insertion is optional; the exact count has already succeeded.
        # Drop a partially inserted row if allocation prevented budget accounting.
        with _RECORD_LOCK:
            _RECORD_POOL.clear()
            _RECORD_RETAINED[0] = 0


# Repeated supplied bodies retain their exact digest without rereading
# all text on an outer-memo hit. stdlib's content-keyed cache is thread-safe;
# two strings of at most16MiB each bound retained text to32MiB plus overhead.
# Other inputs are hashed live, and hash collisions still compare full strings.
MAX_DIGEST_MEMO_BYTES = 16 * 1024 * 1024


@functools.lru_cache(maxsize=2)
def _large_body_digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _record_digest(text: str) -> str:
    if sys.getsizeof(text) <= MAX_DIGEST_MEMO_BYTES:
        return _large_body_digest(text)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


MAX_RECORD_MEMO_BYTES = 32 * 1024 * 1024
_RECORD_LOCK = threading.Lock()
_RECORD_POOL: dict = {}
_RECORD_RETAINED = [0]  # sys.getsizeof of every text in _RECORD_POOL
def _after_record_fork() -> None:
    global _RECORD_LOCK, _RECORD_POOL  # noqa: PLW0603 -- child hook replaces inherited process resources
    # The child has no thread that can release an inherited held mutex.
    _RECORD_LOCK = threading.Lock()
    _RECORD_POOL = {}
    _RECORD_RETAINED[0] = 0
    _large_body_digest.cache_clear()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_record_fork)


_RECORD_KIND = compiled.Kind('tokens:record_body', 1, (), ('text_hash', 'encoding'), False)
compiled.KINDS[_RECORD_KIND.name] = _RECORD_KIND


# The synthetic inequality fails for short bodies: the indexed lookup costs
# more than encoding them, especially with fresh-process BUILD initialization.
# The measured cutoff is eight MiB of supplied characters; ordinary record
# badges still use the safe outer memo without paying for SQLite.
PERSIST_MIN_CHARS = 8_388_608


def _record_measure(text: str, digest: str) -> int:
    if len(text) < PERSIST_MIN_CHARS:
        return count_tokens(text)
    computed = False
    def compute(view, params):
        nonlocal computed
        computed = True
        _last.counter = ''
        count = count_tokens(text)
        return count if last_counter() == ENCODING else compiled.Uncached(count)
    with compiled.batch() as request:
        value = request.derive(_RECORD_KIND, [], params={'text_hash': digest, 'encoding': ENCODING}, compute=compute)
    if not computed:
        _last.counter = ENCODING  # only encoder-counted rows are admitted
    if type(value) is not int or value < 0:
        # A native but structurally wrong optional row cannot change a badge.
        return count_tokens(text)
    return value
