"""Eval-only run interchange format ``grimoire.story-quality/3``: ``/2`` plus director replies.

Sprint 027 added ``director_generation``, an optional scene-level reply by the director or
narrator. ``/2`` froze its stage list without it, so recording one is this new format rather than
a new meaning for shipped ``/2`` files. A ``/3`` run is read exactly as ``/2`` (rebuilt as plans
and ledgers, see ``format2``) except:

- ``director_generation`` is a stage. Like any optional stage it is planned only when the run's
  policy gives it a cap, so a director call the policy does not enable is invalid input, never
  an implicit extra. It speaks for the scene (``actor_id`` null) at most once per turn.
- A model contribution with ``speaker_id`` null is narration, and must name its turn's
  completed ``director_generation`` call. That call contributes exactly one line, with no
  speaker; a second line naming it is invalid input.
- Sources are ``synthetic`` or ``v2-runtime``. A 0.2.1 capture has no director call under its
  profile and stays ``/2``, where its evidence is verified.

The gate reads narration through the ``/1`` ``narration`` purpose, so the sovereignty,
continuity, attribution, orphan and voice-copy checks apply to it as to NPC lines, and two
narration-only checks apply (``checks``, per sentence of the line):

- ``knowledge_leak``: narration states a fact before any player character knows it, or a sentence
  that states a fact names an NPC (scenario name or id) that does not know it yet on that turn.
- ``narration_voices_actor``: a sentence names an actor (NPC or PC) and has quoted speech or a
  speech verb (``text.SPEECH_VERBS``). Only an NPC's own call speaks for it, and only the player
  for a PC, so the director may describe what an actor does but not what it says.
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.core.inference import InferenceStage

from .format import Scenario
from .format2 import STAGES_V2, Dialect, RunV2, parse_ledger_run

FORMAT_V3 = "grimoire.story-quality/3"
#: Frozen for /3, as STAGES_V2 is for /2: a further stage is a /4.
STAGES_V3 = (*STAGES_V2, InferenceStage.DIRECTOR_GENERATION.value)
SOURCE_KINDS_V3 = ("synthetic", "v2-runtime")


@dataclass(frozen=True, slots=True)
class RunV3(RunV2):
    """A ``/3`` run: the ``/2`` value, whose calls may include one director reply per turn."""

    @property
    def format(self) -> str:
        return FORMAT_V3


V3 = Dialect(FORMAT_V3, STAGES_V3, SOURCE_KINDS_V3,
             "only an npc_generation or director_generation call produces a contribution",
             " (scene-level narration names a director_generation call)", RunV3)


def parse_run_v3(data: object, scenario: Scenario) -> RunV3:
    return parse_ledger_run(data, scenario, V3)  # type: ignore[return-value]
