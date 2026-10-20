"""common: clients responsibilities."""

from __future__ import annotations

from fastapi import Request

from ... import embeddings, store, wire
from ...health import ProviderHealth
from ...llm import LLMClient
from ...openai_compatible import OpenAICompatibleClient


def build_llm(health: ProviderHealth | None = None) -> LLMClient:
    """The gateway client for one app (#215).

    Built per app rather than once per process because it owns an
    `httpx.AsyncClient` connection pool that `main._lifespan` closes on the way
    out; a module-level singleton had nowhere to be closed from. Construction
    opens nothing — the pool is created on the first call — so an app that
    never generates pays nothing for holding one.

    The idle bound is passed as a resolver, not a number: llm.py must not import
    the store (#239), and reading config.md per call is what lets a
    Configuration-page change land without a restart (#243). The retry count
    rides the same seam for the same two reasons. The fallback (#144) does not:
    each call's resolved chain carries its own (`wire.Chain.fallback`, resolved
    by `store.inference.resolve`), so the client holds none.

    The token counter rides the seam too (`_count_tokens`): the facade counts
    what a provider did not report, on a worker thread, and the gateway may not
    import `store.tokens` to do it (spec 9.1).

    `health` is that app's registry (#146), passed as the observer every attempt
    reports its outcome to. Optional so a caller that only wants to generate —
    tests, mostly — need not build one; the facade treats a missing observer as
    "nobody is listening" rather than as an error.
    """
    return LLMClient(timeout=store.config.llm_timeout,
                     retries=store.config.llm_retries,
                     observer=health.record if health is not None else None,
                     capture=store.logs.incoming_capture,
                     images=_post_images_for,
                     load_image=_load_post_image,
                     count_tokens=_count_tokens)


# Late-bound through the module attribute, so a test patching
# `store.post_images` intercepts what the facade calls (#377).
def _post_images_for(attempt: wire.Target) -> int:
    return store.post_images.images_for(attempt)


def _load_post_image(cid: str, part: dict) -> str | None:
    return store.post_images.load(cid, part)


# Late-bound for the same reason: a test patching `store.tokens` intercepts the
# counter the facade runs, on a worker thread, for a provider that reported no
# counts (spec 9.1).
def _count_tokens(text: str) -> int:
    return store.tokens.count_tokens(text)


def build_openai_compatible_client() -> OpenAICompatibleClient:
    """The model-listing client for one app, owned and closed like `build_llm`'s.

    A second instance rather than the one `LLMClient` holds for generation:
    `list_models` is not part of the gateway's surface, which dispatches by
    connection kind, and reaching through `LLMClient` for the inner client
    would make a private attribute part of the route's contract.
    """
    return OpenAICompatibleClient()


def get_health(request: Request) -> ProviderHealth:
    """The app's health registry, built in `main.create_app` (#146).

    On `app.state` rather than at module scope for the reason every other piece
    of per-app state is: a `TestClient` builds an app per test, and a shared
    module-level registry would carry one test's recorded provider failures
    into the next.
    """
    return request.app.state.health


def get_openai_compatible_client(request: Request) -> OpenAICompatibleClient:
    return request.app.state.openai_compatible


def build_embeddings() -> embeddings.AsyncEmbeddingsClient:
    """The app's async embeddings client (roadmap 01h-C4b), owned and closed
    like `build_llm`'s: one per app, so its pool is closed by the lifespan
    that served it, and touched only from that app's loop."""
    return embeddings.AsyncEmbeddingsClient()


def get_embeddings(request: Request) -> embeddings.AsyncEmbeddingsClient:
    """The client an async caller hands `store.inference.embed.embed`.
    `app.dependency_overrides` replaces this callable whole: the test seam."""
    return request.app.state.embeddings


def get_llm(request: Request) -> LLMClient:
    """The app's gateway client, built in `main.create_app` and closed by its
    lifespan (#215). `app.dependency_overrides` replaces this callable whole,
    which is the seam `tests/llm_fakes.py` is injected at."""
    return request.app.state.llm
