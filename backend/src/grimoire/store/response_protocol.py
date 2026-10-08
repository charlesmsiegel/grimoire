"""Hidden response control, independent of provider chunk boundaries -- and
the speaker pick that opens a Directed round, as a decision item."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from .. import decisions, prompts
from . import fence, state_fence
from .scenes import serialize as scenes_serialize

_HANDOFF = re.compile(r"```[ \t]*handoff\b", re.IGNORECASE)
_PREFIX = re.compile(
    r"(`{1,2}|`{3}[ \t]*(?:h(?:a(?:n(?:d(?:o(?:f(?:f)?)?)?)?)?)?)?)$", re.IGNORECASE
)
# Every character `_PREFIX` can match, plus the newline its `$` may match in
# front of: a match lies inside the trailing run of these (`fence.trailing_run`).
_PREFIX_RUN = frozenset("` \thandofHANDOF\n")


#: The two issues a speaker pick or a handoff block can raise before anyone
#: speaks: nothing readable came back, or what came back names nobody the
#: round offers. `routes/character_turns._HANDOFF_ISSUES` ends a Directed
#: chain on either, and `_round_state` stores the string as it is.
INVALID_HANDOFF = "missing or invalid handoff"
INELIGIBLE = "ineligible or repeated speaker"


def validate_handoff(payload, eligible, used):
    """`(next, issue)` for a handoff payload. A ref that is eligible but has
    already spoken is reported as `"repeated speaker"`, apart from an
    ineligible one: with automatic rounds remaining, the caller turns that
    repeat into the next round's lead instead of rejecting it."""
    if not isinstance(payload, dict) or set(payload) != {"next"}:
        return None, INVALID_HANDOFF
    ref = payload["next"]
    if ref is None:
        return None, None
    if not isinstance(ref, str) or ref not in eligible:
        return None, INELIGIBLE
    if ref in used:
        return None, "repeated speaker"
    return ref, None


# --- the speaker pick, asked through decide() (spec 7.4) --------------------
#
# `npc_roster` and `observable_conversation` gather what the pick reads,
# `selector_item` is its request as a decision item, and `selection_of` maps
# its answer back to the `(next, issue)` pair `_select` hands `_round_state`,
# with today's two issue strings. Pure: the caller reads the scene, hands in
# the prompt-phase regex view (`store.regex.view.view(..., phase="prompt")`,
# which `test_regex_prompt_guard.py` holds the route to asking for itself) and
# owns the call. The route and the `decide-speaker` eval case gather through
# the same two helpers.

#: The choice's id: the key today's reply format names the speaker under.
SELECTOR_QUESTION = "next"

#: The option for a contribution that belongs to no listed NPC.
GRIMOIRE_REF = "grimoire"


#: How many posts IN CONTEXT the pick reads, oldest first: a hidden post
#: neither shows nor takes one of them.
SELECTOR_POSTS = 12

#: The prompt-phase view of a window of the transcript: `(posts, offset,
#: total)`, where `offset` is the window's first index in the whole and `total`
#: the whole's length, so a depth-limited rule counts over the whole scene.
View = Callable[[list[dict], int, int], list[dict]]


def npc_roster(cast: list[dict]) -> list[dict]:
    """A scene's present cast (`appearances.scene_cast`) as roster entries,
    `{"ref": "kind:id", "name"}` in cast order, with the player's character
    left out: the shape `group_play` plans over and `selector_item` offers."""
    return [{"ref": f"{a['kind']}:{a['id']}", "name": a["name"]}
            for a in cast if a["role"] != "player"]


def observable_conversation(messages: list[dict], view: View) -> list[dict]:
    """The observable transcript the pick reads, as `{speaker, content}`: the
    last `SELECTOR_POSTS` posts in context, each as `view` shows it to a model.
    `view` is handed the window from the first kept post on, never the raw
    transcript's text. A synthetic line (a roll, a transition, a director
    note) keeps its slot and is not shown; a post with no speaker is the
    player's ("You") or the narrator's ("Grimoire") by its role."""
    kept = [i for i, m in enumerate(messages)
            if not scenes_serialize.is_excluded(m)][-SELECTOR_POSTS:]
    lo = kept[0] if kept else len(messages)
    window = view(messages[lo:], lo, len(messages))
    shown = [window[i - lo] for i in kept]
    return [{"speaker": m.get("speaker") or ("You" if m["role"] == "user" else "Grimoire"),
             "content": m["content"]}
            for m in shown
            if m.get("speaker") not in scenes_serialize.SYNTHETIC_SPEAKERS]


def selector_item(roster: list[dict], conversation: list[dict],
                  note: str = "") -> decisions.Item:
    """The speaker pick as a decision item: the observable transcript (and the
    round's user direction, when there is one) as its context, and one choice
    over the eligible refs -- each described by its name -- and `grimoire`,
    whose instructions are the legacy one-call prompt's criteria
    (`scene/response_selector_question.j2`).

    `allow_none`: null is a real answer, the one that returns control to the
    player, so an explicit null is `abstained` rather than unreadable. The
    roster is not repeated in the context, because the options are the
    roster. `roster` is the round record's `eligible` (dicts with `ref` and
    `name`)."""
    context = prompts.render("scene/response_selector_context.j2",
                             conversation=conversation, note=note)
    options = (*(decisions.Option(entry["ref"], entry["name"]) for entry in roster),
               decisions.Option(GRIMOIRE_REF,
                                prompts.render("scene/response_selector_grimoire.j2")))
    return decisions.Item(context, (decisions.Choice(
        SELECTOR_QUESTION, prompts.render("scene/response_selector_question.j2"),
        options, allow_none=True),))


def selection_of(result: decisions.ItemResult) -> tuple[str | None, str | None]:
    """`(next, issue)` from the item's result, as `validate_handoff` reported
    today's reply:

    - an answer is that speaker, with no issue;
    - an explicit null (`abstained`) hands control back, with no issue;
    - an answer present and naming nobody offered (`unreadable` with
      `detail == "not_an_option"`) is `INELIGIBLE`, as an off-roster ref is;
    - anything else -- no object, no answer, a refusal, an error -- is
      `INVALID_HANDOFF`.

    Either issue ends the chain and returns control to the player: nothing
    unreadable is ever guessed into a speaker."""
    answer = result.answers.get(SELECTOR_QUESTION)
    if answer is not None and isinstance(answer.answer, str):
        return answer.answer, None
    if answer is not None and answer.reason == "abstained":
        return None, None
    if answer is not None and answer.detail == decisions.NOT_AN_OPTION:
        return None, INELIGIBLE
    return None, INVALID_HANDOFF


class _PreparationPrefix:
    """Remove one leading preparation fence before response controls see it.

    Only a leading fence is special; ordinary prose keeps its existing grammar.
    Prefix bytes wait until distinguishable, and an interrupted hidden block
    stays hidden. Completed lines are consumed once, avoiding rescans of the
    preparation body. Future leading preparation fields can share this parser.
    """

    def __init__(self, enabled):
        self.pending = ""
        self.mode = "prefix" if enabled else "prose"
        self.body = ""

    @staticmethod
    def _possible(text):
        head = text.lstrip(" \t\r\n")
        head = re.sub(r"^```[ \t]*", "```", head).rstrip(" \t\r").lower()
        return "```perception".startswith(head)

    def feed(self, text):
        if self.mode == "prose":
            return text
        self.pending += text
        if self.mode == "prefix":
            candidate = self.pending.lstrip(" \t\r\n")
            head, sep, rest = candidate.partition("\n")
            if not sep and self._possible(candidate):
                return ""
            if sep and re.fullmatch(r"```[ \t]*perception[ \t\r]*", head, re.IGNORECASE):
                self.mode = "hidden"
                self.pending = rest
            else:
                self.mode = "prose"
                out, self.pending = self.pending, ""
                return out
        while "\n" in self.pending:
            line, _, self.pending = self.pending.partition("\n")
            if line.strip(" \t\r") == "```":
                self.mode = "prose"
                out, self.pending = self.pending, ""
                return out
            self.body += line + "\n"
        return ""

    def finish(self):
        if self.mode == "hidden":
            if self.pending.strip(" \t\r") != "```":
                self.body += self.pending
            self.pending = ""
            return ""
        if self.mode == "prefix" and self._possible(self.pending) and self.pending.lstrip().startswith("```"):
            self.pending = ""
            return ""
        out, self.pending = self.pending, ""
        return out


def preparation_note(body: str) -> str:
    """The reasoning note a perception body is kept as -- `""` for none."""
    return "\n\n[Perception preparation]\n" + body if body else ""


def strip_preparation(text: str) -> tuple[str, str]:
    """`(prose, note)`: `text` without one leading perception fence, as a
    watcher with perception on would have streamed it, and the fence's body as
    the `preparation_note` that watcher would have kept; `(text, "")` for text
    that has none.

    For a reply whose watcher ran with perception OFF but whose model wrote a
    fence anyway -- a "Keep writing" prefill that failed over to an instruction
    route. The prose keeps its leading whitespace, which is what says how a
    continuation joins the reply before it."""
    parser = _PreparationPrefix(True)
    prose = parser.feed(text) + parser.finish()
    return prose, preparation_note(parser.body)


class ResponseWatcher:
    """Rolls interrupt first; state precedes the final handoff and stays hidden."""

    def __init__(self, *, perception=False):
        self.preparation = _PreparationPrefix(perception)
        self.roll = fence.FenceWatcher()
        self.redactor = state_fence.StreamRedactor()
        self.reasoning = ""
        self.raw = ""
        self.visible = 0
        self.handoff = None
        self.issue = None
        self._narration = ""

    def _emit(self, text):
        # Everything before `visible` is decided, so the searches start there
        # -- `fence.FenceWatcher.feed` makes the same argument, and for the
        # same reason: rescanning `raw` from 0 on every feed made a reply
        # O(length²) on the event loop. `visible` only ever stops at the start
        # of a handoff match or of a `_PREFIX` match, every prefix of a handoff
        # opener is itself a `_PREFIX` match, and a search from there finds the
        # same leftmost match a search from 0 would.
        start = self.visible
        self.raw += text
        match = _HANDOFF.search(self.raw, start)
        stop = match.start() if match else len(self.raw)
        if match is None:
            prefix = _PREFIX.search(self.raw, fence.trailing_run(self.raw, _PREFIX_RUN, start))
            if prefix:
                stop = prefix.start()
        delta = self.raw[self.visible : stop]
        self.visible = stop
        return self.redactor.feed(delta)

    def feed(self, text):
        # A no-op for "" in every state (the roll watcher, the searches and the
        # redactor all reach the answer they already had), and the adapters send
        # one per SSE line -- so it is answered before any of them runs.
        if not text:
            return ""
        return self._emit(self.roll.feed(self.preparation.feed(text)))

    def finish(self):
        out = self._emit(self.roll.feed(self.preparation.finish()))
        out += self._emit(self.roll.finish())
        match = _HANDOFF.search(self.raw)
        self._narration = self.raw[: match.start()] if match else self.raw
        if match and not (self.roll.complete or self.roll.truncated):
            tail = self.raw[match.end() :]
            closed = re.fullmatch(r"[ \t\r]*\n(.*?)\n```[ \t\r\n]*", tail, re.DOTALL)
            try:
                self.handoff = json.loads(closed.group(1)) if closed else None
            except (ValueError, TypeError):
                self.handoff = None
        self.issue = None if self.handoff is not None else INVALID_HANDOFF
        if match is None and _PREFIX.search(self.raw) is None:
            out += self.redactor.feed(self.raw[self.visible :])
        out += self.redactor.finish()
        return out

    @property
    def preparation_note(self):
        # The existing reasoning artifact keeps this inspectable without adding
        # private preparation to the post or conversation history.
        return preparation_note(self.preparation.body)

    @property
    def narration(self):
        match = _HANDOFF.search(self.raw)
        partial = _PREFIX.search(self.raw) if match is None else None
        return (
            self.raw[: match.start()]
            if match
            else self.raw[: partial.start()]
            if partial
            else self.raw
        )
