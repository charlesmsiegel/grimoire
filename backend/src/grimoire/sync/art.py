"""What one 018 art file needs before sync may copy it (docs/SYNC.md, "Art").

Sync never creates a state the 018 batch would refuse. The batch's rules (W5-W9, docs/IMAGES.md)
are read here structurally, from the origin's scanned bytes, its scan's listed blob sizes (never a
blob's bytes) and the sources ``load()`` excludes on each device (``LibraryStore.excluded_sources``,
the store's shared, stat-cached answer). ``art_needs`` says what an art file needs on the receiver,
or why the origin itself holds it in a state the batch refuses; ``art_unmet`` says which of those
needs the receiver does not meet. Both ``plan_sync`` and ``resolve`` use them, so a conflict that a
sync holds can never be delivered by a resolve.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from grimoire.core.identity import ImageRef, InstanceRef, PlacementRef, SourceRef
from grimoire.images import (
    IMAGE_SCHEMA, PLACEMENT_SCHEMA, ImageModelError, image_from_payload, placement_from_payload, validate_placement,
)
from grimoire.store import StoreError, UnstorableIdentifier
from grimoire.store.jsonfmt import JsonError, parse_strict
from grimoire.store.layout import (
    classify, definition_parts, image_parts, instance_parts, manifest_parts, placement_parts,
)
from grimoire.store.replica import image_blob, instance_derivation_of, parse_exact, placement_target

from .documents import syncable


@dataclass(frozen=True)
class Needs:
    """What one 018 art file needs on the receiving device (the 018 batch's W5-W9, structurally)."""

    identical: frozenset[str] = frozenset()  # the receiver must hold exactly the origin's bytes
    present: frozenset[str] = frozenset()  # the receiver must hold some version
    campaign: str | None = None  # this campaign must have state (a scope or an instance) there
    hold: str | None = None  # the origin itself is in a state the batch refuses: never copied
    #: W5 on the receiver: these sources must be admitted there, not merely present. The ``present``
    #: manifest is the structural half of "the source exists"; a manifest that is present in a source
    #: ``load()`` excludes (a record claiming another source, a cross-source ``identity_conflict``)
    #: still fails W5 there, and 018 reads all of that source's art as ``excluded_source``.
    admitted: frozenset[str] = frozenset()


def art_needs(path: str, data: bytes | None, origin: Mapping[str, bytes], blobs: Mapping[str, int],
              excluded: frozenset[str] = frozenset()) -> Needs:
    """The files a blob, image object or placement needs (docs/SYNC.md, "Art"), read from the
    origin's bytes and its scan's blob sizes (``blobs``: listed, never read). Everything the resolver
    reads for it must be identical, so it resolves the same on both devices; art the 018 batch would
    refuse there (W5 a source the origin excludes, W6 an image it cannot show, W7 scope, unreadable
    payload, W8's ``validate_placement``, an adjust with nothing inherited) is held.

    ``excluded`` is the set of sources the origin's ``load()`` excludes. Art that belongs to, or
    shows a picture of, such a source is held on the origin's word, whatever the receiver admits:
    on the origin it resolves to nothing (``excluded_source``, ``missing_image``), so a receiver
    that admits the source would show what the origin does not; the source's own identity is what
    the origin's exclusion contests (a record claiming to be another source's, two sources claiming
    one identity), so the head revision and scope read below for it come from data ``load()``
    refused; and an image object is create-only and sync never propagates a deletion, so a copy
    could never be withdrawn if the origin's repair removes the art instead of the damage. The hold
    lifts by itself once the origin admits the source again, and the next sync copies the art.
    """
    kind = syncable(path)
    if kind == "blob" or data is None:
        return Needs()  # content-addressed: needs nothing (and a blob shows nothing until an image does)
    found = classify(tuple(path.split("/")))
    assert found is not None
    identical: set[str] = set()
    present: set[str] = set()
    sources: set[str] = set()

    def admitted(source: str) -> str | None:  # W5: the source must be admitted on both devices
        sources.add(source)
        if source in excluded:
            return f"{source} is excluded on the origin (load() leaves it out: 018's excluded_source)"
        return None

    def art(image: ImageRef) -> str | None:  # W6: the image object and its blob, as the origin holds them
        if image.source is not None:  # already checked by every caller today; kept so art() alone is W5-safe
            excluded_why = admitted(image.source)
            if excluded_why is not None:
                return f"{image} cannot be shown: {excluded_why}"
        try:
            image_path = "/".join(image_parts(image))
        except (ValueError, UnstorableIdentifier):
            return f"{image} cannot be stored"
        image_data = origin.get(image_path)
        blob = None if image_data is None else image_blob(image_data)
        if blob is None:
            return f"{image} or its blob is not readable on the origin"
        refused = _image_refusal(image_data, "/".join(blob), blobs)  # type: ignore[arg-type]
        if refused is not None:
            return f"{image} cannot be shown: {refused}"
        identical.update((image_path, "/".join(blob)))
        return None

    def done(hold: str | None = None, campaign: str | None = None) -> Needs:
        return Needs(frozenset(identical), frozenset(present), campaign, hold, frozenset(sources))
    if kind == "image":
        ref = found[1]
        assert isinstance(ref, ImageRef)
        if ref.source is not None:  # first: an excluded source's image is held whatever else it holds
            excluded_why = admitted(ref.source)
            if excluded_why is not None:
                return done(excluded_why)
        blob = image_blob(data)
        if blob is None:
            return done("it names no storable blob")
        # create-only: copied broken, a repair on the origin could never follow it, so it waits there
        refused = _image_refusal(data, "/".join(blob), blobs)
        if refused is not None:
            return done(refused)
        identical.add("/".join(blob))
        if ref.campaign_id is not None:
            return done(campaign=ref.campaign_id)
        present.add("/".join(manifest_parts(ref.scope)))
        return done()
    ref = found[1]
    assert isinstance(ref, PlacementRef)
    mode, image, readable = placement_target(data)
    if not readable:
        return done("its mode or image cannot be read")
    refused = _placement_refusal(ref, data)
    if refused is not None:
        return done(refused)
    subject = ref.subject
    if isinstance(subject, SourceRef):  # W5 (source admitted, entity exists), W7, W6
        excluded_why = admitted(subject.source)  # before its head: the origin's load() refuses that data
        if excluded_why is not None:
            return done(excluded_why)
        present.add("/".join(manifest_parts(subject.source)))
        head = _entity_head(origin, subject)
        if head is None:
            return done(f"{subject} has no head revision on the origin")
        identical.add(head)
        if image is not None:
            if image.scope != subject.source:
                return done(f"{image} is not in scope for {subject}")
            return done(art(image))
        return done()
    assert isinstance(subject, InstanceRef)
    instance = "/".join(instance_parts(subject))
    present.add(instance)  # W5; a pair-blocked instance is unknown, so the placement is held (A2)
    if mode != "adjust" and (image is None or image.scope == f"campaign:{subject.campaign_id}"):
        # a campaign's own picture (or a hide) never reads a source: no source admission is needed,
        # exactly as the resolver shows campaign-scope art on an instance whatever its derivation
        return done(None if image is None else art(image))
    # inherited source art (W7 through the instance's derivation) or an adjust of it (W9): the
    # derivation, the entity and (for adjust) the source placement must mean the same on both sides
    instance_data = origin.get(instance)
    if instance_data is None:
        return done(f"{subject} is not readable on the origin")
    entity, problem = instance_derivation_of(instance_data)
    if entity is None or image is None or image.scope != entity.source:
        return done(f"{image} is not in scope for {subject} ({problem or 'not its derivation source'})")
    # W5 through the derivation: the resolver inherits nothing from an excluded source, so on the
    # origin this placement shows nothing; never let a receiver that admits the source show it
    excluded_why = admitted(entity.source)
    if excluded_why is not None:
        return done(f"{subject} inherits from {entity}, but {excluded_why}")
    try:  # the derivation is the instance's own text: it may name an entity no library can store
        head = _entity_head(origin, entity)
        manifest = "/".join(manifest_parts(entity.source))
        inherited = "/".join(placement_parts(PlacementRef(entity, ref.slot)))
    except (ValueError, UnstorableIdentifier):
        return done(f"{subject} derives from {entity}, which names no storable entity")
    if head is None:
        return done(f"{entity} has no head revision on the origin")
    identical.update((instance, manifest, head))
    hold = art(image)
    if hold is None and mode == "adjust":
        inherited_data = origin.get(inherited)
        _, base_image, ok = placement_target(inherited_data) if inherited_data is not None else (None, None, False)
        if not ok or base_image is None:
            return done(f"{entity} has no picture in {ref.slot!r} to adjust on the origin")
        refused = _placement_refusal(PlacementRef(entity, ref.slot), inherited_data)  # type: ignore[arg-type]
        if refused is not None:  # W9 reads it through 018 (``nothing_to_adjust``; ``invalid_placement``)
            return done(f"{entity}'s placement in {ref.slot!r} cannot be adjusted: {refused}")
        if base_image != image:  # W9 ``stale_adjust``: the batch refuses an adjust of another picture
            return done(f"{entity} shows {base_image} in {ref.slot!r}, not the adjusted {image}")
        identical.add(inherited)
        hold = art(base_image)
    return done(hold)


def _image_refusal(data: bytes, blob: str, blobs: Mapping[str, int]) -> str | None:
    """Why 018 cannot show this image object (W6, as the batch's ``_check_placement`` and the resolver
    read it: ``invalid_image``, ``missing_blob``, ``blob_size_mismatch``), else ``None``: another schema,
    a payload its codec refuses, or a ``blob`` the origin lists at another size than the object
    declares. ``blobs`` is the origin scan's listing (sizes, never bytes); a blob it does not list is
    an unmet ``identical`` need instead."""
    try:
        document = parse_strict(data)
        schema = document.get("schema") if isinstance(document, Mapping) else None
        if schema != IMAGE_SCHEMA:
            return f"its schema is {schema!r}, not {IMAGE_SCHEMA!r} (invalid_image)"
        image = image_from_payload(document.get("payload"))  # type: ignore[union-attr]
    except (JsonError, ImageModelError) as error:
        return f"its payload cannot be read ({error}) (invalid_image)"
    size = blobs.get(blob)
    if size is not None and size != image.blob.size:
        return f"it declares a {image.blob.size}-byte blob, but {blob} is {size} bytes (blob_size_mismatch)"
    return None


def _placement_refusal(ref: PlacementRef, data: bytes) -> str | None:
    """Why 018 refuses this placement where it sits (its schema and codec, then W8's
    ``validate_placement``, which the batch and the resolver both apply: a source placement may only
    ``set``), else ``None``. The resolver reads a placement under another schema as ``invalid_placement``."""
    try:
        document = parse_strict(data)
        schema = document.get("schema") if isinstance(document, Mapping) else None
        if schema != PLACEMENT_SCHEMA:
            return f"its schema is {schema!r}, not {PLACEMENT_SCHEMA!r} (invalid_placement)"
        placement = placement_from_payload(document.get("payload"))  # type: ignore[union-attr]
    except (JsonError, ImageModelError) as error:
        return f"its payload cannot be read ({error})"
    problem = validate_placement(ref.subject, ref.slot, placement)
    return None if problem is None else f"{problem[0]}: {problem[1]}"


def _entity_head(origin: Mapping[str, bytes], entity: SourceRef) -> str | None:
    """The revision file the origin's manifest names as ``entity``'s head, or its sole revision."""
    manifest = origin.get("/".join(manifest_parts(entity.source)))
    if manifest is not None:
        try:
            heads = parse_exact(manifest_parts(entity.source), manifest).head_refs()  # type: ignore[union-attr]
        except (StoreError, ValueError):
            return None
        revision = heads.get(entity.unpinned())
        if revision is not None:
            try:
                return "/".join(definition_parts(entity.at(revision)))
            except (ValueError, UnstorableIdentifier):
                return None
    prefix = "/".join(definition_parts(entity.at("x"))[:-1]) + "/"
    revisions = [path for path in origin if path.startswith(prefix) and syncable(path) == "definition"]
    return revisions[0] if len(revisions) == 1 else None


def art_unmet(needs: Needs, blocked, final, origin, here: set[str], dest: str,  # type: ignore[no-untyped-def]
              excluded: frozenset[str] = frozenset()) -> str:
    """Why ``needs`` do not hold on ``dest`` ("" when they do). ``final(path)``: the etag ``dest`` will
    hold; ``origin(path)``: the etag the origin holds; ``blocked(path)``: unknown on either side;
    ``excluded``: the sources ``dest``'s ``load()`` excludes.

    The receiver's exclusions are read as they stand before this commit, not as the commit would
    leave them: projecting them would mean re-running whole-library admission over staged bytes. It
    errs one way only. A source this very commit repairs on the receiver (an origin manifest that no
    longer causes a cross-source conflict, say) holds its art for one more sync, which then copies
    it; a source this commit newly excludes there receives art byte-identical to the origin's, with
    everything it needs identical, which 018 simply does not show until that source is repaired.

    Why the receiver's exclusions count at all, when the store already guards its own writes: its
    art-only check refuses an image or source placement written into a source the receiver's own
    files exclude (``InvalidSourceState``). Sync used to report that case as ``unapplicable``; this
    check now catches it first, as ``head_dependency``. But the store sees neither a cross-source
    exclusion (it reads one source's scan, not the library's verdict) nor a campaign placement that inherits a picture from an excluded source (a campaign asset is
    checked against its campaign only). Both would land in a state the receiver's batch refuses.
    Holding them here names the reason as a ``head_dependency``, holds what depends on them in the
    same fixpoint, and leaves the store's check as the backstop it was."""
    if needs.hold is not None:
        return f"018 art the batch would refuse: {needs.hold}"
    unequal = sorted(path for path in needs.identical
                     if blocked(path) or origin(path) is None or final(path) != origin(path))
    absent = sorted(path for path in needs.present if blocked(path) or final(path) is None)
    if needs.campaign is not None and needs.campaign not in here:
        absent.append(f"campaigns/{needs.campaign} (no scope or instance)")
    shut = sorted(needs.admitted & excluded)
    what = (["art it shows not identical on " + dest + ": " + ", ".join(unequal)] if unequal else [])
    what += (["owner or subject missing on " + dest + ": " + ", ".join(absent)] if absent else [])
    what += (["source excluded on " + dest + " (load() leaves it out: 018's excluded_source): " + ", ".join(shut)]
             if shut else [])
    return "; ".join(what)


__all__ = ["Needs", "art_needs", "art_unmet"]
