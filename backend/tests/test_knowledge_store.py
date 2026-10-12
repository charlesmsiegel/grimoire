"""`store/knowledge.py`: the knowledge overrides of roadmap 11 (§7).

The file format and its tolerance, the fingerprints that make a grant stop
when its text changes, the precedence of §7.2, group expansion through 07's
`affiliated`, and the lifecycle hooks: actor and group deletes prune, a
branch copies and a discarded branch drops, undo restores.
"""

from __future__ import annotations

import json
import uuid

import pytest

from grimoire.store import (
    branch,
    campaigns,
    characters,
    group_refs,
    journal,
    knowledge,
    knowledge_scene,
    overlay,
    responses,
    scenes,
    undo,
    worlds,
)


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Saltmarch", wid)


def _hex() -> str:
    return uuid.uuid4().hex


SCENE = "a" * 32
OTHER = "b" * 32


def _posts_subject(*posts, scene=SCENE):
    return {"kind": "posts", "scene": scene,
            "posts": [{"key": k, "part": "", "variant": v, "digest": d} for k, v, d in posts]}


def _entry(audience="characters:mara", status="knows", subject=None, name="Mara", **kw):
    return {"audience": audience, "audience_name": name, "status": status,
            "subject": subject or {"kind": "scene", "scene": SCENE, "digest": "0" * 64},
            "note": "", "source": "manual", **kw}


# -------------------------------------------------------------- file and shape


def test_a_missing_file_is_empty_and_intact(cid):
    assert knowledge.read(cid) == {}
    assert knowledge.read_checked(cid) == ({}, True)


def test_an_unparseable_file_reads_as_empty_but_not_intact(cid):
    knowledge._path(cid).write_text("{not json", encoding="utf-8")
    assert knowledge.read_checked(cid) == ({}, False)


def test_a_malformed_entry_is_skipped_and_the_read_is_not_intact(cid):
    good = knowledge.add(cid, _entry())
    doc = json.loads(knowledge._path(cid).read_text(encoding="utf-8"))
    doc["entries"]["k-deadbeef"] = {"audience": "nobody", "status": "unaware"}
    knowledge._path(cid).write_text(json.dumps(doc), encoding="utf-8")
    entries, intact = knowledge.read_checked(cid)
    assert list(entries) == [good] and not intact


@pytest.mark.parametrize("bad", [
    {"audience": "pcs:winifred"},
    {"audience": "characters:../x"},
    {"status": "maybe"},
    {"source": "model"},
    {"subject": {"kind": "scene", "scene": "short", "digest": "0" * 64}},
    {"subject": {"kind": "scene", "scene": SCENE, "digest": "0"}},
    {"subject": {"kind": "posts", "scene": SCENE, "posts": []}},
    {"subject": _posts_subject(("x-1", "", "0" * 16))},
    {"subject": _posts_subject(("p-1", "", "0" * 15))},
    {"subject": _posts_subject(("p-1", "", "0" * 16), ("p-1", "", "1" * 16))},
    {"note": 3},
    {"audience_name": ""},
    {"audience": "groups:watch", "audience_name": "  "},
])
def test_add_refuses_an_entry_of_the_wrong_shape(cid, bad):
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.add(cid, {**_entry(), **bad})
    assert not knowledge._path(cid).exists()


def test_add_mints_an_id_and_stamps_created(cid):
    kid = knowledge.add(cid, _entry(audience="*", name="ignored"))
    assert kid.startswith("k-") and len(kid) == 10
    got = knowledge.get(cid, kid)
    assert got["audience_name"] == "" and got["created"] and "updated" not in got


def test_mutators_refuse_a_file_of_the_wrong_shape_without_writing_over_it(cid):
    knowledge._path(cid).write_text("[]", encoding="utf-8")
    for call in (lambda: knowledge.add(cid, _entry()),
                 lambda: knowledge.remove(cid, "k-00000000"),
                 lambda: knowledge.restore(cid, "k-00000000", None)):
        with pytest.raises(knowledge.KnowledgeError):
            call()
    assert knowledge._path(cid).read_text(encoding="utf-8") == "[]"


def test_an_unrelated_write_keeps_a_malformed_entry_verbatim(cid):
    knowledge.add(cid, _entry())
    doc = json.loads(knowledge._path(cid).read_text(encoding="utf-8"))
    doc["entries"]["k-deadbeef"] = {"audience": "nobody"}
    knowledge._path(cid).write_text(json.dumps(doc), encoding="utf-8")
    knowledge.add(cid, _entry(status="unaware"))
    raw = json.loads(knowledge._path(cid).read_text(encoding="utf-8"))
    assert raw["entries"]["k-deadbeef"] == {"audience": "nobody"}


def test_update_keeps_created_and_stamps_updated(cid):
    kid = knowledge.add(cid, _entry(created="2026-01-01T00:00:00Z"))
    knowledge.update(cid, kid, _entry(status="suspects", note="Overheard."))
    got = knowledge.get(cid, kid)
    assert got["status"] == "suspects" and got["note"] == "Overheard."
    assert got["created"] == "2026-01-01T00:00:00Z" and got["updated"]
    with pytest.raises(KeyError):
        knowledge.update(cid, "k-00000000", _entry())


def test_remove_and_restore_round_trip(cid):
    kid = knowledge.add(cid, _entry())
    held = knowledge.get(cid, kid)
    assert knowledge.remove(cid, kid) == held
    assert knowledge.remove(cid, kid) is None
    knowledge.restore(cid, kid, held)
    assert knowledge.get(cid, kid) == held
    knowledge.restore(cid, kid, None)
    assert knowledge.get(cid, kid) is None


# ------------------------------------------------------------------ precedence


def _aud(**names):
    return knowledge.Audiences(names={"characters:mara": "Mara",
                                      "characters:winifred": "Winifred", **names})


def _facts(*posts, scene_digest="0" * 64):
    return knowledge.SceneFacts(
        posts={(k, ""): (v, d, False) for k, v, d in posts}, digest=scene_digest)


POST = ("r-" + "1" * 32, "v1", "1" * 16)


def _decide(entries, *, actor="characters:mara", post=(POST[0], ""), author=None,
            facts=None, aud=None):
    return knowledge.decide(entries, actor=actor, scene=SCENE, post=post, author=author,
                            facts=facts or _facts(POST), audiences=aud or _aud())


def _as_entries(*entries):
    return {f"k-{i:08x}": knowledge.normalize({"created": "2026-01-01T00:00:00Z", **e})
            for i, e in enumerate(entries)}


def test_a_post_unaware_beats_a_group_whole_scene_knows():
    aud = knowledge.Audiences(names={"characters:mara": "Mara"},
                              groups={"groups:watch": ("The Watch", ("characters:mara",))})
    entries = _as_entries(
        _entry(audience="groups:watch", name="The Watch"),
        _entry(audience="*", status="unaware", subject=_posts_subject(POST)))
    got = _decide(entries, aud=aud)
    assert (got.status, got.basis) == ("unaware", "override_all")


def test_actor_beats_group_beats_everyone():
    aud = knowledge.Audiences(names={"characters:mara": "Mara"},
                              groups={"groups:watch": ("The Watch", ("characters:mara",))})
    everyone = _entry(audience="*", status="unaware")
    group = _entry(audience="groups:watch", name="The Watch", status="suspects")
    actor = _entry(status="knows")
    assert _decide(_as_entries(everyone, group, actor), aud=aud).basis == "override"
    assert _decide(_as_entries(everyone, group), aud=aud).basis == "override_group"
    assert _decide(_as_entries(everyone), aud=aud).basis == "override_all"


def test_the_newest_entry_wins_within_one_rank():
    entries = _as_entries(_entry(status="unaware", created="2026-01-01T00:00:00Z"),
                          _entry(status="knows", created="2026-02-01T00:00:00Z"))
    assert _decide(entries).status == "knows"


def test_an_exact_tie_breaks_toward_withholding():
    entries = _as_entries(_entry(status="knows"), _entry(status="suspects"),
                          _entry(status="unaware"))
    assert _decide(entries).status == "unaware"
    assert _decide(_as_entries(_entry(status="knows"), _entry(status="suspects"))).status \
        == "suspects"


def test_everyone_skips_the_posts_author():
    entries = _as_entries(_entry(audience="*", status="unaware", subject=_posts_subject(POST)))
    assert _decide(entries, author="characters:mara") is None
    assert _decide(entries, actor="characters:winifred", author="characters:mara").status \
        == "unaware"


def test_the_summary_part_sees_only_whole_scene_entries():
    entries = _as_entries(_entry(subject=_posts_subject(POST)))
    assert _decide(entries, post=None) is None
    assert _decide(_as_entries(_entry()), post=None).status == "knows"


def test_a_keyless_post_sees_only_whole_scene_entries():
    entries = _as_entries(_entry(subject=_posts_subject(POST)), _entry(status="suspects"))
    assert _decide(entries, post=("", "")).status == "suspects"


def test_another_scenes_entry_never_matches():
    entries = _as_entries(_entry(subject={"kind": "scene", "scene": OTHER, "digest": "0" * 64}))
    assert _decide(entries) is None


def test_a_grant_whose_audience_was_renamed_does_not_match_but_unaware_does():
    renamed = _aud(**{"characters:mara": "Mara Vance"})
    assert _decide(_as_entries(_entry()), aud=renamed) is None
    assert _decide(_as_entries(_entry(status="unaware")), aud=renamed).status == "unaware"
    gone = knowledge.Audiences()
    assert _decide(_as_entries(_entry()), aud=gone) is None


def test_a_grant_whose_fingerprint_fails_does_not_match_but_unaware_does():
    stale = _facts(POST, scene_digest="9" * 64)
    assert _decide(_as_entries(_entry()), facts=stale) is None
    assert _decide(_as_entries(_entry(status="unaware")), facts=stale).status == "unaware"
    assert knowledge.decide(_as_entries(_entry()), actor="characters:mara", scene=SCENE,
                            post=None, author=None, facts=None, audiences=_aud()) is None


# ------------------------------------------------------------ real fingerprints


def _reply(cid, sid, content, rid=None):
    """A complete model reply, through the response ledger like a turn."""
    rnd = responses.new_round(cid, sid, eligible=[], automatic=False, post=None,
                              run_id="test")
    record = responses.prepare(cid, sid, rnd["id"], "characters:mara", "Mara", {})
    responses.save_variant(cid, sid, record["id"], content, "complete")
    return record["id"]


def _scene(cid):
    sid = scenes.create_scene(cid, "Pier")
    scenes.append_message(cid, sid, "user", "Seraphine waits.", post_id=_hex())
    rid = _reply(cid, sid, "Mara whispers the code.")
    return sid, rid


def _index(cid, sid, rid):
    return next(i for i, m in enumerate(scenes.read_scene(cid, sid)["messages"])
                if m.get("response_id") == rid)


def _stamped(cid, sid, rid, status="knows"):
    """A post entry and a scene entry, each fingerprinted now."""
    token, messages, facts = knowledge_scene.current(cid, sid)
    variants = responses.variants_by_response(cid, sid)
    m = messages[_index(cid, sid, rid)]
    post = {"kind": "posts", "scene": token, "posts": [knowledge.fingerprint(m, variants)]}
    whole = {"kind": "scene", "scene": token, "digest": facts.digest}
    return token, post, whole


def _holds(cid, sid, subject, status="knows"):
    _token, _messages, facts = knowledge_scene.current(cid, sid)
    return knowledge.holds(knowledge.normalize(_entry(status=status, subject=subject)), facts)


def test_a_fresh_fingerprint_holds(cid):
    sid, rid = _scene(cid)
    _token, post, whole = _stamped(cid, sid, rid)
    assert _holds(cid, sid, post) and _holds(cid, sid, whole)


def test_a_swipe_stops_a_post_grant(cid):
    sid, rid = _scene(cid)
    _token, post, whole = _stamped(cid, sid, rid)
    first = responses.get(cid, sid, rid)["active_variant"]
    responses.save_variant(cid, sid, rid, "Mara says nothing.", "complete")
    assert not _holds(cid, sid, post) and not _holds(cid, sid, whole)
    # Swiping back restores the text the user judged, so the grant holds again.
    responses.activate(cid, sid, rid, first)
    assert _holds(cid, sid, post)


def test_a_swipe_to_identical_text_still_stops_a_post_grant(cid):
    sid, rid = _scene(cid)
    _token, post, _whole = _stamped(cid, sid, rid)
    responses.save_variant(cid, sid, rid, "Mara whispers the code.", "complete")
    assert not _holds(cid, sid, post)


def test_an_edit_stops_both_grants(cid):
    sid, rid = _scene(cid)
    _token, post, whole = _stamped(cid, sid, rid)
    scenes.edit_message(cid, sid, _index(cid, sid, rid), "Mara whispers a lie.")
    assert not _holds(cid, sid, post) and not _holds(cid, sid, whole)


def test_keep_writing_stops_a_post_grant(cid):
    """Keep writing saves the continued reply as a new variant of the same
    response, so the variant moves and the text grows."""
    sid, rid = _scene(cid)
    _token, post, _whole = _stamped(cid, sid, rid)
    responses.save_variant(cid, sid, rid, "Mara whispers the code. Then she runs.", "complete")
    assert not _holds(cid, sid, post)


def test_a_hide_stops_both_grants(cid):
    sid, rid = _scene(cid)
    _token, post, whole = _stamped(cid, sid, rid)
    scenes.set_excluded(cid, sid, _index(cid, sid, rid), True)
    assert not _holds(cid, sid, post) and not _holds(cid, sid, whole)


def test_nothing_stops_an_unaware(cid):
    sid, rid = _scene(cid)
    token, post, whole = _stamped(cid, sid, rid)
    responses.save_variant(cid, sid, rid, "Mara says nothing.", "complete")
    scenes.edit_message(cid, sid, _index(cid, sid, rid), "Edited.")
    scenes.set_excluded(cid, sid, _index(cid, sid, rid), True)
    _token, _messages, facts = knowledge_scene.current(cid, sid)
    for subject in (post, whole):
        entries = _as_entries(_entry(status="unaware", subject=subject))
        key = (post["posts"][0]["key"], "")
        got = knowledge.decide(entries, actor="characters:mara", scene=token, post=key,
                               author=None, facts=facts, audiences=_aud())
        assert got.status == "unaware"


# --------------------------------------------------------------- group audience


def _group(cid, *, leader="", members=""):
    return overlay.create_entity(cid, "groups", "The Saltmarch Watch",
                                 fields={"leader": leader, "members": members})


def _actors(cid):
    for name in ("Mara", "Winifred", "Seraphine"):
        overlay.create_character(cid, name)


def _group_decide(cid, actor, entry):
    aud = knowledge_scene.audiences_for(cid)
    return knowledge.decide(_as_entries(entry), actor=actor, scene=SCENE, post=(POST[0], ""),
                            author=None, facts=_facts(POST), audiences=aud)


def test_a_group_audience_reaches_members_and_the_leader_and_no_one_else(cid):
    _actors(cid)
    gid = _group(cid, leader="characters:winifred", members="characters:mara")
    entry = _entry(audience=f"groups:{gid}", name="The Saltmarch Watch")
    assert _group_decide(cid, "characters:mara", entry).basis == "override_group"
    assert _group_decide(cid, "characters:winifred", entry).basis == "override_group"
    assert _group_decide(cid, "characters:seraphine", entry) is None


def test_a_member_added_later_is_covered(cid):
    _actors(cid)
    gid = _group(cid, members="characters:mara")
    entry = _entry(audience=f"groups:{gid}", name="The Saltmarch Watch")
    assert _group_decide(cid, "characters:seraphine", entry) is None
    overlay.update_entity(cid, "groups", gid,
                          fields={"members": "characters:mara, characters:seraphine"})
    assert _group_decide(cid, "characters:seraphine", entry) is not None


def test_a_renamed_group_stops_granting_but_still_withholds(cid):
    _actors(cid)
    gid = _group(cid, members="characters:mara")
    overlay.update_entity(cid, "groups", gid, name="The Tidewatch")
    grant = _entry(audience=f"groups:{gid}", name="The Saltmarch Watch")
    assert _group_decide(cid, "characters:mara", grant) is None
    assert _group_decide(cid, "characters:mara", {**grant, "status": "unaware"}).status \
        == "unaware"


def test_a_group_that_no_longer_resolves_expands_to_nobody(cid):
    _actors(cid)
    entry = _entry(audience="groups:nowhere", name="Nowhere")
    assert _group_decide(cid, "characters:mara", entry) is None
    assert _group_decide(cid, "characters:mara", {**entry, "status": "unaware"}) is None


# ------------------------------------------------------------------ lifecycle


def test_a_campaign_actor_delete_prunes_its_entries_and_journals_them(cid):
    aid, _vid = overlay.create_character(cid, "Mara")
    mine = knowledge.add(cid, _entry(audience=f"characters:{aid}"))
    other = knowledge.add(cid, _entry(audience="*", status="unaware"))
    overlay.delete_actor(cid, "characters", aid)
    assert set(knowledge.read(cid)) == {other}
    row = journal.read(cid)[-1]
    assert row["kind"] == "knowledge" and row["ref"]["id"] == mine
    assert row["undo"] is None and row["why"] == knowledge.FORGOTTEN_WHY


def test_a_campaign_group_delete_prunes_its_entries(cid):
    gid = _group(cid)
    knowledge.add(cid, _entry(audience=f"groups:{gid}", name="The Saltmarch Watch"))
    overlay.delete_entity(cid, "groups", gid)
    assert knowledge.read(cid) == {}


def test_a_world_delete_prunes_an_inherited_actors_entries(cid):
    wroot = overlay.wroot_of(cid)
    aid, _vid = characters.create_character(wroot, "Winifred")
    knowledge.add(cid, _entry(audience=f"characters:{aid}", name="Winifred"))
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert knowledge.read(cid) == {}


def test_a_world_delete_keeps_the_entries_of_a_campaign_that_kept_its_copy(cid):
    wroot = overlay.wroot_of(cid)
    aid, _vid = characters.create_character(wroot, "Winifred")
    overlay.materialize_actor(cid, "characters", aid)
    kid = knowledge.add(cid, _entry(audience=f"characters:{aid}", name="Winifred"))
    characters.delete_character(wroot, aid)
    overlay.forget_world_record(wroot, "characters", aid)
    assert set(knowledge.read(cid)) == {kid}


def test_undo_restores_a_journalled_removal(cid):
    kid = knowledge.add(cid, _entry())
    held = knowledge.get(cid, kid)
    with undo.journalled(cid, {"w": "knowledge", "id": kid}, kind="knowledge",
                         ref={"kind": "knowledge", "id": kid}, field="status",
                         label="Mara — knowledge removed"):
        knowledge.remove(cid, kid)
    assert knowledge.get(cid, kid) is None
    entry = journal.read(cid)[-1]
    undo.undo(cid, entry["id"])
    assert knowledge.get(cid, kid) == held


def test_a_branch_copies_post_entries_rekeyed_and_skips_the_rest(cid):
    sid = scenes.create_scene(cid, "Pier")
    pid = _hex()
    scenes.append_message(cid, sid, "user", "Seraphine waits.", post_id=pid)
    rid = _reply(cid, sid, "Mara whispers the code.")
    later = _reply(cid, sid, "Mara leaves.")
    token, post, whole = _stamped(cid, sid, rid)
    _t, late_post, _w = _stamped(cid, sid, later)
    kept = knowledge.add(cid, _entry(subject=post))
    knowledge.add(cid, _entry(subject=whole))
    knowledge.add(cid, _entry(subject=late_post))
    new = branch.branch_scene(cid, sid, _index(cid, sid, rid))
    new_token = scenes.ensure_identity(cid, new)
    copied = [e for e in knowledge.read(cid).values() if e["subject"]["scene"] == new_token]
    assert len(copied) == 1
    new_rid = scenes.read_scene(cid, new)["messages"][_index(cid, sid, rid)]["response_id"]
    assert copied[0]["subject"]["posts"][0]["key"] == f"r-{new_rid}" != post["posts"][0]["key"]
    assert copied[0]["subject"]["posts"][0]["variant"] == post["posts"][0]["variant"]
    # The copy holds in the branch, as the original does in its source.
    _tk, _ms, facts = knowledge_scene.current(cid, new)
    assert knowledge.holds(copied[0], facts)
    assert knowledge.get(cid, kept)["subject"]["scene"] == token


def test_a_discarded_branch_drops_its_entries(cid):
    sid, rid = _scene(cid)
    _token, post, _whole = _stamped(cid, sid, rid)
    knowledge.add(cid, _entry(subject=post))
    new = branch.branch_scene(cid, sid, _index(cid, sid, rid))
    assert len(knowledge.read(cid)) == 2
    branch.discard(cid, new)
    assert len(knowledge.read(cid)) == 1


# ------------------------------------------------------ review-driven cases


def test_a_file_of_an_unknown_version_reads_as_nothing_and_refuses_writes(cid):
    knowledge.add(cid, _entry())
    doc = json.loads(knowledge._path(cid).read_text(encoding="utf-8"))
    doc["version"] = 2
    knowledge._path(cid).write_text(json.dumps(doc), encoding="utf-8")
    assert knowledge.read_checked(cid) == ({}, False)
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.add(cid, _entry())


def test_a_key_two_posts_share_holds_nothing():
    m = {"role": "user", "content": "Same.", "post_id": "1" * 32}
    facts = knowledge_scene.scene_facts([m, dict(m)], {})
    entry = knowledge.normalize(_entry(subject=_posts_subject(
        ("p-" + "1" * 32, "", knowledge.digest("Same.")))))
    assert not knowledge.holds(entry, facts)
    assert knowledge.holds(entry, knowledge_scene.scene_facts([m], {}))


def test_strict_get_raises_where_the_tolerant_one_reads_none(cid):
    knowledge._path(cid).write_text("{not json", encoding="utf-8")
    assert knowledge.get(cid, "k-00000000") is None
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.get_strict(cid, "k-00000000")
    with pytest.raises(group_refs.GroupIdentityConflict):
        knowledge.require_readable(cid)


def test_an_actor_delete_refuses_before_removing_anything_when_the_file_is_unreadable(cid):
    aid, _vid = overlay.create_character(cid, "Mara")
    knowledge._path(cid).write_text("{not json", encoding="utf-8")
    with pytest.raises(group_refs.GroupIdentityConflict):
        overlay.delete_actor(cid, "characters", aid)
    assert any(r["id"] == aid for r in overlay.list_characters(cid))


def test_an_actor_delete_retires_an_undo_that_would_restore_its_statement(cid):
    """Remove a statement by hand, delete the actor, recreate the name: the
    old Undo must not grant the newcomer what was said of the first."""
    aid, _vid = overlay.create_character(cid, "Mara")
    kid = knowledge.add(cid, _entry(audience=f"characters:{aid}"))
    with undo.journalled(cid, {"w": "knowledge", "id": kid}, kind="knowledge",
                         ref={"kind": "knowledge", "id": kid}, field="status",
                         label="Mara — knowledge removed"):
        knowledge.remove(cid, kid)
    offer = journal.read(cid)[-1]
    assert offer["undo"] is not None
    overlay.delete_actor(cid, "characters", aid)
    again, _vid = overlay.create_character(cid, "Mara")
    assert again == aid
    retired = journal.get(cid, offer["id"])
    assert retired["undo"] is None and retired["why"] == knowledge.FORGOTTEN_WHY
    with pytest.raises(undo.NotUndoable):
        undo.undo(cid, offer["id"])
    assert knowledge.read(cid) == {}


def test_a_reclassified_group_expands_to_nobody(cid):
    _actors(cid)
    gid = _group(cid, members="characters:mara")
    entry = _entry(audience=f"groups:{gid}", name="The Saltmarch Watch")
    assert _group_decide(cid, "characters:mara", entry) is not None
    overlay.reclassify_entity(cid, "groups", gid, "locations")
    assert _group_decide(cid, "characters:mara", entry) is None
    assert _group_decide(cid, "characters:mara", {**entry, "status": "unaware"}) is None


def test_a_reclassified_groups_grants_do_not_pass_to_a_new_group_of_its_name(cid):
    """The id is free once the group moves, so a new group of the same name
    takes it -- the name backstop cannot tell them apart, so the prune must."""
    _actors(cid)
    gid = _group(cid, members="characters:mara")
    knowledge.add(cid, _entry(audience=f"groups:{gid}", name="The Saltmarch Watch"))
    overlay.reclassify_entity(cid, "groups", gid, "locations")
    assert knowledge.read(cid) == {}
    again = _group(cid, members="characters:seraphine")
    assert again == gid


def test_a_branch_refuses_before_writing_when_the_file_is_unreadable(cid):
    sid, rid = _scene(cid)
    knowledge._path(cid).write_text("{not json", encoding="utf-8")
    before = {s["id"] for s in scenes.list_scenes(cid)}
    with pytest.raises(group_refs.GroupIdentityConflict):
        branch.branch_scene(cid, sid, _index(cid, sid, rid))
    assert {s["id"] for s in scenes.list_scenes(cid)} == before


def test_a_branch_carries_withholding_narrowed_to_the_kept_posts(cid):
    sid = scenes.create_scene(cid, "Pier")
    rid = _reply(cid, sid, "Mara whispers the code.")
    later = _reply(cid, sid, "Mara leaves.")
    _token, post, whole = _stamped(cid, sid, rid)
    _t, late_post, _w = _stamped(cid, sid, later)
    both = {**post, "posts": post["posts"] + late_post["posts"]}
    knowledge.add(cid, _entry(audience="*", status="unaware", subject=both))
    knowledge.add(cid, _entry(audience="*", status="unaware", subject=whole))
    knowledge.add(cid, _entry(status="knows", subject=both))
    new = branch.branch_scene(cid, sid, _index(cid, sid, rid))
    new_token = scenes.ensure_identity(cid, new)
    copied = [e for e in knowledge.read(cid).values() if e["subject"]["scene"] == new_token]
    assert sorted((e["status"], e["subject"]["kind"]) for e in copied) == \
        [("unaware", "posts"), ("unaware", "scene")]
    narrowed = next(e for e in copied if e["subject"]["kind"] == "posts")
    assert len(narrowed["subject"]["posts"]) == 1


def test_a_world_group_reclassify_prunes_the_grants_of_campaigns_that_followed_it(cid):
    from grimoire.store import entities, reclassify
    wid = campaigns.read_campaign(cid)["meta"]["world"]
    gid = entities.create_entity(worlds.world_root(wid), "groups", "The Saltmarch Watch")
    knowledge.add(cid, _entry(audience=f"groups:{gid}", name="The Saltmarch Watch"))
    reclassify.world_entity(wid, "groups", gid, "locations")
    assert knowledge.read(cid) == {}
