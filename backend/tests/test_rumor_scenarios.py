"""063's proving scenario: a distant rival hears a misleading account weeks later and acts on that belief.

Over the synthetic card library (``scene_fixtures``), on the campaign's own 055 calendar. On the quay Anya Rook
laughs at the Vell banner, and only Oswin, a dockhand, sees it. Oswin tells Bryn that evening; Bryn retells it in a
letter to Mara Vell, who is away up the coast, as Anya tearing the banner down and spitting on it. The letter takes
three weeks. Mara is a director NPC whose authored agenda says she calls out whoever slandered her house, and
otherwise keeps trading. She acts on the letter only once it has arrived and the director admits her belief in it,
while the insult itself, its one witness and canon never change. Every model call is a scripted fake.
"""

from __future__ import annotations

import shutil

import pytest

from scene_fixtures import ANA, ADJUDICATOR, Table, card_library, say
from store_helpers import OWNER

from grimoire.calendars import CalendarDefinition, Duration
from grimoire.calendarstore import CalendarLibrary
from grimoire.core import DIRECTOR, Belief, ControlAssignment, Event, Moment, Perception
from grimoire.core.identity import InstanceRef, ScopeRef
from grimoire.core.perspective import standing
from grimoire.evidencestore import EvidenceLibrary
from grimoire.goals import Agenda, Approach, Goal, Holds
from grimoire.ledgers import ControlChange, SovereigntyLedger
from grimoire.rumorledger import output_id
from grimoire.rumors import CampaignTime, DeliveryConditions, Rumor
from grimoire.rumorstore import RumorLibrary
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import RecordEnvelope

DAY = 1440
INSULT = "Anya Rook laughed at the Vell banner on the quay"
TOLD = "Anya Rook laughed at the Vell banner"
LETTER = "Anya Rook tore down the Vell banner and spat on it"
AIM = "Keep the Vell name feared along the coast"
CHALLENGE = "Call the pilot out for what she did to the Vell banner"
TRADE = "Offer the pilot a fair price for passage north"
#: on the quay, face to face; by courier up the coast, where nobody is near
NEAR = DeliveryConditions(proximity=True, affiliation=False, channel=False, attention=True)
LETTERED = DeliveryConditions(proximity=False, affiliation=False, channel=True, attention=True)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return card_library(tmp_path_factory.mktemp("built"))


@pytest.fixture
def cards(built, tmp_path):
    folder, card_chat, _ = built
    copy = tmp_path / "cards"
    shutil.copytree(folder, copy)
    return copy, card_chat


def played(table: Table, chat, text: str):
    table.runner.post(chat, table.player(), text)
    take = table.runner.generate(chat, table.player())
    table.runner.keep(chat, table.player(), take.take)
    return table.runner.open(chat).records[-1]


class Coast:
    """The campaign's insult, its witness and the rumor queue, on one open table."""

    def __init__(self, table: Table, chat, *, fresh: bool = True) -> None:
        self.table, self.chat, self.campaign = table, chat, chat.campaign_id
        scene = table.runner.open(chat)
        self.anya = scene.persona
        (self.mara,) = [actor for actor in scene.cast if actor != scene.persona]
        self.oswin, self.bryn = (InstanceRef(self.campaign, "character", name) for name in ("oswin", "bryn"))
        self.handle = table.authority.handle_for(ADJUDICATOR, trusted=True)
        self.rumors = RumorLibrary(table.store, table.authority, self.handle)
        self.evidence = EvidenceLibrary(table.store, self.handle)
        self.calendar = CalendarLibrary(table.store, self.handle)
        self.scope = ScopeRef(self.campaign)
        if fresh:
            self._setup()

    def _setup(self) -> None:
        table, campaign = self.table, self.campaign
        txn = table.store.transaction()  # two offstage campaign characters, in no chat's cast
        for actor in (self.oswin, self.bryn):
            txn.put(RecordEnvelope(actor, {}), None)
        txn.commit(table.owner)
        for actor in (self.oswin, self.bryn):
            SovereigntyLedger(table.authority).record(campaign, ControlChange(campaign, actor.instance_id, DIRECTOR),
                                                      table.owner)
        table.authority.add_grant(Grant("rumors", ADJUDICATOR, frozenset({Permission.APPLY}),
                                        ResourcePath.parse("campaigns"), OWNER))
        self.calendar.initialize(self.scope, "start", CalendarDefinition(), "1203-03-01", minute=17 * 60,
                                 expected=self.calendar.read(self.scope).etag).commit()

    def next(self) -> Moment:
        knowledge = self.evidence.read(self.campaign, requested_by=DIRECTOR).knowledge
        records = (*knowledge.events, *knowledge.propositions, *knowledge.evidence, *knowledge.beliefs)
        return Moment(max((record.moment.sequence for record in records), default=0) + 1)

    def now(self) -> CampaignTime:
        now = self.calendar.read(self.scope).state.now
        return CampaignTime(now.fixed_day, now.minute)

    def later(self, operation: str, days: int):
        view = self.calendar.read(self.scope)
        return self.calendar.advance(self.scope, operation, Duration(days * DAY), expected=view.etag).commit().state.now

    def deliver(self, conditions: DeliveryConditions):
        return self.rumors.advance(self.campaign, moment=self.next(), conditions=conditions, requested_by=DIRECTOR)

    def send(self, rid, sender, recipient, source, text, distortion, days, route, channel):
        at = self.next()
        sent = self.now()
        rumor = Rumor(rid, sender, recipient, source, text, channel, sender, 80, distortion, "director:rumor",
                      at, sent, sent.advance(days * DAY), route)
        self.rumors.enqueue(rumor, moment=at, requested_by=DIRECTOR)
        return rumor


def test_a_distant_rival_acts_weeks_later_on_a_misleading_account(cards):
    folder, chat = cards
    with Table(folder) as table:
        table.runner.seat(chat, ANA)
        mara = Coast(table, chat).mara
    rival = Agenda(chat.campaign_id, mara, goals=(Goal("name", AIM, 60, (
        Approach("challenge", CHALLENGE, needs=(Holds(output_id(chat.campaign_id, "letter", "proposition")),)),
        Approach("trade", TRADE),
    )),))
    steps = [say("Passage north is forty crowns."), say("Still forty crowns."), say("Still forty."),
             say("You spat on my banner, pilot. Answer for it.")]
    with Table(folder, steps, agendas=[rival]) as table:
        coast = Coast(table, chat, fresh=False)
        campaign, anya, oswin, bryn = coast.campaign, coast.anya, coast.oswin, coast.bryn
        # The insult and its one witness: canon and Oswin's perception, never Mara's.
        at = coast.next()
        coast.evidence.append(campaign, [
            Event(campaign, "banner-insult", INSULT, at, "scene:quay", participants=(anya,)),
            Perception(campaign, "oswin-saw", oswin, "banner-insult", "sight", "a laugh at the banner", at,
                       "scene:quay", True)]).commit()
        truth = coast.evidence.read(campaign, requested_by=DIRECTOR).knowledge
        # That evening Oswin tells Bryn faithfully, face to face.
        coast.send("evening", oswin, bryn, "oswin-saw", TOLD, "none", 0, NEAR, "spoken")
        assert coast.deliver(NEAR)["delivered"] == ("evening",)
        # Bryn's letter up the coast: distorted, and three weeks on the road.
        coast.send("letter", bryn, mara, output_id(campaign, "evening", "report"), LETTER, "exaggerated", 21,
                   LETTERED, "letter")
        sent_view = coast.evidence.actor_knowledge(campaign, bryn, as_of=1000)
        assert [s.text for s in sent_view.sent] == [LETTER]  # Bryn knows she sent it, from the day she did

        first = played(table, chat, '"I need passage north."')
        (intention,) = first.intentions
        assert intention.approach_id == "trade"  # Mara holds nothing about Anya yet

        coast.later("a-week", 7)
        assert coast.deliver(LETTERED)["delivered"] == ()  # still on the road
        coast.later("two-weeks-more", 14)
        assert coast.deliver(NEAR)["delivered"] == ()  # due, but a letter is not carried face to face
        assert coast.deliver(LETTERED)["delivered"] == ("letter",)
        letter = output_id(campaign, "letter", "report")
        assert [item.content for item in coast.evidence.actor_knowledge(campaign, mara, as_of=1000).evidence] == [LETTER]
        # The delivery changed nothing Bryn knows, now or as of before it: the send was always there.
        assert coast.evidence.actor_knowledge(campaign, bryn, as_of=1000) == sent_view

        heard = played(table, chat, '"Forty crowns, then?"')
        assert heard.intentions[0].approach_id == "trade"  # hearing is not believing

        coast.evidence.append(campaign, [Belief(campaign, "mara-believes", mara,
                                                output_id(campaign, "letter", "proposition"), "believed",
                                                coast.next(), DIRECTOR, (letter,))],
                              controls=[ControlAssignment(campaign, mara.instance_id, DIRECTOR)]).commit()
        acted = played(table, chat, '"Well?"')
        (intention,) = acted.intentions
        assert intention.approach_id == "challenge" and intention.as_of == coast.next().sequence - 1
        prompt = table.harness.prompts()[-1]
        assert CHALLENGE in prompt and LETTER in prompt
        assert INSULT not in prompt and "laughed" not in prompt  # Mara never learned what really happened

        # The actual insult and its witness are untouched, and canon never took the letter's word.
        after = coast.evidence.read(campaign, requested_by=DIRECTOR).knowledge
        assert after.events == truth.events and after.rulings == truth.rulings
        assert [e for e in after.evidence if e.holder == oswin] == [e for e in truth.evidence if e.holder == oswin]
        assert standing(after, output_id(campaign, "letter", "proposition"), as_of=1000) == "reported"
        # The director can trace who told whom, and how the account changed on the way.
        rows, _ = coast.rumors.trace(campaign, requested_by=DIRECTOR)
        assert [(rows[r]["rumor"].sender, rows[r]["rumor"].recipient, rows[r]["rumor"].distortion)
                for r in ("evening", "letter")] == [(oswin, bryn, "none"), (bryn, mara, "exaggerated")]
        assert rows["letter"]["rumor"].due_on.fixed_day - rows["letter"]["rumor"].sent_on.fixed_day == 21

