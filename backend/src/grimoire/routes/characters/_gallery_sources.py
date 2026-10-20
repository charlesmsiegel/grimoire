"""characters: gallery_sources responsibilities."""

from __future__ import annotations

from urllib.parse import quote

from ... import store
from ...store.record_art import read as art_read
from ..common import image_id_field, thumb_query
from ._router import router as router


def _describable(seen: dict, base: str, rid: str, vid: str, lookup) -> str | None:
    """The record name to show beside a queued image, or None to leave it out.

    Left out when its record -- or its VERSION -- is gone: the queue would
    offer an image no route can describe, and the PUT it issues is a 404 by
    design, so the entry could never be cleared and would be re-offered
    forever. Shared by this queue's list and count forms, so the count is the
    length of the list by construction -- and by the campaign queue's
    (`routes/campaigns/_images.py`), with its overlay lookup, so the two scopes apply
    one rule.

    `lookup(base, rid)` is the scope's `(name, version ids)` read, and `seen`
    memoizes it per RECORD, not per image. A character with a gallery
    contributes an entry per picture, and the naive loop re-read one record
    once per image in it: 395ms for a 300-character world, on a route that
    fires whenever the character page mounts. The same mistake
    `context.art._keyword_scores` makes it easy to make twice. Name and
    versions come from ONE read, for the same reason: two separate memos, or a
    version check outside the memo, is the per-image re-read again -- which is
    exactly how it crept back in when the version check was added.
    """
    key = (base, rid)
    if key not in seen:
        seen[key] = lookup(base, rid)
    found = seen[key]
    if found is None or (found[1] and vid not in found[1]):
        return None
    return found[0]


#: The RECORD bases the describe queue walks, world and campaign alike.
#: Greetings are not among them -- see `GALLERY_BASES`.
UNDESCRIBED_BASES = ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS)

#: Every base a whole-world image sweep walks: `list_undescribed_images`' list,
#: plus greetings. `ENTITY_KINDS` is spread rather than spelled out, so a sixth
#: entity kind reaches the gallery by existing.
#:
#: The describe backlog above does not walk greetings; this does. Greeting art is
#: real, browsable art, and the sidecar that governs it is `image_subjects` (who
#: is in the picture) rather than `image_descriptions` (what it depicts) -- so a
#: greeting image is something the gallery has to show and something the describe
#: queue has nothing to ask about.
GALLERY_BASES = ("characters", store.pcs.ASSET_BASE, *store.entities.ENTITY_KINDS, "greetings")


def _gallery_art_fields(scope: str, sid: str, base: str, rid: str, vid: str,
                        image: dict, provenance: str) -> dict:
    # Greetings share the gallery, but their subject workflow is separate and
    # must never become a reuse candidate by acquiring a common placement.
    if base not in store.record_art.ART_RECORD_KINDS:
        return {}
    return {"v": image["v"], "provenance": provenance,
            "placement": art_read.placement(scope, sid, base, rid, vid, image["name"], image["v"])}


class _GreetingSubjects:
    """Who is in each greeting picture, read WORLD-side and cached per greeting.

    World-side because that is where the sidecar is written -- it is what the
    tagging queue walks -- so a campaign asking who is in a greeting picture is
    asking the world's answer, not one of its own.

    A small object rather than three dicts threaded through the loop: the
    caching, the one-time character enumeration and the answered/list split are
    one concern, and inlining them is what pushed the route over the complexity
    gate. The split itself is the world sweep's, verbatim: ANSWERED is key
    presence and the LIST is the filtered read, so a hand-edited sidecar cannot
    read as untagged here while the queue considers it done. `None` means "not
    answered"; `[]` means "answered: nobody".
    """

    def __init__(self, wroot):
        self._wroot = wroot
        self._subjects: dict[str, dict[str, list[str]]] = {}
        self._answered: dict[str, set[str]] = {}
        self._known: set[str] | None = None

    def lookup(self, gid: str, name: str) -> list[str] | None:
        if gid not in self._subjects:
            if self._known is None:
                # Once for the whole sweep: `read_subjects` otherwise rescans
                # the character directory per greeting.
                self._known = set(store.characters.character_refs(self._wroot))
            self._subjects[gid], self._answered[gid] = store.image_subjects.read_answered(
                self._wroot, gid, known_cids=self._known)
        if name not in self._answered[gid]:
            return None
        return self._subjects[gid].get(name, [])


def _overlay_name_and_versions(cid: str, base: str, rid: str,
                               ov: store.overlay.View) -> tuple[str, set[str]] | None:
    """`_record_name_and_versions` through the overlay: the campaign's record if
    it has one, the world's if it inherits, and None when neither can be read.

    Split from the world-rooted one rather than parameterised because the reads
    differ by more than a root -- `overlay.read_character` applies tombstones
    and detachment, which a bare root read cannot see. `ov` is the gallery's
    one view of the campaign, shared with every record it names.
    """
    try:
        if base == "characters":
            d = store.overlay.read_character(cid, rid, v=ov)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        if base == store.pcs.ASSET_BASE:
            d = store.overlay.read_pc(cid, rid, v=ov)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        if base == "greetings":
            g = store.overlay.read_greeting(cid, rid, v=ov)
            return str(g.get("name") or rid), set()
        e = store.overlay.read_entity(cid, base, rid, v=ov)
        return str(e["meta"].get("name") or rid), set()
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound,
            store.entities.EntityNotFound, store.greetings.GreetingNotFound,
            KeyError, OSError, UnicodeDecodeError):
        # The same set the world-rooted helper catches, and for the same reason:
        # skip the folder rather than 500 a whole gallery over one record
        # somebody deleted by hand.
        return None


def _actor_image_url(prefix: str, base: str, rid: str, vid: str, name: str) -> str:
    """One tile's serving URL under `prefix` (`/api/worlds/x` or
    `/api/campaigns/y`). An actor's art is per version; an entity's is keyed on
    a fixed `default`, so its URL has no version segment to carry.

    Quoted segment by segment, like `_undescribed_url`'s: `assets.storable`
    accepts names URL syntax owns.
    """
    if base in ("characters", store.pcs.ASSET_BASE):
        return (f"{prefix}/{base}/{quote(rid, safe='')}"
                f"/versions/{quote(vid, safe='')}"
                f"/images/{quote(name, safe='')}")
    return f"{prefix}/{base}/{quote(rid, safe='')}/images/{quote(name, safe='')}"


def _gallery_image_id(item: dict) -> dict:
    """``{"image_id": ...}`` for a listing row backed by the image store, else
    nothing -- a legacy file has no identity, and the key is absent rather than
    null so the client reads "no identity" as one thing."""
    return image_id_field(item.get("image_id"))


def _gallery_urls(wid: str, base: str, item: dict) -> dict:
    """Full and thumbnail URLs for one gallery tile, both carrying the `?v=`
    token the catalog resolved.

    Versioned on purpose: a bare URL is served `no-cache` and revalidates, which
    for a grid of every image in a world is a request per tile on every render.
    The thumb is the `?w=` downscale `store.thumbs` does on the fly — a gallery
    of full-resolution art is tens of megabytes for pictures drawn at 154px.
    """
    base_url = _undescribed_url(wid, base, item)
    v = quote(item["v"], safe="")
    return {"url": f"{base_url}?v={v}", "thumb": f"{base_url}{thumb_query(v)}"}


def _record_name_and_versions(root, base: str, rid: str) -> tuple[str, set[str]] | None:
    """What to call the record an undescribed image hangs off, and which version
    ids it still has — or None when there is no such record any more.

    An empty version set means "this kind has no versions": entity art is keyed
    on a fixed `default`, so the record existing is the whole question there.

    The versions matter because an asset directory can outlive its version.
    Uploading campaign-side art to a locked actor and then importing a different
    world version leaves the old version's folder behind
    (`appearances.import_version` removes the card, not the folder), and an
    image queued from it can never be described: every PUT 404s on the version
    gate, so the entry would be re-offered forever.
    """
    try:
        if base == "characters":
            # The name and the version ids, off `character.md` and a listing:
            # `read_character` would open every card and list every version's
            # art to answer the same two questions, once per record per queue
            # read.
            name, versions = store.characters.name_and_versions(root, rid)
            return str(name), set(versions)
        if base == store.pcs.ASSET_BASE:
            d = store.pcs.read_pc(root, rid)
            return str(d["meta"]["name"]), {v["id"] for v in d["versions"]}
        # Greeting art hangs off the greeting, which has no versions -- the same
        # fixed `default` an entity's art uses, so the empty set says the same
        # thing here: the record existing is the whole question.
        if base == "greetings":
            return str(store.greetings.read_greeting(root, rid)["meta"]["name"]), set()
        return str(store.entities.read_entity(root, base, rid)["meta"]["name"]), set()
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound,
            store.entities.EntityNotFound, store.greetings.GreetingNotFound,
            KeyError, OSError, UnicodeDecodeError):
        return None


def _undescribed_url(wid: str, base: str, item: dict) -> str:
    """The world-scoped serving URL for one queued image. An actor's art is per
    version; an entity's is keyed on a fixed `default`, so its URL has no
    version segment to carry.

    Quoted segment by segment, like the campaign backlog's. `assets.storable`
    accepts names URL syntax owns -- `a#b` truncates at the fragment, a literal
    `%` can decode to another name -- so a raw URL here showed a broken preview
    for exactly the images whose (encoded) description PUT would have worked.
    """
    if base in ("characters", store.pcs.ASSET_BASE):
        return (f"/api/worlds/{wid}/{base}/{quote(item['id'], safe='')}"
                f"/versions/{quote(item['vid'], safe='')}"
                f"/images/{quote(item['name'], safe='')}")
    return (f"/api/worlds/{wid}/{base}/{quote(item['id'], safe='')}"
            f"/images/{quote(item['name'], safe='')}")
