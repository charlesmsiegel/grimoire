"""Synthetic Emberfall cast for the sprint 014 voice proofs. No real library content.

Four NPCs discuss a guild betrayal over three turns. Bellamy learns the secret
on turn 2, Quill on turn 3, Oda and Hask never. Cues are deliberately
speaker-neutral, so any attribution of a rendered line to its speaker comes
from that speaker's fragment, not from the cue.
"""

from __future__ import annotations

import re

from grimoire.core import (
    CampaignActor, CharacterDefinition, ControlAssignment, DefinitionIndex, SourceDescriptor, SourceRef,
)
from grimoire.core.authority import DIRECTOR
from grimoire.evals.format import parse_run, parse_scenario
from grimoire.voice import (
    ASPECTS, EXAMPLES_HEADER, HEADER, SecretFact, VoiceAnchor, VoiceCatalog, VoiceExample, VoiceProfile,
    render_speakers, resolve_voice, speaker_fragment,
)

WORLD, CAMPAIGN, FORMAT = "world:emberfall", "c-ember", "grimoire.story-quality/1"
NPCS = ("npc-oda", "npc-bellamy", "npc-quill", "npc-hask")
PC = "pc-wren"
TURNS = (1, 2, 3)
SECRETS = (SecretFact("duke-betrayal", ("duke sold the guild", "duke's betrayal")),)
KNOWN_FROM = {"npc-bellamy": 2, "npc-quill": 3, PC: 3}

CAST = {
    "npc-oda": ("oda", "Oda", (
        ("a1", "cadence", "Clipped. Three to six words a sentence."),
        ("a2", "diction", "Counting-house words: ledger, tally, coin, counter."),
        ("a3", "avoid", "No pleasantries. No questions."),
    ), (
        ("e1", "Ledger's shut. Tally after dawn.", ()),
        ("e2", "Coin on the counter. Then talk.", ()),
        ("e3", "No credit. Counts first. Always.", ()),
        ("oda-debts", "Some debts get paid in blood.", ("duke-betrayal",)),
    )),
    "npc-bellamy": ("bellamy", "Bellamy", (
        ("a1", "register", "Theatrical, breathless and adoring."),
        ("a2", "diction", "Darling, splendid, marvellous, simply divine, deliciously."),
        ("a3", "habit", "Turns every remark into a lyric about glittering ruin."),
    ), (
        ("e1", "Oh darling, what a splendid, glittering catastrophe this evening promises!", ()),
        ("e2", "Marvellous, simply marvellous, pour the wine and let the gossip flow!", ()),
        ("e3", "Every ballad needs a villain, my sweet, and tonight we are spoiled for choice!", ()),
        ("overheard", "Hush, darling, they whisper the duke sold the guild, how deliciously wicked!",
         ("duke-betrayal",)),
    )),
    "npc-quill": ("quill", "Quill", (
        ("a1", "register", "Precise, hedged and academic."),
        ("a2", "diction", "Strictly speaking, per the archive, cross-referenced, footnote, folio."),
        ("a3", "habit", "Corrects a small error before answering anything."),
    ), (
        ("e1", "Strictly speaking, that entry is misfiled; per the archive the correct folio is seventeen.", ()),
        ("e2", "I must note, for the record, a minor discrepancy in your phrasing.", ()),
        ("e3", "Cross-referencing the registry suggests otherwise, though I concede the footnote is ambiguous.", ()),
    )),
    "npc-hask": ("hask", "Hask", (
        ("a1", "register", "Gruff soldier's barks."),
        ("a2", "diction", "Halt, gate, papers, move along, stranger."),
        ("grudge", "habit", "Grumbles that the duke sold the guild to outsiders."),
    ), (
        ("e1", "Halt. Papers. Now.", ()),
        ("e2", "Gate's barred till the bell. Move along.", ()),
        ("e3", "Keep your hands where I can see them, stranger.", ()),
    )),
}

CHOICES = {
    1: ("ask-ledger", "I ask Oda for the guest ledger.", "guest ledger"),
    2: ("follow-bellamy", "I follow Bellamy out to the balcony.", "balcony"),
    3: ("ask-vault", "I ask whether the vault holds the charter.", "charter"),
}
#: The same plain words for every speaker on a turn; nothing speaker-typical.
NEUTRAL_CUE = {1: "the hall tonight", 2: "the night air", 3: "the old vault"}


def ref(entity: str, revision: str | None = "r1") -> SourceRef:
    return SourceRef(WORLD, "character", entity, revision)


def profile(entity: str, anchors, examples) -> VoiceProfile:
    return VoiceProfile(ref(entity), tuple(VoiceAnchor(*a) for a in anchors),
                        tuple(VoiceExample(*e) for e in examples))


def emberfall():
    """Definitions, catalog, actors, controls and resolved voices for the four NPCs."""
    definitions = DefinitionIndex(
        [SourceDescriptor(WORLD, "synthetic")],
        [CharacterDefinition(ref(entity), name) for entity, name, _, _ in CAST.values()])
    catalog = VoiceCatalog(profile(entity, anchors, examples) for entity, _, anchors, examples in CAST.values())
    actors = {actor_id: CampaignActor(CAMPAIGN, actor_id, ref(entity, None))
              for actor_id, (entity, *_rest) in CAST.items()}
    controls = tuple(ControlAssignment(CAMPAIGN, actor_id, DIRECTOR) for actor_id in NPCS) + (
        ControlAssignment(CAMPAIGN, PC, "human:alice"),)
    voices = {actor_id: resolve_voice(actor, catalog, definitions) for actor_id, actor in actors.items()}
    return definitions, catalog, actors, controls, voices


def known(actor_id: str, turn: int) -> tuple[str, ...]:
    return ("duke-betrayal",) if KNOWN_FROM.get(actor_id, turn + 1) <= turn else ()


def cue(actor_id: str, turn: int) -> str:
    """Speaker-neutral; Oda's carries the turn's choice effect, knowers may name the secret."""
    parts = [NEUTRAL_CUE[turn]]
    if actor_id == "npc-oda":
        parts.append(CHOICES[turn][2])
    if known(actor_id, turn):
        parts.append("duke's betrayal")
    return ", ".join(parts)


_INSTRUCTION_TOKENS = frozenset(
    token.casefold() for token in re.findall(r"\w+", HEADER.format(name="") + " " + EXAMPLES_HEADER)
) | frozenset(ASPECTS)


def alternating_renderer(request) -> str:
    """Deterministic stand-in for a model: every second voice word, then the cue."""
    body = request.fragment_text.split("\n", 1)[1] if "\n" in request.fragment_text else ""
    tokens = [t for t in re.findall(r"\w+", body) if t.casefold() not in _INSTRUCTION_TOKENS]
    return " ".join(tokens[::2] + [request.cue])


def cue_only_renderer(request) -> str:
    return request.cue


def echo_renderer(request) -> str:
    """Copies the first example verbatim: what the gate must reject."""
    return next(line[2:] for line in request.fragment_text.splitlines() if line.startswith("> "))


def fragments_for(turn: int, voices, *, secrets=SECRETS):
    return [speaker_fragment(voices[actor_id], secrets=secrets, known=known(actor_id, turn))
            for actor_id in NPCS]


def render_scene(voices, controls, renderer=alternating_renderer, cue_for=cue):
    """{turn: SpokenLines}, each turn rendered one call per NPC through the contract."""
    return {turn: render_speakers([(fragment, cue_for(fragment.actor.instance_id, turn))
                                   for fragment in fragments_for(turn, voices)], renderer, controls)
            for turn in TURNS}


def scenario_dict(voices) -> dict:
    return {
        "format": FORMAT,
        "scenario_id": "emberfall-betrayal",
        "actors": [{"actor_id": PC, "name": "Wren", "control": "player", "voice_samples": []}] + [
            {"actor_id": actor_id, "name": voices[actor_id].name, "control": "director",
             "voice_samples": [example.text for example in voices[actor_id].examples]}
            for actor_id in NPCS],
        "facts": [{"fact_id": secret.fact_id, "markers": list(secret.markers), "known_from": KNOWN_FROM}
                  for secret in SECRETS],
        "established_facts": [],
        "choices": [{"choice_id": choice_id, "turn": turn, "actor_id": PC, "text": text,
                     "effect_markers": [effect], "negation_markers": [], "window": 0}
                    for turn, (choice_id, text, effect) in CHOICES.items()],
        "script": [{"turn": turn, "speakers": list(NPCS)} for turn in TURNS],
        "pc_authorship_markers": ["wren feels", "wren decides"],
    }


def scenario(voices):
    return parse_scenario(scenario_dict(voices))


def run_dict(lines_by_turn, *, run_id: str, ensemble: bool = False, relabel=None) -> dict:
    """A synthetic run: the player's choice, then each NPC line from its own call.

    ``ensemble`` books every line of a turn to one call. ``relabel`` maps an actor id to the
    speaker id written into the run, to test that the metric depends on the fragments.
    """
    relabel = relabel or {}
    turns, calls = [], []
    for turn, lines in lines_by_turn.items():
        contributions = [{"speaker_id": PC, "author": "player", "text": CHOICES[turn][1], "call_id": None}]
        for line in lines:
            speaker = relabel.get(line.actor.instance_id, line.actor.instance_id)
            call_id = f"t{turn}-ensemble" if ensemble else f"t{turn}-{speaker}"
            contributions.append({"speaker_id": speaker, "author": "model", "text": line.text,
                                  "call_id": call_id})
            if not ensemble or line.call_index == 0:
                calls.append({"call_id": call_id, "turn": turn, "purpose": "npc_generation",
                              "actor_id": speaker, "feature_flag": None, "input_tokens": 300,
                              "cached_input_tokens": 0, "output_tokens": 30, "latency_ms": 100})
        turns.append({"turn": turn, "contributions": contributions})
    return {
        "format": FORMAT, "run_id": run_id, "scenario_id": "emberfall-betrayal",
        "label": "Synthetic fake-renderer run (not a model)", "provenance": "synthetic",
        "conditions": {"model": "fake-alternating", "budget": "none"}, "enabled_flags": [],
        "turns": turns, "calls": calls,
        "choice_outcomes": {choice_id: "honored" for choice_id, _, _ in CHOICES.values()},
        "judge_scores": {},
    }


def run(lines_by_turn, parsed_scenario, **kwargs):
    return parse_run(run_dict(lines_by_turn, **kwargs), parsed_scenario)
