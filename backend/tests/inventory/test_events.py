from grimoire import events as global_events
from grimoire.inventory import events as inv_events


def test_event_constants_exist():
    assert inv_events.INVENTORY_CHANGED == "inventory_changed"
    assert inv_events.INVENTORY_FLAGGED == "inventory_flagged"


def test_events_registered_globally():
    assert global_events.INVENTORY_CHANGED == "inventory_changed"
    assert global_events.INVENTORY_FLAGGED == "inventory_flagged"
