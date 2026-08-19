"""Heuristic scene-break detection (#84): score the signals a scene already
carries, and build the prompt that asks the model to confirm a crossing.

Two halves, in that order, and the order is the whole design. The heuristic is
deterministic and free -- it reads counts a scene already keeps (`location_history`,
`time_history`, the transcript's length) and decides whether anything has
happened that is worth a question. Only a crossing pays for the second half, one
short call that answers the question a count cannot: whether the story actually
reached a resting point, or merely moved rooms mid-argument.

No file IO and no LLM call live here, the split every LLM-backed store module
follows (see rolling_summary.py, taglines.py, absorb/prompt.py): the caller
hands in what it read, the route makes the call, and the prompt text lives in
templates/scene_break/.

Three things this deliberately does NOT do:

- **It never splits a scene.** The answer is "now is a good place to stop", not
  a post index to cut at. Proposing a cut point would need a validated index
  into a transcript that moves under the proposal, and the action a player can
  actually take -- end this scene, start the next -- is about the scene's END.
  Every other continuity feature here proposes and waits (absorb, suggestions);
  this one does too, and the store is only written when the player answers.
- **It never fires on the transition alone.** A location move or a time skip
  costs a point, but the post count since the last question is a hard gate:
  a scene that has produced four posts since the last time it was asked has not
  produced enough story for the answer to have changed, however much furniture
  moved in them.
- **It carries no verdict of its own.** `evaluate` says "worth asking"; the
  model says yes or no. A heuristic that answered by itself would be a scene
  break detector that fires every time a party walks through a door.
"""

from __future__ import annotations

import json

from .. import prompts
from . import calendars

#: The score a scene must reach for a confirmation call to be worth making.
#: Two, so that no single signal fires on its own -- the length gate below is
#: worth 1 at the threshold, and length ALONE only reaches 2 at twice it.
THRESHOLD = 2

#: A time skip at or above this many minutes is a strong break signal rather
#: than an ordinary one: six hours is the smallest gap that cannot be "later
#: that afternoon", and is where a skip starts to read as a cut rather than as
#: pacing. Below it the advance still counts, just for less.
LONG_SKIP_MINUTES = 6 * 60


def moves(history: list[str]) -> int:
    """How many times a scene has actually MOVED, given one of its histories.

    Public because the watermark a DISMISSAL writes (`scenes.dismiss_scene_break`)
    has to agree with the count this scorer compares it against. Two copies of
    "one less than the entries" that drifted apart would leave a dismissed
    suggestion re-earning its location point on the very next evaluation, with
    nothing to say which copy was wrong.

    One less than the number of entries, because the first entry is the scene
    being placed rather than moved: `scenes.moment.set_location` and
    `_apply_datetime` both append their transition line only `if history` --
    the first location and the first date are set silently. Counting entries
    instead would score every scene's opening as a move on its first
    evaluation, which is exactly the moment there is least reason to.
    """
    return max(0, len(history) - 1)


def _gap_minutes(provider, before: str, after: str) -> int | None:
    """Minutes from one scene moment to the next, or None if this calendar
    cannot read them.

    None rather than 0 on failure, so a caller cannot confuse "no time passed"
    with "no answer" -- the first is a real signal about the story, the second
    is a hand-edited `time_history` or a plugin calendar that raised. A moment
    with no time of day counts as midnight, matching `clock._stamp`, so a
    dateless "the 5th" to "the 5th at 21:30" is the 21.5 hours it looks like.
    """
    try:
        days = calendars.fixed_of(provider, after) - calendars.fixed_of(provider, before)
        minutes = (calendars.minutes_of(after) or 0) - (calendars.minutes_of(before) or 0)
    except Exception:  # noqa: BLE001 -- user-authored provider code, hand-edited history
        return None
    return days * 24 * 60 + minutes


def evaluate(messages: list[dict], location_history: list[str],
             time_history: list[str], watermark: dict, every: int,
             provider=None) -> dict:
    """Score the break signals a scene has accumulated since it was last asked.

    `watermark` is `scenes.scene_break_fields`' counts -- the transcript length,
    the moves and the advances that the last question already covered. Every
    "since" below is measured against those and floored at zero, because all
    three can go BACKWARDS: `delete_from` trims the transcript and
    `_rewound_history` trims both histories with it, so a rewound scene must
    read as "nothing new" rather than as a negative count that flips a
    comparison somewhere downstream.

    `every` is `config.scene_break_every()` -- 0 means the feature is off, and
    that is checked here rather than left to each caller, so the GET that only
    reports the score and the POST that would spend a call cannot disagree
    about whether the feature is on.

    `provider` is the campaign's primary calendar, used only to size a time
    skip. Omitted (or unable to read the history) the advance still scores as
    an ordinary one: the fact that time moved is in the history regardless, and
    only the extra point for a LONG skip depends on being able to measure it.
    """
    total = len(messages)
    posts = max(0, total - watermark.get("at", 0))
    locs, times = moves(location_history), moves(time_history)
    moved = max(0, locs - watermark.get("locs", 0))
    advanced = max(0, times - watermark.get("times", 0))

    signals: list[dict] = []
    # Switched off is switched off, all the way down to the signal list: the
    # panel renders what is here, and reporting "the scene has moved twice"
    # under a feature nobody enabled is an observation with no question behind
    # it. The watermark is still reported, so turning the feature ON later
    # starts from the scene as it stands rather than from its first post.
    if every <= 0:
        return {"posts": posts, "signals": [], "score": 0, "due": False,
                "watermark": {"at": total, "locs": locs, "times": times}}
    if posts >= every:
        # Two points at twice the threshold, which is what lets a scene that
        # simply RUNS LONG reach the bar on its own. A scene can be over
        # without anybody moving or the clock jumping -- an argument that has
        # said everything it has to say is exactly that scene -- and a
        # heuristic that only ever fired on transitions would never ask about it.
        long_run = posts >= every * 2
        signals.append({"kind": "length", "weight": 2 if long_run else 1,
                        "detail": f"{posts} posts since this was last considered"})
    if moved:
        signals.append({"kind": "location", "weight": 1,
                        "detail": f"the scene has moved {moved} time{'s' if moved > 1 else ''}"})
    if advanced:
        # Only the advances this question has not already covered: an hour-long
        # skip early in a scene must not keep re-earning its point every time
        # the scene is re-scored. `times` is the index of the LAST entry, so
        # advance i is history[i-1] -> history[i].
        spans = [] if provider is None else [
            _gap_minutes(provider, time_history[i - 1], time_history[i])
            for i in range(watermark.get("times", 0) + 1, times + 1)]
        measured = [s for s in spans if s is not None]
        gap = max(measured) if measured else None
        long_skip = gap is not None and gap >= LONG_SKIP_MINUTES
        signals.append({"kind": "time", "weight": 2 if long_skip else 1,
                        "detail": _time_detail(advanced, gap, long_skip)})

    score = sum(s["weight"] for s in signals)
    return {"posts": posts, "signals": signals, "score": score,
            # The length gate is a precondition, not merely a scoring term.
            # Without it a move plus a skip inside three posts -- a travel beat,
            # which is the middle of a scene rather than its end -- would buy a
            # provider call every time the party crossed a bridge.
            "due": posts >= every and score >= THRESHOLD,
            # What a question asked NOW would cover, for the caller to record
            # alongside whatever the model answers. Read off the same snapshot
            # the score was computed from, so the two cannot describe different
            # transcripts.
            "watermark": {"at": total, "locs": locs, "times": times}}


def _time_detail(advanced: int, gap: int | None, long_skip: bool) -> str:
    """The time signal in words, for a prompt the model reads.

    Hours rather than minutes once there are any, because "the clock moved 930
    minutes" is a number a reader has to convert and "15 hours" is not -- and
    this string's only consumer is a language model being asked a question
    about pacing.
    """
    if gap is None:
        return f"the clock advanced {advanced} time{'s' if advanced > 1 else ''}"
    hours = gap // 60
    span = f"{hours} hours" if hours else f"{gap} minutes"
    return (f"the clock advanced {span}" + (" — a long skip" if long_skip else ""))


def build_prompt(transcript: str, signals: list[dict], facts: dict | None = None,
                 title: str = "") -> list[dict]:
    """The confirmation call's system/user pair.

    `transcript` is the posts since the last question rather than the whole
    scene: the question is whether the story has arrived somewhere since it was
    last asked, and re-sending three hundred posts to ask it would make the
    cheap half of this feature pointless.

    `facts` is `chronicle.scene_facts()`, in for rolling_summary's reason and
    no other: a scene's first location and first date are set silently, so on
    exactly the ordinary scenes -- the ones that never move -- the transcript
    does not say where or when it is. `title` is the scene's own, so a proposed
    NEXT title is not a restatement of the one already on screen.
    """
    return [{"role": "system", "content": prompts.render("scene_break/system.j2")},
            {"role": "user", "content": prompts.render(
                "scene_break/user.j2", transcript=transcript, signals=signals,
                facts=facts, title=title)}]


def _extract_json(text: str):
    """The reply as an object, tolerant of a model that wrapped it in prose.

    Objects only, deliberately narrower than `suggest._extract_json`, which
    also accepts a bare top-level array because the reply it parses is a LIST
    of openings and answering with the array alone is the natural deviation
    there. This reply is a single verdict; a bare array is not a shape it has a
    reading for, so accepting one would only widen what can be misread.
    """
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_output(text: str) -> dict:
    """`{"break": bool, "reason": str, "title": str}` out of the model's reply.

    An unreadable reply is `break: false` with empty prose rather than an
    exception, and that is the safe direction on purpose: this route runs
    automatically off the play loop, and the cost of a missed suggestion is
    that nobody is asked, where the cost of raising is an error banner over a
    scene the player is in the middle of.

    Prose is collapsed to one line for `scenes.set_scene_break`'s reason --
    frontmatter is one line per key and its writer does not escape newlines --
    but the store collapses again anyway, since that invariant must not depend
    on which parser fed it.
    """
    parsed = _extract_json(text) or {}
    return {"break": parsed.get("break") is True,
            "reason": " ".join(str(parsed.get("reason") or "").split()),
            "title": " ".join(str(parsed.get("title") or "").split())}
