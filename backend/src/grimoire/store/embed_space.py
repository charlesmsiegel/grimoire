"""The configured embedding endpoint, and the text preparation that feeds it.

Two things in this codebase embed text: `context/semantic.py`, which recalls
lore behind the `activate` seam, and `store/semsearch.py`, which answers the
reader's own query (#34). They ask the same four questions — which endpoint,
which model, which *space* the resulting vectors live in, and how to cut a
text down to something the provider will accept — and they have to answer them
identically, because they share one on-disk cache.

That sharing is the whole reason this module exists rather than each caller
resolving its own connection. A "space" is the namespace a cached vector is
keyed under, and two callers that disagree about it either miss every vector
the other warmed (harmless, expensive) or read a vector made against different
weights (silent, wrong). One definition, in one place.

What is *not* here is policy: how many entries a recall may add, what a search
scores as relevant, how much of a corpus one request may warm. Those differ
between the two callers and belong with them.
"""

from __future__ import annotations

import zlib

from . import config, llm_connections
from .inference import resolve as inference_resolve
from .inference import translate


def resolve(cfg: dict | None = None) -> dict | None:
    """`{model, base_url, key, space}` for the configured endpoint, or None.

    None is the answer for every kind of "not set up": no model, no connection
    id, an id naming a connection that was deleted, a connection of a kind that
    serves no ``/embeddings`` route, one with no base URL, or a model it is
    KNOWN not to embed with (`inference_resolve.embed_attempt`: no request is
    sent that could only fail; a name-rule guess never counts). Callers treat all
    of them the same way — the layer is off — so distinguishing them here would
    buy nothing. The store this reads may be hand-edited or half-synced, so it
    must not raise for any of them either.

    `cfg` is a config mapping the caller has already read. `read_config` parses
    config.md on every call and nothing memoizes it, so a caller that wanted
    the config for its own settings too — `context/semantic.settings`, on every
    turn — would otherwise pay for the file twice.
    """
    try:
        cfg = config.read_config() if cfg is None else cfg
        conn_id, model = translate.embedding_role(cfg)
        if not model or not conn_id:
            return None
        return _resolved(cfg, llm_connections.read_connection_raw(conn_id), model)
    except (llm_connections.ConnectionNotFound, OSError, UnicodeDecodeError,
            KeyError, TypeError, ValueError):
        return None


def _resolved(cfg: dict, conn: dict, model: str, *, catalog: bool = True) -> dict | None:
    """`resolve`'s answer for the Embedding role served by `conn` at `model`,
    or None when it embeds nothing: no endpoint, or a model `conn` is known not
    to embed with. The rule and the space are `resolve.embed_attempt`'s (its
    `space_of` says what a space is keyed on, and why); `catalog` False judges
    it without the cached catalog row (see there, for `moved_by`)."""
    got = inference_resolve.embed_attempt(str(conn["id"]), model, conn,
                                          current=translate.is_current(cfg), catalog=catalog)
    if got.space_id is None:
        return None
    return {"model": model, "base_url": got.attempt.base_url, "key": conn["api_key"],
            "space": got.space_id}


#: `problem`'s answer when none of its specific reasons applies.
OFF = "Embedding is off"


def problem(cfg: dict | None = None) -> str | None:
    """Why the Embedding role embeds nothing, as a short sentence for the
    Models page's Embedding card; None when it embeds (`resolve` is not None).

    The same conditions `resolve` and `resolve.embed_endpoint` walk, in the
    order a reader fixes them: a provider, one that exists, of a kind that serves
    ``/embeddings`` here, with the key or address it needs, then a model;
    `OFF` when none of them is why. (A model known not to embed is the
    settings card's own reason, asked after these: `settings._embedding_card`.)
    Never raises."""
    try:
        cfg = config.read_config() if cfg is None else cfg
        if resolve(cfg) is not None:
            return None
        conn_id, model = translate.embedding_role(cfg)
        if not conn_id:
            return "No provider chosen"
        try:
            conn = llm_connections.read_connection_raw(conn_id)
        except llm_connections.ConnectionNotFound:
            return "The chosen provider no longer exists"
        name = str(conn.get("name") or conn_id)
        kind = conn.get("kind")
        if kind == "openai_compatible" and not conn.get("base_url"):
            return f"{name} has no address set"
        if kind == "openrouter" and translate.is_current(cfg) and not conn.get("api_key"):
            return f"{name} has no key set"
        if kind != "openai_compatible" and not inference_resolve.embed_endpoint(
                conn, translate.is_current(cfg)):
            return f"{name} cannot embed"
        if not model:
            return "No model chosen"
        return OFF
    except (OSError, UnicodeDecodeError, KeyError, TypeError, ValueError):
        return OFF


def moved_by(cfg: dict, before: dict, after: dict) -> bool:
    """Whether replacing connection `before` with `after` (one id; `after`
    carrying the rev the write will stamp) starts the Embedding role on a NEW
    vector space that embeds: the edit that re-embeds the library, so the one
    a provider edit is confirmed for (spec 7.3, rule 1).

    False when the role does not name this connection, has no model, or embeds
    nothing afterwards (clearing an address re-embeds nothing). A role that
    embedded nothing before and does now -- a key on a keyless OpenRouter
    provider, or an address that leaves a provider known not to embed -- is a
    move: the library is embedded from scratch. Never raises.

    `after` is judged as `resolve()` will read it once the write lands. A rev
    restamp leaves the cached catalog row stale (its sidecar is gated on the
    rev on disk), so a catalog `no` does not speak for `after` then and is
    not read (`catalog=False`); the adapter's, the preset's and the user's
    `no` survive the save and still count.
    """
    try:
        conn_id, model = translate.embedding_role(cfg)
        if not model or not conn_id or conn_id != after.get("id"):
            return False
        new = _resolved(cfg, after, model, catalog=after.get("rev") == before.get("rev"))
        if new is None:
            return False
        old = _resolved(cfg, before, model)
        return old is None or old["space"] != new["space"]
    except (OSError, KeyError, TypeError, ValueError):
        return False


def config_moved(before: dict, after: dict) -> bool:
    """Whether replacing config mapping `before` with `after` starts the
    Embedding role on a NEW vector space that embeds -- the `config.md` write
    that re-embeds the library: the legacy `embeddings_connection_id` /
    `embeddings_model` pair moved by `PUT /config` on a store not yet at
    format 2. `moved_by`'s rule, for a change of the role's own keys rather
    than of the provider it names: off to on is a move, a different space is
    a move, and switching it off re-embeds nothing. Never raises."""
    new = resolve(after)
    if new is None:
        return False
    old = resolve(before)
    return old is None or old["space"] != new["space"]


def facts_moved(cfg: dict, provider_id: str, model: str, before: dict,
                after: dict) -> bool:
    """Whether replacing `model`'s facts on `provider_id` (`before`, as
    `facts.of` reads them) with `after` starts the Embedding role embedding:
    the facts write a user makes that is confirmed first (spec 7.3, rule 1).

    Facts never move the space itself (`space_of` is the provider, its rev
    and the model), but they decide whether the role embeds at all: the
    user's `embed: yes` over a catalog's `no` turns it on, and the library
    is then embedded from scratch -- off to on is a move, as it is for
    `moved_by`. Turning it off re-embeds nothing. False when the role does
    not name this provider and model. Never raises."""
    try:
        conn_id, role_model = translate.embedding_role(cfg)
        if not role_model or conn_id != provider_id or role_model != model:
            return False
        raw = llm_connections.read_connection_raw(provider_id)
        current = translate.is_current(cfg)
        new = inference_resolve.embed_attempt(provider_id, model, raw, current=current,
                                              model_facts=after).space_id
        if new is None:
            return False
        old = inference_resolve.embed_attempt(provider_id, model, raw, current=current,
                                              model_facts=before).space_id
        return old is None or old != new
    except (llm_connections.ConnectionNotFound, OSError, UnicodeDecodeError,
            KeyError, TypeError, ValueError):
        return False


def clip(text: str, max_bytes: int, tail: bool = False) -> str:
    """`text` cut to `max_bytes` UTF-8 bytes, from the end when `tail`.

    Bytes, not characters, because characters are the wrong unit for a token
    window: text written in CJK is about one token per character, so an
    8000-character bound is an 8000-token input against the ~8191-token window
    the common embedding models have, and the provider rejects it. Bytes bound
    tokens from above for every script (no tokenizer emits more than one token
    per byte).

    Cutting bytes can split a multi-byte character; the partial one is dropped
    rather than replaced, so the result is always text the provider will accept
    and always shorter than the bound rather than one byte over it.
    """
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    cut = raw[-max_bytes:] if tail else raw[:max_bytes]
    return cut.decode("utf-8", "ignore")


def warm_window(uncached: list[str], query_text: str, limit: int) -> list[str]:
    """The `limit` texts to embed this time, starting at a rotating offset.

    This is the whole answer to a class of failure that took several attempts
    to see as one thing. A *fixed* prefix means whatever sits at the head and
    fails to cache sits there again next time, and forever: a document the
    provider refuses, a document it answers with a zero vector, a document
    caught by a transient outage. Each of those was found and patched
    separately — with an isolation pass, then a bound on that pass, then a
    tombstone for refused inputs — and the tombstone then had its own failure
    mode, permanently rejecting a valid document over a transient 502. The
    mechanism grew more dangerous than what it was guarding against.

    Rotating the window makes all of it transient instead. Nothing can occupy
    the head, because there is no head: a stuck document costs its own slot in
    the windows that happen to include it, and every other document is reached
    within a few rounds regardless. A permanently unembeddable entry settles
    into costing one failed request on the rounds it appears in, and nothing
    else. No failure classification, no persistent state, no second code path.

    The offset comes from the query, so it varies between queries while staying
    deterministic for any one of them — which keeps the caller a pure function
    of its inputs and the cache.

    Convergence is probabilistic rather than ordered, so it is worth having
    measured. With one permanently unembeddable document among N, everything
    else warms within: 32 rounds at N=64, 5 at N=67, 13 at N=83, 16 at N=263,
    142 at N=1063 — the tail is slowest because the last few entries need a
    window that happens to exclude the stuck one. Without a stuck document
    nothing fails and warming is a full window per round as before.
    """
    if len(uncached) < 2:
        return list(uncached)
    # A PROPER subset, always. Taking the whole list once it fits inside
    # `limit` looks harmless and undoes the rotation entirely: the window
    # stops varying, so a stuck document is in every window again and the last
    # `limit` entries never warm. Measured -- the tail never converged.
    size = min(limit, len(uncached) - 1)
    start = zlib.crc32(query_text.encode("utf-8")) % len(uncached)
    return [uncached[(start + n) % len(uncached)] for n in range(size)]
