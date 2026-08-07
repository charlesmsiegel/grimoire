"""The built frontend's client routes have to survive a reload (#87).

The app is a BrowserRouter SPA, so `/campaigns/<cid>/scenes/<sid>` is a path the
browser resolves — but a reload, a bookmark or a shared link asks the server for
it first. `StaticFiles(html=True)` answers 404 for every such path (it serves
directory indexes and looks for a `404.html`; it does not route unknown paths to
the root document), so the whole point of #87 held only under `vite dev`.
"""

import pytest
from fastapi.testclient import TestClient

from grimoire.main import SPAStaticFiles

PAGE = {"accept": "text/html,application/xhtml+xml"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path / "home"))
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>grimoire</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_DIST", str(dist))

    from fastapi import FastAPI
    from grimoire.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="static")
    return TestClient(app)


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
