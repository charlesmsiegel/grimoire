"""Campaign archaeology: reading prior transcripts into stored posts without inventing anything (036).

Pure: the SillyTavern reader takes decoded lines; the store-backed importer is
``grimoire.archaeologystore``. See docs/ARCHAEOLOGY.md.
"""

from .sillytavern import (
    FORMAT, HEADER_KEYS, MAX_MESSAGES, MAX_SPEAKERS, READ_FIELDS, Loss, StChat, StError, StSpeaker, read_st_chat,
    speaker_id,
)

__all__ = [
    "FORMAT", "HEADER_KEYS", "MAX_MESSAGES", "MAX_SPEAKERS", "READ_FIELDS", "Loss", "StChat", "StError",
    "StSpeaker", "read_st_chat", "speaker_id",
]
