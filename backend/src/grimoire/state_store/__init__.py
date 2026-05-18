"""State Store — authoritative persistence layer.

Files are the source of truth for content (markdown + YAML under
``data/library/`` and ``data/campaigns/``). SQLite holds derived indexes,
campaign-scoped structured state, embeddings, audit log, and snapshots.

The :class:`StateStore` mediates every write so the file and the index stay
consistent, records reversible deltas, and supports undo / fork / retcon.
"""

from grimoire.state_store.config import (
    AutoBackupConfig,
    LibrarySectionConfig,
    RetentionConfig,
    SnapshotsConfig,
    StateStoreConfig,
)
from grimoire.state_store.errors import StateStoreError
from grimoire.state_store.paths import (
    KIND_TO_DIR,
    LibraryRef,
    emergent_path,
    image_metadata_path,
    library_path,
    override_path,
    parse_library_id,
    sheet_path,
)
from grimoire.state_store.store import StateStore
from grimoire.state_store.summarizer import BodySummarizer

__all__ = [
    "AutoBackupConfig",
    "BodySummarizer",
    "KIND_TO_DIR",
    "LibrarySectionConfig",
    "LibraryRef",
    "RetentionConfig",
    "SnapshotsConfig",
    "StateStore",
    "StateStoreConfig",
    "StateStoreError",
    "emergent_path",
    "image_metadata_path",
    "library_path",
    "override_path",
    "parse_library_id",
    "sheet_path",
]
