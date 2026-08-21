"""The durable record of whether a send's post is still in the transcript.

This file exists for one question, asked after the in-memory run record has
expired: the transcript is missing the player's post -- was it rolled back, or
did it never land? Text cannot answer it (a player who repeats themselves
matches an earlier turn), so the attempt id is recorded beside the append and
cleared inside the rollback.

The tests here are mostly about what happens when the record itself will not
read, because that is where the two directions of being wrong stop being
symmetrical.
"""

from __future__ import annotations

import pytest

import grimoire.store as store
from grimoire.routes import scenes as scenes_mod


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = store.worlds.create_world("Realm")
    return store.campaigns.create_campaign("Saltmarch", wid)


def test_a_remembered_attempt_is_retained(campaign):
    store.attempts.remember(campaign, "ident-1", "a-1")
    assert store.attempts.retained(campaign, "ident-1", "a-1") is True


def test_forgetting_retires_it(campaign):
    store.attempts.remember(campaign, "ident-1", "a-1")
    store.attempts.forget(campaign, "ident-1", "a-1")
    assert store.attempts.retained(campaign, "ident-1", "a-1") is False


def test_an_attempt_is_scoped_to_its_scene_identity(campaign):
    """Attempt ids are unique per scene, not per campaign. Answering for the
    wrong scene would settle a send whose words exist nowhere else."""
    store.attempts.remember(campaign, "ident-1", "a-1")
    assert store.attempts.retained(campaign, "ident-2", "a-1") is False


def test_the_query_path_stays_fail_soft(campaign, monkeypatch):
    """`retained` answers "is this post durable?", and an unreadable file means
    "I cannot say" -- which the caller must read as "keep the player's text"."""
    store.attempts.remember(campaign, "ident-1", "a-1")
    monkeypatch.setattr(store.attempts.json, "loads",
                        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("corrupt")))

    assert store.attempts.retained(campaign, "ident-1", "a-1") is False


def _make_unreadable(cid) -> None:
    """Turn the record into something `read_text` raises `OSError` on.

    A directory at the path, rather than a patched `read_text`: this is the
    real syscall failing, so it also proves the code is reading by the path the
    writer uses -- and it needs no undo, since the query path tolerates it.
    """
    p = store.campaigns.paths.campaign_root(cid) / "attempts.json"
    p.unlink(missing_ok=True)
    p.mkdir()


def _make_readable_again(cid) -> None:
    (store.campaigns.paths.campaign_root(cid) / "attempts.json").rmdir()


def test_a_mutating_read_refuses_a_file_it_could_not_open(campaign):
    """The same `{}` on the ROLLBACK path is the opposite sentence: `forget`
    would conclude the marker is already absent and return happily, and
    `_take_the_post_back` would then delete the player's post while the real
    file still says it is retained. Once it reads again, recovery believes that
    marker and discards the only copy of what they typed.
    """
    store.attempts.remember(campaign, "ident-1", "a-1")
    _make_unreadable(campaign)

    with pytest.raises(OSError):
        store.attempts.forget(campaign, "ident-1", "a-1")


def test_a_missing_file_is_not_an_unreadable_one(campaign):
    """The ordinary case on both paths -- nothing has been recorded yet -- and
    a `forget` that raised on it would refuse every rollback of a first turn."""
    store.attempts.forget(campaign, "ident-1", "a-1")     # must not raise


def test_a_corrupt_file_is_rewritten_rather_than_refused(campaign):
    """Distinct from unreadable: trying again cannot fix invalid JSON, so
    refusing every rollback forever is worse than replacing a file that was
    already unusable."""
    (store.campaigns.paths.campaign_root(campaign) / "attempts.json").write_text(
        "{not json", encoding="utf-8")

    store.attempts.remember(campaign, "ident-1", "a-1")   # must not raise
    assert store.attempts.retained(campaign, "ident-1", "a-1") is True


def test_the_rollback_leaves_the_post_alone_when_the_record_will_not_read(campaign):
    """The point of all of the above, at the call site: an unreadable record
    means the post stays in the transcript and the client keeps its copy, so
    the worst case is a duplicate the player can see and delete."""
    sid = store.scenes.create_scene(campaign, "Mara")
    posted_at = store.scenes.append_message(campaign, sid, "user", "Mara waits.")
    identity = store.scenes.scene_identity(campaign, sid)

    class _Run:
        scene_identity = identity
        attempt_id = "a-1"

    store.attempts.remember(campaign, identity, "a-1")
    _make_unreadable(campaign)

    with pytest.raises(OSError):
        scenes_mod._take_the_post_back(campaign, sid, posted_at, "Mara waits.", _Run())

    _make_readable_again(campaign)
    messages = store.scenes.read_scene(campaign, sid)["messages"]
    assert [m["content"] for m in messages] == ["Mara waits."]
