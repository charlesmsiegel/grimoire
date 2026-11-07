"""048's representative benchmarks: a played turn, cold and warm, over a growing history, and two imports.

Synthetic only, as every fixture here: the scene_fixtures libraries, scripted provider replies (their usage
is the transport's reference counts, their latency is a scripted clock's), no real library and no paid call.

- **Turn.** A history of ``history`` kept turns (a post and a reply each) is written into a chat. Then a
  *cold* table (a new store, authority and runner, as after an app start) opens the chat and plays one turn
  (``post``, ``generate``, ``keep``), and the same table plays a second, *warm* turn. Every runner call is one
  trace; the bench also times each call from outside, so the same numbers can be taken from a runner with no
  tracing hook (``wall_s``).
- **Import.** 026's V1 import of its two-NPC library (scan, plan, apply) and 037's import of one card (plan,
  commit), timed by phase.

``backend/tools/bench_scene.py`` prints these; ``test_scene_bench.py`` checks what they report.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from card_fixtures import encode, land
from generation_fixtures import say
from import_fixtures import Env
from scene_fixtures import (
    ANA, MARA_VOICE, SERAPHINE_CARD, SERAPHINE_DIR, TAMSIN_CARD, V1_CHAT, Table, card_library, v1_library,
)
from v1_fixture import CAMPAIGN, sha, write

from grimoire.core.identity import ChatRef
from grimoire.scene import Post, SceneLine, Take, TurnRecord
from grimoire.store import LibraryStore
from grimoire.tracing import Trace, TraceBuilder, TraceLog

PHASES = ("cold", "warm")
TURN_CALLS = ("open", "post", "generate", "keep")


@dataclass(frozen=True)
class Measured:
    """One timed call: which scenario, history size and phase, its outside wall time and its trace (``None``
    from a runner with no tracing hook)."""

    scenario: str
    history: int
    phase: str
    operation: str
    wall_s: float
    trace: Trace | None


def seed_history(folder: Path, chat: ChatRef, turns: int, *, speaker_index: int = 0) -> None:
    """Append kept turns to ``chat`` until it holds ``turns``: a player's post and one NPC reply each, written
    directly as turn records (no model call), each heard by the whole cast."""
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        scene = table.runner.open(chat)
        persona, npc = scene.persona, scene.npcs[speaker_index]
        name = scene.names[npc]
        for number in range(len(scene.records) + 1, turns + 1):
            reply = SceneLine(f"{scene.turns.chat.chat_id}.{number:06d}.1.1", npc, name, f"Reply {number}.",
                              scene.cast)
            record = TurnRecord(chat, number, "post",
                                Post(ANA, persona, "speech", f"Line {number}.", scene.cast, "speech"),
                                (Take(1, "kept", "generated", scene.cast, lines=(reply,), by=ANA),))
            table.runner.scenes.write(record, None)


def _timed(log: list[Measured], traces: TraceLog | None, scenario: str, history: int, phase: str,
           operation: str, call: Callable[[], object]) -> object:
    before = len(traces.traces) if traces is not None else 0
    started = time.perf_counter()
    result = call()
    wall = time.perf_counter() - started
    found = traces.traces[before:] if traces is not None else ()
    log.append(Measured(scenario, history, phase, operation, wall, found[-1] if found else None))
    return result


def play_turns(folder: Path, chat: ChatRef, *, scenario: str, history: int, speakers: int,
               voices=None, traced: bool = True) -> list[Measured]:
    """The cold turn and the warm turn over ``chat`` as it stands. ``speakers`` is how many NPCs answer (one
    scripted reply each per turn); ``traced=False`` runs a runner with no tracer (or one without the hook)."""
    measured: list[Measured] = []
    replies = [say(f"The tide turns, reply {n}.") for n in range(2 * speakers)]
    traces = TraceLog(keep=64) if traced else None
    options = {"tracer": traces.record} if traces is not None else {}
    with Table(folder, replies, voices=voices, **options) as table:
        for phase in PHASES:
            _timed(measured, traces, scenario, history, phase, "open", lambda: table.runner.open(chat))
            _timed(measured, traces, scenario, history, phase, "post",
                   lambda: table.runner.post(chat, table.player(), f'"Where is the {phase} ship bound?"'))
            take = _timed(measured, traces, scenario, history, phase, "generate",
                          lambda: table.runner.generate(chat, table.player()))
            _timed(measured, traces, scenario, history, phase, "keep",
                   lambda: table.runner.keep(chat, table.player(), take.take))
    return measured


def card_turns(root: Path, histories=(10, 100), *, traced: bool = True) -> list[Measured]:
    """The world-less card chat (Mara Vell, with her lorebook; one speaker) at each history size."""
    root.mkdir(parents=True, exist_ok=True)
    folder, chat, _storm = card_library(root)
    measured: list[Measured] = []
    for history in histories:
        seed_history(folder, chat, history)
        measured += play_turns(folder, chat, scenario="card", history=history, speakers=1, traced=traced)
    return measured


def v1_turns(root: Path, histories=(10,), *, traced: bool = True) -> list[Measured]:
    """026's migrated two-NPC campaign (Mara and Seraphine both answer) at each history size."""
    root.mkdir(parents=True, exist_ok=True)
    folder = v1_library(root)
    measured: list[Measured] = []
    for history in histories:
        seed_history(folder, V1_CHAT, history)
        measured += play_turns(folder, V1_CHAT, scenario="v1", history=history, speakers=2, voices=MARA_VOICE,
                               traced=traced)
    return measured


def import_traces(root: Path) -> list[Trace]:
    """026's V1 import (scan, plan, apply) and 037's import of one card (plan, commit), each one trace."""
    root.mkdir(parents=True, exist_ok=True)
    traces = []
    builder = TraceBuilder(time.perf_counter)
    with builder.span("import.v1"):
        with builder.span("scan"):
            (root / "v1-import").mkdir()
            env = Env(root / "v1-import")
            write(env.v1, f"{SERAPHINE_DIR}/default.json", SERAPHINE_CARD)
            write(env.v1, f"{CAMPAIGN}/characters/seraphine/default.json", SERAPHINE_CARD)
            appearances = json.loads((env.v1 / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
            appearances["characters/seraphine"]["base"] = sha(SERAPHINE_CARD)
            write(env.v1, f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
            env.rescan()
        with builder.span("plan"):
            planned = env.plan()
        with builder.span("apply"):
            planned.apply()
        env.store.close()
    traces.append(builder.trace)
    builder = TraceBuilder(time.perf_counter)
    with LibraryStore.create(root / "card-import") as store:
        with builder.span("import.card"):
            land(store, encode(TAMSIN_CARD))
    traces.append(builder.trace)
    return traces


__all__ = ["Measured", "PHASES", "TURN_CALLS", "card_turns", "import_traces", "play_turns", "seed_history",
           "v1_turns"]
