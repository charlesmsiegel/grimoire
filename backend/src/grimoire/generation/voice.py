"""The voice of an actor captured in an effective snapshot (022), derived portrayals included."""

from __future__ import annotations

from grimoire.core.identity import InstanceRef
from grimoire.core.portrayal import derivation_of
from grimoire.core.snapshot import EffectiveSnapshot
from grimoire.voice import (
    InvalidVoice, MissingVoice, ResolvedVoice, VoiceBinding, VoiceCatalog, VoiceMismatch, VoiceProfile,
    merge_binding,
)

#: The section name whose clearing, in a derivation layer, stops voice inheritance above it.
VOICE_SECTION = "voice-evidence"


def _inherited_profile(leaf, snapshot: EffectiveSnapshot, catalog: VoiceCatalog) -> VoiceProfile:
    """The leaf's own profile, else the nearest ancestor's in its pinned derivation chain.

    The walk follows only the chain 022 composed (``derived_variant`` links the sources declared,
    pinned bases): never a name match, a namesake or an alternate portrayal. A leaf that authors a
    profile does not inherit; a layer that clears ``voice-evidence`` stops the walk above it.
    """
    for node in snapshot.effective(leaf).chain:  # leaf first
        found = catalog.get(node.ref)
        if found is not None:
            return found
        derivation = derivation_of(snapshot.definition(node.ref))
        if derivation is None:
            break  # a complete definition: the chain ends here
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
