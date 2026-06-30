import pytest

from grimoire.store import pcs


def test_create_read_single_version(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", ["student"])
    assert (pid, vid) == ("elara", "default")
    pc = pcs.read_pc(tmp_path, pid)
    assert pc["meta"]["name"] == "Elara"
    assert pc["meta"]["tags"] == ["student"]
    assert pc["versions"][0]["persona"]["name"] == "Elara"


def test_persona_fields_round_trip(tmp_path):
    persona = {"name": "Elara", "pronouns": "she/her", "summary": "scholar",
               "birthdate": "1990-06-29", "description": "A wanderer."}
    pid, vid = pcs.create_pc(tmp_path, "Elara", [], persona=persona)
    assert pcs.read_persona(tmp_path, pid, vid) == persona


def test_versions_and_default(tmp_path):
    pid, _ = pcs.create_pc(tmp_path, "Elara", [])
    v2 = pcs.create_version(tmp_path, pid, "Older", pcs.blank_persona("Elara"))
    assert v2 == "older"
    pcs.set_default_version(tmp_path, pid, v2)
    assert pcs.read_pc(tmp_path, pid)["meta"]["default_version"] == "older"


def test_hash_stable_then_changes(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    h1 = pcs.version_hash(tmp_path, pid, vid)
    pcs.update_version(tmp_path, pid, vid, pcs.read_persona(tmp_path, pid, vid))
    assert pcs.version_hash(tmp_path, pid, vid) == h1
    p = pcs.read_persona(tmp_path, pid, vid)
    p["description"] = "changed"
    pcs.update_version(tmp_path, pid, vid, p)
    assert pcs.version_hash(tmp_path, pid, vid) != h1


def test_set_tags_and_counts(tmp_path):
    pid, _ = pcs.create_pc(tmp_path, "Elara", ["student"])
    pcs.set_tags(tmp_path, pid, ["student", "hannah-s-father"])
    assert pcs.read_pc(tmp_path, pid)["meta"]["tags"] == ["student", "hannah-s-father"]
    pcs.create_pc(tmp_path, "Rook", [])
    assert pcs.pc_count(tmp_path) == 2
    assert set(pcs.pc_refs(tmp_path)) == {"elara", "rook"}


def test_delete_last_version_refused_and_missing(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    with pytest.raises(pcs.PCVersionNotFound):
        pcs.read_persona(tmp_path, pid, "ghost")
    with pytest.raises(ValueError):
        pcs.delete_version(tmp_path, pid, vid)
    with pytest.raises(pcs.PCNotFound):
        pcs.read_pc(tmp_path, "nobody")
