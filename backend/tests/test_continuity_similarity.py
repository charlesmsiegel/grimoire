"""`store/continuity/similarity.py` -- identity texts and deterministic signals.

The identity text is what a record is compared by (capstone spec §9.1), and the
lexical and structural signals (§9.2) decide which stored records are worth a
resolver's attention. Scores rank candidates; nothing here writes (§3.2). Every
candidate / non-candidate test first asserts the signal that is supposed to
decide it, so a later floor change fails at that assertion rather than
silently somewhere else.
"""

import importlib
import json
import math

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire.main import create_app
from grimoire.store.campaigns import paths as campaigns_paths
from grimoire.store.continuity import effective, review, similarity

S1, S2 = "001--saltmarch", "002--realm"
MARA = "characters:mara"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def cid(client):
    wid = client.post("/api/worlds", json={"name": "Realm"}).json()["id"]
    return client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]


def _root(cid):
    return campaigns_paths.campaign_root(cid)


def _thread(ref, title, *beats, actors=(), scenes=(), anchors=()):
    record = {"title": title, "beats": [{"text": b} for b in beats]}
    return similarity.subject("thread", ref, record, actors=actors, scenes=scenes,
                              anchors=anchors)


def _commitment(ref, title, *beats, kind="", due="", actors=()):
    record = {"title": title, "beats": [{"text": b} for b in beats], "kind": kind, "due": due}
    return similarity.subject("commitment", ref, record, actors=actors)


# ----------------------------------------------------------- identity texts


def test_thread_identity_text_order_and_no_ids(cid):
    for beat in ("A", "B", "C", "C"):
        store.plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open", beat, S1)
    rec = effective.records(cid, "thread")["thread:find-the-ledger"]
    text = similarity.thread_identity_text(rec)
    assert text == "plot thread\nFind the ledger\nC\nB\nA"
    assert "find-the-ledger" not in text


def test_commitment_identity_text_blank_kind_is_promise_and_due_present():
    rec = {"title": "The midnight deadline", "kind": "", "due": "midnight",
           "beats": [{"text": "Mara swore to pay."}, {"text": "Mara asked for a day."}]}
    lines = similarity.commitment_identity_text(rec).split("\n")
    assert lines[0] == "promise commitment"
    assert lines[1] == "The midnight deadline"
    assert lines[2] == "Mara asked for a day."
    assert lines[3] == "due midnight"
    assert lines[4:] == ["Mara swore to pay."]


def test_identity_text_is_clipped_to_the_byte_bound():
    rec = {"title": "Find the ledger", "beats": [{"text": "x" * 5000}]}
    text = similarity.thread_identity_text(rec)
    assert len(text.encode("utf-8")) <= similarity.CONTINUITY_IDENTITY_BYTES
    assert text.startswith("plot thread\nFind the ledger\n")


# ------------------------------------------------------------- normalizing


def test_titles_equal_after_nfkc_and_casefold():
    assert similarity.titles_equal("\uff2d\uff21\uff32\uff21'S DEBT", "mara's debt")   # fullwidth MARA
    assert not similarity.titles_equal("", "")


def test_cjk_reword_is_a_trigram_candidate_and_untitled_slugs_are_not_equal():
    assert not similarity.slug_equal("海図", "灯台")
    stored = _thread("thread:untitled", "港の帳簿を探す",
                     "ウィニフレッドは港の帳簿の存在を知った。")
    proposed = _thread("proposed:x", "港の帳簿を取り戻す",
                       "ウィニフレッドは港の帳簿を探しに行った。")
    sig = similarity.lexical(proposed, stored)
    assert not sig["slug_equal"]
    assert sig["chars"] >= similarity.CHAR_FLOOR
    assert sig["tokens"] < similarity.WEAK_TOKEN
    assert similarity.plausible(sig)


def test_stopwords_never_count_as_shared_tokens():
    got = similarity.tokens("The map of the harbour")
    assert "the" not in got and "of" not in got
    assert got == frozenset({"map", "harbour"})
    assert "s" not in similarity.tokens("Mara's boat")


def test_combining_marks_stay_inside_their_word():
    # A Devanagari vowel sign or virama is a combining mark, not alphanumeric:
    # split on it, every word breaks into bare consonants that any two texts in
    # the script share.
    assert similarity.normalize("किताब खोई") == "किताब खोई"
    stored = _thread("thread:a", "मारा की नाव", "मारा ने घाट पर अपनी नाव की मरम्मत की।")
    unrelated = _thread("thread:b", "विनिफ्रेड का दीया", "विनिफ्रेड ने खिड़की में दीया जलाया।")
    sig = similarity.lexical(stored, unrelated)
    assert sig["tokens"] < similarity.TOKEN_FLOOR and sig["chars"] < similarity.CHAR_FLOOR
    assert not sig["title_equal"] and not sig["slug_equal"]
    assert not similarity.plausible(sig)

    reworded = _thread("thread:c", "मारा की नाव खो गई", "मारा की नाव घाट से गायब हो गई।")
    sig = similarity.lexical(stored, reworded)
    assert sig["tokens"] >= similarity.TOKEN_FLOOR
    assert similarity.admitted_by(sig) == "lexical"


def test_a_slug_counts_only_when_it_spells_the_whole_title():
    # `paths.slugify` drops every non-ASCII character, so different titles that
    # share only an ASCII remnant slug alike.
    assert not similarity.slug_equal("Mara的海図", "Mara的灯台")
    assert not similarity.slug_equal("海図 1", "灯台 1")
    assert not similarity.slug_equal("Ölmühle", "Älmühle")
    assert similarity.slug_equal("Mara's map", "Mara-s Map!")
    sig = similarity.lexical(_thread("thread:a", "Mara的海図", "港で古い海図が盗まれた。"),
                             _thread("thread:b", "Mara的灯台", "灯台の明かりが消えた夜。"))
    assert not sig["title_equal"] and not sig["slug_equal"]
    assert sig["tokens"] < similarity.TOKEN_FLOOR and sig["chars"] < similarity.CHAR_FLOOR
    assert not similarity.plausible(sig)


def test_the_due_prefix_is_not_a_shared_word():
    # Every dated commitment's identity text carries the line "due <phrase>";
    # the prefix is layout, like the type label, and says nothing about which
    # obligation a record is.
    debt = _commitment("commitment:a", "Mara's debt", "Mara owes the guild.",
                       due="dawn", actors=[MARA])
    oath = _commitment("commitment:b", "Winifred's oath", "Winifred swore an oath.",
                       due="dusk", actors=[MARA])
    sig = similarity.lexical(debt, oath)
    assert sig["actors"] == [MARA]
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert similarity.admitted_by(sig) is None
    assert "due" not in similarity.tokens(similarity.body(debt.text))
    # The phrase itself still counts, and "due" written in a title still does.
    same = _commitment("commitment:c", "Mara's debt", due="dawn")
    assert "dawn" in similarity.tokens(similarity.body(same.text))
    titled = _commitment("commitment:d", "The debt comes due", due="dawn")
    assert "due" in similarity.tokens(similarity.body(titled.text))


# ------------------------------------------------------------ plausibility


def _boat(title="Mara's boat"):
    return _thread("thread:maras-boat", title,
                   "Mara patched the hull of her boat at the pier.", actors=[MARA])


def _lantern():
    return _thread("thread:winifreds-lantern", "Winifred's lantern",
                   "Winifred lit the lantern in the window.", actors=[MARA])


def test_unrelated_records_are_not_plausible_even_with_shared_actors():
    sig = similarity.lexical(_boat(), _lantern())
    assert sig["actors"] == [MARA]
    assert sig["tokens"] < similarity.WEAK_TOKEN
    assert sig["chars"] < similarity.WEAK_CHAR
    assert not similarity.plausible(sig)
    assert similarity.admitted_by(sig) is None


def test_structural_signal_needs_a_weak_lexical_partner():
    sig = similarity.lexical(_boat("Mara's lantern"), _lantern())
    assert sig["actors"] == [MARA]
    assert sig["tokens"] >= similarity.WEAK_TOKEN
    assert sig["tokens"] < similarity.TOKEN_FLOOR and sig["chars"] < similarity.CHAR_FLOOR
    assert not sig["title_equal"] and not sig["slug_equal"]
    assert similarity.admitted_by(sig) == "structural"
    assert similarity.plausible(sig)


def test_admitted_by_names_the_clause():
    equal = similarity.lexical(_thread("thread:a", "Mara's map", "Stolen."),
                               _thread("thread:b", "Mara's map", "Traced at the docks."))
    assert equal["title_equal"]
    assert similarity.admitted_by(equal) == "lexical"

    boat, lantern = _boat(), _lantern()
    near = {boat.text: [1.0, 0.0], lantern.text: [0.4, math.sqrt(1 - 0.16)]}
    sig = similarity.with_cosine(similarity.lexical(boat, lantern), boat, lantern, near)
    assert sig["cosine"] == 0.4
    assert sig["actors"] == [MARA]
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert similarity.admitted_by(sig) == "semantic"

    alone_a = _thread("thread:a", "Mara's boat", "Mara patched the hull of her boat.")
    alone_b = _thread("thread:b", "Winifred's lantern", "Winifred lit the lantern.")
    close = {alone_a.text: [1.0, 0.0], alone_b.text: [0.9, math.sqrt(1 - 0.81)]}
    sig = similarity.with_cosine(similarity.lexical(alone_a, alone_b), alone_a, alone_b, close)
    assert sig["cosine"] == 0.9
    assert sig["actors"] == [] and sig["scenes"] == [] and sig["anchors"] == []
    assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    assert similarity.admitted_by(sig) == "semantic"


def test_with_cosine_leaves_none_when_a_vector_is_missing_or_off_width():
    a = _thread("thread:a", "Mara's map", "Stolen.")
    b = _thread("thread:b", "Winifred's chart", "Lost.")
    sig = similarity.lexical(a, b)
    assert similarity.with_cosine(sig, a, b, {a.text: [1.0, 0.0]})["cosine"] is None
    off = {a.text: [1.0, 0.0], b.text: [1.0, 0.0, 0.0]}
    assert similarity.with_cosine(sig, a, b, off)["cosine"] is None


LEDGER = ("Find the ledger", "Winifred learned the harbour ledger exists.")


def test_top_k_keeps_the_three_best_plausible_and_never_pads():
    subject = _thread("proposed:x", *LEDGER)
    plausible = [
        _thread("thread:e", "Find the ledger", "Mara burned the ledger."),
        _thread("thread:d", "Find the harbour ledger", "Winifred learned the ledger exists."),
        _thread("thread:c", "Recover the harbour ledger",
                "Winifred went looking for the harbour ledger."),
        _thread("thread:b", "The harbour ledger", "Winifred heard of a ledger."),
        _thread("thread:a", "The missing ledger", "Winifred asked about the harbour ledger."),
    ]
    sigs = {c.ref: similarity.lexical(subject, c) for c in plausible}
    assert all(similarity.plausible(s) for s in sigs.values())
    expected = sorted(sigs, key=lambda ref: similarity.rank_key(sigs[ref], ref))[:3]
    got = similarity.nearest(subject, plausible)
    assert len(got) == 3
    assert [s.ref for s, _ in got] == expected
    assert expected == ["thread:e", "thread:d", "thread:b"]   # the title-equal one leads
    assert all(sig["via"] == "lexical" for _, sig in got)

    implausible = [
        _thread("thread:f", "The Saltmarch tithe", "The guild demanded Mara pay the Saltmarch tithe."),
        _thread("thread:g", "Mara's boat", "Mara patched the hull of her boat at the pier."),
        _thread("thread:h", "The coronation", "Seraphine was crowned in the great hall."),
        _thread("thread:i", "Seraphine's oath", "Seraphine knelt before the throne."),
    ]
    for c in implausible:
        sig = similarity.lexical(subject, c)
        assert sig["tokens"] < similarity.WEAK_TOKEN and sig["chars"] < similarity.WEAK_CHAR
    got = similarity.nearest(subject, [plausible[0], *implausible])
    assert [s.ref for s, _ in got] == ["thread:e"]


def test_cross_type_never_enters_nearest():
    subject = _thread("proposed:x", *LEDGER)
    same_title = _commitment("commitment:find-the-ledger", "Find the ledger",
                             "Winifred learned the harbour ledger exists.")
    thread = _thread("thread:find-the-ledger", *LEDGER)
    got = similarity.nearest(subject, [same_title, thread])
    assert [s.ref for s, _ in got] == ["thread:find-the-ledger"]


def test_nearest_never_returns_the_subject_itself():
    subject = _thread("thread:find-the-ledger", *LEDGER)
    assert similarity.nearest(subject, [subject]) == []


# ------------------------------------------------------------------- pools


def _chronicle(cid, casts: dict):
    (_root(cid) / "chronicle.json").write_text(json.dumps(
        {sid: {"id": sid, "one_line": "", "cast": cast} for sid, cast in casts.items()}),
        encoding="utf-8")


def test_pool_includes_closed_and_canonicalizes_aliases(cid):
    _chronicle(cid, {S1: ["characters/mara"]})
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "the-debt", "The debt", "closed", "Paid.", S2)
    store.plot.set_movement(cid, "a", "Winifred's chart", "open", "Lost.", S2)
    store.plot.set_movement(cid, "b", "Winifred's chart found", "open", "Found.", S2)
    review.create_alias(cid, "thread:a", "thread:b")
    pool = similarity.pool(cid, "thread")
    refs = [s.ref for s in pool]
    assert refs == sorted(refs)
    assert "thread:b" in refs and "thread:a" not in refs
    by_ref = {s.ref: s for s in pool}
    assert by_ref["thread:the-debt"].live is False
    assert by_ref["thread:maras-map"].live is True
    assert by_ref["thread:maras-map"].actors == frozenset({MARA})
    assert by_ref["thread:maras-map"].scenes == frozenset({S1})
    assert by_ref["thread:maras-map"].kind == "thread"
    assert by_ref["thread:maras-map"].text.startswith("plot thread\nMara's map\n")
    # the merged group's beats are the canonical's identity
    assert "Lost." in by_ref["thread:b"].text and "Found." in by_ref["thread:b"].text


def test_pool_of_commitments_uses_the_commitment_text(cid):
    store.commitments.set_movement(cid, "the-midnight-deadline", "The midnight deadline",
                                   "threat", "open", "midnight", "Mara owes the guild.", S1)
    [only] = similarity.pool(cid, "commitment")
    assert only.ref == "commitment:the-midnight-deadline"
    assert only.text == "threat commitment\nThe midnight deadline\nMara owes the guild.\ndue midnight"


def test_pool_of_an_unreadable_ledger_is_empty(cid):
    store.plot.set_movement(cid, "maras-map", "Mara's map", "open", "Stolen.", S1)
    (_root(cid) / "plot.json").write_text("{ no", encoding="utf-8")
    assert similarity.pool(cid, "thread") == []


def test_anchors_come_from_reviewed_event_links(cid):
    store.plot.set_movement(cid, "x", "Mara's map", "open", "Stolen.", S1)
    store.plot.set_movement(cid, "y", "Winifred's chart", "open", "Lost.", S1)
    eid = store.events.create(cid, "The coronation", "2026-05-13")
    review.create_link(cid, "thread:x", f"event:{eid}", "on")
    by_ref = {s.ref: s for s in similarity.pool(cid, "thread")}
    assert by_ref["thread:x"].anchors == frozenset({f"event:{eid}"})
    assert by_ref["thread:y"].anchors == frozenset()
