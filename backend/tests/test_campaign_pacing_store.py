"""088's campaign preference uses the native scope revision and authorizes every save."""

from dataclasses import replace

import pytest

from calendar_fixtures import OTHER, SCOPE, seed_calendar_scope
from store_helpers import OWNER, owner_handle
from sync_helpers import handles, make_pair, put

from grimoire.campaignapi.pacing import PACE_KEY, PacingError, read_pacing, save_pacing
from grimoire.ledgers import DurableAuthority
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, StaleWrite
from grimoire.sync import sync

PATH = "campaigns/saltmarch/scope.json"


@pytest.fixture
def library(tmp_path):
    with LibraryStore.create(tmp_path / "library") as store:
        seed_calendar_scope(store)
        yield store


def scope_authority(store):
    authority = DurableAuthority.open(store, {OWNER})
    authority.add_grant(Grant("campaign-apply", OWNER, frozenset({Permission.APPLY}),
                              ResourcePath.parse("campaigns"), OWNER))
    return authority


def test_scope_preference_preserves_extensions_and_reopens(library):
    original = (library.root / PATH).read_bytes()
    authority = scope_authority(library)
    owner = authority.handle_for(OWNER, trusted=True)
    start = read_pacing(library, SCOPE)
    assert start.mode == "manual"
    assert save_pacing(library, SCOPE, owner, mode="manual", expected=start.etag) == start
    assert (library.root / PATH).read_bytes() == original
    written = save_pacing(library, SCOPE, owner, mode="every_action", expected=start.etag)
    with pytest.raises(StaleWrite):
        save_pacing(library, SCOPE, owner, mode="every_action", expected=start.etag)
    latest = read_pacing(library, SCOPE)
    assert latest == written and latest.mode == "every_action" and latest.etag != start.etag
    assert read_pacing(library, OTHER).mode == "manual"
    record = library.read(SCOPE).record
    assert record.owner == OWNER and record.extra["x-editor"] == "keep"
    assert record.payload["x-notes"]["keep"] is True
    with LibraryStore.open(library.root, writable=False) as reopened:
        assert read_pacing(reopened, SCOPE) == latest
    assert save_pacing(library, SCOPE, owner, mode="manual", expected=latest.etag).mode == "manual"
    assert PACE_KEY not in library.read(SCOPE).record.payload


def test_invalid_request_and_unauthorized_noop_cannot_change_scope(library, tmp_path):
    with LibraryStore.create(tmp_path / "other") as foreign_store:
        foreign = DurableAuthority.open(foreign_store, {OWNER}).handle_for(OWNER, trusted=True)
        authority = scope_authority(library)
        owner = authority.handle_for(OWNER, trusted=True)
        untrusted = authority.handle_for(OWNER)
        another = authority.handle_for("human:visitor", trusted=True)
        start = read_pacing(library, SCOPE)
        original = (library.root / PATH).read_bytes()
        for rejected in (foreign, untrusted, another):
            for mode in ("manual", "every_action"):
                with pytest.raises(PacingError, match="owner"):
                    save_pacing(library, SCOPE, rejected, mode=mode, expected=start.etag)
        for mode in (None, [], "unknown"):
            with pytest.raises(PacingError) as invalid:
                save_pacing(library, SCOPE, owner, mode=mode, expected=start.etag)
            assert invalid.value.reason == "invalid_field"
        assert (library.root / PATH).read_bytes() == original


def test_explicit_manual_repairs_malformed_preference(library):
    stored = library.read(SCOPE)
    record = replace(stored.record, payload={**stored.record.payload, PACE_KEY: {"secret": "fixture text"}})
    batch = library.transaction()
    batch.put(record, stored.etag)
    batch.commit(owner_handle())
    authority = scope_authority(library)
    owner = authority.handle_for(OWNER, trusted=True)
    malformed = read_pacing(library, SCOPE)
    assert malformed.mode is None
    with pytest.raises(PacingError) as refused:
        save_pacing(library, SCOPE, owner, mode="every_action", expected=malformed.etag)
    assert refused.value.reason == "pacing_malformed"
    assert save_pacing(library, SCOPE, owner, mode="manual", expected=malformed.etag).mode == "manual"
    assert read_pacing(library, SCOPE).mode == "manual"
    assert PACE_KEY not in library.read(SCOPE).record.payload


def test_scope_keep_both_refuses_even_an_unchanged_save(tmp_path):
    desktop, phone = make_pair(tmp_path)
    try:
        seed_calendar_scope(desktop)
        sync(desktop, phone, handles())
        for store, mode in ((desktop, "every_action"), (phone, "every_third_action")):
            record = store.read(SCOPE).record
            put(store, replace(record, payload={**record.payload, PACE_KEY: mode}))
        report = sync(desktop, phone, handles())
        assert report.conflict(PATH).code == "concurrent"
        authority = scope_authority(desktop)
        owner = authority.handle_for(OWNER, trusted=True)
        stored = desktop.read(SCOPE)
        original = (desktop.root / PATH).read_bytes()
        for action in (
            lambda: read_pacing(desktop, SCOPE),
            lambda: save_pacing(desktop, SCOPE, owner, mode="every_action", expected=stored.etag),
        ):
            with pytest.raises(PacingError) as conflict:
                action()
            assert conflict.value.reason == "scope_conflict"
        assert (desktop.root / PATH).read_bytes() == original
    finally:
        desktop.close()
        phone.close()
