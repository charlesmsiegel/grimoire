"""Synthetic seven-kind setup shared by the record-art contract tests."""

import io

from PIL import Image

from grimoire.store import campaigns, entities, worlds

KINDS = ("characters", "pcs", *entities.ENTITY_KINDS)


def png(seed: int = 0) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (320, 200), (seed % 256, 90, 120)).save(buf, "PNG")
    return buf.getvalue()


def setup_records(client) -> dict:
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    char = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"}).json()
    pc = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Mara"}).json()
    records = {"characters": char["character"], "pcs": pc["pc"]}
    for kind in entities.ENTITY_KINDS:
        records[kind] = client.post(f"/api/worlds/{wid}/{kind}",
                                   json={"name": f"Saltmarch {kind}"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    return {"world": wid, "campaign": cid, "records": records,
            "wroot": worlds.world_root(wid), "croot": campaigns.campaign_root(cid)}


def image_url(ids: dict, scope: str, kind: str, name: str = "") -> str:
    url = f"/api/{scope}s/{ids[scope]}/{kind}/{ids['records'][kind]}"
    if kind in ("characters", "pcs"):
        url += "/versions/default"
    return url + "/images" + (f"/{name}" if name else "")


def art_url(ids: dict, scope: str, kind: str) -> str:
    url = f"/api/{scope}s/{ids[scope]}/record-art/{kind}/{ids['records'][kind]}"
    return url + ("?version=default" if kind in ("characters", "pcs") else "")


def upload(client, ids: dict, scope: str, kind: str, name: str, seed: int = 0):
    response = client.put(image_url(ids, scope, kind, name),
                          files={"file": ("image.png", png(seed), "image/png")})
    assert response.status_code == 200, response.text
    return response.json()
