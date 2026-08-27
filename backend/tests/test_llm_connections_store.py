import importlib

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


# ---- migration ----

def test_zero_config_seeds_both_connections_openrouter_active(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert set(conns) == {"openrouter", "claude"}
    assert conns["openrouter"]["kind"] == "openrouter"
    assert conns["claude"]["kind"] == "claude"
    assert s.read_config()["active_connection_id"] == "openrouter"


def test_migrates_legacy_config_fields(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    (tmp_path / "config.md").write_text(
        "---\n"
        "openrouter_key: 'sk-or-legacy'\n"
        "model: anthropic/claude-x\n"
        "provider: claude\n"
        "claude_model: sonnet\n"
        "---\n\n",
        encoding="utf-8",
    )
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert conns["openrouter"]["key_set"] is True
    openrouter = s.llm_connections.read_connection_raw("openrouter")
    assert openrouter["api_key"] == "sk-or-legacy"
    assert openrouter["model"] == "anthropic/claude-x"
    claude = s.llm_connections.read_connection_raw("claude")
    assert claude["model"] == "sonnet"
    assert s.read_config()["active_connection_id"] == "claude"


def test_migration_is_idempotent_even_after_deleting_everything(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.llm_connections.list_connections()  # triggers migration
    s.llm_connections.delete_connection("openrouter")
    s.llm_connections.delete_connection("claude")
    assert s.llm_connections.list_connections() == []
    s.llm_connections.ensure_migrated()  # must NOT reseed
    assert s.llm_connections.list_connections() == []


def test_crash_recovery_resumes_a_partial_migration(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    home = tmp_path / "llm_connections"
    home.mkdir(parents=True)
    (home / "openrouter.md").write_text(
        "---\nkind: openrouter\nname: OpenRouter\napi_key: 'sk-or-x'\nmodel: m\nbase_url: ''\npost_process: none\nrev: 'r1'\n---\n\n",
        encoding="utf-8",
    )
    # no claude.md, no .migrated marker: simulates a crash between the two seeds
    s.llm_connections.ensure_migrated()
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert set(conns) == {"openrouter", "claude"}
    # the pre-existing openrouter seed must not be clobbered/duplicated
    assert s.llm_connections.read_connection_raw("openrouter")["api_key"] == "sk-or-x"


def test_corrupt_seed_file_is_treated_as_unseeded(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    (tmp_path / "config.md").write_text(
        "---\nopenrouter_key: 'sk-or-fresh'\n---\n\n", encoding="utf-8")
    home = tmp_path / "llm_connections"
    home.mkdir(parents=True)
    (home / "openrouter.md").write_text("not frontmatter at all", encoding="utf-8")
    s.llm_connections.ensure_migrated()
    assert s.llm_connections.read_connection_raw("openrouter")["api_key"] == "sk-or-fresh"


def test_preemptive_create_connection_does_not_block_migration(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    (tmp_path / "config.md").write_text(
        "---\nopenrouter_key: 'sk-or-legacy'\n---\n\n", encoding="utf-8")
    s.llm_connections.create_connection("openai_compatible", "z.ai GLM", base_url="https://api.z.ai")
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert "openrouter" in conns and "claude" in conns
    assert s.llm_connections.read_connection_raw("openrouter")["api_key"] == "sk-or-legacy"


# ---- CRUD ----

def test_create_read_update_delete_openai_compatible(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "z.ai GLM", base_url="https://api.z.ai/v4",
        api_key="sk-z", model="glm-4.6", post_process="strict")
    raw = s.llm_connections.read_connection_raw(cid)
    assert raw["kind"] == "openai_compatible"
    assert raw["base_url"] == "https://api.z.ai/v4"
    assert raw["post_process"] == "strict"
    masked = s.llm_connections.read_connection(cid)
    assert "api_key" not in masked and masked["key_set"] is True
    s.llm_connections.update_connection(cid, name="z.ai GLM (renamed)")
    assert s.llm_connections.read_connection_raw(cid)["name"] == "z.ai GLM (renamed)"
    s.llm_connections.delete_connection(cid)
    import pytest
    with pytest.raises(s.llm_connections.ConnectionNotFound):
        s.llm_connections.read_connection_raw(cid)


def test_multiple_connections_of_the_same_kind_are_allowed(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    a = s.llm_connections.create_connection("openai_compatible", "Endpoint A", base_url="https://a")
    b = s.llm_connections.create_connection("openai_compatible", "Endpoint B", base_url="https://b")
    assert a != b
    assert {c["id"] for c in s.llm_connections.list_connections()} >= {a, b, "openrouter", "claude"}


def test_key_clears_when_base_url_changes_without_a_new_key(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://old.example.com", api_key="sk-old")
    s.llm_connections.update_connection(cid, base_url="https://new.example.com")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == ""


def test_key_is_kept_when_base_url_and_key_change_together(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://old.example.com", api_key="sk-old")
    s.llm_connections.update_connection(cid, base_url="https://new.example.com", api_key="sk-new")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == "sk-new"


def test_unrelated_field_update_leaves_the_key_untouched(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://x", api_key="sk-x")
    s.llm_connections.update_connection(cid, name="Renamed")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == "sk-x"


def test_explicit_empty_api_key_is_treated_as_omitted_when_base_url_unchanged(monkeypatch, tmp_path):
    # A caller that always serializes api_key="" (rather than omitting the
    # field) must not erase a working credential on an unrelated update.
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://x", api_key="sk-x")
    s.llm_connections.update_connection(cid, name="Renamed", api_key="")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == "sk-x"


# ---- rev stamping ----

def test_rev_changes_on_create_and_update(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev1 = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.update_connection(cid, name="Renamed")
    rev2 = s.llm_connections.read_connection_raw(cid)["rev"]
    assert rev1 != rev2


def test_delete_then_recreate_same_name_gets_a_different_rev(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid1 = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev1 = s.llm_connections.read_connection_raw(cid1)["rev"]
    s.llm_connections.delete_connection(cid1)
    cid2 = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://y")
    assert s.llm_connections.read_connection_raw(cid2)["rev"] != rev1


# ---- sidecar / cached models ----

def test_cached_models_empty_until_set(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    assert s.llm_connections.cached_models(cid) == {"models": [], "fetched_at": "", "fetched_by": ""}


def test_set_cached_models_visible_when_rev_matches(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    models = [{"id": "glm-4.6", "name": "GLM 4.6", "context": 128000, "prompt": None, "completion": None}]
    s.llm_connections.set_cached_models(cid, models, rev, attempt="a1")
    result = s.llm_connections.cached_models(cid)
    assert result["models"] == models
    assert result["fetched_at"]
    # Which refresh wrote it (#398): a client whose run was reaped asks the
    # store whether ITS attempt landed, and a timestamp cannot answer that
    # once a second tab refreshing the same connection can move it.
    assert result["fetched_by"] == "a1"


def test_a_sidecar_written_before_the_stamp_existed_still_reads(monkeypatch, tmp_path):
    """`fetched_by` arrived with #398, so every catalog cached before it has
    none. A refresh is not worth failing over a field that only decides whether
    a lost response can be recovered."""
    import json
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint",
                                              base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    models = [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]
    s.llm_connections.set_cached_models(cid, models, rev)
    path = s.llm_connections._sidecar_path(cid)
    old_shape = json.loads(path.read_text(encoding="utf-8"))
    del old_shape["fetched_by"]
    path.write_text(json.dumps(old_shape), encoding="utf-8")

    result = s.llm_connections.cached_models(cid)

    assert result["models"] == models
    # Unclaimed, so nothing can recover against it -- which is the safe
    # direction: an unrecoverable refresh is reported as the reap it was.
    assert result["fetched_by"] == ""


def test_cached_models_hidden_when_rev_is_stale(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    models = [{"id": "old-model", "name": "Old", "context": None, "prompt": None, "completion": None}]
    s.llm_connections.set_cached_models(cid, models, "a-stale-rev")  # connection's real rev differs
    assert s.llm_connections.cached_models(cid) == {"models": [], "fetched_at": "", "fetched_by": ""}


def test_sidecar_cleared_when_base_url_changes(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://old")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}], rev)
    s.llm_connections.update_connection(cid, base_url="https://new")
    assert s.llm_connections.cached_models(cid) == {"models": [], "fetched_at": "", "fetched_by": ""}


def test_delete_removes_the_sidecar_file(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}], rev)
    sidecar = tmp_path / "llm_connections" / f"{cid}.models.json"
    assert sidecar.exists()
    s.llm_connections.delete_connection(cid)
    assert not sidecar.exists()


def test_recreated_connection_never_inherits_an_orphaned_sidecar(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Orphan Source", base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}], rev)
    sidecar_path = tmp_path / "llm_connections" / f"{cid}.models.json"
    (tmp_path / "llm_connections" / f"{cid}.md").unlink()  # delete the record but NOT the sidecar (simulates a crash)
    assert sidecar_path.exists()
    new_id = s.llm_connections.create_connection("openai_compatible", "Orphan Source", base_url="https://y")
    assert new_id == cid  # slugify collides with the freed id
    assert s.llm_connections.cached_models(new_id) == {"models": [], "fetched_at": "", "fetched_by": ""}


def test_deleting_the_active_connection_leaves_nothing_active(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    s.write_config(active_connection_id=cid)
    s.llm_connections.delete_connection(cid)
    assert s.read_config()["active_connection_id"] == ""
    assert s.llm_connections.get_active() is None


def test_recreating_a_deleted_active_connection_does_not_silently_reactivate_it(monkeypatch, tmp_path):
    # The identity-confusion case Codex's review caught: deleting the active
    # connection, then creating a new one under the same name (reusing the
    # freed slug) must NOT make the new one active just because config.md
    # still happened to reference that id — it must require an explicit
    # Set-as-active, same as any other newly-created connection.
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Reused Name", base_url="https://old", api_key="sk-old")
    s.write_config(active_connection_id=cid)
    s.llm_connections.delete_connection(cid)
    new_id = s.llm_connections.create_connection(
        "claude", "Reused Name", model="opus")  # even a different kind
    assert new_id == cid  # same freed slug
    assert s.read_config()["active_connection_id"] == ""
    assert s.llm_connections.get_active() is None


def test_delete_clears_active_id_even_if_file_removal_then_fails(monkeypatch, tmp_path):
    # Proves the ordering fix directly: force the file unlink itself to fail
    # AFTER active_connection_id has already been cleared, and confirm the
    # clear survives (rather than testing the trivial case of failing before
    # any write happens, which proves nothing about the ordering).
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    s.write_config(active_connection_id=cid)

    from pathlib import Path
    original_unlink = Path.unlink
    state = {"failed_once": False}

    def flaky_unlink(self, *args, **kwargs):
        if self.name == f"{cid}.md" and not state["failed_once"]:
            state["failed_once"] = True
            raise OSError("simulated failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    try:
        s.llm_connections.delete_connection(cid)
    except OSError:
        pass
    # active_connection_id is already cleared, even though the file unlink
    # itself failed and the connection technically still exists on disk
    assert s.read_config()["active_connection_id"] == ""

    monkeypatch.setattr(Path, "unlink", original_unlink)
    s.llm_connections.delete_connection(cid)  # retry completes cleanly
    import pytest
    with pytest.raises(s.llm_connections.ConnectionNotFound):
        s.llm_connections.read_connection_raw(cid)


# ---- get_active ----

def test_get_active_resolves_the_configured_connection(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(active_connection_id="claude")
    active = s.llm_connections.get_active()
    assert active is not None and active["kind"] == "claude"


def test_get_active_none_when_unset(monkeypatch, tmp_path):
    # Models an explicit clear that happens AFTER migration has already
    # completed (e.g. via delete_connection on the active connection) --
    # not the pre-migration bootstrap case, which ensure_migrated's own
    # seeding step is responsible for (see test_zero_config_seeds_...).
    s = reload_with_home(monkeypatch, tmp_path)
    s.llm_connections.list_connections()  # let migration complete first (writes the .migrated marker)
    s.write_config(active_connection_id="")  # simulate an explicit clear, e.g. via delete_connection
    assert s.llm_connections.get_active() is None
