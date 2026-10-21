"""A tool run's history: its prompt, every message it appended, and their ids."""

from __future__ import annotations

from dataclasses import replace

from .. import model_guidance, tool_calls
from ._tool_state import _Turn


class _Transcript:
    """The prompt (`base`) and every message the run appended (`appended`,
    `LoopResult.messages`): how much of it the last send carried, and the
    call ids the loop minted (spec 3.11 rule 2)."""

    def __init__(self, messages: list[dict], run_id: str):
        self.base = messages
        self.run_id = run_id
        self.appended: list[dict] = []
        #: How many appended messages the latest send carried.
        self.sent_len = 0
        self.minted = 0
        self._hist: list[dict] | None = None
        self._hist_len = 0

    def outgoing(self, extra: dict | None) -> list[dict]:
        history = self._history()
        if extra is None:
            return history
        if isinstance(history, model_guidance.PreparedMessages):
            return history.with_appended_keeping(extra)
        return [*history, extra]

    def _history(self) -> list[dict]:
        """The prompt and every turn the loop appended, as one prompt: a
        `PreparedMessages` extended message by message so every variant gets
        the same tail (`with_appended_keeping`), cached between turns."""
        if self._hist is None:
            self._hist, self._hist_len = self.base, 0
        for message in self.appended[self._hist_len:]:
            self._hist = (self._hist.with_appended_keeping(message)
                          if isinstance(self._hist, model_guidance.PreparedMessages)
                          else [*self._hist, message])
        self._hist_len = len(self.appended)
        return self._hist

    def unsent(self, extra: dict | None) -> list[dict]:
        """What the next send carries that the last did not: the messages
        appended since, and `extra`."""
        return [*self.appended[self.sent_len:], *([extra] if extra is not None else [])]

    def sent(self, extra: dict | None) -> None:
        """A turn went out with `extra` (a finalize note) after the history."""
        if extra is not None:
            self.appended.append(extra)
        self.sent_len = len(self.appended)

    def extend(self, messages: list[dict]) -> None:
        self.appended.extend(messages)

    def append(self, turn: _Turn, results: list[dict]) -> None:
        """A tool turn, its calls and their results, as the history holds them."""
        said: dict = {"role": "assistant", "content": turn.text,
                      "tool_calls": [{"id": c.id, "name": c.name,
                                      "arguments": c.arguments or {}} for c in turn.calls]}
        if turn.opaque is not None:
            said["_opaque"] = turn.opaque
        self.extend([said, *results])

    def named(self, call: tool_calls.ToolCall) -> tool_calls.ToolCall:
        """A call with an id every provider takes: the provider's own while it
        is one (spec 3.11 rule 2), else the loop's."""
        if tool_calls.SAFE_ID.match(call.id):
            return call
        return replace(call, id=self._mint())

    def _mint(self) -> str:
        self.minted += 1
        return tool_calls.loop_id(self.run_id, self.minted)

    def rename_ids(self) -> None:
        """Every id in the history the loop's own (spec 3.11 rule 2), each
        occurrence renamed apart, and the cached prompt rebuilt."""
        self.appended = tool_calls.rewrite_ids(self.appended, self._mint)
        self._hist = None
