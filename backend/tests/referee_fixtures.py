"""A synthetic inn for the Sprint 047 referee tests (no user content).

The taproom and the cellar are joined by the cellar door, locked; Mara holds its key. Oda (an NPC the
director plays) and Mara (``human:ana``'s PC) stand in the taproom; Toma (an NPC) is in the cellar with the
cask. Pell, a fourth cast member, has left by the front door. Oda drank her last healing draught at the
last turn (operation ``drink-1``). Only Toma was told where Mara hid the strongbox key; Oda saw the strongbox
carried upstairs. Oda's guard is set against bribes after she reported the last one.
"""

from __future__ import annotations

from grimoire.adjudication import ActionFrame
from grimoire.core import DIRECTOR, ChatRef, InstanceRef, Knowledge, Moment, Perception, Proposition, Report, Subject
from grimoire.core.authority import ControlAssignment, NarrationClaim, NarrationContext, Resolution
from grimoire.holdings import Holdings, Line, LogEntry
from grimoire.obstacles import Approach, AttemptState, Attempt, Obstacle, ObstacleFact, ObstacleStake, ObstacleState
from grimoire.obstacles.model import escalation
from grimoire.possessions import Origin, Possession, State
from grimoire.referee import Authority, ContinuityClaim, Facts, LabelledClaim
from grimoire.staging import Absence, Area, Placement, Stage, Way

C = "inn-1"
SCENE = "the-taproom"
ANA = "human:ana"
NOW = 7
AT = "2026-11-06T10:00:00Z"


def ref(name: str, kind: str = "character") -> InstanceRef:
    return InstanceRef(C, kind, name)


ODA, MARA, TOMA, PELL = ref("oda"), ref("mara"), ref("toma"), ref("pell")
KEY, CASK, MUG, SATCHEL = ref("cellar-key", "item"), ref("cask", "item"), ref("mug", "item"), ref("satchel", "item")
DAGGER = ref("dagger", "item")
ACTORS = (ODA, MARA, TOMA, PELL)
CELLAR_DOOR, FRONT_DOOR = "cellar-door", "front-door"
DRAUGHT = "healing-draught"
CONTROLS = (ControlAssignment(C, "oda", DIRECTOR), ControlAssignment(C, "mara", ANA),
            ControlAssignment(C, "toma", DIRECTOR), ControlAssignment(C, "pell", DIRECTOR))


def stage(*, as_of: int = NOW, door: str = "locked", door_basis: str = "established",
          extra=(), absences=None) -> Stage:
    return Stage(
        C, SCENE, as_of, (Area("taproom"), Area("cellar")),
        (Way(CELLAR_DOOR, ("cellar", "taproom"), door, key=KEY, basis=door_basis),
         Way(FRONT_DOOR, ("taproom", None), "open")),
        ACTORS,
        (Placement(ODA, area="taproom"), Placement(MARA, area="taproom"), Placement(TOMA, area="cellar"),
         Placement(KEY, holder=MARA), Placement(CASK, area="cellar"), Placement(MUG, area="taproom"),
         Placement(SATCHEL, holder=ODA), *extra),
        absences if absences is not None else (Absence(PELL, via=FRONT_DOOR),),
    )


def holdings(*, draughts: int = 0) -> Holdings:
    drink = LogEntry("drink-1", 2, AT, (Line("consume", ODA, DRAUGHT, 1),), after={DRAUGHT: draughts})
    buy = LogEntry("buy-1", 1, AT, (Line("add", ODA, DRAUGHT, 1),), after={DRAUGHT: draughts + 1})
    return Holdings(ODA, 2, 0, {DRAUGHT: draughts} if draughts else {}, (buy, drink))


def dagger(holder: InstanceRef = SATCHEL) -> Possession:
    origin = Origin("forge-1", AT, ODA, ODA, "Dagger", None, ())
    return Possession(DAGGER, State(holder, ODA), origin)


def satchel(holder: InstanceRef = ODA) -> Possession:
    return Possession(SATCHEL, State(holder, ODA), Origin("sew-1", AT, ODA, ODA, "Satchel", None, ()))


HIDING = Proposition(C, "p-hiding", "Mara hid the strongbox key under the third stair.", Moment(3), "director",
                     about=(Subject("instance", str(ref("strongbox", "item"))),))
CARRIED = "e-carried"


def knowledge() -> Knowledge:
    from grimoire.core import Event
    return Knowledge(
        C, events=(Event(C, CARRIED, "The strongbox is carried upstairs.", Moment(2), "director"),),
        propositions=(HIDING,),
        evidence=(Report(C, "r-toma", TOMA, HIDING.proposition_id, "told", Moment(4), "director", confirmed=True,
                         sender=MARA, attributed_to=MARA, addressed=True, sent_at=Moment(4)),
                  Perception(C, "p-oda", ODA, CARRIED, "witnessed", "a heavy box going up the stairs", Moment(2),
                             "director", confirmed=True)),
    )


BRIBE = Approach("bribe", "Pay Oda to look the other way.", frozenset({"reported"}))
FORCE = Approach("force", "Shove past Oda.", frozenset())
SNEAK = Approach("sneak", "Slip past while she pours.", frozenset())
REPORTED = ObstacleStake("reported", "Oda reports the bribe to the innkeeper.", {"bribe": "prevents"})
WARY = ObstacleFact("wary", "motive", "Oda watches the stairs.", 1, "event:e-wary", {"*": "hinders"})


def stairs() -> Obstacle:
    return Obstacle("stairs", C, "Get upstairs to the strongbox.", frozenset({ODA}), (WARY,), (REPORTED,),
                    (BRIBE, FORCE, SNEAK), "author:test")


def frame(attempt_id: str, actor: InstanceRef = MARA, sequence: int = 5) -> ActionFrame:
    return ActionFrame(attempt_id, actor, ChatRef(C, "main"), Moment(sequence, SCENE, f"t{sequence}"),
                       "Get past Oda.", (), (), "labeller:test")


def stairs_state(*, status: str = "open", overcome_by: str | None = None, winner: InstanceRef = MARA,
                 framed: bool = True) -> ObstacleState:
    obstacle = stairs()
    failed = AttemptState(Attempt("a-1", BRIBE, True), frame("a-1", sequence=4), "failed")
    attempts = (failed,)
    if status == "overcome":
        attempts += (AttemptState(Attempt("a-2", SNEAK, True), frame("a-2", winner, 6) if framed else None,
                                  "overcame"),)
    return ObstacleState(obstacle, 1, status, attempts, (WARY, escalation(obstacle, REPORTED, 5)), overcome_by)


def agenda():
    from grimoire.goals import Agenda, Approach, Goal, Limit, Present
    return Agenda(C, ODA, (Goal("keep", "Keep the house's stores for the house", 10, (
        Approach("sell", "Sell Mara a cask", tags=frozenset({"trade"})),
        Approach("pour", "Pour Mara a mug on the house", tags=frozenset({"hospitality"})),
        Approach("bully", "Have Toma lean on Mara to leave", needs=(Present(TOMA),), tags=frozenset({"threat"})))),),
        (Limit("no-sale", "Sell nothing to Mara tonight", frozenset({"trade"}), when=(Present(MARA),)),
         Limit("no-threats", "Never threaten a guest", frozenset({"threat"})),))


def intention(*, as_of: int = NOW, present=None, source=None):
    """Oda's 039 choice this turn, for the cast on ``stage()``: briefed to refuse Mara a sale and to threaten no
    one (Toma could lean on Mara), she pours Mara a mug instead."""
    from grimoire.core.perspective import actor_knowledge
    from grimoire.goals import choose
    cast = stage(as_of=as_of).on_stage() if present is None else present
    return choose(agenda(), actor_knowledge(knowledge() if source is None else source, ODA, as_of=as_of), present=cast,
                  controls=CONTROLS)


def facts(**changes) -> Facts:
    fields = dict(campaign_id=C, as_of=NOW, stage=stage(), holdings=(holdings(),),
                  possessions=(dagger(), satchel()), knowledge=knowledge(),
                  resolutions=(Resolution(C, "c-5", "failure"),), obstacles=(stairs_state(),))
    fields.update(changes)
    return Facts(**fields)


def claim(text: str, words: str, claim_id: str, kind: str, actor: InstanceRef = ODA, **fields) -> ContinuityClaim:
    """A claim anchored to the first occurrence of ``words`` in ``text``."""
    start = text.index(words)
    return ContinuityClaim(C, claim_id, kind, actor, (start, start + len(words)), words, **fields)


def labelled(text: str, words: str, claim_id: str, domain: str, subject: str | None, *,
             voiced_by: str | None = None, **fields) -> LabelledClaim:
    start = text.index(words)
    return LabelledClaim(NarrationClaim(C, claim_id, domain, words, subject_id=subject, **fields),
                         (start, start + len(words)), words, voiced_by)


def authority(controls=CONTROLS, **fields) -> Authority:
    return Authority(NarrationContext(C, SCENE, "t7"), controls, **fields)
