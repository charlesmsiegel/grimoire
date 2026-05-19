"""map_lookup_errors should translate RouteNotFoundError → 503,
including the case where the orchestrator wrapped it in an OrchestratorError.
"""

from __future__ import annotations

from grimoire.api.util import map_lookup_errors
from grimoire.llm_gateway.errors import ProviderNotFoundError, RouteNotFoundError
from grimoire.orchestrator.service import OrchestratorError


def test_route_not_found_maps_to_503_directly() -> None:
    exc = RouteNotFoundError("main")
    http = map_lookup_errors(exc)
    assert http.status_code == 503


def test_provider_not_found_maps_to_503_directly() -> None:
    exc = ProviderNotFoundError("llm-openrouter")
    http = map_lookup_errors(exc)
    assert http.status_code == 503


def test_route_not_found_wrapped_in_orchestrator_error_maps_to_503() -> None:
    """Without cause-chain inspection the OrchestratorError name forces 409 —
    even though the real cause ("no LLM provider configured") is a 503 signal.
    """
    try:
        raise RouteNotFoundError("main")
    except RouteNotFoundError as cause:
        outer = OrchestratorError(f"llm gateway failed for turn t_x: {cause}")
        outer.__cause__ = cause
        http = map_lookup_errors(outer)
    assert http.status_code == 503


def test_orchestrator_error_without_route_cause_still_409() -> None:
    """Genuine state-precondition OrchestratorErrors (e.g., "no active scene")
    must keep their 409 — the cause-chain check is targeted at gateway errors
    only.
    """
    exc = OrchestratorError("no active scene for pc 'x' in campaign 'y'")
    http = map_lookup_errors(exc)
    assert http.status_code == 409
