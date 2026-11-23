"""Sprint 116's Authoring ops: a source's authoring tools for the library owner. Called as ``authoring.<op>``; see
docs/AUTHORING_PAGE.md.

Only Sprint 116 edits this package. Each op is ``handler(ctx, args) -> dict`` over an existing domain package (see
``grimoire.app.adapter``); nothing here is a record of its own. 087 keeps proposals and 082 keeps reports in memory
only, so this adapter holds them in the app's memory under server-chosen tokens (``common.Memory``): a restart loses
them.

- ``source``: the source, what 087 offers to link to, and what is available (``overview``).
- ``build``, ``suggest_plan``, ``suggest``, ``review``, ``draft``, ``apply``, ``reject`` (087): a world authoring
  proposal built by hand or suggested by one approved, estimated planning call; reviewed; a decision drafted into
  the exact change set; that change set written on the person's press, or the proposal discarded.
- ``audit``, ``audit_review`` (082): a bounded, read-only consistency audit of a world, and a check of its evidence.
- ``upgrades``, ``pin_plan``, ``pin`` (079): what removing a source would leave missing, its optional dependencies,
  and pinning one at its current version, reviewed first.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter
from grimoire.authoring import AuthoringError
from grimoire.changesets import ChangeSetError
from grimoire.investigations import InvestigationError
from grimoire.sourceupgrades import SourceUpgradeError

from . import audits, overview, proposals, upgrades

ADAPTER = Adapter(
    "authoring", owner="116",
    ops={"source": overview.source,
         "build": proposals.build, "suggest_plan": proposals.suggest_plan, "suggest": proposals.suggest,
         "review": proposals.review, "draft": proposals.draft, "apply": proposals.apply, "reject": proposals.reject,
         "audit": audits.audit, "audit_review": audits.audit_review,
         "upgrades": upgrades.upgrades, "pin_plan": upgrades.pin_plan, "pin": upgrades.pin},
    # what a domain refuses that no op above already answered with its own reason: never a server error
    refusals={AuthoringError: "authoring_refused", ChangeSetError: "changeset_refused",
              InvestigationError: "investigation_refused", SourceUpgradeError: "upgrade_refused"})

__all__ = ["ADAPTER"]
