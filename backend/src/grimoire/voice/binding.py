"""Which voice a campaign actor speaks with: one pin mechanism, explicit links, local edits.

The actor's own ``source`` pins or follows a revision; a binding never pins the
same entity a second way. A binding may select an *alternate portrayal* only
through a direct ``alternate_portrayal`` link that its endpoints' own sources
declared, and may add, replace or suppress items without editing the source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.index import DefinitionIndex, MissingDefinition
from grimoire.core.model import CampaignActor
from grimoire.evals.text import has_words

from .errors import InvalidVoice, MissingVoice, VoiceMismatch
from .profile import (
    ImportedVoiceSource, VoiceAnchor, VoiceCatalog, VoiceExample, as_tuple, check_id, validate_items,
)


@dataclass(frozen=True, slots=True)
class VoiceBinding:
    """Campaign-owned voice choices for one actor; never an edit of the source profile."""

    campaign_id: str
    actor_id: str
    portrayal: SourceRef | None = None
    anchors: tuple[VoiceAnchor, ...] = ()
    examples: tuple[VoiceExample, ...] = ()
    suppress: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_id(self.campaign_id, "campaign_id")
        check_id(self.actor_id, "actor_id")
        if self.portrayal is not None and (
                not isinstance(self.portrayal, SourceRef) or self.portrayal.kind != "character"):
            raise InvalidVoice("bad_portrayal", "a bound portrayal must be a character reference")
        anchors, examples = validate_items(self.anchors, self.examples, "binding")
        suppress = as_tuple(self.suppress, "bad_suppress", "suppress must be a sequence of item ids")
        for item_id in suppress:
            check_id(item_id, "suppressed item id")
        if len(set(suppress)) != len(suppress):
            raise InvalidVoice("duplicate_id", "binding suppresses an item twice")
        object.__setattr__(self, "anchors", anchors)
        object.__setattr__(self, "examples", examples)
        object.__setattr__(self, "suppress", suppress)


@dataclass(frozen=True, slots=True)
class ResolvedVoice:
    """The voice one actor speaks with now, and exactly where each part came from."""

    actor: InstanceRef
    name: str
    actor_revision: SourceRef | None  # the actor's own definition revision
    portrayal: SourceRef | None  # the profile revision used; differs for an alternate portrayal
    anchors: tuple[VoiceAnchor, ...]
    examples: tuple[VoiceExample, ...]
    provenance: Mapping[str, str]  # item id -> "source" | "campaign"
    imported: ImportedVoiceSource | None = None

    __hash__ = None  # type: ignore[assignment]  # provenance is a read-only mapping

    def __post_init__(self) -> None:
        if not isinstance(self.actor, InstanceRef):
            raise InvalidVoice("bad_actor", "a resolved voice belongs to an InstanceRef")
        if not isinstance(self.name, str) or not has_words(self.name):
            raise InvalidVoice("bad_name", f"{self.actor} needs a nonblank name")
        for revision in (self.actor_revision, self.portrayal):
            if revision is not None and not (isinstance(revision, SourceRef) and revision.pinned):
                raise InvalidVoice("unpinned_portrayal", f"{self.actor} revisions must be pinned refs")
        anchors, examples = validate_items(self.anchors, self.examples, "resolved voice")
        if not isinstance(self.provenance, Mapping):
            raise InvalidVoice("bad_provenance", "provenance must map item ids to their origin")
        provenance = dict(self.provenance)
        if (set(provenance) != {item.item_id for item in (*anchors, *examples)}
                or not set(provenance.values()) <= {"source", "campaign"}):
            raise InvalidVoice("bad_provenance", f"{self.actor} provenance does not match its items")
        if self.imported is not None and not isinstance(self.imported, ImportedVoiceSource):
            raise InvalidVoice("bad_imported", "imported must be an ImportedVoiceSource")
        object.__setattr__(self, "anchors", anchors)
        object.__setattr__(self, "examples", examples)
        object.__setattr__(self, "provenance", MappingProxyType(provenance))


def _missing(error: MissingDefinition) -> MissingVoice:
    return MissingVoice("missing_definition", f"no definition for {error.ref}", detail=error.reason)


def _qualifying_link(definitions: DefinitionIndex, actor_source: SourceRef,
                     actor_revision: SourceRef, wanted: SourceRef) -> bool:
    """A direct alternate_portrayal link, pinned endpoints honoured, declared by an endpoint's source."""
    for link in definitions.links_for(actor_source):
        if link.relation != "alternate_portrayal":
            continue
        ends = {link.subject.entity_key: link.subject, link.object.entity_key: link.object}
        actor_end = ends.get(actor_revision.entity_key)
        wanted_end = ends.get(wanted.entity_key)
        if actor_end is None or wanted_end is None:
            continue
        if actor_end.pinned and actor_end != actor_revision:
            continue
        if wanted_end.pinned and wanted_end != wanted:
            continue
        if link.declared_by in (actor_source.source, wanted.source):
            return True
    return False


def _merge(source_anchors: tuple[VoiceAnchor, ...], source_examples: tuple[VoiceExample, ...],
           binding: VoiceBinding | None):
    if binding is None:
        provenance = {item.item_id: "source" for item in (*source_anchors, *source_examples)}
        return source_anchors, source_examples, provenance
    source_ids = {item.item_id for item in (*source_anchors, *source_examples)}
    local_ids = {item.item_id for item in (*binding.anchors, *binding.examples)}
    if ({a.item_id for a in binding.anchors} & {e.item_id for e in source_examples}
            or {e.item_id for e in binding.examples} & {a.item_id for a in source_anchors}):
        raise InvalidVoice("kind_mismatch", "a local item can replace only a source item of its own kind")
    for item_id in binding.suppress:
        if item_id not in source_ids:
            raise InvalidVoice("stale_override", "binding suppresses an item the source does not have")
        if item_id in local_ids:
            raise InvalidVoice("suppress_replaced", "binding both replaces and suppresses an item")
    provenance: dict[str, str] = {}

    def merged(source_items, local_items):
        local = {item.item_id: item for item in local_items}
        result = []
        for item in source_items:
            if item.item_id in binding.suppress:
                continue
            replacement = local.pop(item.item_id, None)
            result.append(replacement or item)
            provenance[item.item_id] = "campaign" if replacement else "source"
        for item in local.values():
            result.append(item)
            provenance[item.item_id] = "campaign"
        return tuple(result)

    anchors = merged(source_anchors, binding.anchors)
    examples = merged(source_examples, binding.examples)
    validate_items(anchors, examples, "resolved voice")
    return anchors, examples, provenance


def resolve_voice(actor: CampaignActor, catalog: VoiceCatalog, definitions: DefinitionIndex,
                  binding: VoiceBinding | None = None) -> ResolvedVoice:
    """The voice ``actor`` speaks with, or a typed refusal; never a namesake or another revision."""
    if not isinstance(actor, CampaignActor):
        raise InvalidVoice("bad_actor", "resolve_voice needs a CampaignActor")
    if not isinstance(catalog, VoiceCatalog):
        raise InvalidVoice("bad_catalog", "resolve_voice needs a VoiceCatalog")
    if binding is not None and not isinstance(binding, VoiceBinding):
        raise InvalidVoice("bad_binding", "binding must be a VoiceBinding")
    if binding is not None and (binding.campaign_id, binding.actor_id) != (actor.campaign_id, actor.actor_id):
        raise VoiceMismatch("other_actor", f"binding is for another actor than {actor.ref}")

    if actor.source is None:
        if binding is not None and binding.portrayal is not None:
            raise VoiceMismatch("campaign_original",
                                "a campaign-original actor has no definition to link a portrayal to")
        if binding is not None and binding.suppress:
            raise InvalidVoice("stale_override", "a campaign-original actor has no source items")
        actor_def = profile = None
        source_anchors, source_examples = (), ()
    else:
        try:
            actor_def = definitions.resolve(actor.source)
        except MissingDefinition as error:
            raise _missing(error) from None
        target = actor_def.ref
        wanted = binding.portrayal if binding is not None else None
        if wanted is not None:
            if wanted.entity_key == actor.source.entity_key:
                raise VoiceMismatch("conflicting_pin",
                                    "pin the actor's own portrayal through its source, not a binding")
            if not _qualifying_link(definitions, actor.source, actor_def.ref, wanted):
                raise VoiceMismatch("unlinked_portrayal",
                                    f"{wanted} is not a declared alternate portrayal of {actor.ref}")
            try:
                target = definitions.resolve(wanted).ref
            except MissingDefinition as error:
                raise _missing(error) from None
        profile = catalog.get(target)
        if profile is None:
            raise MissingVoice("no_profile", f"no voice is authored for {target}")
        source_anchors, source_examples = profile.anchors, profile.examples

    anchors, examples, provenance = _merge(source_anchors, source_examples, binding)
    if not anchors and not examples:
        raise MissingVoice("no_profile", f"{actor.ref} has no voice items")
    return ResolvedVoice(
        actor=actor.ref,
        name=actor.local_name or actor_def.name,
        actor_revision=actor_def.ref if actor_def else None,
        portrayal=profile.portrayal if profile else None,
        anchors=anchors,
        examples=examples,
        provenance=provenance,
        imported=profile.imported if profile else None,
    )
