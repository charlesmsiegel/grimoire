"""A synthetic two-room scene for the Sprint 041 tests (no user content).

The study and the hall are joined by the study door, locked, whose key Mara holds. The hall's front door
leads off stage and is open. Mara and Toma stand in the study; a letter lies on the desk; Mara's satchel
holds a coin; Emma waits in the hall. Odd, a fourth cast member, has not entered yet.
"""

from __future__ import annotations

from grimoire.core import InstanceRef
from grimoire.staging import Absence, Area, Placement, Stage, Way

C = "c1"
SCENE = "the-study"


def ref(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


MARA, TOMA, EMMA, ODD = ref("mara"), ref("toma"), ref("emma"), ref("odd")
KEY, LETTER, SATCHEL, COIN = ref("brass-key", "item"), ref("letter", "item"), ref("satchel", "item"), ref("coin", "item")
ACTORS = (MARA, TOMA, EMMA, ODD)
STUDY_DOOR, FRONT_DOOR = "study-door", "front-door"


def stage(*, as_of: int = 1, door: str = "locked", placements=None, absences=None, ways=None) -> Stage:
    return Stage(
        C, SCENE, as_of,
        (Area("study", "A cramped study"), Area("hall")),
        ways if ways is not None else (Way(STUDY_DOOR, ("study", "hall"), door, key=KEY),
                                       Way(FRONT_DOOR, ("hall", None), "open")),
        ACTORS,
        placements if placements is not None else (
            Placement(MARA, area="study"), Placement(TOMA, area="study"), Placement(EMMA, area="hall"),
            Placement(KEY, holder=MARA), Placement(LETTER, area="study"),
            Placement(SATCHEL, holder=MARA), Placement(COIN, holder=SATCHEL)),
        absences if absences is not None else (Absence(ODD),),
    )
