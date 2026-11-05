"""Reviewed V0.2.1 import apply (Sprint 026): the first writer of V2 state from a V1 library.

An adapter, not part of the pure core. Its only input is 011's reviewed, deterministic inventory
manifest and the V1 library it describes, which ``grimoire.migrate_v1.open_verified`` first proves
unchanged (entry hashes, fingerprint, the manifest itself). ``plan_import`` maps the selected worlds
to sources with one definition per V1 portrayal, campaigns to 107 play scopes with one actor per
appearance, scenes to chats and pictures to 018 image objects and placements; ``review()`` and
``preview()`` show it, and ``apply()`` writes it as **one** guarded store transaction together with a
receipt that maps every V1 file to what it became. A re-run writes only what is missing and never
overwrites, recreates or duplicates: V2 edits are kept, deletions stay deleted. The V1 library is
never written, no model is called and nothing is fetched. See docs/IMPORT_V1.md.
"""

from ._map import MARKER, Desired, ImportRefused, Selection, chat_id_of, image_id_of, revision_of
from ._plan import (
    RECEIPT_SCHEMA, ImportPlan, ImportResult, Receipts, legacy_map, plan_import, read_receipts, receipt_source,
)

__all__ = [
    "MARKER", "RECEIPT_SCHEMA", "Desired", "ImportPlan", "ImportRefused", "ImportResult", "Receipts", "Selection",
    "chat_id_of", "image_id_of", "legacy_map", "plan_import", "read_receipts", "receipt_source", "revision_of",
]
