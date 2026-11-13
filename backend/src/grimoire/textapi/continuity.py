"""047's composition seam: model-derived reviews can propose, never apply fictional state.

The owner opts in through provider configuration. A fixed grant is installed once; a revoked
one is never replaced by loading settings again. The runner still checks the exact proposal
payload at commit time. This handle cannot execute providers or adjudicate outcomes.
"""

from grimoire.security import Grant, Permission, ResourcePath

EXTRACTOR_PRINCIPAL = "app:continuity-extractor"
PROPOSAL_GRANT = "app-continuity-proposals"


def configure_extractor(api, extractor) -> None:
    """Install/clear the separate runtime and this library's untrusted proposal handle."""
    api.runner.extractor = extractor
    api.runner.extract_handle = None
    if extractor is None:
        return
    authority = api.authority
    grant = Grant(PROPOSAL_GRANT, EXTRACTOR_PRINCIPAL, frozenset({Permission.PROPOSE}),
                  ResourcePath(("campaigns",)), api.owner)
    with api.store.lock:
        active = {entry.grant_id: entry for entry in authority.active_grants()}
        if PROPOSAL_GRANT not in active:
            try:
                authority.add_grant(grant)
            except ValueError as error:
                # DurableAuthority intentionally exposes only active grants. A reused ID may
                # be revoked: never invent a new ID to revive that denied permission.
                if str(error) != "grant ids are never reused":
                    raise
        elif active[PROPOSAL_GRANT] != grant:
            raise ValueError("continuity proposal grant belongs to another scope")
        api.runner.extract_handle = authority.handle_for(EXTRACTOR_PRINCIPAL, trusted=False)
