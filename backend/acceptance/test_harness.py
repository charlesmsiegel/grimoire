"""Failure-injection checks for the acceptance harness itself."""
import subprocess
import sys

import pytest
from playwright.sync_api import Page

import conftest as harness


def test_failed_initial_navigation_stops_child(session, monkeypatch):
    children = []
    original = harness.OfflineApp

    class TrackedApp(original):
        def __init__(self, *args):
            super().__init__(*args)
            children.append(self.process)

    def broken_navigation(*args, **kwargs):
        raise RuntimeError("synthetic navigation failure")

    monkeypatch.setattr(harness, "OfflineApp", TrackedApp)
    monkeypatch.setattr(Page, "goto", broken_navigation)
    with pytest.raises(RuntimeError, match="synthetic navigation failure"):
        harness.Session(session.home.parent / "failed-start", session.browser, session.model, session.v1)
    assert children and all(child.poll() is not None for child in children)


def test_backend_guard_refuses_external_socket_variants():
    # The child guard installs before app imports and rejects without DNS or outbound packets.
    code = """
import offline_app
import socket
offline_app.install()
for host in ('example.com', '192.0.2.1', '::ffff:192.0.2.1'):
    for operation in ('connect', 'connect_ex'):
        with socket.socket() as sock:
            try:
                getattr(sock, operation)((host, 443))
            except PermissionError:
                pass
            else:
                raise AssertionError((host, operation))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
