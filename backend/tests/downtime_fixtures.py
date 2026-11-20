"""Native synthetic campaign, actor and study fixtures for Sprint 074 (over 055's calendar fixtures)."""
from grimoire.core.identity import InstanceRef
from grimoire.downtime import Activity
from grimoire.downtimestore import DowntimeLibrary
from grimoire.store import RecordEnvelope

from calendar_fixtures import OLD, OTHER, SCOPE, scheduled, seed_calendar_scope
from store_helpers import owner_handle

MARA = InstanceRef("saltmarch", "character", "mara")
DAY = 1440
#: Twenty days of full-time effort: the first useful proof's study.
STUDY = Activity("tide-cant", MARA, "study", "Learn the harbor's tide cant", 20 * DAY)

__all__ = ["DAY", "MARA", "OLD", "OTHER", "SCOPE", "STUDY", "begun", "seed_downtime", "seed_actor"]


def seed_actor(store, ref=MARA, handle=None):
    txn = store.transaction()
    txn.put(RecordEnvelope(ref, {"bio": "a ferry clerk"}, name="Mara"), None)
    txn.commit(handle or owner_handle())


def seed_downtime(store):
    """The 055 campaign with a meeting deadline on 2024-03-01, plus one actor, Mara."""
    seed_calendar_scope(store)
    seed_actor(store)
    scheduled(store)


def begun(store, activity=STUDY, handle=None):
    library = DowntimeLibrary(store, handle or owner_handle())
    view = library.read(SCOPE)
    if view.state.activity(activity.activity_id) is None:
        library.begin(SCOPE, "begin-" + activity.activity_id, activity, expected=view.etag).commit()
    return library
