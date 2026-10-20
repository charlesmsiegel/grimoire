"""Frozen history is additive, bounded after macros, and inspected separately."""

import ast
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire import prompts
from grimoire.store import (
    campaigns,
    chronicle,
    config,
    embed_space,
    entities,
    locks,
    pins,
    scenes,
    tokens,
)
from grimoire.store.context import assemble, epistemic, layout, macros
from grimoire.store.context import history as history_section
from grimoire.store.context import pack as packer
from grimoire.store.history import model, query
from grimoire.store.history import retrieval as retrieve
from grimoire.store.inference import limits
from grimoire.store.scenes import serialize
from grimoire.store.searchdocs import live
from tests.history_fixtures import absorbed, actor, appear, played
from tests.history_fixtures import cid as cid


def evidence(*items, ceiling=1200):
    candidates = tuple(model.Candidate(i.scene, (model.Signals(
        structural=model.StructuralSignal((model.Relation("relationship", ("REASON_REF_MARKER",)),)),
        lexical=model.LexicalSignal(4.2, ("LEXICAL_MARKER",)),
        semantic=model.SemanticSignal(0.613579),
        rerank=model.RerankSignal("RERANK_MARKER", 1, "native")),),
        {(0, "structural"): 1, (0, "lexical"): 3, (0, "semantic"): 2},
        1, frozenset({"structural", "lexical"})) for i in items)
    return model.Evidence(tuple(items), candidates, model.Coverage("VERDICT_MARKER"), ceiling)


def item(sid="001--saltmarch", text="Mara kept the ledger.", summary="Seraphine found the key."):
    post = model.EvidencePost(41, "p-41", None, "Mara", text)
    return model.EvidenceItem(model.SceneRef(sid, None), "Saltmarch", "2026-10-13", "",
                              summary, (model.Excerpt((post,), "Mara: " + text),))


def _view(cid, sid, ev):
    """09's evidence as the narrator's unclassified view (11-S3)."""
    return epistemic.narrator_view(cid, sid, ev)


def row(detail):
    return next(s for s in detail["sections"] if s["id"] == "history_recall")


def test_fixed_golden_and_reasons_are_inspection_only(cid):
    sid = played(cid)
    ev = evidence(item())
    messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, ev))
    got = row(detail)
    golden = json.loads(Path(__file__).with_name("fixtures").joinpath("history_golden.json").read_text(encoding="utf-8"))
    assert got["text"] == golden["text"]
    assert got["tokens"] == tokens.count_tokens(got["text"])
    assert got["history"]["items"][0]["ranks"][0] == {"round": 0, "signal": "lexical", "rank": 3}
    sent = str(messages)
    for marker in ("REASON_REF_MARKER", "LEXICAL_MARKER", "0.613579", "RERANK_MARKER", "VERDICT_MARKER"):
        assert marker not in sent
        assert marker in json.dumps(got["history"])
    json.dumps(detail)


@pytest.mark.parametrize("context_budget", ["0", "10000"])
def test_macro_expanded_ceiling_independent_of_packer(cid, monkeypatch, context_budget):
    sid = played(cid)
    config.write_config(context_budget=context_budget, history_recall_budget="100")
    monkeypatch.setattr(macros, "expand_macros", lambda text, *args, **kwargs: text.replace("{{user}}", "Seraphine " * 200))
    ev = evidence(item(text="{{user}} " * 10), item("002--realm", text="short"), ceiling=100)
    _messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, ev))
    got = row(detail)
    assert got["tokens"] <= 100
    assert "{{user}}" not in got["text"]
    assert "**Saltmarch**" in got["text"]


def test_frozen_draws_and_legacy_units_shed_separately(cid, monkeypatch):
    draws = []
    def expand(text):
        if "{{random:x,y}}" in text:
            draws.append(text)
        return text.replace("{{random:x,y}}", "FIXED_DRAW")
    ev = evidence(item(text="{{random:x,y}}"), item("002--realm", text="{{random:x,y}}"))
    text, extra = history_section.section(ev, expand, len, {}, config.read_config())
    section = {"id": "history_recall", "label": "Recalled history", "text": text,
               "tier": packer.HISTORY_RECALL, **extra}
    survivor = extra["shed"]["render"](frozenset({"sid:001--saltmarch"}))
    lore = {"id": "recalled_lore", "label": "Lore", "text": "lore", "tier": packer.RECALLED}
    archive = {"id": "archive", "label": "Archive", "text": "archive", "tier": packer.ARCHIVE}
    result = packer.pack([section, lore, archive], [], budget=len(survivor)+len("lore")+len("archive")+4, count=len)
    got = result["sections"][0]
    assert got["shed_refs"] == ["sid:002--realm"]
    assert "FIXED_DRAW" in got["text"]
    assert not any(s["dropped"] for s in result["sections"])
    tiny = packer.pack([section, lore, archive], [], budget=1, count=len)["sections"][0]
    assert tiny["shed_refs"] == ["sid:002--realm", "sid:001--saltmarch"]
    assert tiny["dropped"]
    assert len(draws) == 2


@pytest.mark.parametrize("archive_on", [True, False])
def test_summary_omission_uses_enabled_archive(cid, archive_on):
    config.write_config(recap_depth="0", prompt_layout_enabled="on")
    layout.write_layout([{"id": "archive", "enabled": archive_on}])
    old = played(cid)
    absorbed(cid, old, "Seraphine found the key.")
    sid = played(cid, text="silver ledger")
    ev = evidence(replace(item(old), summary="Seraphine found the key."))
    messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, ev))
    got = row(detail)
    assert ("Seraphine found the key." in got["text"]) is not archive_on
    assert "Seraphine found the key." in str(messages)
    assert got["history"]["items"][0]["summary"] == ("omitted:archive" if archive_on else "included")


def test_depth_one_preview_refills_before_compose_with_unclipped_archive_scan(cid, monkeypatch):
    config.write_config(history_recall_depth="1", recap_depth="0")
    usable = played(cid, text="silver ledger")
    absorbed(cid, usable)
    first = played(cid, text="nothing matching")
    absorbed(cid, first)
    current = played(cid, text="x " * 5000 + "silver ledger")
    captured = []
    real = retrieve.retrieve_preview
    def preview(*args, **kwargs):
        captured.append(kwargs["summary_omissions"])
        return real(*args, **kwargs)
    monkeypatch.setattr(retrieve, "retrieve_preview", preview)
    detail = assemble.context_breakdown(cid, current)
    assert captured == [{first: "omitted:archive", usable: "omitted:archive"}]
    assert row(detail)["history"]["items"][0]["scene"] == usable
    ev = real(cid, current, query.prepare(cid, current).query, 1200, summary_omissions=captured[0])
    monkeypatch.setattr(retrieve, "retrieve_preview", lambda *a, **k: pytest.fail("compose retrieved"))
    assemble.compose_turn(cid, current, history_view=_view(cid, current, ev))


@pytest.mark.parametrize("off", ["depth", "layout"])
def test_off_is_identical_and_never_prepares_or_retrieves(cid, monkeypatch, off):
    sid = played(cid)
    baseline = assemble.compose_turn(cid, sid)
    if off == "depth":
        config.write_config(history_recall_depth="0")
    else:
        config.write_config(prompt_layout_enabled="on")
        layout.write_layout([{"id": "history_recall", "enabled": False}])
    monkeypatch.setattr(query, "prepare", lambda *a, **k: pytest.fail("query prepared while off"))
    monkeypatch.setattr(retrieve, "retrieve_preview", lambda *a, **k: pytest.fail("retrieved while off"))
    # Off reads no SearchDocument (so no compiled-cache lookup) and resolves no
    # embedding space, let alone builds a client for one.
    from grimoire import embeddings
    monkeypatch.setattr(live, "scene_documents", lambda *a, **k: pytest.fail("cache read while off"))
    monkeypatch.setattr(embed_space, "endpoint", lambda *a, **k: pytest.fail("space resolved while off"))
    monkeypatch.setattr(embeddings, "AsyncEmbeddingsClient",
                        lambda *a, **k: pytest.fail("embed client built while off"))
    assert assemble.compose_turn(cid, sid, history_view=_view(cid, sid, evidence(item()))) == baseline
    assert assemble.context_breakdown(cid, sid) == baseline[1]


def test_an_npc_gets_only_its_own_view_and_the_narrator_keeps_frozen_history(cid):
    # 11-C2: an NPC is handed its own view, never the narrator's, and
    # composes only that view's visible slices. A view for another reader is
    # refused before anything renders.
    sid = played(cid)
    aid, _vid = actor(cid, sid, "Mara")
    ev = evidence(item())
    with pytest.raises(ValueError):
        assemble.compose_turn(cid, sid, actor_ref="characters:"+aid, history_view=_view(cid, sid, ev))
    own = epistemic.classify_evidence(epistemic.Perspective.of(
        cid, sid, "characters:"+aid, ("characters:"+aid,),
        identity=scenes.identity.scene_identity(cid, sid)), ev)
    messages, npc = assemble.compose_turn(cid, sid, actor_ref="characters:"+aid, history_view=own)
    assert all(not s["text"] for s in npc["sections"] if s["id"].startswith("history"))
    assert "Mara kept the ledger." not in str(messages)
    _messages, narrator = assemble.compose_director_turn(cid, sid, "Continue", actor_ref="grimoire", history_view=_view(cid, sid, ev))
    assert row(narrator)["text"]


@pytest.mark.parametrize("secrecy", ["gm-only", "excluded"])
def test_preview_protects_header_location(cid, secrecy):
    old = played(cid)
    eid = entities.create_entity(campaigns.campaign_root(cid), "locations", "Realm", secrecy=secrecy)
    scenes.set_location(cid, old, eid)
    absorbed(cid, old)
    current = played(cid, text="silver ledger")
    if secrecy == "excluded":
        pins.set_rule(cid, "locations:"+eid, "exclude", scope="campaign")
    assert "Realm" not in row(assemble.context_breakdown(cid, current))["text"]


def test_history_api_settings_persist(client):
    values = {"history_recall_depth": "2", "history_recall_budget": "900",
              "history_recall_threshold": "0.5", "history_recall_rerank": "on"}
    response = client.put("/api/config", json=values)
    assert response.status_code == 200
    assert {k: client.get("/api/config").json().get(k) for k in values} == values


def test_live_preview_uses_supplied_resolved_window_with_unbounded_context(cid, monkeypatch):
    old = played(cid)
    absorbed(cid, old)
    current = played(cid)
    config.write_config(context_budget="0")
    resolution = object()
    seen = []
    def prompt_ceiling(resolved):
        seen.append(resolved)
        return limits.Ceiling(1000, 2000, 1000, True, None, "")
    monkeypatch.setattr(limits, "prompt_ceiling", prompt_ceiling)
    detail = assemble.context_breakdown(cid, current, resolved=resolution)
    assert seen == [resolution]
    got = row(detail)
    assert got["history"]["ceiling"] == 100
    assert got["tokens"] <= 100
    assert "context_window" in got["history"]["budget_from"]


def test_full_recap_omission_and_no_bare_header(cid):
    old = played(cid)
    absorbed(cid, old, "Seraphine found the key.")
    current = played(cid)
    omissions = history_section.summary_omissions(cid, current, "", full_recap=5)
    assert omissions[old] == "omitted:recap"
    ev = evidence(replace(item(old), excerpts=()))
    text, _extra = history_section.section(ev, lambda text: text, len, omissions, config.read_config())
    assert text == ""
    config.write_config(prompt_layout_enabled="on")
    layout.write_layout([{"id": "story_so_far", "enabled": False}])
    assert old not in history_section.summary_omissions(cid, current, "", full_recap=5)


def test_oversized_summary_is_not_truncated_into_a_header_only_item(cid):
    ev = evidence(replace(item(), summary="Seraphine " * 500), ceiling=100)
    text, _extra = history_section.section(ev, lambda text: text, len, {}, config.read_config())
    assert not text


def test_frame_and_item_macro_draws_are_frozen_through_fit_and_shed(cid, tmp_path, monkeypatch):
    root = tmp_path / "templates"
    shutil.copytree(prompts.DEFAULT_TEMPLATES_DIR, root)
    frame = root / "scene" / "sections" / "history_recall.j2"
    text = frame.read_text(encoding="utf-8")
    frame.write_text(text.replace("# Earlier in the campaign", "# Earlier in the campaign {% raw %}{{random:x,y}}{% endraw %}"), encoding="utf-8")
    monkeypatch.setenv("GRIMOIRE_TEMPLATES", str(root))
    prompts._env.cache_clear()
    calls = []
    def expand(text):
        if "{{random:x,y}}" in text:
            calls.append(text)
        return text.replace("{{random:x,y}}", "FROZEN")
    try:
        ev = evidence(item(text="{{random:x,y}} " * 100), item("002--realm", text="short"), ceiling=300)
        text, extra = history_section.section(ev, expand, len, {}, config.read_config())
        assert len(text) <= 300
        assert calls[0].startswith("# Earlier in the campaign")
        assert len(calls) == 2
        got = extra["shed"]["render"](frozenset({"sid:001--saltmarch"}))
        assert "# Earlier in the campaign FROZEN" in got
        assert "{{" not in got
        assert len(calls) == 2
    finally:
        prompts._env.cache_clear()


def test_macro_fit_metadata_contains_only_retained_posts_and_evidence_ids(cid):
    config.write_config(history_recall_depth="1")
    posts = (model.EvidencePost(41, "p-41", None, "Mara", "{{random:x,y}} " + "{{user}} " * 30),
             model.EvidencePost(42, "p-42", None, "Mara", "TRAILING_POST_MARKER"))
    excerpt = model.Excerpt(posts, prompts.render("snippets/transcript.j2", messages=[
        {"role": "assistant", "speaker": p.speaker, "content": p.text} for p in posts]))
    later = model.EvidencePost(43, "p-43", None, "Mara", "LATER_EXCERPT_MARKER")
    ev = evidence(replace(item(summary=""), excerpts=(excerpt, model.Excerpt((later,), later.text))), ceiling=200)
    draws = []
    def expand(text):
        if "{{random:x,y}}" in text:
            draws.append(text)
        return text.replace("{{random:x,y}}", "FROZEN").replace("{{user}}", "Seraphine " * 10)
    text, extra = history_section.section(ev, expand, len, {}, config.read_config())
    detail = extra["history"]["items"][0]
    assert detail["excerpts"] == [[41]]
    assert detail["evidence_ids"] == ["sid:001--saltmarch@p-41"]
    assert "TRAILING_POST_MARKER" not in text
    assert "LATER_EXCERPT_MARKER" not in text
    assert len(draws) == 1
    assert detail["posts"][0]["text"] in text
    assert detail["posts"][0]["key"] == "p-41"


@pytest.mark.parametrize("match_source", ["director", "image_url"])
def test_preview_archive_scan_omission_matches_actual_compose_window(cid, monkeypatch, match_source):
    config.write_config(history_recall_depth="1", recap_depth="0", context_scan_depth="1")
    usable = played(cid, text="Mara kept the ledger.")
    mara, version = actor(cid, usable, "Mara")
    absorbed(cid, usable)
    first = played(cid, text="nothing matching")
    appear(cid, first, mara, version)
    absorbed(cid, first)
    # A single archive keyword can occur only in the raw image URL or stored
    # director note. Query safety still removes those from its scoring text.
    for old in (usable, first):
        record = chronicle.read_chronicle(cid)[old]
        chronicle.absorb(cid, {**record, "keywords": ["ledger"]})
    current = played(cid, text="Mara asks.")
    appear(cid, current, mara, version)
    if match_source == "director":
        scenes.append_message(cid, current, "assistant", "ledger", speaker=serialize.DIRECTOR_SPEAKER)
    else:
        scenes.edit_message(cid, current, 0, "Mara asks. ![portrait](https://example.invalid/silver/ledger)")
    prepared = query.prepare(cid, current)
    assert "ledger" not in prepared.query.terms
    actual = assemble._assemble(cid, current)["data"]["archive_entries"]
    expected = dict.fromkeys((entry["id"] for entry in actual), "omitted:archive")
    assert set(expected) == {usable, first}
    captured = []
    real = retrieve.retrieve_preview
    def preview(*args, **kwargs):
        captured.append(kwargs["summary_omissions"])
        return real(*args, **kwargs)
    monkeypatch.setattr(retrieve, "retrieve_preview", preview)
    got = assemble.context_breakdown(cid, current)
    assert captured == [expected]
    assert row(got)["history"]["items"][0]["scene"] == usable
    ev = real(cid, current, prepared.query, 1200, summary_omissions=expected)
    monkeypatch.setattr(retrieve, "retrieve_preview", lambda *a, **k: pytest.fail("compose retrieved"))
    with locks.campaign_lock(cid):
        _messages, detail = assemble.compose_turn(cid, current, history_view=_view(cid, current, ev))
    assert row(detail)["history"]["items"][0]["scene"] == usable


@pytest.mark.parametrize("expanded_summary", ["", " \n\t"])
def test_expanded_empty_summary_cannot_keep_header_or_evidence(cid, expanded_summary):
    supplied = replace(item(summary="{{user}}"), date="", excerpts=())
    calls = []
    def expand(text):
        if "{{user}}" in text:
            calls.append(text)
        return text.replace("{{user}}", expanded_summary)
    text, extra = history_section.section(evidence(supplied), expand, len, {}, config.read_config())
    assert text == ""
    assert extra["history"]["items"] == []
    assert extra["shed"]["units"] == []
    assert len(calls) == 1


def test_disappearing_summary_keeps_retained_excerpt_without_another_draw(cid):
    calls = []
    def expand(text):
        if "{{random:x,y}}" in text:
            calls.append(text)
        return text.replace("{{user}}", "").replace("{{random:x,y}}", "FROZEN")
    supplied = item(text="Mara kept {{random:x,y}}.", summary="{{user}}")
    text, extra = history_section.section(evidence(supplied), expand, len, {}, config.read_config())
    detail = extra["history"]["items"][0]
    assert detail["summary"] == "none"
    assert detail["excerpts"] == [[41]]
    assert detail["posts"][0]["text"] in text
    assert "FROZEN" in text
    assert extra["shed"]["render"](frozenset({supplied.scene.key})) == text
    assert len(calls) == 1


def test_empty_summary_macro_does_not_render_history_header(cid):
    current = played(cid)
    # Unresolved user/char macros stay literal under the existing contract;
    # an unknown macro exercises actual empty expansion through compose.
    supplied = replace(item(summary="{{missing}}"), date="", excerpts=())
    messages, detail = assemble.compose_turn(cid, current, history_view=_view(cid, current, evidence(supplied)))
    got = row(detail)  # text-less: it only says why nothing was carried
    assert got["text"] == "" and got["history"]["items"] == []
    assert "**Saltmarch**" not in str(messages)


def test_world_info_and_history_are_the_only_shedding_sections():
    """9.3: the packer's shed loop is generic, so a third section growing a
    `shed` hook would change what gives way first without anyone deciding it.
    World info and Recalled history are the two that may (their order under
    pressure is pinned by `test_frozen_draws_and_legacy_units_shed_separately`
    and the lore-shedding suite)."""
    root = Path(assemble.__file__).parent.parent
    found = set()
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Dict) and any(
                        isinstance(k, ast.Constant) and k.value == "shed"
                        and isinstance(v, ast.Dict) for k, v in zip(node.keys, node.values, strict=True)):
                    found.add((path.relative_to(root).as_posix(), fn.name))
    assert found == {("assemble/_lore_render.py", "_world_info_section"),
                     ("history.py", "section")}, found
    # 11-S3: the history set gives way first, its aside tier (the narrator's
    # withheld items, an NPC's suspicions) before the recalled tier.
    assert packer.DROP_ORDER[:2] == (packer.HISTORY_ASIDE, packer.HISTORY_RECALL)


@pytest.mark.parametrize("opening, closing", [("She typed {{ name", "and then }} laughed."),
                                              ("She typed {{random:a,b", "and then }} laughed.")])
def test_a_macro_spanning_two_posts_drops_that_item_not_the_turn(cid, opening, closing):
    """Each part of an item is marked before the block expands whole, so a
    `{{` in one post closed by a `}}` in the next is one macro to the
    expander and swallows the markers between. That item can no longer name
    its posts and is dropped; the other recalled scene and the turn stand."""
    sid = played(cid)
    posts = (model.EvidencePost(3, "p-3", None, "Mara", opening),
             model.EvidencePost(4, "p-4", None, "Seraphine", closing))
    text = prompts.render("snippets/transcript.j2", messages=[
        {"role": "assistant", "speaker": p.speaker, "content": p.text} for p in posts])
    spanning = model.EvidenceItem(model.SceneRef("001--saltmarch", None), "Saltmarch", "", "",
                                  "", (model.Excerpt(posts, text),))
    _messages, detail = assemble.compose_turn(
        cid, sid, history_view=_view(cid, sid, evidence(spanning, item("002--realm", text="Mara kept the ledger."))))
    got = row(detail)
    scenes_kept = [i["scene"] for i in got["history"]["items"]]
    assert "002--realm" in scenes_kept and "Mara kept the ledger." in got["text"]
    if "{{random" not in opening:
        assert scenes_kept == ["002--realm"] and "laughed" not in got["text"]
    # A draw that keeps the option carrying every marker leaves an intact item;
    # any other draw drops it. Neither raises.
    assert assemble.context_breakdown(cid, sid)


def test_an_empty_retrieval_still_explains_itself_in_the_row(cid):
    """On, with nothing selected (an error here), the prompt carries nothing
    while the inspector row still says why; off, there is no row at all."""
    sid = played(cid)
    failed = model.Evidence(coverage=model.Coverage("error", semantic="failed:network"))
    messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, failed))
    got = row(detail)
    assert got["text"] == ""
    assert got["history"]["coverage"]["verdict"] == "error"
    assert got["history"]["coverage"]["semantic"] == "failed:network"
    config.write_config(history_recall_depth="0")
    off_messages, off_detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, failed))
    assert messages == off_messages
    assert all(s["id"] != "history_recall" for s in off_detail["sections"])


def test_an_unrenderable_history_section_costs_the_section_not_the_turn(cid, monkeypatch, caplog):
    sid = played(cid, text="Mara waits at the pier.")
    view = _view(cid, sid, evidence(item(text="Seraphine hid the ledger.")))
    assert "Seraphine hid the ledger." in str(assemble.compose_turn(cid, sid, history_view=view)[0])

    def unreadable(*args, **kwargs):
        raise ValueError("the recalled scene could not be rendered")

    # At the renderer boundary: composition, view selection, and the current
    # conversation stay real. Returning an empty part here would only prove
    # the test's fake could omit it, not the compose's failure policy.
    monkeypatch.setattr(history_section, "section", unreadable)
    messages, detail = assemble.compose_turn(cid, sid, history_view=view)
    assert "Seraphine hid the ledger." not in str(messages)
    assert messages[-1] == {"role": "user", "content": "Mara waits at the pier."}
    assert all(section["id"] != "history_recall" for section in detail["sections"])
    assert "Recalled history section history_recall dropped from a compose" in caplog.text


def test_compose_refits_to_the_selection_depth_not_the_setting(cid):
    """A query with `max_scenes` below the setting fits its items to
    `ceiling // max_scenes`; compose must re-fit them to that same share, not
    to `ceiling // setting`, or an already-selected excerpt is cut short."""
    config.write_config(history_recall_depth="5", history_recall_budget="400")
    sid = played(cid)
    long = " ".join(["The silver ledger was hidden beneath the boathouse floor."] * 12)
    one = replace(evidence(item(text=long), ceiling=400), depth=1)
    _messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, one))
    assert row(detail)["history"]["items"][0]["tokens"] > 400 // 5
    clipped = replace(one, depth=0)  # the setting's share: a fifth of the ceiling
    _messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, clipped))
    got = row(detail)["history"]["items"]
    assert not got or got[0]["tokens"] <= 400 // 5


def test_the_row_lists_what_was_found_but_not_selected(cid, monkeypatch):
    """Every choice is visible (goal 6): admitted candidates past depth keep
    their signals in the row, the strongest few in merge order with the rest
    counted, and none of it reaches the prompt."""
    monkeypatch.setattr(assemble._inspection, "HISTORY_UNSELECTED_SHOWN", 2)
    sid = played(cid)
    chosen = item()
    found = evidence(chosen, *(item(sid=f"00{n}--saltmarch") for n in range(2, 6)))
    ev = replace(found, items=(chosen,))
    messages, detail = assemble.compose_turn(cid, sid, history_view=_view(cid, sid, ev))
    got = row(detail)["history"]
    assert [i["scene"] for i in got["items"]] == ["001--saltmarch"]
    assert got["unselected"] == 4
    assert [c["scene"] for c in got["candidates"]] == ["002--saltmarch", "003--saltmarch"]
    assert got["candidates"][0]["rounds"][0]["lexical"]["matched"] == ["LEXICAL_MARKER"]
    assert "002--saltmarch" not in str(messages)
    json.dumps(detail)
