"""map_lookup_errors should translate NoBackendAvailableError → 503."""

from __future__ import annotations

from grimoire.api.util import map_lookup_errors
from grimoire.imagegen.service import NoBackendAvailableError


def test_no_backend_available_error_maps_to_503() -> None:
    exc = NoBackendAvailableError("no plugin installed yet")
    http = map_lookup_errors(exc)
    assert http.status_code == 503
    assert "no plugin" in http.detail
