"""The gateway clients belong to the app, and shutdown closes them (#215).

`LLMClient` and the model-listing `OpenAICompatibleClient` each own an
`httpx.AsyncClient` connection pool. They used to be module-level singletons
built at import, which left the pools with nowhere to be closed from: harmless
where the server is the whole process and it exits anyway, a leak per app
wherever one is rebuilt inside a living process — the Android entry point runs
uvicorn in-process, and this suite builds an app per test.
"""

import importlib

import anyio
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import grimoire.store as store
from grimoire import main, routes
from grimoire.llm import LLMClient
from grimoire.main import create_app
from grimoire.openai_compatible import OpenAICompatibleClient


class _Recorder:
    """Stands in for a gateway client; counts the closes it is asked for."""

    def __init__(self):
        self.closes = 0

    async def aclose(self) -> None:
        self.closes += 1


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return create_app()


def _flatten(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _flatten(sub)]
    return [exc]


def _request_to(app) -> Request:
    """The bare scope a dependency needs: `Request.app` is `scope["app"]`."""
    return Request({"type": "http", "headers": [], "app": app})


def test_each_app_owns_its_own_gateway_clients(app):
    other = create_app()

    assert isinstance(app.state.llm, LLMClient)
    assert isinstance(app.state.openai_compatible, OpenAICompatibleClient)
    assert app.state.llm is not other.state.llm
    assert app.state.openai_compatible is not other.state.openai_compatible


def test_the_providers_hand_back_the_app_s_clients(app):
    request = _request_to(app)

    assert routes.get_llm(request) is app.state.llm
    assert routes.get_openai_compatible_client(request) is app.state.openai_compatible


def test_shutdown_closes_both_clients(app):
    app.state.llm = _Recorder()
    app.state.openai_compatible = _Recorder()

    with TestClient(app):
        pass

    assert app.state.llm.closes == 1
    assert app.state.openai_compatible.closes == 1


def test_shutdown_closes_the_underlying_connection_pools(app):
    """The recorder above proves the lifespan calls `aclose`; this proves the
    call reaches the real `httpx` pools rather than stopping at the facade."""
    with TestClient(app):
        # Both pools are lazy -- force them into existence so there is
        # something for shutdown to close.
        pools = [app.state.llm._openrouter._client(),
                 app.state.llm._openai_compatible._client(),
                 app.state.openai_compatible._client()]
        assert not any(pool.is_closed for pool in pools)

    assert all(pool.is_closed for pool in pools)


def test_every_closable_the_app_holds_is_closed_by_shutdown(app):
    """`_lifespan` names the clients it closes, so a third one hung on
    `app.state` later would leak in exactly the silence #215 was about. This is
    the thing that notices: anything on the app with an `aclose` has to be
    closed on the way out, or be given a reason not to be here.

    Its blind spot, stated: a client that never reaches `app.state` at all --
    a module-level singleton of the kind this issue removed, or the
    `EmbeddingsClient` pair still living in `store/` -- is invisible from
    here."""
    # Starlette keeps `State`'s attributes in this dict.
    closable = [name for name, value in app.state._state.items()
                if hasattr(value, "aclose")]
    assert closable, "nothing closable on app.state — has the wiring moved?"
    for name in closable:
        setattr(app.state, name, _Recorder())

    with TestClient(app):
        pass

    leaked = [name for name in closable if getattr(app.state, name).closes != 1]
    assert not leaked, f"app.state holds {leaked}, which shutdown never closes"


def test_a_second_run_over_the_same_app_gets_a_working_pool(app):
    """Closing resets the lazy handle rather than leaving a closed client in
    it: an app served a second time builds a new pool instead of handing every
    caller one that raises."""
    with TestClient(app):
        first = app.state.llm._openrouter._client()

    with TestClient(app):
        second = app.state.llm._openrouter._client()

        assert second is not first
        assert not second.is_closed


@pytest.mark.parametrize("broken", ["llm", "openai_compatible"])
def test_one_failing_close_does_not_strand_the_other_pool(app, caplog, broken):
    """Shutdown cleanup that gives up halfway is the leak this change is about.

    Both arrangements, because guarding only the first close would pass the one
    where the first client is the broken one and strand the pool in the other."""
    class _Broken:
        async def aclose(self):
            raise RuntimeError("the pool refused to close")

    other = "openai_compatible" if broken == "llm" else "llm"
    setattr(app.state, broken, _Broken())
    setattr(app.state, other, _Recorder())

    with TestClient(app):
        pass

    assert getattr(app.state, other).closes == 1
    assert "the pool refused to close" in caplog.text


def test_a_crash_on_the_way_out_still_closes_the_clients(app):
    """The close runs in a `finally`: an exception thrown into the lifespan on
    shutdown must not be the reason a pool leaks. Driven through `_lifespan`
    directly because `TestClient` only ever exits it cleanly."""
    app.state.llm = _Recorder()
    app.state.openai_compatible = _Recorder()

    async def drive():
        ctx = main._lifespan(app)
        await ctx.__aenter__()
        with pytest.raises(BaseException) as caught:
            await ctx.__aexit__(RuntimeError, RuntimeError("shutdown exploded"), None)
        # The crash still comes out — closing the pools must not swallow it.
        # Flattened because anyio wraps what escapes a task group in a group,
        # and how deeply is anyio's business rather than this test's.
        assert [type(exc) for exc in _flatten(caught.value)] == [RuntimeError]

    anyio.run(drive)

    assert app.state.llm.closes == 1
    assert app.state.openai_compatible.closes == 1
