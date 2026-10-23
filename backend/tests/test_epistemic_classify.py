"""The deterministic epistemic classifier (roadmap 11-S2, spec 11 §4).

Every store here is real, through `evals/epistemic/synth.py`'s recipes
(shared with the leakage suite), so a post text is exactly what 09 would hand
over, and re-matching is exercised against the real thing.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from evals.epistemic import synth
from grimoire.store import (
    chronicle,
    knowledge,
    knowledge_scene,
    locks,
    overlay,
    responses,
    scenes,
)
from grimoire.store.appearances import paths as appearances_paths
from grimoire.store.context import epistemic
from grimoire.store.context.epistemic import _classification as epistemic_classification
from grimoire.store.history import budget, model
from grimoire.store.locks import _runtime as locks_runtime
from grimoire.store.scenes import identity as scenes_identity
from tests.campaign_fixtures import saltmarch_cid as saltmarch_cid

Stage = synth.Stage
_hex = synth.hex_id
_npc = synth.npc
_enter = synth.enter
_leave = synth.leave
_grant = synth.grant
_key = synth.key
_view = synth.view


def _post_classes(view) -> dict[int, tuple[str, tuple[str, ...]]]:
    return {i: (s.access, s.basis) for c in view.items for s in c.slices
            if s.part == "posts" for i in s.posts}


@pytest.fixture
def pier(cid):
    """Mara and Winifred at the pier: Mara there throughout, Winifred
    arriving after the first post."""
    st = Stage(cid)
    mara, maid, mvid = _npc(cid, "Mara")
    win, waid, wvid = _npc(cid, "Winifred")
    _enter(st, maid, mvid)
    st.say("Seraphine reaches the pier at dusk.")
    _enter(st, waid, wvid)
    rid = st.reply(mara, "Mara", "Mara hands over the silver ledger.")
    return {"st": st, "mara": mara, "maid": maid, "win": win, "waid": waid,
            "wvid": wvid, "rid": rid}


# ------------------------------------------------------------------ vocabulary


def test_the_perspective_string_round_trips_and_refuses_anything_else(cid):
    p = epistemic.Perspective.of(cid, "s", "characters:mara", ("characters:mara",))
    assert p.to_string() == "actor:characters:mara"
    assert epistemic.Perspective.of_string(p.to_string(), cid=cid, sid="s", identity=None,
                                           present=p.present) == p
    narrator = epistemic.Perspective.of(cid, "s", "grimoire", ())
    assert narrator.actor is None and narrator.to_string() == "narrator"
    assert epistemic.Perspective.of(cid, "s", None, ()).actor is None
    for bad in ("Mara", "actor:groups:watch", "actor:", "actor:characters:../x"):
        with pytest.raises(ValueError):
            epistemic.Perspective.of_string(bad, cid=cid, sid="s", identity=None, present=())


# ------------------------------------------------------------------ the post rules


def test_r3_presence_and_r4_not_recorded_split_an_excerpt(pier):
    st, win = pier["st"], pier["win"]
    first, second = 0, st.index_of("silver ledger")
    view = _view(st, win, [st.item()])
    classes = _post_classes(view)
    assert classes[first] == ("unknown", ("not_recorded_present",))
    assert classes[second] == ("witnessed", ("presence_interval",))
    [shown] = view.visible()
    assert [s.posts for s in shown.slices] == [(second,)]
    assert "silver ledger" in shown.slices[0].text
    assert "reaches the pier" not in shown.slices[0].text


def test_r2_authorship_proves_presence_without_an_interval(pier, cid):
    st, mara = pier["st"], pier["mara"]
    record = appearances_paths.record(cid)
    record[f"characters/{pier['maid']}"]["presence"] = {}
    record[f"characters/{pier['maid']}"]["scenes"] = []
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    classes = _post_classes(_view(st, mara, [st.item()]))
    assert classes[st.index_of("silver ledger")] == ("witnessed", ("authored",))
    assert classes[0] == ("unknown", ("no_record",))


def test_r5_scene_lists_without_intervals_are_not_presence(pier, cid):
    st, win = pier["st"], pier["win"]
    record = appearances_paths.record(cid)
    record[f"characters/{pier['waid']}"]["presence"] = {}
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    classes = _post_classes(_view(st, win, [st.item()]))
    assert set(classes.values()) == {("unknown", ("scene_cast",))}


def test_r5_reads_the_chronicle_cast_too(pier, cid):
    st, win = pier["st"], pier["win"]
    record = appearances_paths.record(cid)
    record[f"characters/{pier['waid']}"] = {**record[f"characters/{pier['waid']}"],
                                            "presence": {}, "scenes": []}
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    assert set(_post_classes(_view(st, win, [st.item()])).values()) == \
        {("unknown", ("no_record",))}
    chronicle.absorb(cid, {"id": st.sid, "one_line": "", "summary": "",
                           "cast": [f"characters/{pier['waid']}"]})
    assert set(_post_classes(_view(st, win, [st.item()])).values()) == \
        {("unknown", ("scene_cast",))}


def test_r6_an_actor_with_no_record_has_nothing(pier, cid):
    st = pier["st"]
    stranger, _aid, _vid = _npc(cid, "Ilse")
    view = _view(st, stranger, [st.item()])
    assert set(_post_classes(view).values()) == {("unknown", ("no_record",))}
    assert view.visible() == ()


def test_r1_overrides_beat_every_derived_signal(pier):
    st, mara, win = pier["st"], pier["mara"], pier["win"]
    secret = st.index_of("silver ledger")
    _grant(st, win, "Winifred", "unaware", keys=(_key(st, secret),))
    _grant(st, win, "Winifred", "knows", keys=(_key(st, 0),))
    classes = _post_classes(_view(st, win, [st.item()]))
    assert classes[secret] == ("narrator_only", ("override",))
    assert classes[0] == ("known", ("override",))
    # A post `unaware` on the actor's own reply still withholds it: R1 first.
    _grant(st, mara, "Mara", "unaware", keys=(_key(st, secret),))
    assert _post_classes(_view(st, mara, [st.item()]))[secret] == \
        ("narrator_only", ("override",))


def test_r1_suspects_and_everyone_skip_the_author(pier):
    st, mara, win = pier["st"], pier["mara"], pier["win"]
    secret = st.index_of("silver ledger")
    _grant(st, "*", "", "unaware", keys=(_key(st, secret),))
    assert _post_classes(_view(st, win, [st.item()]))[secret] == \
        ("narrator_only", ("override_all",))
    # `*` never covers the post's author: Mara wrote it.
    access, basis = _post_classes(_view(st, mara, [st.item()]))[secret]
    assert access == "witnessed" and "authored" in basis
    _grant(st, win, "Winifred", "suspects", keys=(_key(st, 0),))
    assert _post_classes(_view(st, win, [st.item()]))[0] == ("suspected", ("override",))


def test_a_grant_stops_matching_when_its_text_changes(pier):
    st, win = pier["st"], pier["win"]
    _grant(st, win, "Winifred", "knows", keys=(_key(st, 0),))
    scenes.edit_message(st.cid, st.sid, 0, "Seraphine reaches the pier at dawn.")
    item = st.item()
    assert _post_classes(_view(st, win, [item]))[0] == \
        ("unknown", ("not_recorded_present",))


def test_r2_prime_a_stamped_player_post_proves_the_pc_was_there(cid):
    st = Stage(cid)
    pc, paid, pvid = _npc(cid, "Seraphine")
    _enter(st, paid, pvid, role="player")
    i = st.say("I hold my ground.", speaker="Seraphine")
    record = appearances_paths.record(cid)
    record[f"characters/{paid}"]["presence"] = {}
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    view = _view(st, None, [st.item()], present=(pc,))
    [c] = view.items
    assert c.state == "attributed" and c.known_to == (pc,)
    p = epistemic.Perspective.of(cid, st.sid, None, (pc,), identity=st.identity)
    assert epistemic.classify(p, [st.item()], roster=(pc,)).items[0].known_to == (pc,)
    assert i == 0


# ------------------------------------------------------------------ the keyless rule


def test_a_keyless_post_is_withheld_when_its_scene_has_post_listed_overrides(pier):
    st, win = pier["st"], pier["win"]
    old = st.legacy("An old line, before posts had ids.")
    assert _post_classes(_view(st, win, [st.item()]))[old] == \
        ("witnessed", ("presence_interval",))
    _grant(st, win, "Winifred", "knows", keys=(_key(st, 0),))
    assert _post_classes(_view(st, win, [st.item()]))[old] == ("unknown", ("unkeyed",))


def test_presentation_identity_migration_does_not_broaden_legacy_knowledge(pier):
    st, win = pier["st"], pier["win"]
    old = st.legacy("An old line, before posts had ids.")
    _grant(st, win, "Winifred", "knows", keys=(_key(st, 0),))
    assert _post_classes(_view(st, win, [st.item()]))[old] == ("unknown", ("unkeyed",))
    responses.migrate_if_needed(st.cid, st.sid)
    assert st.messages()[old]["post_id"]
    assert _post_classes(_view(st, win, [st.item()]))[old] == ("unknown", ("unkeyed",))


def test_a_whole_scene_entry_settles_a_keyless_post(pier):
    st, win = pier["st"], pier["win"]
    old = st.legacy("An old line, before posts had ids.")
    _grant(st, win, "Winifred", "unaware")
    assert _post_classes(_view(st, win, [st.item()]))[old] == \
        ("narrator_only", ("override",))


# ------------------------------------------------------------------ S1


def _absorb(st: Stage, summary: str) -> None:
    chronicle.absorb(st.cid, {"id": st.sid, "one_line": summary, "summary": summary,
                              "cast": []})


def test_s1_presence_never_shows_a_summary_or_title(pier):
    st, mara = pier["st"], pier["mara"]
    _absorb(st, "Mara gave away the silver ledger.")
    view = _view(st, mara, [st.item(summary="Mara gave away the silver ledger.")])
    [c] = view.items
    summary = next(s for s in c.slices if s.part == "summary")
    assert summary.access == "unknown" and summary.basis == ("summary_needs_grant",)
    assert c.header == {}
    assert c.item.summary == "" and c.item.excerpts == () and c.item.title == ""


def test_s1_a_holding_whole_scene_grant_shows_them_and_an_edit_withdraws_it(pier):
    st, mara = pier["st"], pier["mara"]
    _absorb(st, "Mara gave away the silver ledger.")
    _grant(st, mara, "Mara", "knows")
    item = st.item(summary="Mara gave away the silver ledger.")
    [c] = _view(st, mara, [item]).visible()
    assert c.header["title"] == "The Pier"
    assert any(s.part == "summary" and s.access == "known" for s in c.slices)
    scenes.edit_message(st.cid, st.sid, 0, "Seraphine reaches the pier at dawn.")
    [c] = _view(st, mara, [st.item(summary="Mara gave away the silver ledger.")]).items
    assert "title" not in c.header
    assert next(s for s in c.slices if s.part == "summary").access == "unknown"


def test_s1_a_post_unaware_withholds_the_summary_past_a_whole_scene_grant(pier, cid):
    st, win = pier["st"], pier["win"]
    _absorb(st, "Mara gave away the silver ledger.")
    _grant(st, win, "Winifred", "knows")
    _grant(st, "*", "", "unaware", keys=(_key(st, st.index_of("silver ledger")),))
    [c] = _view(st, win, [st.item(summary="Mara gave away the silver ledger.")]).items
    # An override's doing, so never a part the Decision stage may ask about
    # (11-S6): a `known` answer would show a summary that may paraphrase the
    # withheld post.
    summary = next(s for s in c.slices if s.part == "summary")
    assert summary.basis == ("override",) and not epistemic.eligible(summary)
    assert "title" not in c.header


def test_s1_a_grant_never_shows_a_summary_the_chronicle_does_not_hold(pier):
    st, mara = pier["st"], pier["mara"]
    _grant(st, mara, "Mara", "knows")       # granted before any absorb
    item = st.item(summary="A rolling summary, regenerated after the grant.")
    [c] = _view(st, mara, [item]).items
    summary = next(s for s in c.slices if s.part == "summary")
    assert summary.access == "unknown" and summary.text == ""
    assert c.header["title"] == "The Pier"
    # A title 09 changed since (a source prefix, a rename) is not shown either.
    [c] = _view(st, mara, [st.item(title="[source] The Pier")]).items
    assert "title" not in c.header


def test_s1_a_whole_scene_unaware_makes_them_narrator_only(pier):
    st, mara = pier["st"], pier["mara"]
    _grant(st, mara, "Mara", "unaware")
    view = _view(st, mara, [st.item(summary="x")])
    assert view.visible() == ()
    assert next(s for s in view.items[0].slices if s.part == "summary").access == \
        "narrator_only"


# ------------------------------------------------------------------ the narrator


def test_the_narrator_sees_everything_and_the_aggregate(pier, cid):
    st, mara, win = pier["st"], pier["mara"], pier["win"]
    stranger, _a, _v = _npc(cid, "Ilse")
    present = (mara, win, stranger)
    view = _view(st, None, [st.item(summary="s")], present)
    [c] = view.visible()
    assert c.state == "attributed" and c.known_to == (mara, win) and c.suspected_by == ()
    assert c.header["title"] == "The Pier"
    assert {s.access for s in c.slices} == {"known"}
    assert "reaches the pier" in c.slices[0].text

    other = Stage(cid, "The Mill")
    other.say("Nobody here was recorded.")
    [c] = _view(other, None, [other.item()], present).items
    assert c.state == "unattributed" and c.known_to == ()


def test_the_narrator_aggregate_withheld_and_suspected(cid):
    st = Stage(cid)
    mara, _aid, _vid = _npc(cid, "Mara")
    st.say("A whisper by the well.")
    _grant(st, mara, "Mara", "unaware")
    [c] = _view(st, None, [st.item()], (mara,)).items
    assert c.state == "withheld"
    other = Stage(cid, "The Mill")
    other.say("A rumour at the mill.")
    _grant(other, mara, "Mara", "suspects")
    [c] = _view(other, None, [other.item()], (mara,)).items
    assert c.state == "attributed" and c.suspected_by == (mara,) and c.known_to == ()


# ------------------------------------------------------------------ re-matching


def test_a_post_that_moved_is_dropped_for_everyone(pier):
    st, mara = pier["st"], pier["mara"]
    item = st.item()
    secret = st.index_of("silver ledger")
    scenes.edit_message(st.cid, st.sid, secret, "Something else entirely.")
    for actor in (mara, None):
        classes = _post_classes(_view(st, actor, [item], (mara,)))
        assert classes[secret] == ("unknown", ("moved",))
    # Appended text still matches: what was shown is still in the post.
    scenes.edit_message(st.cid, st.sid, secret, "Mara hands over the silver ledger. Then.")
    assert _post_classes(_view(st, mara, [item]))[secret][0] == "witnessed"


def test_a_cut_between_retrieval_and_classification_makes_nothing_newly_witnessed(cid):
    st = Stage(cid)
    mara, _aid, _vid = _npc(cid, "Mara")
    win, waid, wvid = _npc(cid, "Winifred")
    first = st.reply(mara, "Mara", "Mara speaks first.")
    st.say("A secret, before Winifred arrives.")
    _enter(st, waid, wvid)
    st.say("Winifred is told about the weather.")
    item = st.item()
    secret = st.index_of("A secret")
    responses.delete(cid, st.sid, first)   # renumbers posts and remaps presence
    classes = _post_classes(_view(st, win, [item]))
    # The secret's old index now names the weather post Winifred did see; the
    # evidence no longer matches it, so nothing there is newly witnessed.
    assert classes[secret] == ("unknown", ("moved",))
    assert "witnessed" not in {a for a, _b in classes.values()}


def test_a_hidden_post_and_a_clipped_post(pier):
    st, mara = pier["st"], pier["mara"]
    long = st.say("The tide " + "rolls in and out " * 200 + "and the end.")
    item = st.item()
    e = item.excerpts[0]
    whole = next(p for p in e.posts if p.index == long)
    # 08 clips a long post around its match with an ellipsis on each side.
    clip = model.EvidencePost(whole.index, whole.key, whole.part, whole.speaker,
                              "\u2026 " + whole.text[40:400] + " \u2026")
    clipped = model.EvidenceItem(item.scene, item.title, excerpts=(model.Excerpt(
        tuple(clip if p.index == long else p for p in e.posts), e.text),))
    assert _post_classes(_view(st, mara, [clipped]))[long][0] == "witnessed"
    scenes.set_excluded(st.cid, st.sid, 0, True)
    assert _post_classes(_view(st, mara, [item]))[0] == ("unknown", ("moved",))


def test_a_post_trimmed_by_09_still_matches(pier):
    st, mara = pier["st"], pier["mara"]
    item = st.item()
    e = item.excerpts[0]
    last = e.posts[-1]
    cut = model.EvidencePost(last.index, last.key, last.part, last.speaker,
                             last.text[:8].rstrip() + "…")
    trimmed = model.EvidenceItem(item.scene, item.title, excerpts=(
        model.Excerpt((*e.posts[:-1], cut), e.text),))
    assert _post_classes(_view(st, mara, [trimmed]))[last.index][0] == "witnessed"


def test_a_renamed_scene_still_matches_and_a_deleted_one_is_dropped(pier, cid):
    st, mara = pier["st"], pier["mara"]
    item = st.item()
    new = scenes.rename_scene(cid, st.sid, "The Old Pier")
    st.sid = new
    assert "witnessed" in {a for a, _b in _post_classes(_view(st, mara, [item])).values()}
    scenes.delete_scene(cid, new)
    view = _view(st, None, [item], (mara,))
    assert view.items[0].admitted is False and view.visible() == ()


def test_a_scene_with_no_identity_is_unverifiable(pier):
    st, mara = pier["st"], pier["mara"]
    item = st.item(identity=None)
    view = _view(st, mara, [item])
    assert view.visible() == ()
    assert {s.basis for s in view.items[0].slices} == {("unverifiable",)}
    [c] = _view(st, None, [item], (mara,)).visible()
    assert c.slices and c.slices[0].text == item.excerpts[0].text


def test_another_campaigns_evidence_is_never_classified(pier, cid):
    st, mara = pier["st"], pier["mara"]
    item = st.item()
    foreign = model.EvidenceItem(model.SceneRef(st.sid, st.identity, campaign="elsewhere"),
                                 item.title, excerpts=item.excerpts)
    assert _view(st, mara, [foreign]).visible() == ()


# ------------------------------------------------------------------ groups


def test_an_unresolvable_group_unaware_withholds_from_everyone(pier, cid):
    st, win = pier["st"], pier["win"]
    secret = st.index_of("silver ledger")
    _grant(st, "groups:watch", "The Watch", "unaware", keys=(_key(st, secret),))
    access, basis = _post_classes(_view(st, win, [st.item()]))[secret]
    assert access == "unknown" and "override_group" in basis
    # An entry on the post naming the actor still wins.
    _grant(st, win, "Winifred", "knows", keys=(_key(st, secret),))
    assert _post_classes(_view(st, win, [st.item()]))[secret] == ("known", ("override",))


def test_unreadable_audiences_withhold_everything_from_actors(pier, monkeypatch):
    st, win = pier["st"], pier["win"]

    def boom(_cid):
        raise OSError("mid-sync")
    monkeypatch.setattr(knowledge_scene, "audiences_for", boom)
    view = _view(st, win, [st.item()])
    assert view.visible() == ()
    assert view.items[0].slices[0].basis == ("unverifiable",)


# ------------------------------------------------------------------ failure table


def test_an_unreadable_appearance_record_leaves_only_overrides(pier, monkeypatch):
    st, win = pier["st"], pier["win"]
    _grant(st, win, "Winifred", "knows", keys=(_key(st, 0),))
    item = st.item()

    class Unreadable:
        @staticmethod
        def record(_cid):
            raise ValueError("appearances.json will not parse")
    monkeypatch.setattr(epistemic_classification, "appearances_paths", Unreadable)
    classes = _post_classes(_view(st, win, [item]))
    assert classes[0] == ("known", ("override",))
    assert classes[st.index_of("silver ledger")] == ("unknown", ("no_record",))


def test_a_garbled_appearance_record_on_disk_narrows_further(pier, cid):
    """The scene read itself resolves speakers through the same file, so on
    disk the item is unreadable for actors: narrower than the row, never wider."""
    st, win = pier["st"], pier["win"]
    item = st.item()
    appearances_paths._path(cid).write_text("{not json", encoding="utf-8")
    assert _view(st, win, [item]).visible() == ()


def test_a_malformed_interval_is_nothing_proven(pier, cid):
    st, win = pier["st"], pier["win"]
    record = appearances_paths.record(cid)
    record[f"characters/{pier['waid']}"]["presence"][st.sid] = [{"start": "0"}]
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    assert set(_post_classes(_view(st, win, [st.item()])).values()) == \
        {("unknown", ("no_record",))}


def test_an_unreadable_response_ledger_costs_only_authorship(pier, cid):
    st, mara = pier["st"], pier["mara"]
    item = st.item()
    responses._path(cid).write_text("[]", encoding="utf-8")
    assert _post_classes(_view(st, mara, [item]))[st.index_of("silver ledger")] == \
        ("witnessed", ("presence_interval",))


@pytest.mark.parametrize("garbage", ["{not json", json.dumps(
    {"version": 1, "entries": {"k-00000000": {"audience": "*"}}})])
def test_an_unreadable_knowledge_file_withholds_everything_from_actors(pier, cid, garbage):
    st, mara = pier["st"], pier["mara"]
    _grant(st, mara, "Mara", "unaware", keys=(_key(st, st.index_of("silver ledger")),))
    knowledge._path(cid).write_text(garbage, encoding="utf-8")
    view = _view(st, mara, [st.item()])
    assert view.visible() == ()
    [c] = _view(st, None, [st.item()], (mara,)).visible()
    assert {s.access for s in c.slices} == {"known"}


def test_an_unreadable_chronicle_holds_no_whole_scene_grant(pier, cid):
    st, mara = pier["st"], pier["mara"]
    _grant(st, mara, "Mara", "knows")           # before the absorb: digest of no record
    chronicle._chronicle_path(cid).write_text("{not json", encoding="utf-8")
    [c] = _view(st, mara, [st.item()]).items
    assert "title" not in c.header


def test_an_unreadable_source_scene_drops_the_item_for_actors(pier, monkeypatch):
    st, mara = pier["st"], pier["mara"]
    item = st.item()

    def unreadable(_cid, _sid):
        raise OSError("mid-sync")
    monkeypatch.setattr(scenes.read, "read_scene", unreadable)
    assert _view(st, mara, [item]).visible() == ()
    [c] = _view(st, None, [item], (mara,)).visible()
    assert c.slices[0].text == item.excerpts[0].text


def test_a_lock_not_acquired_turns_off_the_interval_rules(pier, monkeypatch):
    st, win = pier["st"], pier["win"]

    @contextlib.contextmanager
    def contended(_cid, timeout=2.0):
        yield False
    monkeypatch.setattr(locks, "best_effort_campaign_lock", contended)
    classes = _post_classes(_view(st, win, [st.item()]))
    assert "witnessed" not in {a for a, _b in classes.values()}
    assert set(classes.values()) == {("unknown", ("scene_cast",))}


# ------------------------------------------------------------------ purity and shape


def test_classify_takes_no_campaign_lock_and_writes_nothing(pier, cid, monkeypatch, tmp_path):
    st, mara = pier["st"], pier["mara"]
    _grant(st, mara, "Mara", "knows")
    item = st.item(summary="s")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    taken = []
    real = locks.campaign_lock

    class Guarded:
        """The lock object, which may be acquired with a timeout (the
        best-effort hold) and never entered (a blocking hold)."""

        def __init__(self, lock):
            self._lock = lock

        def __enter__(self):
            raise AssertionError("classify took a blocking campaign_lock")

        def __getattr__(self, name):
            return getattr(self._lock, name)

    def guarded(c, *a, **k):
        taken.append(c)
        return Guarded(real(c, *a, **k))
    monkeypatch.setattr(locks_runtime, "campaign_lock", guarded)
    epistemic.classify(epistemic.Perspective.of(cid, st.sid, mara, (mara,),
                                                identity=st.identity), [item])
    assert taken == [cid]
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_a_slice_renders_with_09s_own_line_renderer(pier):
    st = pier["st"]
    item = st.item()
    [c] = _view(st, None, [item], ()).visible()
    assert c.slices[0].text == budget._excerpt(item.excerpts[0].posts).text


def test_the_detail_carries_no_text(pier):
    st, mara = pier["st"], pier["mara"]
    detail = _view(st, mara, [st.item()]).detail()
    assert detail["perspective"] == f"actor:{mara}"
    assert "silver ledger" not in json.dumps(detail)
    assert {s["access"] for i in detail["items"] for s in i["slices"]} <= set(epistemic.ACCESS)
    assert {b for i in detail["items"] for s in i["slices"] for b in s["basis"]} \
        <= epistemic.BASES


# ------------------------------------------------------------------ review round


def test_leaving_ends_presence(pier):
    """An actor who left is not witness to what came after (the interval's end)."""
    st, win = pier["st"], pier["win"]
    _leave(st, pier["waid"])
    after = st.say("Once Winifred is gone, Seraphine burns the ledger.")
    classes = _post_classes(_view(st, win, [st.item()]))
    assert classes[st.index_of("silver ledger")][0] == "witnessed"
    assert classes[after] == ("unknown", ("not_recorded_present",))


def _swap_post(item: model.EvidenceItem, index: int, **changes) -> model.EvidenceItem:
    e = item.excerpts[0]
    posts = tuple(model.EvidencePost(**{**p.__dict__, **changes}) if p.index == index else p
                  for p in e.posts)
    return model.EvidenceItem(item.scene, item.title, excerpts=(model.Excerpt(posts, e.text),))


def test_a_post_whose_key_or_speaker_moved_is_dropped(pier):
    st, mara = pier["st"], pier["mara"]
    secret = st.index_of("silver ledger")
    item = st.item()
    for changes in ({"key": "r-" + _hex()}, {"key": ""}, {"speaker": "Winifred"}):
        assert _post_classes(_view(st, mara, [_swap_post(item, secret, **changes)]))[secret] \
            == ("unknown", ("moved",))


def test_a_director_note_is_never_evidence(pier):
    st, mara = pier["st"], pier["mara"]
    note = scenes.append_message(st.cid, st.sid, "assistant", "Make Mara nervous.",
                                 speaker=scenes.serialize.DIRECTOR_SPEAKER)
    item = st.item()
    forged = model.EvidencePost(note, "", None, "Grimoire", "Make Mara nervous.")
    e = item.excerpts[0]
    planted = model.EvidenceItem(item.scene, item.title, excerpts=(
        model.Excerpt((*e.posts, forged), e.text),))
    for actor in (mara, None):
        assert _post_classes(_view(st, actor, [planted], (mara,)))[note] == \
            ("unknown", ("moved",))


def test_a_whole_scene_unaware_for_an_unresolvable_group_withholds_everything(pier):
    st, mara = pier["st"], pier["mara"]
    _absorb(st, "Mara gave away the silver ledger.")
    _grant(st, "groups:watch", "The Watch", "unaware")
    view = _view(st, mara, [st.item(summary="Mara gave away the silver ledger.")])
    assert view.visible() == ()
    assert {s.basis for s in view.items[0].slices} == {("override_group",)}
    # An entry on a post beats a whole-scene one.
    _grant(st, mara, "Mara", "knows", keys=(_key(st, 0),))
    assert _post_classes(_view(st, mara, [st.item()]))[0] == ("known", ("override",))


def test_an_unresolvable_group_post_unaware_withholds_the_summary(pier):
    st, mara = pier["st"], pier["mara"]
    _absorb(st, "Mara gave away the silver ledger.")
    _grant(st, mara, "Mara", "knows")
    _grant(st, "groups:watch", "The Watch", "unaware", keys=(_key(st, 0),))
    [c] = _view(st, mara, [st.item(summary="Mara gave away the silver ledger.")]).items
    assert next(s for s in c.slices if s.part == "summary").basis == ("override",)
    assert "title" not in c.header


def test_a_resolvable_group_grant_reaches_its_members(pier, cid):
    st, win = pier["st"], pier["win"]
    stranger, _a, _v = _npc(cid, "Ilse")
    gid = overlay.create_entity(cid, "groups", "The Saltmarch Watch",
                                fields={"leader": "", "members": win})
    _grant(st, f"groups:{gid}", "The Saltmarch Watch", "knows", keys=(_key(st, 0),))
    assert _post_classes(_view(st, win, [st.item()]))[0] == ("known", ("override_group",))
    assert _post_classes(_view(st, stranger, [st.item()]))[0] == ("unknown", ("no_record",))


def test_r2_prime_needs_a_stamped_user_post(cid):
    st = Stage(cid)
    pc, paid, pvid = _npc(cid, "Seraphine")
    _enter(st, paid, pvid, role="player")
    record = appearances_paths.record(cid)
    record[f"characters/{paid}"]["presence"] = {}
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    # An unstamped line is shown under the player label, but proves nothing:
    # with two players seated it could be either.
    st.say("An unstamped line from the table.")
    scenes.append_message(cid, st.sid, "assistant", "The narrator describes the dusk.")
    p = epistemic.Perspective.of(cid, st.sid, None, (pc,), identity=st.identity)
    [c] = epistemic.classify(p, [st.item()]).items
    assert c.state == "unattributed" and c.known_to == ()
    st.say("I hold my ground.", speaker="Seraphine")
    [c] = epistemic.classify(p, [st.item()]).items
    assert c.known_to == (pc,)


def test_known_to_and_suspected_by_are_exclusive(pier):
    st, mara = pier["st"], pier["mara"]
    _grant(st, mara, "Mara", "suspects", keys=(_key(st, 0),))
    [c] = _view(st, None, [st.item()], (mara,)).items
    assert c.known_to == (mara,) and c.suspected_by == ()


def test_the_identity_is_checked_again_after_the_read(pier, monkeypatch):
    st, mara = pier["st"], pier["mara"]
    item = st.item()
    real = scenes_identity.scene_identity
    calls = []

    def flips(c, sid):
        calls.append(sid)
        return real(c, sid) if len(calls) == 1 else "f" * 32
    monkeypatch.setattr(scenes_identity, "scene_identity", flips)
    assert _view(st, mara, [item]).visible() == ()           # unreadable for actors
    calls.clear()
    monkeypatch.setattr(scenes_identity, "find_by_identity", lambda _c, _i: None)
    view = _view(st, None, [item], (mara,))
    assert view.items[0].admitted is False and view.visible() == ()   # gone for everyone


def test_with_presence_unreadable_authorship_is_off_too(pier, monkeypatch):
    st, mara = pier["st"], pier["mara"]
    item = st.item()

    class Unreadable:
        @staticmethod
        def record(_cid):
            raise ValueError("appearances.json will not parse")
    monkeypatch.setattr(epistemic_classification, "appearances_paths", Unreadable)
    assert _post_classes(_view(st, mara, [item]))[st.index_of("silver ledger")] == \
        ("unknown", ("no_record",))


def test_a_whole_scene_grant_carries_the_header(pier):
    st, mara = pier["st"], pier["mara"]
    _absorb(st, "Mara gave away the silver ledger.")
    _grant(st, mara, "Mara", "knows")
    item = model.EvidenceItem(model.SceneRef(st.sid, st.identity), "The Pier",
                              date="Day 3", location="The pier",
                              summary="Mara gave away the silver ledger.")
    [c] = _view(st, mara, [item]).visible()
    assert c.header == {"title": "The Pier", "date": "Day 3", "location": "The pier"}
    assert c.item.scene.sid == "" and c.item.title == "The Pier"


def test_a_partial_attendee_gets_no_date_or_location(pier):
    """09's location is the scene's last: a place Winifred may never have been."""
    st, win = pier["st"], pier["win"]
    [c] = _view(st, win, [st.item()]).visible()
    assert c.header == {} and c.item.location == "" and c.item.date == ""


def test_the_narrator_reads_a_legacy_scene_unannotated(cid):
    st = Stage(cid)
    mara, aid, vid = _npc(cid, "Mara")
    _enter(st, aid, vid)
    st.say("Something happened before intervals were recorded.")
    record = appearances_paths.record(cid)
    record[f"characters/{aid}"]["presence"] = {}
    appearances_paths._path(cid).write_text(json.dumps(record), encoding="utf-8")
    [c] = _view(st, None, [st.item()], (mara,)).items
    assert c.state == "unattributed" and c.known_to == ()


def test_presence_follows_a_renamed_scene(pier, cid):
    st, win = pier["st"], pier["win"]
    item = st.item()
    st.sid = scenes.rename_scene(cid, st.sid, "The Old Pier")
    classes = _post_classes(_view(st, win, [item]))
    assert classes[st.index_of("silver ledger")] == ("witnessed", ("presence_interval",))


def test_an_unexpected_failure_narrows_and_never_raises(pier, monkeypatch):
    st, mara = pier["st"], pier["mara"]
    item = st.item()

    def boom(*_a, **_k):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "campaign.md")
    monkeypatch.setattr(epistemic.regex_view, "view", boom)
    assert _view(st, mara, [item]).visible() == ()
    [c] = _view(st, None, [item], (mara,)).visible()
    assert c.slices[0].text == item.excerpts[0].text
    monkeypatch.undo()
    monkeypatch.setattr(epistemic_classification, "_classify_item", boom)
    assert _view(st, mara, [item]).visible() == ()


def test_a_perspective_refuses_an_actor_that_is_not_an_npc(cid):
    with pytest.raises(ValueError):
        epistemic.Perspective.of(cid, "s", "pcs:seraphine", ())
