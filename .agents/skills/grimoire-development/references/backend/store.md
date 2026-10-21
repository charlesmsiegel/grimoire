# Store boundaries and imports

Source: [storage.md](../../../../../docs/reference/storage.md), [locks/](../../../../../backend/src/grimoire/store/locks/), [test_locks_store.py](../../../../../backend/tests/test_locks_store.py).

For a compiled-cache or sync change, read the
[compiled-cache contract](../../../../../docs/reference/storage.md#compiled-caches)
and the affected hook in [cache_sync/](../../../../../backend/src/grimoire/store/cache_sync/).
Records remain authoritative; cached results cannot supply a decision's read set.

- Store publication goes through `store.atomic`; campaign-scoped read-modify-write
  operations use `locks.campaign_lock(cid)`. Scene transcripts cannot be regenerated,
  so preserve their lock exclusion and failure-recovery behavior. The lock-domain
  declarations in `store/locks/_domain.py` document ownership and require review
  when a campaign mutator is added or moved.
- For more than one campaign lock, use `locks.hold_all(cids)`, which sorts. Two
  holders once acquired locks in different orders, by recency and by id, and two
  concurrent requests wedged permanently (#267). Verify exclusion and acquisition
  order through runtime tests rather than matching a lock's source spelling.
- Imports in `backend/src/grimoire/` belong at module scope and the module graph
  stays acyclic. Inside `store/`, a cross-package import binds a submodule, such as
  `from ..campaigns import read` followed by `read.world_refs()`. Binding a name
  from an initializing package can fail at import; binding a function by value
  can also make fault injection patch the owner while the caller keeps an old
  reference. Preserve those seams when moving code.

Use the existing static-analysis tools and review for structural conventions.
[AGENTS.md](../../../../../AGENTS.md#test-behavior-not-repository-structure)
explains why source guards and their exemption markers do not belong in tests.
