"""Per-(provider, model) facts: the file beside a connection, and its guards."""

from __future__ import annotations

import json
import threading

import pytest

from grimoire.store import llm_connections
from grimoire.store.inference import facts


@pytest.fixture()
def conn(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = llm_connections.create_connection("openai_compatible", "Saltmarch Local",
                                            base_url="http://localhost:1234/v1")
    return cid, llm_connections.read_connection_raw(cid)["rev"]


def _ok(at: str = "2026-10-07T00:00:00Z") -> dict:
    return {"ok": True, "at": at}


def test_facts_path_sits_beside_the_connection(conn, tmp_path):
    cid, _ = conn
    assert llm_connections.facts_path(cid) == tmp_path / "llm_connections" / f"{cid}.facts.json"


def test_an_unknown_model_has_the_empty_shape(conn):
    cid, rev = conn
    assert facts.of(cid, "mara-7b", rev) == {
        "vision": "", "prefill": None, "post_process": "", "rates": None,
        "verified": {}, "overrides": {}, "context_window": None, "max_output": None,
    }
    assert facts.read(cid) == {}


def test_round_trip(conn):
    cid, rev = conn
    facts.record_verified(cid, "mara-7b", rev, {"vision": _ok(), "embed": {
        "ok": False, "at": "2026-10-07T00:00:01Z", "error": "no such endpoint"}})
    facts.set_overrides(cid, "mara-7b", {"prefill": "yes", "structured_output": "no"})
    got = facts.of(cid, "mara-7b", rev)
    assert got["verified"] == {
        "vision": _ok(),
        "embed": {"ok": False, "at": "2026-10-07T00:00:01Z", "error": "no such endpoint"},
    }
    assert got["overrides"] == {"prefill": "yes", "structured_output": "no"}
    assert set(facts.read(cid)) == {"mara-7b"}


def test_recording_again_merges_results_under_one_rev(conn):
    cid, rev = conn
    facts.record_verified(cid, "m", rev, {"vision": _ok()})
    facts.record_verified(cid, "m", rev, {"embed": _ok(), "vision": {"ok": False, "at": "t"}})
    assert facts.of(cid, "m", rev)["verified"] == {
        "vision": {"ok": False, "at": "t"}, "embed": _ok()}


def test_verified_from_another_rev_is_hidden_and_replaced(conn):
    cid, rev = conn
    assert facts.record_verified(cid, "m", rev, {"vision": _ok()}) is True
    assert facts.of(cid, "m", "some-other-rev")["verified"] == {}
    # Recording under a new rev starts over rather than blending two eras.
    llm_connections.update_connection(cid, base_url="http://localhost:5678/v1")
    new = llm_connections.read_connection_raw(cid)["rev"]
    assert new != rev
    assert facts.record_verified(cid, "m", new, {"embed": _ok()}) is True
    assert facts.of(cid, "m", new)["verified"] == {"embed": _ok()}
    assert facts.of(cid, "m", rev)["verified"] == {}


def test_a_rev_that_is_not_the_connections_own_writes_nothing(conn):
    """The compare and the write are one step in the store: results probed
    under a rev the connection has moved past describe another endpoint, and
    writing them would replace what the current rev holds."""
    cid, rev = conn
    llm_connections.update_connection(cid, base_url="http://localhost:5678/v1")
    new = llm_connections.read_connection_raw(cid)["rev"]
    assert facts.record_verified(cid, "m", new, {"embed": _ok()}) is True
    before = llm_connections.facts_path(cid).read_text(encoding="utf-8")

    assert facts.record_verified(cid, "m", rev, {"vision": {"ok": False, "at": "t"}}) is False
    assert facts.record_verified(cid, "m", "never-a-rev", {"vision": _ok()}) is False
    assert llm_connections.facts_path(cid).read_text(encoding="utf-8") == before
    assert facts.of(cid, "m", new)["verified"] == {"embed": _ok()}


def test_a_connection_that_is_gone_gets_no_facts_file(conn):
    cid, rev = conn
    llm_connections.delete_connection(cid)
    assert facts.record_verified(cid, "m", rev, {"vision": _ok()}) is False
    assert not llm_connections.facts_path(cid).exists()
    assert facts.record_verified("nobody-here", "m", rev, {"vision": _ok()}) is False
    assert not llm_connections.facts_path("nobody-here").exists()


def test_a_rev_change_does_not_hide_what_the_user_stated(conn):
    cid, _ = conn
    facts.set_overrides(cid, "m", {"vision": "yes"})
    p = llm_connections.facts_path(cid)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["m"].update({"vision": "on", "prefill": True, "post_process": "strict",
                     "rates": {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 2.0}})
    p.write_text(json.dumps(doc), encoding="utf-8")
    got = facts.of(cid, "m", "a-different-rev")
    assert got["overrides"] == {"vision": "yes"}
    assert (got["vision"], got["prefill"], got["post_process"], got["rates"]) == (
        "on", True, "strict",
        {"prompt_usd_per_1k": 1.0, "completion_usd_per_1k": 2.0})


def test_set_overrides_replaces_and_empty_clears(conn):
    cid, rev = conn
    facts.set_overrides(cid, "m", {"vision": "yes", "prefill": "no"})
    facts.set_overrides(cid, "m", {"embed": "yes"})
    assert facts.of(cid, "m", rev)["overrides"] == {"embed": "yes"}
    facts.set_overrides(cid, "m", {})
    assert facts.of(cid, "m", rev)["overrides"] == {}


def test_set_overrides_keeps_the_rest_of_the_model_and_other_models(conn):
    cid, rev = conn
    facts.record_verified(cid, "a", rev, {"vision": _ok()})
    facts.record_verified(cid, "b", rev, {"embed": _ok()})
    facts.set_overrides(cid, "a", {"prefill": "yes"})
    assert facts.of(cid, "a", rev)["verified"] == {"vision": _ok()}
    assert facts.of(cid, "b", rev)["verified"] == {"embed": _ok()}


@pytest.mark.parametrize("bad", [
    {"telepathy": "yes"}, {"vision": "maybe"}, {"vision": True}, {"vision": ""},
])
def test_set_overrides_refuses_unknown_names_and_values(conn, bad):
    cid, _ = conn
    with pytest.raises(ValueError):
        facts.set_overrides(cid, "m", bad)
    assert facts.read(cid) == {}


def test_record_verified_refuses_unknown_capabilities(conn):
    cid, rev = conn
    with pytest.raises(ValueError):
        facts.record_verified(cid, "m", rev, {"telepathy": _ok()})
    assert facts.read(cid) == {}


@pytest.mark.parametrize("raw", [
    "", "{not json", "[]", "3", '"x"', "null",
    '{"m": 3}', '{"m": []}', '{"m": null}',
])
def test_a_mangled_file_reads_as_empty_and_never_raises(conn, raw):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    assert facts.read(cid).get("m", {}) == {}
    assert facts.of(cid, "m", rev) == {
        "vision": "", "prefill": None, "post_process": "", "rates": None,
        "verified": {}, "overrides": {}, "context_window": None, "max_output": None,
    }


def test_mangled_fields_inside_a_model_read_as_empty(conn):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"m": {
        "vision": 7, "prefill": "yes", "post_process": 3, "rates": [1],
        "verified": {"rev": rev, "caps": {"vision": 5, "embed": _ok(), "telepathy": _ok()}},
        "overrides": {"vision": "maybe", "embed": "no", "telepathy": "yes"},
        "context_window": "8192", "max_output": True,
    }}), encoding="utf-8")
    got = facts.of(cid, "m", rev)
    assert got == {"vision": "", "prefill": None, "post_process": "", "rates": None,
                   "verified": {"embed": _ok()}, "overrides": {"embed": "no"},
                   "context_window": None, "max_output": None}


@pytest.mark.parametrize("raw", [
    "", "{not json", '{"other": {"rates": {"prompt_usd_per_1k": 1,}}}', "[]", "null",
    '{"m": 3}',
])
@pytest.mark.parametrize("write", ["overrides", "state", "rates", "verified"])
def test_no_write_replaces_a_file_it_could_not_parse(conn, raw, write):
    """A hand-mangled file reads as nothing stated, but it still holds every
    other model's word -- a trailing comma away from readable. A write merged
    onto that empty read would keep only its own entry, so every write refuses
    (`FactsMangledError`) and the bytes stay as they were."""
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    with pytest.raises(facts.FactsMangledError):
        if write == "overrides":
            facts.set_overrides(cid, "m", {"vision": "yes"})
        elif write == "state":
            facts.state(cid, "m", vision="off")
        elif write == "rates":
            facts.state(cid, "m", rates={"prompt_usd_per_1k": 0.0,
                                         "completion_usd_per_1k": 0.0})
        else:
            facts.record_verified(cid, "m", rev, {"vision": _ok()})
    assert p.read_text(encoding="utf-8") == raw


def test_a_mangled_file_is_refused_on_a_strict_read_and_fail_soft_otherwise(conn):
    cid, rev = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert facts.of(cid, "m", rev)["overrides"] == {}
    with pytest.raises(facts.FactsMangledError):
        facts.of(cid, "m", rev, strict=True)


@pytest.mark.parametrize("write", ["state", "verified"])
def test_no_write_replaces_a_file_it_could_not_read(conn, monkeypatch, write):
    """One that could not be READ (a sharing violation, a sync client holding
    it) is refused as well -- rewritten from an empty read, it would hold only
    this write, and every other model's verified results and overrides would
    be gone. It is the transient case, so it is not `FactsMangledError`."""
    from pathlib import Path

    cid, rev = conn
    assert facts.record_verified(cid, "other", rev, {"vision": _ok()})
    p = llm_connections.facts_path(cid)
    before = p.read_bytes()
    real = Path.read_text

    def held(self, *a, **kw):
        if self == p:
            raise PermissionError("held by a sync client")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    with pytest.raises(facts.FactsUnreadableError) as got:
        if write == "state":
            facts.state(cid, "m", vision="off")
        else:
            facts.record_verified(cid, "m", rev, {"vision": _ok()})
    monkeypatch.setattr(Path, "read_text", real)
    assert not isinstance(got.value, facts.FactsMangledError)
    assert p.read_bytes() == before


@pytest.mark.parametrize("raw", ["", "{not json", "[]", '{"m": 3}'])
def test_the_migrations_copy_refuses_a_mangled_file(conn, raw):
    cid, _ = conn
    p = llm_connections.facts_path(cid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw, encoding="utf-8")
    with pytest.raises(facts.FactsUnreadableError):
        facts.adopt_legacy(cid, "m", {"vision": "off"})
    assert p.read_text(encoding="utf-8") == raw


def test_the_migrations_copy_states_nothing_without_writing(conn):
    cid, _ = conn
    assert facts.adopt_legacy(cid, "m", {}) == {}
    assert not llm_connections.facts_path(cid).exists()


def test_the_migrations_copy_reports_a_refused_value_and_lands_the_rest(conn):
    cid, rev = conn
    refused = facts.adopt_legacy(cid, "m", {"vision": "off", "post_process": "sideways"})
    assert set(refused) == {"post_process"}
    assert facts.of(cid, "m", rev)["vision"] == "off"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "..", "a:b"])
def test_an_unsafe_provider_id(conn, bad):
    _, rev = conn
    assert facts.read(bad) == {}
    assert facts.of(bad, "m", rev)["verified"] == {}
    with pytest.raises(ValueError):
        facts.set_overrides(bad, "m", {"vision": "yes"})
    with pytest.raises(ValueError):
        facts.record_verified(bad, "m", rev, {"vision": _ok()})


def test_deleting_the_connection_removes_its_facts(conn):
    cid, _ = conn
    facts.set_overrides(cid, "m", {"vision": "yes"})
    p = llm_connections.facts_path(cid)
    assert p.exists()
    llm_connections.delete_connection(cid)
    assert not p.exists()


def test_the_file_is_pretty_json_with_a_trailing_newline(conn):
    cid, _ = conn
    facts.set_overrides(cid, "m", {"vision": "yes"})
    text = llm_connections.facts_path(cid).read_text(encoding="utf-8")
    assert text.endswith("}\n") and "\n  " in text


def test_concurrent_recordings_for_different_models_both_land(conn):
    cid, rev = conn
    names = [f"model-{i}" for i in range(24)]
    barrier = threading.Barrier(len(names))
    errors: list[BaseException] = []

    def go(name: str) -> None:
        try:
            barrier.wait()
            facts.record_verified(cid, name, rev, {"vision": _ok()})
        except BaseException as exc:  # noqa: BLE001 -- surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert set(facts.read(cid)) == set(names)
    assert all(facts.of(cid, n, rev)["verified"] == {"vision": _ok()} for n in names)


def test_clearing_overrides_on_an_unknown_model_leaves_no_entry(conn):
    cid, _ = conn
    facts.set_overrides(cid, "ghost", {})
    assert facts.read(cid) == {}
