"""characters: galleries responsibilities."""

from __future__ import annotations

from urllib.parse import quote

from ... import store
from ..common import _campaign_root_or_404, _world_root_or_404, thumb_query
from . import _gallery_sources
from ._router import router as router


@router.get("/worlds/{wid}/images/undescribed")
def list_undescribed_images(wid: str, count: bool = False):
    """Every stored image in this world with NO description entry — the backlog
    `DescribeQueue` steps through. With `?count=1`, only how many: `{"count": n}`.

    Key ABSENT, never merely empty: an image reviewed and deliberately left
    undescribed is finished, and re-offering it is how a queue never empties.

    Registered here rather than in `entities.py` because `/{kind}/{eid}` would
    otherwise swallow it — `entities.router` is included last precisely so a
    named route in another module wins, the same arrangement
    `/worlds/{wid}/subjects/untagged` relies on.

    World-scoped. A campaign reaches most of its art through its world, so
    describing it here describes it once; a campaign that has diverged an image
    describes that one in its own editor.

    The cost is a directory walk over every record of every base, and it is
    paid whenever the character page mounts, because the rail button shows the
    count. Measured at ~200ms for a 300-character world with 900 undescribed
    images — an outlier by some way, and one that shrinks as the queue is
    worked. Worth knowing before adding anything else to this loop.

    Which is what the count form is for: a page that only labels a button
    needs the number, and the number needs neither a `v` token per image (a
    stat apiece) nor a URL. It walks `undescribed_by_version` instead, with
    the SAME record filter (`_describable`), so it is the length of the list
    this route would have answered, not an approximation of it.
    """
    root = _world_root_or_404(wid)
    # One lookup per RECORD, not per image; see `_describable`.
    seen: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    if count:
        n = sum(k for base in _gallery_sources.UNDESCRIBED_BASES
                for rid, vid, k in store.image_descriptions.undescribed_by_version(root, base)
                if _gallery_sources._describable(seen, base, rid, vid,
                                lambda b, r: _gallery_sources._record_name_and_versions(root, b, r)) is not None)
        return {"count": n + store.world_images.undescribed_count(wid)}
    out = []
    for base in _gallery_sources.UNDESCRIBED_BASES:
        for item in store.image_descriptions.undescribed(root, base):
            name = _gallery_sources._describable(seen, base, item["id"], item["vid"],
                                lambda b, r: _gallery_sources._record_name_and_versions(root, b, r))
            if name is None:
                continue
            out.append({"kind": base, "id": item["id"], "vid": item["vid"],
                        "name": item["name"], "record_name": name,
                        "url": _gallery_sources._undescribed_url(wid, base, item),
                        **({"conflicts": item["conflicts"]} if "conflicts" in item else {})})
    # The world's own library, which hangs off no record and so is reachable by
    # none of the base walks above. It belongs in THIS queue rather than each
    # campaign's: every campaign on the world inherits these pictures, so
    # describing one here describes it once for all of them.
    out.extend({"kind": "world", "id": "", "vid": "", "name": image["name"],
                "record_name": "World library",
                "url": f"/api/worlds/{wid}/images/{quote(image['name'], safe='')}",
                **({"conflicts": image["conflicts"]} if "conflicts" in image else {})}
               for image in store.world_images.undescribed(wid))
    return out


@router.get("/worlds/{wid}/gallery")
def list_world_gallery(wid: str):
    """Every image this world holds, in one response — the browser #200 asks for.

    A route rather than a client-side fan-out because art hangs off a record AND
    a version: a gallery assembled in the browser is one request per character,
    per PC, per entity of five kinds and per greeting, and then one more per
    version of each actor. The sweep is the same directory walk
    `/images/undescribed` already pays for (see its note on the cost), over one
    more base.

    Registered here, beside that route, for the same route-ordering reason:
    `entities.router` is included last so `/worlds/{wid}/{kind}` cannot swallow
    a named path in another module.

    World-scoped. A campaign reaches most of its art through its world, and the
    art it has diverged is listed in its own editors — the same split
    `/images/undescribed` draws.
    """
    root = _world_root_or_404(wid)
    out = []
    # One record read per RECORD, not per image, and one subjects read per
    # GREETING -- the mistake `list_undescribed_images` documents right above,
    # which a listing over every image in the world would pay for even harder.
    seen: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    subjects: dict[str, dict[str, list[str]]] = {}
    answered: dict[str, set[str]] = {}
    # The world's character ids, enumerated ONCE. `read_subjects` filters
    # deleted characters out of every entry it returns, and without this it
    # rescans the character directory to learn them per greeting -- an
    # O(greetings x characters) walk on a store that may sit in a synced folder.
    # Its own docstring asks sweeps to pass this; the gallery is a sweep.
    known_cids: set[str] | None = None
    for base in _gallery_sources.GALLERY_BASES:
        for item in store.image_descriptions.catalog(root, base):
            key = (base, item["id"])
            if key not in seen:
                seen[key] = _gallery_sources._record_name_and_versions(root, base, item["id"])
            found = seen[key]
            if found is None or (found[1] and item["vid"] not in found[1]):
                # An asset folder whose record -- or whose VERSION -- is gone.
                # Skipped for `list_undescribed_images`' reason turned around:
                # there is no route that serves these bytes, so a tile over them
                # is a broken image the reader cannot clear.
                continue
            row = {"kind": base, "id": item["id"], "vid": item["vid"],
                   "name": item["name"], "ext": item["ext"], "record_name": found[0],
                   "described": item["described"], "description": item["description"],
                   **_gallery_sources._gallery_image_id(item), **_gallery_sources._gallery_urls(wid, base, item),
                   **_gallery_sources._gallery_art_fields("world", wid, base, item["id"], item["vid"], item, "world")}
            if base == "greetings":
                # Who is in the picture, which for greeting art is the sidecar
                # that governs it. Carried here so the gallery can say which
                # tiles the tagging queue is still going to ask about, without a
                # second sweep of the same tree.
                #
                # ANSWERED is key presence (`reviewed_names`), the same test the
                # queue offers by; the LIST is the filtered read, which drops an
                # entry whose value is not a list. Asking one function both
                # questions is what let a hand-edited sidecar read as untagged
                # here while the queue considered it done -- an unfinished tile
                # with no way to resolve it. Null means "not answered"; `[]`
                # means "answered: nobody", including when what was stored was
                # not a list this can render.
                if item["id"] not in subjects:
                    if known_cids is None:
                        known_cids = set(store.characters.character_refs(root))
                    subjects[item["id"]], answered[item["id"]] = store.image_subjects.read_answered(
                        root, item["id"], known_cids=known_cids)
                row["subjects"] = (subjects[item["id"]].get(item["name"], [])
                                   if item["name"] in answered[item["id"]] else None)
            out.append(row)
    # The world's own library, which hangs off no record and so is reached by
    # none of the base walks above. `kind: "world"` is a new kind on this wire
    # rather than a reuse: `id` and `vid` are empty because there is no record
    # to name, and the rail labels it from the kind.
    descs = store.world_images.read_descriptions(wid)
    for image in store.world_images.list_images(wid):
        url = f"/api/worlds/{quote(wid, safe='')}/images/{quote(image['name'], safe='')}"
        v = quote(image["v"], safe="")
        out.append({"kind": "world", "id": "", "vid": "", "name": image["name"],
                    "ext": image["ext"], "record_name": "World library",
                    "described": image["name"] in descs,
                    "description": descs.get(image["name"], ""),
                    **_gallery_sources._gallery_image_id(image),
                    "provenance": "world",
                    "url": f"{url}?v={v}", "thumb": f"{url}{thumb_query(v)}"})
    return out


@router.get("/campaigns/{cid}/gallery")
def list_campaign_gallery(cid: str):
    """Every image this CAMPAIGN sees, world-inherited and its own alike.

    The world gallery one route up is world-scoped, and says so: a campaign
    reaches most of its art through its world. What that leaves out is the art
    the campaign has of its own -- a diverged avatar, a gallery the campaign
    added -- which is invisible in a world sweep because it is not in the world
    root. A reader who came here from a campaign is asking about the pictures
    that campaign actually renders, so those are the ones this answers with.

    Resolution is `overlay`'s, not a merge written again here. Every row comes
    from `overlay.list_images` and `overlay.read_descriptions`, which is what
    the app serves and describes from -- so a tombstoned image is absent, a
    detached record shows only its own art, a campaign copy shadows the world's
    under the same name whatever it is stored as, and a description written
    campaign-side about inherited art is the one reported. A hand-rolled merge
    here would be a second set of rules to keep in step with those, and the
    first thing to drift would be exactly the case this route exists for.

    URLs are campaign-scoped for EVERY row, inherited ones included. The
    campaign serve route resolves through the same overlay, so one URL shape is
    correct for both origins and no row has to carry where its bytes live.

    Costlier than the world sweep, knowingly: the two roots are both catalogued
    to find the version directories, and then each is resolved through the
    overlay, which reads a campaign sidecar and a world sidecar per version.
    That is the price of an answer that matches what the campaign renders; the
    world route is still there, unchanged, for the world's own question. What
    it does NOT pay is resolving the campaign itself per folder: one
    `overlay.view` carries the world root, the tombstones and the detachments
    through every row below, so those reads are a constant per request rather
    than several per version folder.
    """
    _campaign_root_or_404(cid)
    croot = store.campaigns.campaign_root(cid)
    ov = store.overlay.view(cid)
    out = []
    names: dict[tuple[str, str], tuple[str, set[str]] | None] = {}
    subjects = _gallery_sources._GreetingSubjects(ov.wroot)
    for base in _gallery_sources.GALLERY_BASES:
        out.extend(_campaign_base_rows(cid, croot, ov, base, names=names, subjects=subjects))
    out.extend(_campaign_library_rows(cid))
    return out


def _campaign_version_pairs(croot, wroot, base: str) -> list[tuple[str, str]]:
    """Which (record, version) folders exist on EITHER side. The campaign's
    own art is the point of `list_campaign_gallery`, and a version the world
    never had is only in the campaign's catalog."""
    pairs: set[tuple[str, str]] = set()
    for root in (croot, wroot):
        for item in store.image_descriptions.catalog(root, base):
            pairs.add((item["id"], item["vid"]))
    return sorted(pairs)


def _campaign_base_rows(cid: str, croot, ov, base: str, *,
                        names: dict[tuple[str, str], tuple[str, set[str]] | None],
                        subjects) -> list[dict]:
    """One gallery base's rows for `list_campaign_gallery`, every image
    resolved through the overlay view `ov`. `names` caches each record's
    name and versions across the request."""
    out = []
    for rid, vid in _campaign_version_pairs(croot, ov.wroot, base):
        key = (base, rid)
        if key not in names:
            names[key] = _gallery_sources._overlay_name_and_versions(cid, base, rid, ov)
        found = names[key]
        # Same gate as the world sweep: an asset folder whose record -- or
        # whose version -- is gone serves no bytes, so a tile over it is a
        # broken image the reader cannot clear.
        if found is None or (found[1] and vid not in found[1]):
            continue
        imgs = store.overlay.list_images(cid, rid, vid, base, v=ov)
        if not imgs:
            continue   # every image here was tombstoned campaign-side
        desc = store.overlay.read_descriptions(cid, rid, vid, base, v=ov)
        for item in imgs:
            row = _campaign_gallery_row(cid, croot, ov, (base, rid, vid), item,
                                        record_name=found[0], desc=desc)
            if base == "greetings":
                row["subjects"] = subjects.lookup(rid, item["name"])
            out.append(row)
    return out


def _campaign_gallery_row(cid: str, croot, ov, folder: tuple[str, str, str], item: dict, *,
                          record_name: str, desc: dict) -> dict:
    """One campaign-scoped tile for `item` in the `(base, rid, vid)` folder."""
    base, rid, vid = folder
    base_url = _gallery_sources._actor_image_url(f"/api/campaigns/{quote(cid, safe='')}",
                                base, rid, vid, item["name"])
    v = quote(item["v"], safe="")
    return {"kind": base, "id": rid, "vid": vid, "name": item["name"],
            "ext": item["ext"], "record_name": record_name,
            # Key PRESENCE, the distinction the sidecar turns on: an
            # image reviewed and left blank is described.
            "described": item["name"] in desc,
            "description": desc.get(item["name"], ""),
            **_gallery_sources._gallery_image_id(item),
            "url": f"{base_url}?v={v}",
            "thumb": f"{base_url}{thumb_query(v)}",
            **_gallery_sources._gallery_art_fields("campaign", cid, base, rid, vid, item,
                "campaign" if store.overlay.image_root(cid, rid, vid, item["name"], base, v=ov)
                == croot else "inherited")}


def _campaign_library_rows(cid: str) -> list[dict]:
    """The library this campaign can see: its own images and the world's alike.

    Its own function because the sweep above was already at the complexity gate
    and this is a separate question -- everything above walks records, and a
    library image hangs off none.

    `kind: "campaign"` for BOTH halves, because this route promises a
    campaign-scoped URL on every row it returns and the kind is what names that
    scope: an inherited row cannot be `kind: "world"` here without lying about
    where its bytes are served from. Which one it is rides on `record_name`.
    """
    descs = store.campaign_images.read_descriptions(cid)
    rows = []
    for image in store.campaign_images.list_images(cid):
        url = f"/api/campaigns/{quote(cid, safe='')}/images/{quote(image['name'], safe='')}"
        v = quote(image["v"], safe="")
        rows.append({"kind": "campaign", "id": "", "vid": "", "name": image["name"],
                     "ext": image["ext"],
                     "record_name": "World library" if image["inherited"]
                                    else "Campaign library",
                     "provenance": "inherited" if image["inherited"] else "campaign",
                     "described": image["name"] in descs,
                     "description": descs.get(image["name"], ""),
                     **_gallery_sources._gallery_image_id(image),
                     "url": f"{url}?v={v}", "thumb": f"{url}{thumb_query(v)}"})
    return rows
