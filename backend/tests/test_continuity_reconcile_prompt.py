"""The reconciliation sweep's one model call (capstone spec §11.2-§11.4, §23, §24).

`select` chooses what is asked -- capped, prioritized, and never a finding that
already has a proposal or that the reader already answered. `build_payload`
bounds what is sent: the records the chosen candidates name, their last beats
and the scene lines around them, never a transcript. `parse_output` trusts
nothing the reply says: an unknown key is dropped, a word outside the
candidate's vocabulary or a direction §5.3 does not allow is ``uncertain``, and
a closure without a known evidence scene and a reason is ``uncertain`` too.

Payloads are built through `select` / `build_payload` over a seeded campaign,
so what is parsed is what the prompt actually showed.
"""

from __future__ import annotations

import json

import pytest

from grimoire import prompts
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


def _persist_found(cid, sweep):
    """Persist 1's write, as far as `select` reads it: the cache's records plus
    what the sweep discovered, keeping only ``live`` ones. A stand-in until
    Task 6's `reconcile.persist_found` exists."""
    current = pending.Current.load(cid)
    stored = candidates.read(cid)
    records = {**stored["records"], **sweep.discovered}
    stored["records"] = {key: rec for key, rec in records.items()
                         if pending.verdict(current, rec) == "live"}
    candidates.write(cid, stored)


def _payload(cid, sweep=None):
    sweep = sweep or reconcile.Sweep("00000000000000000010-run", True)
    return reconcile.build_payload(cid, reconcile.select(cid, sweep))


def _by_id(payload, candidate_id):
    [cand] = [c for c in payload["candidates"] if c["id"] == candidate_id]
    return cand


def _reply(*decisions):
    return json.dumps({"decisions": list(decisions)})


def _user(payload):
    return reconcile.build_prompt(payload)[1]["content"]


def _closure(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up in Saltmarch.", s0)
    key, rec = _record(cid, "possible_thread_closure", [MAP],
                       {"reason": "stale", "days_since": 75})
    _cache(cid, (key, rec))
    return key


# ------------------------------------------------------------------ parsing


def test_parse_undecodable_is_none(cid, s0):
    payload = _payload(cid)
    assert reconcile.parse_output("I think so.", payload) is None
    assert reconcile.parse_output("", payload) is None


def test_parse_decodable_empty_is_empty(cid, s0):
    _closure(cid, s0)
    payload = _payload(cid)
    assert payload["candidates"]
    assert reconcile.parse_output("{}", payload) == {}
    assert reconcile.parse_output('{"decisions": 4}', payload) == {}
    assert reconcile.parse_output('{"decisions": [3, "x", null]}', payload) == {}


def test_cross_type_duplicate_is_uncertain(cid, s0):
    _thread(cid, MAP, "Mara's map", "The map turned up.", s0)
    _commitment(cid, OATH, "Mara's oath", "Mara swore to find the map.", s0)
    key, rec = _record(cid, "possible_relation", [OATH, MAP], _pair_signals())
    _cache(cid, (key, rec))
    payload = _payload(cid)
    assert _by_id(payload, key)["vocabulary"] == "cross"

    got = reconcile.parse_output(_reply(
        {"candidate": "c1", "decision": "duplicate", "from": "A", "to": "B",
         "reason": "Same business."}), payload)
    assert got[key]["decision"] == "uncertain"
    assert (got[key]["relation"], got[key]["from"], got[key]["to"]) == ("", "", "")


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
    keys = {c["id"]: c["key"] for c in payload["candidates"]}
    cross_c = _by_id(payload, cross_key)
    assert [r["ref"] for r in cross_c["records"]] == [OATH, MAP]
    assert [r["letter"] for r in cross_c["records"]] == ["A", "B"]
    assert _by_id(payload, owed_key)["vocabulary"] == "same_commitment"
    assert _by_id(payload, plot_key)["vocabulary"] == "same_thread"

    def decide(key, decision, frm, to):
        reply = _reply({"candidate": keys[key], "decision": decision, "from": frm, "to": to,
                        "reason": "Because."})
        return reconcile.parse_output(reply, payload)[key]

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
    sub = decide(plot_key, "subthread", "a", "b")
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

    got = reconcile.parse_output(_reply(
        {"candidate": cand["key"], "decision": "before", "from": "B", "to": "A",
         "reason": "The oath falls before the crowning."}), payload)[key]
    assert (got["decision"], got["relation"], got["from"], got["to"]) == (
        "before", "before", OATH, event)
    got = reconcile.parse_output(_reply(
        {"candidate": cand["key"], "decision": "unrelated"}), payload)[key]
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


def test_closure_without_known_evidence_is_uncertain(cid, s0):
    key = _closure(cid, s0)
    payload = _payload(cid)
    assert s0 in payload["known_scenes"]

    def decide(**over):
        item = {"candidate": "c1", "decision": "close", "reason": "The map was burned.",
                "evidence_scenes": [s0], **over}
        return reconcile.parse_output(_reply(item), payload)[key]

    unknown = decide(evidence_scenes=["999--nowhere"])
    assert unknown["decision"] == "uncertain"
    assert unknown["status"] == "" and unknown["evidence_scenes"] == []
    assert decide(reason="")["decision"] == "uncertain"
    assert decide(reason="   ")["decision"] == "uncertain"
    assert decide(evidence_scenes=s0)["decision"] == "uncertain"
    closed = decide(evidence_scenes=["999--nowhere", s0, s0])
    assert (closed["decision"], closed["status"], closed["evidence_scenes"]) == (
        "close", "closed", [s0])
    assert closed["reason"] == "The map was burned."
    assert decide(decision="keep_open", reason="")["decision"] == "keep_open"


def test_resolutions_need_evidence_too_and_carry_their_status(cid, s0):
    _commitment(cid, OATH, "Mara's oath", "Mara swore it.", s0, due="2026-05-05")
    key, rec = _record(cid, "possible_commitment_resolution", [OATH],
                       {"reason": "overdue", "in_days": -5, "via": "deadline"})
    _cache(cid, (key, rec))
    payload = _payload(cid)
    for word in ("fulfilled", "broken", "expired"):
        got = reconcile.parse_output(_reply(
            {"candidate": "c1", "decision": word, "reason": "So it went.",
             "evidence_scenes": [s0]}), payload)[key]
        assert (got["decision"], got["status"]) == (word, word)
        bare = reconcile.parse_output(_reply(
            {"candidate": "c1", "decision": word, "reason": "So it went."}), payload)[key]
        assert bare["decision"] == "uncertain"
    closed = reconcile.parse_output(_reply(
        {"candidate": "c1", "decision": "close", "reason": "x", "evidence_scenes": [s0]}),
        payload)[key]
    assert closed["decision"] == "uncertain"


def test_unknown_candidate_keys_and_enums_are_dropped_or_uncertain(cid, s0):
    key = _closure(cid, s0)
    payload = _payload(cid)
    got = reconcile.parse_output(_reply(
        {"candidate": "c9", "decision": "close", "reason": "x", "evidence_scenes": [s0]},
        {"candidate": 1, "decision": "close"},
        "not an object",
        {"candidate": "  Candidate C1 ", "decision": "Merge it", "reason": "r" * 400},
        {"candidate": "c1", "decision": "keep_open", "reason": "second answer"}), payload)
    assert set(got) == {key}
    first = got[key]
    assert first["decision"] == "uncertain"
    assert first["reason"] == "r" * reconcile.RECONCILE_REASON_CHARS
    assert set(first) == {"decision", "from", "to", "relation", "status", "reason",
                          "evidence_scenes"}
    assert reconcile.parse_output(_reply({"candidate": "c1", "decision": 3}),
                                  payload)[key]["decision"] == "uncertain"
    assert reconcile.parse_output(_reply({"candidate": "candidate:c1", "decision": "KEEP_OPEN"}),
                                  payload)[key]["decision"] == "keep_open"


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
        _persist_found(cid, sweep)
        selected = reconcile.select(cid, sweep)
        assert not hidden & {s["id"] for s in selected}
        assert visible in {s["id"] for s in selected}
        payload = reconcile.build_payload(cid, selected)
        text = "\n".join(m["content"] for m in reconcile.build_prompt(payload))
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
    got = reconcile.parse_output(_reply(
        {"candidate": "c1", "decision": "close", "reason": "Answered.",
         "evidence_scenes": [s2]}), payload)
    assert got[payload["candidates"][0]["id"]]["decision"] == "uncertain"


def test_beats_are_capped_and_newest_last(cid):
    sids = [scenes.create_scene(cid, f"Saltmarch {n}") for n in range(5)]
    for n, sid in enumerate(sids):
        _thread(cid, MAP, "Mara's map", f"Beat {n}.", sid)
    _cache(cid, _record(cid, "possible_thread_closure", [MAP], {"reason": "stale"}))
    [record] = _payload(cid)["candidates"][0]["records"]
    assert len(record["beats"]) == reconcile.RECONCILE_BEATS == 3
    assert record["beats"] == [{"scene": sids[n], "text": f"Beat {n}."} for n in (2, 3, 4)]


def test_no_transcript_text_is_sent(cid, s0):
    scenes.append_message(cid, s0, "user", "Seraphine whispered the lighthouse password.")
    scenes.append_message(cid, s0, "assistant", "Mara nodded and pocketed the brass key.")
    chronicle.absorb(cid, {"id": s0, "one_line": "Mara came ashore."})
    _closure(cid, s0)
    text = "\n".join(m["content"] for m in reconcile.build_prompt(_payload(cid)))
    assert "Mara came ashore." in text
    assert "lighthouse password" not in text
    assert "brass key" not in text


def test_build_prompt_renders_with_no_optional_fields():
    payload = {"now": "", "chronicle": [], "known_scenes": [], "candidates": [
        {"key": "c1", "id": "possible_thread_closure-0123456789abcdef",
         "vocabulary": "thread", "signal_text": "",
         "records": [{"letter": "A", "ref": MAP, "line": "mara-s-map: Mara's map (open)",
                      "beats": [], "pressure": "", "links": [], "actors": []}]}]}
    system, user = reconcile.build_prompt(payload)
    assert system == {"role": "system",
                      "content": prompts.render("continuity_reconcile/system.j2")}
    assert user["role"] == "user"
    assert user["content"] == ('Candidate c1 — whether a plot thread is finished '
                               '(answer with: "close", "keep_open", "uncertain")\n'
                               "A: mara-s-map: Mara's map (open)")


def test_the_user_prompt_shows_every_part(cid, s0):
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
                           f"- {s0} — Mara came ashore.\n\nCandidate c1 — two plot threads "
                           '(answer with: "duplicate", "continuation", "subthread", "related", '
                           '"distinct", "uncertain")\n')
    assert "A: mara-s-map: Mara's map (open)\n" in user
    assert f"  [{s0}] The map turned up in Saltmarch.\n" in user
    assert "  links: Mara's map pays_off Mara's oath\n" in user
    assert user.endswith("signals: same title; word overlap 0.42; meaning 0.81; "
                         "shared characters: Mara; shared scenes: 1")


def test_the_system_prompt_is_static_and_names_every_word():
    text = prompts.render("continuity_reconcile/system.j2")
    assert text.startswith(
        "You are reviewing a campaign's story ledger for records that may overlap or be finished")
    for opener in ("You are absorbing a completed role-play scene",
                   "You are auditing a completed role-play scene",
                   "You are checking whether newly proposed story records"):
        assert opener not in text
    for words in reconcile.DECISIONS.values():
        for word in words:
            assert f'"{word}"' in text
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
