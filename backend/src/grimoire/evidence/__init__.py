"""Stored events and evidence (Sprint 020): a pure payload codec and read-time assembly.

``codec`` gives each 012 record one canonical JSON payload (a proposition with its 017 claim
shape, when it has one), plus the batch log that accounts
for every stored file. ``assemble`` turns what a scan found into a valid ``Knowledge`` ledger,
quarantining damage so that actors can only ever know less. No I/O and no model call: the
store-backed service is ``grimoire.evidencestore``.
"""

from .assemble import CHAIN_FAMILIES, Assembly, Quarantine, ScannedFile, assemble, build_ledger
from .codec import (
    BATCH_LIMIT, BATCH_SCHEMA, CLAIMED_PROPOSITION_SCHEMA, FAMILIES, LABEL_LIMIT, SCHEMAS, TEXT_LIMIT, Batch, BatchEntry, EvidenceCodecError,
    batch_entry, chain_key, decode, decode_batch, decode_claimed, encode, encode_batch, record_ref,
)

__all__ = [
    "BATCH_LIMIT", "BATCH_SCHEMA", "CHAIN_FAMILIES", "CLAIMED_PROPOSITION_SCHEMA", "FAMILIES", "LABEL_LIMIT", "SCHEMAS", "TEXT_LIMIT", "Assembly",
    "Batch", "BatchEntry", "build_ledger", "EvidenceCodecError", "Quarantine", "ScannedFile", "assemble", "batch_entry",
    "chain_key", "decode", "decode_batch", "decode_claimed", "encode", "encode_batch", "record_ref",
]
