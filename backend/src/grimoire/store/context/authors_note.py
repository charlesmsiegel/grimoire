"""Author's notes in the composed history (play controls V): which notes apply
to this turn, where each one goes, and the history projected around them.

The notes themselves are stored by `store.authors_notes`; this module only
places them, and is pure apart from the template render.

**Which.** The campaign note, this scene's note, and -- only in an actor-scoped
call -- the character note of the actor being asked to speak. The narrator's
call and every non-actor composition get no character note, so a character's
steer never reaches another character's call.

**When.** `note_turn` counts player posts and director-note lines over the
WHOLE scene transcript -- excluded posts included, so hiding a post does not
shift the cadence, and before `actor.observed_history` narrows it, so every
actor's call agrees on the turn. A note applies when `turn % every == 0`; turn
0 (a scene with no post yet, and every opener) applies only `every == 1` notes
(`authors_notes.applies`). An empty send stores no line, so repeated empty sends
("next NPC round") share one turn number and one cadence decision. A roll
resume recomposes the turn, so it sees the notes as they stand at the resume,
not as they stood when the roll was proposed. A reroll does not recompose: it
replays the frozen prompt, notes included.

**Where.** `depth` counts in-context posts from the end: the note goes before
the `depth`-th most recent one, clamped to the start, and `0` means after the
last post. The point is then snapped back to the start of a player post (the
start of a run of them), or to the very start when there is none, so a note
always sits on a projected-message boundary and never splits a merged run.
Notes at one point go campaign, scene, character.

**How it reaches the model.** Each note renders through
`templates/scene/authors_note.j2` into its own `{"role": "system"}` message --
never merged with a neighbour, never given a speaker label. Nothing extra rides
on the wire message: `inject` returns the notes' positions alongside, which is
how the packer keeps them off its floor and the inspector moves them into a row
of their own.

Two provider caveats, stated rather than solved. Strict OpenAI-compatible
endpoints turn a system message that precedes an assistant message into a
standalone user turn, so a mid-history note can reach the model as if the
player had said it; the Claude agent path lifts every system message into its
system prompt, so the note arrives but its depth does not. And a note sits at a
fixed distance from the END, so it moves one post deeper every turn -- which
breaks a provider's cached prompt prefix from the note onwards, every turn.
"""

from __future__ import annotations

from collections.abc import Callable

from ... import prompts
from .. import authors_notes
from ..scenes import serialize as scenes_serialize
from . import story


def note_turn(messages: list[dict]) -> int:
    """The cadence's turn number: player posts plus director-note lines in the
    full transcript, hidden ones included (see the module docstring)."""
    return sum(1 for m in messages
               if m.get("role") == "user" or scenes_serialize.is_director_note(m))


def applicable(notes: dict, identity: str | None, actor_ref: str | None, actor_name: str,
               turn: int) -> tuple[list[dict], list[dict]]:
    """`(applied, skipped)` for this turn, each in the order campaign, scene,
    character. Each entry is `{"level", "name", "text", "depth", "every"}`;
    `name` is `actor_name` for a character note and "" otherwise. The scene
    note is considered only with an `identity`, the character note only for an
    actor-scoped call (`actor_ref` neither None nor "grimoire")."""
    candidates: list[tuple[str, str, dict | None]] = [("campaign", "", notes.get("campaign"))]
    if identity:
        candidates.append(("scene", "", (notes.get("scenes") or {}).get(identity)))
    if actor_ref not in (None, "grimoire"):
        candidates.append(("character", actor_name,
                           (notes.get("characters") or {}).get(actor_ref)))
    applied: list[dict] = []
    skipped: list[dict] = []
    for level, name, note in candidates:
        if not note:
            continue
        entry = {"level": level, "name": name, "text": note["text"],
                 "depth": note["depth"], "every": note["every"]}
        (applied if authors_notes.applies(note, turn) else skipped).append(entry)
    return applied, skipped


def split_point(history: list[dict], depth: int) -> int:
    """Where a note at `depth` goes in `history` (in-context messages): before
    the `depth`-th most recent post, clamped to 0, `depth == 0` meaning after
    the last; snapped back to the start of a run of player posts, or 0."""
    if depth <= 0:
        return len(history)
    target = max(len(history) - depth, 0)
    for i in range(target, -1, -1):
        if (i < len(history) and history[i].get("role") == "user"
                and (i == 0 or history[i - 1].get("role") != "user")):
            return i
    return 0


def render(note: dict) -> str:
    """The note's text as it is sent, before macro expansion."""
    return prompts.render("scene/authors_note.j2", level=note["level"], name=note["name"],
                          text=note["text"])


def _runs_before(history: list[dict], k: int) -> int:
    """How many projected messages `history[:k]` becomes: one per run of a
    role, since the projection merges consecutive same-role messages."""
    return sum(1 for i in range(k) if i == 0 or history[i]["role"] != history[i - 1]["role"])


def inject(history: list[dict], applied: list[dict],
           project: Callable[[list[dict]], list[dict]] | None = None
           ) -> tuple[list[dict], list[dict]]:
    """`history` projected (`story._project_history`, or `project`) with each
    applied note inserted as its own system message. Returns `(projected,
    positions)`: the applied entries, in their given order, each plus `"index"`
    (into `projected`) and `"status": "applied"`.

    Projects the in-context history ONCE and inserts between its messages:
    every split point is a role boundary, so the projection of the whole is
    the projections of the segments laid end to end -- which is also what lets
    a `project` that numbers what it keeps across the whole history (the
    image-reference projection, #377) be used unchanged."""
    history = scenes_serialize.in_context(history)
    projected = list((project or story._project_history)(history))
    if not applied:
        return projected, []
    points: dict[int, list[int]] = {}
    for n, note in enumerate(applied):
        points.setdefault(split_point(history, note["depth"]), []).append(n)
    out: list[dict] = []
    index: dict[int, int] = {}
    prev = 0
    for k in sorted(points):
        at = _runs_before(history, k)
        out.extend(projected[prev:at])
        prev = at
        for n in points[k]:
            index[n] = len(out)
            out.append({"role": "system", "content": render(applied[n])})
    out.extend(projected[prev:])
    positions = [{**note, "index": index[n], "status": "applied"}
                 for n, note in enumerate(applied)]
    return out, positions
