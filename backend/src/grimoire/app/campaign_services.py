"""113's production composition: an explicit labeling route, never narration authority.

The caller supplies the same stages/agendas to TextApi at construction. Both runners share its writer lock,
while the optional label policy is independent of 112's editable narration configuration.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping

from grimoire.campaignapi import CampaignApi
from grimoire.core.inference import InferencePolicy
from grimoire.providers import ProviderRegistry, RouteChoice, StaticCredentials
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.transport import HttpTransport
from grimoire.runtime import LabelRuntime
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.textapi import TextApi

from .campaign_config import CampaignConfig, CampaignConfigError

LABEL_PRINCIPAL = "app:campaign-labeller"
PROPOSAL_GRANT = "app-campaign-labeller-proposals"


def _proposal_handle(api: TextApi):  # type: ignore[no-untyped-def]
    wanted = Grant(PROPOSAL_GRANT, LABEL_PRINCIPAL, frozenset({Permission.PROPOSE}),
                   ResourcePath(("campaigns",)), api.owner)
    relevant = [g for g in api.authority.active_grants()
                if g.grant_id == PROPOSAL_GRANT or g.principal == LABEL_PRINCIPAL]
    if relevant:
        if relevant != [wanted]:
            raise CampaignConfigError("campaign labeller authority differs from its PROPOSE-only contract")
    else:
        try:
            # DurableAuthority never reuses an id, including a revoked grant: startup fails rather than
            # resurrecting it. The kernel validates before changing anything.
            api.authority.add_grant(wanted)
        except ValueError:
            raise CampaignConfigError("campaign labeller proposal authority was revoked or cannot be issued") from None
    return api.authority.handle_for(LABEL_PRINCIPAL, trusted=False)


def validate_credentials(config: CampaignConfig, environ: Mapping[str, str] | None = None) -> None:
    """Refuse before a setup listener can create a library for an unusable paid route."""
    model = config.labelling
    env = os.environ if environ is None else environ
    if model is not None and model.key_env is not None and not env.get(model.key_env):
        raise CampaignConfigError("campaign labelling credential is missing from the configured environment")


def configure(api: TextApi, config: CampaignConfig, *, transport=None,
              environ: Mapping[str, str] | None = None) -> CampaignApi:  # type: ignore[no-untyped-def]
    """Configure once, before serving. Only this explicitly opted-in route can call the label provider.

    The provider authority is separate from the durable library authority: it grants EXECUTE only at the
    configured provider, with payload-bound single-use approval per requested attempt. The library handle
    can PROPOSE only. Neither is the owner's handle or the adjudicator's key.
    """
    with api.lock:
        if getattr(api, "_campaign_hub", None) is not None:
            raise CampaignConfigError("campaign services are already configured")
        model = config.labelling
        labeller = label_handle = policy = metadata = None
        if model is not None:
            env = os.environ if environ is None else environ
            key = env.get(model.key_env) if model.key_env is not None else None
            validate_credentials(config, env)
            authorizer = Authorizer({api.owner})
            scope = ResourcePath(("providers", model.endpoint.provider_id))
            authorizer.add_grant(Grant("campaign-label-provider", LABEL_PRINCIPAL,
                                       frozenset({Permission.EXECUTE}), scope, api.owner))
            provider_handle = authorizer.handle_for(LABEL_PRINCIPAL, trusted=False)
            credentials = StaticCredentials({model.endpoint.provider_id: key} if key is not None else {})
            adapter = ProviderAdapter(ProviderRegistry([model.endpoint]), credentials, authorizer,
                                      HttpTransport() if transport is None else transport)

            def approve(route, attempt, payload):  # type: ignore[no-untyped-def]
                approval_id = "label-" + uuid.uuid4().hex
                authorizer.register_approval(Approval(approval_id, LABEL_PRINCIPAL, Permission.EXECUTE,
                                                      scope, payload_digest(payload), api.owner))
                return approval_id

            labeller = LabelRuntime(adapter, tuple(RouteChoice(model.endpoint.provider_id, value) for value in model.models),
                                    provider_handle, approvals=approve, max_output_tokens=model.max_output_tokens)
            label_handle = _proposal_handle(api)
            policy = InferencePolicy({"labelling": 1}, max_attempts_per_call=model.attempts)
            metadata = {"provider": model.endpoint.provider_id, "model": model.model,
                        "fallbacks": list(model.fallbacks), "attempts": model.attempts,
                        "max_output_tokens": model.max_output_tokens, "settings": "campaign startup configuration"}
        hub = CampaignApi(api.store, api.authority, owner=api.owner, handle=api.owner_handle, lock=api.lock,
                          labeller=labeller, label_handle=label_handle, policy=policy,
                          stages=config.stages, agendas=config.agendas)
        hub.labelling_config = metadata
        api._campaign_hub = hub
        return hub
