"""Synthetic cards for Sprint 037 tests. Every card here is written for these tests: nothing is
downloaded from a card-sharing site and no text is a real character."""

from __future__ import annotations

import copy
import json

from grimoire.cardstore import CardImporter
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import LibraryStore, RecordEnvelope, SourceManifest

from chat_fixtures import Clock
from store_helpers import OWNER, owner_handle

WORKER = "card-import"
GUILD, CITY = "world:harbor", "world:harbor"

V3 = {
    "spec": "chara_card_v3",
    "spec_version": "3.0",
    "data": {
        "name": "Mara Vell",
        "description": "A smuggler of the Silver Guild who works the docks of Port Vell.",
        "personality": "Wry, patient, loyal to her crew.",
        "scenario": "Rain on the docks; the harbor master is watching.",
        "first_mes": "You came back. I didn't think you would.",
        "alternate_greetings": ["The tide is late tonight."],
        "group_only_greetings": ["Everyone, sit down and keep your hands visible."],
        "mes_example": "<START>\n{{char}}: Keep your voice down.",
        "system_prompt": "Write in the third person.",
        "post_history_instructions": "Stay in character.",
        "creator_notes": "A synthetic test card.",
        "creator": "grimoire tests",
        "character_version": "1",
        "tags": ["synthetic", "harbor"],
        "extensions": {"x-test": {"kept": True}},
        "character_book": {
            "name": "Harbor lore",
            "extensions": {},
            "entries": [
                {"keys": ["lighthouse"], "content": "The lighthouse has been dark for a year.", "enabled": True,
                 "insertion_order": 10, "use_regex": False, "extensions": {"x-depth": 4}},
                {"keys": ["guild", "Silver Guild"], "content": "The guild meets above the fish market.",
                 "enabled": False, "insertion_order": 20, "use_regex": False, "extensions": {}},
            ],
        },
        "assets": [{"type": "icon", "uri": "ccdefault:", "name": "main", "ext": "png"},
                   {"type": "background", "uri": "https://example.invalid/bg.png", "name": "bg", "ext": "png"}],
    },
}

SCENARIO = {
    "format": "grimoire.scenario-card/1",
    "title": "Storm over Port Vell",
    "premise": "A storm traps everyone in the harbor inn for the night.",
    "openings": ["Thunder rolls as the inn door bangs open.", "The lamps gutter; someone screams upstairs."],
    "instructions": "Keep the tone tense.",
    "setting": "Port Vell, a rain-soaked harbor town.",
    "cast": [{"ref": "world:harbor/character/innkeeper"}, {"ref": "world:harbor/character/nobody"},
             {"name": "Captain Osk"}],
    "tags": ["synthetic"],
    "lorebook": {"name": "Storm lore", "entries": [{"keys": ["storm"], "content": "Storms here last three days.",
                                                     "enabled": True, "insertion_order": 0, "use_regex": False,
                                                     "extensions": {}}]},
}

LOREBOOK = {
    "spec": "lorebook_v3",
    "data": {"name": "Port Vell gazetteer", "extensions": {}, "entries": [
        {"keys": ["market"], "content": "The fish market opens at dawn.", "enabled": True, "insertion_order": 0,
         "use_regex": False, "extensions": {}},
    ]},
}

PORTRAIT = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic portrait"
PORTRAIT_2 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR synthetic portrait two"


def card(base: dict = V3, **data_changes: object) -> dict:
    found = copy.deepcopy(base)
    target = found["data"] if "data" in found else found
    for key, value in data_changes.items():
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value
    return found


def encode(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")


def seed_world(store: LibraryStore) -> None:
    """A harbor world with a guild, a city and an innkeeper: what a card's names may suggest."""
    txn = store.transaction()
    txn.put(SourceManifest(GUILD, OWNER, "Harbor"), None)
    txn.put(RecordEnvelope(_ref("group", "silver-guild"), {}, name="Silver Guild"), None)
    txn.put(RecordEnvelope(_ref("location", "port-vell"), {}, name="Port Vell"), None)
    txn.put(RecordEnvelope(_ref("character", "innkeeper"), {}, name="Innkeeper"), None)
    txn.put(SourceManifest("catalog:other", OWNER, "Other"), None)
    txn.put(RecordEnvelope(_other("character", "mara"), {}, name="Mara Vell"), None)
    txn.commit(owner_handle())


def _ref(kind: str, entity: str):
    from grimoire.core.identity import SourceRef
    return SourceRef(GUILD, kind, entity, "r1")


def _other(kind: str, entity: str):
    from grimoire.core.identity import SourceRef
    return SourceRef("catalog:other", kind, entity, "r1")


class Gate:
    """An owner's authority over one untrusted card-import worker: grants, then exact-byte approvals."""

    def __init__(self, *scopes: str, permissions=frozenset({Permission.PROPOSE, Permission.APPLY})) -> None:
        self.authority = Authorizer([OWNER])
        self.count = 0
        for scope in scopes:
            self.grant(scope, permissions)
        self.handle = self.authority.handle_for(WORKER)

    def grant(self, scope: str, permissions=frozenset({Permission.PROPOSE, Permission.APPLY})) -> None:
        self.count += 1
        self.authority.add_grant(Grant(f"g{self.count}", WORKER, frozenset(permissions), ResourcePath.parse(scope),
                                       OWNER))

    def approve(self, preview: dict[str, bytes]) -> dict[str, str]:
        approvals = {}
        for path, data in sorted(preview.items()):
            self.count += 1
            approval_id = f"a{self.count}"
            self.authority.register_approval(Approval(approval_id, WORKER, Permission.APPLY, ResourcePath.parse(path),
                                                      payload_digest(data), OWNER))
            approvals[path] = approval_id
        return approvals


def importer(store: LibraryStore, handle=None) -> CardImporter:
    if handle is None:
        handle = Gate("sources", "assets").handle
    return CardImporter(store, handle, owner=OWNER, clock=Clock())


def land(store: LibraryStore, data: bytes, gate: Gate | None = None, **options):
    """Plan, approve every staged path against its exact bytes, and commit."""
    gate = gate or Gate("sources", "assets")
    planned = CardImporter(store, gate.handle, owner=OWNER, clock=Clock()).plan(data, **options)
    planned.commit(gate.approve(planned.preview()))
    return planned
