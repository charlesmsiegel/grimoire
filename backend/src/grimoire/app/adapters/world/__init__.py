"""Sprint 115's World adapter: a campaign's places and routes (059), rumors (063), faction decisions (057) and what
an NPC thinks others think (060), all the director's. Called as ``world.<op>``; see docs/WORLD.md.

Only Sprint 115 edits this package. Each op is ``handler(ctx, args) -> dict`` over the existing domain service
(``TopologyLibrary``, 063's rumor ledger with 020's ``EvidenceLibrary``, 057's ``decide``, 060's ``admit``), and
nothing here keeps a record of its own. Every op refuses ``director_only`` unless ``audience`` is ``director``,
so the table never reads any of it. The two writes (a road blocked or opened in one campaign, a rumor's recipient's
belief admitted) are each reviewed first, with a plan that writes nothing, and then made exactly as reviewed, with
the client's operation id and the revision it was shown: a retry is ``already_applied``, a change made meanwhile is
``changed``, an id reused for another change ``operation_conflict``. 057 and 060 are pure: their sections preview
the domain's answer for what the director lays out, and save nothing. Every domain refusal keeps its reason.
"""

from __future__ import annotations

from grimoire.app.adapter import Adapter

from . import factions, minds, places, rumors
from .common import DOMAIN_ERRORS, guarded

#: Each op and the reason a refusal with no machine-readable reason of its own is answered with.
_OPS = {**{name: guarded(op, "topology_refused") for name, op in places.OPS.items()},
        **{name: guarded(op, "rumor_refused") for name, op in rumors.OPS.items()},
        **{name: guarded(op, "institution_refused") for name, op in factions.OPS.items()},
        **{name: guarded(op, "hypothesis_refused") for name, op in minds.OPS.items()}}

ADAPTER = Adapter("world", owner="115", ops=_OPS, refusals={kind: "world_refused" for kind in DOMAIN_ERRORS})

__all__ = ["ADAPTER"]
