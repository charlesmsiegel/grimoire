"""Absorb membership proposals end to end (spec 07 sections 9.2 to 9.6, 17.4).

Staged from a parsed reply against a host capture, saved through
`PUT /chronicle`, undone through the journal. Each row stands alone: a forged
row is skipped, a row whose group went away fails with its own reason, and
neither refuses the rest of the save.
"""

import json

import pytest

from grimoire import routes
from grimoire.store import (
    changes,
    characters,
    commits,
    entities,
    overlay,
    pcs,
    provenance,
    scenes,
    worlds,
)
from grimoire.store.absorb import materializer, parse, snapshots
from grimoire.store.absorb import membership as absorb_membership
from tests import review_runs
from tests.llm_fakes import decision_reply, from_entries


def _scene(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Mara")
    characters.create_character(wroot, "Seraphine")
    pcs.create_pc(wroot, "Winifred", [])
    for kind, aid, role in (("characters", "mara", "npc"), ("characters", "seraphine", "npc"),
                            ("pcs", "winifred", "player")):
        r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                        json={"kind": kind, "id": aid, "version": "default", "role": role})
        assert r.status_code == 200, r.text
    return wid, cid, sid, wroot


def _group(root, name="Salt Circle", **fields):
    entities.create_entity(root, "groups", name, "The gate.", fields=fields)


def _stage(cid, sid, rows):
    with absorb_membership.held(cid):
        cap = absorb_membership.capture(cid)
    parsed = parse.parse_output(json.dumps({"membership_changes": rows}))
    return [e for e in materializer.materialize(cid, sid, parsed, membership_capture=cap)
            if e["kind"] == "membership"]


def _cite(row):
    return {**row, "quote": "I swear myself to the Circle.", "speaker": "Mara", "certainty": 0.9}


def _save(client, cid, sid, edits):
    body = {"one_line": "o", "summary": "s", "keywords": [], "timeline_events": [], "edits": edits,
            "commit_token": commits.mint(commits.scene_epoch(cid, sid))}
    return client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json=body)


def _members(cid, gid="salt-circle"):
    return overlay.read_group_membership(cid, gid)[1].members


# --- staging (9.3) ---------------------------------------------------------

@pytest.mark.parametrize("actor", ["characters/mara", "characters:mara"])
def test_the_present_line_spelling_stages_like_the_ref(client, actor):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    [edit] = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": actor, "change": "join"})])
    assert edit["id"] == "membership:salt-circle:characters:mara"
    assert edit["label"] == "Salt Circle — Mara joins"
    assert edit["field"] == "members:characters:mara"
    assert (edit["before"], edit["after"]) == ("not a member", "member")
    assert edit["target"] == {"kind": "groups", "id": "salt-circle"}
    assert set(edit["payload"]) == {"group", "actor", "change", "group_lifecycle_token"}
    assert edit["payload"]["group"] == "salt-circle"


def test_staging_drops_noops_unknowns_and_repeats(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    _group(wroot, name="Hidden Hand", members="characters:mara", secrecy="gm-only")
    staged = _stage(cid, sid, [
        {"group": "groups/salt-circle", "actor": "characters/seraphine", "change": "join"},   # already
        {"group": "groups/salt-circle", "actor": "characters/mara", "change": "leave"},       # not in it
        {"group": "groups/nowhere", "actor": "characters/mara", "change": "join"},            # no group
        {"group": "groups/hidden-hand", "actor": "characters/mara", "change": "leave"},       # gm-only
        {"group": "groups/salt-circle", "actor": "characters/nobody", "change": "join"},      # no actor
        {"group": "groups/salt-circle", "actor": "locations/saltmarch", "change": "join"},    # not an actor
        {"group": "groups/salt-circle", "actor": "pcs/winifred", "change": "join"},           # stages
        {"group": "groups/salt-circle", "actor": "pcs/winifred", "change": "leave"},          # repeat
    ])
    assert [(e["payload"]["actor"], e["payload"]["change"]) for e in staged] == [("pcs:winifred", "join")]


def test_an_actor_not_on_the_present_line_is_not_proposed(client):
    """Spec 07 section 9.7: the transcript can give evidence only about the
    present cast, so a reply naming an absent actor stages nothing."""
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    characters.create_character(wroot, "Orrin")
    assert _stage(cid, sid, [{"group": "groups/salt-circle", "actor": "characters/orrin",
                              "change": "join"}]) == []


# --- the snapshot (9.2) ----------------------------------------------------

def test_snapshot_names_only_present_affiliates_leader_first(client):
    _wid, cid, _sid, wroot = _scene(client)
    _group(wroot, members="characters:mara, pcs:winifred, characters:seraphine", leader="characters:seraphine")
    _group(wroot, name="Tide Watch", members="pcs:winifred")
    snap = snapshots.group_snapshot(cid, ["characters/mara", "characters/seraphine"])
    lines = snap.splitlines()
    assert lines[0] == ("- groups/salt-circle (Salt Circle): (no state) | Affiliates here: "
                        "Seraphine (characters/seraphine, leader, member), Mara (characters/mara, member)")
    # No present affiliate: the line reads exactly as it did before membership.
    assert lines[1] == "- groups/tide-watch (Tide Watch): (no state)"
    assert "Winifred" not in snap and "winifred" not in snap


def test_snapshot_frames_secret_groups_apart_and_omits_gm_only(client):
    _wid, cid, _sid, wroot = _scene(client)
    _group(wroot, members="characters:mara")
    _group(wroot, name="Night Court", members="characters:mara", secrecy="secret")
    _group(wroot, name="Hidden Hand", members="characters:mara", secrecy="gm-only")
    lines = snapshots.group_snapshot(cid, ["characters/mara"]).splitlines()
    assert lines[0].startswith("- groups/salt-circle (Salt Circle)")
    assert lines[1:] == ["Secret groups:", snapshots.SECRET_GROUPS_PREFACE,
                         ("- groups/night-court (Night Court): (no state) | Affiliates here: "
                          "Mara (characters/mara, member)")]


def test_a_detached_member_is_not_named_here(client):
    """Spec 07 sections 7.6 and 17.2: an inherited roster's ref to an actor the
    campaign detached confers nothing, so the snapshot does not name them."""
    _wid, cid, _sid, wroot = _scene(client)
    _group(wroot, members="characters:mara")
    overlay.add_detached(cid, "characters/mara")
    assert snapshots.group_snapshot(cid, ["characters/mara"]) == "- groups/salt-circle (Salt Circle): (no state)"


def test_a_leader_alone_is_named_as_leader(client):
    _wid, cid, _sid, wroot = _scene(client)
    _group(wroot, leader="characters:seraphine")
    snap = snapshots.group_snapshot(cid, ["characters/seraphine"])
    assert snap.endswith("| Affiliates here: Seraphine (characters/seraphine, leader)")


# --- saving (9.4, 4.5) -----------------------------------------------------

def test_two_actors_on_one_group_land_independently_with_their_own_citations(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    staged = _stage(cid, sid, [
        _cite({"group": "groups/salt-circle", "actor": "characters/mara", "change": "join"}),
        _cite({"group": "groups/salt-circle", "actor": "characters/seraphine", "change": "leave"})])
    r = _save(client, cid, sid, staged)
    assert r.status_code == 200, r.text
    assert r.json()["failures"] == []
    assert _members(cid) == ("characters:mara",)
    assert {"groups/salt-circle#members:characters:mara",
            "groups/salt-circle#members:characters:seraphine"} <= set(provenance.read(cid))


def test_membership_never_enters_the_changes_log(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    staged = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": "characters/mara",
                                      "change": "join"})])
    assert _save(client, cid, sid, staged).status_code == 200
    assert "membership" not in changes.BROWSABLE_KINDS
    assert "members:" not in json.dumps(changes.read(cid))


def test_a_deleted_group_fails_its_row_and_the_rest_saves(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    _group(wroot, name="Tide Watch")
    staged = _stage(cid, sid, [
        _cite({"group": "groups/salt-circle", "actor": "characters/mara", "change": "join"}),
        _cite({"group": "groups/tide-watch", "actor": "characters/mara", "change": "join"})])
    entities.delete_entity(wroot, "groups", "salt-circle")
    r = _save(client, cid, sid, staged)
    assert r.status_code == 200, r.text
    [failure] = r.json()["failures"]
    assert failure["id"] == "membership:salt-circle:characters:mara"
    assert _members(cid, "tide-watch") == ("characters:mara",)


def test_a_forged_row_is_skipped_and_the_rest_saves(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    [good] = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": "characters/mara",
                                      "change": "join"})])
    forged = {**good, "id": "membership:salt-circle:characters:seraphine",
              "field": "members:characters:mara",
              "payload": {**good["payload"], "actor": "characters:seraphine", "change": "leave"},
              "before": "member", "after": "not a member"}
    r = _save(client, cid, sid, [forged, good])
    assert r.status_code == 200, r.text
    assert r.json()["failures"] == []
    assert _members(cid) == ("characters:seraphine", "characters:mara")


def test_a_lease_that_was_never_issued_fails_only_its_row(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    [good] = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": "characters/mara",
                                      "change": "join"})])
    stale = {**good, "id": "membership:salt-circle:pcs:winifred", "field": "members:pcs:winifred",
             "payload": {**good["payload"], "actor": "pcs:winifred", "group_lifecycle_token": "f" * 32}}
    r = _save(client, cid, sid, [stale, good])
    assert r.status_code == 200, r.text
    assert [f["id"] for f in r.json()["failures"]] == ["membership:salt-circle:pcs:winifred"]
    assert _members(cid) == ("characters:seraphine", "characters:mara")


def test_a_join_whose_actor_was_removed_fails_and_grants_no_preparation(client, monkeypatch):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    staged = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": "characters/mara",
                                      "change": "join"})])
    edits = absorb_membership.resumed_edits(staged, {}, {})
    assert absorb_membership.neutral_actors(cid, edits) == {}
    monkeypatch.setattr(absorb_membership, "_actor_exists", lambda cid, actor: False)
    bound = [{**staged[0], "_membership_target": {"ref": "groups:salt-circle"}}]
    assert absorb_membership.neutral_actors(cid, bound) == {}


def test_a_row_with_no_id_or_a_settled_shape_cannot_break_the_save(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    [good] = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": "characters/mara",
                                      "change": "join"})])
    no_id = {k: v for k, v in good.items() if k != "id"}
    noop = {**good, "id": "membership:salt-circle:characters:seraphine",
            "field": "members:characters:seraphine", "before": "member",
            "payload": {**good["payload"], "actor": "characters:seraphine"}}
    smuggled = {**good, "_membership_failed": "forged", "_membership_target": {"ref": "groups:x"}}
    r = _save(client, cid, sid, [noop, no_id])
    assert r.status_code == 200, r.text
    assert _members(cid) == ("characters:seraphine", "characters:mara")
    [row] = _journal_rows(client, cid)
    assert client.post(f"/api/campaigns/{cid}/journal/{row['id']}/undo").status_code == 200
    r = _save(client, cid, sid, [smuggled])
    assert r.status_code == 200, r.text
    assert r.json()["failures"] == [] and _members(cid) == ("characters:seraphine", "characters:mara")


# --- undo (9.5) ------------------------------------------------------------

def _journal_rows(client, cid):
    return [row for row in client.get(f"/api/campaigns/{cid}/journal").json()
            if row.get("kind") == "membership"]


def test_undo_is_per_actor_and_keeps_the_other_actors_change(client):
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine")
    staged = _stage(cid, sid, [
        _cite({"group": "groups/salt-circle", "actor": "characters/mara", "change": "join"}),
        _cite({"group": "groups/salt-circle", "actor": "characters/seraphine", "change": "leave"})])
    assert _save(client, cid, sid, staged).status_code == 200
    rows = {row["field"]: row for row in _journal_rows(client, cid)}
    assert set(rows) == {"members:characters:mara", "members:characters:seraphine"}
    assert all(row["undoable"] for row in rows.values())
    r = client.post(f"/api/campaigns/{cid}/journal/{rows['members:characters:mara']['id']}/undo")
    assert r.status_code == 200, r.text
    assert _members(cid) == ()          # Mara's join undone; Seraphine's leave stands
    r = client.post(f"/api/campaigns/{cid}/journal/{rows['members:characters:seraphine']['id']}/undo")
    assert r.status_code == 200, r.text
    assert _members(cid) == ("characters:seraphine",)


# --- the whole route path: absorb proposes, the review saves (17.4) ----------

def test_an_absorbed_join_is_proposed_saved_and_undone_through_the_routes(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Saltmarch", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara", "version_name": "main"})
    _group(worlds.world_root(wid))
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "The Pier"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": "mara", "version": "main", "role": "npc"})
    scenes.append_message(cid, sid, "user", "I swear myself to the Circle.", speaker="Mara")
    client.put("/api/llm-connections/openrouter", json={"api_key": "sk-or-x"})
    reply = json.dumps({"one_line": "Mara joined.", "summary": "Mara joined the Circle.",
                        "keywords": [], "timeline_events": [], "membership_changes": [
                            {"group": "groups/salt-circle", "actor": "characters/mara", "change": "join",
                             "quote": "I swear myself to the Circle.", "speaker": "Mara",
                             "certainty": 1.0}]})
    def fake():
        return from_entries([
            {"when": {"system_contains": "You are absorbing a completed role-play scene"}, "reply": reply},
            {"when": {"system_contains": "You are updating a game master's dossier"}, "reply": "Steady."},
            {"when": {"system_contains": "You answer closed questions about material you are given.",
                      "user_contains": "Voice anchor:"}, "reply": decision_reply({"verdict": "in_voice"})},
            {"when": {"system_contains": "You are auditing a completed role-play scene"},
             "reply": '{"warnings": [], "sheet_deltas": []}'}])

    client.app.dependency_overrides[routes.get_llm] = fake
    started = client.post(f"/api/campaigns/{cid}/scenes/{sid}/absorb")
    assert started.status_code == 202, started.json()
    run = review_runs.wait_for_run(client, cid, sid, started.json()["run"]["id"])
    assert run["state"] == "landed", run
    review = client.get(f"/api/campaigns/{cid}/scenes/{sid}/pending-review").json()["review"]
    [edit] = [e for e in review["edits"] if e["kind"] == "membership"]
    assert edit["label"] == "Salt Circle — Mara joins"
    assert edit["payload"]["actor"] == "characters:mara"
    body = {k: review[k] for k in ("one_line", "summary", "keywords", "timeline_events", "commit_token")}
    r = client.put(f"/api/campaigns/{cid}/scenes/{sid}/chronicle", json={**body, "edits": [edit]})
    assert r.status_code == 200, r.text
    assert r.json()["failures"] == []
    assert _members(cid) == ("characters:mara",)
    assert client.get(f"/api/campaigns/{cid}/membership", params={"actor": "characters:mara"}).json()[
        "groups"][0]["ref"] == "groups:salt-circle"
    [row] = _journal_rows(client, cid)
    assert client.post(f"/api/campaigns/{cid}/journal/{row['id']}/undo").status_code == 200
    assert _members(cid) == ()


def test_undoing_an_ordinary_leave_revalidates_the_world_actor(client):
    """A leave approved a removal, not a binding: if the World actor it named
    is gone by the time of Undo, Undo refuses rather than restore a ref that
    names nobody (spec 07 sections 6.4 and 9.5)."""
    _wid, cid, sid, wroot = _scene(client)
    _group(wroot, members="characters:seraphine, characters:mara")
    staged = _stage(cid, sid, [_cite({"group": "groups/salt-circle", "actor": "characters/seraphine",
                                      "change": "leave"})])
    assert _save(client, cid, sid, staged).status_code == 200
    assert _members(cid) == ("characters:mara",)
    characters.delete_character(wroot, "seraphine")
    [row] = _journal_rows(client, cid)
    assert client.post(f"/api/campaigns/{cid}/journal/{row['id']}/undo").status_code == 409
    assert _members(cid) == ("characters:mara",)
