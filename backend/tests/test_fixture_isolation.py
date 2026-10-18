"""An inherited launcher library or frontend build is not test fixture state."""

import subprocess
import sys

from tests import synthetic_suite


def _backend_run(tmp_path, nodes, dist):
    home = tmp_path / "inherited-home"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(tmp_path / "pytest"), *nodes],
        cwd=synthetic_suite.BACKEND,
        env=synthetic_suite.child_env(GRIMOIRE_HOME=str(home), GRIMOIRE_DIST=str(dist)),
        capture_output=True, text=True, timeout=120, check=False)
    return result, home


def _dist(tmp_path):
    dist = tmp_path / "built-dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>grimoire</title>", encoding="utf-8")
    return dist


def test_inherited_home_does_not_share_image_objects_between_tests(tmp_path):
    # These existing cases write and then expect an undescribed copy of the
    # same image bytes. The real shared fixtures must give them separate pools.
    result, inherited = _backend_run(tmp_path, [
        "tests/test_image_descriptions_store.py::test_write_rejects_unknown_image_and_persists_empty",
        "tests/test_image_descriptions_store.py::test_version_wrappers",
    ], tmp_path / "absent-dist")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
    assert not inherited.exists()


def test_inherited_frontend_build_does_not_shadow_test_only_routes(tmp_path):
    # Run the actual route/queue assertions with an ordinary built frontend
    # present, rather than reproducing the fixture's env patch in this test.
    result, _inherited = _backend_run(tmp_path, [
        "tests/test_writeset.py::test_a_def_route_and_an_async_route_hand_their_writes_over",
        "tests/test_cache_warm.py::test_a_raising_route_hands_its_paths_to_the_real_queue",
    ], _dist(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout


def test_undo_keeps_the_inherited_library_and_build_outside_the_session_floor(tmp_path, monkeypatch):
    root = synthetic_suite.write(tmp_path / "suite", {"test_floor.py": '''
from fastapi.testclient import TestClient

from grimoire.main import create_app
from grimoire.store import characters, paths

def test_before_and_after_undo(monkeypatch):
    characters.create_character(paths.home(), "Seraphine", "main")
    monkeypatch.undo()
    characters.create_character(paths.home(), "Mara", "main")
    app = create_app()

    def late_route():
        return {"ok": True}

    app.add_api_route("/fixture-probe", late_route, methods=["PUT"])
    assert TestClient(app).put("/fixture-probe").status_code == 200
'''})
    # Load the actual shared fixtures in the child, without copying their
    # privacy patches or the synthetic harness's separate profiler hooks.
    (root / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(synthetic_suite.BACKEND)!r})\n"
        "pytest_plugins = ['tests.conftest']\n", encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_DIST", str(_dist(tmp_path)))
    result = synthetic_suite.run(root, "--basetemp", str(tmp_path / "pytest"), timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert not (root / "home").exists()
