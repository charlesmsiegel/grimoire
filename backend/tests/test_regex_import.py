"""SillyTavern regex import: the field mapping (store/regex/st_import.py) and
the preview / commit routes (spec §7)."""

import pytest

from grimoire import store
from grimoire.store.regex import st_import


def _script(**over) -> dict:
    base = {
        "id": "0b1c2d3e-st-own-id",
        "scriptName": "Strip thinking",
        "findRegex": "/<think>[\\s\\S]*?<\\/think>/gi",
        "replaceString": "",
        "trimStrings": [],
        "placement": [2],
        "disabled": False,
        "markdownOnly": True,
        "promptOnly": True,
        "runOnEdit": False,
        "substituteRegex": 0,
        "minDepth": None,
        "maxDepth": None,
    }
    return {**base, **over}


def _row(**over) -> dict:
    [row] = st_import.preview(_script(**over))
    return row


def test_import_mapping_exact_script():
    row = _row()
    assert row["verdict"] == "exact" and row["notes"] == []
    rule = row["rule"]
    assert rule["name"] == "Strip thinking"
    assert rule["pattern"] == "<think>[\\s\\S]*?</think>"
    assert rule["flags"] == "gia"
    assert rule["targets"] == ["model"] and rule["applies"] == ["display", "prompt"]
    assert rule["rewrite_stored"] is False
    assert rule["imported"] is None
    # SillyTavern's id is not carried; the commit mints one.
    assert "id" not in rule


@pytest.mark.parametrize("placement, targets", [
    ([2], ["model"]),
    ([1], ["user"]),
    ([1, 2], ["model", "user"]),
    ([2, 5], ["model"]),
])
def test_import_mapping_placement(placement, targets):
    assert _row(placement=placement)["rule"]["targets"] == targets


@pytest.mark.parametrize("placement", [[5], [3, 6], []])
def test_import_mapping_placement_with_no_target_is_untranslatable(placement):
    row = _row(placement=placement)
    assert row["verdict"] == "untranslatable" and row["rule"] is None


def test_import_mapping_dropped_placement_is_noted():
    row = _row(placement=[2, 6])
    assert row["verdict"] == "exact"
    assert any("reasoning" in n for n in row["notes"])
    assert row["rule"]["imported"]["notes"] == row["notes"]


@pytest.mark.parametrize("markdown, prompt, applies", [
    (True, False, ["display"]),
    (False, True, ["prompt"]),
    (True, True, ["display", "prompt"]),
])
def test_import_mapping_applies(markdown, prompt, applies):
    row = _row(markdownOnly=markdown, promptOnly=prompt)
    assert row["rule"]["applies"] == applies
    assert row["rule"]["rewrite_stored"] is False


def test_import_mapping_neither_flag_does_not_rewrite_stored():
    row = _row(markdownOnly=False, promptOnly=False)
    rule = row["rule"]
    assert rule["applies"] == ["display", "prompt"]
    assert rule["rewrite_stored"] is False
    assert any("rewrote the stored chat" in n for n in row["notes"])
    assert rule["imported"]["from"] == "sillytavern"
    assert rule["imported"]["pattern"] == "/<think>[\\s\\S]*?<\\/think>/gi"


def test_import_mapping_substitute_regex_is_untranslatable():
    row = _row(substituteRegex=1)
    assert row["verdict"] == "untranslatable" and row["rule"] is None
    assert any("substituteRegex" in n for n in row["notes"])


def test_import_mapping_disabled():
    assert _row(disabled=True)["rule"]["enabled"] is False
    assert _row()["rule"]["enabled"] is True


@pytest.mark.parametrize("raw, depth", [(-1, None), (None, None), (3, 3), (0, 0)])
def test_import_mapping_depths(raw, depth):
    rule = _row(minDepth=raw, maxDepth=raw)["rule"]
    assert rule["min_depth"] == depth and rule["max_depth"] == depth


def test_import_mapping_absent_depth_is_none():
    script = _script()
    del script["minDepth"], script["maxDepth"]
    [row] = st_import.preview(script)
    assert row["rule"]["min_depth"] is None and row["rule"]["max_depth"] is None


def test_import_mapping_replacement_and_trim_verbatim():
    rule = _row(replaceString="[$1|$<n>|$&|{{match}}|$$]", trimStrings=["*", "~"])["rule"]
    assert rule["replacement"] == "[$1|$<n>|$&|{{match}}|$$]"
    assert rule["trim"] == ["*", "~"]


def test_import_mapping_run_on_edit_is_noted():
    row = _row(runOnEdit=True)
    assert row["verdict"] == "exact"
    assert any("runOnEdit" in n for n in row["notes"])
    assert row["rule"]["imported"]["notes"] == row["notes"]


@pytest.mark.parametrize("replacement", ["before $` after", "$'", "x$$$'"])
def test_import_mapping_context_replacements_are_untranslatable(replacement):
    row = _row(replaceString=replacement)
    assert row["verdict"] == "untranslatable" and row["rule"] is None


def test_import_mapping_escaped_dollar_before_quote_is_fine():
    assert _row(replaceString="$$'")["verdict"] == "exact"


def test_import_mapping_other_macro_in_replacement_is_approximate():
    row = _row(replaceString="{{char}}: {{match}}")
    assert row["verdict"] == "approximate"
    assert any("{{char}}" in n for n in row["notes"])


def test_import_mapping_approximate_pattern_carries_its_notes():
    row = _row(findRegex="/^Mara:/gm")
    assert row["verdict"] == "approximate"
    assert row["rule"]["pattern"] == "^Mara:"
    assert row["rule"]["imported"] == {"from": "sillytavern", "pattern": "/^Mara:/gm",
                                       "notes": row["notes"]}


def test_import_mapping_bare_body_has_no_flags():
    row = _row(findRegex="Winifred")
    assert row["rule"]["pattern"] == "Winifred" and row["rule"]["flags"] == "a"


def test_untranslatable_rows_have_no_rule():
    script = _script(findRegex="/\\p{L}+/gu")
    [row] = st_import.preview(script)
    assert row["verdict"] == "untranslatable"
    assert row["rule"] is None
    assert row["original"] == script
    assert row["name"] == "Strip thinking"
    assert any("property escape" in n for n in row["notes"])


def test_scripts_accepts_every_shape():
    one = _script()
    assert st_import.scripts(one) == [one]
    assert st_import.scripts([one, one]) == [one, one]
    assert st_import.scripts({"regex_scripts": [one]}) == [one]
    assert st_import.scripts({"extensions": {"regex_scripts": [one]}}) == [one]
    for bad in ({"name": "a preset with no scripts"}, "text", None, {"regex_scripts": {}}):
        with pytest.raises(ValueError):
            st_import.scripts(bad)


def test_preview_rows_are_in_file_order_with_indices():
    rows = st_import.preview([_script(scriptName="One"), "junk", _script(scriptName="Two")])
    assert [(r["index"], r["name"], r["verdict"]) for r in rows] == [
        (0, "One", "exact"), (1, "", "untranslatable"), (2, "Two", "exact")]


# --- routes ---------------------------------------------------------------------

@pytest.fixture
def cid(client):
    wid = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign("Saltmarch", wid)


def test_import_preview_accepts_settings_export(client):
    export = {"extensions": {"regex_scripts": [
        _script(scriptName="Strip thinking"),
        _script(scriptName="Letters", findRegex="/\\p{L}/u"),
    ]}}
    res = client.post("/api/regex/import/preview", json={"data": export})
    assert res.status_code == 200
    rows = res.json()["rows"]
    assert [(r["name"], r["verdict"]) for r in rows] == [
        ("Strip thinking", "exact"), ("Letters", "untranslatable")]
    assert rows[0]["rule"]["pattern"] == "<think>[\\s\\S]*?</think>"
    assert rows[1]["rule"] is None
    # Preview writes nothing.
    assert client.get("/api/regex").json()["layer"]["rules"] == []


def test_import_preview_rejects_what_is_not_scripts(client):
    res = client.post("/api/regex/import/preview", json={"data": {"name": "Seraphine"}})
    assert res.status_code == 400


def test_import_commit_appends_and_remints_colliding_id(client, cid):
    url = f"/api/campaigns/{cid}/regex"
    existing = client.put(url, json={"rules": [{"name": "Mine", "pattern": "a"}]}).json()
    mine = existing["layer"]["rules"][0]
    [row] = client.post("/api/regex/import/preview",
                        json={"data": [_script(scriptName="Imported")]}).json()["rows"]
    # A row naming an id already in use, the way a second import of one file
    # (or a hand-edited body) would, is still appended under a fresh one.
    rows = [{**row["rule"], "id": mine["id"]}, row["rule"]]
    res = client.post("/api/regex/import",
                      json={"scope": {"kind": "campaign", "cid": cid}, "rows": rows})
    assert res.status_code == 200
    body = res.json()
    names = [r["name"] for r in body["layer"]["rules"]]
    assert names == ["Mine", "Imported", "Imported"]
    ids = [r["id"] for r in body["layer"]["rules"]]
    assert ids[0] == mine["id"]
    assert len(set(ids)) == 3 and all(i.startswith("r-") for i in ids)
    assert body["added"] == ids[1:]
    assert client.get(url).json()["layer"] == body["layer"]


def test_import_commit_twice_appends_again(client):
    rows = st_import.preview([_script()])
    payload = {"scope": {"kind": "global"}, "rows": [rows[0]["rule"]]}
    client.post("/api/regex/import", json=payload)
    body = client.post("/api/regex/import", json=payload).json()
    rules = body["layer"]["rules"]
    assert len(rules) == 2 and rules[0]["id"] != rules[1]["id"]


def test_import_commit_invalid_row_reports_index(client):
    rows = [{"name": "Fine", "pattern": "a"}, {"name": "Broken", "pattern": "("}]
    res = client.post("/api/regex/import", json={"scope": {"kind": "global"}, "rows": rows})
    assert res.status_code == 400
    assert res.json()["kind"] == "invalid_rule" and res.json()["index"] == 1
    assert client.get("/api/regex").json()["layer"]["rules"] == []


def test_import_commit_scope_errors(client):
    row = {"name": "Fine", "pattern": "a"}
    missing = client.post("/api/regex/import",
                          json={"scope": {"kind": "campaign", "cid": "nowhere"}, "rows": [row]})
    assert missing.status_code == 404
    bad = client.post("/api/regex/import", json={"scope": {"kind": "shelf"}, "rows": [row]})
    assert bad.status_code == 400
    empty = client.post("/api/regex/import", json={"scope": {"kind": "global"}, "rows": []})
    assert empty.status_code == 400
