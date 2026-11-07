"""The headless text scene runner (035): post, generate, keep, strike, pass and mid-session requests.

One ``SceneRunner`` per open library. Every call reads the chat as it is now (one ``LibraryView``, the
chat's snapshot, its turns, control as recorded, the staged cast) and writes at most one turn file by
compare-and-swap, so a process that dies between calls loses nothing it reported as done. See
docs/SCENES.md for the contract.

Authority is never passed in: who controls an actor comes from 003's sovereignty ledger, who may act
is an authenticated handle the library's authority issued, and every generated line is a proposal
until the table keeps it. Nothing here writes world state (events, evidence, effects, rulings).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from grimoire.adjudication import Outcome
from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.chats import ChatCodecError
from grimoire.comms import TransmissionError, Unread, read_message
from grimoire.context import Budget
from grimoire.contextstore import actor_context, chat_lore
from grimoire.core import DIRECTOR, snapshot_actor_view
from grimoire.core.authority import (
    AuthorityError, ControlAssignment, NarrationClaim, NarrationContext, PlayerContribution, review_narration,
    submit_contribution,
)
from grimoire.core.boundaries import BoundaryContext, BoundaryRequest, decide_request
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.inference import InferencePolicy, InferenceStage, TurnUsageLedger
from grimoire.core.perspective import actor_knowledge
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import EvidenceLibrary
from grimoire.generation import GenerationError, director_brief, npc_brief, plan_generation_turn
from grimoire.import_v1 import imported_voices
from grimoire.intent import RoutingContext, Utterance, route
from grimoire.ledgers import SovereigntyLedger
from grimoire.ledgers.codec import ControlChange
from grimoire.partystore import PartyLibrary
from grimoire.play.adjudicate import check_decided, confirm_action, decide_action, outcome_lines, outcome_of
from grimoire.play.forks import choose_branch
from grimoire.play.history import chat_history
from grimoire.play.labels import label_action
from grimoire.play.scene import (
    DEFAULT_BUDGET, NOW, WRITE_RETRIES, Scene, _assigned, _audience, _Context, _declined, _find_line, _first_text,
    _follows, _has_imported, _human_of, _latest, _macros, _mark, _name_index, _no_clock, _no_id, _payload,
    _readable, _refuse, _scenario_text, _stage, _Stale,
)
from grimoire.providers.meter import TurnMeter
from grimoire.runtime import GenerationRuntime, LabelRuntime, RunFence
from grimoire.scene import (
    MAX_TAKES, NARRATOR, CallRecord, Label, Post, SceneLine, SceneRefused, Take, TurnRecord, audible, heard_for, render,
    segments, table_lines, turn_id,
)
from grimoire.scenestore import SceneLibrary, StoredTurn
from grimoire.security import is_human
from grimoire.store import LibraryStore, RecordNotFound
from grimoire.voice import ResolvedVoice, VoiceCatalog


class SceneRunner:
    """Plays chats of one open library through 027's runtime; every write is one turn file.

    ``handle`` is a trusted human handle ``authority`` issued (the library owner's): it writes turn files
    and records control at ``seat``. ``runtime`` is the app's ``GenerationRuntime`` (a trusted app handle,
    the configured provider only); without one nothing is generated. ``policy`` caps the calls a turn may
    plan (a director reply needs ``director_generation``). ``secrets`` are screened in every brief; with
    none, only 031's director-lore markers are screened. ``voices`` are authored profiles; imported
    characters derive their own (027/026). ``fence`` makes an older run of the same chat stale.
    ``labeller`` frames player characters' actions (``label``) on its own untrusted provider handle, and
    ``label_handle`` is the untrusted handle this library's authority issued it, which must be allowed to
    propose each labelling before it is written; without both nothing is labelled. ``adjudication`` is the
    library's 029 ``AdjudicationLibrary`` over the app's own adjudication key: it frames a confirmed labelling
    (``confirm``) and records its decision (``decide``); without it nothing is framed.
    """

    def __init__(self, store: LibraryStore, authority, handle, *, owner: str,
                 runtime: GenerationRuntime | None = None, policy: InferencePolicy | None = None,
                 secrets: Iterable[object] = (), voices: VoiceCatalog | None = None,
                 fence: RunFence | None = None, budget: Budget = DEFAULT_BUDGET,
                 labeller: LabelRuntime | None = None, label_handle=None,
                 adjudication: AdjudicationLibrary | None = None) -> None:
        if not (authority.issued(handle) and getattr(handle, "trusted", False) is True
                and is_human(getattr(handle, "principal", ""))):
            raise _refuse("bad_handle", "the runner writes with a trusted human handle this library's authority issued")
        if runtime is not None and not isinstance(runtime, GenerationRuntime):
            raise _refuse("bad_runtime", "runtime must be a GenerationRuntime")
        if labeller is not None and not isinstance(labeller, LabelRuntime):
            raise _refuse("bad_labeller", "labeller must be a LabelRuntime")
        if label_handle is not None and not (authority.issued(label_handle)
                                             and getattr(label_handle, "trusted", None) is False):
            raise _refuse("bad_handle", "the label handle is an untrusted handle this library's authority issued")
        if adjudication is not None and not (isinstance(adjudication, AdjudicationLibrary)
                                             and adjudication.store is store and adjudication.authority is authority):
            raise _refuse("bad_adjudication", "adjudication must be this library's AdjudicationLibrary")
        if adjudication is not None and (not adjudication.principal.startswith("app:") or adjudication.principal in {
                getattr(label_handle, "principal", None), getattr(getattr(labeller, "_handle", None), "principal", None)}):
            # the requester's call: the key is the app's own, never a player's, the owner's or the labeller's
            raise _refuse("bad_adjudication", "the adjudication key is the app's own, apart from the labeller's")
        self.store = store
        self.authority = authority
        self.handle = handle
        self.owner = owner
        self.runtime = runtime
        self.policy = policy if policy is not None else InferencePolicy()
        self.secrets = tuple(secrets)
        self.authored = voices
        self.fence = fence if fence is not None else RunFence()
        self.budget = budget
        self.labeller = labeller
        self.label_handle = label_handle
        self.adjudication = adjudication
        self.scenes = SceneLibrary(store, handle, owner=owner)
        self.sovereignty = SovereigntyLedger(authority)

    # -- reading ----------------------------------------------------------------------------------

    def open(self, chat: ChatRef) -> Scene:
        """The chat as it stands now; nothing is written."""
        return self._context(chat).scene

    def _context(self, chat: ChatRef) -> _Context:
        if type(chat) is not ChatRef:
            raise _refuse("invalid_field", "chat must be a ChatRef")
        view = self.store.load()
        try:
            captured = chat_snapshot(view, chat)
        except (RecordNotFound, ChatCodecError) as error:
            raise _refuse("no_chat", f"{chat} cannot be read ({type(error).__name__})") from None
        problems: list[tuple[str, str]] = [("snapshot", problem) for problem in captured.problems]
        # A bound source that is gone (a card deleted while the app was closed, its lorebook, a scenario) is
        # reported by its exact ref; the story stays, and nothing similarly named is bound in its place.
        problems += [("broken_source", str(resolution.source.ref)) for resolution in captured.snapshot.sources()
                     if resolution.broken is not None]
        turns = self.scenes.turns(chat)
        problems += [("turns", f"{path}:{code}") for path, code in turns.problems]
        problems += self.scenes.fork_problems(turns)  # 028: which turns, which devices, from which number
        if turns.conflicted:  # 019's keep-both on a turn: each device reads its own until it is resolved
            problems.append(("turn_conflict", ", ".join(turns.conflicted)))
        history, history_problems = chat_history(self.store, chat, captured.chat, captured.snapshot)
        problems += history_problems
        controls = tuple(self.sovereignty.load(chat.campaign_id).controls)
        party = PartyLibrary(self.store, self.handle, clock=_no_clock, new_id=_no_id).view(chat.campaign_id, as_of=NOW)
        party_problems = tuple(f"{problem.path}:{problem.code}" for problem in party.problems)
        problems += [("party", problem) for problem in party_problems]
        candidates = []
        for binding in captured.chat.active_bindings():
            actor = binding.actor
            if actor is None:
                continue
            if party.state.uncertain(actor):
                problems.append(("party_uncertain", str(actor)))  # fail closed: it may be stored away
            elif not party.state.reserved(actor):  # a reserved member is stored away: never on stage
                candidates.append(actor)
        staged, unstaged = _stage(party, chat, candidates)
        problems += unstaged
        cast_t = staged.present if staged is not None else ()
        persona_binding = captured.chat.persona
        persona = persona_binding.actor if persona_binding is not None else None
        snap = captured.snapshot
        names: dict[InstanceRef, str] = {}
        for actor in cast_t:
            try:
                names[actor] = snap.entity(actor).name
            except Exception as error:  # 022's refusals: the actor stays nameless, and says why
                problems.append(("unnamed_actor", f"{actor}:{type(error).__name__}"))
        voices = imported_voices(snap, self.authored)
        scene = Scene(chat, turns, controls, cast_t, persona, names, (), staged, history)
        for actor in scene.cast:
            if actor == persona or _human_of(scene, actor) is not None:
                continue  # a player character speaks through its player
            try:
                voices.resolve(actor)
            except Exception as error:  # 014/022's refusals: it stays silent, and says why
                problems.append(("no_voice", f"{actor}:{getattr(error, 'reason', type(error).__name__)}"))
        if persona is not None and persona in scene.cast and scene.controller(persona) == DIRECTOR:
            problems.append(("persona_not_player", str(persona)))  # 003 must give it back to a player
        if scene.interrupted:
            problems.append(("interrupted", scene.pending.turn_id))
        return _Context(replace(scene, problems=tuple(problems)), view, captured, voices, party_problems)

    # -- control ----------------------------------------------------------------------------------

    def seat(self, chat: ChatRef, player: str) -> tuple[ControlAssignment, ...]:
        """Record control for the chat's actors that have none: the persona to ``player``, every other
        bound character to the director. A recorded controller is never changed here."""
        if not is_human(player):
            raise _refuse("invalid_field", "a player is a human principal")
        context = self._context(chat)
        scene = context.scene
        for binding in context.captured.chat.active_bindings():
            actor = binding.actor
            if actor is None or scene.controller(actor) is not None or _assigned(scene, actor):
                continue
            controller = player if binding.role == "persona" else DIRECTOR
            try:
                self.sovereignty.record(chat.campaign_id, ControlChange(chat.campaign_id, actor.instance_id, controller),
                                        self.handle)
            except AuthorityError as error:
                raise _refuse("not_seated", str(error)[:200]) from None
        return tuple(self.sovereignty.load(chat.campaign_id).controls)

    def _principal(self, scene: Scene, handle) -> str:
        """The authenticated principal behind ``handle``, if it plays a present character or owns the library."""
        if not (self.authority.issued(handle) and getattr(handle, "trusted", False) is True):
            raise _refuse("not_authenticated", "a player acts with a trusted handle this library's authority issued")
        principal = getattr(handle, "principal", None)
        if not isinstance(principal, str) or not is_human(principal):
            raise _refuse("not_authenticated", "only a human player acts at the table")
        if principal in self.authority.owners or any(scene.controller(pc) == principal for pc in scene.players):
            return principal
        raise _refuse("not_at_table", "only a player of a present character, or the owner, may do that")

    # -- turns ------------------------------------------------------------------------------------

    def begin(self, chat: ChatRef, by, *, opening: str | None = None) -> TurnRecord:
        """The opening turn (number 1), as a proposed take: a card's first greeting spoken by its character,
        or a scenario's first opening (or the chat's premise) narrated. Keep it with ``keep``.

        ``opening`` chooses another one by its imported id (052's Start Chat): a greeting of the launch card
        (``first``, ``alt-2``, ``group-1``) or an opening of the bound scenario (``opening-alt-2``). An id the
        chat does not have is refused ``unknown_opening``, never replaced by the first one."""
        if opening is not None and (type(opening) is not str or not opening):
            raise _refuse("invalid_field", "an opening is chosen by its id")
        context = self._context(chat)
        scene = context.scene
        principal = self._principal(scene, by)
        self._ready(scene)
        stored = scene.turns.last
        if stored is None and (scene.imported is not None or _has_imported(scene)):
            raise _refuse("already_begun", "the chat goes on from its imported history; post or generate")
        if stored is None:
            record = TurnRecord(chat, 1, "opening", device=self.scenes.tag_for(scene.turns, 1))
        elif len(scene.turns.turns) == 1 and stored.record.origin == "opening" and stored.record.open:
            record = stored.record  # the opening again, for the cast as it is now (an earlier one went stale)
            if len(record.takes) >= MAX_TAKES:
                raise _refuse("too_many_takes", f"a turn holds at most {MAX_TAKES} takes")
            if record.running is not None:
                record = record.with_take(replace(record.running, state="superseded"))
        else:
            raise _refuse("already_begun", "the chat has turns; it opens only once")
        number = len(record.takes) + 1
        line = self._opening(context, record, number, opening)
        take = Take(number, "proposed", "opening", scene.cast, lines=(line,), by=principal)
        record = record.with_take(take)
        self.scenes.write(record, stored, unpaused=True)
        return record

    def _opening(self, context: _Context, record: TurnRecord, take: int, chosen: str | None) -> SceneLine:
        scene, chat = context.scene, context.captured.chat
        user = scene.names.get(scene.persona) if scene.persona is not None else None
        line_id = f"{record.turn_id}.{take}.1"
        for binding in chat.active_bindings("scenario"):
            payload = _payload(context.captured.snapshot, binding.ref, "scenario")
            text = _first_text(payload.get("openings"), wanted=chosen, chosen=chosen is not None)
            if text:
                return SceneLine(line_id, None, NARRATOR, _macros(text, None, user), scene.players)
        launch = chat.launch
        if launch.kind == "character" and launch.ref is not None:
            for binding in chat.active_bindings("character"):
                if binding.ref.unpinned() != launch.ref.unpinned() or binding.actor not in scene.cast:
                    continue
                payload = _payload(context.captured.snapshot, binding.ref, "character")
                card = payload.get("card")
                greetings = card.get("greetings") if isinstance(card, Mapping) else None
                text = _first_text(greetings, wanted=chosen or "first", chosen=chosen is not None)
                if text:
                    name = scene.names.get(binding.actor, binding.actor.instance_id)
                    return SceneLine(line_id, binding.actor, name, _macros(text, name, user), scene.cast)
        if chosen is not None:
            raise _refuse("unknown_opening", "this chat has no greeting or opening with that id")
        if launch.premise and launch.premise.strip():
            return SceneLine(line_id, None, NARRATOR, _macros(launch.premise, None, user), scene.players)
        raise _refuse("no_opening", "this chat has no greeting or opening to begin with")

    def post(self, chat: ChatRef, by, text: str, *, mode: str = "auto") -> TurnRecord:
        """A player's line for their character, saved as a new turn before anything is generated.

        Speech is read with 032 and heard by whoever ``transmit`` says; an action that is not a private
        message is a visible action everyone present sees. A retry of the same post onto its own turn, before
        anything was generated, returns that turn.
        """
        context = self._context(chat)
        scene = context.scene
        principal = self._principal(scene, by)
        self._ready(scene)
        persona = scene.persona
        if persona is None:
            raise _refuse("no_persona", "this chat has no player character; ask the cast to go on with generate")
        if persona not in scene.cast:
            raise _refuse("persona_absent", "the player character is not present in this scene")
        last = scene.last
        number = 1 if last is None else last.number + 1
        device = self.scenes.tag_for(scene.turns, number)
        tid = turn_id(chat, number, device)
        if not isinstance(text, str) or not text.strip():
            raise _refuse("no_content", "a post needs text")
        try:
            utterance = Utterance(text, mode)
            intent = route(utterance, RoutingContext(active_pc=persona.instance_id, can_direct=False))
        except ValueError as error:
            raise _refuse("invalid_field", str(error)[:200]) from None
        if intent.kind == "uncertain":
            raise _refuse("uncertain", intent.feedback)
        if intent.kind not in ("pc_speech", "pc_action"):
            raise _refuse("not_in_this_slice", f"{intent.kind} is not played yet")
        kind = "speech" if intent.kind == "pc_speech" else "action"
        try:
            submit_contribution(PlayerContribution(chat.campaign_id, tid, chat.chat_id, tid, persona.instance_id,
                                                   principal, kind, intent.text), scene.controls)
        except AuthorityError:
            raise _refuse("not_your_character", f"{principal} does not control {persona}") from None
        except ValueError as error:
            raise _refuse("invalid_field", str(error)[:200]) from None
        try:
            read = read_message(intent, persona, _name_index(scene), message_id=tid)
        except TransmissionError as error:
            raise _refuse(error.reason, "the post cannot be read") from None
        if isinstance(read, Unread):
            # 032: an Unread transmits nothing. An action that is no message at all is seen by everyone present;
            # one 032 cannot rule out as a private message (a note, an aside, a signal) is withheld: its sender
            # alone hears it. A message aimed at someone 032 cannot resolve is refused, never made public.
            if kind == "speech" or read.reason not in ("not_directed", "unresolved_private"):
                raise _refuse(read.reason, "the line cannot be read as a message; say who it is for, and what")
            withheld = read.reason == "unresolved_private"
            post = Post(principal, persona, "action", intent.text, (persona,) if withheld else scene.cast,
                        withheld=withheld)
        else:
            post = Post(principal, persona, kind, read.text, _audience(read, scene, chat, tid, number),
                        read.channel, read.addressees)
        if not audible(post):  # as clipped and shown
            raise _refuse("no_content", "a post needs words someone could hear")
        if last is not None and last.open:
            if last.post == post and not last.takes:
                return last  # the same post retried: it is already saved
            raise _refuse("turn_open", "the last turn is still open: keep a take, or pass it")
        record = TurnRecord(chat, number, "post", post, device=device, follows=_follows(last))
        self.scenes.write(record, None, after=scene.turns.last)
        return record

    def generate(self, chat: ChatRef, by, *, speakers: Iterable[InstanceRef] | None = None,
                 director: bool = False) -> Take:
        """One new proposed take for the open turn (a turn with no post is opened when none is).

        Each speaker's call is briefed from its own knowledge and only what it heard; a director reply,
        asked explicitly, sees only what the table saw. The take is written ``running`` before any call, so
        a crash leaves a record; afterwards it holds every call's attempts and usage, even when the turn
        halted. A run made stale by a newer one keeps its usage and drops its lines.
        """
        context = self._context(chat)
        scene = context.scene
        principal = self._principal(scene, by)
        self._ready(scene)
        if self.runtime is None:
            raise _refuse("no_runtime", "no model is configured")
        scope = context.captured.scope
        if scope is not None and scope.session_zero is not None:
            raise _refuse("session_zero_unreadable", "this slice cannot read a session zero yet; generation waits")
        if context.party_problems:
            raise _refuse("party_unreadable", "who is present cannot be read in full")
        unseated = [str(actor) for actor in scene.cast if scene.controller(actor) is None]
        if unseated:
            raise _refuse("unassigned_actor", f"nobody controls {unseated}; seat the chat first")
        chosen = self._speakers(context, speakers)
        if not chosen and not director:
            raise _refuse("nothing_to_generate", "no NPC here can speak; ask for a director reply")
        pending = scene.pending
        check_decided(self, pending)
        stored = scene.turns.last if pending is not None else None
        following = 1 if scene.last is None else scene.last.number + 1
        record = pending if pending is not None else TurnRecord(
            chat, following, "prompt", device=self.scenes.tag_for(scene.turns, following), follows=_follows(scene.last))
        number = len(record.takes) + 1
        if number > MAX_TAKES:
            raise _refuse("too_many_takes", f"a turn holds at most {MAX_TAKES} takes")
        plan_turn = record.plan_turn(number)
        history = scene.records if pending is None else scene.records[:-1]
        heard_turns = (*history, record)
        try:
            plan = plan_generation_turn(chat.campaign_id, plan_turn, present=[a.instance_id for a in scene.cast],
                                        speakers=[a.instance_id for a, _ in chosen], controls=scene.controls,
                                        director_reply=director, policy=self.policy)
        except GenerationError as error:
            raise _refuse(error.reason, "the turn cannot be planned") from None
        if director and not any(call.stage is InferenceStage.DIRECTOR_GENERATION for call in plan.calls):
            raise _refuse("director_declined", "the inference policy allows no director reply")
        try:
            briefs = self._briefs(context, chosen, heard_turns)
            director_view = self._director(context, heard_turns) if director else None
        except GenerationError as error:
            raise _refuse(error.reason, "a brief cannot be built") from None
        planned = tuple(CallRecord(call.call_id.phase, call.stage, call.call_id.actor_id, call.call_id.ordinal,
                                   "planned") for call in plan.calls)
        running = Take(number, "running", "generated", scene.cast, calls=planned, declined=_declined(plan), by=principal)
        superseded = record.running
        if superseded is not None:
            record = record.with_take(replace(superseded, state="superseded"))
        record = record.with_take(running)
        claimed = []  # the fence is claimed as the running take is published, never after: an older run
        # resuming between the two would otherwise cancel the reroll that superseded it
        stored = self.scenes.write(record, stored, after=scene.turns.last if stored is None else None, unpaused=True,
                                   on_commit=lambda: claimed.append(self.fence.claim(str(chat))))
        (token,) = claimed
        meter = TurnMeter(TurnUsageLedger(plan))
        silent = tuple(voice for actor, voice in self._voices(context) if actor not in {a for a, _ in chosen})
        try:
            outcome = self.runtime.run_turn(meter, briefs, controls=scene.controls,
                                            director=director_view[0] if director_view else None, token=token,
                                            scene_voices=silent)
        except GenerationError as error:
            outcome = None
            failure = error.reason
        else:
            failure = None
        take = self._finished(record, running, plan, meter, outcome, failure, scene,
                              director_view[1] if director_view else ())
        return self._land(chat, stored, take, token)

    # -- generation helpers -----------------------------------------------------------------------

    def _voices(self, context: _Context) -> list[tuple[InstanceRef, ResolvedVoice]]:
        found = []
        for actor in context.scene.npcs:
            try:
                found.append((actor, context.voices.resolve(actor)))
            except Exception:  # reported by ``open`` as ``no_voice``
                continue
        return found

    def _speakers(self, context: _Context, speakers) -> list[tuple[InstanceRef, ResolvedVoice]]:
        scene = context.scene
        voiced = dict(self._voices(context))
        if speakers is None:
            return [(actor, voiced[actor]) for actor in scene.npcs if actor in voiced]
        chosen = []
        for actor in speakers:
            if type(actor) is not InstanceRef or actor not in scene.cast:
                raise _refuse("not_present", f"{actor} is not present in this scene")
            if scene.controller(actor) != DIRECTOR:
                raise _refuse("not_an_npc", f"{actor} is a player character; its player speaks for it")
            if actor not in voiced:
                raise _refuse("no_voice", f"{actor} has no voice to speak with")
            if actor in dict(chosen):
                raise _refuse("invalid_field", f"{actor} is named twice")
            chosen.append((actor, voiced[actor]))
        return chosen

    def _knowledge(self, chat: ChatRef):
        ledger = EvidenceLibrary(self.store, self.handle, owner=self.owner).read(chat.campaign_id, requested_by=DIRECTOR)
        return ledger, _latest(ledger.knowledge)

    def _briefs(self, context: _Context, chosen, turns: Sequence[TurnRecord]) -> list:
        if not chosen:
            return []
        scene, snap, chat = context.scene, context.captured.snapshot, context.scene.chat
        ledger, as_of = self._knowledge(chat)
        _, bare = outcome_lines(self, turns, scene.names)  # an NPC hears a result where it saw the action
        briefs = []
        for actor, voice in chosen:
            heard = heard_for(turns, actor, scene.names, before=scene.imported, outcomes=bare)
            built = actor_context(context.view, chat, ledger, actor, as_of=as_of, budget=self.budget,
                                  present=scene.cast, scene_text=render(heard))
            knowledge = actor_knowledge(ledger.knowledge, actor, as_of=as_of)
            view = snapshot_actor_view(snap.instance(actor), snap, knowledge)
            briefs.append(npc_brief(view, voice, heard=heard, secrets=self.secrets, names=scene.names,
                                    context=built.pack, snapshot=snap))
        return briefs

    def _director(self, context: _Context, turns: Sequence[TurnRecord]):
        scene, chat = context.scene, context.scene.chat
        scenario = _scenario_text(context)
        if not scenario:
            raise _refuse("no_scenario", "a director reply needs a bound scenario or a premise")
        table = table_lines(turns, scene.names, before=scene.imported,
                            outcomes=outcome_lines(self, turns, scene.names)[0])
        lore = chat_lore(context.view, chat, table_text=render(table), budget=self.budget).lore
        names = {actor.instance_id: name for actor, name in scene.names.items()}
        brief = director_brief(scenario, campaign_id=chat.campaign_id, present=[a.instance_id for a in scene.cast],
                               controls=scene.controls, names=names, heard=table, secrets=self.secrets, lore=lore,
                               snapshot=context.captured.snapshot)
        return brief, tuple(f"{ref}#{name}" for ref, name in brief.unscreened)

    def _finished(self, record: TurnRecord, running: Take, plan, meter: TurnMeter, outcome, failure: str | None,
                  scene: Scene, unscreened: tuple[str, ...]) -> Take:
        """The take a run ended with: every call's outcome and attempts, and the lines it kept."""
        by_call = {}
        for attempt in meter.ledger.attempts:
            by_call.setdefault(attempt.call_id, []).append(attempt)
        statuses = {call.call_id: (call.status, call.reason) for call in outcome.calls} if outcome else {}
        lines: list[SceneLine] = []
        rejected: dict[object, str] = {}
        if outcome is not None and outcome.status != "stale":
            context = NarrationContext(record.chat.campaign_id, record.chat.chat_id, record.turn_id)
            for line in outcome.lines:
                line_id = f"{record.turn_id}.{running.take}.{len(lines) + 1}"
                if line.actor is not None:
                    claim = NarrationClaim(record.chat.campaign_id, line_id, "voluntary_speech", line.text,
                                           subject_id=line.actor.instance_id)
                    review = review_narration(context, (claim,), scene.controls)
                    if review.rejected:
                        rejected[line.call_id] = f"review_{review.rejected[0].reason}"
                        continue
                    audience = scene.cast
                else:
                    audience = scene.players
                lines.append(SceneLine(line_id, line.actor, line.speaker, line.text, audience,
                                       actor_revision=line.actor_revision, voice_profile=line.voice_profile))
        calls = []
        for planned in plan.calls:
            status, reason = statuses.get(planned.call_id, ("not_issued", failure))
            if planned.call_id in rejected:
                status, reason = "rejected", rejected[planned.call_id]
            calls.append(CallRecord(planned.call_id.phase, planned.stage, planned.call_id.actor_id,
                                    planned.call_id.ordinal, status, reason, tuple(by_call.get(planned.call_id, ()))))
        if outcome is None:
            state, status = "proposed", "halted"
        elif outcome.status == "stale":
            state, status = "stale", "stale"
        else:
            state, status = "proposed", outcome.status
        return replace(running, state=state, lines=tuple(lines) if state == "proposed" else (), calls=tuple(calls),
                       outcome=status, unscreened=unscreened)

    def _land(self, chat: ChatRef, stored: StoredTurn, take: Take, token) -> Take:
        """Write the finished take over its running one; a run gone stale keeps its usage and no lines."""
        def fence() -> None:
            if take.state != "stale" and token.stale:
                raise _Stale

        published = next(t for t in stored.record.takes if t.take == take.take)  # the running take, as written
        for _ in range(WRITE_RETRIES):
            try:
                self.scenes.write(stored.record.with_take(take), stored, landing=True, before_commit=fence)
                return take
            except _Stale:  # a newer run of this chat started: keep the usage, drop the lines
                take = replace(take, state="stale", lines=(), outcome="stale")
            except SceneRefused as error:
                if error.reason != "turn_changed":
                    raise
            # someone else wrote the turn (an older run's usage, a pass, a newer run): read it again
            current = self.scenes.turns(chat)
            found = next((s for s in (*current.turns, *current.set_aside) if s.record.ref == stored.record.ref), None)
            if found is None:
                break
            stored = found
            existing = next((t for t in stored.record.takes if t.take == take.take), None)
            if existing is None and len(stored.record.takes) + 1 == take.take:
                # its turn now holds another version (a sync conflict resolved to the peer's meanwhile), with no
                # take of this number: that version stands as chosen, and this run's usage joins it as a stale
                # take under the number its calls were billed under (``<turn_id>.<take>``)
                take = replace(take, state="stale", lines=(), outcome="stale")
            elif (existing is None or existing.state not in ("running", "superseded")
                  or replace(published, state=existing.state) != existing):
                break  # another version's own take of this number (or a gap before it): nowhere to record it
            elif existing.state == "superseded":  # passed, or replaced by a newer run: this one lost
                take = replace(take, state="stale", lines=(), outcome="stale")
        raise _refuse("turn_changed", "another run changed this turn; its usage could not be saved")

    # -- labelling ---------------------------------------------------------------------------------

    def label(self, chat: ChatRef, by) -> Label:
        """Propose 029 labels for the open turn's action: which offered facts bear on it, what is at stake.

        Asked by the acting character's player or the owner. One ``labelling`` call on the labeller's
        untrusted handle; its reply may cite only facts from that character's own context pack. The
        labelling is written ``running`` before the call, so a crash leaves its usage on record, then
        ``proposed`` (labels to confirm) or ``refused`` (why). An earlier unconfirmed labelling of the turn
        is superseded. Nothing is framed, decided or accepted here.
        """
        return label_action(self, chat, by)

    def confirm(self, chat: ChatRef, by) -> Outcome | None:
        """Frame the open action exactly as its latest proposed labelling says (029), unedited.

        Only the acting character's player confirms, with their own trusted handle; a wrong labelling is
        fixed by labelling again, never by editing it. The frame is recorded with the adjudication key and
        the labelling written ``framed``. An action the rules decide (straightforward, impossible) is decided
        at once, by rule, and its ``Outcome`` returned; an uncertain one waits for ``decide`` (``None``).
        """
        return confirm_action(self, chat, by)

    def decide(self, chat: ChatRef, by, result: str, *, basis: str = "adjudicated", cites=(),
               consequences=()) -> Outcome:
        """Decide the open turn's framed, uncertain action: the director (the library owner) adjudicates
        it (``basis="adjudicated"``), or its player consents to a setback (``basis="consent"``), citing
        the frame's factors and naming the stakes that come true. 029 checks every rule."""
        return decide_action(self, chat, by, result, basis=basis, cites=cites, consequences=consequences)

    def outcome(self, chat: ChatRef) -> Outcome | None:
        """The recorded outcome of the open turn's framed action, if it was decided."""
        return outcome_of(self, self._context(chat).scene.pending)

    # -- the table's choices ------------------------------------------------------------------------

    def keep(self, chat: ChatRef, by, take: int, *, acknowledge_unscreened: bool = False) -> TurnRecord:
        """Keep one proposed take: its shown lines become the transcript, and the turn closes."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        stored = self._open(scene)
        if scene.paused:
            raise _refuse("paused", "play is paused")
        record = stored.record
        found = next((t for t in record.takes if t.take == take), None)
        if found is None or found.state != "proposed":
            raise _refuse("not_proposed", "only a proposed take can be kept")
        check_decided(self, record)
        if not found.shown():
            raise _refuse("empty_take", "this take has no line to keep")
        if found.unscreened and not acknowledge_unscreened:
            raise _refuse("unscreened_lore", "this take was written with director lore no screen covered; "
                                            "keep it only after reading it")
        if set(found.cast) != set(scene.cast) or any(
                set(line.audience) != set(scene.players if line.actor is None else scene.cast)
                or (line.actor is not None and line.actor not in scene.npcs)  # a player's character now
                for line in found.shown()):
            raise _refuse("cast_changed", "who is present, or who plays them, changed since this take was written; "
                                          "ask again")
        if record.running is not None:  # a run still in flight loses: its take lands stale
            record = record.with_take(replace(record.running, state="superseded"))
        record = record.with_take(replace(found, state="kept", by=principal))
        self.scenes.write(record, stored, unpaused=True)
        return record

    def strike(self, chat: ChatRef, by, line_id: str) -> TurnRecord:
        """Strike one kept line from the transcript: no one hears it from now on."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        _readable(scene)
        stored, take, line = _find_line(scene, line_id)
        if take.state != "kept" or not line.shown:
            raise _refuse("not_kept", "only a shown line of a kept take can be struck")
        record = stored.record.with_take(_mark(take, {line_id}, "struck", principal))
        self.scenes.write(record, stored)
        return record

    def pass_turn(self, chat: ChatRef, by) -> TurnRecord:
        """Close the open turn with nothing kept, so play can go on."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        stored = self._open(scene)
        if scene.paused:
            raise _refuse("paused", "play is paused")
        record = stored.record
        if record.running is not None:
            record = record.with_take(replace(record.running, state="superseded"))
        record = replace(record, passed_by=principal)
        self.scenes.write(record, stored, unpaused=True)
        return record

    def request(self, chat: ChatRef, by, kind: str, targets: Iterable[str] = (), *,
                direction: str | None = None) -> TurnRecord:
        """016's mid-session request: ``pause`` stops play; ``skip``, ``redirect`` and ``rephrase`` withdraw
        the named lines (``redirect``/``rephrase`` record the direction on their take)."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        _readable(scene, pausing=kind == "pause")
        targets = tuple(targets)
        if kind in ("skip", "redirect", "rephrase") and not targets:
            raise _refuse("no_targets", f"a {kind} names the lines it is about")
        try:
            request = BoundaryRequest(chat.campaign_id, f"{chat.chat_id}.request", principal, kind, targets,
                                      direction or "")
        except ValueError as error:
            raise _refuse("invalid_field", str(error)[:200]) from None
        boundary = BoundaryContext(chat.campaign_id, frozenset({principal}))
        if kind == "pause":
            if targets:
                raise _refuse("invalid_field", "a pause names no lines")
            stored = scene.turns.last
            if stored is None:
                raise _refuse("nothing_to_pause", "the chat has no turn yet")
            if scene.paused:
                raise _refuse("already_paused", "play is already paused")
            decision = decide_request(request, boundary, ())
            if not decision.halt:
                raise _refuse(decision.reason, "the pause was refused")
            record = replace(stored.record, paused=True, paused_by=principal)
            self.scenes.write(record, stored, last=True)
            return record
        stored, _take, _line = _find_line(scene, targets[0])
        decision = decide_request(request, boundary, segments(stored.record))
        if decision.status != "accepted":
            raise _refuse("unknown_line" if decision.reason == "unknown_segment" else decision.reason,
                          "the request was refused")
        withdrawn = set(decision.withdrawn_proposals) | set(decision.rerender)
        record = stored.record
        for take in record.takes:
            hit = withdrawn & {line.line_id for line in take.shown()}
            if not hit:
                continue
            changed = _mark(take, hit, "withdrawn", principal)
            if decision.direction:
                changed = replace(changed, direction=decision.direction)
            record = record.with_take(changed)
        self.scenes.write(record, stored)
        return record

    def resume(self, chat: ChatRef, by) -> TurnRecord:
        """Lift a pause: the player who paused lifts their own, the library's owner every one (so a pauser who has
        left the table never locks the chat). A pause on a turn a fork choice set aside is lifted the same way;
        play goes on once no pause stands. Returns the chat's last turn."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        _readable(scene)
        after = self.scenes.resume(scene.turns, principal, owner=principal in self.authority.owners)
        assert after.last is not None  # a pause needs a turn, and a set-aside one a chosen line
        return after.last.record

    def choose(self, chat: ChatRef, by, turn: str) -> Scene:
        """028: at a forked chat, the branch whose first turn is ``turn`` goes on; the others are set aside.
        Only the library's owner may choose (``play.forks``)."""
        return choose_branch(self, chat, by, turn)

    # -- checks -----------------------------------------------------------------------------------

    def _ready(self, scene: Scene) -> None:
        _readable(scene)
        if scene.staged is None:
            raise _refuse("party_unreadable", "who is present cannot be staged; nobody is on stage")
        if scene.paused:
            raise _refuse("paused", "play is paused")
        if scene.persona is not None and scene.persona in scene.cast and scene.controller(scene.persona) == DIRECTOR:
            raise _refuse("persona_not_player", "the director controls this chat's persona; a player must take it back")

    def _open(self, scene: Scene) -> StoredTurn:
        _readable(scene)
        stored = scene.turns.last
        if stored is None or not stored.record.open:
            raise _refuse("no_open_turn", "no turn is open")
        return stored


__all__ = ["DEFAULT_BUDGET", "Scene", "SceneRunner"]
