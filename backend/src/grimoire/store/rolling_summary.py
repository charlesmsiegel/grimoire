"""The live per-scene running summary (#85): prompt, parse, and the digest that
tells a fold whether the ground under it moved.

Storage lives in `scenes/` — it is scene frontmatter, like `one_line`/`summary`
— so this module holds no file IO at all. The LLM call lives in the route layer,
the split every LLM-backed store module follows (see taglines.py, dossiers.py,
absorb/prompt.py); the prompt text lives in templates/rolling_summary/.

Unlike absorb, this summary is folded *incrementally*: the prior summary plus
the posts appended since it, so a scene that runs to three hundred posts costs
one short prompt per refresh rather than one growing one. That is only sound
while the prefix the prior summary covers is still the prefix on disk, which is
what `covered_digest` exists to check -- see its docstring.
"""

from __future__ import annotations

import hashlib
import json

from .. import prompts


def covered_digest(messages: list[dict]) -> str:
    """A stable digest of the messages a stored summary covers.

    The transcript is not append-only. A reroll replaces the trailing reply,
    `edit_message` rewrites one in place, and `trim_continuation` /
    `remove_trailing_*` shorten it -- and the first two can leave the message
    COUNT unchanged while changing what the messages say. So "we have folded the
    first 12 posts" is not a fact a count can carry: fold forward on a stale
    count and the summary keeps describing a turn the player deleted, for the
    rest of the scene, with nothing that can notice.

    Comparing this against the current prefix is what notices. A mismatch is not
    an error -- it just means the fold has to start over from the whole
    transcript, which is the correct answer to "the ground moved".

    Role, speaker and content, in order: those are the three fields the
    transcript renders (`snippets/transcript.j2`), so two prefixes with the same
    digest produce the same prompt. Frontmatter and turn boundaries are
    deliberately out -- they do not reach the summary, and folding them in would
    invalidate a perfectly good summary every time a location line was stamped.
    """
    canonical = [[m.get("role", ""), m.get("speaker") or "", m.get("content", "")]
                 for m in messages]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_prompt(prior: str, transcript: str, facts: dict | None = None) -> list[dict]:
    """The refresh call's system/user pair. `prior` is "" on a from-scratch
    fold, in which case `transcript` is the whole scene rather than the tail.

    `facts` is `chronicle.scene_facts()`, and it is not optional in spirit: the
    system prompt asks where the scene is and who is present, and the transcript
    is not guaranteed to say. A scene's FIRST location is set silently and cast
    seated before the first message is seated silently, so on exactly the scenes
    that never move or re-cast -- the ordinary ones -- neither fact appears in
    the text at all. Worse for a fold than for absorb, because a fact missing
    from the first refresh can never be recovered by a later one: the transcript
    it would have come from is behind the fold. Defaulted only so the parameter
    can be omitted where there is genuinely nothing to say.
    """
    return [{"role": "system", "content": prompts.render("rolling_summary/system.j2")},
            {"role": "user", "content": prompts.render(
                "rolling_summary/user.j2", prior=prior, transcript=transcript,
                facts=facts)}]


def parse_output(text: str) -> str:
    """The reply as one line of prose.

    `str.split()` on no argument, so every run of whitespace -- newlines
    included -- collapses to a single space. Two reasons, and the second is the
    load-bearing one: the prompt asks for one paragraph and a model that answers
    in three should still produce something usable, and scene frontmatter is a
    ONE-LINE-PER-KEY format (`store/frontmatter.py`) whose writer quotes without
    escaping newlines, so a multi-line value silently corrupts the scene file.
    `scenes.set_rolling_summary` collapses again for that second reason, since
    the store's own invariant must not depend on which parser fed it.
    """
    return " ".join(text.split())
