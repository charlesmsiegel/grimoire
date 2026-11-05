"""Screen an accepted reply before it becomes a line: silent, copied or leaking text is rejected."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from grimoire.evals.text import has_words
from grimoire.voice import ResolvedVoice, example_copies

from .brief import MAX_LINE_CHARS, DirectorBrief, HeardLine, NpcBrief, _leaks

#: Why a provider-accepted reply is still not a line. The call stays billed either way.
REJECTIONS = ("silent_reply", "too_long", "copied_example", "unlearned_secret")


def screen_reply(brief: NpcBrief | DirectorBrief, text: str, *, spoken: Sequence[HeardLine] = (),
                 voices: Iterable[ResolvedVoice] = ()) -> str | None:
    """The rejection reason for ``text`` from the call ``brief`` describes, or ``None`` to keep it.

    ``silent_reply``: blank or punctuation only. ``too_long``: over ``MAX_LINE_CHARS``. ``copied_example``: it reproduces an authored example
    of any voice in the scene (``voices``). ``unlearned_secret``: it states a marker of a secret the
    speaker has not learned and that nothing it held or heard (its brief, ``spoken`` this turn)
    exposed. A director brief holds every secret as hidden, so only its own inputs expose one.
    """
    if not isinstance(brief, (NpcBrief, DirectorBrief)):
        raise TypeError("screen_reply needs an NpcBrief or a DirectorBrief")
    if not isinstance(text, str) or not has_words(text):
        return "silent_reply"
    if len(text.strip()) > MAX_LINE_CHARS:
        return "too_long"  # later speakers hear every line; one this long would not fit a transcript
    if example_copies(text, voices):
        return "copied_example"
    exposed = brief.exposed + tuple(line.text for line in spoken)
    if _leaks(text, brief.hidden, exposed) is not None:
        return "unlearned_secret"
    return None
