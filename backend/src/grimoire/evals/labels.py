"""Choice outcomes labelled by a reader, in place of phrase markers (068).

A scripted choice is the player's act. Whether the scene *engages* with it is not a matter of
phrasing: an innkeeper who refuses in character ("Ledger's guild business.") has answered the
player as surely as one who hands the book over, and a phrase list cannot tell either from a reply
that happens to repeat the request's words. So when a comparison is given labels, every choice in
every sample is decided by its label, read from the model text that the marker check reads (the
replies after the player's line on the choice's turn, then its window):

* ``honored``: the scene gives the act the effect the player sought;
* ``complicated``: the scene answers the act in character but not as the player hoped, such as a
  refusal, a condition, a stall or a question that is about the act;
* ``ignored``: the act draws no answer, so it has no effect;
* ``negated``: the scene contradicts or undoes the act, as if it had not happened.

``honored`` and ``complicated`` give the choice effect. ``ignored`` does not, and counts against
the strict-majority rule as an unrealized choice does. ``negated`` breaks the player's agency, so
like a negation marker it is a hard finding in any one sample.

Labels are made blind. :func:`label_sheet` writes every play of a comparison in a shuffled order
under neutral ids, with no side, kind, sample or run digest on it, and a separate key that binds each
id to its run by a digest. The reader is given the sheet only. :func:`bind_labels` joins a filled
sheet to its key and refuses it unless every choice's text is still exactly what the current
scenario reads from that run, so a label can neither move to another run nor outlive a change to a
choice's turn, window or act.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .format import BenchmarkFormatError, Run, Scenario

#: The sheet and label file format tag.
FORMAT_LABELS = "grimoire.choice-labels/2"
#: The key format tag: which run each neutral play id on a sheet stands for.
FORMAT_KEY = "grimoire.choice-label-key/1"
#: What a reader may label a choice.
LABELS = ("honored", "complicated", "ignored", "negated")
#: Labels that give a choice effect.
GIVEN = frozenset({"honored", "complicated"})
#: The definitions printed on every sheet, so the reader labels against the documented meaning.
DEFINITIONS = {
    "honored": "the scene gives the player's act the effect the player sought",
    "complicated": "the scene answers the act in character, but not as the player hoped: a "
                   "refusal, a condition, a stall or a question that is about the act",
    "ignored": "nothing in the text answers the act, so it has no effect",
    "negated": "the text contradicts or undoes the act, as if it had not happened",
}


def choice_evidence(scenario: Scenario, run: Run) -> dict[str, list[dict[str, str | None]]]:
    """Per choice, the model contributions the choice check reads, with their speakers.

    The same text :func:`grimoire.evals.checks._evidence_text` reads: what the model wrote after the
    choice's own scripted line on its turn, then every model contribution in its window.
    """
    occurrences: Counter[tuple[int, str, str]] = Counter()
    evidence: dict[str, list[dict[str, str | None]]] = {}
    for choice in scenario.choices:
        key = (choice.turn, choice.actor_id, choice.text)
        occurrence = occurrences[key]
        occurrences[key] += 1
        lines: list[dict[str, str | None]] = []
        for turn in run.turns:
            if turn.turn == choice.turn:
                after, seen = None, 0
                for position, contribution in enumerate(turn.contributions):
                    if (contribution.author == "player" and contribution.speaker_id == choice.actor_id
                            and contribution.text == choice.text):
                        if seen == occurrence:
                            after = position
                            break
                        seen += 1
                if after is not None:
                    lines += [{"turn": str(turn.turn), "speaker": c.speaker_id, "text": c.text}
                              for c in turn.contributions[after + 1:] if c.author == "model"]
            elif choice.turn < turn.turn <= choice.turn + choice.window:
                lines += [{"turn": str(turn.turn), "speaker": c.speaker_id, "text": c.text}
                          for c in turn.contributions if c.author == "model"]
        evidence[choice.choice_id] = lines
    return evidence


@dataclass(frozen=True, slots=True)
class Play:
    """One run to label: its digest and its scoring view."""

    digest: str
    run: Run


def _shown(scenario: Scenario, run: Run) -> dict[str, list[dict[str, str | None]]]:
    """Per choice, the evidence as the sheet shows it, with speakers by name."""
    names = {actor.actor_id: actor.name for actor in scenario.actors}
    return {choice_id: [{**line, "speaker": names.get(line["speaker"] or "", line["speaker"])}
                        for line in lines]
            for choice_id, lines in choice_evidence(scenario, run).items()}


def _acts(scenario: Scenario) -> list[dict[str, str | None]]:
    names = {actor.actor_id: actor.name for actor in scenario.actors}
    return [{"choice_id": choice.choice_id, "player": names.get(choice.actor_id),
             "act": choice.text} for choice in scenario.choices]


def label_sheet(scenario: Scenario, plays: Sequence[Play], *, salt: str) -> tuple[dict, dict]:
    """A blind labelling sheet for ``plays`` and its key.

    The sheet holds the plays in a shuffled order under neutral ids, every outcome left empty, and
    nothing that names a run. The key maps each id to its run's digest and is not given to the
    reader. The order is the plays sorted by a hash of ``salt`` and each run's digest; the caller
    draws the salt at random and does not keep it, so the order says nothing about which run is
    which.
    """
    digests = [play.digest for play in plays]
    if len(set(digests)) != len(digests):
        raise ValueError("two plays to label are the same run")
    if not salt:
        raise ValueError("the sheet needs a random salt to shuffle its plays")
    order = sorted(plays, key=lambda play: hashlib.sha256(
        f"{salt}:{play.digest}".encode()).hexdigest())
    ids = [f"p{index:02d}" for index in range(1, len(order) + 1)]
    sheet = {
        "format": FORMAT_LABELS,
        "scenario_id": scenario.scenario_id,
        "instructions": (
            "Label every choice of every play with one outcome, from the model text shown for it "
            "only. Judge whether the scene engages with the player's act, not whether the player "
            "got what they wanted. Fill in each \"outcome\" and nothing else."),
        "definitions": dict(DEFINITIONS),
        "choices": _acts(scenario),
        "plays": [{
            "play": play_id,
            "choices": {choice_id: {"text": lines, "outcome": None}
                        for choice_id, lines in _shown(scenario, play.run).items()},
        } for play_id, play in zip(ids, order)],
    }
    key = {"format": FORMAT_KEY, "scenario_id": scenario.scenario_id,
           "plays": {play_id: play.digest for play_id, play in zip(ids, order)}}
    return sheet, key


def bind_labels(sheet: object, key: object, scenario: Scenario,
                plays: Sequence[Play]) -> dict[str, dict[str, str]]:
    """A filled-in sheet joined to its key, as run digest -> choice id -> outcome.

    Refused unless the sheet and key are for ``scenario``, the key names exactly ``plays``, every
    choice of every play has one outcome, and the sheet's acts and each choice's text are what the
    current scenario reads from that run.
    """
    def fail(where: str, why: str) -> BenchmarkFormatError:
        return BenchmarkFormatError(f"labels: {where}: {why}")
    if not isinstance(key, Mapping) or key.get("format") != FORMAT_KEY:
        raise fail("key format", f"expected {FORMAT_KEY!r}")
    if key.get("scenario_id") != scenario.scenario_id:
        raise fail("key scenario_id", f"expected {scenario.scenario_id!r}")
    ids = key.get("plays")
    if not isinstance(ids, Mapping) or not all(
            isinstance(digest, str) and len(digest) == 64
            and not set(digest) - set("0123456789abcdef") for digest in ids.values()):
        raise fail("key plays", "expected play ids mapped to sha256 digests")
    by_digest = {play.digest: play for play in plays}
    if len(set(ids.values())) != len(ids) or set(ids.values()) != set(by_digest):
        missing = len(set(by_digest) - set(ids.values()))
        extra = len(set(ids.values()) - set(by_digest))
        raise fail("key plays", f"not this comparison's key ({missing} of its {len(by_digest)} "
                                f"plays missing, {extra} plays not in it)")
    if not isinstance(sheet, Mapping) or sheet.get("format") != FORMAT_LABELS:
        raise fail("format", f"expected {FORMAT_LABELS!r}")
    if sheet.get("scenario_id") != scenario.scenario_id:
        raise fail("scenario_id", f"expected {scenario.scenario_id!r}")
    if sheet.get("choices") != _acts(scenario):
        raise fail("choices", "the acts are not the current scenario's; make a new sheet")
    entries = sheet.get("plays")
    if not isinstance(entries, list) or not entries:
        raise fail("plays", "expected a non-empty list")
    expected = [choice.choice_id for choice in scenario.choices]
    labels: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"plays[{index}]"
        if not isinstance(entry, Mapping):
            raise fail(where, "expected an object")
        play_id = entry.get("play")
        if not isinstance(play_id, str) or play_id not in ids:
            raise fail(f"{where}.play", "not a play id in the key")
        if play_id in seen:
            raise fail(f"{where}.play", "labels the same play twice")
        seen.add(play_id)
        choices = entry.get("choices")
        if not isinstance(choices, Mapping) or sorted(choices) != sorted(expected):
            raise fail(f"{where}.choices", f"expected exactly {', '.join(expected)}")
        shown = _shown(scenario, by_digest[ids[play_id]].run)
        outcomes: dict[str, str] = {}
        for choice_id in expected:
            choice = choices[choice_id]
            if not isinstance(choice, Mapping):
                raise fail(f"{where}.choices.{choice_id}", "expected an object")
            if choice.get("text") != shown[choice_id]:
                raise fail(f"{where}.choices.{choice_id}.text",
                           "not what the current scenario reads from this play; make a new sheet")
            outcome = choice.get("outcome")
            if outcome not in LABELS:
                raise fail(f"{where}.choices.{choice_id}.outcome",
                           f"expected one of {', '.join(LABELS)}")
            outcomes[choice_id] = outcome
        labels[ids[play_id]] = outcomes
    if seen != set(ids):
        raise fail("plays", f"{len(set(ids) - seen)} of the key's {len(ids)} plays are not labelled")
    return labels
