"""A synthetic Saltmarch cast for the Sprint 031 context-packing tests. No user content.

Six NPCs share one chat with a lorebook. The duke, the antagonist, is bound but absent: his card has
a secret section and he alone holds a private report. A world source holds a bestiary lorebook that
no chat binds. The lorebook format (``test.lorebook/0``) is owned by these tests: 031 defines no
lorebook schema, and this reader, unlike 037's card readers, lets an entry be the director's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grimoire.context import LoreEntry
from grimoire.core import DIRECTOR, DefinitionIndex, SourceDescriptor, SourceRef
from grimoire.core.entity import EntityDefinition, EntityInstance
from grimoire.core.facets import Agent, Communicative, FacetSet, Physical
from grimoire.core.identity import InstanceRef
from grimoire.core.knowledge import Belief, Event, Knowledge, Moment, Perception, Proposition, Report, Ruling, Subject
from grimoire.core.snapshot import EffectiveSnapshot, SelectedSource, SourceSelection
from grimoire.graph import ClaimShape

CAST, WORLD = "catalog:saltmarch-cast", "world:saltmarch"
LORE_SCHEMA = "test.lorebook/0"
NPCS = ("mara", "anya", "orrin", "tavi", "ines", "wren")
EVERYONE = (*NPCS, "duke")
DUKE = SourceRef(CAST, "character", "duke", "r1")
LORE = SourceRef(CAST, "lorebook", "saltmarch-lore", "r1")
BESTIARY = SourceRef(WORLD, "lorebook", "bestiary", "r1")
SCENARIO = SourceRef(CAST, "scenario", "flood-night", "r1")
SECRET = "SECRET-TOKEN the duke means to scuttle the ferry"
PLOT = "PLOT-TOKEN the ferry sinks at the third bell"
VOICE = Communicative(speaks={"speech"}, understands={"speech"})


def card(entity: str) -> SourceRef:
    return SourceRef(CAST, "character", entity, "r1")


def sections(entity: str) -> dict[str, str]:
    found = {"occupation": f"{entity.title()} works the Saltmarch quays.",
             "voice-evidence": f"{entity.title()} speaks in short sentences."}
    if entity == "duke":
        found["secret"] = SECRET
    return found


def definition(entity: str) -> EntityDefinition:
    return EntityDefinition(card(entity), entity.title(), "individual", FacetSet([Physical(), Agent(), VOICE]),
                            f"A Saltmarch local called {entity.title()}.", {"sections": sections(entity)})


def entry(entry_id: str, text: str, *keys: str, constant=False, audience="common", order=0) -> dict:
    return {"id": entry_id, "text": text, "keys": list(keys), "constant": constant, "audience": audience,
            "order": order}


LORE_ENTRIES = [
    entry("ferry", "The ferry crosses the marsh at dusk.", "ferry"),
    entry("guild", "The Ashen Guild runs the salt pans.", "ashen guild", "salt pans", order=1),
    entry("lighthouse", "The lighthouse has been dark for a year.", "lighthouse"),
    entry("tides", "Tides here turn twice a day.", constant=True),
    entry("duke-plan", "DIRECTOR-TOKEN the duke owes the guild a fortune.", "duke", audience="director"),
    entry("unclassified", "UNCLASSIFIED-TOKEN the marsh hides a shrine.", "marsh", audience=None),
]
BESTIARY_ENTRIES = [entry(f"beast-{n}", f"BESTIARY-TOKEN creature {n} of the far continent.", "wyrm", "ferry")
                    for n in range(40)]
SCENARIO_ENTRIES = [entry("opening", "SCENARIO-TOKEN the flood comes tonight.", constant=True, audience="director")]


def read_lore(payload) -> list[LoreEntry]:
    """The test-owned reader for ``test.lorebook/0`` (a missing audience stays the fail-closed default)."""
    found = []
    for raw in payload["entries"]:
        extra = {} if raw.get("audience") is None else {"audience": raw["audience"]}
        found.append(LoreEntry(raw["id"], raw["text"], tuple(raw.get("keys", ())), raw.get("constant", False),
                               order=raw.get("order", 0), **extra))
    return found


READERS = {LORE_SCHEMA: read_lore}


@dataclass(frozen=True)
class LoreRecord:
    """A non-entity definition for the pure tests: a ref, a display name, its schema and payload."""

    ref: SourceRef
    name: str
    schema: str
    payload: dict = field(default_factory=dict, hash=False, compare=False)


def unpack(definition):
    return (definition.schema, definition.payload) if isinstance(definition, LoreRecord) else None


def index() -> DefinitionIndex:
    definitions = [definition(n) for n in (*NPCS, "duke")]
    definitions += [LoreRecord(LORE, "Saltmarch lore", LORE_SCHEMA, {"entries": LORE_ENTRIES}),
                    LoreRecord(BESTIARY, "Bestiary", LORE_SCHEMA, {"entries": BESTIARY_ENTRIES}),
                    LoreRecord(SCENARIO, "Flood night", LORE_SCHEMA, {"entries": SCENARIO_ENTRIES})]
    return DefinitionIndex([SourceDescriptor(CAST, owner="human:owner"), SourceDescriptor(WORLD, owner="human:owner")],
                           definitions)


_default_index = index  # ``snapshot`` takes an ``index`` argument of its own


# -- the campaign ledger ------------------------------------------------------------------------------

C = "c1"


def actor(entity: str) -> InstanceRef:
    return InstanceRef(C, "character", f"{entity}-1")


def instance(entity: str) -> EntityInstance:
    return EntityInstance(actor(entity), card(entity))


def selection(*extra: SelectedSource, cast=EVERYONE, world=None, lore=True) -> SourceSelection:
    """Every cast card bound to its actor, plus the lorebook and the scenario unless ``lore`` is off."""
    sources = [SelectedSource(card(n), "character", actor=actor(n)) for n in cast]
    if lore:
        sources += [SelectedSource(LORE, "lorebook"), SelectedSource(SCENARIO, "scenario")]
    return SourceSelection(world, (*sources, *extra))


def snapshot(*extra: SelectedSource, index=None, cast=EVERYONE, world=None, lore=True) -> EffectiveSnapshot:
    """A frozen view of the chat's selection, with each cast member's instance captured."""
    return EffectiveSnapshot(index or _default_index(), selection(*extra, cast=cast, world=world, lore=lore),
                             instances=[(instance(n), None) for n in cast])


def claim_shapes(campaign: str = C, actor_of=actor) -> list[ClaimShape]:
    """017's typed reading of p5: Anya and Tavi are kin (symmetric)."""
    return [ClaimShape(campaign, "p5", "kin_of", actor_of("anya"), actor_of("tavi"))]


def knowledge(campaign: str = C, actor_of=actor) -> Knowledge:
    """The duke's private plot; Orrin passed Mara a refuted rumor about the duke; Anya saw the ferry.

    Mara also heard, early, that Anya and Tavi are cousins (p5, the one typed claim).
    """
    duke, mara, anya, orrin = (actor_of(n) for n in ("duke", "mara", "anya", "orrin"))
    m = Moment
    return Knowledge(
        campaign,
        events=[Event(campaign, "e1", "the ferry leaves late", m(1), "director")],
        propositions=[
            Proposition(campaign, "p1", PLOT, m(1), "director", about=(Subject("instance", str(duke)),)),
            Proposition(campaign, "p2", "the duke paid off the harbor watch", m(2), "director",
                        about=(Subject("instance", str(duke)),)),
            Proposition(campaign, "p3", "Anya keeps the old charts", m(2), "director",
                        about=(Subject("instance", str(anya)),)),
            Proposition(campaign, "p4", "UNCONFIRMED-TOKEN the bell is cracked", m(2), "director"),
            Proposition(campaign, "p5", "Anya and Tavi are cousins", m(1), "director",
                        about=(Subject("instance", str(anya)), Subject("instance", str(actor_of("tavi"))))),
        ],
        rulings=[Ruling(campaign, "r1", "p2", "refuted", m(3), "director", grounds=(Subject("event", "e1"),))],
        evidence=[
            Report(campaign, "v1", duke, "p1", "told", m(1), "director", confirmed=True),
            Report(campaign, "v2", mara, "p2", "told", m(2), "director", confirmed=True, sender=orrin,
                   attributed_to=duke, addressed=True, sent_at=m(2)),
            Report(campaign, "v3", mara, "p3", "told", m(3), "director", confirmed=True),
            Perception(campaign, "v4", anya, "e1", "witnessed", "the ferry pulling out after dark", m(1), "director",
                       confirmed=True),
            Report(campaign, "v5", orrin, "p4", "overheard", m(2), "director", confirmed=False),
            Report(campaign, "v6", mara, "p5", "told", m(1), "director", confirmed=True),
        ],
        beliefs=[Belief(campaign, "b1", mara, "p2", "suspected", m(3), DIRECTOR, ("v2",))],
    )
