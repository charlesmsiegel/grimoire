"""The built frontend's client routes have to survive a reload (#87).

The app is a BrowserRouter SPA, so `/campaigns/<cid>/scenes/<sid>` is a path the
browser resolves — but a reload, a bookmark or a shared link asks the server for
it first. `StaticFiles(html=True)` answers 404 for every such path (it serves
directory indexes and looks for a `404.html`; it does not route unknown paths to
the root document), so the whole point of #87 held only under `vite dev`.
"""

import ntpath
import os.path

import pytest
from fastapi.testclient import TestClient

from grimoire.main import SPAStaticFiles

PAGE = {"accept": "text/html,application/xhtml+xml"}


def _resolve_with(mod, scope):
    """Starlette's `StaticFiles.get_path`, with its `os.path` swapped for `mod`.

    `os.path.normpath(os.path.join(*route_path.split("/")))` is the real thing;
    substituting `ntpath` resolves a URL the way Windows resolves it, which is
    how a checkout on Ubuntu CI can produce the `api\\not-a-real-endpoint` from
    #313 at all. The app under test mounts at `/`, so the route path is the
    request path.
    """
    return mod.normpath(mod.join(*scope["path"].split("/")))


def _windows_get_path(self, scope):
    """`get_path` as a Windows checkout would answer it."""
    return _resolve_with(ntpath, scope)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>grimoire</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    monkeypatch.setenv("GRIMOIRE_DIST", str(dist))

    from fastapi import FastAPI

    from grimoire.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="static")
    # `with`, so the lifespan runs: producing routes hand their work to a
    # runner that lives on it, and a client without one cannot drive a turn.
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", [
    "/campaigns/realm-watch/scenes/003--council-of-mara",
    "/campaigns/realm-watch",
    "/worlds",
    "/config",
])
def test_a_client_route_serves_the_app_rather_than_404(client, path):
    r = client.get(path, headers=PAGE)
    assert r.status_code == 200
    assert "grimoire" in r.text


def test_real_files_still_win_over_the_fallback(client):
    assert client.get("/", headers=PAGE).status_code == 200
    r = client.get("/assets/app.js", headers=PAGE)
    assert r.status_code == 200
    assert r.text == "console.log(1)"


def test_a_missing_asset_keeps_its_404(client):
    # Answering index.html for a bad .js URL replaces "file not found" with a
    # document the browser tries to parse as a script. A subresource request
    # does not ask for text/html, which is how the fallback tells them apart.
    r = client.get("/assets/gone.js", headers={"accept": "*/*"})
    assert r.status_code == 404


def test_an_unknown_api_path_stays_a_404_even_for_a_browser(client):
    # A navigation to a mistyped endpoint still asks for text/html; answering
    # it with the SPA would hand back 200 and an HTML body for a missing API.
    r = client.get("/api/not-a-real-endpoint", headers=PAGE)
    assert r.status_code == 404
    assert "grimoire</html>" not in r.text


def test_an_unknown_api_path_stays_a_404_with_windows_separators(client, monkeypatch):
    # The same request as above, on a Windows checkout. `path == "api"` matched
    # either way, so only *nested* paths slipped through -- which is every real
    # endpoint. The packaged desktop build and the Android WebView shell both
    # serve through this class rather than `vite dev`, so Windows is not a
    # developer-only surface here.
    monkeypatch.setattr(SPAStaticFiles, "get_path", _windows_get_path)
    r = client.get("/api/not-a-real-endpoint", headers=PAGE)
    assert r.status_code == 404
    assert "grimoire</html>" not in r.text


def test_a_client_route_still_falls_back_with_windows_separators(client, monkeypatch):
    # The other half of the separator fix: normalizing must not make a nested
    # *client* route look like an API path and cost it its fallback.
    monkeypatch.setattr(SPAStaticFiles, "get_path", _windows_get_path)
    r = client.get("/campaigns/realm-watch/scenes/003--council-of-mara", headers=PAGE)
    assert r.status_code == 200
    assert "grimoire" in r.text
    assert r.headers["cache-control"] == "no-cache"


# ---- caching: the document revalidates, the hashed bundle never does ----

@pytest.mark.parametrize("path", [
    "/",
    "/index.html",
    # The fallback answers a client route with the same document, so it has
    # to carry the same policy -- a reload of a scene URL is how most loads
    # after the first one arrive.
    "/campaigns/realm-watch/scenes/003--council-of-mara",
    "/worlds",
])
def test_the_document_is_revalidated_on_every_load(client, path):
    # Vite empties `dist/` on every build, so an index.html a browser kept
    # past a rebuild (or an APK update) names bundles that no longer exist,
    # and the fallback deliberately 404s the script request: a blank app
    # until a hard reload. `no-cache`, not `no-store`: the ETag keeps each
    # revalidation a bodiless 304.
    r = client.get(path, headers=PAGE)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers.get("etag")


def test_a_hashed_asset_is_immutable(client):
    r = client.get("/assets/app.js", headers={"accept": "*/*"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_an_unhashed_public_file_is_cached_for_a_day(client):
    r = client.get("/favicon.ico", headers={"accept": "image/*"})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=86400"


@pytest.mark.parametrize("path, accept, policy", [
    ("/", PAGE["accept"], "no-cache"),
    ("/campaigns/realm-watch", PAGE["accept"], "no-cache"),
    ("/assets/app.js", "*/*", "public, max-age=31536000, immutable"),
])
def test_a_304_carries_the_policy_too(client, path, accept, policy):
    # A cache replaces its stored headers with a 304's, so this is how a copy
    # cached BEFORE the policy existed -- heuristically fresh, with no
    # Cache-Control of its own -- learns it on its first revalidation. Without
    # it that copy would stay on heuristic freshness for as long as it lived.
    etag = client.get(path, headers={"accept": accept}).headers["etag"]
    r = client.get(path, headers={"accept": accept, "if-none-match": etag})
    assert r.status_code == 304
    assert r.headers["cache-control"] == policy


def test_a_missing_asset_is_not_cached_as_immutable(client):
    # A 404 under `assets/` is a deploy in flux or a typo, not a file that
    # will never change; pinning it for a year would outlive the fix.
    r = client.get("/assets/gone.js", headers={"accept": "*/*"})
    assert r.status_code == 404
    assert "immutable" not in r.headers.get("cache-control", "")


@pytest.mark.parametrize("path, policy", [
    ("assets/index-3f9a1c.js", "public, max-age=31536000, immutable"),
    # A Windows checkout resolves the same URL with backslashes (#313).
    ("assets\\index-3f9a1c.js", "public, max-age=31536000, immutable"),
    ("assets/fonts/inter-latin.woff2", "public, max-age=31536000, immutable"),
    ("index.html", "no-cache"),
    # The root URL resolves to "." and is answered with index.html.
    (".", "no-cache"),
    ("favicon.ico", "public, max-age=86400"),
    ("grimoire-128.png", "public, max-age=86400"),
    # Whole first segment, as with the API guard: only Vite's own output
    # directory is content-hashed.
    ("assets-old/index.js", "public, max-age=86400"),
])
def test_the_cache_policy_reads_either_separator(path, policy):
    assert SPAStaticFiles._cache_control(path) == policy


@pytest.mark.parametrize("path, under", [
    # The two spellings of the same request. The second is the one from the
    # bug report verbatim, asserted against the predicate directly: every other
    # test here reaches it through `_windows_get_path`, which is this repo's
    # *belief* about how Windows resolves a URL. If that belief is wrong, those
    # tests go green while Windows stays broken -- this one cannot.
    ("api/not-a-real-endpoint", True),
    ("api\\not-a-real-endpoint", True),
    ("api", True),
    ("api\\campaigns\\realm-watch\\scenes", True),
    # Near misses. A first segment that merely *starts with* "api" is a client
    # route, and `startswith("api")` would have taken its fallback away.
    ("apiary", False),
    ("api-docs", False),
    ("apidocs", False),
    # `get_path` resolves `..` before this sees it, so `/api/../worlds` arrives
    # as a client route and `/x/../api/gone` arrives under the API.
    ("worlds", False),
    ("campaigns\\realm-watch", False),
    # The root URL: `os.path.normpath("")` is ".", not "".
    (".", False),
])
def test_the_api_guard_reads_either_separator(path, under):
    assert SPAStaticFiles._under_api(path) is under


@pytest.mark.parametrize("url", ["/api", "/api/", "/api/x/y", "/", "/worlds",
                                 "/api/../worlds", "//api/x"])
def test_the_windows_simulation_still_tracks_starlette(tmp_path, url):
    """`_resolve_with` is a *copy* of Starlette's `get_path`, and a copy drifts.

    If `get_path` grows a step this does not model -- stripping `root_path`,
    joining differently -- the two tests above keep passing while simulating
    something Starlette no longer does, and the Windows regression they exist to
    catch walks straight back in. So the copy is checked against the real method
    on whatever platform is running: on Ubuntu that pins the posix spelling, on
    Windows it pins the very ntpath one `_windows_get_path` fakes elsewhere.
    """
    files = SPAStaticFiles(directory=str(tmp_path), html=True)
    scope = {"type": "http", "path": url, "root_path": "", "headers": []}
    assert files.get_path(scope) == _resolve_with(os.path, scope)
