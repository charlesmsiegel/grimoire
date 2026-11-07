"""Synthetic tables for the 035 scene runner. No real library content, no paid calls.

Two libraries, each built through the real importers:

- **V1:** 026's two-NPC Drowned Ledger import. Winifred is the persona (``human:ana`` plays her); Mara
  speaks with an authored voice; Seraphine speaks with the voice derived from her imported card.
- **Cards:** 037's Mara Vell card (with its lorebook) launched as a world-less chat, an Anya Rook card
  as the persona, and a scenario-only chat over the Storm over Port Vell scenario.

The library's durable authority is opened last, so every later write needs a handle it issued: the
owner is granted the synced scopes. Every model call is a scripted fake.
"""

from __future__ import annotations

import json
from pathlib import Path

from card_fixtures import SCENARIO, V3, card, encode, land
from chat_fixtures import Clock
from generation_fixtures import Harness, say
from import_fixtures import ARCHIVE, CAMPAIGN_ID, MARA_DEFAULT_REF, Env
from ledger_helpers import fixed_clock
from store_helpers import OWNER, owner_handle
from v1_fixture import CAMPAIGN, sha, write

from grimoire.adjudicationstore import AdjudicationLibrary
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import ChatRef, InstanceRef, SourceRef
from grimoire.core.inference import InferencePolicy
from grimoire.import_v1 import chat_id_of
from grimoire.ledgers import DurableAuthority
from grimoire.play import SceneRunner
from grimoire.providers import RouteChoice
from grimoire.runtime import LabelRuntime
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore
from grimoire.voice import VoiceAnchor, VoiceCatalog, VoiceExample, VoiceProfile

ANA, BOB = "human:ana", "human:bob"
SCOPES = ("sources", "campaigns", "assets", "kept")
POLICY = InferencePolicy({"director_generation": 1}, max_attempts_per_call=1)
LABELLER = "app:labeller"
ADJUDICATOR = "app:adjudicator"
LABEL_POLICY = InferencePolicy({"director_generation": 1, "labelling": 1}, max_attempts_per_call=1)

# -- V1 ---------------------------------------------------------------------------------------------

V1_CHAT = ChatRef(CAMPAIGN_ID, chat_id_of(f"v1:{ARCHIVE}:{CAMPAIGN}/campaign.md", "00000000000000000000000000000001"))
WINIFRED, MARA, SERAPHINE = (InstanceRef(CAMPAIGN_ID, "character", name) for name in ("winifred", "mara", "seraphine"))
SERAPHINE_DIR = "worlds/saltmarch/characters/seraphine"
SERAPHINE_CARD = json.dumps({"spec": "chara_card_v3", "spec_version": "3.0", "data": {
    "name": "Seraphine", "description": "A lamplighter of Saltmarch. She speaks in a hush, as if the lamps listen.",
    "personality": "Patient and wry.", "scenario": "Night on the quay.", "first_mes": "The lamps are lit.",
    "mes_example": "<START>\n{{user}}: Who's there in the dark?\n{{char}}: Only the lamplighter, {{user}}. "
                   "Mind the wick.\n<START>\n{{char}}: The tide keeps its own hours, and so do I.",
    "system_prompt": "Narrate in the present tense.", "alternate_greetings": [], "tags": [],
    "extensions": {}}}, indent=2, sort_keys=True) + "\n"
SERAPHINE_EXAMPLE = "The tide keeps its own hours, and so do I."
MARA_EXAMPLE = "Ledgers drown. Debts float."
#: Mara's V1 card is blank, so her voice is authored for her imported revision.
MARA_VOICE = VoiceCatalog([VoiceProfile(MARA_DEFAULT_REF, (VoiceAnchor("clipped", "cadence", "Clipped, dry, counts everything."),),
                                        (VoiceExample("ledger", MARA_EXAMPLE),))])


def v1_library(root: Path) -> Path:
    """026's import with Seraphine's card voiced, closed: the library folder."""
    env = Env(root)
    write(env.v1, f"{SERAPHINE_DIR}/default.json", SERAPHINE_CARD)
    write(env.v1, f"{CAMPAIGN}/characters/seraphine/default.json", SERAPHINE_CARD)
    appearances = json.loads((env.v1 / CAMPAIGN / "appearances.json").read_text(encoding="utf-8"))
    appearances["characters/seraphine"]["base"] = sha(SERAPHINE_CARD)
    write(env.v1, f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    env.rescan()
    env.plan().apply()
    env.store.close()
    secure(root / "v2")
    return root / "v2"


# -- cards ------------------------------------------------------------------------------------------

MARA_VELL = SourceRef("card:mara-vell", "character", "mara-vell")
MARA_LORE = SourceRef("card:mara-vell", "lorebook", "mara-vell-lore")
ANYA = SourceRef("card:anya-rook", "character", "anya-rook")
STORM = SourceRef("card:storm-over-port-vell", "scenario", "storm-over-port-vell")
LIGHTHOUSE = "The lighthouse has been dark for a year."
VELL_EXAMPLE = "We sail when the lamps go out."
VOICED = card(V3, mes_example="<START>\n{{user}}: Is the cargo safe?\n{{char}}: Safe as the tide, {{user}}.\n"
                              "<START>\n{{char}}: " + VELL_EXAMPLE)
ANYA_CARD = card(V3, name="Anya Rook", description="A harbor pilot who knows every channel.", character_book=None,
                 first_mes="Steady now.", mes_example="<START>\n{{char}}: Mind the shoals.")
TAMSIN = SourceRef("card:tamsin-hale", "character", "tamsin-hale")
TAMSIN_CARD = card(V3, name="Tamsin Hale", description="A customs clerk who notices everything.", character_book=None,
                   first_mes="Papers, please.", mes_example="<START>\n{{char}}: Every crate has a story, and a stamp.")


class Tagged:
    """Deterministic ids that name their use (``q-card-1``), so no two builders draw the same one."""

    def __init__(self, tag: str) -> None:
        self.tag, self.count = tag, 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}-{self.tag}-{self.count}"


def card_library(root: Path) -> tuple[Path, ChatRef, ChatRef]:
    """Mara Vell's card chat (Anya as persona, the card's lorebook bound) and a scenario-only chat (Anya as
    persona), closed: the library folder and the two chats."""
    with LibraryStore.create(root / "cards") as store:
        for document in (VOICED, ANYA_CARD, TAMSIN_CARD, SCENARIO):
            land(store, encode(document))
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Tagged("c"), owner=OWNER)
        card_chat = chats.start_chat(Launch("character", MARA_VELL), attach=[(MARA_LORE, "lorebook")]).commit().chat
        _, etag = chats.read_chat(card_chat)
        chats.set_persona(card_chat, ANYA, expected=etag).commit()
        storm = chats.start_chat(Launch("scenario", STORM)).commit().chat
        _, etag = chats.read_chat(storm)
        chats.set_persona(storm, ANYA, expected=etag).commit()
    secure(root / "cards")
    return root / "cards", card_chat, storm


def attach_tamsin(store: LibraryStore, handle, chat: ChatRef) -> InstanceRef:
    """A second card attached mid-chat; its new actor."""
    chats = ChatLibrary(store, handle, clock=Clock(), new_id=Tagged("late"), owner=OWNER)
    _, etag = chats.read_chat(chat)
    change = chats.attach_source(chat, TAMSIN, "character", expected=etag)
    change.commit()
    record, _ = chats.read_chat(chat)
    (actor,) = [b.actor for b in record.active_bindings("character") if b.ref.unpinned() == TAMSIN]
    return actor


def secure(folder: Path) -> None:
    """Open the library's durable authority for the first time and grant the owner every synced scope: from
    then on every write needs a handle that authority issued."""
    with LibraryStore.open(folder) as store:
        authority = DurableAuthority.open(store, [OWNER], clock=fixed_clock())
        for scope in SCOPES:
            authority.add_grant(Grant(f"owner-{scope}", OWNER, frozenset({Permission.APPLY}),
                                      ResourcePath.parse(scope), OWNER))
        authority.close()


# -- an open table ----------------------------------------------------------------------------------

class Table:
    """One open library: its durable authority, the owner's and players' handles, and a runner."""

    def __init__(self, folder: Path, steps=(), *, voices: VoiceCatalog | None = None, runtime: bool = True,
                 policy: InferencePolicy = POLICY, secrets=(), fence=None, labels=None,
                 propose: bool = True, adjudicate: bool = False, **traced) -> None:
        self.store = LibraryStore.open(folder)
        self.authority = DurableAuthority.open(self.store, [OWNER], clock=fixed_clock())
        self.owner = self.authority.handle_for(OWNER, trusted=True)
        self.harness = Harness(list(steps))
        labeller = self.label_handle = self.labels = None
        if labels is not None:  # the labeller's own untrusted provider handle, and the library's label handle
            self.labels = Harness(list(labels), approve=True)
            labeller = LabelRuntime(self.labels.adapter, (RouteChoice("primary", "model-a"),), self.labels.handle,
                                    approvals=self.labels.approvals)
            if propose:
                self.authority.add_grant(Grant("labeller-campaigns", LABELLER, frozenset({Permission.PROPOSE}),
                                               ResourcePath.parse("campaigns"), OWNER))
            self.label_handle = self.authority.handle_for(LABELLER, trusted=False)
        self.adjudication = None
        if adjudicate:  # the app's own adjudication key: never the labeller's, never a player's
            self.adjudication = AdjudicationLibrary(self.store, self.authority,
                                                    self.authority.handle_for(ADJUDICATOR, trusted=True))
        self.runner = SceneRunner(self.store, self.authority, self.owner, owner=OWNER,
                                  runtime=self.harness.runtime if runtime else None, policy=policy, secrets=secrets,
                                  voices=voices, fence=fence, labeller=labeller, label_handle=self.label_handle,
                                  adjudication=self.adjudication,
                                  **traced)  # 048: ``tracer`` and ``clock``

    def player(self, principal: str = ANA):
        return self.authority.handle_for(principal, trusted=True)

    def close(self) -> None:
        self.authority.close()
        self.store.close()

    def __enter__(self) -> Table:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = [
    "ADJUDICATOR", "ANA", "ANYA", "BOB", "LABEL_POLICY", "LABELLER", "LIGHTHOUSE", "MARA", "MARA_EXAMPLE", "MARA_LORE",
    "MARA_VELL",
    "MARA_VOICE", "POLICY",
    "SERAPHINE", "SERAPHINE_EXAMPLE", "STORM", "TAMSIN", "Table", "V1_CHAT", "VELL_EXAMPLE", "WINIFRED",
    "attach_tamsin", "card_library", "say", "secure", "v1_library",
]
