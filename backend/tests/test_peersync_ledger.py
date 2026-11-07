"""028 over the peer transport: two paired devices' offline ledgers merge exactly as a local sync merges
them (test_sync_ledger.py), the pair's base cache included: the snapshot carries the base entries a merge
may need, and a device accepts a peer's base entry only when it holds exactly those bytes itself.

Every library here is synthetic and lives under the test's temporary directory; every server listens on
loopback.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from grimoire.peersync import transfer
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore
from grimoire.sync import identify

from grimoire.effectstore import TurnEffectsLibrary

from chat_fixtures import Clock, Ids
from effects_fixtures import C, CHAT, CONTROLS, TAM
from ledger_fixtures import COIN, build_base, pay, read_ledger
from peersync_helpers import DESKTOP, OWNER_SCOPES, PHONE, Device, serving
from store_helpers import OWNER
from test_sync_ledger import ledger_files


def _accept(device: Device, proposal) -> None:  # type: ignore[no-untyped-def]
    """A director effect (no endorsement), under the device's own durable authority."""
    library = TurnEffectsLibrary(device.store, device.handle, issued=device.authority.issued, clock=Clock(),
                                 new_id=Ids(), owner=OWNER)
    library.accept(proposal, controls=CONTROLS).commit()


@pytest.fixture
def pair(tmp_path: Path):
    from grimoire.peersync import new_pairing_key, pair as make_pair
    base = build_base(tmp_path)
    made = {}
    for name in (DESKTOP, PHONE):
        shutil.copytree(base, tmp_path / name)
        with LibraryStore.open(tmp_path / name) as store:
            identify(store, name)
        device = Device(tmp_path, name, create=False)
        for number, scope in enumerate(OWNER_SCOPES):
            device.authority.add_grant(Grant(f"owner-{number}", OWNER, frozenset({Permission.APPLY}),
                                             ResourcePath.parse(scope), OWNER))
        made[name] = device
    key = new_pairing_key()
    make_pair(made[DESKTOP].store, made[DESKTOP].authority, PHONE, key, granted_by=OWNER)
    make_pair(made[PHONE].store, made[PHONE].authority, DESKTOP, key, granted_by=OWNER)
    yield made
    for device in made.values():
        device.close()


def test_offline_ledgers_merge_over_the_connection_from_the_base_cache(pair):
    desktop, phone = pair[DESKTOP], pair[PHONE]
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _accept(desktop, pay(sequence=8, chat=CHAT, attempt="pay-desk-8"))
        _accept(phone, pay())
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.status == "synced", report
        assert ledger_files(desktop.store) == ledger_files(phone.store)
        tam = f"campaigns/{C}/holdings/character/tam.json"
        assert phone.store.read_sync_state(f"peers/{DESKTOP}/bases/{tam}") == (phone.store.root / tam).read_bytes()
        # both devices change Tam's holder file again: the merge needs the agreed bytes neither still holds
        _accept(desktop, pay(sequence=12, chat=CHAT, attempt="pay-desk-12"))
        _accept(phone, pay(sequence=13, attempt="pay-phone-13"))
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.status == "synced", report
    assert ledger_files(desktop.store) == ledger_files(phone.store)
    assert read_ledger(phone.store).holdings[TAM].value.count(COIN) == 2  # ten, less four payments of two
    with serving(desktop) as server:  # the other way round: already agreed, nothing to write
        report = transfer(phone.store, phone.authority, DESKTOP, server.address)
        assert report.status == "synced", report


def test_a_ledger_merge_both_devices_must_review_is_reported_over_the_connection(pair):
    desktop, phone = pair[DESKTOP], pair[PHONE]
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _accept(desktop, pay(sequence=8, attempt="pay-desk"))
        _accept(phone, pay(sequence=9, attempt="pay-phone"))
        before = {name: ledger_files(device.store) for name, device in pair.items()}
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        assert report.status == "conflicts", report
        assert {conflict.code for conflict in report.conflicts} == {"merge_review"}
        assert {name: ledger_files(device.store) for name, device in pair.items()} == before


def test_resolve_ledger_over_the_connection_merges_and_never_sends_a_discard(pair):
    from grimoire.peersync import resolve_ledger_with
    from grimoire.sync import ResolveRefused
    desktop, phone = pair[DESKTOP], pair[PHONE]
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
        _accept(desktop, pay(sequence=8, attempt="pay-desk"))
        _accept(phone, pay(sequence=9, attempt="pay-phone"))
        report = transfer(desktop.store, desktop.authority, PHONE, server.address)
        token = {conflict.token for conflict in report.conflicts}.pop()
        with pytest.raises(ResolveRefused, match="decide it on phone"):  # it would discard the phone's event
            resolve_ledger_with(desktop.store, desktop.authority, PHONE, server.address, C, DESKTOP, token,
                                decided_by=OWNER)
        with pytest.raises(ResolveRefused, match="only an owner"):
            resolve_ledger_with(desktop.store, desktop.authority, PHONE, server.address, C, PHONE, token,
                                decided_by="human:guest")
        with pytest.raises(ResolveRefused, match="not_on_kept_device"):  # the desktop's own event: revoke it first
            resolve_ledger_with(desktop.store, desktop.authority, PHONE, server.address, C, PHONE, token,
                                decided_by=OWNER)
        resolve_ledger_with(desktop.store, desktop.authority, PHONE, server.address, C, "merge", token,
                            decided_by=OWNER)
        assert ledger_files(desktop.store) == ledger_files(phone.store)
        assert len(read_ledger(phone.store).events) == 2
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"


@pytest.mark.parametrize("what", ["other bytes", "a file it lacks"])
def test_a_peer_may_not_plant_a_base_entry_this_device_does_not_hold(pair, what):
    from test_peersync_bookkeeping import _raw_step
    desktop, phone = pair[DESKTOP], pair[PHONE]
    with serving(phone) as server:
        assert transfer(desktop.store, desktop.authority, PHONE, server.address).status == "synced"
    tam = f"campaigns/{C}/holdings/character/tam.json"
    name = f"peers/{DESKTOP}/bases/{tam}" if what == "other bytes" else f"peers/{DESKTOP}/bases/campaigns/{C}/accepted/e-9-x.json"
    before = phone.store.read_sync_state(name)
    planted = (phone.store.root / tam).read_bytes().replace(b'"seq"', b'"seq" ', 1)
    with serving(phone) as server:
        _raw_step(desktop, server, (name, planted))
    assert phone.store.read_sync_state(name) == before
