"""Sprint 116's Authoring page and its ``authoring.*`` adapter ops, through 108's shell contract.

The adapter serves 087's ``AuthoringLibrary`` and ``AuthoringGenerator``, 082's ``InvestigationService`` and 079's
``sourceupgrades`` over the app's open library, as the library owner. The proving scenario runs as a screen would:
on a synthetic world with an existing town and guild, a proposal of three linked towns, a namesake of the guild and a
brainstorm is built (by hand, and again from a scripted model reply behind an approved estimate); its review shows
the namesake; two of the three towns are accepted, and the drafted change set lists exactly their two entity files,
one topology revision and the manifest; it is applied; the two towns and their links are there, the third and the
brainstorm are not. Then the audit finds a cited problem, and previewing the removal of a shared source lists a
chat's reference as missing. The page itself runs end to end under Node (``js/authoring.e2e.mjs``) where Node is
installed. Synthetic library; the one model call goes to ``ScriptedTransport`` only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from authoring_app_fixtures import (
    COAST,
    COAST_MAP,
    GUILD,
    HALL,
    LANTERN,
    MANIFEST,
    VALE,
    entity,
    reply,
    seed_maps,
    seed_vale,
    settings,
    towns,
)
from scene_fixtures import MARA_VELL
from textapi_fixtures import Device, desktop_library

from grimoire.app import AppServer
from grimoire.app.adapters import providers
from grimoire.app.adapters.authoring import common, proposals
from grimoire.app.appapi import AppApi
from grimoire.app.config import authoring_policy, config_json, parse_model_config
from grimoire.app.pages import load_pages
from grimoire.core.identity import SourceRef
from grimoire.testing.fileops import snapshot as files_of
from grimoire.testing.providers import ScriptedTransport
from grimoire.textapi import ApiRefused
from grimoire.topology.codec import decode_topology

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
REQUIRES = {"authoring.source", "authoring.build", "authoring.suggest_plan", "authoring.suggest", "authoring.review",
            "authoring.draft", "authoring.apply", "authoring.reject", "authoring.audit", "authoring.audit_review",
            "authoring.upgrades", "authoring.pin_plan", "authoring.pin"}


@pytest.fixture
def desk(tmp_path):
    return desktop_library(tmp_path / "desk")


def refused(app: AppApi, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def canonical(device) -> dict:
    """Every library file but the security log (082's grants and reads are audited there) and the store's own
    bookkeeping: what an op that writes nothing must leave byte-identical."""
    return {path: data for path, data in files_of(device.table.store.root).items()
            if not path.startswith(("authority/security/", "."))}


def configured(device, path: Path, replies=()) -> ScriptedTransport:
    transport = ScriptedTransport(list(replies))
    providers.configure(device.api, path, environ={}, transport=transport)
    return transport


def record(ref: SourceRef) -> str:
    return f"sources/world/vale/_source/records/{ref.kind}/{ref.entity_id}/{ref.revision}.json"


def held(device) -> common.Memory:
    """The app's in-memory proposals and requests for ``device``'s library (what the ops keep)."""
    return common.memory(SimpleNamespace(api=device.api))  # type: ignore[arg-type]


def accept(app: AppApi, proposal: dict, *keys: str, distinct=()) -> dict:
    return app.call("authoring.draft", {"source": VALE, "proposal": proposal["proposal"], "accept": list(keys),
                                        "distinct": list(distinct)})["proposal"]


# -- the page -----------------------------------------------------------------------------------------------------
def test_the_page_registers_hidden_on_its_own_route_and_the_source_page_links_to_it(desk):
    pages = {page.id: page for page in load_pages(STATIC)}
    page = pages["authoring"]
    assert (page.route, page.slot, page.owner) == ("authoring", "hidden", "116")
    assert set(page.requires) == REQUIRES
    folder, _chat = desk
    with Device(folder) as device:
        assert page.to_json(AppApi(device.api).ops)["missing"] == []
    shown = (STATIC / "pages" / "source" / "page.js").read_text(encoding="utf-8")
    assert 'ctx.registered("authoring")' in shown and '"authoring", [read.source]' in shown


# -- 087: the proving scenario ---------------------------------------------------------------------------------------
def test_two_of_three_linked_towns_are_accepted_and_written_as_reviewed(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        before = canonical(device)
        shown = app.call("authoring.source", {"source": VALE})
        assert shown["world"] is True and shown["title"] == "The Vale"
        assert {item["ref"] for item in shown["records"]} == {str(COAST), str(LANTERN), str(GUILD)}
        assert shown["suggest"] == {"available": False, "blocked": shown["suggest"]["blocked"]}
        assert shown["suggest"]["blocked"]["reason"] == "no_model"

        proposal = app.call("authoring.build", {"source": VALE, **towns()})["proposal"]
        assert proposal["kind"] == "hand" and proposal["origin"] == "human:owner" and proposal["state"] == "open"
        assert proposal["reviewed"] is True
        # the review shows the guild's namesake, as the world's own Lamplighters Guild
        flagged = {item["key"]: item["duplicates"] for item in proposal["candidates"] if item["duplicates"]}
        assert flagged == {"lamplighters": [{"existing": str(GUILD), "existing_name": "Lamplighters Guild",
                                             "other": None}]}
        assert [item["problem"] for item in proposal["links"]] == [None] * 6
        assert canonical(device) == before

        # a namesake is accepted only when it is marked distinct, and a brainstorm never
        assert refused(app, "authoring.draft", source=VALE, proposal=proposal["proposal"],
                       accept=["brinewick", "lamplighters"]).reason == "duplicate_unacknowledged"
        assert refused(app, "authoring.draft", source=VALE, proposal=proposal["proposal"],
                       accept=["bell"]).reason == "brainstorm_not_storable"
        assert refused(app, "authoring.apply", source=VALE, proposal=proposal["proposal"],
                       draft="nothing").reason == "changed"

        drafted = accept(app, proposal, "brinewick", "gullhaven")
        draft = drafted["draft"]
        brinewick, gullhaven = SourceRef(VALE, "location", "brinewick", "r1"), SourceRef(VALE, "location", "gullhaven", "r1")
        new_map = SourceRef(VALE, "topology", "map", "r1")
        # exactly two entity files, one topology revision and the manifest, each shown byte for byte
        assert [(item["change"], item["path"]) for item in draft["files"]] == sorted(
            [("create", record(brinewick)), ("create", record(gullhaven)), ("create", record(new_map)),
             ("manifest", MANIFEST)], key=lambda row: row[1])
        assert all(item["text"] for item in draft["files"])
        town = json.loads(next(item["text"] for item in draft["files"] if item["path"] == record(brinewick)))
        assert town["payload"]["name"] == "Brinewick"
        assert draft["records"] == {"brinewick": str(brinewick), "gullhaven": str(gullhaven)}
        assert draft["topology"] == str(new_map) and draft["applicable"] is True
        assert [item["ref"] for item in draft["preconditions"]] == [str(COAST)]
        assert draft["kept"] == ["shore-road", "brinewick-in", "gullhaven-in"]
        assert {item["link"]: item["reason"] for item in draft["dropped"]} == {
            "marsh-road": "rejected_endpoint", "tidemoor-in": "rejected_endpoint", "bell-road": "rejected_endpoint"}
        assert draft["discarded"] == ["tidemoor", "lamplighters", "bell"] and draft["surfaced"] == ["lamplighters"]
        assert canonical(device) == before  # a draft writes nothing

        done = app.call("authoring.apply", {"source": VALE, "proposal": proposal["proposal"], "draft": draft["digest"]})
        assert done["done"]["already_applied"] is False and done["proposal"]["state"] == "applied"
        created = done["proposal"]["applied"]
        assert [(item["key"], item["ref"], item["name"]) for item in created["records"]] == [
            ("brinewick", str(brinewick), "Brinewick"), ("gullhaven", str(gullhaven), "Gullhaven")]
        after = canonical(device)
        assert {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)} == {
            record(brinewick), record(gullhaven), record(new_map), MANIFEST}
        # the same press retried after a lost answer writes nothing again
        again = app.call("authoring.apply", {"source": VALE, "proposal": proposal["proposal"], "draft": draft["digest"]})
        assert again["done"]["already_applied"] is True and canonical(device) == after
        assert refused(app, "authoring.review", source=VALE, proposal=proposal["proposal"]).reason == "already_applied"

        view = device.table.store.load()
        names = sorted(d.name for d in view.index.definitions() if d.ref.source == VALE and d.ref.kind != "topology")
        assert names == ["Brinewick", "Gullhaven", "Lamplighters Guild", "Port Lantern", "Saltmarch Coast"]
        graph = decode_topology(new_map, view.index.resolve(new_map).envelope.payload)
        assert [(path.source.entity_id, path.target.entity_id, path.bidirectional) for path in graph.paths] == [
            ("brinewick", "gullhaven", True)]
        parents = {place.ref.entity_id: place.parent and place.parent.entity_id for place in graph.places}
        assert parents["brinewick"] == "saltmarch-coast" and parents["gullhaven"] == "saltmarch-coast"
        assert "tidemoor" not in parents
        # neither the third town nor the brainstorm is stored anywhere in the world
        world = b"".join(data or b"" for path, data in after.items() if path.startswith("sources/world/vale/"))
        assert b"Tidemoor" not in world and b"bell tower" not in world and b"Lamplighters' Guild" not in world


def test_a_review_the_world_outran_refuses_and_the_proposal_is_reviewed_again(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        proposal = app.call("authoring.build", {"source": VALE, **towns()})["proposal"]
        # someone adds a town named Gullhaven to the world after the review
        store = device.table.store
        txn = store.transaction()
        stored = store.read_source(VALE)
        txn.put(entity(SourceRef(VALE, "location", "old-gullhaven", "r1"), "Gullhaven", spatial=True), None)
        txn.put(stored.manifest.__class__(VALE, stored.manifest.owner, stored.manifest.title,
                                          {**stored.manifest.heads, "location/old-gullhaven": "r1"}), stored.etag)
        txn.commit(device.table.owner)
        before = canonical(device)
        assert refused(app, "authoring.draft", source=VALE, proposal=proposal["proposal"],
                       accept=["brinewick", "gullhaven"]).reason == "stale_review"
        assert canonical(device) == before
        again = app.call("authoring.review", {"source": VALE, "proposal": proposal["proposal"]})["proposal"]
        assert [item["key"] for item in again["candidates"] if item["duplicates"]] == ["gullhaven", "lamplighters"]
        drafted = accept(app, again, "brinewick", "gullhaven", distinct=["gullhaven"])
        assert drafted["draft"]["distinct"] == ["gullhaven"]
        assert drafted["draft"]["records"]["gullhaven"] == str(SourceRef(VALE, "location", "gullhaven", "r1"))

        # a draft the world then outran is refused at apply, and nothing is written
        txn = store.transaction()
        stored = store.read_source(VALE)
        txn.put(entity(COAST.at("r2"), "Saltmarch Coast", spatial=True, description="Edited."), None)
        txn.put(stored.manifest.__class__(VALE, stored.manifest.owner, stored.manifest.title,
                                          {**stored.manifest.heads, "location/saltmarch-coast": "r2"}), stored.etag)
        txn.commit(device.table.owner)
        before = canonical(device)
        made = held(device).proposals[again["proposal"]].draft
        assert refused(app, "authoring.apply", source=VALE, proposal=again["proposal"],
                       draft=drafted["draft"]["digest"]).reason == "conflict"
        assert canonical(device) == before
        assert made.changes.state == "rejected", "the refused draft is ended in 043, its approvals revoked"
        problems = app.call("authoring.review", {"source": VALE, "proposal": again["proposal"]})["proposal"]
        assert {item["link"]: item["problem"] for item in problems["links"] if item["problem"]} == {
            "brinewick-in": "endpoint_moved", "gullhaven-in": "endpoint_moved", "tidemoor-in": "endpoint_moved"}


def test_rejecting_writes_nothing_and_ends_the_proposal(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        before = canonical(device)
        proposal = app.call("authoring.build", {"source": VALE, **towns()})["proposal"]
        drafted = accept(app, proposal, "tidemoor")
        done = app.call("authoring.reject", {"source": VALE, "proposal": proposal["proposal"]})["proposal"]
        assert done["state"] == "rejected" and done["draft"] is None
        assert refused(app, "authoring.apply", source=VALE, proposal=proposal["proposal"],
                       draft=drafted["draft"]["digest"]).reason == "rejected"
        assert canonical(device) == before


def test_what_the_page_sends_is_checked_as_087_checks_a_reply(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        before = canonical(device)
        built = towns()
        # a link may name only a pinned record the world offers: not one invented, nor one at another revision
        invented = {**built, "links": [{"id": "x", "relation": "road", "subject": "brinewick",
                                        "object": f"{VALE}/location/atlantis@r1"}]}
        assert refused(app, "authoring.build", source=VALE, **invented).reason == "unknown_record"
        moved = {**built, "links": [{"id": "x", "relation": "road", "subject": "brinewick", "object": str(COAST.at("r9"))}]}
        assert refused(app, "authoring.build", source=VALE, **moved).reason == "unknown_record"
        template = {"candidates": [{"key": "kind", "kind": "location", "name": "A fishing village", "role": "template",
                                    "description": ""}, built["candidates"][0]],
                    "links": [{"id": "x", "relation": "road", "subject": "brinewick", "object": "kind"}]}
        assert refused(app, "authoring.build", source=VALE, **template).reason == "template_endpoint"
        extra = {**built, "candidates": [{**built["candidates"][0], "origin": "model:planning"}]}
        assert refused(app, "authoring.build", source=VALE, **extra).reason == "invalid_shape"
        assert refused(app, "authoring.build", source=VALE, candidates="many").reason == "invalid_shape"
        # a world source only: a card's records would be carried by every world that includes it
        assert refused(app, "authoring.build", source=MARA_VELL.source, **built).reason == "not_a_world"
        assert refused(app, "authoring.source", source="world:nowhere").reason == "missing_source"
        assert refused(app, "authoring.review", source=VALE, proposal="hand-000").reason == "unknown_proposal"
        # the origin of what the page builds is the owner's, whatever it sends
        made = app.call("authoring.build", {"source": VALE, **built, "origin": "model:planning"})["proposal"]
        assert made["origin"] == "human:owner" and made["kind"] == "hand"
        assert canonical(device) == before


# -- 087: a suggestion behind an approved estimate --------------------------------------------------------------------
def test_the_setting_is_off_by_default_and_round_trips():
    raw = {"provider_id": "p", "base_url": "https://p.example/v1", "model": "m"}
    off = parse_model_config(raw)
    assert off.authoring_calls == 0 and "authoring" not in config_json(off)
    assert authoring_policy(off).caps.get("planning", 0) == 0
    on = parse_model_config({**raw, "authoring": 1})
    assert config_json(on)["authoring"] == 1 and authoring_policy(on).caps["planning"] == 1
    with pytest.raises(ValueError):
        parse_model_config({**raw, "authoring": 2})


def suggestion(*, extra=()) -> dict:
    """A model reply of the proving proposal (the guild's namesake and a brainstorm among three linked towns)."""
    body = towns()
    return {"candidates": body["candidates"] + list(extra), "links": body["links"], "rationale": body["rationale"]}


ASK = {"source": VALE, "request_id": "coast-1", "brief": "Three linked towns on the coast and a guild",
       "constraints": ["towns sit within the Saltmarch Coast"], "max_candidates": 8}


@pytest.mark.parametrize("model, reason", [(False, "no_model"), (True, "not_enabled")])
def test_nothing_is_sent_without_a_model_or_with_suggestions_switched_off(desk, tmp_path, model, reason):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        transport = configured(device, settings(tmp_path / "model.json", authoring=0) if model else None,
                               [reply(suggestion())])
        plan = app.call("authoring.suggest_plan", ASK)["plan"]
        assert plan["blocked"]["reason"] == reason and plan["call"]["calls"] == 1
        assert app.call("authoring.source", {"source": VALE})["suggest"]["blocked"]["reason"] == reason
        assert refused(app, "authoring.suggest", **ASK, expected=plan["expected"]).reason == reason
        assert not transport.sends


def test_a_suggestion_is_one_call_sent_only_as_estimated_and_approved(desk, tmp_path):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        ask = dict(ASK)
        transport = configured(device, settings(tmp_path / "model.json"), [reply(suggestion())])
        before = canonical(device)
        plan = app.call("authoring.suggest_plan", ask)["plan"]
        assert plan["blocked"] is None and plan["replay"] is False
        call = plan["call"]
        assert call["stage"] == "planning" and call["calls"] == 1 and call["cost_state"] == "estimated"
        assert call["reply_tokens_at_most"] == 900 and call["route"]
        assert plan["context"]["records"] == 3
        assert not transport.sends and canonical(device) == before
        assert refused(app, "authoring.suggest", **ask).reason == "review_required"
        assert refused(app, "authoring.suggest", **ask, expected="stale").reason == "changed"
        # an estimate for another brief is not this one's
        assert refused(app, "authoring.suggest", **{**ask, "brief": "Something else"},
                       expected=plan["expected"]).reason == "changed"
        assert not transport.sends

        traces = len(app.call("providers.diagnostics", {})["traces"])
        done = app.call("authoring.suggest", {**ask, "expected": plan["expected"]})
        assert done["done"] == {"request": "coast-1", "replay": False, "review_problem": None}
        assert len(transport.sends) == 1
        # one 048 trace of its own, like every other model call
        shown = app.call("providers.diagnostics", {})["traces"]
        assert len(shown) == traces + 1
        assert shown[0]["operation"] == "world_authoring" and shown[0]["status"] == "ok"
        assert [(call["stage"], call["calls"], call["attempts"]) for call in shown[0]["calls"]] == [("planning", 1, 1)]
        proposal = done["proposal"]
        assert proposal["origin"] == "model:planning" and proposal["kind"] == "model"
        assert proposal["usage"]["sent"] == 1
        sent = json.loads(transport.sends[0].body)
        data = json.loads(sent["messages"][-1]["content"])
        assert data["brief"] == ask["brief"] and {item["ref"] for item in data["records"]} == {
            str(COAST), str(LANTERN), str(GUILD)}
        # only the world's own names and kinds go: nothing of the library's chats, cards or other sources
        assert "Mara" not in json.dumps(sent) and "drowned" not in json.dumps(sent).lower()
        assert [item["key"] for item in proposal["candidates"] if item["duplicates"]] == ["lamplighters"]
        assert canonical(device) == before  # a suggestion is kept in memory only

        # the same request again answers what it gave and sends nothing; another brief under its id is refused
        replay = app.call("authoring.suggest_plan", ask)["plan"]
        assert replay["replay"] is True
        again = app.call("authoring.suggest", {**ask, "expected": replay["expected"]})
        assert again["done"]["replay"] is True and again["proposal"]["proposal"] == proposal["proposal"]
        assert len(transport.sends) == 1 and len(app.call("providers.diagnostics", {})["traces"]) == traces + 1
        assert refused(app, "authoring.suggest_plan", **{**ask, "brief": "Another"}).reason == "request_conflict"

        # the suggested proposal is accepted in part like any other
        drafted = accept(app, proposal, "brinewick", "gullhaven")
        app.call("authoring.apply", {"source": VALE, "proposal": proposal["proposal"],
                                     "draft": drafted["draft"]["digest"]})
        town = device.table.store.load().index.resolve(SourceRef(VALE, "location", "brinewick", "r1"))
        assert town.envelope.payload["authoring"]["suggested_by"] == "model:planning"
        assert town.envelope.payload["authoring"]["accepted_by"] == "human:owner"
        # Models and costs names the switch that allowed it
        stages = {item["stage"]: item for item in app.call("providers.models", {})["stages"]}
        assert stages["world_authoring"]["enabled"] is True


def test_a_reply_087_refuses_keeps_nothing_and_says_what_was_spent(desk, tmp_path):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        invented = {"candidates": [{"key": "a", "kind": "location", "name": "A", "role": "individual",
                                    "description": ""}],
                    "links": [{"id": "x", "relation": "road", "subject": "a", "object": f"{VALE}/location/nowhere@r1"}],
                    "rationale": ""}
        transport = configured(device, settings(tmp_path / "model.json"), [reply(invented), reply(suggestion())])
        ask = {"source": VALE, "request_id": "coast-2", "brief": "A town"}
        plan = app.call("authoring.suggest_plan", ask)["plan"]
        assert refused(app, "authoring.suggest", **ask, expected=plan["expected"]).reason == "unknown_record"
        assert len(transport.sends) == 1
        # the spent call is part of what the next approval is for: the old estimate is not approval again
        assert refused(app, "authoring.suggest", **ask, expected=plan["expected"]).reason == "changed"
        assert len(transport.sends) == 1
        shown = app.call("authoring.source", {"source": VALE})
        assert shown["proposals"] == []


def test_a_world_with_several_maps_names_the_one_a_draft_extends(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        seed_maps(device)
        app = AppApi(device.api)
        shown = app.call("authoring.source", {"source": VALE})
        assert shown["maps"] == [{"topology": "coast-map", "ref": str(COAST_MAP), "name": "Coast map"},
                                 {"topology": "sea-map", "ref": str(COAST_MAP).replace("coast-map", "sea-map"),
                                  "name": "Sea map"}]
        proposal = app.call("authoring.build", {"source": VALE, **towns()})["proposal"]
        before = canonical(device)
        args = {"source": VALE, "proposal": proposal["proposal"], "accept": ["brinewick", "gullhaven"]}
        assert refused(app, "authoring.draft", **args).reason == "ambiguous_topology"
        drafted = app.call("authoring.draft", {**args, "topology": "coast-map"})["proposal"]["draft"]
        assert drafted["topology"] == str(COAST_MAP.at("r2"))
        assert [item["change"] for item in drafted["files"] if "/topology/" in item["path"]] == ["revise"]
        assert canonical(device) == before
        done = app.call("authoring.apply", {"source": VALE, "proposal": proposal["proposal"],
                                            "draft": drafted["digest"]})["proposal"]["applied"]
        assert done["topology"] == str(COAST_MAP.at("r2"))
        graph = decode_topology(COAST_MAP.at("r2"),
                                device.table.store.load().index.resolve(COAST_MAP.at("r2")).envelope.payload)
        assert {place.ref.entity_id for place in graph.places} == {"saltmarch-coast", "port-lantern", "brinewick",
                                                                  "gullhaven"}


def test_an_estimate_approves_one_send_only_even_once_its_request_is_forgotten(desk, tmp_path, monkeypatch):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        monkeypatch.setattr(common, "MAX_REQUESTS", 1)
        invented = {"candidates": [{"key": "a", "kind": "location", "name": "A", "role": "individual",
                                    "description": ""}],
                    "links": [{"id": "x", "relation": "road", "subject": "a", "object": f"{VALE}/location/nowhere@r1"}],
                    "rationale": ""}
        transport = configured(device, settings(tmp_path / "model.json"), [reply(invented), reply(suggestion())])
        first = {"source": VALE, "request_id": "first", "brief": "A town"}
        token = app.call("authoring.suggest_plan", first)["plan"]["expected"]
        assert refused(app, "authoring.suggest", **first, expected=token).reason == "unknown_record"
        second = {"source": VALE, "request_id": "second", "brief": "Some towns"}
        app.call("authoring.suggest", {**second, "expected": app.call("authoring.suggest_plan", second)["plan"]["expected"]})
        assert len(transport.sends) == 2
        assert list(held(device).requests) == ["second"], "the first request is forgotten, with what it spent"
        # everything the first token covered is the same again, but the approval it carried was used
        assert app.call("authoring.suggest_plan", first)["plan"]["expected"] == token
        assert refused(app, "authoring.suggest", **first, expected=token).reason == "changed"
        assert len(transport.sends) == 2


def test_a_token_is_had_only_from_an_estimate(tmp_path):
    tokens = []
    for name in ("one", "two"):
        folder, _chat = desktop_library(tmp_path / name)
        with Device(folder) as device:
            seed_vale(device)
            configured(device, settings(tmp_path / (name + ".json")))
            tokens.append(AppApi(device.api).call("authoring.suggest_plan", dict(ASK))["plan"]["expected"])
    assert tokens[0] != tokens[1], "the same request in another library (or after a restart) has another token"


def test_a_paid_reply_is_kept_even_when_its_review_fails(desk, tmp_path, monkeypatch):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        transport = configured(device, settings(tmp_path / "model.json"), [reply(suggestion())])
        real = proposals._reviewed

        def failing(ctx, entry):  # type: ignore[no-untyped-def]
            raise proposals.ApiRefused("unreadable_topology", "the map cannot be read")

        monkeypatch.setattr(proposals, "_reviewed", failing)
        plan = app.call("authoring.suggest_plan", dict(ASK))["plan"]
        done = app.call("authoring.suggest", {**ASK, "expected": plan["expected"]})
        assert done["done"]["review_problem"] == "unreadable_topology" and done["proposal"]["reviewed"] is False
        assert len(transport.sends) == 1
        assert refused(app, "authoring.draft", source=VALE, proposal=done["proposal"]["proposal"],
                       accept=["brinewick"]).reason == "review_required"
        # the request answers the kept proposal, sending nothing; reviewed again, it is drafted as any other
        again = app.call("authoring.suggest", {**ASK, "expected": app.call("authoring.suggest_plan", dict(ASK))["plan"]["expected"]})
        assert again["done"]["replay"] is True and again["proposal"]["proposal"] == done["proposal"]["proposal"]
        monkeypatch.setattr(proposals, "_reviewed", real)
        reviewed = app.call("authoring.review", {"source": VALE, "proposal": done["proposal"]["proposal"]})["proposal"]
        assert reviewed["reviewed"] is True and len(transport.sends) == 1


# -- 082: a read-only audit ------------------------------------------------------------------------------------------
def test_the_audit_cites_what_it_read_writes_nothing_and_marks_stale_evidence(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        before = canonical(device)
        report = app.call("authoring.audit", {"source": VALE})["audit"]
        assert report["status"] == "finished" and report["written"] is False
        assert canonical(device) == before
        found = {item["category"]: item for item in report["findings"]}
        link = found["missing_link"]
        assert [(c["ref"], c["field"], c["quote"]) for c in link["citations"]] == [
            (str(LANTERN), "/payload/description", "Lamplighters Guild"), (str(GUILD), "/name", "Lamplighters Guild")]
        assert link["citations"][0]["name"] == "Port Lantern"
        assert {item["ref"] for item in report["read"]} == {str(COAST), str(LANTERN), str(GUILD)}
        assert report["unread"] == [] and report["rejected"] == []
        assert report["budget"] == {"max_steps": 400, "max_reads": 200, "max_findings": 50, "max_seconds": 20.0}

        fresh = app.call("authoring.audit_review", {"source": VALE, "audit": report["audit"]})["review"]
        assert fresh["stale"] == [] and canonical(device) == before
        # the cited town is edited: the finding is stale, with the change
        store = device.table.store
        txn = store.transaction()
        stored = store.read_source(VALE)
        txn.put(entity(LANTERN.at("r2"), "Port Lantern", spatial=True, description=HALL + " Edited."), None)
        txn.put(stored.manifest.__class__(VALE, stored.manifest.owner, stored.manifest.title,
                                          {**stored.manifest.heads, "location/port-lantern": "r2"}), stored.etag)
        txn.commit(device.table.owner)
        stale = app.call("authoring.audit_review", {"source": VALE, "audit": report["audit"]})["review"]
        assert stale["stale"] == [link["finding"]]
        changes = next(item for item in stale["findings"] if item["finding"] == link["finding"])["changes"]
        assert changes == [{"kind": "location", "entity_id": "port-lantern", "field": "/payload/description",
                            "reason": "head_moved", "revision": "r2"}]


def test_an_audit_is_bounded_and_only_of_a_world(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        short = app.call("authoring.audit", {"source": VALE, "max_reads": 1})["audit"]
        assert short["status"] == "budget_exhausted" and short["reason"] == "reads"
        assert len(short["read"]) == 1 and len(short["unread"]) == 2
        for bad in ({"max_steps": 0}, {"max_reads": 5000}, {"max_seconds": 0}, {"max_findings": "9"}):
            assert refused(app, "authoring.audit", source=VALE, **bad).reason == "invalid_field"
        assert refused(app, "authoring.audit", source=MARA_VELL.source).reason == "not_a_world"
        assert refused(app, "authoring.audit", source="world:nowhere").reason == "missing_source"
        # a report is named by the app's own id: one the app never made, or of another source, is not reviewed
        assert refused(app, "authoring.audit_review", source=VALE, audit="audit-forged").reason == "unknown_audit"
        assert refused(app, "authoring.audit_review", source="world:other", audit=short["audit"]).reason \
            == "unknown_audit"


# -- 079: source upgrades ----------------------------------------------------------------------------------------------
def test_removing_a_shared_source_lists_the_chats_reference_as_missing(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        before = canonical(device)
        shown = app.call("authoring.upgrades", {"source": MARA_VELL.source})
        removal = shown["removal"]
        assert removal["complete"] is True and removal["missing"] >= 1
        chats = [group for group in removal["files"] if group["record"] == "chat"]
        assert chats, removal["files"]
        texts = {ref["text"]: ref["status"] for group in chats for ref in group["references"]}
        assert any(text.startswith(str(MARA_VELL)) and status == "missing" for text, status in texts.items())
        assert any(item["ref"].startswith(str(MARA_VELL).rsplit("@", 1)[0]) and item["status"] == "removed"
                   for item in removal["entities"])
        assert shown["update"]["available"] is False and "103" in shown["update"]["reason"]
        assert shown["apply"]["available"] is False
        assert canonical(device) == before


def test_a_dependency_is_pinned_once_as_reviewed(desk):
    folder, _chat = desk
    with Device(folder) as device:
        seed_vale(device)
        app = AppApi(device.api)
        dependency = MARA_VELL.source
        shown = app.call("authoring.upgrades", {"source": VALE})["dependencies"]
        assert shown["declared"] == [] and dependency in shown["offered"] and VALE not in shown["offered"]
        etag = shown["etag"]
        before = canonical(device)
        plan = app.call("authoring.pin_plan", {"source": VALE, "dependency": dependency, "expected": etag})["plan"]
        assert plan["before"] == [] and plan["after"] == [{"source": dependency, "version": plan["version"]}]
        assert [item["path"] for item in plan["files"]] == [MANIFEST] and plan["files"][0]["action"] == "replace"
        assert '"optional_dependencies"' in plan["files"][0]["text"]
        assert canonical(device) == before
        assert refused(app, "authoring.pin_plan", source=VALE, dependency=dependency, expected="old").reason == "changed"
        assert refused(app, "authoring.pin_plan", source=VALE, dependency=VALE, expected=etag).reason \
            == "self_dependency"
        assert refused(app, "authoring.pin_plan", source=VALE, dependency="world:nowhere", expected=etag).reason \
            == "missing_dependency"
        assert refused(app, "authoring.pin", source=VALE, dependency=dependency, expected=etag).reason \
            == "review_required"
        args = {"source": VALE, "dependency": dependency, "expected": etag, "version": plan["version"]}
        done = app.call("authoring.pin", args)
        assert done["done"]["already_applied"] is False
        assert done["dependencies"]["declared"] == [{"source": dependency, "version": plan["version"],
                                                     "status": "pinned", "current": plan["version"]}]
        written = canonical(device)
        assert written[MANIFEST] == plan["files"][0]["text"].encode("utf-8")
        # the same press retried after a lost answer writes nothing again
        assert app.call("authoring.pin", args)["done"]["already_applied"] is True
        assert canonical(device) == written
        # an old review of another version is refused, and nothing is written
        assert refused(app, "authoring.pin", **{**args, "version": "sha256:" + "0" * 64}).reason == "changed"
        assert canonical(device) == written


# -- the page end to end -----------------------------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_authoring_page_end_to_end(desk, tmp_path):
    folder, chat = desk
    with Device(folder) as device:
        seed_vale(device)
        transport = configured(device, settings(tmp_path / "model.json"), [reply(suggestion())])
        app = AppServer(device.api)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = app.address
            env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
                   "GRIMOIRE_WORLD": VALE, "GRIMOIRE_TOWN": str(LANTERN), "GRIMOIRE_COAST": str(COAST),
                   "GRIMOIRE_SHARED": MARA_VELL.source, "GRIMOIRE_CHAT": str(chat)}
            result = subprocess.run([NODE, "--test", "--test-reporter=tap",
                                     str(Path(__file__).with_name("js") / "authoring.e2e.mjs")],
                                    capture_output=True, text=True, timeout=240, check=False, env=env)
        finally:
            app.close()
            thread.join(10)
        assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
        assert "# pass 1" in result.stdout
        # the page made exactly the one approved call
        assert len(transport.sends) == 1
        view = device.table.store.load()
        names = sorted(d.name for d in view.index.definitions() if d.ref.source == VALE and d.ref.kind != "topology")
        assert names == ["Brinewick", "Gullhaven", "Lamplighters Guild", "Port Lantern", "Saltmarch Coast"]
