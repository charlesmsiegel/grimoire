"""Structural/lexical retrieval retains admission independently of selection."""

import pytest

from grimoire import prompts
from grimoire.store import (
    campaigns,
    chronicle,
    commitments,
    compiled,
    config,
    entities,
    pins,
    plot,
    regex,
    relationship_history,
    scenes,
)
from grimoire.store.history import budget, coverage, expand, lexical, model, settings
from grimoire.store.history import retrieval as retrieve
from grimoire.store.inference import limits
from grimoire.store.searchdocs import live, scene
from tests.history_fixtures import absorbed, actor, appear, location, played
from tests.history_fixtures import cid as cid


def test_every_relation_and_group_leader_contribute(cid):
    old = played(cid)
    mara, mv = actor(cid, old, "Mara")
    seraphine, sv = actor(cid, old, "Seraphine")
    winifred, _wv = actor(cid, old, "Winifred")
    loc = location(cid, old)
    absorbed(cid, old)
    plot.set_movement(cid, "ledger", "silver ledger", "open", "Mara gives the ledger.", old)
    commitments.set_movement(
        cid, "seal", "amber seal", "promise", "open", None, "Mara promises the seal.", old
    )
    group = entities.create_entity(
        campaigns.campaign_root(cid),
        "groups",
        "Realm",
        fields={"leader": f"characters:{mara}", "members": f"characters:{seraphine}"},
    )
    relationship_history.append(
        cid,
        [
            relationship_history.row(
                "bond",
                f"characters/{mara}",
                f"characters/{seraphine}",
                label="trust",
                before="",
                after="trust",
                scene=old,
            )
        ],
    )
    current = played(cid, text="Winifred asks about the silver ledger and amber seal.")
    appear(cid, current, mara, mv)
    appear(cid, current, seraphine, sv)
    scenes.set_location(cid, current, loc)
    q = model.Query(
        ("Winifred asks about the silver ledger.",),
        subjects=(f"characters:{winifred}", "thread:ledger", "commitment:seal", f"groups:{group}"),
    )
    evidence = retrieve.retrieve_preview(cid, current, q, 1200)
    cand = next(c for c in evidence.candidates if c.scene.sid == old)
    assert {r.kind for r in cand.rounds[0].structural.relations} == {
        "shared_cast",
        "actor",
        "location",
        "thread",
        "relationship",
        "group",
    }
    assert f"locations:{loc}" in {
        ref for r in cand.rounds[0].structural.relations for ref in r.refs
    }


def test_unreadable_chronicle_keeps_appearance_relations(cid):
    old = played(cid)
    mara, mv = actor(cid, old, "Mara")
    seraphine, sv = actor(cid, old, "Seraphine")
    current = played(cid)
    appear(cid, current, mara, mv)
    appear(cid, current, seraphine, sv)
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{", encoding="utf-8")
    e = retrieve.retrieve_preview(cid, current, model.Query(("",)), 1200)
    assert e.candidates[0].scene.sid == old
    assert e.candidates[0].admitted_by == frozenset({"structural"})


def test_shared_cast_over_cap_does_not_admit(cid):
    old = played(cid)
    mara, mv = actor(cid, old, "Mara")
    seraphine, sv = actor(cid, old, "Seraphine")
    for _ in range(12):
        sid = played(cid)
        appear(cid, sid, mara, mv)
        appear(cid, sid, seraphine, sv)
    current = played(cid)
    appear(cid, current, mara, mv)
    appear(cid, current, seraphine, sv)
    e = retrieve.retrieve_preview(cid, current, model.Query(("",)), 1200)
    assert e.candidates == ()
    assert e.coverage.verdict == "empty"


def test_later_current_branch_and_closed_siblings_are_ineligible(cid):
    old = played(cid)
    current = played(cid)
    later = played(cid)
    sibling = played(cid)
    abandoned = played(cid)
    accepted = played(cid)
    for candidate_sid in (old, later, sibling, abandoned):
        scenes.mark_absorbed(cid, candidate_sid, "silver ledger", "silver ledger")
    root = campaigns.campaign_root(cid) / "scenes"
    for sid, group in [
        (current, "current-group"),
        (sibling, "current-group"),
        (abandoned, "old-group"),
        (accepted, "old-group"),
    ]:
        p = root / (sid + ".md")
        p.write_text(
            p.read_text(encoding="utf-8").replace("---\n", f"---\nbranch_group: {group}\n", 1),
            encoding="utf-8",
        )
    abandoned_path = root / (abandoned + ".md")
    abandoned_path.write_text(
        abandoned_path.read_text(encoding="utf-8").replace("done: true", "done: false"),
        encoding="utf-8",
    )
    absorbed(cid, accepted)
    # Place the closed/current siblings before current in the archive's exact string order.
    current_path = root / (current + ".md")
    new_current = "999--saltmarch"
    current_path.rename(root / (new_current + ".md"))
    e = retrieve.retrieve_preview(
        cid, new_current, model.Query(("",), terms=("silver", "ledger")), 1200
    )
    found = {c.scene.sid for c in e.candidates}
    assert old in found and later in found
    assert sibling not in found and abandoned not in found and new_current not in found
    e2 = retrieve.retrieve_preview(
        cid, current, model.Query(("",), terms=("silver", "ledger")), 1200
    )
    assert later not in {c.scene.sid for c in e2.candidates}


def test_lexical_requires_weighted_or_two_plain_terms_and_cache_is_optional(cid, monkeypatch):
    docs = [
        scene.SceneDocument("a", "Mara holds the ledger", {}),
        scene.SceneDocument("b", "The ledger counts silver", {}),
        scene.SceneDocument("c", "The ledger", {}),
    ]
    a = lexical.score(("ledger",), docs)
    assert all(not lexical.admits(s) for s in a)
    b = lexical.score(("mara",), docs, weights={"mara": 2})
    assert lexical.admits(b[0], {"mara": 2}) and not lexical.admits(b[1], {"mara": 2})
    cold = lexical.score(("ledger", "silver"), docs)
    assert lexical.admits(cold[1]) and not lexical.admits(cold[2])
    monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    assert lexical.score(("ledger", "silver"), docs) == cold


def test_all_1100_lexical_candidates_survive_semantic_limits(cid, monkeypatch):
    current = played(cid)
    actor(cid, current, "Mara")
    docs = tuple(
        live.LiveDocument(
            f"{i:04}--saltmarch",
            None,
            scene.SceneDocument(
                str(i), "Mara gives a ledger.", {"identity": "", "title": "Saltmarch"}
            ),
        )
        for i in range(1100)
    )
    monkeypatch.setattr(live, "scene_documents", lambda *args, **kwargs: live.LiveSet(docs, (), ()))
    e = retrieve.retrieve_preview(cid, "9999--saltmarch", model.Query(("",), terms=("mara",)), 1200)
    assert len(e.candidates) == 1100


def test_superseded_document_cannot_recall_old_words(cid):
    old = played(cid, text="amber seal")
    absorbed(cid, old, "amber seal")
    current = played(cid)
    q = model.Query(("",), terms=("amber", "seal"))
    assert retrieve.retrieve_preview(cid, current, q, 1200).candidates
    chronicle.absorb(cid, {"id": old, "summary": "velvet crown", "keywords": [], "cast": []})
    scenes.mark_absorbed(cid, old, "velvet crown", "velvet crown")
    assert retrieve.retrieve_preview(cid, current, q, 1200).candidates == ()


def test_summary_only_omission_does_not_spend_depth(cid):
    config.write_config(history_recall_depth="1")
    usable = played(cid, text="Mara holds the silver ledger.")
    absorbed(cid, usable)
    omitted = played(cid, text="The room is silent.")
    absorbed(cid, omitted)
    current = played(cid)
    e = retrieve.retrieve_preview(
        cid,
        current,
        model.Query(("",), terms=("silver", "ledger")),
        1200,
        summary_omissions={omitted: "omitted:archive"},
    )
    assert [i.scene.sid for i in e.items] == [usable]
    assert {c.scene.sid for c in e.candidates} == {usable, omitted}


def test_omitted_summary_is_retained_when_excerpts_exist(cid):
    old = played(cid)
    absorbed(cid, old)
    current = played(cid)
    e = retrieve.retrieve_preview(
        cid,
        current,
        model.Query(("",), terms=("silver", "ledger")),
        1200,
        summary_omissions={old: "omitted:archive"},
    )
    assert e.items[0].summary == "Mara gives Seraphine the silver ledger."
    assert e.items[0].summary_omission == "omitted:archive"
    assert e.items[0].excerpts
    assert e.items[0].evidence_ids[0].startswith(e.items[0].scene.key + "@")


def test_expand_copies_post_keys_part_and_prompt_text(cid):
    old = played(cid)
    scenes.append_messages(
        cid,
        old,
        [
            {
                "role": "assistant",
                "speaker": "Mara",
                "content": "Mara returns the ledger.",
                "response_id": "reply",
                "response_part": 2,
            }
        ],
    )
    played(cid)
    excerpts = expand.excerpts(
        cid, model.SceneRef(old, None), model.Query(("",), terms=("ledger",)), 1200
    )
    posts = [p for e in excerpts for p in e.posts]
    assert posts[1].key == "r-reply" and posts[1].part == 2
    assert posts[1].text == "Mara returns the ledger."


@pytest.mark.parametrize(
    "eligible,admissions,semantic_complete,want",
    [
        (0, [], True, "exhausted"),
        (2, [], True, "empty"),
        (2, [("lexical",)], True, "thin"),
        (3, [("lexical", "structural"), ("lexical",)], True, "sufficient"),
        (2, [("lexical",), ("structural",)], True, "exhausted"),
        (2, [("lexical",), ("structural",)], False, "thin"),
    ],
)
def test_coverage_distinguishes_exhaustion_from_incomplete_semantic(
    eligible, admissions, semantic_complete, want
):
    candidates = [
        model.Candidate(model.SceneRef(str(i), None), (), {}, 1, frozenset(signals))
        for i, signals in enumerate(admissions)
    ]
    assert (
        coverage.verdict(candidates, 2, eligible, semantic_complete=semantic_complete).verdict
        == want
    )


def test_budget_counts_heading_and_trimmed_post_text(cid):
    ref = model.SceneRef("001--saltmarch", None)
    post = model.EvidencePost(3, "p-post", None, "Mara", "silver " * 1000)
    item = model.EvidenceItem(
        ref, "Saltmarch", excerpts=(model.Excerpt((post,), "**Mara:** " + post.text),)
    )
    fitted = budget.fit([item], 100, lambda text: (len(text) + 3) // 4)
    assert fitted
    assert len(budget.render(fitted)) <= 400
    assert "# Earlier in the campaign" in budget.render(fitted)
    assert fitted[0].excerpts[0].posts[0].text in fitted[0].excerpts[0].text
    assert budget.fit([item], 79, len) == []


def test_ceiling_uses_shared_prompt_ceiling_and_context_budget(cid, monkeypatch):
    monkeypatch.setattr(
        limits, "prompt_ceiling", lambda resolved: limits.Ceiling(1000, 2000, 1000, True, None, "")
    )
    assert (
        budget.ceiling({"history_recall_budget": "1200", "context_budget": "200"}, object()) == 30
    )
    monkeypatch.setattr(
        limits, "prompt_ceiling", lambda resolved: limits.Ceiling(None, None, 0, False, None, "")
    )
    assert (
        budget.ceiling({"history_recall_budget": "1200", "context_budget": "0"}, object()) == 1200
    )


def test_programming_errors_are_refused(cid):
    current = played(cid)
    for q in [
        model.Query(("",), perspective="Mara"),
        model.Query(("",), scopes=frozenset({"bad"})),
    ]:
        with pytest.raises(ValueError):
            retrieve.retrieve_preview(cid, current, q, 1200)


def test_header_never_names_gm_only_location(cid):
    old = played(cid)
    location(cid, old, secrecy="gm-only")
    absorbed(cid, old)
    current = played(cid)
    e = retrieve.retrieve_preview(
        cid, current, model.Query(("",), terms=("silver", "ledger")), 1200
    )
    assert e.items[0].location == ""


def test_settings_keys_survive_config_read_and_off_does_no_work(cid, monkeypatch):
    config.write_config(
        history_recall_depth="0",
        history_recall_budget="555",
        history_recall_threshold="0.42",
        history_recall_rerank="on",
    )
    cfg = config.read_config()
    assert cfg["history_recall_budget"] == "555" and cfg["history_recall_threshold"] == "0.42"
    assert settings.read() is None
    monkeypatch.setattr(live, "scene_documents", lambda *a, **kw: pytest.fail("off read documents"))
    assert retrieve.retrieve_preview(cid, "001--saltmarch", model.Query(("",)), 1200).items == ()


def test_group_seed_cannot_reintroduce_gm_only_or_excluded_records(cid):
    old = played(cid)
    mara, mv = actor(cid, old, "Mara")
    seraphine, sv = actor(cid, old, "Seraphine")
    group = entities.create_entity(
        campaigns.campaign_root(cid),
        "groups",
        "Realm",
        fields={"leader": f"characters:{mara}", "members": f"characters:{seraphine}"},
        secrecy="gm-only",
    )
    current = played(cid, text="Realm")
    appear(cid, current, mara, mv)
    appear(cid, current, seraphine, sv)
    q = model.Query(("Realm",), subjects=(f"groups:{group}",), scopes=frozenset({"group"}))
    assert retrieve.retrieve_preview(cid, current, q, 1200).candidates == ()
    pins.set_rule(cid, f"characters:{seraphine}", "exclude", sid=current)
    q = model.Query(
        ("Seraphine",),
        subjects=(f"characters:{seraphine}",),
        scopes=frozenset({"actor", "shared_cast", "relationship"}),
    )
    assert retrieve.retrieve_preview(cid, current, q, 1200).candidates == ()


def test_expansion_uses_prompt_rules_radius_one_max_two_and_no_fallback(cid):
    old = played(cid, text="Mara sees the silver ledger.")
    for index in range(1, 12):
        text = "Mara sees the silver ledger." if index in (5, 11) else "The room is quiet."
        scenes.append_message(cid, old, "assistant", text)
    regex.layers.write_campaign(
        cid,
        {
            "rules": [
                {
                    "name": "Hide ledger",
                    "pattern": "silver ledger",
                    "replacement": "amber seal",
                    "applies": ["prompt"],
                }
            ]
        },
    )
    q = model.Query(("",), terms=("amber seal",))
    found = expand.excerpts(cid, model.SceneRef(old, None), q, 2000)
    assert len(found) == 2
    assert all(len(e.posts) <= 3 for e in found)
    assert all("silver ledger" not in e.text for e in found)
    assert sum("amber seal" in p.text for e in found for p in e.posts) == 2
    assert (
        expand.excerpts(
            cid, model.SceneRef(old, None), model.Query(("",), terms=("velvet crown",)), 2000
        )
        == ()
    )


def test_summary_fallback_still_answers_when_chronicle_is_unreadable(cid):
    old = played(cid)
    absorbed(cid, old)
    current = played(cid)
    (campaigns.campaign_root(cid) / "chronicle.json").write_text("{", encoding="utf-8")
    e = retrieve.retrieve_preview(
        cid, current, model.Query(("",), terms=("silver", "ledger")), 1200
    )
    assert e.items[0].summary == "Mara gives Seraphine the silver ledger."


def test_history_terms_are_persisted_only_under_supplied_document_keys(cid):
    doc = scene.SceneDocument("current-document-key", "silver ledger", {})
    lexical.score(("silver", "ledger"), [doc])
    key = compiled.make_key(compiled.HISTORY_TERMS, [], params={"document_key": doc.key})
    assert key is not None
    with compiled.batch() as request:
        found = request.lookup(key)
    assert found.hit and found.value["counts"] == {"silver": 1, "ledger": 1}


def test_mixed_date_and_ordinal_ids_use_archive_string_order(cid, monkeypatch):
    sid = played(cid)
    actor(cid, sid, "Mara")
    docs = tuple(
        live.LiveDocument(
            value, None, scene.SceneDocument(value, "Mara", {"identity": "", "title": "Saltmarch"})
        )
        for value in ("001--saltmarch", "1999-01-01--saltmarch", "2027-01-01--saltmarch")
    )
    monkeypatch.setattr(live, "scene_documents", lambda *a, **kw: live.LiveSet(docs, (), ()))
    result = retrieve.retrieve_preview(
        cid, "2026-01-01--saltmarch", model.Query(("",), terms=("mara",)), 1200
    )
    assert {c.scene.sid for c in result.candidates} == {"001--saltmarch", "1999-01-01--saltmarch"}


def test_section_and_item_templates_share_the_exact_renderer(cid):
    item = model.EvidenceItem(
        model.SceneRef("001--saltmarch", None),
        "Saltmarch",
        date="2026-10-13",
        location="Realm",
        summary="Mara returns.",
    )
    assert (
        budget.render([item])
        == prompts.render("scene/sections/history_recall.j2", history_evidence=[item]).strip()
    )
    assert budget.render([]) == ""


def test_original_scene_in_current_branch_group_is_not_history(cid):
    original = played(cid)
    absorbed(cid, original)
    path = campaigns.campaign_root(cid) / "scenes" / (original + ".md")
    identity_line = next(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("identity:")
    )
    identity = identity_line.partition(":")[2].strip()
    current = played(cid)
    p = campaigns.campaign_root(cid) / "scenes" / (current + ".md")
    p.write_text(
        p.read_text(encoding="utf-8").replace("---\n", f"---\nbranch_group: {identity}\n", 1),
        encoding="utf-8",
    )
    e = retrieve.retrieve_preview(
        cid, current, model.Query(("",), terms=("silver", "ledger")), 1200
    )
    assert e.candidates == ()


def test_supplied_terms_cannot_admit_protected_multiword_record(cid):
    entities.create_entity(
        campaigns.campaign_root(cid), "items", "Mara Winifred", secrecy="gm-only"
    )
    old = played(cid, text="Mara Winifred and Mara ribbon.")
    absorbed(cid, old, "Mara Winifred and Mara ribbon.")
    current = played(cid, text="Mara dust and Winifred entries.")
    q = model.Query(
        ("",), terms=("\uff2d\uff21\uff32\uff21", "Winifred", "Mara ribbon"), scopes=frozenset()
    )
    assert retrieve.retrieve_preview(cid, current, q, 1200).candidates == ()
