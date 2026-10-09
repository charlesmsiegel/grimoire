"""Models in use that nothing prices (slice E, Task 5; spec 9.2).

`store.inference.in_use` answers two questions from configuration alone, never
from the ledger:

- `selections()`: every stored selection that names a provider -- the global
  roles and their fallbacks, the Embedding role, the chosen route pins, and
  each campaign's own -- in the order `settings.used_by` lists them, read
  through the legacy translation so a store the migration has not reached
  reports what plays;
- `unpriced()`: the distinct `(provider, model)` pairs among those whose
  provider exists, whose model is not blank, whose provider reports no price
  and which no rate prices -- neither the model's own rates nor `pricing.json`.

A zero rate somebody entered is a price. Only a campaign's frontmatter parse is
memoized; the translation and the connection lookup are read fresh on every
call (I5), so deleting a provider or editing a legacy connection's model shows
on the next read.

Invented ids and the codebase's placeholder names only.
"""

from __future__ import annotations

import ast
import inspect
import os
import time

import pytest

import grimoire.store as store
from grimoire.store import inference_keys as keys
from grimoire.store import llm_connections, pricing, routing
from grimoire.store.frontmatter import dump_frontmatter, parse_frontmatter
from grimoire.store.inference import facts, in_use, settings

from . import inference_baseline as base

MODEL = "vendor/model-a"
OTHER = "vendor/model-b"
BOTH = {"prompt_usd_per_1k": 0.001, "completion_usd_per_1k": 0.002}
ZERO = {"prompt_usd_per_1k": 0, "completion_usd_per_1k": 0}
ROUTE = "summary"


@pytest.fixture
def client(tmp_path):
    with base.client_at(tmp_path) as c:
        yield c


def _local(name: str = "Saltmarch", **fields) -> str:
    """A provider on the Custom preset, which reports no price."""
    return llm_connections.create_connection(
        "openai_compatible", name, base_url="http://localhost:1/v1", **fields)


def _format2() -> None:
    store.write_config(**{keys.FORMAT_KEY: keys.CURRENT_FORMAT})


def _role(role: str, provider: str, model: str) -> dict:
    return {keys.role_key(role, "provider"): provider, keys.role_key(role, "model"): model}


def _meta_path(cid: str):
    return store.campaigns.paths.campaign_meta_path(cid)


def _set_campaign_meta(cid: str, **fields: str) -> None:
    path = _meta_path(cid)
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta.update(fields)
    path.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def _settle(cid: str, age_s: int = 60) -> None:
    """Backdate `campaign.md` past `statcache`'s racy window, so the memo
    caches it: a file written a moment ago is parsed and never cached."""
    when = time.time() - age_s
    os.utime(_meta_path(cid), (when, when))


def _campaign(client, **fields: str) -> str:
    cid = base._world_and_campaign(client)
    _set_campaign_meta(cid, **{keys.FORMAT_KEY: keys.CURRENT_FORMAT, **fields})
    return cid


def _pairs() -> list[tuple[str, str]]:
    return [(u["provider_id"], u["model"]) for u in in_use.unpriced()]


# ---- what counts ----
def test_counts_distinct_selections_across_roles_fallbacks_pins_and_campaigns(client):
    pid = _local()
    _format2()
    store.write_config(**_role("primary", pid, MODEL),
                       **{keys.fallback_key("decision", "provider"): pid,
                          keys.fallback_key("decision", "model"): MODEL,
                          keys.use_key(ROUTE): keys.PIN,
                          keys.pin_key(ROUTE, "provider"): pid,
                          keys.pin_key(ROUTE, "model"): OTHER})
    cid = _campaign(client, **_role("fast", pid, MODEL))

    got = in_use.unpriced()
    assert got == [
        {"provider_id": pid, "provider_name": "Saltmarch", "model": MODEL,
         "uses": [{"kind": "role", "key": "primary", "scope": "global"},
                  {"kind": "fallback", "key": "decision", "scope": "global"},
                  {"kind": "role", "key": "fast", "scope": "campaign", "cid": cid}]},
        {"provider_id": pid, "provider_name": "Saltmarch", "model": OTHER,
         "uses": [{"kind": "route", "key": ROUTE, "scope": "global"}]},
    ]


def test_one_model_on_two_providers_is_two_pairs(client):
    """M6: a model served by two providers is priced per provider, so it is
    two pairs, sorted by provider name."""
    a, b = _local("Winifred"), _local("Saltmarch")
    _format2()
    store.write_config(**_role("primary", a, MODEL), **_role("fast", b, MODEL))
    assert _pairs() == [(b, MODEL), (a, MODEL)]


def test_a_provider_that_reports_prices_is_not_counted(client):
    _format2()
    # The seeded OpenRouter and Claude subscription providers; a Claude
    # provider with no model runs its default, and still reports a price.
    store.write_config(**_role("primary", "openrouter", MODEL), **_role("fast", "claude", ""))
    assert in_use.unpriced() == []
    assert [(u.provider, u.key) for u in in_use.selections()] == [
        ("openrouter", "primary"), ("claude", "fast")]


def test_rates_in_facts_or_the_pricing_table_clear_it(client):
    pid = _local()
    _format2()
    store.write_config(**_role("primary", pid, MODEL), **_role("fast", pid, "other/model-c"),
                       **_role("decision", pid, "plain"))
    # Sorted by provider name, then model.
    assert _pairs() == [(pid, "other/model-c"), (pid, "plain"), (pid, MODEL)]

    facts.state(pid, MODEL, rates=BOTH)
    assert _pairs() == [(pid, "other/model-c"), (pid, "plain")]

    pricing.write_pricing({"other/*": BOTH})
    assert _pairs() == [(pid, "plain")]

    pricing.write_pricing({"other/*": BOTH, "": BOTH})
    assert _pairs() == []


def test_zero_rates_clear_it(client):
    """Review Focus 5: a local model is free once somebody enters zero rates,
    and a zero somebody entered is a price."""
    pid = _local()
    _format2()
    store.write_config(**_role("primary", pid, MODEL), **_role("fast", pid, OTHER))
    facts.state(pid, MODEL, rates=ZERO)
    pricing.write_pricing({OTHER: ZERO})
    assert _pairs() == []


# ---- what does not ----
def test_a_dangling_provider_is_not_counted(client):
    _format2()
    store.write_config(**_role("primary", "gone-provider", MODEL))
    assert in_use.unpriced() == []
    # Still a stored selection: the provider's detail lists it.
    assert settings.used_by("gone-provider") == [
        {"kind": "role", "key": "primary", "scope": "global"}]


def test_a_deleted_provider_leaves_the_count_on_the_next_read(client):
    """I5: the campaign's parse is memoized, the lookup is not -- a campaign
    selection a delete leaves dangling stops counting at once."""
    pid = _local()
    _format2()
    cid = _campaign(client, **_role("fast", pid, MODEL))
    _settle(cid)
    assert _pairs() == [(pid, MODEL)]

    llm_connections.delete_connection(pid)
    assert in_use.unpriced() == []


def test_a_legacy_connection_model_edit_is_reflected(client):
    """I5: at format 1 a campaign's route override names a connection, and the
    model is the connection's own -- read fresh, not from the memo."""
    pid = _local(model=MODEL)
    route = next(r for r in routing.ROUTES if r.campaign_scoped)
    cid = base._world_and_campaign(client)
    _set_campaign_meta(cid, **{routing.config_key(routing.legacy_key(route)): pid})
    _settle(cid)
    assert _pairs() == [(pid, MODEL)]

    llm_connections.update_connection(pid, model=OTHER)
    assert _pairs() == [(pid, OTHER)]


def test_selections_keep_a_role_with_a_blank_model(client):
    """M12: `selections` is what `used_by` lists, blank model and all; only
    `unpriced` filters it."""
    pid = _local()
    _format2()
    store.write_config(**_role("primary", pid, ""))
    assert in_use.selections() == [in_use.Use(pid, "", "role", "primary", "global", "")]
    assert settings.used_by(pid) == [{"kind": "role", "key": "primary", "scope": "global"}]
    assert in_use.unpriced() == []


def test_an_unchosen_pin_is_not_in_use(client):
    pid = _local()
    _format2()
    store.write_config(**{keys.pin_key(ROUTE, "provider"): pid,
                          keys.pin_key(ROUTE, "model"): MODEL})
    assert in_use.unpriced() == []
    store.write_config(**{keys.use_key(ROUTE): keys.PIN})
    assert _pairs() == [(pid, MODEL)]


def test_the_embedding_role_counts(client):
    pid = _local()
    _format2()
    store.write_config(**{keys.role_key("embedding", "provider"): pid,
                          keys.role_key("embedding", "model"): "vendor/embed"})
    assert in_use.unpriced() == [
        {"provider_id": pid, "provider_name": "Saltmarch", "model": "vendor/embed",
         "uses": [{"kind": "role", "key": "embedding", "scope": "global"}]}]


def test_a_legacy_store_reads_through_the_translation(client):
    pid = _local(model="local-model")
    store.write_config(active_connection_id=pid)
    assert in_use.unpriced() == [
        {"provider_id": pid, "provider_name": "Saltmarch", "model": "local-model",
         "uses": [{"kind": "role", "key": "primary", "scope": "global"}]}]


# ---- what it costs ----
def test_reads_no_ledger(client, monkeypatch):
    pid = _local()
    _format2()
    store.write_config(**_role("primary", pid, MODEL))

    def boom(*_a, **_k):
        raise AssertionError("the chore read the ledger")

    monkeypatch.setattr(store.usage, "_read_rows", boom)
    monkeypatch.setattr(store.usage, "unpriced_models", boom)
    assert _pairs() == [(pid, MODEL)]


def test_a_campaign_parse_is_memoized_until_its_file_changes(client, monkeypatch):
    pid = _local()
    _format2()
    cid = _campaign(client, **_role("fast", pid, MODEL))
    _settle(cid)
    parsed: list[int] = []
    real = in_use.parse_frontmatter

    def counting(text: str):
        parsed.append(1)
        return real(text)

    monkeypatch.setattr(in_use, "parse_frontmatter", counting)
    in_use.selections()
    in_use.selections()
    assert len(parsed) == 1

    _set_campaign_meta(cid, **_role("fast", pid, OTHER))
    _settle(cid, age_s=120)
    assert _pairs() == [(pid, OTHER)]
    assert len(parsed) == 2


def test_campaign_meta_hands_out_a_copy(client):
    cid = _campaign(client)
    _settle(cid)
    first = in_use.campaign_meta(cid)
    first["name"] = "Mara"
    assert in_use.campaign_meta(cid)["name"] != "Mara"


def test_in_use_imports_no_settings():
    """`settings` imports `in_use`; the other way would be a cycle."""
    tree = ast.parse(inspect.getsource(in_use))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "settings" and not (node.module or "").endswith(".settings")
            assert "settings" not in {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            assert not any(a.name.endswith("settings") for a in node.names)


# ---- order, unreadable campaigns, the enumeration (review fixes) ----
def _another_campaign(client, cid: str, name: str) -> str:
    """A second campaign on `cid`'s world."""
    world = in_use.campaign_meta(cid)["world"]
    got = client.post("/api/campaigns", json={"name": name, "world": world})
    assert got.status_code == 200, got.text
    other = got.json()["id"]
    _set_campaign_meta(other, **{keys.FORMAT_KEY: keys.CURRENT_FORMAT})
    return other


def test_selections_keep_used_bys_order(client):
    """Global roles and their fallbacks per role, then chosen pins, then the
    Embedding role, then each campaign by id -- compared as listed, unsorted."""
    pid = _local()
    _format2()
    store.write_config(**_role("primary", pid, MODEL), **_role("fast", pid, MODEL),
                       **{keys.fallback_key("primary", "provider"): pid,
                          keys.use_key(ROUTE): keys.PIN,
                          keys.pin_key(ROUTE, "provider"): pid,
                          keys.role_key("embedding", "provider"): pid,
                          keys.role_key("embedding", "model"): "vendor/embed"})
    late = _campaign(client, **_role("decision", pid, MODEL))       # saltmarch-run
    early = _another_campaign(client, late, "A Long Run")           # a-long-run
    _set_campaign_meta(early, **_role("fast", pid, MODEL))
    assert early < late

    assert [(u.kind, u.key, u.scope, u.cid) for u in in_use.selections()] == [
        ("role", "primary", "global", ""),
        ("fallback", "primary", "global", ""),
        ("role", "fast", "global", ""),
        ("route", ROUTE, "global", ""),
        ("role", "embedding", "global", ""),
        ("role", "fast", "campaign", early),
        ("role", "decision", "campaign", late),
    ]


def test_an_unreadable_campaign_names_nothing_and_the_rest_still_count(client):
    pid = _local()
    _format2()
    good = _campaign(client, **_role("fast", pid, MODEL))
    bad = _another_campaign(client, good, "A Long Run")
    _set_campaign_meta(bad, **_role("decision", pid, OTHER))
    _meta_path(bad).write_bytes(b"\xff\xfe not text")

    assert [(u.key, u.cid) for u in in_use.selections()] == [("fast", good)]
    assert in_use.unpriced() == [
        {"provider_id": pid, "provider_name": "Saltmarch", "model": MODEL,
         "uses": [{"kind": "role", "key": "fast", "scope": "campaign", "cid": good}]}]


def test_campaign_ids_match_world_refs(client):
    """`campaign_ids` replaced `world_refs` in `used_by`'s walk: the same ids,
    in the same order, less the ones `used_by` always dropped as unsafe."""
    cid = base._world_and_campaign(client)
    root = store.campaigns.paths.campaign_meta_path(cid).parent.parent
    (root / "odd.").mkdir()
    (root / "odd." / "campaign.md").write_text("---\nname: Odd\n---\n", encoding="utf-8")
    (root / "no-meta").mkdir()

    refs = [c for c, _name, _world in store.campaigns.read.world_refs()]
    assert "odd." in refs and "no-meta" not in refs
    assert store.campaigns.paths.campaign_ids() == sorted(
        c for c in refs if store.paths.safe_id(c)) == [cid]


def test_a_campaign_write_reads_campaign_md_fresh(client):
    """A change the memo cannot see -- same size, same inode, its mtime put
    back, as a sync client restoring a timestamp leaves it -- still decides a
    write: a campaign a newer build marked is refused, not written over."""
    pid = _local()
    _format2()
    cid = _campaign(client)
    _settle(cid)
    assert in_use.campaign_meta(cid)[keys.FORMAT_KEY] == keys.CURRENT_FORMAT  # memoized

    path = _meta_path(cid)
    before = path.stat()
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    meta[keys.FORMAT_KEY] = str(int(keys.CURRENT_FORMAT) + 1)
    changed = dump_frontmatter(meta, body).encode("utf-8")
    assert len(changed) == before.st_size
    with open(path, "r+b") as f:                    # in place: the inode stays
        f.write(changed)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_ino == before.st_ino
    # The memo cannot tell -- which is the residual the write must not rely on.
    assert in_use.campaign_meta(cid)[keys.FORMAT_KEY] == keys.CURRENT_FORMAT

    with pytest.raises(settings.RefusedError) as refused:
        settings.write("campaign", cid,
                       {"roles": {"fast": {"selection": {"provider": pid, "model": MODEL}}}})
    assert refused.value.status == 409
    assert parse_frontmatter(path.read_text(encoding="utf-8"))[0].get(
        keys.role_key("fast", "provider"), "") == ""


# ---- a model answered natively is priced by no rate (slice H, Task 9) ----
DECIDE_ROUTE = next(r.key for r in routing.ROUTES if r.operation == "decide")


def _native_only() -> str:
    """The OpenAI preset (its own native decisions endpoint, no reported
    price), with `MODEL` stated unable to generate: a Decision use of it is
    answered natively (`resolve.native_only`), where no rate prices anything."""
    pid = llm_connections.create_connection(
        "openai_compatible", "Realm OpenAI", base_url="https://api.openai.com/v1",
        api_key="sk-fake-openai")
    facts.state(pid, MODEL, overrides={"generate": "no"})
    return pid


def test_a_model_in_use_only_natively_is_not_listed(client):
    pid = _native_only()
    _format2()
    store.write_config(**_role("decision", pid, MODEL),
                       **{keys.fallback_key("decision", "provider"): pid,
                          keys.fallback_key("decision", "model"): MODEL,
                          keys.use_key(DECIDE_ROUTE): keys.PIN,
                          keys.pin_key(DECIDE_ROUTE, "provider"): pid,
                          keys.pin_key(DECIDE_ROUTE, "model"): MODEL})
    assert in_use.unpriced() == []
    # Still a stored selection: the provider's detail lists every use.
    assert len(in_use.selections()) == 3


def test_a_model_also_in_use_another_way_is_listed_for_that_use(client):
    pid = _native_only()
    _format2()
    store.write_config(**_role("decision", pid, MODEL), **_role("fast", pid, MODEL))
    assert in_use.unpriced() == [
        {"provider_id": pid, "provider_name": "Realm OpenAI", "model": MODEL,
         "uses": [{"kind": "role", "key": "fast", "scope": "global"}]}]


def test_a_decision_model_that_generates_is_still_listed(client):
    """Ruling 1: one that can generate is answered by structured generation,
    which a rate prices, whatever its `decide_native` says."""
    pid = _native_only()
    facts.state(pid, MODEL, overrides={"generate": "", "decide_native": "yes"})
    _format2()
    store.write_config(**_role("decision", pid, MODEL))
    assert _pairs() == [(pid, MODEL)]
