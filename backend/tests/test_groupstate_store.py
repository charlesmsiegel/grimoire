from grimoire.store import campaigns, groupstate, worlds


def _croot(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    return campaigns.campaign_root(campaigns.create_campaign("Run", wid))


def test_read_missing_is_none(monkeypatch, tmp_path):
    assert groupstate.read_state(_croot(monkeypatch, tmp_path), "salt-circle") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _croot(monkeypatch, tmp_path)
    groupstate.write_state(root, "salt-circle",
                           "## Goals\nFind the ledger.\n\n## Secrets\nThe abbot is a member.")
    st = groupstate.read_state(root, "salt-circle")
    assert st["goals"] == "Find the ledger."
    assert st["secrets"] == "The abbot is a member."
    assert st["resources"] == "" and st["focus"] == "" and st["public_perception"] == ""
    assert st["updated"]


def test_unheaded_body_reads_as_goals(monkeypatch, tmp_path):
    root = _croot(monkeypatch, tmp_path)
    groupstate.write_state(root, "salt-circle", "Quietly expanding.")
    st = groupstate.read_state(root, "salt-circle")
    assert st["goals"] == "Quietly expanding."
    assert st["secrets"] == ""


def test_prose_containing_fake_header_not_split(monkeypatch, tmp_path):
    root = _croot(monkeypatch, tmp_path)
    body = "Expanding.\n## Secrets\nnot a real section"  # doesn't START with a header
    groupstate.write_state(root, "salt-circle", body)
    assert groupstate.read_state(root, "salt-circle")["goals"] == body


def test_compose_body_bare_when_only_goals():
    assert groupstate.compose_body({"goals": "Expand."}) == "Expand."


def test_compose_body_headed_and_ordered():
    body = groupstate.compose_body({"secrets": "S.", "goals": "G.", "focus": "F."})
    assert body.index("## Goals\nG.") < body.index("## Focus\nF.") < body.index("## Secrets\nS.")
    assert "## Resources" not in body


def test_compose_body_empty_when_all_blank():
    assert groupstate.compose_body({}) == ""
