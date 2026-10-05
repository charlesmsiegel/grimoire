"""The display phase reaches the scene read and the exports (regex spec 5.3, 5.4).

A scene GET carries `shown` only on a message a display rule changed, and
`content` stays raw so an edit starts from what is stored. The Markdown, HTML,
text and EPUB exports read the display text; the JSON export is the raw store.
"""

from __future__ import annotations

import io
import json
import zipfile

from grimoire import store
from grimoire.store import export

STAR = {"name": "Strip actions", "pattern": r"\*[^*]*\*", "replacement": "",
        "targets": ["model", "user"]}


def put_rules(client, url, *rules):
    response = client.put(url, json={"rules": list(rules)})
    assert response.status_code == 200, response.text


def seed(client, texts):
    wid = store.worlds.create_world("Realm")
    cid = store.campaigns.create_campaign("Saltmarch", wid)
    sid = store.scenes.create_scene(cid, "Mara")
    for i, text in enumerate(texts):
        store.scenes.append_message(cid, sid, "user" if i % 2 == 0 else "assistant", text)
    return cid, sid


def messages_of(client, cid, sid, query=""):
    response = client.get(f"/api/campaigns/{cid}/scenes/{sid}{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_shown_only_when_changed(client):
    cid, sid = seed(client, ["Hello", "*smiles* Welcome", "Plain reply"])
    put_rules(client, f"/api/campaigns/{cid}/regex", STAR)
    msgs = messages_of(client, cid, sid)["messages"]
    assert "shown" not in msgs[0] and "shown" not in msgs[2]
    assert msgs[1]["shown"].strip() == "Welcome"


def test_edit_starts_from_content(client):
    cid, sid = seed(client, ["Hello", "*smiles* Welcome"])
    put_rules(client, f"/api/campaigns/{cid}/regex", STAR)
    msg = messages_of(client, cid, sid)["messages"][1]
    assert "*smiles*" in msg["content"] and "*smiles*" not in msg["shown"]


def test_no_rules_leaves_the_read_untouched(client):
    cid, sid = seed(client, ["Hello", "*smiles* Welcome"])
    assert all("shown" not in m for m in messages_of(client, cid, sid)["messages"])
    assert all("shown" not in m for m in messages_of(client, cid, sid, "?limit=5")["messages"])


def test_windowed_read_depth_from_total(client):
    cid, sid = seed(client, [f"line {i} *x*" for i in range(10)])
    put_rules(client, f"/api/campaigns/{cid}/regex",
              {**STAR, "name": "Recent only", "max_depth": 2})
    tail = messages_of(client, cid, sid, "?limit=4&before=10")
    assert tail["offset"] == 6
    assert [("shown" in m) for m in tail["messages"]] == [False, True, True, True]
    earlier = messages_of(client, cid, sid, "?limit=4&before=4")
    assert earlier["offset"] == 0
    assert not any("shown" in m for m in earlier["messages"])
    whole = messages_of(client, cid, sid)["messages"]
    assert [i for i, m in enumerate(whole) if "shown" in m] == [7, 8, 9]


def _chapter_text(cid):
    z = zipfile.ZipFile(io.BytesIO(export.build_markdown_bundle(cid)[0]))
    return next(z.read(n).decode() for n in z.namelist() if n.startswith("001-"))


def test_markdown_export_uses_display(client):
    cid, _sid = seed(client, ["Hello", "*smiles* Welcome"])
    assert "*smiles*" in _chapter_text(cid)
    put_rules(client, f"/api/campaigns/{cid}/regex", STAR)
    text = _chapter_text(cid)
    assert "*smiles*" not in text and "Welcome" in text


def test_text_export_uses_display(client):
    cid, _sid = seed(client, ["Hello", "*smiles* Welcome"])
    put_rules(client, f"/api/campaigns/{cid}/regex", STAR)
    response = client.get(f"/api/campaigns/{cid}/export.txt")
    assert response.status_code == 200
    assert "*smiles*" not in response.text and "Welcome" in response.text


def test_json_export_raw(client):
    cid, _sid = seed(client, ["Hello", "*smiles* Welcome"])
    put_rules(client, f"/api/campaigns/{cid}/regex", STAR)
    blob = client.get(f"/api/campaigns/{cid}/export.json").content
    assert "*smiles*" in json.dumps(json.loads(blob))
