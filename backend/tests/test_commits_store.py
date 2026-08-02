"""The commit-token ledger behind PUT /chronicle's replay guard (#235), its
commit journal and its per-scene epoch (#271)."""

import json

from grimoire.store import campaigns, commits, scene_refs, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def _age(cid, token, when):
    """Backdate a ledger entry -- cheaper and steadier than waiting."""
    p = campaigns.campaign_root(cid) / "commits.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["tokens"][token]["at"] = when
    p.write_text(json.dumps(data), encoding="utf-8")


def test_an_unseen_token_has_no_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert commits.lookup(cid, "tok") is None


def test_a_recorded_token_returns_its_result(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "tok", {"applied": ["a"], "failures": []})
    entry = commits.lookup(cid, "tok")
    assert entry["done"] is True and entry["result"] == {"applied": ["a"], "failures": []}


def test_a_reserved_token_is_seen_but_not_done(monkeypatch, tmp_path):
    """The window the reserve exists for: effects have begun, the result is not
    known, and a replay must not run them again."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp")
    entry = commits.lookup(cid, "tok")
    assert entry is not None and entry["done"] is False


def test_recording_completes_a_reservation(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp")
    commits.record(cid, "tok", {"applied": []})
    assert commits.lookup(cid, "tok")["done"] is True


def test_an_empty_token_is_never_recorded_or_matched(monkeypatch, tmp_path):
    """A client that sends no token opts out of the guard -- it must not collide
    with every other tokenless save."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "", {"applied": []})
    commits.reserve(cid, "", "fp")
    assert commits.lookup(cid, "") is None


def test_a_token_survives_many_later_saves(monkeypatch, tmp_path):
    """Eviction by count would drop a token whose review is still open on
    someone's screen, and its retry would then replay every append."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "mine", {"applied": ["a"]})
    for i in range(50):
        commits.record(cid, f"other{i}", {"applied": []})
    assert commits.lookup(cid, "mine")["result"] == {"applied": ["a"]}


def test_completed_entries_expire_by_age(monkeypatch, tmp_path):
    """Bounded some other way, or commits.json becomes a permanent log."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "old", {"applied": []})
    _age(cid, "old", "2000-01-01T00:00:00Z")
    commits.record(cid, "fresh", {"applied": []})     # any write prunes
    assert commits.lookup(cid, "old") is None
    assert commits.lookup(cid, "fresh") is not None


def test_an_unfinished_reservation_never_expires(monkeypatch, tmp_path):
    """The dangerous entry: dropping it lets a retry replay a commit that may
    have partly landed. It has no result to age out into, so it stays."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "wedged", "fp")
    _age(cid, "wedged", "2000-01-01T00:00:00Z")
    commits.record(cid, "fresh", {"applied": []})
    assert commits.lookup(cid, "wedged") is not None
    assert commits.lookup(cid, "wedged")["done"] is False


def test_the_fingerprint_is_stable_and_order_insensitive(monkeypatch, tmp_path):
    a = commits.fingerprint({"one_line": "o", "edits": [{"id": "x", "after": "y"}]})
    b = commits.fingerprint({"edits": [{"after": "y", "id": "x"}], "one_line": "o"})
    assert a == b
    assert a != commits.fingerprint({"one_line": "CHANGED", "edits": []})


def test_a_recorded_token_keeps_its_fingerprint(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.record(cid, "tok", {"applied": []}, "fp1")
    assert commits.lookup(cid, "tok")["fingerprint"] == "fp1"


def test_a_garbled_ledger_reads_as_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commits.json").write_text("{ not json", encoding="utf-8")
    assert commits.lookup(cid, "tok") is None


def test_a_pre_journal_ledger_still_answers_its_tokens(monkeypatch, tmp_path):
    """#271 nested the token map under "tokens" to make room for the per-scene
    epochs. Reading the old shape as garbled would forget every open review's
    token, and forgetting one is what lets its retry replay the appends."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commits.json").write_text(json.dumps({
        "spent": {"done": True, "result": {"applied": ["a"]}, "fingerprint": "fp",
                  "sid": "001--landing", "at": "2026-07-29T00:00:00Z"},
        "wedged": {"done": False, "result": None, "fingerprint": "fp",
                   "sid": "001--landing", "at": "2026-07-29T00:00:00Z"},
    }), encoding="utf-8")
    assert commits.lookup(cid, "spent")["result"] == {"applied": ["a"]}
    # An entry from before the journal describes a commit that began without
    # one. Its empty progress is a stand-in, NOT an account of a commit that
    # did nothing -- reading it as the latter would resume the whole thing as
    # fresh work and duplicate every append it had already made.
    wedged = commits.lookup(cid, "wedged")
    assert wedged["done"] is False and wedged["journalled"] is False


# ---- the commit journal (#271) ----
def test_a_journal_outlives_the_attempt_that_wrote_it(monkeypatch, tmp_path):
    """The whole point: a commit that dies partway leaves behind what it did."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {"timeline": "pending"})
    commits.checkpoint(cid, "tok", {"timeline": "done",
                                    "edits": {"0": {"state": "applied", "id": "lore:x"}}})
    progress = commits.lookup(cid, "tok")["progress"]
    assert progress["timeline"] == "done"
    assert progress["edits"]["0"] == {"state": "applied", "id": "lore:x"}


def test_a_reservation_carries_a_resumed_journal_forward(monkeypatch, tmp_path):
    """The retry re-reserves; if that dropped the journal, the resumed commit
    would start a second account of itself and repeat every step."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {"timeline": "done"})
    prior = commits.lookup(cid, "tok")["progress"]
    commits.reserve(cid, "tok", "fp", "001--landing", prior)
    assert commits.lookup(cid, "tok")["progress"] == {"timeline": "done"}


def test_completing_a_commit_drops_its_journal(monkeypatch, tmp_path):
    """The result supersedes it, and it is the bulky half of the entry -- the
    write-back deltas of every applied edit ride in it."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {"timeline": "done"})
    commits.record(cid, "tok", {"applied": []}, "fp", "001--landing")
    assert commits.lookup(cid, "tok")["progress"] == {}


def test_only_a_commit_in_flight_can_be_journalled(monkeypatch, tmp_path):
    """A journal describes an unfinished attempt. Writing one onto an unseen or
    a completed token would invent or reopen an outcome that is settled."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.checkpoint(cid, "never-reserved", {"timeline": "done"})
    assert commits.lookup(cid, "never-reserved") is None
    commits.record(cid, "spent", {"applied": []}, "fp", "001--landing")
    commits.checkpoint(cid, "spent", {"timeline": "done"})
    assert commits.lookup(cid, "spent")["progress"] == {}


# ---- the per-scene commit epoch (#271) ----
def test_a_minted_token_carries_the_epoch_it_was_given(monkeypatch, tmp_path):
    """Carried in the token, not in an entry: `POST /absorb` is a proposal and
    leaves the campaign byte-identical, so the mint cannot write one.

    And it takes the epoch rather than reading it -- absorb spends its LLM calls
    between reading the snapshot and returning the token, so the caller has to
    hold the value from before that window."""
    cid = _campaign(monkeypatch, tmp_path)
    token = commits.mint(commits.scene_epoch(cid, "001--landing"))
    assert commits.token_epoch(token) == 0
    assert commits.lookup(cid, token) is None      # nothing was recorded
    assert not (campaigns.campaign_root(cid) / "commits.json").exists()


def test_only_a_token_this_module_minted_carries_an_epoch(monkeypatch, tmp_path):
    """A caller-minted key may be anything. Reading a leading number out of one
    would hand it an epoch its owner never meant and lose its FIRST save to a
    spurious 409, so the whole shape has to match."""
    assert commits.token_epoch(commits.mint(0)) == 0
    for foreign in ("deadbeefdeadbeef", "", "123", "1-custom", "12345678901234567890",
                    "1-" + "f" * 31, "1-" + "F" * 32, "01-x"):
        assert commits.token_epoch(foreign) is None, foreign


def test_claiming_a_token_advances_only_its_own_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {})
    assert commits.scene_epoch(cid, "001--landing") == 1
    assert commits.scene_epoch(cid, "002--the-road") == 0


def test_the_epoch_advances_at_the_claim_not_at_completion(monkeypatch, tmp_path):
    """A commit that claims its token and then dies never records. With the
    epoch advanced at completion, a rival review minted at the same epoch would
    still pass its check and save on top of the half-applied one."""
    cid = _campaign(monkeypatch, tmp_path)
    rival = commits.mint(commits.scene_epoch(cid, "001--landing"))
    commits.reserve(cid, "wedged", "fp", "001--landing", {"timeline": "pending"})
    assert commits.token_epoch(rival) != commits.scene_epoch(cid, "001--landing")


def test_resuming_a_reservation_does_not_advance_the_epoch_again(monkeypatch, tmp_path):
    """A resumption is the same commit, not a second one."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {"timeline": "pending"})
    commits.reserve(cid, "tok", "fp", "001--landing", {"timeline": "done"})
    commits.record(cid, "tok", {"applied": []}, "fp", "001--landing")
    assert commits.scene_epoch(cid, "001--landing") == 1


def test_a_review_prepared_earlier_carries_the_earlier_epoch(monkeypatch, tmp_path):
    """What lets a save tell "nothing has committed since I was prepared" from
    "another review of this scene got there first" -- the two carry different
    tokens, so the key alone cannot order them."""
    cid = _campaign(monkeypatch, tmp_path)
    early = commits.mint(commits.scene_epoch(cid, "001--landing"))
    commits.reserve(cid, "other", "fp", "001--landing", {})
    commits.record(cid, "other", {"applied": []}, "fp", "001--landing")
    late = commits.mint(commits.scene_epoch(cid, "001--landing"))
    assert commits.token_epoch(early) == 0
    assert commits.token_epoch(late) == 1


def test_a_tokenless_save_still_retires_the_reviews_before_it(monkeypatch, tmp_path):
    """No token means no replay guard, but the commit still happened."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "", "", "001--landing", {})
    commits.record(cid, "", {"applied": []}, "", "001--landing")
    assert commits.scene_epoch(cid, "001--landing") == 1
    assert commits.lookup(cid, "") is None


def test_renaming_a_scene_carries_its_epoch_and_its_tokens(monkeypatch, tmp_path):
    """A scene id is its filename stem, so a title rename moves it. Left behind,
    the epoch map reads the new id as 0 and refuses an open review as
    superseded, and the entries keep the old id and refuse a retry as a scene
    mismatch."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {})
    commits.record(cid, "tok", {"applied": []}, "fp", "001--landing")
    scene_refs.repoint(cid, {"001--landing": "001--the-landing"})
    assert commits.scene_epoch(cid, "001--the-landing") == 1
    assert commits.scene_epoch(cid, "001--landing") == 0
    assert commits.lookup(cid, "tok")["sid"] == "001--the-landing"


def test_repointing_never_lowers_an_epoch(monkeypatch, tmp_path):
    """A width re-pad can rename INTO an id that already carries one."""
    cid = _campaign(monkeypatch, tmp_path)
    for _ in range(3):
        commits.reserve(cid, f"a{_}", "fp", "01--landing", {})
    commits.reserve(cid, "b", "fp", "001--landing", {})
    scene_refs.repoint(cid, {"01--landing": "001--landing"})
    assert commits.scene_epoch(cid, "001--landing") == 3


def test_a_legacy_token_named_like_the_new_schema_is_still_found(monkeypatch, tmp_path):
    """A token is a caller-chosen string, so a pre-#271 ledger can hold one keyed
    literally `tokens`. Reading that entry as the nested map would hide every
    sibling token in the file -- and a hidden token's retry replays its appends.
    The schema marker, not the presence of a key, tells the two shapes apart."""
    cid = _campaign(monkeypatch, tmp_path)
    (campaigns.campaign_root(cid) / "commits.json").write_text(json.dumps({
        "tokens": {"done": True, "result": {"applied": ["a"]}, "fingerprint": "fp",
                   "sid": "001--landing", "at": "2026-07-29T00:00:00Z"},
        "scenes": {"done": False, "result": None, "fingerprint": "fp",
                   "sid": "001--landing", "at": "2026-07-29T00:00:00Z"},
        "ordinary": {"done": True, "result": {"applied": ["b"]}, "fingerprint": "fp",
                     "sid": "001--landing", "at": "2026-07-29T00:00:00Z"},
    }), encoding="utf-8")
    assert commits.lookup(cid, "tokens")["result"] == {"applied": ["a"]}
    assert commits.lookup(cid, "scenes")["done"] is False
    assert commits.lookup(cid, "ordinary")["result"] == {"applied": ["b"]}


def test_every_write_stamps_the_schema(monkeypatch, tmp_path):
    """Missing on any one of the three writers, the next read takes the
    flat-ledger branch and loses every token in the file."""
    cid = _campaign(monkeypatch, tmp_path)
    p = campaigns.campaign_root(cid) / "commits.json"
    commits.reserve(cid, "tok", "fp", "001--landing", {"timeline": "pending"})
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == commits.SCHEMA
    commits.checkpoint(cid, "tok", {"timeline": "done"})
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == commits.SCHEMA
    commits.record(cid, "tok", {"applied": []}, "fp", "001--landing")
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == commits.SCHEMA
    scene_refs.repoint(cid, {"001--landing": "001--the-landing"})
    assert json.loads(p.read_text(encoding="utf-8"))["v"] == commits.SCHEMA
    assert commits.lookup(cid, "tok")["sid"] == "001--the-landing"


def test_retiring_a_scene_advances_its_epoch_without_dropping_entries(monkeypatch, tmp_path):
    """Deletion recycles the id, so leftover state has to stop matching. Dropping
    the entries would be worse than useless: an unfinished reservation is the one
    record that must never go, and a dropped token reads as unseen -- a FRESH
    commit into the replacement scene, which is what this prevents."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "wedged", "fp", "001--landing", {"timeline": "pending"})
    commits.retire_scene(cid, "001--landing")
    assert commits.scene_epoch(cid, "001--landing") == 2
    assert commits.lookup(cid, "wedged")["progress"] == {"timeline": "pending"}


def test_a_reservation_records_the_epoch_its_claim_produced(monkeypatch, tmp_path):
    """What a resume compares against. Kept on the ENTRY rather than derived from
    the token, so a caller-minted key -- which carries no epoch and is a
    supported thing to send -- is fenced exactly like a server-minted one."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "caller-chose-this", "fp", "001--landing", {"timeline": "pending"})
    assert commits.lookup(cid, "caller-chose-this")["claimed"] == 1
    assert commits.token_epoch("caller-chose-this") is None   # nothing to derive
    # a resumption is the same commit: it neither re-advances nor forgets
    commits.reserve(cid, "caller-chose-this", "fp", "001--landing", {"timeline": "done"})
    assert commits.lookup(cid, "caller-chose-this")["claimed"] == 1
    assert commits.scene_epoch(cid, "001--landing") == 1
    # ...and a checkpoint must not drop it either
    commits.checkpoint(cid, "caller-chose-this", {"timeline": "done", "edits": {}})
    assert commits.lookup(cid, "caller-chose-this")["claimed"] == 1


def test_a_non_ascii_digit_key_is_not_read_as_a_minted_token(monkeypatch, tmp_path):
    """`\\d` matches Unicode decimal digits and `int()` parses them, so a caller
    key like `٠-<32 hex>` would be read as a minted token at epoch 0 -- and lose
    its first save to a spurious 409 the moment the scene moved on. `mint` emits
    ASCII and nothing else."""
    assert commits.token_epoch("٠-" + "a" * 32) is None      # ARABIC-INDIC ZERO
    assert commits.token_epoch("０-" + "a" * 32) is None      # FULLWIDTH ZERO
    assert commits.token_epoch("0-" + "a" * 32) == 0


def test_renaming_a_scene_repoints_the_stored_replay_result(monkeypatch, tmp_path):
    """A completed entry's result is the chronicle record, which names the scene
    a second time. Left behind, a replay after a rename is accepted and answers
    with a record naming a scene that no longer exists."""
    cid = _campaign(monkeypatch, tmp_path)
    commits.reserve(cid, "tok", "fp", "001--landing", {})
    commits.record(cid, "tok", {"id": "001--landing", "one_line": "o", "applied": []},
                   "fp", "001--landing")
    scene_refs.repoint(cid, {"001--landing": "001--the-landing"})
    entry = commits.lookup(cid, "tok")
    assert entry["sid"] == "001--the-landing"
    assert entry["result"]["id"] == "001--the-landing"
