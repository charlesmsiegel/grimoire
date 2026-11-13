"""The campaign hub's API (113): campaign state, adjudication and effect review as JSON ops.

``CampaignApi`` is trusted application code over one open library, acting for its owner, who is both the
table's one player and its director in this slice (one person, one library, as 044). It keeps no record of its
own and writes only through the services that own each record:

- 035's ``SceneRunner``, configured with 029's ``AdjudicationLibrary`` and 040's ``ObstacleLibrary`` on the
  app's own adjudication key, and 021's ``TurnEffectsLibrary`` on the owner's handle: labelling, confirming and
  deciding an action, proposing an effect, each key on it, and recovering an acceptance cut short;
- nothing else: holdings, items, party, stage, knowledge, relations, accepted events, powers, bindings and
  agendas are only read.

**Two keys, two calls.** A player's own key on an effect (``endorse_effect``) and the director's
(``approve_effect``) are separate ops the person makes separately, even when one person holds both (021's
"solo play" rule): the hub never gives one for the other. A consent to a setback is the player's own
``decide`` with ``basis: consent`` naming the frame's fingerprint, as 035 requires.

**Who sees what.** ``audience`` is ``table`` (default) or ``director``. The table sees the cast, control, the
stage, its own characters' party standing, holdings, items, powers, source pins and knowledge, the obstacles
(040 has no hidden facts), every turn's adjudication and the effects it holds a key on. The director also sees
world truth (021's accepted events, 017's relations and 012's standing of every proposition beside what the
cast believes of it), every effect, membership, holding, item, power and binding, 021's problems, the app's
039 agendas and each turn's recorded intentions, and any one actor's perspective by asking for it. An actor's
perspective is that actor's observations and beliefs, never world truth, and is never folded into the table's.
A character pinned behind its source's head is handed to the review screen's rebase review (110, over 042):
the hub names the op and rebases nothing. See docs/CAMPAIGN_HUB.md.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.core import DIRECTOR
from grimoire.core.abilities import Abilities
from grimoire.core.authority import AuthorityError
from grimoire.core.index import MissingDefinition
from grimoire.core.inference import InferencePolicy
from grimoire.core.identity import ChatRef
from grimoire.core.perspective import actor_knowledge, director_knowledge
from grimoire.effective import chat_snapshot
from grimoire.effectstore import TurnEffectsLibrary
from grimoire.evidencestore import EvidenceLibrary
from grimoire.goals import GoalError, check_agenda
from grimoire.holdingstore import HoldingsLibrary
from grimoire.obstaclestore import ObstacleLibrary
from grimoire.partystore import PartyLibrary
from grimoire.play import SceneRunner
from grimoire.play.scene import NOW
from grimoire.possessionstore import PossessionLibrary
from grimoire.scene import SceneRefused
from grimoire.store import LibraryStore, StaleWrite, StoreError
from grimoire.textapi import ApiRefused
from grimoire.textapi.api import ADJUDICATOR  # the app's own adjudication key (029/040), shared with the chat

from . import parse
from .views import (
    Names,
    abilities_view, actor_ref, agenda_view, claim_view, effect_view, event_view, framed_view, holdings_view,
    items_view, knowledge_view, label_view, obstacle_view, outcome_view, relation_view, source_view, stage_view,
    turn_view,
)

AUDIENCES = ("table", "director")
MAX_SUMMARY = 4000

#: What the hub shows as not available yet, and the work that would make it so. Never offered as an action.
UNAVAILABLE = (
    {"feature": "revoke_effect", "blocker": "035",
     "reason": "the scene runner offers no revocation of an accepted effect yet; abort a cut-short acceptance "
               "through recovery instead"},
    {"feature": "ability_use", "blocker": "035",
     "reason": "the scene runner frames no ability use and refuses one on an effect in this slice"},
    {"feature": "proposed_by_narration", "blocker": "035/047",
     "reason": "no model proposes an effect from narration yet; effects are proposed here, in structured form"},
    {"feature": "campaign_ledger", "blocker": "053", "reason": "memory consolidation (the long-term ledger) is Wave 8"},
    {"feature": "story_graph", "blocker": "081", "reason": "story graph and navigation is Wave 9"},
    {"feature": "clocks", "blocker": "055", "reason": "calendar and clocks are Wave 8"},
    {"feature": "npc_autonomy", "blocker": "039",
     "reason": "NPC agendas are the app's configuration; no screen edits them in this slice"},
    {"feature": "goal_progress", "blocker": "039",
     "reason": "stored agendas, goal progress and completion are 039's next slice; the hub shows the configured "
               "agendas and each turn's recorded intentions"},
    {"feature": "rebase_scenario", "blocker": "042",
     "reason": "rebasing a pinned scenario or lorebook is a later 042 milestone; a character behind its source's "
               "head is handed to the review screen's rebase review"},
    {"feature": "stored_change_sets", "blocker": "043",
     "reason": "stored change-set proposals are 043 milestone 3; a change set is reviewed from its document in "
               "the review screen"},
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _refused(error: Exception) -> ApiRefused:
    reason = getattr(error, "reason", None)
    return ApiRefused(reason if isinstance(reason, str) and reason else type(error).__name__, str(error))


class CampaignApi:
    """The ops of the campaign hub over one open library. ``owner`` is the library owner (a ``human:``
    principal), who plays and directs. ``labeller``, ``label_handle`` and ``policy`` are the app's 035
    labelling runtime, the untrusted handle this library's authority issued it, and the inference policy that
    allows its ``labelling`` call (without all three, ``label`` is unavailable).
    ``stages`` is the app's 041 stage source. ``lock`` is the lock the app's other APIs over the same library
    hold, so that one library has one writer at a time; without it this API has a lock of its own (the store's
    own locks and every service's etags still refuse a stale write). ``handle`` is the owner's trusted handle the
    app already holds (asked of ``authority`` when not given). The library owner alone may construct it."""

    def __init__(self, store: LibraryStore, authority, *, owner: str, labeller=None, label_handle=None,  # type: ignore[no-untyped-def]
                 policy: InferencePolicy | None = None, stages=None, agendas=(),
                 lock: threading.RLock | None = None, adjudicator: str = ADJUDICATOR, handle=None,
                 clock: Callable[[], str] = _utc_now, new_id: Callable[[str], str] = _new_id) -> None:
        if owner not in authority.owners:  # the director view and every write are the library owner's
            raise ValueError("the campaign hub acts for this library's owner")
        self.store = store
        self.authority = authority
        self.owner = owner
        # the owner's trusted handle: the app's own (108's ``AdapterContext.handle``) when it passes one
        self.handle = authority.handle_for(owner, trusted=True) if handle is None else handle
        if getattr(self.handle, "principal", None) != owner or getattr(self.handle, "trusted", False) is not True:
            raise ValueError("the campaign hub writes with its owner's own trusted handle")
        key = authority.handle_for(adjudicator, trusted=True)
        self.adjudication = AdjudicationLibrary(store, authority, key)
        self.obstacles = ObstacleLibrary(store, authority, key)
        self.effects = TurnEffectsLibrary(store, self.handle, issued=authority.issued, clock=clock, new_id=new_id,
                                          owner=owner)
        agendas = tuple(agendas) if not isinstance(agendas, (str, bytes, Mapping)) else agendas
        # the runner checks the agendas (039's exact values, one per NPC); the hub only shows them to the director
        self.runner = SceneRunner(store, authority, self.handle, owner=owner, labeller=labeller,
                                  label_handle=label_handle, policy=policy, adjudication=self.adjudication,
                                  effects=self.effects, obstacles=self.obstacles, stages=stages, agendas=agendas)
        self.agendas = {agenda.actor: agenda for agenda in agendas}
        self.labelling = labeller is not None and label_handle is not None and policy is not None
        self.evidence = EvidenceLibrary(store, self.handle, owner=owner)
        self.holdings = HoldingsLibrary(store, self.handle, clock=clock, owner=owner)
        self.items = PossessionLibrary(store, self.handle, clock=clock, owner=owner)
        self.party = PartyLibrary(store, self.handle, clock=clock, new_id=new_id, owner=owner)
        self._lock = lock if lock is not None else threading.RLock()
        self._ops: dict[str, Callable[[Mapping[str, object]], dict]] = {
            "hub": self._hub_op, "knowledge": self._knowledge, "label": self._label, "confirm": self._confirm,
            "decide": self._decide, "propose_effect": self._propose, "endorse_effect": self._endorse,
            "approve_effect": self._approve, "recover_effects": self._recover,
        }

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(sorted(self._ops))

    # -- dispatch ----------------------------------------------------------------------------------------
    def call(self, op: str, args: Mapping[str, object] | None = None) -> dict:
        """Run one op. Raises ``ApiRefused``; any other exception is a bug the caller reports as such."""
        handler = self._ops.get(op) if isinstance(op, str) else None
        if handler is None:
            raise ApiRefused("unknown_op", f"no op {op!r}")
        if args is None:
            args = {}
        if not isinstance(args, Mapping):
            raise ApiRefused("invalid_field", "the arguments must be a JSON object")
        with self._lock:
            try:
                return handler(args)
            except ApiRefused:
                raise
            except SceneRefused as error:
                raise _refused(error) from None
            except StaleWrite as error:
                raise ApiRefused("changed", f"the library changed meanwhile; nothing was written ({error})") from None
            except StoreError as error:
                raise ApiRefused(type(error).__name__, str(error)) from None
            except AuthorityError as error:
                raise ApiRefused("not_authorized", str(error)) from None

    # -- reading -----------------------------------------------------------------------------------------
    def _audience(self, args: Mapping[str, object]) -> str:
        audience = parse.field(args, "audience", str, required=False, default="table")
        if audience not in AUDIENCES:
            raise ApiRefused("invalid_field", "audience must be table or director")
        return audience

    def _hub_op(self, args: Mapping[str, object]) -> dict:
        return self.hub(parse.chat_of(args), self._audience(args))

    def hub(self, chat: ChatRef, audience: str = "table") -> dict:
        """The chat's campaign situation for ``audience``: see the module docstring."""
        scene = self.runner.open(chat)
        campaign = chat.campaign_id
        names = scene.names.get
        director = audience == "director"
        # 021's problems name accepted events of the whole campaign: the director's, shown once, in ``truth``
        problems = [{"code": code, "detail": detail} for code, detail in scene.problems if code != "effects"]
        on_stage = set(scene.on_stage)
        # control is recorded, not read from the present cast: a character set in reserve leaves the cast (033)
        # and stays its player's
        mine = {item.actor_id for item in scene.controls
                if item.campaign_id == campaign and item.controller_id == self.owner}
        stage = scene.stage
        cast = []
        for actor in scene.cast:
            controller = scene.controller(actor)
            absence = None if stage is None else stage.absence(actor)
            cast.append({"actor": str(actor), "name": names(actor), "controller": controller,
                         "role": "unseated" if controller is None else "npc" if controller == DIRECTOR else "player",
                         "on_stage": actor in on_stage,
                         "absent": None if absence is None else {"via": absence.via, "basis": absence.basis}})
        party, members = self._party(campaign, scene, names, problems, None if director else mine)
        shown = [a for a in scene.cast if director or a.instance_id in mine]
        shown += [a for a in members if a not in shown and (director or a.instance_id in mine)]
        holdings, items = self._holdings(shown, problems), self._items(campaign, shown, problems)
        obstacles = self._obstacles(campaign, names, problems)
        turns = [turn_view(record, names, None if director else self.owner) for record in scene.records
                 if record.labels or record.effects or (record.open and record.post is not None)
                 or (director and record.intentions)]
        view, captured = self._captured(chat, problems)
        abilities = self._abilities(captured, shown, problems)
        sources = self._sources(captured, names, None if director else mine, problems)
        hub = {"chat": str(chat), "audience": audience, "director": self.owner, "player": self.owner,
               "labelling_config": getattr(self, "labelling_config", None),
               "cast": cast, "persona": None if scene.persona is None else str(scene.persona),
               "paused": scene.paused, "stage": stage_view(stage, names), "party": party,
               "holdings": holdings, "items": items, "abilities": abilities, "sources": sources,
               "obstacles": obstacles, "turns": turns,
               "open_action": self._open_action(chat, scene, names, problems), "problems": problems,
               "actions": self._actions(scene.paused), "unavailable": self._unavailable(stage)}
        if director:
            ledger = self._ledger(campaign, problems)
            hub["truth"] = self._truth(campaign, scene, names, problems, ledger)
            hub["goals"] = self._goals(campaign, scene, view, names, ledger)
        return hub

    def _captured(self, chat: ChatRef, problems: list):  # type: ignore[no-untyped-def]
        """The library as loaded now and 022's snapshot of the chat over it, or ``(None, None)`` with a problem."""
        try:
            view = self.store.load()
            return view, chat_snapshot(view, chat)
        except (ValueError, LookupError, StoreError) as error:
            problems.append({"code": "snapshot_unreadable", "detail": type(error).__name__})
            return None, None

    def _abilities(self, captured, actors, problems: list) -> dict:  # type: ignore[no-untyped-def]
        """Each shown actor's effective powers (025), from the chat's snapshot, a character in reserve included:
        an NPC's are the director's alone."""
        if captured is None:
            return {}
        found = {}
        for actor in actors:
            try:
                entity = captured.snapshot.entity(actor)
            except (ValueError, MissingDefinition) as error:  # an actor whose definition cannot be composed now
                if getattr(error, "reason", None) == "unknown_instance":
                    continue  # a party member this chat never bound: its powers are another chat's to show
                problems.append({"code": "abilities_unreadable",
                                 "detail": f"{actor}: {getattr(error, 'reason', type(error).__name__)}"})
                continue
            found[str(actor)] = abilities_view(entity.facets.get(Abilities))
        return found

    def _sources(self, captured, names, own, problems: list) -> list[dict]:  # type: ignore[no-untyped-def]
        """The chat's active bindings, their pinned revisions and their sources' heads; for the table only its
        own characters' (``own``), for the director every binding. A character behind its source's head is
        handed to the review screen's rebase review (110); nothing here rebases."""
        if captured is None:
            return []
        snapshot = captured.snapshot
        resolutions = {(item.source.role, item.source.ref.entity_key): item for item in snapshot.sources()}
        found = []
        for binding in captured.chat.active_bindings():
            actor = binding.actor
            if own is not None and (actor is None or actor.instance_id not in own):
                continue
            try:
                checked = self.store.check_definition(binding.ref.unpinned())
            except (ValueError, StoreError) as error:
                checked = type(error).__name__
            head = None if isinstance(checked, str) else str(checked.ref)
            try:
                notices = () if actor is None else snapshot.instance_notices(actor)
            except ValueError as error:
                notices = ()
                problems.append({"code": "source_unreadable",
                                 "detail": f"{actor}: {getattr(error, 'reason', type(error).__name__)}"})
            found.append(source_view(binding, resolutions.get((binding.role, binding.ref.entity_key)), head,
                                     checked if isinstance(checked, str) else None, notices, names))
        return found

    def _ledger(self, campaign: str, problems: list):  # type: ignore[no-untyped-def]
        """020's stored ledger, the director's, or ``None`` with a problem. Damage is said, never hidden."""
        try:
            stored = self.evidence.read(campaign, requested_by=DIRECTOR)
        except (ValueError, StoreError) as error:
            problems.append({"code": "knowledge_unreadable", "detail": getattr(error, "reason", type(error).__name__)})
            return None
        if stored.quarantined or stored.problems:
            problems.append({"code": "knowledge_damaged",
                             "detail": f"{len(stored.quarantined)} quarantined, {len(stored.problems)} problems"})
        return stored

    def _claims(self, scene, names, ledger) -> list[dict]:  # type: ignore[no-untyped-def]
        """Every proposition with canon's standing and what each cast actor believes of it now (director only)."""
        if ledger is None:
            return []
        knowledge = ledger.knowledge
        truth = director_knowledge(knowledge, as_of=NOW, requested_by=DIRECTOR)
        held: dict[str, list] = {}
        for actor in scene.cast:
            try:
                known = actor_knowledge(knowledge, actor, as_of=NOW)
            except ValueError:  # an actor the ledger cannot project holds nothing here
                continue
            for belief in known.current:
                held.setdefault(belief.proposition_id, []).append((actor, belief.stance))
        shapes = ledger.claims.shapes
        return [claim_view(item, shapes.get(item.proposition.proposition_id),
                           held.get(item.proposition.proposition_id, ()), names) for item in truth.propositions]

    @staticmethod
    def _complete(ledger) -> bool:  # type: ignore[no-untyped-def]
        """Whether every record of the ledger was read: a lost ruling would show a claim as merely reported."""
        return ledger is not None and not ledger.quarantined and not ledger.problems

    def _goals(self, campaign: str, scene, view, names, ledger) -> list[dict]:  # type: ignore[no-untyped-def]
        """The app's 039 agendas for this chat's cast, each checked as play would use it now (director only):
        only a voiced director NPC on stage speaks, and its agenda must check against the whole ledger."""
        actors = None if view is None else [ref for ref in view.instances if ref.campaign_id == campaign]
        voiceless = {detail.rsplit(":", 1)[0] for code, detail in scene.problems if code == "no_voice"}
        on_stage = set(scene.on_stage)
        found = []
        for actor in scene.cast:
            agenda = self.agendas.get(actor)
            if agenda is None:
                continue
            problem = None
            if scene.controller(actor) != DIRECTOR:
                problem = "not_an_npc"  # a player character's intentions are its player's
            elif actor not in on_stage:
                problem = "not_on_stage"
            elif str(actor) in voiceless:
                problem = "no_voice"
            elif not self._complete(ledger) or actors is None:
                problem = "ledger_damaged"
            else:
                try:
                    check_agenda(agenda, propositions=[p.proposition_id for p in ledger.knowledge.propositions],
                                 actors=actors)
                except GoalError as error:
                    problem = error.reason
            found.append(agenda_view(agenda, names, problem))
        return found

    def _party(self, campaign: str, scene, names, problems: list, own):  # type: ignore[no-untyped-def]
        """Who is present, and 033's memberships: for the table only its own characters' (``own``: the actor ids
        its player controls), for the director all. Also the members shown, in or out of the present cast."""
        staged = scene.staged
        members: list = []
        try:
            view = self.party.view(campaign, as_of=NOW)
        except (ValueError, StoreError) as error:
            problems.append({"code": "party_unreadable", "detail": type(error).__name__})
            memberships = []
        else:
            def mine(actor) -> bool:  # type: ignore[no-untyped-def]
                return own is None or (actor.campaign_id == campaign and actor.instance_id in own)

            members = sorted((member for member in view.state.memberships if mine(member)), key=str)
            memberships = [{"member": actor_ref(item.member, names), "party": actor_ref(item.party, names),
                            "status": item.status, "since": item.since.sequence}
                           for member, item in sorted(view.state.memberships.items(), key=lambda pair: str(pair[0]))
                           if mine(member)]
            for problem in view.state.problems:
                if problem.member is None or mine(problem.member):
                    problems.append({"code": f"party_{problem.code}",
                                     "detail": "" if problem.member is None else str(problem.member)})
            if own is None:
                problems.extend({"code": f"party_{item.code}", "detail": item.path} for item in view.problems)
        return {"present": None if staged is None else [actor_ref(a, names) for a in staged.present],
                "memberships": memberships}, members

    def _holdings(self, shown, problems: list) -> dict:  # type: ignore[no-untyped-def]
        found = {}
        for actor in shown:
            try:
                found[str(actor)] = holdings_view(self.holdings.view(actor))
            except (ValueError, StoreError) as error:
                problems.append({"code": "holdings_unreadable", "detail": f"{actor}: {type(error).__name__}"})
        return found

    def _items(self, campaign: str, shown, problems: list) -> dict:  # type: ignore[no-untyped-def]
        try:
            items = self.items.campaign_items(campaign)
        except (ValueError, StoreError) as error:
            problems.append({"code": "items_unreadable", "detail": type(error).__name__})
            return {}
        if not items.complete:
            problems.append({"code": "items_incomplete", "detail": "some item records cannot be read"})
        return {str(actor): items_view(items.held_by(actor), items.equipped_by(actor)) for actor in shown}

    def _obstacles(self, campaign: str, names, problems: list) -> list:  # type: ignore[no-untyped-def]
        try:
            return [obstacle_view(state, names) for state in self.obstacles.states(campaign)]
        except (ValueError, StoreError) as error:
            problems.append({"code": "obstacles_unreadable", "detail": getattr(error, "reason", type(error).__name__)})
            return []

    def _open_action(self, chat: ChatRef, scene, names, problems: list) -> dict | None:  # type: ignore[no-untyped-def]
        pending = scene.pending
        if pending is None:
            return None
        latest = pending.labels[-1] if pending.labels else None
        framed = outcome = None
        try:
            framed = self.runner.framed(chat)
            outcome = self.runner.outcome(chat)
        except SceneRefused as error:  # 029's ledger cannot be read: say so, decide nothing
            problems.append({"code": error.reason, "detail": str(error)})
        actor = None if pending.post is None else pending.post.actor
        return {"turn": pending.turn_id, "actor": actor_ref(actor, names),
                "expected": scene.turns.etags().get(pending.turn_id),
                "label": None if latest is None else label_view(latest),
                "framed": framed_view(framed, names), "outcome": outcome_view(outcome, names)}

    def _truth(self, campaign: str, scene, names, problems: list, ledger) -> dict:  # type: ignore[no-untyped-def]
        """World truth, for the director: 021's accepted events, the cast's 017 relations and 012's standing of
        every proposition beside what the cast believes of it."""
        claims = self._claims(scene, names, ledger)
        try:
            continuity = self.effects.continuity(campaign, as_of=NOW, requested_by=DIRECTOR)
        except (ValueError, StoreError) as error:
            problems.append({"code": "effects_unreadable", "detail": getattr(error, "reason", type(error).__name__)})
            return {"events": [], "revoked": [], "relations": [], "claims": claims,
                    "claims_complete": self._complete(ledger)}
        for problem in continuity.problems:
            problems.append({"code": "effects", "detail": f"{problem.code}:{problem.event_id or ''}"})
        relations, seen = [], set()
        for actor in scene.cast:
            try:
                found = continuity.graph.index.relations(actor, as_of=NOW, requested_by=DIRECTOR)
            except ValueError:  # an actor the graph has no node for holds no relation
                continue
            for entry in found:
                relation = getattr(entry, "relation", entry)
                key = relation.ref.relation_id
                if key not in seen:
                    seen.add(key)
                    relations.append(relation_view(relation, names))
        return {"events": [event_view(event, names) for event in continuity.events],
                "revoked": [event_view(event, names) for event in continuity.revoked],
                "relations": sorted(relations, key=lambda item: (item["since"], item["relation"])),
                "claims": claims, "claims_complete": self._complete(ledger)}

    def _actions(self, paused: bool) -> list[dict]:
        """What can be done from the hub now, each implemented; a disabled one says why. A pause holds every
        step of play (035); recovery of an acceptance cut short is never held by one."""
        found = []
        for op in ("label", "confirm", "decide", "propose_effect", "endorse_effect", "approve_effect"):
            if op == "label" and not self.labelling:
                found.append({"op": op, "available": False, "reason": "no labelling model is configured for this app"})
            elif paused:
                found.append({"op": op, "available": False, "reason": "play is paused"})
            else:
                found.append({"op": op, "available": True})
        return [*found, {"op": "recover_effects", "available": True}]

    def _unavailable(self, stage) -> list[dict]:  # type: ignore[no-untyped-def]
        found = [dict(item) for item in UNAVAILABLE]
        if stage is None:
            found.append({"feature": "stage", "blocker": "041",
                          "reason": "this chat has no staged positions: the cast alone decides who is present"})
        return found

    def _knowledge(self, args: Mapping[str, object]) -> dict:
        """One perspective. With no ``actor``, the table's: what the player's own characters know. With an actor
        the player plays, that character's. Any other actor's perspective is the director's alone
        (``audience: director``): it is never the table's, and never world truth."""
        chat = parse.chat_of(args)
        audience = self._audience(args)
        scene = self.runner.open(chat)
        names = scene.names.get
        value = args.get("actor")
        if value is None:
            known = self.evidence.player_knowledge(chat.campaign_id, self.owner, scene.controls, as_of=NOW)
            return {"chat": str(chat), "perspective": "table", "audience": audience,
                    "actors": [knowledge_view(item, names) for item in known.actors],
                    "table_events": [{"kind": "table_event", "event": item.event_id, "summary": item.summary}
                                     for item in known.table_events]}
        actor = parse.actor_of(value, "actor", chat.campaign_id)
        if actor not in scene.cast:
            raise ApiRefused("no_actor", f"{actor} is not in this chat's cast")
        own = scene.controller(actor) == self.owner
        if not own and audience != "director":
            raise ApiRefused("director_only", "another character's perspective is the director's to read")
        if own:  # as the player's own: what the director wrote while it was an NPC is inherited, not theirs
            known = self.evidence.player_knowledge(chat.campaign_id, self.owner, scene.controls, as_of=NOW)
            (known,) = [item for item in known.actors if item.actor == actor]
        else:
            known = self.evidence.actor_knowledge(chat.campaign_id, actor, as_of=NOW)
        return {"chat": str(chat), "perspective": "actor", "audience": "table" if own else "director",
                "actors": [knowledge_view(known, names)], "table_events": []}

    # -- adjudication (029/040 through 035) ----------------------------------------------------------------
    def _after(self, chat: ChatRef, audience: str, done: Callable[[Names], dict]) -> dict:
        """The write's result (``done``, given the chat's names) and the hub as it now stands. The write is
        already committed, so a chat that cannot be read again now is reported beside it (``hub: None``, and
        the result without names), never as a refusal of the write."""
        try:
            hub = self.hub(chat, audience)
        except (SceneRefused, StoreError, ValueError) as error:
            return {"done": done(lambda actor: None), "hub": None,
                    "problems": [{"code": getattr(error, "reason", type(error).__name__), "detail": str(error)}]}
        names = {item["actor"]: item["name"] for item in hub["cast"]}
        return {"done": done(lambda actor: None if actor is None else names.get(str(actor))), "hub": hub}

    def _label(self, args: Mapping[str, object]) -> dict:
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        if not self.labelling:
            raise ApiRefused("no_labeller", "no labelling model is configured for this app")
        obstacle = parse.field(args, "obstacle", str, required=False)
        label = self.runner.label(chat, self.handle, obstacle=obstacle)
        return self._after(chat, audience, lambda names: {"label": label_view(label)})

    def _confirm(self, args: Mapping[str, object]) -> dict:
        """The acting player confirms the open action's latest labelling, unedited (035)."""
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        outcome = self.runner.confirm(chat, self.handle)
        return self._after(chat, audience, lambda names: {"outcome": outcome_view(outcome, names)})

    def _decide(self, args: Mapping[str, object]) -> dict:
        """The director adjudicates (``basis: adjudicated``) or the acting player consents to a setback
        (``basis: consent``, naming the frame's ``fingerprint``)."""
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        result = parse.field(args, "result", str)
        basis = parse.field(args, "basis", str, required=False, default="adjudicated")
        fingerprint = parse.field(args, "fingerprint", str, required=False)
        outcome = self.runner.decide(chat, self.handle, result, basis=basis, cites=parse.names(args, "cites"),
                                     consequences=parse.names(args, "consequences"), fingerprint=fingerprint)
        return self._after(chat, audience, lambda names: {"outcome": outcome_view(outcome, names)})

    # -- effects (021 through 035) -------------------------------------------------------------------------
    def _turn_number(self, args: Mapping[str, object]) -> tuple[str, int]:
        turn = parse.field(args, "turn", str)
        number = args.get("number")
        if type(number) is not int:
            raise ApiRefused("invalid_field", "number must be a whole number")
        return turn, number

    def _propose(self, args: Mapping[str, object]) -> dict:
        """The director proposes a turn's effect whole; what an obstacle attempt made due comes first (035)."""
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        turn = parse.field(args, "turn", str)
        summary = parse.field(args, "summary", str)
        if not summary.strip() or len(summary) > MAX_SUMMARY:
            raise ApiRefused("invalid_field", f"summary is 1 to {MAX_SUMMARY} characters of director narration")
        operations = parse.operations(args, chat.campaign_id)
        holdings = parse.holdings(args, chat.campaign_id)
        effect = self.runner.propose_effect(chat, self.handle, turn, summary, operations, holdings,
                                            waive=parse.waivers(args))
        return self._after(chat, audience, lambda names: {"effect": effect_view(effect, names)})

    def _endorse(self, args: Mapping[str, object]) -> dict:
        """The player's own key on the exact effect shown: ``fingerprint`` as the hub showed it."""
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        turn, number = self._turn_number(args)
        fingerprint = parse.field(args, "fingerprint", str)
        effect = self.runner.endorse_effect(chat, self.handle, turn, number, fingerprint)
        return self._after(chat, audience, lambda names: {"effect": effect_view(effect, names)})

    def _approve(self, args: Mapping[str, object]) -> dict:
        """The director's key; when it is the last one, the effect is accepted, once."""
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        turn, number = self._turn_number(args)
        effect = self.runner.approve_effect(chat, self.handle, turn, number)
        return self._after(chat, audience, lambda names: {"effect": effect_view(effect, names)})

    def _recover(self, args: Mapping[str, object]) -> dict:
        """Settle this chat's acceptances cut short, by what landed (030); ``abort`` undoes them instead. Where
        035 needs the player present (an abort of an effect they endorsed, or a completion whose endorsement the
        authority no longer records), ``player_key: true`` is the player's own key, given knowingly in this call
        (the screen asks the player for it apart from the director's choice); without it, 035 refuses and
        nothing is settled."""
        chat = parse.chat_of(args)
        audience = self._audience(args)  # read before any write: a malformed request writes nothing
        abort = parse.field(args, "abort", bool, required=False, default=False)
        own = parse.field(args, "own", bool, required=False, default=False)
        player_key = parse.field(args, "player_key", bool, required=False, default=False)
        endorsers = (self.handle,) if player_key else ()
        settled = self.runner.recover_effects(chat, self.handle, abort=abort, endorsers=endorsers, own=own)
        return self._after(chat, audience,
                           lambda names: {"settled": [effect_view(effect, names) for effect in settled]})


__all__ = ["ADJUDICATOR", "AUDIENCES", "UNAVAILABLE", "CampaignApi"]
