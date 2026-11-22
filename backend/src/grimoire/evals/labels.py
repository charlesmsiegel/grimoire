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
under neutral ids, with no side, kind or sample on it, and binds each id to its run by a digest, so
labels can neither be swapped between runs nor read as one side's.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .format import BenchmarkFormatError, Run, Scenario

#: The sheet and label file format tag.
FORMAT_LABELS = "grimoire.choice-labels/1"
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


def label_sheet(scenario: Scenario, plays: Sequence[Play], *, salt: str) -> dict:
    """A blind labelling sheet for ``plays``: shuffled, neutral ids, every outcome left empty.

    The order is the plays sorted by a hash of ``salt`` and each run's digest. The caller draws the
    salt at random and does not keep it, so the order says nothing about which run is which.
    """
    digests = [play.digest for play in plays]
    if len(set(digests)) != len(digests):
        raise ValueError("two plays to label are the same run")
    if not salt:
        raise ValueError("the sheet needs a random salt to shuffle its plays")
    order = sorted(plays, key=lambda play: hashlib.sha256(
        f"{salt}:{play.digest}".encode("utf-8")).hexdigest())
    names = {actor.actor_id: actor.name for actor in scenario.actors}
    return {
        "format": FORMAT_LABELS,
        "scenario_id": scenario.scenario_id,
        "instructions": (
            "Label every choice of every play with one outcome, from the model text shown for it "
            "only. Judge whether the scene engages with the player's act, not whether the player "
            "got what they wanted. Fill in each \"outcome\" and nothing else."),
        "definitions": dict(DEFINITIONS),
        "choices": [{"choice_id": choice.choice_id, "player": names.get(choice.actor_id),
                     "act": choice.text} for choice in scenario.choices],
        "plays": [{
            "play": f"p{index:02d}",
            "run_sha256": play.digest,
            "choices": {choice_id: {
                "text": [{**line, "speaker": names.get(line["speaker"] or "", line["speaker"])}
                         for line in lines],
                "outcome": None,
            } for choice_id, lines in choice_evidence(scenario, play.run).items()},
        } for index, play in enumerate(order, start=1)],
    }


def parse_labels(data: object, scenario: Scenario) -> dict[str, dict[str, str]]:
    """A filled-in sheet as run digest -> choice id -> outcome; every choice must be labelled."""
    def fail(where: str, why: str) -> BenchmarkFormatError:
        return BenchmarkFormatError(f"labels: {where}: {why}")
    if not isinstance(data, Mapping) or data.get("format") != FORMAT_LABELS:
        raise fail("format", f"expected {FORMAT_LABELS!r}")
    if data.get("scenario_id") != scenario.scenario_id:
        raise fail("scenario_id", f"expected {scenario.scenario_id!r}")
    plays = data.get("plays")
    if not isinstance(plays, list) or not plays:
        raise fail("plays", "expected a non-empty list")
    expected = [choice.choice_id for choice in scenario.choices]
    labels: dict[str, dict[str, str]] = {}
    for index, play in enumerate(plays):
        where = f"plays[{index}]"
        if not isinstance(play, Mapping):
            raise fail(where, "expected an object")
        digest = play.get("run_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
            raise fail(f"{where}.run_sha256", "expected a sha256")
        if digest in labels:
            raise fail(f"{where}.run_sha256", "labels the same run twice")
        choices = play.get("choices")
        if not isinstance(choices, Mapping) or sorted(choices) != sorted(expected):
            raise fail(f"{where}.choices", f"expected exactly {', '.join(expected)}")
        outcomes: dict[str, str] = {}
        for choice_id in expected:
            entry = choices[choice_id]
            outcome = entry.get("outcome") if isinstance(entry, Mapping) else None
            if outcome not in LABELS:
                raise fail(f"{where}.choices.{choice_id}.outcome",
                           f"expected one of {', '.join(LABELS)}")
            outcomes[choice_id] = outcome
        labels[digest] = outcomes
    return labels
