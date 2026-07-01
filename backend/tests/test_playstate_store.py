from grimoire.store import playstate, worlds


def _root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return worlds.world_root(worlds.create_world("W"))


def test_read_missing_is_none(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    assert playstate.read_state(root, "seraphine") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "Wounded left arm; travels with the party.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Wounded left arm; travels with the party."
    assert st["updated"]


def test_write_replaces_snapshot(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "v1")
    playstate.write_state(root, "seraphine", "v2")
    assert playstate.read_state(root, "seraphine")["current_state"] == "v2"


def test_compose_body_bare_when_no_knowledge():
    assert playstate.compose_body("Just hurt.", "", "") == "Just hurt."


def test_compose_body_headed_when_knowledge_present():
    body = playstate.compose_body("Hurt.", "The map is fake.", "")
    assert "## Current state\nHurt." in body
    assert "## Knows\nThe map is fake." in body
    assert "## Suspects" not in body  # empty section omitted


def test_read_parses_three_sections(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine",
                          "## Current state\nHurt.\n\n## Knows\nThe map is fake.\n\n## Suspects\nElara lies.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Hurt."
    assert st["knows"] == "The map is fake."
    assert st["suspects"] == "Elara lies."


def test_read_unheaded_body_is_current_state(monkeypatch, tmp_path):
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "Wounded; travels with the party.")
    st = playstate.read_state(root, "seraphine")
    assert st["current_state"] == "Wounded; travels with the party."
    assert st["knows"] == "" and st["suspects"] == ""


def test_read_does_not_split_prose_with_fake_header(monkeypatch, tmp_path):
    # A current_state-only body whose prose contains a header-looking line must NOT be
    # split (round-trip corruption): it isn't structured because it doesn't START with one.
    root = _root(monkeypatch, tmp_path)
    body = playstate.compose_body("He lectured: ## Knows nothing of value.\n## Suspects everyone.", "", "")
    playstate.write_state(root, "seraphine", body)
    st = playstate.read_state(root, "seraphine")
    assert "## Knows nothing of value." in st["current_state"]
    assert st["knows"] == "" and st["suspects"] == ""


def test_read_keeps_leading_prose_before_a_header(monkeypatch, tmp_path):
    # A hand-edited/synced body with leading prose then a header: nothing is dropped.
    root = _root(monkeypatch, tmp_path)
    playstate.write_state(root, "seraphine", "He is the duke.\n\n## Knows\nthe password")
    st = playstate.read_state(root, "seraphine")
    assert "He is the duke." in st["current_state"]
    assert "## Knows" in st["current_state"]  # taken wholesale, not split
    assert st["knows"] == ""
