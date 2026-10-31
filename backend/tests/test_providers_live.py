"""One opt-in, billed call to a real provider. Never runs in CI.

Set GRIMOIRE_V2_LIVE_PROVIDER_BASE_URL, GRIMOIRE_V2_LIVE_PROVIDER_MODEL,
GRIMOIRE_V2_LIVE_PROVIDER_KEY_ENV (the *name* of the variable holding the key)
and GRIMOIRE_V2_LIVE_PROVIDER_CONFIRM=billed. No proxy support: the endpoint
must be directly reachable.
"""

import os

import pytest

from grimoire.core.inference import TurnUsageLedger, plan_turn
from grimoire.providers import (
    ChatMessage, EnvironmentCredentials, ModelRequest, ProviderEndpoint, ProviderRegistry,
    RouteChoice,
)
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.meter import TurnMeter
from grimoire.providers.transport import HttpTransport
from grimoire.security import Authorizer, Grant, Permission, ResourcePath

PREFIX = "GRIMOIRE_V2_LIVE_PROVIDER_"
SETTINGS = {name: os.environ.get(PREFIX + name) for name in ("BASE_URL", "MODEL", "KEY_ENV",
                                                             "CONFIRM")}


@pytest.mark.skipif(SETTINGS["CONFIRM"] != "billed" or not all(SETTINGS.values()),
                    reason="live provider calls are opt-in and billed")
def test_live_provider_call():
    authz = Authorizer({"human:tester"})
    authz.add_grant(Grant("live", "app:live-test", frozenset({Permission.EXECUTE}),
                          ResourcePath(("providers", "live")), "human:tester"))
    adapter = ProviderAdapter(
        ProviderRegistry([ProviderEndpoint("live", SETTINGS["BASE_URL"])]),
        EnvironmentCredentials({"live": SETTINGS["KEY_ENV"]}), authz, HttpTransport())
    plan = plan_turn("live", "t1", present_actor_ids=["npc"], speaking_npc_ids=["npc"])
    meter = TurnMeter(TurnUsageLedger(plan))
    result = adapter.execute(
        meter, plan.calls[0],
        ModelRequest([ChatMessage("user", "Reply with the single word: ready")],
                     max_output_tokens=16),
        [RouteChoice("live", SETTINGS["MODEL"])], authz.handle_for("app:live-test", trusted=True))
    assert result.status == "accepted", result
    assert meter.summary().provider_reported_only, result.attempts
