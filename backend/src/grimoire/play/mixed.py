"""Keeping chosen lines of several takes as one kept take (the app's compare mode).

A turn keeps at most one take, and a line belongs to its take by its id, so lines of two takes cannot be kept by
keeping either. ``keep_lines`` writes one new take holding copies of the chosen lines and keeps that, in the one
compare-and-swap ``keep`` makes. The stored turn then means what a normal keep means: one ``kept`` take whose
shown lines are the transcript, each heard by the audience its line recorded. Nothing later reads how the take
came to be, so other devices, search, recaps and exports need not know.

What it holds to:

- **Only what could be kept anyway.** Every chosen line is a shown line of a ``proposed`` take of the open turn,
  and every take it is drawn from passes ``keep``'s own checks: the cast, the stage and the player characters
  are as they were when it was written, each chosen line is still an NPC's and heard by exactly who would hear
  it now, an NPC that chose what to pursue still sees whom it chose by, unscreened director lore is
  acknowledged, a framed action is decided, play is not paused, and 016's boundaries present the lines to the
  table as it stands when the write commits.
- **One line a speaker.** No take holds two lines of one speaker, so neither does this one: two lines of one
  character (or two narrations) are refused ``one_line_per_speaker``.
- **A defined order.** The lines are kept in the order they were spoken in their own takes (the number that ends
  a line's id), and lines spoken at the same place in two takes in the order of their takes.
- **Nobody heard what was not kept.** A line's audience is copied as recorded and nothing else is: the takes the
  lines came from stay ``proposed`` in a closed turn, which no one hears (``TurnRecord.visible``), exactly as the
  takes a normal keep leaves behind.
- **No call is billed twice or moved.** The new take holds no calls: each call stays on the take it was planned
  and billed under. An NPC's recorded intention is copied with its line.
- **Nothing more is committed.** Like ``keep``, it writes the turn and nothing else: no event, belief or effect.

Choosing every shown line of one take is that take kept whole, so it is ``keep`` itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

from grimoire.core.identity import ChatRef
from grimoire.play.adjudicate import check_decided
from grimoire.play.scene import _refuse, _stage_ok
from grimoire.scene import MAX_TAKES, SceneLine, Take, TurnRecord

#: The most lines one selection names: far more than a turn's speakers, and bounded.
MAX_PICKED = 64


def line_place(line_id: str) -> int:
    """Where a line was spoken in its own take: the number that ends its id (``<turn_id>.<take>.<n>``)."""
    return int(line_id.rsplit(".", 1)[1])


def kept_order(picked: Iterable[tuple[Take, SceneLine]]) -> list[tuple[Take, SceneLine]]:
    """The order chosen lines are kept in: by their place in their own take, then by take number."""
    return sorted(picked, key=lambda pair: (line_place(pair[1].line_id), pair[0].take))


def keep_lines(runner, chat: ChatRef, by, lines: Iterable[str], *, acknowledge_unscreened: bool = False,  # type: ignore[no-untyped-def]
               turn: str | None = None) -> TurnRecord:
    """Keep the lines named by id (as the scene shows them) as the open turn's one kept take; the turn closes.

    ``turn`` names the turn the lines were read on, as for ``keep``: another open turn is refused
    ``turn_changed``, and so is a turn that changed between this read and the write (nothing is half kept)."""
    from grimoire.play import runner as _runner  # the runner imports this module

    if isinstance(lines, (str, bytes)):
        raise _refuse("invalid_field", "lines is a list of line ids")
    chosen = tuple(lines)
    if not all(type(line_id) is str and line_id for line_id in chosen) or len(chosen) > MAX_PICKED:
        raise _refuse("invalid_field", f"lines is at most {MAX_PICKED} line ids")
    if len(set(chosen)) != len(chosen):
        raise _refuse("invalid_field", "a line is named once")
    scene = runner._context(chat).scene
    principal = runner._principal(scene, by)
    stored = runner._open(scene)
    if scene.paused:
        raise _refuse("paused", "play is paused")
    record = stored.record
    if turn is not None and record.turn_id != turn:
        raise _refuse("turn_changed", "those lines were on another turn; read the chat again")
    if not chosen:
        raise _refuse("no_targets", "name the lines to keep")
    offered = {line.line_id: (take, line) for take in record.takes if take.state == "proposed"
               for line in take.shown()}
    missing = [line_id for line_id in chosen if line_id not in offered]
    if missing:
        raise _refuse("not_proposed", "only a shown line of a proposed take of the open turn can be kept")
    picked = kept_order(offered[line_id] for line_id in chosen)
    speakers = [line.actor for _take, line in picked]
    if len(set(speakers)) != len(speakers):
        raise _refuse("one_line_per_speaker", "keep one line a speaker: choose between the takes' lines")
    sources = sorted({take.take: take for take, _line in picked}.values(), key=lambda take: take.take)
    if len(sources) == 1 and len(picked) == len(sources[0].shown()):
        # the whole of one take: the take itself is kept, as ``keep`` keeps it
        return runner.keep(chat, by, sources[0].take, acknowledge_unscreened=acknowledge_unscreened,
                           turn=record.turn_id)
    check_decided(runner, record)
    unscreened = tuple(sorted({name for take in sources for name in take.unscreened}))
    if unscreened and not acknowledge_unscreened:
        raise _refuse("unscreened_lore", "a chosen line's take was written with director lore no screen covered; "
                                        "keep it only after reading it")
    _stage_ok(scene)
    for source in sources:
        if not _runner._stands(scene, source, [line for take, line in picked if take is source]):
            raise _refuse("cast_changed", "who is present, who plays them, or who is in earshot changed since "
                                          "these lines were written; ask again")
    spoke = {(take.take, line.actor) for take, line in picked if line.actor is not None}
    carried = [intention for intention in record.intentions if (intention.take, intention.actor) in spoke]
    if any(_runner._differs(intention.perceived, _runner._sight(scene, intention.actor)) for intention in carried):
        raise _refuse("cast_changed", "who an NPC can see changed since it chose what to pursue; ask again")
    number = len(record.takes) + 1
    if number > MAX_TAKES:
        raise _refuse("too_many_takes", f"a turn holds at most {MAX_TAKES} takes: keep one take whole, or pass")
    kept = tuple(replace(line, line_id=f"{record.turn_id}.{number}.{place}")
                 for place, (_take, line) in enumerate(picked, 1))
    # an opening asked for twice is still an opening; anything else was generated
    origin = "opening" if all(take.origin == "opening" for take in sources) else "generated"
    composed = Take(number, "kept", origin, scene.cast, lines=kept, outcome="complete", unscreened=unscreened,
                    by=principal, on_stage=_runner._here(scene), players=_runner._table(scene))
    if record.running is not None:  # a run still in flight loses: its take lands stale
        record = record.with_take(replace(record.running, state="superseded"))
    record = record.with_take(composed).with_intentions(number, [replace(item, take=number) for item in carried])
    # 016 again, as for ``keep``: for the table read again under the store lock
    runner._write(record, stored, unpaused=True,
                  before_commit=lambda: runner._check_now(chat, principal, kept))
    return record


__all__ = ["MAX_PICKED", "keep_lines", "kept_order", "line_place"]
