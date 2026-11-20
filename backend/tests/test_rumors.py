import pytest
from grimoire.rumors import Rumor, DeliveryConditions
from evidence_fixtures import MARA, INES, m


def test_clock_and_route_validation():
    base = dict(communication_id='r1', sender=MARA, recipient=INES, source_evidence_id='v-mara', text='news', channel='told', attributed_to=MARA, confidence=80, distortion='none', provenance='authored', authored_at=m(8), clock_id='world', clock_unit='day', sent_tick=1, due_tick=2, route=DeliveryConditions(True, True, True, True))
    assert Rumor(**base).due_tick == 2
    for key, value in [('sent_tick', True), ('due_tick', 0), ('confidence', 101)]:
        with pytest.raises(ValueError):
            Rumor(**(base | {key: value}))
