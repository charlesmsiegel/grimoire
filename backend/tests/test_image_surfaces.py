"""Every surface that stores an image, rostered and driven end to end.

The content-addressed store (`store.image_store`) holds every picture's bytes
once, and a record, a library or a cover holds only a placement
(`image-refs/<name>.json`) naming it. That is a claim about EVERY writer, and a
writer missed by the conversion would not fail anything else: it would put a
file back in a record directory, which still reads, still serves and still
exports -- as a copy nobody shares. So the writers are listed here, the upload
routes are checked against the list by introspection (a new `PUT .../images/
{name}` or `.../cover` with no entry fails `test_every_image_upload_route_is
_rostered`), and each one is driven through the reads the spec names (§13):
serve, thumbnail, listing, `names_in`, the describe queue, a campaign export,
and the art handle.

Fixtures are synthetic (Pillow-drawn PNGs) and every name is invented.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PIL import Image

from grimoire.store import (
    appearances,
    assets,
    campaigns,
    entities,
    image_collections,
    image_hash,
    image_store,
    localize,
    overlay,
    scenes,
    worlds,
)
from grimoire.store.context import art

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
REMOTE = "https://example.invalid/the-gala.png"
COLLECTION = "0123456789abcdef0123456789abcdef"


def _png(seed: int = 0) -> bytes:
    """A real PNG, big enough that `?w=128` makes a genuine thumbnail rather
    than serving the original back."""
    im = Image.new("RGB", (320, 200))
    im.putdata([((x * 3 + seed) % 256, (y * 5 + seed) % 256, (x ^ y) % 256)
                for y in range(200) for x in range(320)])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class _Stored:
    """What a store-driven surface answers in place of an HTTP response, so
    the parametrized test reads every surface the same way."""

    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _put(client, url: str, data: bytes):
    return client.put(url, files={"file": ("upload.png", io.BytesIO(data), "image/png")})


# ---- the surfaces ------------------------------------------------------------

@dataclass(frozen=True)
class Surface:
    id: str
    #: ``(client, ids) -> response``: the write, through the route a user's
    #: upload takes (or the store call a surface with no upload route uses).
    upload: Callable
    #: ``(ids, name) -> url``: where the image is served.
    url: Callable[[dict, str], str]
    #: ``ids -> Path``: the record directory no image file may appear under.
    record_dir: Callable[[dict], Path]
    #: ``(client, ids, name) -> image_id | None``: the listing row's identity,
    #: or None for a surface with no listing (a cover's PUT answer is where its
    #: `image_id` is reported, and the test pins that answer for every surface).
    listed: Callable | None
    #: The describe queue's ``(scope, kind, id)`` for this image, or None for a
    #: surface the queue does not walk (greetings, covers).
    queue: Callable[[dict], tuple[str, str, str]] | None = None
    #: The art handle's ``(kind, id)`` -- and, for an actor, the version to
    #: cast -- or None for a surface the narrator is never offered.
    handle: Callable[[dict], tuple] | None = None
    #: ``(ids, name) -> [url, ...]``: transcript URLs that must export as ONE
    #: packed image. None for a surface no transcript links to (covers).
    export_urls: Callable[[dict, str], list[str]] | None = None
    #: Directory whose `names_in` must list the image.
    image_dir: Callable[[dict], Path] = field(default=lambda ids: Path())


def _actor(scope: str, base: str) -> Surface:
    """A character or PC version's art, world- or campaign-side."""
    key = "char" if base == "characters" else "pc"

    def where(ids: dict) -> str:
        sid = ids["wid"] if scope == "world" else ids["cid"]
        return f"/api/{scope}s/{sid}/{base}/{ids[key]}/versions/default/images"

    def root(ids: dict) -> Path:
        return ids["wroot"] if scope == "world" else ids["croot"]

    def listed(client, ids, name):
        if base == "characters" and scope == "campaign":
            # No per-version listing route on this side: the detail's
            # `image_ids` map is the listing (companion to `image_v`).
            detail = client.get(f"/api/campaigns/{ids['cid']}/characters/{ids['char']}").json()
            ver = next(v for v in detail["versions"] if v["id"] == "default")
            return ver["image_ids"].get(name)
        rows = client.get(where(ids)).json()
        return next(r for r in rows if r["name"] == name).get("image_id")

    def export_urls(ids, name):
        return [f"/api/worlds/{ids['wid']}/{base}/{ids[key]}/versions/default/images/{name}",
                f"/api/campaigns/{ids['cid']}/{base}/{ids[key]}/versions/default/images/{name}"]

    return Surface(
        id=f"{scope}-{base}",
        upload=lambda client, ids: _put(client, f"{where(ids)}/gallery_1", ids["png"]),
        url=lambda ids, name: f"{where(ids)}/{name}",
        record_dir=lambda ids: root(ids) / base / ids[key],
        listed=listed,
        queue=lambda ids: (scope, base, ids[key]),
        handle=lambda ids: (base, ids[key], "default"),
        export_urls=export_urls,
        image_dir=lambda ids: assets.version_dir(root(ids), ids[key], "default", base=base),
    )


def _entity(scope: str, kind: str) -> Surface:
    def where(ids: dict) -> str:
        sid = ids["wid"] if scope == "world" else ids["cid"]
        return f"/api/{scope}s/{sid}/{kind}/{ids[kind]}/images"

    def root(ids: dict) -> Path:
        return ids["wroot"] if scope == "world" else ids["croot"]

    def listed(client, ids, name):
        rows = client.get(where(ids)).json()
        return next(r for r in rows if r["name"] == name).get("image_id")

    return Surface(
        id=f"{scope}-{kind}",
        upload=lambda client, ids: _put(client, f"{where(ids)}/gallery_1", ids["png"]),
        url=lambda ids, name: f"{where(ids)}/{name}",
        record_dir=lambda ids: root(ids) / kind / ids[kind],
        listed=listed,
        queue=lambda ids: (scope, kind, ids[kind]),
        handle=lambda ids: (kind, ids[kind], None),
        export_urls=lambda ids, name: [
            f"/api/worlds/{ids['wid']}/{kind}/{ids[kind]}/images/{name}",
            f"/api/campaigns/{ids['cid']}/{kind}/{ids[kind]}/images/{name}"],
        image_dir=lambda ids: assets.version_dir(root(ids), ids[kind], "default", base=kind),
    )


def _library(scope: str) -> Surface:
    def where(ids: dict) -> str:
        sid = ids["wid"] if scope == "world" else ids["cid"]
        return f"/api/{scope}s/{sid}/images"

    def root(ids: dict) -> Path:
        return ids["wroot"] if scope == "world" else ids["croot"]

    def listed(client, ids, name):
        rows = client.get(where(ids)).json()
        if scope == "campaign":
            rows = rows["images"]   # beside `hidden`, which the picker shows too
        return next(r for r in rows if r["name"] == name).get("image_id")

    return Surface(
        id=f"{scope}-library",
        upload=lambda client, ids: _put(client, f"{where(ids)}/harbour", ids["png"]),
        url=lambda ids, name: f"{where(ids)}/{name}",
        record_dir=lambda ids: root(ids) / "assets" / "images",
        listed=listed,
        queue=lambda ids: (scope, scope, ""),
        handle=lambda ids: (art.LIBRARY, "", None),
        export_urls=lambda ids, name: [f"/api/worlds/{ids['wid']}/images/{name}",
                                       f"/api/campaigns/{ids['cid']}/images/{name}"],
        image_dir=lambda ids: root(ids) / "assets" / "images",
    )


def _cover(scope: str) -> Surface:
    def where(ids: dict) -> str:
        return f"/api/worlds/{ids['wid']}/cover" if scope == "world" \
            else f"/api/campaigns/{ids['cid']}/cover"

    def root(ids: dict) -> Path:
        return ids["wroot"] if scope == "world" else ids["croot"]

    return Surface(
        id=f"{scope}-cover",
        upload=lambda client, ids: _put(client, where(ids), ids["png"]),
        url=lambda ids, name: where(ids),
        record_dir=lambda ids: root(ids) / "assets",
        listed=None,
        image_dir=lambda ids: root(ids) / "assets",
    )


def _collection_upload(client, ids):
    name = image_collections.put_member(ids["wid"], ids["png"])
    image_collections.publish(ids["wid"], COLLECTION, [name])
    placed = assets.resolve(image_collections.image_directory(ids["wid"]), name)
    return _Stored({"name": name, "image_id": placed.image_id if placed else None})


def _greeting_upload(client, ids):
    got = localize.localize_greeting(ids["wroot"], ids["gid"], ids["wid"],
                                     fetch=lambda url: (ids["png"], "png"))
    assert got["localized"] == 1, got
    rows = client.get(f"/api/worlds/{ids['wid']}/greetings/{ids['gid']}/images").json()
    (name,) = [r["name"] for r in rows]
    return _Stored({"name": name, "image_id": assets.image_id(
        ids["wroot"], ids["gid"], "default", name, base="greetings")})


def _library_listed(client, ids, name):
    rows = client.get(f"/api/worlds/{ids['wid']}/images").json()
    return next(r for r in rows if r["name"] == name).get("image_id")


def _greeting_listed(client, ids, name):
    rows = client.get(f"/api/worlds/{ids['wid']}/greetings/{ids['gid']}/images").json()
    return next(r for r in rows if r["name"] == name).get("image_id")


SURFACES: list[Surface] = [
    _actor("world", "characters"),
    _actor("campaign", "characters"),
    _actor("world", "pcs"),
    _actor("campaign", "pcs"),
    *(_entity(scope, kind) for scope in ("world", "campaign") for kind in entities.ENTITY_KINDS),
    _library("world"),
    _library("campaign"),
    _cover("world"),
    _cover("campaign"),
    Surface(
        id="collection-member",
        upload=_collection_upload,
        url=lambda ids, name: f"/api/worlds/{ids['wid']}/images/{name}",
        record_dir=lambda ids: ids["wroot"] / "assets" / "images",
        listed=_library_listed,
        queue=lambda ids: ("world", "world", ""),
        # The member is served at the collection's own URL too, which is the
        # one a transcript carries, and it must pack as the same one image.
        export_urls=lambda ids, name: [
            f"/api/worlds/{ids['wid']}/image-collections/{COLLECTION}/image",
            f"/api/worlds/{ids['wid']}/images/{name}"],
        image_dir=lambda ids: image_collections.image_directory(ids["wid"]),
    ),
    Surface(
        id="greeting",
        upload=_greeting_upload,
        url=lambda ids, name: f"/api/worlds/{ids['wid']}/greetings/{ids['gid']}/images/{name}",
        record_dir=lambda ids: ids["wroot"] / "greetings" / ids["gid"],
        listed=_greeting_listed,
        export_urls=lambda ids, name: [
            f"/api/worlds/{ids['wid']}/greetings/{ids['gid']}/images/{name}",
            f"/api/campaigns/{ids['cid']}/greetings/{ids['gid']}/images/{name}"],
        image_dir=lambda ids: assets.version_dir(ids["wroot"], ids["gid"], "default",
                                                 base="greetings"),
    ),
]

_BY_ID = {s.id: s for s in SURFACES}

#: Every image upload route, by path, and the surface that drives it. The
#: generic `{kind}` routes serve every entity kind, so they map to all of them.
ROUTE_SURFACES: dict[str, set[str]] = {
    "/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}": {"world-characters"},
    "/api/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}": {"campaign-characters"},
    "/api/worlds/{wid}/pcs/{pid}/versions/{vid}/images/{name}": {"world-pcs"},
    "/api/campaigns/{cid}/pcs/{pid}/versions/{vid}/images/{name}": {"campaign-pcs"},
    "/api/worlds/{wid}/{kind}/{eid}/images/{name}": {f"world-{k}" for k in entities.ENTITY_KINDS},
    "/api/campaigns/{cid}/{kind}/{eid}/images/{name}":
        {f"campaign-{k}" for k in entities.ENTITY_KINDS},
    "/api/worlds/{wid}/images/{name}": {"world-library"},
    "/api/campaigns/{cid}/images/{name}": {"campaign-library"},
    "/api/worlds/{wid}/cover": {"world-cover"},
    "/api/campaigns/{cid}/cover": {"campaign-cover"},
}

#: Surfaces that store images with no upload route of their own.
STORE_DRIVEN = {"collection-member", "greeting"}


def _flatten(routes) -> list[tuple[set[str], str]]:
    out: list[tuple[set[str], str]] = []
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":   # lazily expanded include
            out.extend(_flatten(r.effective_candidates()))
        elif hasattr(r, "methods") and hasattr(r, "path"):
            out.append((set(r.methods or ()), r.path))
    return out


def test_every_image_upload_route_is_rostered(client):
    upload_routes = {path for methods, path in _flatten(client.app.routes)
                     if "PUT" in methods
                     and path.endswith(("/images/{name}", "/cover"))}
    assert upload_routes == set(ROUTE_SURFACES), (
        "an image upload route was added or removed: give it a SURFACES entry "
        "and a ROUTE_SURFACES row")
    reached = set().union(*ROUTE_SURFACES.values())
    assert reached <= set(_BY_ID)
    assert reached | STORE_DRIVEN == set(_BY_ID), "a surface no route or store call drives"
    assert len(_BY_ID) == len(SURFACES), "two surfaces share an id"


# ---- the parametrized walk ------------------------------------------------

@pytest.fixture
def ids(client) -> dict:
    """A world with one record of every image-bearing kind, a campaign on it,
    and a greeting whose body links a remote picture for localize to fetch."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    char = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    assert char["version"] == "default"
    pc = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Mara"}).json()
    assert pc["version"] == "default"
    out = {"wid": wid, "char": char["character"], "pc": pc["pc"], "png": _png(7)}
    for kind in entities.ENTITY_KINDS:
        out[kind] = client.post(f"/api/worlds/{wid}/{kind}",
                                json={"name": f"Saltmarch {kind}"}).json()["id"]
    out["gid"] = client.post(f"/api/worlds/{wid}/greetings", json={
        "name": "The Gala", "character": out["char"], "version": "default",
        "body": f"Come in.\n\n![]({REMOTE})\n"}).json()["id"]
    out["cid"] = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    out["wroot"] = worlds.world_root(wid)
    out["croot"] = campaigns.campaign_root(out["cid"])
    return out


def _image_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def _blob(image_id: str) -> bytes:
    obj = image_store.read(image_id)
    assert obj is not None, image_id
    return image_store.blob_path(obj.blob_sha256, obj.ext).read_bytes()


def _queue(client, ids: dict, scope: str) -> set[tuple[str, str, str]]:
    sid = ids["wid"] if scope == "world" else ids["cid"]
    rows = client.get(f"/api/{scope}s/{sid}/images/undescribed").json()
    return {(r["kind"], r["id"], r["name"]) for r in rows}


@pytest.mark.parametrize("surface", SURFACES, ids=[s.id for s in SURFACES])
def test_surface_writes_a_placement_and_reads_back_everywhere(client, ids, surface):
    r = surface.upload(client, ids)
    assert 200 <= r.status_code < 300, getattr(r, "text", r)
    body = r.json()
    image_id = body.get("image_id")
    # Every PUT answers with the placement's identity -- including the world
    # PC route, which nothing pinned before this roster.
    assert image_hash.is_image_id(image_id), body
    name = body.get("name") or "cover"

    # No image bytes in the record directory, nor anywhere in either tree:
    # the record holds a placement and the bytes are in the global store.
    assert _image_files(surface.record_dir(ids)) == []
    assert _image_files(ids["wroot"]) == [] and _image_files(ids["croot"]) == []
    assert any(p.name == f"{name}.json" for p in surface.image_dir(ids).joinpath(
        "image-refs").glob("*.json"))

    blob = _blob(image_id)
    got = client.get(surface.url(ids, name))
    assert got.status_code == 200
    assert got.content == blob
    thumb = client.get(surface.url(ids, name), params={"w": 128})
    assert thumb.status_code == 200 and thumb.content

    assert name in assets.names_in(surface.image_dir(ids))[0]
    if surface.listed is not None:
        assert surface.listed(client, ids, name) == image_id

    if surface.queue is not None:
        scope, kind, rid = surface.queue(ids)
        assert (kind, rid, name) in _queue(client, ids, scope)

    if surface.export_urls is not None:
        _assert_packed_once(client, ids, surface.export_urls(ids, name), blob)

    if surface.handle is not None:
        _assert_reachable_as_art(client, ids, surface, name, blob)


def _assert_packed_once(client, ids: dict, urls: list[str], blob: bytes) -> None:
    """Two posts, each linking the image by a different URL (world-shaped and
    campaign-shaped, or a collection's): the book packs ONE file, the blob."""
    cid = ids["cid"]
    sid = scenes.create_scene(cid, "The crossing")
    for i, url in enumerate(urls):
        scenes.append_message(cid, sid, "assistant", f"Post {i}.\n\n![a picture]({url})\n",
                              speaker="Narrator")
    r = client.get(f"/api/campaigns/{cid}/export.md.zip")
    assert r.status_code == 200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        packed = [n for n in z.namelist() if n.startswith("images/")]
        assert len(packed) == 1, packed
        assert z.read(packed[0]) == blob
        text = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".md"))
    assert text.count(packed[0]) == len(urls)


def _assert_reachable_as_art(client, ids: dict, surface: Surface, name: str,
                             blob: bytes) -> None:
    """Describe the image (through the route its editor uses), and the
    narrator's handle resolves to a campaign URL serving the same blob."""
    assert surface.queue is not None and surface.handle is not None
    scope, queued_kind, queued_id = surface.queue(ids)
    put = client.put(f"{surface.url(ids, name)}/description",
                     json={"description": "Fishing boats at the quay under fog."})
    assert put.status_code == 200, put.text
    assert (queued_kind, queued_id, name) not in _queue(client, ids, scope)

    kind, rid, vid = surface.handle(ids)
    cid = ids["cid"]
    sid = scenes.create_scene(cid, "The harbour")
    if vid is not None:
        role = "player" if kind == "pcs" else "npc"
        appearances.appear(cid, sid, kind, rid, vid, role)
    out = art.resolve_handles(cid, art.handle_for(kind, rid, name), sid)
    assert out.startswith("![Fishing boats at the quay under fog.](/api/campaigns/"), out
    url = out[out.index("](") + 2:-1]
    got = client.get(url)
    assert got.status_code == 200 and got.content == blob


# ---- invariant 13, across surfaces -----------------------------------------

def test_one_picture_on_two_records_packs_once(client, ids):
    """The same picture placed on a character and on a location is one object
    with one blob, and a book that shows both packs it once."""
    wid, png = ids["wid"], ids["png"]
    a = _put(client, f"/api/worlds/{wid}/characters/{ids['char']}/versions/default/images/gallery_1",
             png).json()["image_id"]
    b = _put(client, f"/api/worlds/{wid}/locations/{ids['locations']}/images/gallery_1",
             png).json()["image_id"]
    assert a == b
    _assert_packed_once(client, ids, [
        f"/api/worlds/{wid}/characters/{ids['char']}/versions/default/images/gallery_1",
        f"/api/worlds/{wid}/locations/{ids['locations']}/images/gallery_1"], _blob(a))


# ---- a fork's refs outlive the world record they came from -----------------

def test_a_forks_promoted_art_survives_deleting_the_world_character(client, ids):
    """Deleting the world under a campaign is refused (`WorldInUse`), so the
    nearest deletion that strands a fork's placements is the world CHARACTER.

    The campaign holds its own copy of the character, gives it art -- one
    picture the world never had, and one the world's avatar also is, so the two
    trees share an image object -- and promotes the first to its avatar. Then
    it is forked, and the world record is deleted. Every placement the fork
    holds still resolves to the same bytes: a delete removes placements and
    never an object or a blob, however many other placements named it.

    The copy is materialized on purpose. A campaign's art for a record it only
    ever INHERITED is swept with the world record (#225,
    `overlay.forget_world_record`), by design and before the image store.
    """
    wid, cid, char = ids["wid"], ids["cid"], ids["char"]
    world_art = f"/api/worlds/{wid}/characters/{char}/versions/default/images"
    camp_art = f"/api/campaigns/{cid}/characters/{char}/versions/default/images"
    first, second = _png(1), _png(2)
    shared = _put(client, f"{world_art}/avatar", first).json()["image_id"]
    overlay.materialize_actor(cid, "characters", char)
    own = _put(client, f"{camp_art}/gallery_1", second).json()["image_id"]
    assert _put(client, f"{camp_art}/gallery_2", first).json()["image_id"] == shared
    # A journalled ref swap on the campaign side.
    assert client.post(f"{camp_art}/gallery_1/promote").status_code == 200
    assert assets.image_id(campaigns.campaign_root(cid), char, "default", "avatar") == own

    fork = client.post(f"/api/campaigns/{cid}/fork",
                       json={"name": "Saltmarch Again"}).json()["id"]
    fork_art = f"/api/campaigns/{fork}/characters/{char}/versions/default/images"

    def held() -> dict[str, bytes]:
        detail = client.get(f"/api/campaigns/{fork}/characters/{char}").json()
        ver = next(v for v in detail["versions"] if v["id"] == "default")
        out = {}
        for name, image_id in ver["image_ids"].items():
            got = client.get(f"{fork_art}/{name}")
            assert got.status_code == 200 and got.content == _blob(image_id), name
            out[name] = got.content
        return out

    before = held()
    assert set(before) >= {"avatar", "gallery_2"}
    assert before["avatar"] == _blob(own) == client.get(f"{camp_art}/avatar").content
    assert before["gallery_2"] == _blob(shared)

    assert client.delete(f"/api/worlds/{wid}").status_code == 409   # in use: the fallback
    assert client.delete(f"/api/worlds/{wid}/characters/{char}").status_code == 200
    assert not (ids["wroot"] / "characters" / char).exists()

    assert held() == before
    assert _image_files(campaigns.campaign_root(fork)) == []



# ---- the release note: the first format ingested is the one kept -----------

def test_the_same_picture_in_another_format_keeps_the_first_format(client, ids):
    """A pixel-identical re-upload in a different container is the SAME image
    object, and an object keeps the blob it was first given -- so the slot goes
    on serving the first format. Stated in `docs/store-guarantees.md`."""
    url = (f"/api/worlds/{ids['wid']}/characters/{ids['char']}"
           f"/versions/default/images/avatar")
    png = ids["png"]
    first = _put(client, url, png).json()
    buf = io.BytesIO()
    Image.open(io.BytesIO(png)).save(buf, "WEBP", lossless=True)
    again = client.put(url, files={"file": ("same.webp", io.BytesIO(buf.getvalue()),
                                            "image/webp")}).json()
    assert again["image_id"] == first["image_id"]
    assert first["ext"] == again["ext"] == "png"
    assert client.get(url).content == _blob(first["image_id"])
