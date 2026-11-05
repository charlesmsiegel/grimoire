"""Synthetic worlds for the 027 per-NPC generation proofs. No real library content, no paid calls.

The Emberfall cast (014's voices) at a guild dinner: Bellamy privately saw a sealed letter change
hands, the duke's betrayal is a director-only secret, Wren is Alice's player character. A scripted
provider transport stands in for every model call.
"""

from __future__ import annotations

import json

from voice_fixtures import CAMPAIGN, PC, SECRETS, emberfall

from grimoire.core import Event, InstanceRef, Knowledge, Moment, Perception, actor_knowledge
from grimoire.core.inference import InferencePolicy, TurnUsageLedger
from grimoire.core.model import ActorView
from grimoire.generation import npc_brief, plan_generation_turn
from grimoire.providers import ProviderEndpoint, ProviderRegistry, RouteChoice, StaticCredentials
from grimoire.providers.adapter import ProviderAdapter
from grimoire.providers.meter import TurnMeter
from grimoire.runtime import GenerationRuntime
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.testing.providers import Reply, ScriptedClock, ScriptedTransport

ODA, BELLAMY, QUILL = (InstanceRef(CAMPAIGN, "character", actor) for actor in ("npc-oda", "npc-bellamy", "npc-quill"))
WREN = InstanceRef(CAMPAIGN, "character", PC)
PRIVATE = "A sealed letter passed under the duke's table"
TRUTH = "The duke sold the guild to the river lords"
DIRECTOR_POLICY = InferencePolicy({"director_generation": 1})

LEDGER = Knowledge(
    CAMPAIGN,
    events=[Event(CAMPAIGN, "letter", "A letter changed hands", Moment(1), "director"),
            Event(CAMPAIGN, "betrayal", TRUTH, Moment(1), "director")],
    evidence=[Perception(CAMPAIGN, "saw-letter", BELLAMY, "letter", "sight", PRIVATE, Moment(1), "director",
                         confirmed=True)])


def say(text: str) -> Reply:
    return Reply(deltas=(text,))


def view(actor: InstanceRef, name: str, *, description: str = "", knowledge=True) -> ActorView:
    return ActorView(CAMPAIGN, actor.instance_id, name, description,
                     knowledge=actor_knowledge(LEDGER, actor, as_of=1) if knowledge else None)


def world():
    _definitions, _catalog, _actors, controls, voices = emberfall()
    return controls, voices


def brief(actor: InstanceRef, name: str, voices, **kw):
    return npc_brief(view(actor, name), voices[actor.instance_id], secrets=kw.pop("secrets", SECRETS), **kw)


class Harness:
    """A scripted adapter and a runtime over it; a trusted app handle unless ``approve`` is set."""

    def __init__(self, steps, *, approve: bool = False, attempts: int = 1) -> None:
        self.authz = Authorizer({"human:owner"})
        principal = "app:worker" if approve else "app:runtime"
        self.authz.add_grant(Grant("g", principal, frozenset({Permission.EXECUTE}), ResourcePath(("providers",)),
                                   "human:owner"))
        self.handle = self.authz.handle_for(principal, trusted=not approve)
        self.approved: list[str] = []
        self.transport = ScriptedTransport(list(steps))
        clock = ScriptedClock()
        self.adapter = ProviderAdapter(ProviderRegistry([ProviderEndpoint("primary", "https://primary.example/v1")]),
                                       StaticCredentials({"primary": "sk-test-0000"}), self.authz, self.transport,
                                       clock=clock, wait=clock.wait)
        self.attempts = attempts

        def approvals(label, attempt, payload):
            approval_id = f"ap-{len(self.approved) + 1}"
            self.authz.register_approval(Approval(approval_id, principal, Permission.EXECUTE,
                                                  ResourcePath(("providers", label.split("@", 1)[0])),
                                                  payload_digest(payload), "human:owner"))
            self.approved.append(approval_id)
            return approval_id

        self.runtime = GenerationRuntime(self.adapter, (RouteChoice("primary", "model-a"),), self.handle,
                                         approvals=approvals if approve else None, max_output_tokens=64)

    def meter(self, plan) -> TurnMeter:
        return TurnMeter(TurnUsageLedger(plan))

    def plan(self, controls, *, present, speakers, director_reply=False, policy=None, turn="t1"):
        policy = policy or InferencePolicy(max_attempts_per_call=self.attempts)
        return plan_generation_turn(CAMPAIGN, turn, present=present, speakers=speakers, controls=controls,
                                    director_reply=director_reply, policy=policy)

    def prompts(self) -> list[str]:
        """Each sent request's message text, in send order."""
        return ["\n".join(message["content"] for message in json.loads(send.body)["messages"])
                for send in self.transport.sends]
