"""An orphaned memory or rumor still reserves the individuals it names."""

import pytest

from grimoire.chatstore import campaign_taken_ids
from grimoire.core.identity import InstanceRef
from grimoire.ledgers import DurableAuthority
from grimoire.ledgers.codec import envelope
from grimoire.store import LibraryStore, ProcessLocker, TakenIdsUnlisted


@pytest.mark.parametrize('family,field', [('memory', 'actor'), ('rumors', 'sender')])
def test_orphan_continuity_ledger_reserves_actor_identity(tmp_path, family, field):
    # No instance or evidence file exists. The raw ledger is intentionally not
    # domain-decodable: damage must not permit identity reuse either.
    with LibraryStore.create(tmp_path / 'lib', locker=ProcessLocker()) as store:
        with DurableAuthority.open(store, {'human:owner'}) as authority:
            authority._append(f'{family}/camp', [envelope(
                'legacy-or-damaged', 'synthetic', value={field: str(InstanceRef('camp', 'character', 'mara'))}
            )], None)
            assert 'mara' in campaign_taken_ids(store, 'camp')
            assert 'mara' not in campaign_taken_ids(store, 'elsewhere')


@pytest.mark.parametrize('family', ['memory', 'rumors'])
def test_unreadable_continuity_ledger_blocks_identity_allocation(tmp_path, family):
    with LibraryStore.create(tmp_path / 'lib', locker=ProcessLocker()) as store:
        with DurableAuthority.open(store, {'human:owner'}) as authority:
            authority._append(f'{family}/camp', [envelope('synthetic', 'synthetic')], None)
        batch = next((store.root / 'authority' / family / 'camp').glob('*.json'))
        batch.write_bytes(b'{unreadable')
        with pytest.raises(TakenIdsUnlisted):
            campaign_taken_ids(store, 'camp')


@pytest.mark.parametrize('actor', [None, ['campaign:camp/character/mara'], 7,
                                  'campaign:elsewhere/character/mara'])
def test_malformed_memory_actor_blocks_identity_allocation(tmp_path, actor):
    with LibraryStore.create(tmp_path / 'lib', locker=ProcessLocker()) as store:
        with DurableAuthority.open(store, {'human:owner'}) as authority:
            authority._append('memory/camp', [envelope(
                'damaged', 'synthetic', value={'actor': actor}
            )], None)
            with pytest.raises(TakenIdsUnlisted):
                campaign_taken_ids(store, 'camp')


@pytest.mark.parametrize('field', ['sender', 'recipient'])
def test_null_required_rumor_actor_blocks_identity_allocation(tmp_path, field):
    with LibraryStore.create(tmp_path / 'lib', locker=ProcessLocker()) as store:
        with DurableAuthority.open(store, {'human:owner'}) as authority:
            authority._append('rumors/camp', [envelope('damaged', 'synthetic', value={field: None})], None)
            with pytest.raises(TakenIdsUnlisted):
                campaign_taken_ids(store, 'camp')


def test_anonymous_rumor_attribution_does_not_block_identity_allocation(tmp_path):
    with LibraryStore.create(tmp_path / 'lib', locker=ProcessLocker()) as store:
        with DurableAuthority.open(store, {'human:owner'}) as authority:
            authority._append('rumors/camp', [envelope('damaged', 'synthetic', value={'attributed_to': None})], None)
            assert campaign_taken_ids(store, 'camp') == frozenset()
