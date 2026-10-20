"""characters: memo responsibilities."""

from __future__ import annotations

#: The listing's memo families live in pools of their own rather than in
#: statcache's shared FIFO of 4096 (see `statcache.memo`'s `pool`): a large
#: library's rows and card summaries can outnumber that budget on their own,
#: and a listing that evicted its own working set -- and every entity and sync
#: hash with it -- would hand the next listing a cold cache. Sized above any
#: plausible library's cast, like `scenes.read.POOL_ENTRIES`; a row and a
#: summary are each a small dict.
POOL_ENTRIES = 65536
_ROW_POOL: dict = {}
_SUMMARY_POOL: dict = {}
_FACTS_POOL: dict = {}
_IDENTITY_POOL: dict = {}
