"""Explicit browser suite; ordinary backend/tests collection does not import it."""
import contextlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest
from playwright.sync_api import sync_playwright, expect

from test_app_release_proof import App, OWNER, SRC, model_server, voiced_v1_library
from grimoire.testing.fileops import snapshot

HERE = Path(__file__).resolve().parent


class OfflineApp(App):
    def __init__(self, home, *args):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GRIMOIRE") and not key.endswith("_API_KEY")}
        environment.update(PYTHONPATH=os.pathsep.join((str(HERE), str(SRC))),
                           HOME=str(home), USERPROFILE=str(home),
                           GRIMOIRE_V2_HOME=str(home / ".grimoire-v2"))
        self.errors = tempfile.TemporaryFile("w+", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "offline_app", "serve", *args, "--owner", OWNER, "--port", "0"],
            stdout=subprocess.PIPE, stderr=self.errors, text=True, env=environment)
        try:
            self.port, self.token, self.sync_port = self._started()
        except BaseException:
            self.stop()
            raise


class Session:
    def __init__(self, root, browser, model, v1):
        self.home, self.browser, self.model, self.v1 = root, browser, model, v1
        root.mkdir()
        self.context = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        self.blocked, self.errors = [], []
        self.app = None
        self.context.route("**/*", self.route)
        self.context.set_default_timeout(8000)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        try:
            self.start()
        except BaseException:
            self.close()
            raise

    def route(self, route):
        if self.app and route.request.url.startswith(f"http://127.0.0.1:{self.app.port}/"):
            route.continue_()
        else:
            self.blocked.append(route.request.url)
            route.abort()

    def start(self, reopen=False):
        args = ([str(self.home / ".grimoire-v2")] if reopen else [])
        self.app = OfflineApp(self.home, *args, "--model", str(self.home / "model.json"),
                              *(["--sync-port", "0"] if reopen else []))
        self.page.goto(f"http://127.0.0.1:{self.app.port}/#token={self.app.token}")

    def restart(self):
        self.page.goto("about:blank")
        errors = self.app.stop()
        self.app = None
        assert "an op failed" not in errors, errors
        self.start(reopen=True)
        expect(self.page.get_by_role("main").get_by_role("heading", level=2).first).to_be_visible()

    @contextlib.contextmanager
    def new_client(self, name):
        peer = Session(self.home.parent / name, self.browser, self.model, self.v1)
        try:
            yield peer
        finally:
            peer.close()

    def close(self):
        try:
            self.context.close()
        finally:
            errors = self.app.stop() if self.app else ""
            self.app = None
        assert "an op failed" not in errors, errors
        assert not self.blocked, "External browser requests refused: " + repr(self.blocked)
        assert not self.errors, "Browser exceptions: " + repr(self.errors)


@pytest.fixture
def session(tmp_path):
    v1 = voiced_v1_library(tmp_path / "synthetic-v1")
    before = snapshot(v1)
    executable = os.environ.get("GRIMOIRE_TEST_CHROMIUM") or shutil.which("chromium")
    with model_server() as model, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=executable)
        current = None
        try:
            current = Session(tmp_path / "desktop", browser, model, v1)
            yield current
        finally:
            try:
                if current:
                    current.close()
            finally:
                browser.close()
                assert snapshot(v1) == before, "The read-only synthetic V1 source changed"
