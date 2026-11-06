"""World Change Sets (043): reviewable multi-record edits to one world source.

Pure values, a strict JSON codec and readable diffs. Reviewing and applying a change set
against the library is ``grimoire.changesetstore``'s (an adapter). See docs/CHANGESETS.md.
"""

from .codec import SCHEMA, decode, encode
from .diff import unified
from .model import (
    MAX_CHANGES, MAX_PRECONDITIONS, ChangeSet, ChangeSetError, Precondition, RecordChange, summarize,
)

__all__ = [
    "MAX_CHANGES", "MAX_PRECONDITIONS", "SCHEMA", "ChangeSet", "ChangeSetError", "Precondition", "RecordChange",
    "decode", "encode", "summarize", "unified",
]
