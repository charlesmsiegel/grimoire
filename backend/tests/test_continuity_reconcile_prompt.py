"""The reconciliation sweep's one model call (capstone spec §11.2-§11.4, §23, §24).

`select` chooses what is asked -- capped, prioritized, and never a finding that
already has a proposal or that the reader already answered. `build_payload`
bounds what is sent: the records the chosen candidates name, their last beats
and the scene lines around them, never a transcript. `proposals_of` trusts
nothing the reply says: a word outside the candidate's vocabulary or a
direction §5.3 does not allow is ``uncertain``, and a closure without an
evidence scene its item showed is ``uncertain`` too.

Payloads are built through `select` / `build_payload` over a seeded campaign,
so what is read is what the items actually showed.
"""

from __future__ import annotations

import pytest

from grimoire import decisions, prompts
from grimoire.store import (
    campaigns,
    characters,
    chronicle,
    clock,
    commitments,
    events,
    plot,
    scenes,
    worlds,
)
from grimoire.store.continuity import candidates, canon, doc, pending, reconcile

MAP = "thread:mara-s-map"
CHART = "thread:winifred-s-chart"
LEDGER = "thread:find-the-ledger"
OATH = "commitment:mara-s-oath"
DEBT = "commitment:winifred-s-debt"
VOW = "commitment:seraphine-s-vow"


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    characters.create_character(worlds.world_root(wid), "Mara", "default",
                                characters.blank_card("Mara"))
    return campaigns.create_campaign("Run", wid)


@pytest.fixture
def s0(cid):
    return scenes.create_scene(cid, "Saltmarch docks")


def _thread(cid, ref, title, beat, scene, status="open"):
    plot.set_movement(cid, ref.partition(":")[2], title, status, beat, scene)


def _commitment(cid, ref, title, beat, scene, due="", kind="promise"):
    commitments.set_movement(cid, ref.partition(":")[2], title, kind, "open", due, beat, scene)


def _record(cid, kind, refs, signals, proposal=None):
    fp = pending.fingerprint(pending.Current.load(cid), kind, refs)
    assert fp is not None
    return canon.candidate_id(kind, refs), {
        "kind": kind, "refs": list(refs), "fingerprint": fp, "signals": signals,
        "proposal": proposal, "created": ""}


def _cache(cid, *records):
    data = candidates.empty()
    data["records"] = dict(records)
    candidates.write(cid, data)


def _pair_signals(**over):
    return {"title_exact": False, "slug_equal": False, "lexical": 0.3, "cosine": None,
            "shared_actors": [], "shared_scenes": [], "shared_anchors": [],
            "via": "lexical", **over}


def _proposal(**over):
    return {"decision": "close", "from": "", "to": "", "relation": "", "status": "closed",
            "reason": "It was answered.", "evidence_scenes": [], **over}


def _sweep(cid, *, stamp="00000000000000000010-run"):
    return reconcile.discover(cid, stamp=stamp, full=True, embed=False)


def _payload(cid, sweep=None):
    sweep = sweep or reconcile.Sweep("00000000000000000010-run", True)
    return reconcile.build_payload(cid, reconcile.select(cid, sweep))


def _by_id(payload, candidate_id):
    [cand] = [c for c in payload["candidates"] if c["id"] == candidate_id]
    return cand


def _user(payload):
    """Every item's context, as the model is shown them."""
    return "\n\n".join(item.context for item in reconcile.build_items(payload))


def _decided(payload, key, **given):
    """The proposal for candidate `key` when the reply answers only it (every
    other candidate ``uncertain``)."""
    order = [c["id"] for c in payload["candidates"]]
    per_item = [{} for _ in order]
    per_item[order.index(key)] = given
    return _proposals(payload, *per_item)[key]


def _closure(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up in Saltmarch.", s0)
    key, rec = _record(cid, "possible_thread_closure", [MAP],
                       {"reason": "stale", "days_since": 75})
    _cache(cid, (key, rec))
    return key


# ------------------------------------------------------------------ reading answers


def test_cross_type_duplicate_is_uncertain(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up.", s0)
    _commitment(cid, OATH, "Mara's oath", "Mara swore to find the map.", s0)
    key, rec = _record(cid, "possible_relation", [OATH, MAP], _pair_signals())
    _cache(cid, (key, rec))
    payload = _payload(cid)
    assert _by_id(payload, key)["vocabulary"] == "cross"

    got = _decided(payload, key, decision="duplicate", **{"from": "A", "to": "B"})
    assert got["decision"] == "uncertain"
    assert (got["relation"], got["from"], got["to"]) == ("", "", "")


def test_disallowed_direction_is_uncertain(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up.", s0)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s0)
    _commitment(cid, OATH, "Mara's oath", "Mara swore to find the map.", s0)
    _commitment(cid, DEBT, "Winifred's debt", "Winifred owes the salt.", s0, kind="debt")
    cross_key, cross = _record(cid, "possible_relation", [OATH, MAP], _pair_signals())
    owed_key, owed = _record(cid, "possible_duplicate", [OATH, DEBT], _pair_signals())
    plot_key, plots = _record(cid, "possible_duplicate", [MAP, CHART], _pair_signals())
    _cache(cid, (cross_key, cross), (owed_key, owed), (plot_key, plots))
    payload = _payload(cid)
    cross_c = _by_id(payload, cross_key)
    assert [r["ref"] for r in cross_c["records"]] == [OATH, MAP]
    assert [r["letter"] for r in cross_c["records"]] == ["A", "B"]
    assert _by_id(payload, owed_key)["vocabulary"] == "same_commitment"
    assert _by_id(payload, plot_key)["vocabulary"] == "same_thread"

    def decide(key, decision, frm, to):
        return _decided(payload, key, decision=decision,
                        **{"from": frm or None, "to": to or None})

    # pays_off runs from the thread to the commitment, never the other way
    assert decide(cross_key, "pays_off", "A", "B")["decision"] == "uncertain"
    good = decide(cross_key, "pays_off", "B", "A")
    assert (good["decision"], good["relation"], good["from"], good["to"]) == (
        "pays_off", "pays_off", MAP, OATH)
    # a commitment never continues, nor is a subthread of, another
    assert decide(owed_key, "subthread", "A", "B")["decision"] == "uncertain"
    assert decide(owed_key, "continuation", "A", "B")["decision"] == "uncertain"
    # a duplicate needs two different records, and a direction at all
    assert decide(plot_key, "duplicate", "A", "A")["decision"] == "uncertain"
    assert decide(plot_key, "duplicate", "", "")["decision"] == "uncertain"
    assert decide(plot_key, "continuation", "A", "C")["decision"] == "uncertain"
    dup = decide(plot_key, "duplicate", "B", "A")
    assert (dup["decision"], dup["relation"], dup["from"], dup["to"]) == (
        "duplicate", "", CHART, MAP)
    sub = decide(plot_key, "subthread", "A", "B")
    assert (sub["decision"], sub["relation"], sub["from"], sub["to"]) == (
        "subthread", "subthread_of", MAP, CHART)
    cont = decide(plot_key, "continuation", "B", "A")
    assert (cont["relation"], cont["from"], cont["to"]) == ("continues", CHART, MAP)
    related = decide(owed_key, "related", "", "")
    assert (related["decision"], related["relation"]) == ("related", "related_to")
    assert {related["from"], related["to"]} == {OATH, DEBT}


def test_temporal_words_name_the_commitment_and_the_event(cid, s0):
    clock.advance(cid, to="2026-05-10")
    _commitment(cid, OATH, "Mara's oath", "Mara swore it.", s0, due="before the bells stop")
    event = "event:" + events.create(cid, "The coronation", "2026-05-13")
    sweep = _sweep(cid)
    key = canon.candidate_id("possible_relation", [OATH, event])
    assert key in sweep.model_only
    payload = _payload(cid, sweep)
    cand = _by_id(payload, key)
    assert cand["vocabulary"] == "temporal"
    [_, ev] = cand["records"]
    assert ev["line"] == "event: The coronation (2026-05-13)"

    got = _decided(payload, key, decision="before")
    assert (got["decision"], got["relation"], got["from"], got["to"]) == (
        "before", "before", OATH, event)
    got = _decided(payload, key, decision="unrelated")
    assert (got["decision"], got["relation"], got["from"], got["to"]) == (
        "unrelated", "", "", "")


def test_actors_are_sent_per_record(cid, s0):
    clock.advance(cid, to="2026-05-10")
    chronicle.absorb(cid, {"id": s0, "one_line": "Mara came ashore.",
                           "cast": ["characters/mara"]})
    _closure(cid, s0)
    quiet = scenes.create_scene(cid, "Realm road")
    _commitment(cid, OATH, "Mara's oath", "An oath was sworn.", quiet,
                due="before the bells stop")
    event = "event:" + events.create(cid, "The coronation", "2026-05-13")
    sweep = _sweep(cid)
    payload = _payload(cid, sweep)

    closure = _by_id(payload, canon.candidate_id("possible_thread_closure", [MAP]))
    assert closure["records"][0]["actors"] == ["Mara"]
    temporal = _by_id(payload, canon.candidate_id("possible_relation", [OATH, event]))
    assert [r["actors"] for r in temporal["records"]] == [[], []]
    user = _user(payload)
    assert "people: Mara" in user
    assert user.count("people:") == 1


# ---------------------------------------------------------------- selection


def test_select_prioritizes_overdue_then_duplicates_and_caps(cid, s0, monkeypatch):
    _thread(cid, MAP, "Mara's map", "The map turned up.", s0)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s0)
    _thread(cid, LEDGER, "Find the ledger", "The ledger is somewhere.", s0)
    _commitment(cid, OATH, "Mara's oath", "Mara swore it.", s0, due="2026-05-05")
    stale = _record(cid, "possible_thread_closure", [LEDGER],
                    {"reason": "stale", "days_since": 75})
    weak = _record(cid, "possible_duplicate", [CHART, LEDGER], _pair_signals(lexical=0.5))
    strong = _record(cid, "possible_duplicate", [MAP, CHART],
                     _pair_signals(title_exact=True, lexical=0.2))
    overdue = _record(cid, "possible_commitment_resolution", [OATH],
                      {"reason": "overdue", "in_days": -5, "via": "deadline"})
    _cache(cid, stale, weak, strong, overdue)
    sweep = reconcile.Sweep("00000000000000000010-run", True)

    every = reconcile.select(cid, sweep)
    assert [s["id"] for s in every] == [overdue[0], strong[0], weak[0], stale[0]]
    assert set(every[0]) == {"id", "kind", "refs", "signals", "fingerprint"}
    assert every[0]["fingerprint"] == overdue[1]["fingerprint"]

    monkeypatch.setattr(reconcile, "RECONCILE_MAX_CANDIDATES", 2)
    assert [s["id"] for s in reconcile.select(cid, sweep)] == [overdue[0], strong[0]]


def test_select_skips_cached_proposals(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up.", s0)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s0)
    answered = _record(cid, "possible_thread_closure", [MAP],
                       {"reason": "stale", "days_since": 75}, _proposal(evidence_scenes=[s0]))
    open_ = _record(cid, "possible_thread_closure", [CHART],
                    {"reason": "stale", "days_since": 75})
    _cache(cid, answered, open_)
    sweep = reconcile.Sweep("00000000000000000010-run", False)
    # a model-only re-check of the answered record is not asked again either
    sweep.model_only = {answered[0]: {**answered[1], "proposal": None,
                                      "signals": {"reason": "touched"}}}
    assert [s["id"] for s in reconcile.select(cid, sweep)] == [open_[0]]


def test_select_skips_hidden_cached_findings(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up.", s0)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s0)
    moved = _record(cid, "possible_thread_closure", [MAP], {"reason": "stale"})
    kept = _record(cid, "possible_thread_closure", [CHART], {"reason": "stale"})
    _cache(cid, moved, kept)
    _thread(cid, MAP, "", "Mara burned a corner of it.", s0, status="advanced")
    sweep = reconcile.Sweep("00000000000000000010-run", True)
    assert [s["id"] for s in reconcile.select(cid, sweep)] == [kept[0]]
    sweep.continuity = "malformed"
    assert reconcile.select(cid, sweep) == []


def test_select_skips_suppressed_and_satisfied_model_only_nominations(cid, s0):
    clock.advance(cid, to="2026-05-10")
    _commitment(cid, OATH, "Mara's oath", "Mara swore it.", s0, due="before the bells stop")
    dismissed, related, kept = ("event:" + events.create(cid, name, date) for name, date in (
        ("The coronation", "2026-05-13"), ("Saltmarch Eve", "2026-05-15"),
        ("Mara's audience", "2026-05-17")))
    fp = pending.fingerprint(pending.Current.load(cid), "possible_relation", [OATH, dismissed])
    assert fp is not None
    doc.put_suppression(cid, fp, {"kind": "possible_relation", "refs": [OATH, dismissed],
                                  "decision": "dismiss", "created": ""})
    doc.put_link(cid, canon.link_id("related_to", OATH, related),
                 {"a": OATH, "b": related, "relation": "related_to", "created": "",
                  "scene": "", "note": ""})
    hidden = {canon.candidate_id("possible_relation", [OATH, e]) for e in (dismissed, related)}
    visible = canon.candidate_id("possible_relation", [OATH, kept])

    for n in range(2):
        sweep = _sweep(cid, stamp=f"0000000000000000001{n}-run")
        reconcile.persist_found(cid, sweep)
        selected = reconcile.select(cid, sweep)
        assert not hidden & {s["id"] for s in selected}
        assert visible in {s["id"] for s in selected}
        payload = reconcile.build_payload(cid, selected)
        text = _user(payload)
        assert "The coronation" not in text
        assert "event: Saltmarch Eve (" not in text
        assert "event: Mara's audience (2026-05-17)" in text

    # A dismissal that lands between discovery and selection is honoured too.
    sweep = _sweep(cid, stamp="00000000000000000020-run")
    fp = pending.fingerprint(pending.Current.load(cid), "possible_relation", [OATH, kept])
    assert fp is not None
    doc.put_suppression(cid, fp, {"kind": "possible_relation", "refs": [OATH, kept],
                                  "decision": "dismiss", "created": ""})
    assert visible not in {s["id"] for s in reconcile.select(cid, sweep)}


# ------------------------------------------------------------------ payload


def test_known_scenes_are_the_shown_beats_and_chronicle_lines(cid, monkeypatch):
    s1, s2, s3 = (scenes.create_scene(cid, title)
                  for title in ("Saltmarch docks", "Realm road", "Winifred's house"))
    for sid, line in ((s1, "Mara came ashore."), (s2, "The road was long."),
                      (s3, "Winifred opened the door.")):
        chronicle.absorb(cid, {"id": sid, "one_line": line})
    _closure(cid, s1)
    monkeypatch.setattr(reconcile, "RECONCILE_RECENT_SCENES", 1)
    payload = _payload(cid)

    assert [c["id"] for c in payload["chronicle"]] == sorted([s1, s3])
    assert {c["one_line"] for c in payload["chronicle"]} == {
        "Mara came ashore.", "Winifred opened the door."}
    assert payload["known_scenes"] == sorted({s1, s3})
    [record] = payload["candidates"][0]["records"]
    assert record["beats"] == [{"scene": s1, "text": "The map turned up in Saltmarch."}]
    user = _user(payload)
    assert f"{s3} — Winifred opened the door." in user
    assert "The road was long." not in user
    # A scene the item did not show is not an option, so it cannot be evidence.
    got = _decided(payload, payload["candidates"][0]["id"], decision="close",
                   evidence_scene=s2)
    assert got["decision"] == "uncertain"


def test_a_deleted_scene_is_not_known_evidence(cid, s0):
    """`delete_scene` leaves beats and the chronicle line naming the scene behind,
    but persist 1 voids a proposal citing a scene `list_scenes` does not have. A
    deleted scene is therefore never shown as evidence: its beat keeps its text
    without the marker, its chronicle line is not shown, and a closure citing it
    is ``uncertain`` rather than a proposal nobody could ever apply."""
    gone = scenes.create_scene(cid, "Realm road")
    _thread(cid, MAP, "Mara's map", "The map was found on the road.", gone)
    chronicle.absorb(cid, {"id": gone, "one_line": "Mara found the map."})
    scenes.delete_scene(cid, gone)
    key, rec = _record(cid, "possible_thread_closure", [MAP],
                       {"reason": "stale", "days_since": 75})
    _cache(cid, (key, rec))
    payload = _payload(cid)

    assert gone not in payload["known_scenes"]
    assert gone not in {line["id"] for line in payload["chronicle"]}
    [record] = _by_id(payload, key)["records"]
    assert record["beats"] == [{"scene": "", "text": "The map was found on the road."}]
    user = _user(payload)
    assert gone not in user
    assert "  The map was found on the road." in user
    got = _decided(payload, key, decision="close", evidence_scene=gone)
    assert got["decision"] == "uncertain"
    assert got["evidence_scenes"] == []
    live = {s["id"] for s in scenes.list_scenes(cid)}
    assert set(payload["known_scenes"]) <= live


def test_the_recent_window_is_the_last_live_scenes_in_play_order(cid, monkeypatch):
    """Deleted scenes' chronicle lines cannot crowd live scenes out of the
    recent window."""
    s1, s2, s3 = (scenes.create_scene(cid, title)
                  for title in ("Saltmarch docks", "Realm road", "Winifred's house"))
    for sid, line in ((s1, "Mara came ashore."), (s2, "The road was long."),
                      (s3, "Winifred opened the door.")):
        chronicle.absorb(cid, {"id": sid, "one_line": line})
    scenes.delete_scene(cid, s3)
    _closure(cid, s1)
    monkeypatch.setattr(reconcile, "RECONCILE_RECENT_SCENES", 1)
    payload = _payload(cid)
    assert [line["id"] for line in payload["chronicle"]] == [s1, s2]
    assert payload["known_scenes"] == [s1, s2]


def test_a_cross_candidate_names_each_records_type(cid, s0):
    """A cross pair is stored commitment first (refs sort), so A is the
    commitment. Each record line says its type, so a reply reading the heading
    cannot take A for the plot thread and lose a real ``pays_off``."""
    _thread(cid, LEDGER, "Find the ledger", "Winifred learned the ledger exists.", s0)
    _commitment(cid, OATH, "Mara's oath", "Mara swore to find the ledger.", s0)
    key, rec = _record(cid, "possible_relation", [OATH, LEDGER], _pair_signals())
    _cache(cid, (key, rec))
    payload = _payload(cid)
    cand = _by_id(payload, key)
    assert [(r["letter"], r["type"]) for r in cand["records"]] == [
        ("A", "commitment"), ("B", "plot thread")]
    user = _user(payload)
    assert "\nA (commitment): mara-s-oath: Mara's oath (promise, open)" in user
    assert "\nB (plot thread): find-the-ledger: Find the ledger (open)" in user


def test_an_event_record_line_names_itself(cid, s0):
    clock.advance(cid, to="2026-05-10")
    _commitment(cid, OATH, "Mara's oath", "Mara swore it.", s0, due="before the bells stop")
    event = "event:" + events.create(cid, "The coronation", "2026-05-13")
    sweep = _sweep(cid)
    payload = _payload(cid, sweep)
    cand = _by_id(payload, canon.candidate_id("possible_relation", [OATH, event]))
    assert [r["type"] for r in cand["records"]] == ["commitment", ""]
    user = _user(payload)
    assert "\nA (commitment): mara-s-oath: Mara's oath (" in user
    assert "\nB: event: The coronation (2026-05-13)" in user


def test_beats_are_capped_and_newest_last(cid):
    sids = [scenes.create_scene(cid, f"Saltmarch {n}") for n in range(5)]
    for n, sid in enumerate(sids):
        _thread(cid, MAP, "Mara's map", f"Beat {n}.", sid)
    _cache(cid, _record(cid, "possible_thread_closure", [MAP], {"reason": "stale"}))
    [record] = _payload(cid)["candidates"][0]["records"]
    assert len(record["beats"]) == reconcile.RECONCILE_BEATS == 3
    assert record["beats"] == [{"scene": sids[n], "text": f"Beat {n}."} for n in (2, 3, 4)]


def test_a_pathologically_long_record_cannot_unbound_the_prompt(cid, s0):
    """A ledger route stores a title, due or beat of any length, and a
    chronicle line is hand-editable. Only counts bound the sweep's selection,
    so a long note pasted as a beat would ride whole into the one shared call --
    past the model's context, failing every candidate in it on every pass. Each
    record shares `RECONCILE_RECORD_BYTES` (the identity resolver's bound for
    the same records) and each scene line `RECONCILE_SCENE_LINE_BYTES`; the
    letters still name the records back."""
    huge = "harbour " * 25_000
    clock.advance(cid, to="2026-05-10")
    chronicle.absorb(cid, {"id": s0, "one_line": "Mara came ashore. " + huge})
    _thread(cid, LEDGER, "Find the harbour ledger " + huge, "Mara heard of it.", s0)
    _thread(cid, LEDGER, "Find the harbour ledger " + huge, huge, s0)
    _thread(cid, MAP, "Recover the harbour ledger", "Winifred asked after it.", s0)
    _commitment(cid, OATH, "Mara's oath " + huge, huge, s0, due=huge)
    doc.put_link(cid, canon.link_id("pays_off", MAP, OATH),
                 {"a": MAP, "b": OATH, "relation": "pays_off", "created": "", "scene": "",
                  "note": ""})
    event = "event:" + events.create(cid, "The coronation " + huge, "2026-05-13")
    pair_key, pair = _record(cid, "possible_duplicate", [LEDGER, MAP],
                             _pair_signals(title_exact=True))
    when_key, when = _record(cid, "possible_relation", [OATH, event], _pair_signals())
    _cache(cid, (pair_key, pair), (when_key, when))
    payload = _payload(cid)
    user = _user(payload)

    assert len(payload["candidates"]) == 2
    assert len(user.encode("utf-8")) < 12_000
    assert "\nA (plot thread): find-the-ledger: Find the harbour ledger harbour" in user
    assert "\nB (plot thread): mara-s-map: Recover the harbour ledger (open)\n" in user
    assert f"  [{s0}] Winifred asked after it." in user
    assert f"- {s0} — Mara came ashore. harbour" in user
    assert "\nB: event: The coronation harbour" in user
    assert " (2026-05-13)\nsignals:" in user
    got = _decided(payload, pair_key, decision="duplicate", **{"from": "A", "to": "B"})
    assert (got["decision"], got["from"], got["to"]) == ("duplicate", LEDGER, MAP)


def test_no_transcript_text_is_sent(cid, s0):
    scenes.append_message(cid, s0, "user", "Seraphine whispered the lighthouse password.")
    scenes.append_message(cid, s0, "assistant", "Mara nodded and pocketed the brass key.")
    chronicle.absorb(cid, {"id": s0, "one_line": "Mara came ashore."})
    _closure(cid, s0)
    text = _user(_payload(cid))
    assert "Mara came ashore." in text
    assert "lighthouse password" not in text
    assert "brass key" not in text


def test_build_items_render_with_no_optional_fields():
    payload = {"now": "", "chronicle": [], "known_scenes": [], "recent": [], "candidates": [
        {"key": "c1", "id": "possible_thread_closure-0123456789abcdef",
         "vocabulary": "thread", "signal_text": "",
         "records": [{"letter": "A", "ref": MAP, "type": "plot thread",
                      "line": "mara-s-map: Mara's map (open)",
                      "beats": [], "pressure": "", "links": [], "actors": []}]}]}
    [item] = reconcile.build_items(payload)
    assert item.context == ("Candidate — whether a plot thread is finished\n"
                            "A (plot thread): mara-s-map: Mara's map (open)")


def test_the_item_context_shows_every_part(cid, s0):
    clock.advance(cid, to="2026-05-10")
    chronicle.absorb(cid, {"id": s0, "one_line": "Mara came ashore.",
                           "cast": ["characters/mara"]})
    _thread(cid, MAP, "Mara's map", "The map turned up in Saltmarch.", s0)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s0)
    _commitment(cid, OATH, "Mara's oath", "Mara swore to find the map.", s0)
    doc.put_link(cid, canon.link_id("pays_off", MAP, OATH),
                 {"a": MAP, "b": OATH, "relation": "pays_off", "created": "", "scene": "",
                  "note": ""})
    key, rec = _record(cid, "possible_duplicate", [MAP, CHART],
                       _pair_signals(title_exact=True, lexical=0.42, cosine=0.81,
                                     shared_actors=["characters:mara"], shared_scenes=[s0]))
    _cache(cid, (key, rec))
    payload = _payload(cid)
    assert payload["now"]
    user = _user(payload)
    assert user.startswith(f"Campaign date: {payload['now']}\nRecent scenes:\n"
                           f"- {s0} — Mara came ashore.\n\nCandidate — two plot threads\n")
    assert "A (plot thread): mara-s-map: Mara's map (open)\n" in user
    assert f"  [{s0}] The map turned up in Saltmarch.\n" in user
    assert "  links: Mara's map pays_off Mara's oath\n" in user
    assert user.endswith("signals: same title; word overlap 0.42; meaning 0.81; "
                         "shared characters: Mara; shared scenes: 1")


def test_the_questions_carry_the_criteria():
    text = "\n".join([*(prompts.render("continuity_reconcile/question.j2", vocabulary=vocab)
                       for vocab in reconcile.DECISIONS),
                      prompts.render("continuity_reconcile/evidence.j2")])
    assert text.startswith(
        "You are reviewing a campaign's story ledger for records that may overlap or be finished")
    for opener in ("You are absorbing a completed role-play scene",
                   "You are auditing a completed role-play scene",
                   "You are checking whether newly proposed story records"):
        assert opener not in text
    for vocab, words in reconcile.DECISIONS.items():
        question = prompts.render("continuity_reconcile/question.j2", vocabulary=vocab)
        for word in words:
            assert f'"{word}"' in question, (vocab, word)
    for needle in ('"duplicate" only when both records are the same question or obligation',
                   ('a narrower or later question is "continuation" or "subthread", '
                    'not "duplicate"'),
                   'A thread and a commitment are never "duplicate"',
                   "Age alone is never evidence that a thread is finished",
                   "A passed deadline alone is never evidence that a promise was kept or broken",
                   "name at least one evidence scene id from the lines shown",
                   "Do not invent a date"):
        assert needle in text


def test_the_vocabulary_is_the_global_constraints_one():
    assert reconcile.DECISIONS == {
        "same_thread": ("duplicate", "continuation", "subthread", "related", "distinct",
                        "uncertain"),
        "same_commitment": ("duplicate", "related", "distinct", "uncertain"),
        "cross": ("pays_off", "related", "distinct", "uncertain"),
        "thread": ("close", "keep_open", "uncertain"),
        "commitment": ("fulfilled", "broken", "expired", "keep_open", "uncertain"),
        "temporal": ("before", "on", "after", "by", "unrelated", "uncertain"),
    }
    assert reconcile.vocabulary({"kind": "possible_duplicate",
                                 "refs": [OATH, DEBT]}) == "same_commitment"
    assert reconcile.vocabulary({"kind": "possible_duplicate",
                                 "refs": [MAP, CHART]}) == "same_thread"
    assert reconcile.vocabulary({"kind": "possible_relation", "refs": [OATH, MAP]}) == "cross"
    assert reconcile.vocabulary({"kind": "possible_relation",
                                 "refs": [OATH, "event:e1"]}) == "temporal"
    assert reconcile.vocabulary({"kind": "possible_thread_closure", "refs": [MAP]}) == "thread"
    assert reconcile.vocabulary({"kind": "possible_commitment_resolution",
                                 "refs": [OATH]}) == "commitment"


# ------------------------------------------------------ as decision items
#
# The sweep's adjudication as `decide()` items (spec §7.4): one per candidate,
# its context self-contained, its scenes only the ones it shows. These are what
# the sweep sends and reads.

EVENT = "event:the-coronation"
D1, D2, D3, D4 = ("0001--saltmarch-docks", "0002--realm-road", "0003--winifreds-house",
                  "0004--saltmarch-quay")


def _shown(letter, ref, *scenes):
    """A record as `_record_view` shapes it, with one beat per scene."""
    prefix, _, rid = ref.partition(":")
    fields = {"title": rid.replace("-", " ").capitalize(), "status": "open", "kind": "",
              "due": "2026-05-13" if prefix == "event" else ""}
    return {"letter": letter, "ref": ref,
            "type": {"thread": "plot thread", "commitment": "commitment"}.get(prefix, ""),
            "line": reconcile.snippet_line(ref, fields),
            "beats": [{"scene": sid, "text": f"Something happened in {sid}."} for sid in scenes],
            "pressure": "", "links": [], "actors": []}


def _cand(n, vocabulary, *records, signal_text="word overlap 0.30"):
    return {"key": f"c{n}", "id": f"candidate-{n}", "vocabulary": vocabulary,
            "records": list(records), "signal_text": signal_text}


def _hand(*cands, now="", lines=(), recent=()):
    """A `build_payload`-shaped payload with no store: its known scenes are
    the beat scenes and the chronicle lines shown, as `build_payload`'s are."""
    beats = {b["scene"] for c in cands for r in c["records"] for b in r["beats"]} - {""}
    chron = [{"id": sid, "one_line": text} for sid, text in lines]
    return {"now": now, "chronicle": chron, "recent": list(recent), "candidates": list(cands),
            "known_scenes": sorted(beats | {line["id"] for line in chron})}


def _six():
    """One candidate per vocabulary: Mara's map shows two scenes, Mara's oath one."""
    return _hand(
        _cand(1, "same_thread", _shown("A", MAP, D1, D2), _shown("B", CHART)),
        _cand(2, "same_commitment", _shown("A", OATH, D1), _shown("B", DEBT)),
        _cand(3, "cross", _shown("A", OATH, D1), _shown("B", MAP, D1, D2)),
        _cand(4, "temporal", _shown("A", OATH, D1), _shown("B", EVENT)),
        _cand(5, "thread", _shown("A", MAP, D1, D2)),
        _cand(6, "commitment", _shown("A", OATH, D1)),
        lines=((D1, "Mara came ashore."), (D2, "The road was long.")))


def _answers(item, **given):
    """Every question `item` asks, null unless given; `decision` defaults to
    ``uncertain``."""
    out = {q.id: None for q in item.questions}
    out[reconcile.DECISION_ID] = "uncertain"
    out.update(given)
    return out


def _proposals(payload, *per_item, rationales=()):
    from tests.llm_fakes import decision_reply

    items = reconcile.build_items(payload)
    answers = [_answers(item, **(per_item[n] if n < len(per_item) else {}))
               for n, item in enumerate(items)]
    results = decisions.parse(decision_reply(*answers, rationales=rationales), items,
                              explain=True)
    return reconcile.proposals_of(payload, results)


def test_build_payload_names_the_recent_window(cid, monkeypatch):
    s1, s2, s3 = (scenes.create_scene(cid, title)
                  for title in ("Saltmarch docks", "Realm road", "Winifred's house"))
    _closure(cid, s1)
    monkeypatch.setattr(reconcile, "RECONCILE_RECENT_SCENES", 2)
    payload = _payload(cid)
    assert payload["recent"] == [s2, s3]
    monkeypatch.setattr(reconcile, "RECONCILE_RECENT_SCENES", 0)
    assert _payload(cid)["recent"] == []


def test_build_items_one_per_candidate_under_its_vocabulary():
    payload = _six()
    items = reconcile.build_items(payload)
    decisions.validate(items)
    assert len(items) == len(payload["candidates"])
    for item, cand in zip(items, payload["candidates"], strict=True):
        vocab = cand["vocabulary"]
        decision = item.questions[0]
        assert decision.id == reconcile.DECISION_ID
        assert isinstance(decision, decisions.Choice) and not decision.allow_none
        assert decision.instructions == prompts.render("continuity_reconcile/question.j2",
                                                       vocabulary=vocab)
        assert [(o.id, o.description) for o in decision.options] == [
            (w, w.replace("_", " ")) for w in reconcile.DECISIONS[vocab]]
        assert f"Candidate — {reconcile.LABELS[vocab]}\n" in item.context
        assert cand["key"] not in item.context.split("\n")[0]
        for rec in cand["records"]:
            assert rec["line"] in item.context
    assert reconcile.explain() == prompts.render("continuity_reconcile/explain.j2")


def test_pair_items_ask_a_direction_and_the_others_do_not():
    payload = _six()
    for item, cand in zip(reconcile.build_items(payload), payload["candidates"], strict=True):
        ids = [q.id for q in item.questions]
        if cand["vocabulary"] in reconcile.PAIR_VOCABULARIES:
            assert ids[:3] == [reconcile.DECISION_ID, reconcile.FROM_ID, reconcile.TO_ID]
            frm, to = item.questions[1:3]
            assert frm.instructions == prompts.render("continuity_reconcile/direction.j2")
            assert to.instructions == prompts.render("continuity_reconcile/direction_to.j2")
            for q in (frm, to):
                assert isinstance(q, decisions.Choice) and q.allow_none
                assert [(o.id, o.description) for o in q.options] == [
                    ("A", "record A"), ("B", "record B")]
        else:
            assert reconcile.FROM_ID not in ids and reconcile.TO_ID not in ids
    assert reconcile.PAIR_VOCABULARIES == ("same_thread", "same_commitment", "cross")


def _evidence_options(item):
    return [[o.id for o in q.options] for q in item.questions
            if q.id in reconcile.EVIDENCE_IDS]


def test_evidence_options_are_the_scenes_the_item_shows(cid, monkeypatch):
    s1, s2 = (scenes.create_scene(cid, title) for title in ("Saltmarch docks", "Realm road"))
    gone = scenes.create_scene(cid, "Winifred's house")
    for sid, line in ((s1, "Mara came ashore."), (s2, "The road was long."),
                      (gone, "Winifred opened the door.")):
        chronicle.absorb(cid, {"id": sid, "one_line": line})
    _thread(cid, MAP, "Mara's map", "The map turned up.", s1)
    _thread(cid, MAP, "Mara's map", "The map was copied.", gone)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s2)
    scenes.delete_scene(cid, gone)
    one = _record(cid, "possible_thread_closure", [MAP], {"reason": "stale"})
    two = _record(cid, "possible_thread_closure", [CHART], {"reason": "stale"})
    _cache(cid, one, two)
    monkeypatch.setattr(reconcile, "RECONCILE_RECENT_SCENES", 0)
    payload = _payload(cid)
    by_id = dict(zip((c["id"] for c in payload["candidates"]),
                     reconcile.build_items(payload), strict=True))

    assert reconcile.item_scenes(payload, _by_id(payload, one[0])) == [s1]
    assert _evidence_options(by_id[one[0]]) == [[s1]]
    assert _evidence_options(by_id[two[0]]) == [[s2]]
    # A deleted scene is offered nowhere, and another candidate's scene is
    # neither offered nor shown.
    assert gone not in by_id[one[0]].context
    assert s2 not in by_id[one[0]].context and "The road was long." not in by_id[one[0]].context


def test_an_item_asks_one_evidence_question_per_shown_scene_up_to_three():
    scenes_ = (D1, D2, D3, D4)
    payload = _hand(*(_cand(n + 1, "thread", _shown("A", MAP, *scenes_[:n]))
                      for n in range(5)))
    for n, item in enumerate(reconcile.build_items(payload)):
        asked = [q for q in item.questions if q.id in reconcile.EVIDENCE_IDS]
        count = min(n, reconcile.EVIDENCE_SCENES)
        assert [q.id for q in asked] == list(reconcile.EVIDENCE_IDS[:count])
        for k, q in enumerate(asked):
            assert isinstance(q, decisions.Choice) and q.allow_none
            assert [o.id for o in q.options] == list(scenes_[:n])
            assert q.instructions == prompts.render(
                "continuity_reconcile/evidence.j2" if k == 0
                else "continuity_reconcile/evidence_more.j2")
    assert reconcile.EVIDENCE_SCENES == 3
    assert reconcile.EVIDENCE_IDS == ("evidence_scene", "evidence_scene_2",
                                      "evidence_scene_3")


def test_item_context_carries_the_date_and_only_its_own_scene_lines():
    payload = _hand(_cand(1, "thread", _shown("A", MAP, D1)),
                    _cand(2, "thread", _shown("A", CHART, D2)),
                    now="the twelfth of May", recent=[D3],
                    lines=((D1, "Mara came ashore."), (D2, "The road was long."),
                           (D3, "Winifred opened the door.")))
    first, second = reconcile.build_items(payload)
    assert first.context.startswith(
        f"Campaign date: the twelfth of May\nRecent scenes:\n- {D1} — Mara came ashore.\n"
        f"- {D3} — Winifred opened the door.\n\n"
        "Candidate — whether a plot thread is finished\n"
        "A (plot thread): mara-s-map: Mara s map (open)\n")
    assert "The road was long." not in first.context
    assert f"- {D2} — The road was long.\n- {D3}" in second.context
    assert "Mara came ashore." not in second.context
    assert first.context == prompts.render(
        "continuity_reconcile/item.j2", now="the twelfth of May",
        chronicle=[payload["chronicle"][0], payload["chronicle"][2]],
        c={"label": reconcile.LABELS["thread"], "records": payload["candidates"][0]["records"],
           "signal_text": "word overlap 0.30"})
    # With no date and no lines, the heading opens the context.
    [bare] = reconcile.build_items(_hand(_cand(1, "thread", _shown("A", MAP))))
    assert bare.context.startswith("Candidate — whether a plot thread is finished\n")


def test_option_descriptions_do_not_repeat_the_context():
    for item in reconcile.build_items(_six()):
        for q in item.questions[1:]:
            for opt in q.options:
                if q.id in (reconcile.FROM_ID, reconcile.TO_ID):
                    assert opt.description == f"record {opt.id}"
                else:
                    assert opt.description == f"the scene listed above as {opt.id}"
                assert opt.description not in item.context


def test_the_union_of_item_scenes_is_todays_known_scenes(cid, monkeypatch):
    s1, s2, s3, s4 = (scenes.create_scene(cid, title) for title in (
        "Saltmarch docks", "Realm road", "Winifred's house", "Saltmarch quay"))
    for sid in (s1, s2, s3, s4):
        chronicle.absorb(cid, {"id": sid, "one_line": f"A line for {sid}."})
    _thread(cid, MAP, "Mara's map", "The map turned up.", s1)
    _thread(cid, CHART, "Winifred's chart", "The chart was copied.", s2)
    _commitment(cid, OATH, "Mara's oath", "Mara swore to find the map.", s3)
    pair = _record(cid, "possible_relation", [OATH, MAP], _pair_signals())
    lone = _record(cid, "possible_thread_closure", [CHART], {"reason": "stale"})
    _cache(cid, pair, lone)
    monkeypatch.setattr(reconcile, "RECONCILE_RECENT_SCENES", 1)
    payload = _payload(cid)
    shown = [reconcile.item_scenes(payload, c) for c in payload["candidates"]]
    assert set().union(*shown) == set(payload["known_scenes"]) == {s1, s2, s3, s4}
    for each in shown:
        assert each == [sid for sid in payload["known_scenes"] if sid in each]
    assert shown[[c["id"] for c in payload["candidates"]].index(lone[0])] == [s2, s4]


def test_build_items_drops_a_scene_whose_id_collides_once_normalised():
    twin, blank = "0001--saltmarch_docks", "   "
    payload = _hand(_cand(1, "thread", _shown("A", MAP, D1, twin, blank, D2)),
                    lines=((D1, "Mara came ashore."), (twin, "A twin of the docks."),
                           (D2, "The road was long.")))
    assert {D1, twin, blank} <= set(payload["known_scenes"])
    items = reconcile.build_items(payload)
    decisions.validate(items)
    # Every id of the colliding group is dropped, not all but one (M8).
    assert reconcile.item_scenes(payload, payload["candidates"][0]) == [D2]
    assert _evidence_options(items[0]) == [[D2]]
    assert "A twin of the docks." not in items[0].context
    assert "Mara came ashore." not in items[0].context
    assert "The road was long." in items[0].context
    # Collided twice over, a hand-edited payload still never builds a request
    # `validate` refuses.
    assert decisions.normalise(twin) == decisions.normalise(D1.upper())
    loud = _hand(_cand(1, "thread", _shown("A", MAP, D1.upper(), D1, twin)))
    decisions.validate(reconcile.build_items(loud))
    assert reconcile.item_scenes(loud, loud["candidates"][0]) == []


def test_a_citation_of_a_dropped_colliding_scene_is_a_scene_not_shown():
    """M8: on a hand-edited chronicle two scene ids collide once normalised,
    and the record block's beat markers show both. A citation of the one the
    item dropped normalises onto the one it kept, so neither is offered: the
    dropped id is never stored as the kept scene, it reads as a scene the item
    did not show, and a status word standing on it alone is `uncertain`. No
    request is refused and nothing fails."""
    twin = "0001--saltmarch_docks"
    assert decisions.normalise(twin) == decisions.normalise(D1)
    payload = _hand(_cand(1, "thread", _shown("A", MAP, D1, twin, D2)),
                    lines=((D1, "Mara came ashore."), (twin, "A twin of the docks."),
                           (D2, "The road was long.")))
    [cand] = payload["candidates"]
    decisions.validate(reconcile.build_items(payload))
    for cited in (twin, D1):
        got = _proposals(payload, {"decision": "close", "evidence_scene": cited})[cand["id"]]
        assert (got["decision"], got["status"]) == ("uncertain", "")
        assert D1 not in got["evidence_scenes"] and twin not in got["evidence_scenes"]
    # A scene that collides with nothing still stands as evidence.
    got = _proposals(payload, {"decision": "close", "evidence_scene": D2})[cand["id"]]
    assert (got["decision"], got["status"], got["evidence_scenes"]) == (
        "close", "closed", [D2])


def test_proposals_of_runs_every_answer_through_todays_rules():
    payload = _six()
    ids = [c["id"] for c in payload["candidates"]]
    got = _proposals(payload,
                     {"decision": "related"},                              # c1: no letters
                     {"decision": "duplicate", "from": "A", "to": "A"},    # c2: one record
                     {"decision": "pays_off", "from": "A", "to": "B"},     # c3: refused
                     {"decision": "before", "from": "B", "to": "A"},       # c4: temporal
                     {"decision": "close"},                                # c5: no scene
                     {"decision": "keep_open"})                            # c6
    assert set(got) == set(ids)
    assert (got[ids[0]]["decision"], got[ids[0]]["relation"], got[ids[0]]["from"],
            got[ids[0]]["to"]) == ("related", "related_to", MAP, CHART)
    assert got[ids[1]]["decision"] == "uncertain"
    assert got[ids[2]]["decision"] == "uncertain"
    assert (got[ids[3]]["decision"], got[ids[3]]["relation"], got[ids[3]]["from"],
            got[ids[3]]["to"]) == ("before", "before", OATH, EVENT)
    assert got[ids[4]]["decision"] == "uncertain" and got[ids[4]]["status"] == ""
    assert got[ids[5]]["decision"] == "keep_open"
    # A direction that holds stands, through the same code as today.
    good = _proposals(payload, {}, {}, {"decision": "pays_off", "from": "B", "to": "A"})
    assert (good[ids[2]]["decision"], good[ids[2]]["relation"], good[ids[2]]["from"],
            good[ids[2]]["to"]) == ("pays_off", "pays_off", MAP, OATH)
    assert set(good[ids[2]]) == {"decision", "from", "to", "relation", "status", "reason",
                                 "evidence_scenes"}


def test_proposals_of_keeps_every_cited_scene_in_order():
    payload = _hand(_cand(1, "thread", _shown("A", MAP, D1, D2, D3)))
    [cid_] = [c["id"] for c in payload["candidates"]]
    got = _proposals(payload, {"decision": "close", "evidence_scene": D2,
                               "evidence_scene_2": D1, "evidence_scene_3": D2})[cid_]
    assert (got["decision"], got["status"], got["evidence_scenes"]) == (
        "close", "closed", [D2, D1])
    got = _proposals(payload, {"decision": "close", "evidence_scene": D2,
                               "evidence_scene_3": D1})[cid_]
    assert got["evidence_scenes"] == [D2, D1]


def test_a_status_verdict_stands_without_a_rationale():
    payload = _hand(_cand(1, "thread", _shown("A", MAP, D1)),
                    _cand(2, "commitment", _shown("A", OATH, D1)))
    closure, owed = (c["id"] for c in payload["candidates"])
    got = _proposals(payload, {"decision": "close", "evidence_scene": D1},
                     {"decision": "fulfilled", "evidence_scene": D1}, rationales=("", ""))
    assert (got[closure]["decision"], got[closure]["status"], got[closure]["reason"],
            got[closure]["evidence_scenes"]) == ("close", "closed", "", [D1])
    for word in ("fulfilled", "broken", "expired"):
        got = _proposals(payload, {}, {"decision": word, "evidence_scene": D1})
        assert (got[owed]["decision"], got[owed]["status"], got[owed]["reason"]) == (
            word, word, "")
        bare = _proposals(payload, {}, {"decision": word})
        assert (bare[owed]["decision"], bare[owed]["status"]) == ("uncertain", "")
    assert _proposals(payload, {"decision": "close"})[closure]["decision"] == "uncertain"


def _result(decision: decisions.Answer) -> decisions.ItemResult:
    return decisions.ItemResult({reconcile.DECISION_ID: decision})


def test_proposals_of_leaves_out_every_candidate_the_reply_never_reached():
    payload = _hand(_cand(1, "thread", _shown("A", MAP)), _cand(2, "thread", _shown("A", CHART)))
    first, second = (c["id"] for c in payload["candidates"])
    answered = _result(decisions.Answer("keep_open"))
    for unread in (decisions.Answer(None, "unreadable", detail=decisions.NO_OBJECT),
                   decisions.Answer(None, "unreadable", detail=decisions.NO_ITEM),
                   decisions.Answer(None, "error"), decisions.Answer(None, "refused"),
                   decisions.Answer(None, "abstained")):
        got = reconcile.proposals_of(payload, [answered, _result(unread)])
        assert got is not None and set(got) == {first}, unread
        assert got[first]["decision"] == "keep_open"
    for garbled in (decisions.Answer(None, "unreadable"),
                    decisions.Answer(None, "unreadable", detail=decisions.NOT_AN_OPTION)):
        got = reconcile.proposals_of(payload, [answered, _result(garbled)])
        assert got is not None and got[second]["decision"] == "uncertain", garbled


def test_proposals_of_is_none_only_when_no_item_held_an_object():
    payload = _six()
    items = reconcile.build_items(payload)
    for text in ("I think so.", ""):
        assert reconcile.proposals_of(payload, decisions.parse(text, items,
                                                               explain=True)) is None
    for text in ("{}", '{"decisions": []}'):
        assert reconcile.proposals_of(payload, decisions.parse(text, items,
                                                               explain=True)) == {}
