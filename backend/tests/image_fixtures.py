"""Synthetic pictures and sources for Sprint 018 tests (no real library, no real artwork).

The "images" are tiny byte strings with valid magic headers: the store sniffs the format and
never decodes pixels, so nothing more is needed.
"""

from __future__ import annotations

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.security import Authorizer, Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, LinkSpec, RecordEnvelope, ScopeRecord, SourceManifest

from store_helpers import OWNER

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic red"
PNG_2 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic blue"
GIF = b"GIF89a" + b"synthetic gif"
JPEG = b"\xff\xd8\xff\xe0" + b"synthetic jpeg"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 synthetic"

HARBOR, CARD, OTHER = "world:harbor", "card:mira", "catalog:other"
SAILOR = SourceRef(HARBOR, "character", "wren-sailor")
CAPTAIN = SourceRef(HARBOR, "character", "wren-captain")
MIRA = SourceRef(CARD, "character", "mira")
STRANGER = SourceRef(OTHER, "character", "stranger")
WREN_A, WREN_B, WREN_C, WREN_D, WREN_E = (InstanceRef("c1", "character", f"wren-{x}") for x in "abcde")


def handle(*scopes: str):
    """An owner handle with APPLY on ``scopes`` (default: sources, campaigns and assets)."""
    authority = Authorizer([OWNER])
    for number, scope in enumerate(scopes or ("sources", "campaigns", "assets")):
        authority.add_grant(Grant(f"g{number}", OWNER, frozenset({Permission.APPLY}),
                                  ResourcePath.parse(scope), OWNER))
    return authority.handle_for(OWNER, trusted=True)


def source_puts():
    """Manifests and definitions for harbor (two alternate portrayals), a standalone card and a stranger."""
    return [
        (SourceManifest(HARBOR, OWNER, "Harbor", links=(LinkSpec(str(SAILOR.at("r1")), "alternate_portrayal",
                                                                 str(CAPTAIN.at("r1"))),)), None),
        (RecordEnvelope(SAILOR.at("r1"), {"bio": "a sailor"}, name="Wren"), None),
        (RecordEnvelope(CAPTAIN.at("r1"), {"bio": "a captain"}, name="Wren"), None),
        (SourceManifest(CARD, OWNER, "Mira card"), None),
        (RecordEnvelope(MIRA.at("r1"), {"bio": "a smuggler"}, name="Mira"), None),
        (SourceManifest(OTHER, OWNER, "Other"), None),
        (RecordEnvelope(STRANGER.at("r1"), {}, name="Stranger"), None),
    ]


def instance_puts():
    """Campaign c1: five instances derived from the sailor portrayal."""
    return [(RecordEnvelope(ref, {}, derived_from=str(SAILOR), base=str(SAILOR.at("r1"))), None)
            for ref in (WREN_A, WREN_B, WREN_C, WREN_D, WREN_E)]


def seed(store: LibraryStore, *, campaign: bool = True) -> None:
    txn = store.transaction()
    for envelope, expected in source_puts() + (instance_puts() if campaign else []):
        txn.put(envelope, expected)
    txn.commit(handle())


__all__ = ["CAPTAIN", "CARD", "GIF", "HARBOR", "JPEG", "MIRA", "OTHER", "PNG", "PNG_2", "SAILOR", "STRANGER",
           "ScopeRecord", "WEBP", "WREN_A", "WREN_B", "WREN_C", "WREN_D", "WREN_E", "handle", "instance_puts",
           "seed", "source_puts"]
