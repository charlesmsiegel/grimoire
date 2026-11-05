"""Sealed per-call briefs: one NPC's own perspective, or the scene's authored material.

An ``NpcBrief`` is built only from one actor's ``ActorView`` (its own ``perspective`` projection,
its composed card) and its resolved voice. It never takes director knowledge, another actor's
records or a world truth, so the prompt it renders cannot hold them. A ``DirectorBrief`` takes
authored scenario text and table-visible lines only: there is no knowledge parameter at all.
Pure and deterministic; the runtime turns a brief into provider messages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import InitVar, dataclass

from grimoire.core.authority import DIRECTOR
from grimoire.core.identity import InstanceRef
from grimoire.core.model import ActorView, ControlAssignment
from grimoire.core.perspective import ActorKnowledge
from grimoire.evals.text import contains_marker, has_words
from grimoire.voice import DEFAULT_MAX_CHARS, ResolvedVoice, SecretFact, SpeakerFragment, speaker_fragment

from .errors import GenerationError
from .plan import checked_controls, controller
from .voice import VOICE_SECTION

#: Budgets, in characters, for the parts of a brief that grow with the campaign.
KNOWLEDGE_CHARS = 2400
CONTEXT_CHARS = 1600
SCENARIO_CHARS = 4000
MAX_HEARD = 40
MAX_LINE_CHARS = 2000
MAX_NAME_CHARS = 200

_SPACE = re.compile(r"\s+")
_SEAL = object()
_STANCE = {"believed": "You believe", "suspected": "You suspect", "rejected": "You doubt"}


def _one_line(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class HeardLine:
    """One line spoken aloud at the table, as every present actor heard it."""

    speaker: str
    text: str

    def __post_init__(self) -> None:
        for value, label in ((self.speaker, "speaker"), (self.text, "text")):
            if not isinstance(value, str) or not has_words(value):
                raise GenerationError("bad_heard", f"a heard line needs a nonblank {label}")
        if len(self.text) > MAX_LINE_CHARS or len(self.speaker) > MAX_LINE_CHARS:
            raise GenerationError("bad_heard", f"a heard line is longer than {MAX_LINE_CHARS} characters")


def heard_lines(heard: object) -> tuple[HeardLine, ...]:
    if isinstance(heard, (str, bytes, Mapping)):
        raise GenerationError("bad_heard", "heard must be a sequence of HeardLine values")
    try:
        heard = tuple(heard)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_heard", "heard must be a sequence of HeardLine values") from None
    if not all(isinstance(line, HeardLine) for line in heard):
        raise GenerationError("bad_heard", "heard must be HeardLine values")
    if len(heard) > MAX_HEARD:
        raise GenerationError("bad_heard", f"more than {MAX_HEARD} heard lines")
    return heard


def _spoken(spoken: object) -> tuple[HeardLine, ...]:
    """This turn's lines: typed, but not capped (the plan bounds them)."""
    if isinstance(spoken, (str, bytes, Mapping)):
        raise GenerationError("bad_heard", "spoken must be a sequence of HeardLine values")
    spoken = tuple(spoken)  # type: ignore[arg-type]
    if not all(isinstance(line, HeardLine) for line in spoken):
        raise GenerationError("bad_heard", "spoken must be HeardLine values")
    return spoken


def _secrets(secrets: object) -> tuple[SecretFact, ...]:
    if isinstance(secrets, (str, bytes, Mapping)):
        raise GenerationError("bad_secrets", "secrets must be an ordered sequence of SecretFact values")
    try:
        secrets = tuple(secrets)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_secrets", "secrets must be an ordered sequence of SecretFact values") from None
    if not all(isinstance(secret, SecretFact) for secret in secrets):
        raise GenerationError("bad_secrets", "secrets must be SecretFact values")
    return secrets


def _budget(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GenerationError("bad_budget", f"{label} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class NpcBrief:
    """Everything one NPC's call may see. Only ``npc_brief`` builds one."""

    actor: InstanceRef
    name: str
    voice: ResolvedVoice
    fragment: SpeakerFragment
    system: str
    heard: tuple[HeardLine, ...]
    hidden: tuple[SecretFact, ...]  # the secrets this actor has not learned, by id
    exposed: tuple[str, ...]  # what the actor holds or heard: a marker in here is not a leak
    _seal: InitVar[object] = None

    __hash__ = None  # type: ignore[assignment]  # holds a ResolvedVoice

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _SEAL:
            raise GenerationError("unsealed_brief", "build NPC briefs with npc_brief()")


@dataclass(frozen=True, slots=True)
class DirectorBrief:
    """Everything the scene-level reply may see. Only ``director_brief`` builds one."""

    campaign_id: str
    players: frozenset[str]  # the present player characters' actor ids, derived from control
    system: str
    heard: tuple[HeardLine, ...]
    hidden: tuple[SecretFact, ...]  # every secret: the director brief has no knowledge to learn one from
    exposed: tuple[str, ...]  # authored material and table-visible lines
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _SEAL:
            raise GenerationError("unsealed_brief", "build director briefs with director_brief()")


def _check_view(view: object) -> tuple[InstanceRef, ActorKnowledge]:
    if not isinstance(view, ActorView):
        raise GenerationError("bad_view", "npc_brief needs an ActorView")
    actor = InstanceRef(view.campaign_id, "character", view.actor_id)
    knowledge = view.knowledge
    if knowledge is None:
        # None means no projection was supplied, never "knows everything".
        raise GenerationError("no_knowledge", f"{actor} has no perspective projection")
    if type(knowledge) is not ActorKnowledge:
        raise GenerationError("foreign_knowledge", f"{actor} knowledge must be its own ActorKnowledge")
    if knowledge.actor != actor:
        raise GenerationError("foreign_knowledge", f"{actor} was given another actor's knowledge")
    return actor, knowledge


def known_ids(knowledge: ActorKnowledge) -> frozenset[str]:
    """Proposition ids the actor holds: reports it received or sent, and its own beliefs."""
    beliefs = (*knowledge.current, *knowledge.history, *(knowledge.inherited or ()),
               *(knowledge.inherited_history or ()))
    return frozenset({item.proposition_id for item in knowledge.evidence if item.proposition_id is not None}
                     | {report.proposition_id for report in knowledge.sent}
                     | {belief.proposition_id for belief in beliefs})


def _who(ref: InstanceRef | None, names: Mapping[InstanceRef, str]) -> str:
    name = names.get(ref) if ref is not None else None
    return _one_line(name) if isinstance(name, str) and has_words(name) else "Someone"


def _knowledge_items(knowledge: ActorKnowledge, names: Mapping[InstanceRef, str]) -> list[tuple[tuple, str]]:
    """Each item the actor knows, rendered without ids, with a stable campaign-order key."""
    items: list[tuple[tuple, str]] = []
    for item in knowledge.evidence:
        if item.kind == "report":
            text = f"{_who(item.attributed_to, names)} told you: {_one_line(item.content)}"
        else:
            text = f"You noticed ({_one_line(item.channel)}): {_one_line(item.content)}"
        items.append(((item.moment.sequence, 0, item.evidence_id), text))
    for report in knowledge.sent:
        items.append(((report.sent_at.sequence, 1, report.evidence_id),
                      f"You told {_who(report.recipient, names)}: {_one_line(report.text)}"))
    for belief in knowledge.current:
        items.append(((belief.moment.sequence, 2, belief.belief_id),
                      f"{_STANCE.get(belief.stance, 'You believe')}: {_one_line(belief.text)}"))
    for belief in knowledge.history:
        items.append(((belief.moment.sequence, 2, belief.belief_id),
                      f"Earlier, you thought: {_one_line(belief.text)}"))
    return sorted(items)


def _newest_within(texts: list[str], budget: int) -> list[str]:
    """The newest whole items that fit ``budget``, shown oldest first."""
    kept: list[str] = []
    used = 0
    for text in reversed(texts):
        cost = len(text) + 3  # "- " and a newline
        if used + cost > budget:
            break
        kept.append(text)
        used += cost
    return kept[::-1]


def _context(description: str, sections: tuple[tuple[str, str], ...], budget: int) -> list[str]:
    parts = []
    used = 0
    for text in ([_one_line(description)] if has_words(description) else []) + [
            f"{name}: {_one_line(text)}" for name, text in sections]:
        if used + len(text) + 1 > budget:
            continue  # whole parts only; a later, shorter section may still fit
        parts.append(text)
        used += len(text) + 1
    return parts


def _sections(sections: object) -> tuple[tuple[str, str], ...]:
    if isinstance(sections, (str, bytes, Mapping)):
        raise GenerationError("bad_sections", "sections must be (name, text) pairs")
    try:
        sections = tuple(sections)  # type: ignore[arg-type]
    except TypeError:
        raise GenerationError("bad_sections", "sections must be (name, text) pairs") from None
    for entry in sections:
        if not (isinstance(entry, tuple) and len(entry) == 2 and all(isinstance(v, str) for v in entry)):
            raise GenerationError("bad_sections", "sections must be (name, text) pairs")
    return sections


def _leaks(text: str, hidden: tuple[SecretFact, ...], exposed: Sequence[str]) -> str | None:
    """The id of a hidden secret whose marker ``text`` states and nothing in ``exposed`` does."""
    for secret in hidden:
        for marker in secret.markers:
            if contains_marker(text, marker) and not any(contains_marker(seen, marker) for seen in exposed):
                return secret.fact_id
    return None


def npc_brief(view: ActorView, voice: ResolvedVoice, *, heard: Iterable[HeardLine] = (),
              secrets: Iterable[SecretFact], sections: Iterable[tuple[str, str]] = (),
              names: Mapping[InstanceRef, str] | None = None, knowledge_chars: int = KNOWLEDGE_CHARS,
              context_chars: int = CONTEXT_CHARS, voice_chars: int = DEFAULT_MAX_CHARS) -> NpcBrief:
    """Build one NPC's sealed brief from its own view; ``secrets`` has no default on purpose.

    ``view`` must carry the actor's own ``ActorKnowledge`` (``no_knowledge``/``foreign_knowledge``
    otherwise). ``sections`` is the actor's composed card (``snapshot.effective(...).sections``), for
    context. ``heard`` is what the actor heard before this turn's calls; a line stating a secret the
    actor has not learned (not held by id, and not in its own projection) is ``leaky_cue``: the
    caller screened the cue too little, and the call is refused rather than sent.
    """
    actor, knowledge = _check_view(view)
    # The voice reaches the prompt only through the screened fragment: the card's raw voice-evidence
    # text would bypass suppression and secret screening, so it is neither shown nor counted as held.
    if not isinstance(voice, ResolvedVoice):
        raise GenerationError("bad_voice", "npc_brief needs a ResolvedVoice")
    if voice.actor != actor:
        raise GenerationError("voice_mismatch", f"the voice belongs to {voice.actor}, not {actor}")
    secrets = _secrets(secrets)
    heard = heard_lines(heard)
    sections = tuple(entry for entry in _sections(sections) if entry[0] != VOICE_SECTION)
    names = dict(names or {})
    if not all(isinstance(key, InstanceRef) and isinstance(value, str) for key, value in names.items()):
        raise GenerationError("bad_names", "names must map InstanceRef values to display names")
    knowledge_chars = _budget(knowledge_chars, "knowledge_chars")
    context_chars = _budget(context_chars, "context_chars")

    known = known_ids(knowledge)
    hidden = tuple(secret for secret in secrets if secret.fact_id not in known)
    rendered = [text for _key, text in _knowledge_items(knowledge, names)]
    context = _context(view.description, sections, context_chars)
    # What the actor holds, in full: the projection and its own card, whatever the budget showed.
    own = tuple(rendered) + ((view.description,) if has_words(view.description) else ()) + tuple(
        text for _name, text in sections)
    for line in heard:
        leaked = _leaks(line.text, hidden, own)
        if leaked is not None:
            raise GenerationError("leaky_cue", f"a heard line states {leaked}, which {actor} has not learned")

    try:
        fragment = speaker_fragment(voice, secrets=secrets, known=known, max_chars=voice_chars)
    except ValueError as error:
        raise GenerationError(getattr(error, "reason", "bad_voice"), f"{actor} voice: {error}") from None
    name = _one_line(voice.name)
    if len(name) > MAX_NAME_CHARS:
        raise GenerationError("bad_voice", f"{actor} has a name longer than {MAX_NAME_CHARS} characters")
    shown = _newest_within(rendered, knowledge_chars)
    parts = [
        f"You are {name}, a character in a shared story. Write only {name}'s next contribution: what {name} "
        f"says and does, in {name}'s own voice. Never write for any other character, and never describe what "
        "anyone else thinks, feels or decides.",
        fragment.text,
    ]
    if context:
        parts.append("Who you are:\n" + "\n".join(context))
    parts.append("What you know, oldest first:\n" + "\n".join(f"- {text}" for text in shown) if shown
                 else "You know nothing yet beyond what you hear now.")
    return NpcBrief(actor, name, voice, fragment, "\n\n".join(parts), heard, hidden,
                    own + tuple(line.text for line in heard), _SEAL)


def director_brief(scenario: str, *, campaign_id: str, present: Iterable[str],
                   controls: Iterable[ControlAssignment], names: Mapping[str, str], heard: Iterable[HeardLine] = (),
                   secrets: Iterable[SecretFact], scenario_chars: int = SCENARIO_CHARS) -> DirectorBrief:
    """The scene-level reply's sealed brief: authored scenario text and table-visible lines only.

    The player characters are derived here from ``controls``, never passed in: every ``present``
    actor whose controller is a human. ``names`` must name each of them (``bad_players``), and the
    brief forbids writing their words, thoughts, feelings or decisions. ``run_turn`` checks the
    same set against the plan and controls before any call. The reply is an unreviewed proposal:
    035's ``review_narration`` reviews it before it is kept.
    """
    if not isinstance(scenario, str) or not has_words(scenario):
        raise GenerationError("bad_scenario", "a director reply needs nonblank authored scenario text")
    scenario_chars = _budget(scenario_chars, "scenario_chars")
    if len(scenario) > scenario_chars:
        raise GenerationError("scenario_too_long", f"scenario text is longer than {scenario_chars} characters")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise GenerationError("bad_input", "campaign_id must be nonblank text")
    if isinstance(present, (str, bytes)) or not isinstance(names, Mapping):
        raise GenerationError("bad_players", "present must be a collection of ids and names a mapping")
    present = tuple(present)
    controls = checked_controls(controls)
    players = sorted(actor_id for actor_id in present if isinstance(actor_id, str)
                     and controller(campaign_id, actor_id, controls) not in (None, DIRECTOR))
    labels = [names.get(actor_id) for actor_id in players]
    if not all(isinstance(name, str) and has_words(name) and len(name) <= MAX_NAME_CHARS for name in labels):
        raise GenerationError("bad_players", "every present player character needs a nonblank display name")
    secrets = _secrets(secrets)
    heard = heard_lines(heard)
    parts = ["You are the narrator of a shared story. Describe the scene and what happens next, and voice only "
             "minor unnamed background figures. Write one short narration."]
    if players:
        listed = ", ".join(_one_line(name) for name in labels)  # type: ignore[arg-type]
        parts.append(f"The player characters are: {listed}. Never write their words, thoughts, feelings or "
                     "decisions; their players do that.")
    parts.append("Scenario:\n" + scenario.strip())
    return DirectorBrief(campaign_id, frozenset(players), "\n\n".join(parts), heard, secrets,
                         (scenario,) + tuple(line.text for line in heard), _SEAL)


def _transcript(lines: Sequence[HeardLine]) -> str:
    return "\n".join(f"{_one_line(line.speaker)}: {_one_line(line.text)}" for line in lines)


def npc_messages(brief: NpcBrief, spoken: Sequence[HeardLine] = ()) -> tuple[tuple[str, str], ...]:
    """The (role, content) messages of one NPC call: its brief, then everything it heard so far."""
    if not isinstance(brief, NpcBrief):
        raise GenerationError("bad_brief", "npc_messages needs an NpcBrief")
    lines = brief.heard + _spoken(spoken)
    said = f"What was just said:\n{_transcript(lines)}\n\n" if lines else "Nothing has been said yet.\n\n"
    return (("system", brief.system), ("user", f"{said}Give {brief.name}'s reply only."))


def director_messages(brief: DirectorBrief, spoken: Sequence[HeardLine] = ()) -> tuple[tuple[str, str], ...]:
    """The (role, content) messages of the scene-level call."""
    if not isinstance(brief, DirectorBrief):
        raise GenerationError("bad_brief", "director_messages needs a DirectorBrief")
    lines = brief.heard + _spoken(spoken)
    said = f"What was just said:\n{_transcript(lines)}\n\n" if lines else ""
    return (("system", brief.system), ("user", f"{said}Narrate what happens next."))
