"""The play view's portraits carry `avatar_v`, so their URLs cache immutable.

Every face the play view draws -- the cast grid, the speaker plates, the
inspector, the scene's cast list, the dossier and the record drawer -- is built
off one of three reads: `GET /appearances`, the casefile, and the cast detail.
None of them used to name the avatar's bytes, so every portrait URL went out
without `?v=`, which `_serve_image_file` serves `no-cache`: reopening a scene
revalidated one image per actor on it.

Each test here spends the token the way the client does and checks the answer
is the immutable one for the bytes that are actually there.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


def _seated(client):
    """A scene with Seraphine (NPC) and Winifred (PC) in it, each with an avatar
    in the world library -- where a locked actor's art stays, since locking
    copies cards and never assets."""
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    wroot = store.worlds.world_root(wid)
    npc, nv = store.characters.create_character(wroot, "Seraphine")
    pc, pv = store.pcs.create_pc(wroot, "Winifred", [], persona=store.pcs.blank_persona("Winifred"))
    store.assets.put_image(wroot, npc, nv, "avatar", PNG, "png")
    store.assets.put_image(wroot, pc, pv, "avatar", PNG + b"-pc", "png", store.pcs.ASSET_BASE)
    sid = store.scenes.create_scene(cid, "The Long Tide")
    store.appearances.appear(cid, sid, "characters", npc, nv, "npc")
    store.appearances.appear(cid, sid, "pcs", pc, pv, "player")
    return wroot, cid, sid, (npc, nv), (pc, pv)


def _spend(client, cid, kind, aid, vid, token):
    """The portrait request the client builds from a row: immutable, and the
    bytes the image route resolves to."""
    r = client.get(f"/api/campaigns/{cid}/{kind}/{aid}/versions/{vid}/images/avatar",
                   params={"v": token})
    assert r.status_code == 200, r.text
    assert "immutable" in r.headers["cache-control"]
    return r.content


def test_every_appearance_row_carries_its_avatar_token(client):
    wroot, cid, _sid, (npc, nv), (pc, pv) = _seated(client)
    rows = {(r["kind"], r["id"]): r for r in client.get(f"/api/campaigns/{cid}/appearances").json()}
    npc_v = rows[("characters", npc)]["avatar_v"]
    pc_v = rows[("pcs", pc)]["avatar_v"]
    assert npc_v == store.assets.image_version(store.assets.image_path(wroot, npc, nv, "avatar"))
    assert pc_v == store.assets.image_version(
        store.assets.image_path(wroot, pc, pv, "avatar", store.pcs.ASSET_BASE))
    assert _spend(client, cid, "characters", npc, nv, npc_v) == PNG
    assert _spend(client, cid, "pcs", pc, pv, pc_v) == PNG + b"-pc"


def test_an_appearance_row_follows_a_redrawn_avatar(client):
    """The token has to move when the bytes do: an old `?v=` is a year-long
    cache entry for the old picture, so a row that kept handing it out would
    never show the new one."""
    wroot, cid, _sid, (npc, nv), _pc = _seated(client)

    def token():
        return next(r["avatar_v"] for r in client.get(f"/api/campaigns/{cid}/appearances").json()
                    if r["id"] == npc)

    before = token()
    store.assets.put_image(wroot, npc, nv, "avatar", PNG + b"-redrawn and longer", "png")
    after = token()
    assert after != before
    assert _spend(client, cid, "characters", npc, nv, after) == PNG + b"-redrawn and longer"


def test_an_actor_with_no_avatar_has_no_token(client):
    """None, not an empty string or a token for some other file: a row with no
    avatar draws initials, and nothing should be cached on its behalf."""
    wroot, cid, sid, _npc, _pc = _seated(client)
    bare, bv = store.characters.create_character(wroot, "Mara")
    store.appearances.appear(cid, sid, "characters", bare, bv, "npc")
    row = next(r for r in client.get(f"/api/campaigns/{cid}/appearances").json()
               if r["id"] == bare)
    assert row["avatar_v"] is None


@pytest.mark.parametrize("path", ["casefile", ""])
def test_the_dossier_and_the_drawer_carry_the_actor_s_avatar_token(client, path):
    _wroot, cid, sid, (npc, nv), (pc, pv) = _seated(client)
    for kind, aid, vid, body in (("characters", npc, nv, PNG), ("pcs", pc, pv, PNG + b"-pc")):
        url = f"/api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{aid}" + (f"/{path}" if path else "")
        got = client.get(url).json()
        assert got["version"] == vid
        assert got["avatar_v"] is not None
        assert _spend(client, cid, kind, aid, vid, got["avatar_v"]) == body
