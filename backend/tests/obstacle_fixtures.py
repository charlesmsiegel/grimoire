"""A synthetic guarded archive for Sprint 040, on the 029 table: Mara (Ana's PC) wants in; Iris keeps the door.

The archive has four suggested approaches (bribe the guard, fight past him, slip past the lantern, climb
in by a window that does not exist) and facts that bear on them differently. Every name and fact is
synthetic.
"""

from __future__ import annotations

from grimoire.adjudication import ActionFrame, Factor, Stake
from grimoire.core import Moment
from grimoire.core.authority import PlayerContribution
from grimoire.obstacles import Approach, Cost, Obstacle, ObstacleFact, ObstacleStake
from grimoire.obstaclestore import ObstacleLibrary

from adjudication_fixtures import ANA, CHAT, IRIS, MARA, Q, Table, moment

GREEDY = ObstacleFact("greedy", "motive", "The night guard drinks away his pay and is short of coin.", 3,
                      "relation:r-guard-debts", {"bribe": "favours"})
LOYAL = ObstacleFact("loyal", "motive", "He owes his post to the archivist.", 4, "relation:r-guard-post",
                     {"bribe": "hinders"})
ARMED = ObstacleFact("armed", "constraint", "He carries a halberd and wears mail.", 2, "event:e-armed",
                     {"force": "hinders"})
LANTERN = ObstacleFact("lantern", "constraint", "A lantern lights the only door all night.", 5, "event:e-lantern",
                       {"sneak": "hinders"})
WATCH = ObstacleFact("watch", "constraint", "The guard is awake and watching the door.", 6, "event:e-watch",
                     {"*": "hinders"})
SEALS = ObstacleFact("seals", "clue", "Archive requisitions carry the archivist's wax seal.", 1, "event:e-seals", {})
WINDOWLESS = ObstacleFact("windowless", "constraint", "The archive has no windows.", 1, "event:e-plan",
                          {"climb": "prevents"})

REPORTED = ObstacleStake("reported", "The guard reports the attempt to the archivist.",
                         {"bribe": "prevents", "sneak": "hinders"})
SPENT = ObstacleStake("spent", "He pockets the coin and turns her away.")
WOUNDED = ObstacleStake("wounded", "Mara takes a halberd cut.")
ALARM = ObstacleStake("alarm", "The bell is rung and the watch comes running.", {"*": "hinders"})

PURSE = Cost("purse", "Twelve crowns from Mara's purse go to the guard.")
OWED = Cost("owed", "The guard will expect a cut of whatever she takes from the archive.")
BRIBE = Approach("bribe", "Pay the guard to look the other way.", frozenset({"reported", "spent"}), (PURSE, OWED))
FORCE = Approach("force", "Fight past the guard.", frozenset({"wounded", "alarm"}))
SNEAK = Approach("sneak", "Slip past the lantern while he dozes.", frozenset({"alarm"}))
CLIMB = Approach("climb", "Climb in by a window.")
FORGE = Approach("forge", "Forge a requisition under the archivist's seal.", frozenset({"reported"}),
                 (Cost("forgery", "A forged requisition now sits in the archive's daybook."),))

TRAINING = Factor("training", "favours", "Mara trained with the city watch.", 2, "relation:r-mara-watch")
SILVER = Factor("silver", "favours", "Mara has a silver tongue.", 2, "relation:r-mara-tongue")


def archive(**changes) -> Obstacle:
    fields = dict(obstacle_id="archive", campaign_id=Q, goal="Reach the sealed ledger inside the archive.",
                  actors=frozenset({IRIS}), facts=(GREEDY, LOYAL, ARMED, LANTERN, WATCH, SEALS, WINDOWLESS),
                  stakes=(REPORTED, SPENT, WOUNDED, ALARM), approaches=(BRIBE, FORCE, SNEAK, CLIMB),
                  provenance="author:test")
    fields.update(changes)
    return Obstacle(**fields)


def stakes_of(approach: Approach, obstacle: Obstacle | None = None) -> tuple[Stake, ...]:
    obstacle = obstacle or archive()
    return tuple(obstacle.stake(risk).stake for risk in sorted(approach.risks))


def attempt(factors, stakes, *, cid: str = "c10", at: Moment | None = None, actor=MARA,
            intent: str = "Get into the archive.") -> ActionFrame:
    return ActionFrame(cid, actor, CHAT, at or moment(), intent, tuple(factors), tuple(stakes), "labeller:test")


def offer(text: str = "I offer him twelve crowns to look away.", *, cid: str = "c10", turn: str = "t10",
          by: str = ANA) -> PlayerContribution:
    return PlayerContribution(Q, cid, "s1", turn, "mara-1", by, "action", text)


class ArchiveTable(Table):
    def obstacles(self) -> ObstacleLibrary:
        return ObstacleLibrary(self.store, self.authority, self.key)
