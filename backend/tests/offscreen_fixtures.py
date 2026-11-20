"""Synthetic rival guild, its recorded leader decision and a campaign calendar (076)."""
from datetime import date

from grimoire.calendars import Advance, CalendarDefinition, Deadline, Duration, Initialize, Schedule
from grimoire.calendars import Moment as Day
from grimoire.calendars import apply as calendar_apply
from grimoire.core.authority import DIRECTOR
from grimoire.core.entity import EntityInstance
from grimoire.core.facets import Agent, FacetBundle
from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.index import DefinitionIndex
from grimoire.core.knowledge import Moment
from grimoire.core.model import ControlAssignment
from grimoire.core.roster import CampaignRoster
from grimoire.graph.model import Relation
from grimoire.institutions import Institution, InstitutionState, decide
from grimoire.offscreen import FactionPlan, PlanStep

C = "saltmarch"
GUILD = InstanceRef(C, "group", "rival-guild")
LEADER = InstanceRef(C, "character", "guildmaster")
SHOPKEEPER = InstanceRef(C, "character", "shopkeeper")
DAY = 1440
START = Day(date(2024, 2, 20).toordinal(), 600)


def decided_guild() -> InstitutionState:
    roster = CampaignRoster(C, tuple(EntityInstance(r, local_name=r.instance_id,
        added=FacetBundle((Agent(),)) if r.kind == "character" else FacetBundle())
        for r in (GUILD, LEADER, SHOPKEEPER)))
    member = Relation(RelationRef(C, "m1"), "member_of", LEADER, GUILD, Moment(0), "authored", "now")
    state = InstitutionState(Institution(GUILD, LEADER, ("Control the harbour",), "Leader decides"))
    state, _ = decide(state, preferences=(), proposal_id="docks", choice="Seize the docks",
                      operation_id="seize-docks", expected_revision=0, roster=roster,
                      definitions=DefinitionIndex((), ()), relations=(member,),
                      controls=(ControlAssignment(C, "guildmaster", DIRECTOR),), authored_by=DIRECTOR,
                      moment=Moment(1), authorization_moment=Moment(1), audience=(), channel="council",
                      provenance="director-authored council")
    return state


def rival_plan(**changes) -> FactionPlan:
    values = dict(plan_id="docks", group=GUILD, decision_id="seize-docks", choice="Seize the docks",
                  goal="Control the harbour",
                  steps=(PlanStep("coin", "Gather the bribe money", 5 * DAY),
                         PlanStep("magistrate", "Buy the harbour magistrate", 7 * DAY),
                         PlanStep("seize", "Take the docks by force", 10 * DAY)),
                  priority=80, deadline_id="festival")
    values.update(changes)
    return FactionPlan(**values)


def calendar(*operations):
    state = None
    for operation in (Initialize("start", CalendarDefinition(), START),
                      Schedule("schedule-festival", Deadline("festival", START.advance(21 * DAY), "Harbour festival")),
                      *operations):
        state, _, _ = calendar_apply(C, state, operation)
    return state


def travel(operation_id="travel", days=20):
    return Advance(operation_id, Duration(days * DAY))
