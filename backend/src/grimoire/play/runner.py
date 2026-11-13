"""The headless text scene runner (035): post, generate, keep, strike, pass and mid-session requests.

One ``SceneRunner`` per open library. Every call reads the chat as it is now (one ``LibraryView``, the
chat's snapshot, its turns, control as recorded, the staged cast) and writes at most one turn file by
compare-and-swap, so a process that dies between calls loses nothing it reported as done. See
docs/SCENES.md for the contract.

Authority is never passed in: who controls an actor comes from 003's sovereignty ledger, who may act
is an authenticated handle the library's authority issued, and every generated line is a proposal
until the table keeps it. The only world state written here is an accepted effect (021), and only once
it holds every key (``play/accept.py``); nothing writes events, evidence or rulings.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace

from grimoire.adjudication import Outcome
from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.chats import ChatCodecError
from grimoire.comms import TransmissionError, Unread, read_message
from grimoire.context import Budget
from grimoire.contextstore import actor_context, chat_lore
from grimoire.decisionstore import DecisionLibrary
from grimoire.decisions import DecisionError, record_decision, replay
from grimoire.generation.brief import heard_transcript
from grimoire.effectstore import EffectRecovery, TurnEffectsLibrary
from grimoire.core import DIRECTOR, snapshot_actor_view
from grimoire.core.authority import (
    AuthorityError, ControlAssignment, NarrationClaim, NarrationContext, PlayerContribution, review_narration,
    submit_contribution,
)
from grimoire.core.boundaries import BoundaryChange, BoundaryContext, BoundaryRequest, decide_request
from grimoire.core.identity import ChatRef, InstanceRef
from grimoire.core.inference import InferencePolicy, InferenceStage, TurnUsageLedger
from grimoire.core.perspective import actor_knowledge
from grimoire.effective import chat_snapshot
from grimoire.evidencestore import EvidenceLibrary
from grimoire.generation import GenerationError, director_brief, npc_brief, plan_generation_turn, unrecognised
from grimoire.goals import Agenda, Choice, GoalError, check_agenda, choose, explain
from grimoire.import_v1 import imported_voices
from grimoire.intent import Command, RoutingContext, Utterance, route
from grimoire.intent.text import wrapped
from grimoire.ledgers import SovereigntyLedger
from grimoire.ledgers.codec import ControlChange
from grimoire.obstaclestore import ObstacleLibrary
from grimoire.partystore import PartyLibrary
from grimoire.play.accept import (
    approve_effect, effect_problems, endorse_effect, propose_effect, recover_effects,
)
from grimoire.play.boundaries import HELD, Boundaries, change as change_boundary, check_presented, in_force
from grimoire.play.boundaries import view as boundary_view
from grimoire.play.adjudicate import (
    FramedAction, check_decided, confirm_action, decide_action, framed_of, outcome_lines, outcome_of,
)
from grimoire.play.forks import choose_branch
from grimoire.play.history import chat_history
from grimoire.play.labels import label_action
from grimoire.play.mixed import keep_lines
from grimoire.play.obstacles import pass_attempt
from grimoire.play.scene import (
    DEFAULT_BUDGET, NOW, WRITE_RETRIES, Scene, _assigned, _audience, _Context, _declined, _find_line, _first_text,
    _follows, _has_imported, _human_of, _latest, _macros, _mark, _name_index, _no_clock, _no_id, _payload,
    _perceived, _readable, _refuse, _room, _scenario_text, _seen, _stage, _stage_ok, _Stale, _table_audience,
    _table_known,
)
from grimoire.play.stages import StageSource, stage_key, succession
from grimoire.providers.meter import TurnMeter
from grimoire.runtime import GenerationRuntime, LabelRuntime, RunFence
from grimoire.scene import (
    MAX_TAKES, NARRATOR, CallRecord, Intention, IntentionOption, Label, Post, SceneLine, SceneRefused, Take, TurnRecord,
    audible, heard_for, render, segments, table_lines, turn_id,
)
from grimoire.scenestore import MAX_TURN_BYTES, SceneLibrary, StoredTurn
from grimoire.security import is_human
from grimoire.staging import Stage, StagingRefused, check_cast
from grimoire.store import LibraryStore, RecordNotFound
from grimoire.tracing import Trace
from grimoire.voice import ResolvedVoice, VoiceCatalog

from .tracing import record_calls, span, traced

#: What a player can do about a line 032 cannot read, by its reason: the refusal says how to post it so it reads.
UNREAD_HINTS = {
    "unresolved_private": "the line sounds meant for fewer ears than the room's: put the words in quotes to say "
                          "them aloud, or post an action such as I whisper to (name): \"...\"",
    "mixed_narration": "the line mixes the words said with narration: post the words alone, or in quotes, and the "
                       "rest as an action",
    "unknown_addressee": "use a name from the scene and set the words off after it, such as "
                         "I whisper to (name): \"...\"",
    "ambiguous_addressee": "more than one listener fits: name one, and put the words in quotes, such as "
                           "I whisper to (name): \"...\"",
    "conflicting_channel": "the line both whispers and speaks mind to mind: choose one, such as "
                           "I tell (name) telepathically: \"...\"",
    "indirect_speech": "say how it reaches them, such as I whisper to (name): \"...\" or "
                       "I tell (name) telepathically: \"...\", or post the words as speech",
    "unclear_content": "the words said are unclear: put the exact words in quotes after the name, such as "
                       "I whisper to (name): \"...\"",
    "no_content": "the line has no words for anyone to hear",
}

#: Room a finished take needs in its turn file beyond its running take: each planned call's line (a screened line is
#: at most ``MAX_LINE_CHARS`` characters, with its speaker, ids and audience, escaped), each attempt's usage, and the
#: take's own fields. ``generate`` refuses ``turn_too_large`` before any call when the running turn leaves less.
LINE_BYTES = 32 * 1024
ATTEMPT_BYTES = 2 * 1024
TAKE_BYTES = 64 * 1024


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
    (``confirm``) and records its decision (``decide``); without it nothing is framed. ``effects`` is the
    library's 021 ``TurnEffectsLibrary`` on the same store and this runner's own (owner's) handle: it accepts
    a turn's effect once it holds every key (``propose_effect``); without it no effect is proposed. ``obstacles``
    is the library's 040 ``ObstacleLibrary`` on the same adjudication key: a labelling may then be asked against one
    of its obstacles, the labeller choosing the approach, and confirming frames the attempt through it; whatever the
    attempt makes due is part of every effect proposed for its turn (docs/SCENES.md). ``agendas``
    are 039's director-authored ``Agenda`` values, at most one per NPC: each take chooses afresh what a speaking
    director NPC with one pursues, from the knowledge its brief is built from, and records the choice on the
    turn. They are the app's configuration, never read from a turn, a post or a reply. ``stages`` is the app's 041
    ``StageSource`` (``play.stages``): each call reads the chat's latest stage from it, checks it against the cast
    033 stages now, and plays who hears and sees whom by it (docs/SCENES.md#the-stage-041); without one, or for a
    chat it has no stage for, the cast alone decides, as before.
    ``tracer`` receives one ``Trace`` per public call, timed with ``clock`` (048, docs/PERFORMANCE.md);
    without one nothing is timed.
    """

    def __init__(self, store: LibraryStore, authority, handle, *, owner: str,
                 runtime: GenerationRuntime | None = None, policy: InferencePolicy | None = None,
                 secrets: Iterable[object] = (), voices: VoiceCatalog | None = None,
                 fence: RunFence | None = None, budget: Budget = DEFAULT_BUDGET,
                 labeller: LabelRuntime | None = None, label_handle=None,
                 adjudication: AdjudicationLibrary | None = None,
                 effects: TurnEffectsLibrary | None = None, obstacles: ObstacleLibrary | None = None,
                 agendas: Iterable[Agenda] = (), decisions: DecisionLibrary | None = None,
                 stages: StageSource | None = None,
                 tracer: Callable[[Trace], None] | None = None,
                 clock: Callable[[], float] = time.perf_counter) -> None:
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
        if effects is not None and not (isinstance(effects, TurnEffectsLibrary) and effects.store is store
                                        and effects.handle is handle):
            raise _refuse("bad_effects", "effects must be this library's TurnEffectsLibrary on the runner's handle")
        if obstacles is not None and not (isinstance(obstacles, ObstacleLibrary) and obstacles.store is store
                                          and adjudication is not None
                                          and obstacles.adjudication.authority is authority
                                          and obstacles.adjudication.principal == adjudication.principal):
            raise _refuse("bad_obstacles", "obstacles must be this library's ObstacleLibrary on the adjudication key")
        if stages is not None and not callable(getattr(stages, "latest", None)):
            raise _refuse("bad_stages", "stages must be a StageSource: latest(chat) gives a chat's latest stage")
        if decisions is not None and not (isinstance(decisions, DecisionLibrary)
                and decisions.store is store and decisions._authority is authority
                and decisions._principal.startswith("app:")
                and decisions._principal not in {getattr(label_handle, "principal", None),
                    getattr(adjudication, "principal", None),
                    getattr(getattr(runtime, "_handle", None), "principal", None)}):
            raise _refuse("bad_decisions", "decisions needs this library's distinct trusted app key")
        by_actor = _agendas(agendas)
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
        self.effects = effects
        self.obstacles = obstacles
        self.recovery = None if effects is None else EffectRecovery(effects)
        self.decisions = decisions
        self._agendas = by_actor  # director-authored input: played text cannot author or change one
        self.stages = stages
        self._stages: dict[tuple[str, str], Stage] = {}  # the latest stage this runner played each chat with
        self.tracer = tracer
        self.clock = clock
        self.scenes = SceneLibrary(store, handle, owner=owner)
        self.sovereignty = SovereigntyLedger(authority)

    # -- reading ----------------------------------------------------------------------------------

    def set_agendas(self, agendas: Iterable[Agenda]) -> None:
        """Replace device-authored policy input; played text never supplies these values."""
        checked = _agendas(agendas)
        with self.store.lock:
            self._agendas = checked

    def inspect_context(self, chat: ChatRef, actor: InstanceRef) -> dict:
        """Current actor-authorized context only, without providers or writes."""
        context = self._context(chat)
        _readable(context.scene)
        chosen = self._speakers(context, (actor,))  # membership and sovereignty before assembly
        if context.party_problems:
            raise _refuse("party_unreadable", "the cast cannot be read")
        ledger, _ = self._knowledge(chat)
        if ledger.quarantined or ledger.problems:
            raise _refuse("ledger_damaged", "the knowledge ledger cannot be read in full")
        pending = context.scene.pending
        entry = (self.decisions.get(pending.ref, actor)
                 if self.decisions is not None and pending is not None else None)
        if entry is not None and actor not in self._agendas:
            raise _refuse("agenda_changed", "the recorded decision no longer has its authored agenda")
        built = self._actor_assembly(context, actor, chosen[0][1], context.scene.records, ledger,
                                     recorded=entry.record if entry is not None else None)
        brief, pack = built["brief"], built["pack"]

        def origin(value):
            # No selection bases, global source diagnostics or other actors' identities.
            result = {"kind": value.kind}
            for name in ("source", "name", "record", "belief"):
                field = getattr(value, name)
                if field is not None:
                    result[name] = str(field)
            if value.actor == actor:
                result["actor"] = str(actor)
            if value.evidence:
                result["evidence"] = list(value.evidence)
            return result

        return {"label": "Current generation context", "actor": str(actor),
                "system": brief.system, "heard": heard_transcript(brief),
                "items": [{"line": item.line, "tokens": item.tokens, "lane": item.lane,
                           "provenance": origin(item.origin)} for item in pack.items],
                "omitted": [{"reason": item.reason, "tokens": item.tokens, "lane": item.lane,
                             "provenance": origin(item.origin)} for item in pack.omitted],
                "budget": {"tokens": pack.tokens, "max_tokens": pack.budget.max_tokens,
                           "measurement": pack.measurement, "lane_caps": dict(pack.budget.lane_caps),
                           "item_overhead": pack.budget.item_overhead}}

    @traced("open")
    def open(self, chat: ChatRef) -> Scene:
        """The chat as it stands now; nothing is written. With an effects library, its problems also name
        every 021 problem of the campaign (``effects``) and each acceptance cut short (``effect_unsettled``,
        ``<turn id>:<event id>:<shape>``) for ``recover_effects``, or, one another device started, for that device
        (``effect_elsewhere``, ``<turn id>:<event id>:<device tag>``), or one of unknown device for the owner's
        word (``effect_untagged``, ``<turn id>:<event id>``)."""
        scene = self._context(chat).scene
        found = effect_problems(self, scene)
        try:  # 016: a configuration that cannot be read holds every narration; said, never hidden
            in_force(self, chat)
        except SceneRefused as refused:
            found = [*found, ("session_zero", refused.reason)]
        return replace(scene, problems=scene.problems + tuple(found)) if found else scene

    def _context(self, chat: ChatRef) -> _Context:
        if type(chat) is not ChatRef:
            raise _refuse("invalid_field", "chat must be a ChatRef")
        with span("read.library"):
            view = self.store.load()
        try:
            with span("read.snapshot"):
                captured = chat_snapshot(view, chat)
        except (RecordNotFound, ChatCodecError) as error:
            raise _refuse("no_chat", f"{chat} cannot be read ({type(error).__name__})") from None
        problems: list[tuple[str, str]] = [("snapshot", problem) for problem in captured.problems]
        # A bound source that is gone (a card deleted while the app was closed, its lorebook, a scenario) is
        # reported by its exact ref; the story stays, and nothing similarly named is bound in its place.
        problems += [("broken_source", str(resolution.source.ref)) for resolution in captured.snapshot.sources()
                     if resolution.broken is not None]
        with span("read.turns") as timed:
            turns = self.scenes.turns(chat)
            timed.count(files=turns.reads.files, decoded=turns.reads.decoded, reused=turns.reads.reused,
                        turns=len(turns.turns))
        problems += [("turns", f"{path}:{code}") for path, code in turns.problems]
        problems += self.scenes.fork_problems(turns)  # 028: which turns, which devices, from which number
        if turns.conflicted:  # 019's keep-both on a turn: each device reads its own until it is resolved
            problems.append(("turn_conflict", ", ".join(turns.conflicted)))
        with span("read.transcript") as timed:
            history, history_problems = chat_history(self.store, chat, captured.chat, captured.snapshot)
            timed.count(posts=0 if history is None else history.posts)
        problems += history_problems
        with span("read.control"):
            controls = tuple(self.sovereignty.load(chat.campaign_id).controls)
        with span("read.party"):
            party = PartyLibrary(self.store, self.handle, clock=_no_clock, new_id=_no_id).view(chat.campaign_id,
                                                                                               as_of=NOW)
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
        with span("stage.cast"):
            staged, unstaged = _stage(party, chat, candidates)
        problems += unstaged
        stage = None
        played_staged = any(take.on_stage is not None for record in turns.records() for take in record.takes)
        if self.stages is not None:
            with span("read.stage"):
                stage, refusal = self._latest_stage(chat, staged, party.state, played_staged)
            if refusal is not None:
                problems.append(("stage", refusal))
        elif played_staged:  # its turns say it was played on a stage: no source now is not "no stage"
            problems.append(("stage", "stage_lost"))
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
        scene = Scene(chat, turns, controls, cast_t, persona, names, (), staged, history, stage)
        with span("voices"):
            voices = imported_voices(snap, self.authored)
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

    def _latest_stage(self, chat: ChatRef, staged, state, played_staged: bool = False
                      ) -> tuple[Stage | None, str | None]:
        """The chat's latest stage from the source, once it holds: (the stage, ``None``), (``None``, ``None``) when
        the source has none for the chat, or (``None``, why it is refused).

        Refused, failing closed: a source that fails or gives no ``Stage`` (``stage_unreadable``), or none for a
        chat played on a stage (``stage_lost``: this runner saw a stage for it, or one of its takes records an
        on-stage cast, ``played_staged``, so a restart is no way round; whispers would otherwise pass shut doors
        again), a stage of
        another campaign or scene (``scene_mismatch``), one older than, or at the same sequence as but unlike, the
        latest this runner played the chat with (``stale_stage``, ``stage_diverged``), no cast to check it against
        (``cast_unstaged``), and every refusal of 041's ``check_cast`` against the cast 033 stages now: its actors
        are not exactly that cast (``cast_mismatch``), or the cast no longer holds (``stale_cast``). The stage is
        judged as the scene now, as the cast is (033's ``NOW``): the source's latest stage is its word that
        nothing physical changed since its own sequence.
        """
        try:
            found = self.stages.latest(chat)
        except Exception:  # the app's source: whatever it raises, there is no stage to trust
            return None, "stage_unreadable"
        if found is None:  # a chat this runner played by a stage never falls back to the cast alone
            return None, ("stage_lost" if played_staged or stage_key(chat) in self._stages else None)
        if type(found) is not Stage or found.as_of > NOW:  # past "now", it could never be judged as the scene now
            return None, "stage_unreadable"
        if (found.campaign_id, found.scene_id) != stage_key(chat):
            return None, "scene_mismatch"
        refusal = succession(self._stages.get(stage_key(chat)), found)
        if refusal is not None:
            return None, refusal
        self._stages[stage_key(chat)] = found  # kept even when the cast refuses it: an older one never comes back
        if staged is None:
            return None, "cast_unstaged"
        try:
            check_cast(found.at(NOW), staged, state=state)
        except StagingRefused as refused:
            return None, refused.reason
        return found, None

    # -- control ----------------------------------------------------------------------------------

    @traced("seat")
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

    def _table_now(self, chat: ChatRef, principal: str) -> tuple[frozenset[str], bool]:
        """016's audience as the table stands now, read again from the store (a fence calls it under the store
        lock, so a player seated while a call was in flight is in it), and whether who may be present could be
        read in full (``_table_known``: no cast staged, membership that cannot be told, two controllers of one
        character are not)."""
        with span("read.table"):
            context = self._context(chat)
        return _table_audience(context.scene, principal), not context.party_problems and _table_known(context.scene)

    def _check_now(self, chat: ChatRef, principal: str, lines=None) -> None:
        """``check_presented`` for the table as it stands now (``_table_now``). A table that cannot be read in full,
        or at all, is never judged by the part of it that could be: every player with a boundary counts as seated."""
        try:
            audience, complete = self._table_now(chat, principal)
        except Exception:  # the chat, its ledger or its party cannot be read now: who is seated is unknown
            audience, complete = frozenset({principal}), False
        check_presented(self, chat, audience, lines, complete=complete)

    def _write(self, *args, **kwargs) -> StoredTurn:
        with span("write.turn"):
            return self.scenes.write(*args, **kwargs)

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

    @traced("begin")
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
        take = Take(number, "proposed", "opening", scene.cast, lines=(line,), by=principal, on_stage=_here(scene),
                    players=_table(scene))
        record = record.with_take(take)
        # 016, under the store lock as the opening is shown, for the table as it stands then
        self._write(record, stored, unpaused=True, before_commit=lambda: self._check_now(chat, principal, (line,)))
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
                if text and binding.actor not in scene.on_stage:
                    if chosen is not None:  # an off-stage character greets no one; the premise may still open
                        raise _refuse("not_on_stage", "the character of that greeting is not on stage")
                    continue
                if text:
                    name = scene.names.get(binding.actor, binding.actor.instance_id)
                    return SceneLine(line_id, binding.actor, name, _macros(text, name, user),
                                     _room(scene, binding.actor))
        if chosen is not None:
            raise _refuse("unknown_opening", "this chat has no greeting or opening with that id")
        if launch.premise and launch.premise.strip():
            return SceneLine(line_id, None, NARRATOR, _macros(launch.premise, None, user), scene.players)
        raise _refuse("no_opening", "this chat has no greeting or opening to begin with")

    @traced("post")
    def post(self, chat: ChatRef, by, text: str, *, mode: str = "auto") -> TurnRecord:
        """A player's line for their character, saved as a new turn before anything is generated.

        Speech is read with 032 and heard by whoever ``transmit`` says; an action that is not a private
        message is a visible action everyone present sees. A retry of the same post onto its own turn, before
        anything was generated, returns that turn.

        Text the router reads as one of the table's request commands (``pause``, ``resume``, ``skip``,
        ``redirect``: "Grimoire, skip", "redirect: kinder") is no post: it is run as ``request`` (or ``resume``)
        would run it for the same player, and the turn it wrote is returned. A typed skip or redirect is about
        the lines on show (``_on_show``); to name other lines, call ``request``.
        """
        context = self._context(chat)
        scene = context.scene
        principal = self._principal(scene, by)
        command = _table_command(scene, text, mode)
        if command is not None:  # 016's text request lanes: the same path as the equivalent click
            if command.name == "resume":
                return self._resume(scene, principal)
            targets = () if command.name == "pause" else None  # None: the lines on show
            return self._request(scene, principal, command.name, targets, command.argument)
        self._ready(scene)
        persona = scene.persona
        if persona is None:
            raise _refuse("no_persona", "this chat has no player character; ask the cast to go on with generate")
        if persona not in scene.cast:
            raise _refuse("persona_absent", "the player character is not present in this scene")
        if persona not in scene.on_stage:  # 041: in the cast, but physically gone; it speaks in no room
            raise _refuse("not_on_stage", "the player character has left the scene's stage")
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
        if intent.kind == "pc_speech" and intent.reason == "explicit_mode" and wrapped(text.strip(), '"', '"') is not None:
            # 008 takes the quotes off a wholly quoted line in the speech lane as in auto; 032 still reads its words as
            # quoted ones, said aloud privacy words and all, as it would had auto routed the same line
            intent = replace(intent, reason="quoted")
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
                hint = UNREAD_HINTS.get(read.reason, "the line cannot be read as a message; say who it is for, and what")
                raise _refuse(read.reason, hint)
            withheld = read.reason == "unresolved_private"
            post = Post(principal, persona, "action", intent.text, (persona,) if withheld else _seen(scene, persona),
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
        self._write(record, None, after=scene.turns.last)
        return record

    @traced("generate")
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
        # 016, before any call is billed: a held table gets no narration
        check_presented(self, chat, _table_audience(scene, principal), complete=_table_known(scene))
        if context.party_problems:
            raise _refuse("party_unreadable", "who is present cannot be read in full")
        unseated = [str(actor) for actor in scene.cast if scene.controller(actor) is None]
        if unseated:
            raise _refuse("unassigned_actor", f"nobody controls {unseated}; seat the chat first")
        chosen = self._speakers(context, speakers)
        if not chosen and not director:
            raise _refuse("nothing_to_generate", "no NPC here can speak; ask for a director reply")
        rooms = {actor: _room(scene, actor) for actor, _ in chosen}  # who hears each speaker, before any call
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
        existing = {} if self.decisions is None else {
            entry.record.actor: entry.record for entry in self.decisions.decisions(record.ref)}
        if any(actor not in self._agendas for actor in existing):
            raise _refuse("agenda_changed", "a recorded decision no longer has its authored agenda")
        if existing:
            # A caller narrowing speakers cannot bypass a recorded actor's changed authority/agenda.
            recorded_speakers = self._speakers(context, tuple(existing))
            self._briefs(context, recorded_speakers, heard_turns, number, existing=existing)
        deciders = [actor.instance_id for actor, _ in chosen
                    if self.decisions is not None and actor in self._agendas and actor not in existing]
        try:
            with span("plan"):
                plan = plan_generation_turn(chat.campaign_id, plan_turn,
                                            present=[a.instance_id for a in scene.on_stage],
                                            speakers=[a.instance_id for a, _ in chosen], controls=scene.controls,
                                            director_reply=director, policy=self._decision_policy(record),
                                            decisions=deciders)
        except GenerationError as error:
            raise _refuse(error.reason, "the turn cannot be planned") from None
        if director and not any(call.stage is InferenceStage.DIRECTOR_GENERATION for call in plan.calls):
            raise _refuse("director_declined", "the inference policy allows no director reply")
        try:
            assemblies = {}
            briefs, intentions = self._briefs(context, chosen, heard_turns, number,
                                              existing=existing, assemblies=assemblies)
            director_view = self._director(context, heard_turns) if director else None
        except GenerationError as error:
            raise _refuse(error.reason, "a brief cannot be built") from None
        planned = tuple(CallRecord(call.call_id.phase, call.stage, call.call_id.actor_id, call.call_id.ordinal,
                                   "planned") for call in plan.calls)
        running = Take(number, "running", "generated", scene.cast, calls=planned, declined=_declined(plan),
                       by=principal, on_stage=_here(scene), players=_table(scene))
        superseded = record.running
        if superseded is not None:
            record = record.with_take(replace(superseded, state="superseded"))
        record = record.with_take(running).with_intentions(number, intentions)
        room = len(planned) * (LINE_BYTES + plan.policy.max_attempts_per_call * ATTEMPT_BYTES) + TAKE_BYTES
        if self.scenes.size(record, stored) + room > MAX_TURN_BYTES:  # before any call: nothing is billed
            raise _refuse("turn_too_large", "this turn's file has no room for another take; keep a take or pass")
        claimed = []  # the fence is claimed as the running take is published, never after: an older run
        # resuming between the two would otherwise cancel the reroll that superseded it
        stored = self._write(record, stored, after=scene.turns.last if stored is None else None, unpaused=True,
                                   on_commit=lambda: claimed.append(self.fence.claim(str(chat))))
        (token,) = claimed
        meter = TurnMeter(TurnUsageLedger(plan))
        silent = tuple(voice for actor, voice in self._voices(context) if actor not in {a for a, _ in chosen})
        decision_outcomes = {}
        try:
            with span("provider", calls=len(plan.calls)):
                try:
                    if self.decisions is not None:
                        briefs, intentions = self._decide(context, record, stored, running, plan, meter,
                                                          token, assemblies, existing, decision_outcomes)
                    outcome = self.runtime.run_turn(meter, briefs, controls=scene.controls,
                                                    director=director_view[0] if director_view else None,
                                                    token=token, scene_voices=silent, hears=_hearing(scene, rooms))
                finally:
                    record_calls(meter)
        except Exception as error:
            # Once running was published, every attempted call must land even if ledger/rebrief fails.
            outcome = None
            failure = getattr(error, "reason", "decision_failed")
        else:
            failure = None
        with span("review"):
            take = self._finished(record, running, plan, meter, outcome, failure, scene,
                                  director_view[1] if director_view else (), rooms, decision_outcomes)
        return self._land(chat, stored, take, token, intentions, principal)

    # -- generation helpers -----------------------------------------------------------------------

    def _decision_policy(self, record: TurnRecord) -> InferencePolicy:
        """Reserve decision capacity across every take of the scene turn, including crashed runs.

        A published planned call can still be executing, even on a superseded take. Only a finished
        call known not to have sent anything releases its reservation; a paid or uncertain attempt
        spends one slot regardless of its outcome. A stored issued outcome without attempt detail
        is unknown usage and also reserves a slot. Other stages retain the configured policy.
        """
        cap = self.policy.cap_for(InferenceStage.DECISION)
        if cap is None:
            return self.policy
        spent = sum(
            call.status == "planned"
            or (not call.attempts and call.status != "not_issued")
            or any(attempt.outcome != "not_sent" for attempt in call.attempts)
            for take in record.takes for call in take.calls if call.stage is InferenceStage.DECISION)
        if not spent:
            return self.policy
        caps = dict(self.policy.per_turn_call_caps)
        remaining = max(0, cap - spent)
        if remaining:
            caps[InferenceStage.DECISION] = remaining
        else:
            caps.pop(InferenceStage.DECISION, None)  # absent is the policy's representation of zero
        return replace(self.policy, per_turn_call_caps=caps)

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
        if speakers is None:  # 041: a cast member off the stage is not a speaker
            return [(actor, voiced[actor]) for actor in scene.npcs if actor in voiced and actor in scene.on_stage]
        chosen = []
        for actor in speakers:
            if type(actor) is not InstanceRef or actor not in scene.cast:
                raise _refuse("not_present", f"{actor} is not present in this scene")
            if scene.controller(actor) != DIRECTOR:
                raise _refuse("not_an_npc", f"{actor} is a player character; its player speaks for it")
            if actor not in scene.on_stage:
                raise _refuse("not_on_stage", f"{actor} has left the scene's stage; it speaks in no room")
            if actor not in voiced:
                raise _refuse("no_voice", f"{actor} has no voice to speak with")
            if actor in dict(chosen):
                raise _refuse("invalid_field", f"{actor} is named twice")
            chosen.append((actor, voiced[actor]))
        return chosen

    def _knowledge(self, chat: ChatRef):
        with span("read.knowledge"):
            ledger = EvidenceLibrary(self.store, self.handle, owner=self.owner).read(chat.campaign_id,
                                                                                     requested_by=DIRECTOR)
            return ledger, _latest(ledger.knowledge)

    def _actor_assembly(self, context, actor, voice, turns, ledger, *, recorded=None):
        """One shared pack/brief assembly for generation and read-only inspection."""
        with span("brief.npc") as timed:
            built = self._assemble_actor(context, actor, voice, turns, ledger, recorded=recorded)
            timed.count(heard=len(built["brief"].heard))
            return built

    def _assemble_actor(self, context, actor, voice, turns, ledger, *, recorded=None):
        scene, snap, chat = context.scene, context.captured.snapshot, context.scene.chat
        as_of = _latest(ledger.knowledge)
        _, bare = outcome_lines(self, turns, scene.names)
        heard = heard_for(turns, actor, scene.names, before=scene.imported, outcomes=bare)
        seen = _perceived(scene, actor)
        with span("pack") as packed:
            built = actor_context(context.view, chat, ledger, actor, as_of=as_of, budget=self.budget,
                                  present=seen, scene_text=render(heard))
            packed.count(items=len(built.pack.items), omitted=len(built.pack.omitted))
        knowledge = actor_knowledge(ledger.knowledge, actor, as_of=as_of)
        view = snapshot_actor_view(snap.instance(actor), snap, knowledge)
        kwargs = dict(heard=heard, secrets=self.secrets, names=scene.names, context=built.pack, snapshot=snap)
        brief = npc_brief(view, voice, **kwargs)
        agenda = self._agendas.get(actor)
        choice = None
        if agenda is not None:
            choice = self._choose(context, ledger, agenda, knowledge, brief, seen)
            if recorded is not None:
                try:
                    choice = replay(recorded, agenda, knowledge, present=choice.present,
                                    controls=scene.controls, unrecognised=choice.unrecognised)
                except DecisionError as error:
                    raise _refuse(error.reason, "the recorded decision cannot be replayed") from None
            brief = npc_brief(view, voice, **kwargs, intention=choice)
        return dict(brief=brief, pack=built.pack, choice=choice, view=view, voice=voice,
                    kwargs=kwargs, knowledge=knowledge, agenda=agenda)

    def _briefs(self, context: _Context, chosen, turns: Sequence[TurnRecord], take: int,
                *, existing=None, assemblies=None) -> tuple[list, tuple[Intention, ...]]:
        if not chosen:
            return [], ()
        ledger, _ = self._knowledge(context.scene.chat)
        briefs, intentions = [], []
        for actor, voice in chosen:
            built = self._actor_assembly(context, actor, voice, turns, ledger,
                                         recorded=(existing or {}).get(actor))
            if assemblies is not None:
                assemblies[actor] = built
            briefs.append(built["brief"])
            if built["choice"] is not None:
                intentions.append(_intention(take, built["choice"], _sight(context.scene, actor)))
        return briefs, tuple(intentions)

    def _decide(self, context, record, stored, running, plan, meter, token, assemblies, existing, outcomes):
        scene = context.scene
        for call in plan.calls:
            if call.stage is not InferenceStage.DECISION:
                continue
            actor = next(actor for actor in assemblies if actor.instance_id == call.actor_id)
            built = assemblies[actor]
            outcome = self.runtime.decide(meter, call, built["brief"], controls=scene.controls, token=token)
            outcomes[call.call_id] = outcome  # retain status before any post-call operation can fail
            if outcome.pick is not None:
                basis = built["choice"]
                built["choice"] = choose(built["agenda"], built["knowledge"], present=basis.present,
                                          unrecognised=basis.unrecognised, controls=scene.controls,
                                          selected=outcome.pick)
            built["decision"] = outcome
            built["brief"] = npc_brief(built["view"], built["voice"], **built["kwargs"],
                                       intention=built["choice"])
        for actor, built in assemblies.items():
            choice = built["choice"]
            if choice is None or actor in existing:
                continue
            result = built.get("decision")
            # A skip with too few candidates is a policy record, as required by 045's codec.
            model = result is not None and result.status != "skipped"
            decided = record_decision(choice, record.ref,
                                      call=str(result.call_id) if model else None,
                                      fallback=result.reason if model and result.pick is None else None)
            # DurableAuthority.lock is this store's RLock. LibraryStore holds an exclusive process
            # lease for its entire open session; no second writer process may open this library.
            with self.authority.lock:
                current_context = self._context(record.chat)
                current = current_context.scene
                last = current.turns.last
                if (token.stale or current.paused or last is None or last.etag != stored.etag
                        or last.record.ref != record.ref or not last.record.open
                        or last.record.running != running):
                    raise _refuse("turn_changed", "a newer take replaced this decision run")
                if self._agendas.get(actor) != built["agenda"]:
                    raise _refuse("agenda_changed", "the authored agenda changed during the decision")
                current_voice = self._speakers(current_context, (actor,))[0][1]
                self._actor_assembly(current_context, actor, current_voice, current.records,
                                     self._knowledge(record.chat)[0], recorded=decided)
                self.decisions.record(decided)
        briefs = [built["brief"] for built in assemblies.values()]
        intentions = tuple(_intention(running.take, built["choice"], _sight(scene, actor))
                           for actor, built in assemblies.items() if built["choice"] is not None)
        return briefs, intentions

    def _choose(self, context: _Context, ledger, agenda: Agenda, knowledge, brief,
                seen: Sequence[InstanceRef]) -> Choice:
        """039's choice for ``agenda``'s NPC, once its agenda checks against this campaign's ledger and actors.

        Fail closed: a ledger with a quarantined record or a problem may hide the very belief a limit watches
        for, and an agenda naming what the campaign lacks would let a limit lapse silently, so neither is used
        (refused ``ledger_damaged``, or by 039's own reason, such as ``unknown_proposition``). The cast split is the
        take's on-stage cast (``Scene.on_stage``), so an actor who has left the stage is never ``Present``; of it,
        whoever the NPC does not perceive (``seen``: hidden, or behind a shut door) is ``unrecognised``, which no
        039 condition sees, so ``Present`` of them is unmet and a limit watching for them does not apply.
        """
        scene = context.scene
        if ledger.quarantined or ledger.problems:
            raise _refuse("ledger_damaged", f"the knowledge ledger cannot be read in full, so the agenda of "
                          f"{agenda.actor} is not used")
        try:
            check_agenda(agenda, propositions=[p.proposition_id for p in ledger.knowledge.propositions],
                         actors=[ref for ref in context.view.instances if ref.campaign_id == scene.chat.campaign_id])
            here = scene.on_stage  # 041: the take's scene is who is on stage, as 027's plan and 047's referee hold
            # ...but no condition sees whom the NPC cannot perceive (hidden, behind a shut door): 039's unrecognised
            unseen = unrecognised(brief, here) | (frozenset(here) - frozenset(seen))
            return choose(agenda, knowledge, present=[actor for actor in here if actor not in unseen],
                          unrecognised=unseen, controls=scene.controls)
        except GoalError as error:
            raise _refuse(error.reason, f"the agenda of {agenda.actor} cannot be used") from None

    def _director(self, context: _Context, turns: Sequence[TurnRecord]):
        with span("brief.director"):
            return self._director_brief(context, turns)

    def _director_brief(self, context: _Context, turns: Sequence[TurnRecord]):
        scene, chat = context.scene, context.scene.chat
        scenario = _scenario_text(context)
        if not scenario:
            raise _refuse("no_scenario", "a director reply needs a bound scenario or a premise")
        table = table_lines(turns, scene.names, before=scene.imported,  # 041: on a stage, what a PC heard then
                            outcomes=outcome_lines(self, turns, scene.names)[0], as_heard=True)
        with span("pack") as packed:
            lore = chat_lore(context.view, chat, table_text=render(table), budget=self.budget).lore
            packed.count(items=len(lore.items), omitted=len(lore.omitted))
        names = {actor.instance_id: name for actor, name in scene.names.items()}
        here = [a.instance_id for a in scene.on_stage]  # 041: a cast member off stage is not in the scene's present
        brief = director_brief(scenario, campaign_id=chat.campaign_id, present=here,
                               controls=scene.controls, names=names, heard=table, secrets=self.secrets, lore=lore,
                               snapshot=context.captured.snapshot)
        return brief, tuple(f"{ref}#{name}" for ref, name in brief.unscreened)

    def _finished(self, record: TurnRecord, running: Take, plan, meter: TurnMeter, outcome, failure: str | None,
                  scene: Scene, unscreened: tuple[str, ...],
                  rooms: Mapping[InstanceRef, tuple[InstanceRef, ...]], decision_outcomes=None) -> Take:
        """The take a run ended with: every call's outcome and attempts, and the lines it kept. An NPC's line is
        heard by ``rooms``' audience of its speaker, judged before the run; a line by anyone else is rejected."""
        by_call = {}
        for attempt in meter.ledger.attempts:
            by_call.setdefault(attempt.call_id, []).append(attempt)
        statuses = {call.call_id: (call.status, call.reason) for call in outcome.calls} if outcome else {}
        # 035's persisted call schema has no skipped state: retain its specific reason and
        # the meter's not_sent attempt under not_issued rather than inventing a schema migration.
        statuses.update({key: ("not_issued" if value.status == "skipped" else value.status, value.reason)
                         for key, value in (decision_outcomes or {}).items()})
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
                    if line.actor not in rooms:
                        rejected[line.call_id] = "not_a_speaker"
                        continue
                    audience = rooms[line.actor]
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

    def _land(self, chat: ChatRef, stored: StoredTurn, take: Take, token,
              intentions: tuple[Intention, ...] = (), principal: str | None = None) -> Take:
        """Write the finished take over its running one; a run gone stale keeps its usage and no lines. The run's
        intentions go with it, onto another version of its turn too (a sync conflict resolved meanwhile).

        A take that would make its turn file too large (``turn_too_large``) still lands its usage: first without
        its lines (``_unlined``), then without this take's intentions as well; only a turn too large even for
        that is refused, as the store refuses it. Shrinking spends none of the re-reads a changed turn gets.

        Its lines are shown only if 016 still presents them to the table as it stands when the take commits (read
        again, under the store lock: a boundary set, or a player seated, while the run was in flight holds them):
        otherwise it lands without them (``_held_back``), its usage kept, as a take without room does.
        """
        def fence() -> None:
            if take.state != "stale" and token.stale:
                raise _Stale
            if take.lines:
                try:
                    if principal is None:  # no requester to judge it for: shown to nobody
                        raise _refuse(HELD, "no table to present this take to")
                    self._check_now(chat, principal, take.lines)
                except SceneRefused as refused:
                    raise _Held(refused.reason) from None

        published = next(t for t in stored.record.takes if t.take == take.take)  # the running take, as written
        bare = False  # this take's intentions are left out: with them its turn file would be too large
        for _ in range(WRITE_RETRIES):
            try:
                while True:
                    landed = stored.record.with_take(take)
                    if bare:
                        landed = landed.with_intentions(take.take, ())
                    elif intentions:
                        landed = landed.with_intentions(take.take, intentions)
                    try:
                        self._write(landed, stored, landing=True, before_commit=fence)
                        return take
                    except _Held as held:  # shown to nobody: the usage lands, the lines do not
                        take = _held_back(take, held.reason)
                        continue
                    except SceneRefused as error:
                        if error.reason != "turn_too_large" or bare:
                            raise
                    if take.lines:
                        take = _unlined(take)
                    else:
                        bare = True
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

    @traced("label")
    def label(self, chat: ChatRef, by, *, obstacle: str | None = None) -> Label:
        """Propose 029 labels for the open turn's action: which offered facts bear on it, what is at stake.

        Asked by the acting character's player or the owner. One ``labelling`` call on the labeller's
        untrusted handle; its reply may cite only facts from that character's own context pack. The
        labelling is written ``running`` before the call, so a crash leaves its usage on record, then
        ``proposed`` (labels to confirm) or ``refused`` (why). An earlier unconfirmed labelling of the turn
        is superseded. Nothing is framed, decided or accepted here. With ``obstacle`` (a 040 obstacle's id), the
        action is an attempt at it: the labeller chooses a suggested approach or a novel one, and the obstacle's
        own facts and stakes for that approach are added by rule.
        """
        return label_action(self, chat, by, obstacle)

    @traced("confirm")
    def confirm(self, chat: ChatRef, by) -> Outcome | None:
        """Frame the open action exactly as its latest proposed labelling says (029), unedited.

        Only the acting character's player confirms, with their own trusted handle; a wrong labelling is
        fixed by labelling again, never by editing it. The frame is recorded with the adjudication key and
        the labelling written ``framed``. An action the rules decide (straightforward, impossible) is decided
        at once, by rule, and its ``Outcome`` returned; an uncertain one waits for ``decide`` (``None``).
        """
        return confirm_action(self, chat, by)

    @traced("decide")
    def decide(self, chat: ChatRef, by, result: str, *, basis: str = "adjudicated", cites=(),
               consequences=(), fingerprint: str | None = None) -> Outcome:
        """Decide the open turn's framed, uncertain action: the director (the library owner) adjudicates
        it (``basis="adjudicated"``), or its player consents to a setback (``basis="consent"``), citing
        the frame's factors and naming the stakes that come true. 029 checks every rule.

        A consent is this call alone, by the acting player with their own trusted handle, and names the
        ``fingerprint`` of the frame it was given for (``framed``): a consent for any other attempt, or an
        earlier revision, is ``fingerprint_mismatch``. The director may name one too, and it is checked."""
        return decide_action(self, chat, by, result, basis=basis, cites=cites, consequences=consequences,
                             fingerprint=fingerprint)

    @traced("framed")
    def framed(self, chat: ChatRef) -> FramedAction | None:
        """The open turn's framed action as 029 holds it (latest revision, assessed now) and its fingerprint,
        which a consent names; ``None`` when nothing is framed (or 029 holds no frame for it yet)."""
        return framed_of(self, self._context(chat).scene.pending)

    @traced("outcome")
    def outcome(self, chat: ChatRef) -> Outcome | None:
        """The recorded outcome of the open turn's framed action, if it was decided."""
        return outcome_of(self, self._context(chat).scene.pending)

    # -- accepting an effect ---------------------------------------------------------------------------

    @traced("propose_effect")
    def propose_effect(self, chat: ChatRef, by, turn: str, summary: str, operations=(), holdings=None, *,
                       abilities=(), waive=()):
        """Propose ``turn``'s effect as structured 021 operations and holdings lines, whole (no model call).

        For a kept turn or a decided action, by the owner or the player whose post the turn is. It takes the
        next campaign sequence, names who must endorse it (each acting player character's player), supersedes
        the turn's earlier live proposal, and is written ``proposed``. Ability use is not in this slice. What
        the turn's 040 obstacle attempt made due comes first, but for the ``Waiver`` values the director gives
        in ``waive`` (a due change the world cannot take: docs/SCENES.md).
        """
        return propose_effect(self, chat, by, turn, summary, operations, holdings, abilities=abilities, waive=waive)

    @traced("approve_effect")
    def approve_effect(self, chat: ChatRef, by, turn: str, number: int):
        """The director's (owner's) key on a proposed effect; when it is the last key, the effect is accepted."""
        return approve_effect(self, chat, by, turn, number)

    @traced("endorse_effect")
    def endorse_effect(self, chat: ChatRef, by, turn: str, number: int, fingerprint: str):
        """A player's own key on the exact effect shown (its fingerprint); the last key accepts it."""
        return endorse_effect(self, chat, by, turn, number, fingerprint)

    @traced("recover_effects")
    def recover_effects(self, chat: ChatRef, by, *, abort: bool = False, endorsers=(), own: bool = False):
        """Settle every acceptance of this chat that was cut short, by what landed (030); the owner only.
        ``abort`` undoes what landed instead, with every endorser of it present (``endorsers``: their handles).
        An endorser present stands in for an endorsement the authority holds no record of (shape B only).
        An acceptance another device started is never settled here (``accepted_elsewhere``, after this device's),
        nor one that names no device and of which nothing is here, unless ``own`` says it is this library's
        (``untagged_acceptance``)."""
        return recover_effects(self, chat, by, abort=abort, endorsers=endorsers, own=own)

    # -- the table's choices ------------------------------------------------------------------------

    @traced("keep")
    def keep(self, chat: ChatRef, by, take: int, *, acknowledge_unscreened: bool = False,
             turn: str | None = None) -> TurnRecord:
        """Keep one proposed take: its shown lines become the transcript, and the turn closes.

        ``turn`` is the turn the table read the take on (044): take numbers start again on every turn, so a
        late or repeated keep names its turn and is refused (``turn_changed``) once another turn is open."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        stored = self._open(scene)
        if scene.paused:
            raise _refuse("paused", "play is paused")
        record = stored.record
        if turn is not None and record.turn_id != turn:
            raise _refuse("turn_changed", "that take was on another turn; read the chat again")
        found = next((t for t in record.takes if t.take == take), None)
        if found is None or found.state != "proposed":
            raise _refuse("not_proposed", "only a proposed take can be kept")
        check_decided(self, record)
        if not found.shown():
            raise _refuse("empty_take", "this take has no line to keep")
        if found.unscreened and not acknowledge_unscreened:
            raise _refuse("unscreened_lore", "this take was written with director lore no screen covered; "
                                            "keep it only after reading it")
        _stage_ok(scene)
        if not _stands(scene, found, found.shown()):
            raise _refuse("cast_changed", "who is present, who plays them, or who is in earshot changed since this "
                                          "take was written; ask again")
        if any(_differs(intention.perceived, _sight(scene, intention.actor))
               for intention in record.intentions if intention.take == take):  # 039 chose by whom it could see
            raise _refuse("cast_changed", "who an NPC can see changed since it chose what to pursue; ask again")
        if record.running is not None:  # a run still in flight loses: its take lands stale
            record = record.with_take(replace(record.running, state="superseded"))
        record = record.with_take(replace(found, state="kept", by=principal))
        # 016 again, as the lines become transcript, for the table read again under the store lock: a boundary
        # set since they were proposed (or a player who sat down since) holds them; nothing caches a presentation
        self._write(record, stored, unpaused=True,
                    before_commit=lambda: self._check_now(chat, principal, found.shown()))
        return record

    @traced("keep_lines")
    def keep_lines(self, chat: ChatRef, by, lines: Iterable[str], *, acknowledge_unscreened: bool = False,
                   turn: str | None = None) -> TurnRecord:
        """Keep chosen lines of the open turn's proposed takes, at most one a speaker, as the turn's one kept
        take (``play.mixed``): what ``keep`` would store had one take held exactly those lines."""
        return keep_lines(self, chat, by, lines, acknowledge_unscreened=acknowledge_unscreened, turn=turn)

    @traced("strike")
    def strike(self, chat: ChatRef, by, line_id: str) -> TurnRecord:
        """Strike one kept line from the transcript: no one hears it from now on."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        _readable(scene)
        stored, take, line = _find_line(scene, line_id)
        if take.state != "kept" or not line.shown:
            raise _refuse("not_kept", "only a shown line of a kept take can be struck")
        if principal not in self.authority.owners and not _heard(scene, take, line, principal):
            raise _refuse("not_heard", "no character of yours heard that line; only its listeners or the owner "
                                       "may strike it")
        record = stored.record.with_take(_mark(take, {line_id}, "struck", principal))
        self._write(record, stored)
        return record

    @traced("pass_turn")
    def pass_turn(self, chat: ChatRef, by, *, turn: str | None = None) -> TurnRecord:
        """Close the open turn with nothing kept, so play can go on. ``turn``, as for ``keep``, names the turn
        meant (044): a late or repeated pass is refused ``turn_changed`` once another turn is open."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        stored = self._open(scene)
        if scene.paused:
            raise _refuse("paused", "play is paused")
        record = stored.record
        if turn is not None and record.turn_id != turn:
            raise _refuse("turn_changed", "that was another turn; read the chat again")
        framed = record.framed
        # 029's lock: a decision of this turn's action (``decide``, or ``confirm``'s by rule) re-reads the turn under
        # it, so a pass and a decision are ordered: one decided before the pass stands, and one after is refused
        with self.store.lock:
            if framed is not None and framed.against is not None and outcome_of(self, record) is None:
                pass_attempt(self, record)  # 040: refused, or withdrawn for good before the turn is written
            if record.running is not None:
                record = record.with_take(replace(record.running, state="superseded"))
            record = replace(record, passed_by=principal)
            self._write(record, stored, unpaused=True)
        return record

    @traced("request")
    def request(self, chat: ChatRef, by, kind: str, targets: Iterable[str] = (), *,
                direction: str | None = None) -> TurnRecord:
        """016's mid-session request: ``pause`` stops play; ``skip``, ``redirect`` and ``rephrase`` withdraw
        the named lines (``redirect``/``rephrase`` record the direction on their take).

        A line of a kept take whose turn holds an accepted 021 effect depicts accepted canon (``segments``): a
        skip passes over its depiction (the line is withdrawn) and the effect, its event and the turn's outcome
        stay exactly as accepted; a redirect leaves it alone, and one that would withdraw nothing is refused
        ``accepted_canon``."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        return self._request(scene, principal, kind, tuple(targets), direction)

    def _request(self, scene: Scene, principal: str, kind: str, targets: tuple[str, ...] | None,
                 direction: str | None) -> TurnRecord:
        """``request`` for an authenticated ``principal``; ``targets=None`` is a typed request: the lines on show."""
        chat = scene.chat
        _readable(scene, pausing=kind == "pause")
        owner = principal in self.authority.owners
        if targets is None:
            targets = _on_show(scene, principal, owner)
            if not targets:
                raise _refuse("no_targets", f"no line you heard is on show to {kind}; name the lines it is about")
        if kind in ("skip", "redirect", "rephrase") and not targets:
            raise _refuse("no_targets", f"a {kind} names the lines it is about")
        if kind != "pause" and not owner:  # a player withdraws only what their own character heard
            heard = {line.line_id: _heard(scene, take, line, principal) for stored in scene.turns.turns
                     for take in stored.record.takes for line in take.lines}
            if any(heard.get(target) is False for target in targets):
                raise _refuse("not_heard", "no character of yours heard that line; only its listeners or the owner "
                                           "may withdraw it")
        try:
            request = BoundaryRequest(chat.campaign_id, f"{chat.chat_id}.request", principal, kind, targets,
                                      direction or "")
        except ValueError as error:
            raise _refuse("invalid_field", str(error)[:200]) from None
        boundary = BoundaryContext(chat.campaign_id, _table_audience(scene, principal))
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
            self._write(record, stored, last=True)
            return record
        stored, _take, _line = _find_line(scene, targets[0])
        decision = decide_request(request, boundary, segments(stored.record))
        if decision.status != "accepted":
            raise _refuse("unknown_line" if decision.reason == "unknown_segment" else decision.reason,
                          "the request was refused")
        # A skip passes over accepted canon's depiction (``skip_presentation``) as it withdraws a proposal; the
        # effect and its event are not on the turn's takes, so nothing here can touch them.
        withdrawn = set(decision.withdrawn_proposals) | set(decision.skip_presentation) | set(decision.rerender)
        if not withdrawn:  # a redirect of accepted canon only: it withdraws proposals, and there are none
            raise _refuse("accepted_canon", "those lines depict an accepted effect: a redirect leaves them; "
                                            "skip them, or rephrase them")
        record = stored.record
        for take in record.takes:
            hit = withdrawn & {line.line_id for line in take.shown()}
            if not hit:
                continue
            changed = _mark(take, hit, "withdrawn", principal)
            if decision.direction:
                changed = replace(changed, direction=decision.direction)
            record = record.with_take(changed)
        self._write(record, stored)
        return record

    @traced("resume")
    def resume(self, chat: ChatRef, by) -> TurnRecord:
        """Lift a pause: the player who paused lifts their own, the library's owner every one (so a pauser who has
        left the table never locks the chat). A pause on a turn a fork choice set aside is lifted the same way;
        play goes on once no pause stands. Returns the chat's last turn."""
        scene = self._context(chat).scene
        return self._resume(scene, self._principal(scene, by))

    def _resume(self, scene: Scene, principal: str) -> TurnRecord:
        _readable(scene)
        with span("write.turn"):
            after = self.scenes.resume(scene.turns, principal, owner=principal in self.authority.owners)
        assert after.last is not None  # a pause needs a turn, and a set-aside one a chosen line
        return after.last.record

    @traced("choose")
    def choose(self, chat: ChatRef, by, turn: str) -> Scene:
        """028: at a forked chat, the branch whose first turn is ``turn`` goes on; the others are set aside.
        Only the library's owner may choose (``play.forks``)."""
        return choose_branch(self, chat, by, turn)

    # -- session zero (016) ---------------------------------------------------------------------------

    @traced("boundaries")
    def boundaries(self, chat: ChatRef, by) -> Boundaries:
        """The chat's session zero as one table member may see it: every boundary in force for the table,
        anonymised, the member's own in full, its revision, and whether narration is held now. Nothing is written."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        return boundary_view(self, chat, principal, _table_audience(scene, principal), _table_known(scene))

    @traced("change_boundaries")
    def change_boundaries(self, chat: ChatRef, by, change: BoundaryChange) -> Boundaries:
        """One table member's 016 change to the chat's session zero (``set``/``withdraw`` their own boundary,
        ``agree``/``unagree`` to a group one), from the player-input channel only: ``change.requested_by`` must be
        the principal ``by`` authenticates (a player of a present character, or the owner). 016's ownership rules
        decide; the scope is written by compare-and-swap with the next revision. It goes on while play is paused
        (a limit is never kept waiting), and applies from the next narration shown: a take in flight, or proposed
        and not yet kept, is checked again as it lands and as it is kept."""
        scene = self._context(chat).scene
        principal = self._principal(scene, by)
        return change_boundary(self, chat, principal, lambda: self._table_now(chat, principal), change)

    # -- checks -----------------------------------------------------------------------------------

    def _ready(self, scene: Scene) -> None:
        _readable(scene)
        if scene.staged is None:
            raise _refuse("party_unreadable", "who is present cannot be staged; nobody is on stage")
        _stage_ok(scene)
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


__all__ = ["DEFAULT_BUDGET", "Boundaries", "Scene", "SceneRunner"]


#: The router's commands ``post`` runs as the table's requests (016): each by the path its click takes.
TABLE_COMMANDS = frozenset({"pause", "resume", "skip", "redirect"})


def _table_command(scene: Scene, text: object, mode: str) -> Command | None:
    """The table request ``text`` routes to (008), if any; anything else is left to the post's own routing,
    which refuses what it cannot play (``invalid_field``, ``uncertain``, ``not_in_this_slice``)."""
    if not isinstance(text, str) or not text.strip():
        return None
    persona = scene.persona.instance_id if scene.persona is not None else None
    try:
        intent = route(Utterance(text, mode), RoutingContext(active_pc=persona, can_direct=False))
    except ValueError:
        return None
    if intent.kind == "ui_command" and intent.command is not None and intent.command.name in TABLE_COMMANDS:
        return intent.command
    return None


def _on_show(scene: Scene, principal: str, owner: bool) -> tuple[str, ...]:
    """The lines a typed skip or redirect is about: on the chat's last turn, while it is open, its newest take by
    number, only while that take is still proposed; once the turn is closed, its kept take. Of that take, the shown
    lines the requester heard (``_heard``; the owner, every shown line). None otherwise (``no_targets``): a typed
    request never walks back to an older take or turn, so a retried one withdraws nothing more."""
    stored = scene.turns.last
    if stored is None:
        return ()
    record = stored.record
    if record.open:
        take = max(record.takes, key=lambda t: t.take, default=None)
        take = take if take is not None and take.state == "proposed" else None
    else:
        take = record.kept
    if take is None:
        return ()
    return tuple(line.line_id for line in take.shown() if owner or _heard(scene, take, line, principal))


def _heard(scene: Scene, take: Take, line: SceneLine, principal: str) -> bool:
    """Whether ``principal`` heard the line: a character they control (003, as recorded now) is among its listeners.
    On a staged take the listeners are only its audience's player characters as of that take (``Take.players``,
    as ``table_lines(as_heard=True)`` reads them), so an NPC handed to a player later brings none of what it heard;
    a staged take that recorded no players has none (fail closed). An unstaged take counts its whole audience."""
    listeners: Iterable[InstanceRef] = line.audience
    if take.on_stage is not None:
        listeners = set(line.audience) & set(take.players or ())
    return any(scene.controller(actor) == principal for actor in listeners)


def _agendas(agendas: object) -> dict[InstanceRef, Agenda]:
    """039's agendas by actor: exact ``Agenda`` values, one per actor (``bad_agenda`` otherwise)."""
    if isinstance(agendas, (str, bytes, Mapping)):
        raise _refuse("bad_agenda", "agendas must be a collection of Agenda values")
    try:
        found = tuple(agendas)  # type: ignore[arg-type]
    except TypeError:
        raise _refuse("bad_agenda", "agendas must be a collection of Agenda values") from None
    by_actor: dict[InstanceRef, Agenda] = {}
    for agenda in found:
        if type(agenda) is not Agenda:
            raise _refuse("bad_agenda", "agendas must be Agenda values")
        if agenda.actor in by_actor:
            raise _refuse("bad_agenda", f"{agenda.actor} has more than one agenda")
        by_actor[agenda.actor] = agenda
    return by_actor


def _intention(take: int, choice: Choice, perceived: tuple[InstanceRef, ...] | None = None) -> Intention:
    """The turn record's account of ``choice``: what was pursued and refused, the approaches weighed up to the
    chosen one and those after it a limit forbids, and why. The rest after the choice (outranked, or unmet)
    are only counted, so an agenda's size never multiplies into every take's turn file. ``perceived`` is
    ``_sight``'s answer for its NPC when it chose."""
    goal, approach = choice.goal, choice.approach
    entries = choice.considered
    cut = next((index for index, entry in enumerate(entries) if entry.outcome == "chosen"), len(entries))
    kept = (*(entry for entry in entries[:cut + 1] if entry.outcome != "outranked"),
            *(entry for entry in entries[cut + 1:] if entry.outcome == "forbidden"))
    considered = tuple(IntentionOption(entry.goal_id, entry.approach_id, entry.outcome,
                                       tuple(condition.describe() for condition in entry.unmet), entry.forbidden_by)
                       for entry in kept)
    return Intention(take, choice.actor, choice.as_of, tuple(choice.present), tuple(choice.unrecognised),
                     goal.goal_id if goal is not None else None, approach.approach_id if approach is not None else None,
                     tuple(limit.limit_id for limit in choice.refusals), considered, explain(choice),
                     len(entries) - len(kept), perceived)


def _sight(scene: Scene, actor: InstanceRef) -> tuple[InstanceRef, ...] | None:
    """Who of the take's on-stage cast ``actor`` perceives now, which 039's choice is judged against (``None``
    without a stage, where the whole cast is)."""
    if scene.stage is None:
        return None
    seen = set(_perceived(scene, actor))
    return tuple(other for other in scene.on_stage if other in seen)


def _differs(then: tuple[InstanceRef, ...] | None, now: tuple[InstanceRef, ...] | None) -> bool:
    return (then is None) != (now is None) or set(then or ()) != set(now or ())


def _stands(scene: Scene, take: Take, lines: Iterable[SceneLine]) -> bool:
    """Whether ``take`` was written for the cast as it stands now (who is in the chat, who is present, who the
    player characters are), and each of ``lines`` is still an NPC's, heard by exactly who would hear it now
    (narration: by the player characters). What keeping those lines requires (``cast_changed`` otherwise)."""
    if set(take.cast) != set(scene.cast) or set(take.present) != set(scene.on_stage) or _differs(
            take.players, _table(scene)):
        return False
    return not any(
        (line.actor is not None and line.actor not in scene.npcs)  # a player's character now
        or set(line.audience) != set(scene.players if line.actor is None else _heard_now(scene, line.actor))
        for line in lines)


def _hearing(scene: Scene, rooms: Mapping[InstanceRef, tuple[InstanceRef, ...]]):
    """027's rule for which earlier lines of a take each later call is given, on a stage (``None`` without one:
    everyone hears everything, as before). An NPC is given a line only when it is in that line's speaker's room;
    the director only lines some player character heard as of its turn (the players by control as this take is
    written, which the take records as ``Take.players``), since it narrates to the table and sees only what the
    table saw (across turns too: ``table_lines(as_heard=True)`` judges each kept line by its own take's
    ``players``, never by control now, for its brief and its lore); a narrator line is the table's, never an
    NPC's."""
    if scene.stage is None:
        return None
    players = frozenset(scene.players)

    def hears(listener: InstanceRef | None, speaker: InstanceRef | None) -> bool:
        if speaker is None:
            return listener is None
        audience = rooms.get(speaker, ())
        return bool(players.intersection(audience)) if listener is None else listener in audience
    return hears


def _table(scene: Scene) -> tuple[InstanceRef, ...] | None:
    """A staged take's player characters, by control as it is written (``Take.players``); ``None`` with no stage."""
    return None if scene.stage is None else scene.players


def _here(scene: Scene) -> tuple[InstanceRef, ...] | None:
    """A take's on-stage cast: who the stage has present, or ``None`` with no stage (the whole cast is)."""
    return None if scene.stage is None else scene.on_stage


def _heard_now(scene: Scene, speaker: InstanceRef) -> tuple[InstanceRef, ...]:
    """Who would hear ``speaker`` speak to the room now; nobody when it cannot (off stage, or on a guess)."""
    try:
        return _room(scene, speaker)
    except SceneRefused:
        return ()


class _Held(Exception):
    """016 no longer presents a finished take's lines as it commits (``held_by_boundary``, or a configuration that
    cannot be read now): it lands without them."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _held_back(take: Take, reason: str) -> Take:
    """``take`` without the lines 016 holds: its calls keep their attempts and usage, and each call that gave a line
    is ``rejected`` with why (``held_by_boundary``), so the table sees why and nothing it holds is shown."""
    calls = tuple(replace(call, status="rejected", reason=reason) if call.status == "accepted" else call
                  for call in take.calls)
    return replace(take, lines=(), calls=calls, outcome="halted" if take.state == "proposed" else take.outcome)


def _unlined(take: Take) -> Take:
    """``take`` without the lines its turn file has no room for: its calls keep their attempts and usage, and
    each call that gave a line is ``rejected`` (``turn_too_large``), so the table sees why and generates again."""
    calls = tuple(replace(call, status="rejected", reason="turn_too_large") if call.status == "accepted" else call
                  for call in take.calls)
    return replace(take, lines=(), calls=calls, outcome="halted" if take.state == "proposed" else take.outcome)
