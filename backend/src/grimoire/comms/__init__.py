"""Diegetic communication (Sprint 032, first slice): who a message in the fiction reaches.

Pure and deterministic: no model call, clock, file or UI. ``transmit`` turns one in-scene
message into 012 records (one proposition and one report per receiver) for 020's
``EvidenceLibrary.append``; ``read_message`` reads a 008 PC intent into a message, or refuses.
See docs/COMMUNICATION.md.
"""

from .model import (
    CHANNELS, MAX_RECEIVERS, MESSAGE_ID_MAX, Channel, Message, Presence, Transmission, TransmissionError,
)
from .reader import UNREAD_REASONS, Unread, read_message
from .transmit import evidence_id, transmit

__all__ = [
    "CHANNELS", "MAX_RECEIVERS", "MESSAGE_ID_MAX", "Channel", "Message", "Presence", "Transmission",
    "TransmissionError", "UNREAD_REASONS", "Unread", "evidence_id", "read_message", "transmit",
]
