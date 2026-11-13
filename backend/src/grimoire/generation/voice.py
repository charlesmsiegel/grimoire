"""The voice of an actor captured in an effective snapshot (022), derived portrayals included."""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.core.portrayal import PortrayalError, authored_sections, derivation_of
from grimoire.core.snapshot import EffectiveSnapshot
from grimoire.voice import (
    InvalidVoice, MissingVoice, ResolvedVoice, VoiceBinding, VoiceCatalog, VoiceMismatch, VoiceProfile,
    merge_binding,
)

from .imported import NO_VOICE_TEXT, DerivedVoice, VoiceReader, VoiceTexts, derive_profile, derive_voice

#: The section name whose clearing, in a derivation layer, stops voice inheritance above it.
VOICE_SECTION = "voice-evidence"


def _inherited_profile(leaf, snapshot: EffectiveSnapshot, catalog: VoiceCatalog) -> VoiceProfile:
    """The leaf's own profile, else the nearest ancestor's in its pinned derivation chain.

    The walk follows only the chain 022 composed (``derived_variant`` links the sources declared,
    pinned bases): never a name match, a namesake or an alternate portrayal. A leaf that authors a
    profile does not inherit. A derived layer with no profile that clears or replaces its base's
    ``voice-evidence`` section stops the walk: the base's voice no longer describes it.
    """
    for node in snapshot.effective(leaf).chain:  # leaf first
        found = catalog.get(node.ref)
        if found is not None:
            return found
        if isinstance(catalog, SnapshotCatalog) and node.ref in catalog.silent:
            raise _no_text(node.ref)  # imported with no text: a base's voice would speak for text it lacks
        layer = snapshot.definition(node.ref)
        derivation = derivation_of(layer)
        if derivation is None:
            break  # a complete definition: the chain ends here
        if VOICE_SECTION in dict(authored_sections(layer)):
            # It replaces its base's voice evidence but has no profile: inheriting would contradict it.
            raise MissingVoice("no_profile", f"{node.ref} authors voice evidence but no voice profile")
        if VOICE_SECTION in derivation.clear_sections:
            raise MissingVoice("no_profile", f"{node.ref} clears the voice it would inherit and authors none")
    raise MissingVoice("no_profile", f"no voice is authored for {leaf} or its pinned bases")


def resolve_snapshot_voice(ref: InstanceRef, snapshot: EffectiveSnapshot, catalog: VoiceCatalog,
                           binding: VoiceBinding | None = None) -> ResolvedVoice:
    """The voice the snapshot's actor ``ref`` speaks with, or a typed refusal.

    ``resolve_voice`` stays the path for an index actor; this is the path for a snapshot actor, whose
    selected revision may be a derived portrayal with no profile of its own. The campaign binding's
    replace/add/suppress applies last. A binding's alternate ``portrayal`` is refused here
    (``alternate_portrayal``): selecting another portrayal is a snapshot selection, not a voice pin.
    """
    if not isinstance(ref, InstanceRef):
        raise InvalidVoice("bad_actor", "resolve_snapshot_voice needs an InstanceRef")
    if not isinstance(snapshot, EffectiveSnapshot):
        raise InvalidVoice("bad_snapshot", "resolve_snapshot_voice needs an EffectiveSnapshot")
    if not isinstance(catalog, VoiceCatalog):
        raise InvalidVoice("bad_catalog", "resolve_snapshot_voice needs a VoiceCatalog")
    if binding is not None:
        if not isinstance(binding, VoiceBinding):
            raise InvalidVoice("bad_binding", "binding must be a VoiceBinding")
        if (binding.campaign_id, binding.actor_id) != (ref.campaign_id, ref.instance_id):
            raise VoiceMismatch("other_actor", f"binding is for another actor than {ref}")
        if binding.portrayal is not None:
            raise VoiceMismatch("alternate_portrayal",
                                "a snapshot actor's portrayal is its selection; a binding cannot switch it")
    entity = snapshot.entity(ref)  # selection, composition and missing-definition refusals propagate
    leaf = entity.resolved_source
    if leaf is None:
        if binding is not None and binding.suppress:
            raise InvalidVoice("stale_override", "a campaign-original actor has no source items")
        profile = None
        anchors, examples, provenance = merge_binding((), (), binding)
    else:
        profile = _inherited_profile(leaf, snapshot, catalog)
        anchors, examples, provenance = merge_binding(profile.anchors, profile.examples, binding)
    if not anchors and not examples:
        raise MissingVoice("no_profile", f"{ref} has no voice items")
    return ResolvedVoice(
        actor=ref,
        name=entity.name,
        actor_revision=leaf,
        portrayal=profile.portrayal if profile else None,
        anchors=anchors,
        examples=examples,
        provenance=provenance,
        imported=profile.imported if profile else None,
    )


class SnapshotCatalog(VoiceCatalog):
    """A ``VoiceCatalog`` that also holds the revisions imported with no text to speak from.

    ``silent`` stops ``resolve_snapshot_voice``'s walk with ``no_voice_text`` wherever the revision
    sits on the chain, as a cleared ``voice-evidence`` section does: a profile cannot be empty, so
    leaving the revision out alone would let the walk reach a base's voice and speak with text the
    revision no longer has.
    """

    __slots__ = ("silent",)

    def __init__(self, profiles: Iterable[VoiceProfile], silent: Iterable[SourceRef] = ()) -> None:
        super().__init__(profiles)
        self.silent = frozenset(silent)


class SnapshotVoices:
    """The voice catalog for playing one snapshot: authored profiles plus imported characters' own.

    For every revision on a selected actor's pinned chain (leaf and bases) with no authored profile,
    ``derive_voice`` builds one from that revision's own imported text (``readers`` first, then the 037
    card shape; ``grimoire.import_v1.imported_voices`` adds the V1 reader). An authored profile for a
    revision wins over the derived one. ``catalog`` is the ``SnapshotCatalog`` (a 014 ``VoiceCatalog``)
    that ``resolve_snapshot_voice`` takes; ``resolve`` is the same call over it.

    A derived portrayal inherits a base's *derived* voice only while no layer between them clears or
    replaces the personality or the description it was derived from. Otherwise the leaf gets its own
    profile, derived from its effective personality and description and the base's example messages,
    so text a layer removed never comes back as an anchor. A hand-authored base voice is inherited as
    027 always did. A revision imported with no usable text is kept in ``gaps`` and is silent in
    ``catalog``: resolving it, or any portrayal derived from it, refuses with ``no_voice_text`` and
    never falls back to a base's voice.
    """

    __slots__ = ("catalog", "derived", "gaps", "_snapshot")

    def __init__(self, snapshot: EffectiveSnapshot, authored: VoiceCatalog | None = None,
                 readers: Iterable[VoiceReader] = ()) -> None:
        if not isinstance(snapshot, EffectiveSnapshot):
            raise InvalidVoice("bad_snapshot", "SnapshotVoices needs an EffectiveSnapshot")
        authored = VoiceCatalog(()) if authored is None else authored
        if not isinstance(authored, VoiceCatalog):
            raise InvalidVoice("bad_catalog", "authored voices must be a VoiceCatalog")
        readers = tuple(readers)
        chains = [_chain_refs(snapshot, source.actor) for source in snapshot.selection.sources if source.actor]
        derived: dict[SourceRef, DerivedVoice] = {}
        for chain in chains:
            for ref in chain:
                if ref in derived or authored.get(ref) is not None:
                    continue
                try:
                    found = derive_voice(snapshot.definition(ref), readers)
                except (PortrayalError, ValueError, LookupError):
                    continue  # unreadable here; resolving the actor reports why
                if found is not None:
                    derived[ref] = found
        for chain in chains:
            if chain and chain[0] not in derived and authored.get(chain[0]) is None:
                _rederive(snapshot, chain, authored, derived)
        self._snapshot = snapshot
        self.derived = MappingProxyType(derived)
        self.gaps = MappingProxyType({ref: found.reason for ref, found in derived.items() if found.profile is None})
        self.catalog = SnapshotCatalog([*authored, *(found.profile for found in derived.values() if found.profile)],
                                       silent=self.gaps)

    def resolve(self, ref: InstanceRef, binding: VoiceBinding | None = None) -> ResolvedVoice:
        """``resolve_snapshot_voice`` over this catalog; an imported revision with no text is ``no_voice_text``."""
        return resolve_snapshot_voice(ref, self._snapshot, self.catalog, binding)


def _no_text(ref: SourceRef) -> MissingVoice:
    return MissingVoice(NO_VOICE_TEXT, f"{ref} has no text to speak from: no example lines, "
                                       "personality or description")


def _rederive(snapshot: EffectiveSnapshot, chain: tuple[SourceRef, ...], authored: VoiceCatalog,
              derived: dict[SourceRef, DerivedVoice]) -> None:
    """Give the leaf its own derived voice when a layer above a derived base voice changed its sources.

    The walk mirrors ``_inherited_profile``: it stops at the first revision with a voice, and at a layer
    that authors or clears ``voice-evidence`` (no inheritance at all). Only a base voice that was
    *derived* is re-derived; an authored one describes the base as its author wrote it.
    """
    touched: set[str] = set()
    try:
        for node in chain:
            if authored.get(node) is not None:
                return
            if node in derived:
                break
            layer = snapshot.definition(node)
            derivation = derivation_of(layer)
            if derivation is None:
                return
            sections = dict(authored_sections(layer))
            if VOICE_SECTION in sections or VOICE_SECTION in derivation.clear_sections:
                return
            if "personality" in sections or "personality" in derivation.clear_sections:
                touched.add("personality")
            if layer.description or derivation.clear_description:
                touched.add("description")
        else:
            return
        base = derived[node].texts
        if not touched or base is None:
            return
        effective = snapshot.effective(chain[0])
    except (PortrayalError, ValueError, LookupError):
        return  # resolving the actor reports why
    texts = VoiceTexts(
        examples=base.examples,
        personality=(effective.section("personality") or "") if "personality" in touched else base.personality,
        description=effective.description if "description" in touched else base.description,
        speaker=base.speaker)  # the base's examples still label the base's turns with its name
    derived[chain[0]] = derive_profile(chain[0], effective.name, texts)


def _chain_refs(snapshot: EffectiveSnapshot, actor: InstanceRef) -> tuple[SourceRef, ...]:
    """The pinned revisions an actor's voice may come from, leaf first; none when it cannot resolve."""
    try:
        leaf = snapshot.entity(actor).resolved_source
        return () if leaf is None else tuple(node.ref for node in snapshot.effective(leaf).chain)
    except (PortrayalError, ValueError, LookupError):
        return ()
