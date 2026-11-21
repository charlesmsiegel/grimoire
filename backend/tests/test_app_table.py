"""Sprint 115's shared turn (058): two players at one screen, through the ``table.*`` adapter ops and the chat page.

Anya Rook is the owner's character and Tamsin Hale is Bob's, both at this device. Each drafts their own part (058's
core ``draft`` op, held in memory), the turn is reviewed (``table.review``) and posted once as reviewed
(``table.submit``): a part changed after the review refuses ``changed`` and posts nothing, and a submission retried
after its answer was lost posts nothing twice. The page runs end to end under Node (``js/table.e2e.mjs``), including
the sprint's proving scenario: switch chats without losing a draft, and resume play. Synthetic library; scripted
generation; no model is called.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scene_fixtures import POLICY
from table_fixtures import BOB, OWNER, node_e2e, played, say

from grimoire.app.adapters.table import etag_of
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.textapi import ApiRefused, TextApi

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
FIELDS = ("actor", "by", "text", "mode")


def refused(table, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        table(op, **args)
    return caught.value


def commit_args(review: dict) -> dict:
    return {"chat": review["chat"], "expected": review["etag"], "head": review["head"],
            "reviewed": [{name: item[name] for name in FIELDS} for item in review["drafts"]]}


def two_parts(table) -> tuple[str, str]:
    chat, anya, tamsin = str(table.chat), table.cast["Anya Rook"], table.cast["Tamsin Hale"]
    table("draft", chat=chat, actor=anya, text='"Is the lamp lit?"')
    table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check the lamp*")
    return anya, tamsin


def test_the_shared_turn_is_drawn_in_the_chat_and_its_ops_are_served(tmp_path):
    pages = {page.id: page for page in load_pages(STATIC)}
    assert "chat" in pages  # the shared turn is part of the chat itself, not a page of its own
    assert (STATIC / "pages" / "chat" / "table.js").is_file()
    with played(tmp_path) as table:
        ops = set(table("library")["ops"])
    assert {"table.review", "table.submit", "draft", "drop_draft", "submit", "seat"} <= ops


def test_a_review_names_each_part_its_writer_and_where_the_drafts_are_kept(tmp_path):
    with played(tmp_path) as table:
        chat = str(table.chat)
        empty = table("table.review", chat=chat)
        assert empty["drafts"] == [] and empty["etag"] == etag_of([]) and empty["kept_in"] == "memory"
        assert empty["players"] == [OWNER, BOB] and empty["owner"] == OWNER and empty["head"] is not None
        anya, tamsin = two_parts(table)
        review = table("table.review", chat=chat)
        assert [(item["name"], item["by"], item["ready"]) for item in review["drafts"]] == [
            ("Anya Rook", OWNER, True), ("Tamsin Hale", BOB, True)]
        assert review["etag"] != empty["etag"] and review["limits"]["chars"] > 0
        # a part held is its writer's: Bob cannot correct the owner's part for Anya
        assert refused(table, "draft", chat=chat, actor=anya, by=BOB, text="hi").reason == "not_your_draft"
        assert table("table.review", chat=chat)["etag"] == review["etag"]


def test_a_part_for_a_character_its_writer_does_not_play_is_held_flagged_and_refused_when_posted(tmp_path):
    # 058 checks who plays a character when the turn is posted, not when a part is held (docs/TABLE.md)
    with played(tmp_path) as table:
        chat, anya = str(table.chat), table.cast["Anya Rook"]
        held = table("draft", chat=chat, actor=anya, by=BOB, text='"Bob speaks for Anya"')
        assert [(item["actor"], item["by"]) for item in held["drafts"]] == [(anya, BOB)]
        review = table("table.review", chat=chat)
        assert [(item["controller"], item["ready"]) for item in review["drafts"]] == [(OWNER, False)]
        turns = table("open", chat=chat)["turns"]
        assert refused(table, "table.submit", **commit_args(review)).reason == "not_your_character"
        assert table("open", chat=chat)["turns"] == turns


def test_the_turn_is_posted_once_as_reviewed_and_a_lost_answer_posts_nothing_twice(tmp_path):
    with played(tmp_path, [say("The lamp gutters.")]) as table:
        chat = str(table.chat)
        anya, tamsin = two_parts(table)
        review = table("table.review", chat=chat)
        args = commit_args(review)
        assert refused(table, "table.submit", chat=chat).reason == "review_required"
        assert refused(table, "table.submit", **{**args, "reviewed": []}).reason == "review_required"
        assert refused(table, "table.submit", **{**args, "expected": "d-other"}).reason == "invalid_field"
        assert refused(table, "table.submit", **{**args, "reviewed": [{"actor": anya}]}).reason == "invalid_field"
        before = len(table("open", chat=chat)["turns"])

        done = table("table.submit", **args)
        turns = done["scene"]["turns"]
        assert done["done"] == {"already_applied": False, "posts": 2, "turn": turns[-1]["turn"], "answered": False}
        assert len(turns) == before + 1 and done["scene"]["drafts"] == [] and done["review"]["drafts"] == []
        assert [(post["actor"], post["by"]) for post in turns[-1]["posts"]] == [(anya, OWNER), (tamsin, BOB)]

        # the same submission again (its answer lost): the turn after the reviewed head holds it already
        again = table("table.submit", **args)
        assert again["done"] == {"already_applied": True, "posts": 2, "turn": turns[-1]["turn"], "answered": False}
        assert len(table("open", chat=chat)["turns"]) == before + 1

        # play resumes: the table answers the combined turn once
        answered = table("generate", chat=chat)
        assert answered["state"]["open_turn"] == turns[-1]["turn"]


def test_a_part_changed_after_the_review_refuses_it_and_nothing_is_posted(tmp_path):
    with played(tmp_path) as table:
        chat = str(table.chat)
        anya, tamsin = two_parts(table)
        review = table("table.review", chat=chat)
        before = table("open", chat=chat)["turns"]
        # Bob corrects his part after the owner reviewed the turn
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I blow the lamp out*")
        assert refused(table, "table.submit", **commit_args(review)).reason == "changed"
        assert table("open", chat=chat)["turns"] == before
        # moved, dropped or added: each is another set of drafts than the one reviewed
        fresh = table("table.review", chat=chat)
        table("draft", chat=chat, actor=tamsin, by=BOB, at=0)
        assert refused(table, "table.submit", **commit_args(fresh)).reason == "changed"
        moved = table("table.review", chat=chat)
        table("drop_draft", chat=chat, actor=anya)
        assert refused(table, "table.submit", **commit_args(moved)).reason == "changed"
        # a review of drafts that were posted by someone else meanwhile is not this review's turn
        alone = table("table.review", chat=chat)
        table("draft", chat=chat, actor=anya, text='"Something else entirely."')
        table("submit", chat=chat)
        assert refused(table, "table.submit", **commit_args(alone)).reason == "changed"


def keep_answer(table, chat: str) -> str:
    """The table answers the open turn and its first take is kept; the turn's id."""
    turn = table("generate", chat=chat)["state"]["open_turn"]
    table("keep", chat=chat, take=1, turn=turn)
    return turn


def test_a_review_taken_before_another_turn_was_posted_posts_nothing(tmp_path):
    with played(tmp_path, [say("The lamp gutters."), say("Rain.")]) as table:
        chat, tamsin = str(table.chat), table.cast["Tamsin Hale"]
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check the lamp*")
        review = table("table.review", chat=chat)
        # the owner posts on their own through the chat's composer; the table answers and the take is kept. Bob's
        # part is still held, so the drafts are exactly the ones reviewed: only the head says the review is stale
        table("post", chat=chat, text='"Hello?"')
        keep_answer(table, chat)
        turns = table("open", chat=chat)["turns"]
        assert table("table.review", chat=chat)["etag"] == review["etag"] and turns[-1]["turn"] != review["head"]
        stale = refused(table, "table.submit", **commit_args(review))
        assert stale.reason == "changed" and "not posted now" in str(stale) and "another turn" in str(stale)
        assert table("open", chat=chat)["turns"] == turns
        # a fresh review of the same parts posts them, after the new head
        done = table("table.submit", **commit_args(table("table.review", chat=chat)))
        assert done["done"]["already_applied"] is False and done["scene"]["turns"][-2]["turn"] == turns[-1]["turn"]


def test_a_retry_matches_the_posted_words_exactly_never_a_part_of_them(tmp_path):
    with played(tmp_path) as table:
        chat, tamsin = str(table.chat), table.cast["Tamsin Hale"]
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check the lamp carefully*")
        first = table("table.review", chat=chat)
        # Bob shortens his part and that second review is posted: the words posted are contained in the first
        # review's, but they are not its words, so the first review was never posted
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check*")
        table("table.submit", **commit_args(table("table.review", chat=chat)))
        turns = table("open", chat=chat)["turns"]
        assert turns[-1]["posts"][0]["text"] in "*I check the lamp carefully*"
        assert refused(table, "table.submit", **commit_args(first)).reason == "changed"
        assert table("open", chat=chat)["turns"] == turns


def test_a_retry_is_answered_although_058_posted_other_words_than_the_drafts(tmp_path):
    # quotes taken off by 008 and the addressee taken off a whisper by 032: the receipt, not the words, answers it
    with played(tmp_path, [say("The lamp gutters.")]) as table:
        chat, anya, tamsin = str(table.chat), table.cast["Anya Rook"], table.cast["Tamsin Hale"]
        table("draft", chat=chat, actor=anya, text='"Is the lamp lit?"')
        table("draft", chat=chat, actor=tamsin, by=BOB, text='*I whisper to Mara Vell: "the lamp"*')
        args = commit_args(table("table.review", chat=chat))
        posts = table("table.submit", **args)["scene"]["turns"][-1]["posts"]
        assert [(post["kind"], post["text"]) for post in posts] == [("speech", "Is the lamp lit?"), ("action", "the lamp")]
        assert table("table.submit", **args)["done"]["already_applied"] is True


def test_a_lost_answer_is_found_after_new_parts_and_later_turns_and_answered_once(tmp_path):
    with played(tmp_path, [say("The lamp gutters."), say("Rain."), say("Thunder.")]) as table:
        chat, anya, tamsin = str(table.chat), table.cast["Anya Rook"], table.cast["Tamsin Hale"]
        table("draft", chat=chat, actor=anya, text='"Is the lamp lit?"')
        args = commit_args(table("table.review", chat=chat))
        posted = table("table.submit", **args)["done"]["turn"]  # its answer is lost
        # Bob holds a part for the next turn meanwhile: the retry is still this review's turn, not yet answered
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check the lamp*")
        again = table("table.submit", **args)
        assert again["done"] == {"already_applied": True, "posts": 1, "turn": posted, "answered": False}
        # play moves past it: answered, then another turn posted and answered; the retry still finds its turn,
        # says it is answered (so no second answer is asked for), and posts nothing
        assert keep_answer(table, chat) == posted
        table("submit", chat=chat)
        keep_answer(table, chat)
        turns = table("open", chat=chat)["turns"]
        assert turns[-1]["turn"] != posted
        late = table("table.submit", **args)
        assert late["done"] == {"already_applied": True, "posts": 1, "turn": posted, "answered": True}
        assert table("open", chat=chat)["turns"] == turns


def test_only_the_turn_right_after_the_head_can_be_the_reviewed_one(tmp_path):
    with played(tmp_path, [say("The lamp gutters."), say("Rain.")]) as table:
        chat, anya = str(table.chat), table.cast["Anya Rook"]
        table("draft", chat=chat, actor=anya, text='"Is the lamp lit?"')
        review = table("table.review", chat=chat)
        # someone else's turn is posted first; then the very same words are posted again by a later review: that
        # later turn is not this review's (it came after a head this review never saw), so its retry posts nothing
        table("drop_draft", chat=chat, actor=anya)
        table("post", chat=chat, text='"Hello?"')
        keep_answer(table, chat)
        table("draft", chat=chat, actor=anya, text='"Is the lamp lit?"')
        table("table.submit", **commit_args(table("table.review", chat=chat)))
        turns = table("open", chat=chat)["turns"]
        assert refused(table, "table.submit", **commit_args(review)).reason == "changed"
        assert table("open", chat=chat)["turns"] == turns


def test_the_same_words_posted_through_the_composer_are_not_this_review(tmp_path):
    with played(tmp_path) as table:
        chat, anya = str(table.chat), table.cast["Anya Rook"]
        table("draft", chat=chat, actor=anya, text='"Is anyone there?"')
        review = table("table.review", chat=chat)
        # the same line is posted through the ordinary composer (a second tab, say): the turn holds exactly the
        # words reviewed, but the review was never submitted and its part is still held, so it is no receipt
        table("post", chat=chat, text='"Is anyone there?"')
        turns = table("open", chat=chat)["turns"]
        assert [post["text"] for post in turns[-1]["posts"]] == ["Is anyone there?"]
        stale = refused(table, "table.submit", **commit_args(review))
        assert stale.reason == "changed" and "another turn" in str(stale)
        after = table("open", chat=chat)
        assert after["turns"] == turns and [item["text"] for item in after["drafts"]] == ['"Is anyone there?"']


def test_a_retry_is_answered_from_the_receipt_even_after_the_same_words_are_held_again(tmp_path):
    with played(tmp_path, [say("The lamp gutters.")]) as table:
        chat, tamsin = str(table.chat), table.cast["Tamsin Hale"]
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check the lamp*")
        review = table("table.review", chat=chat)
        twin = table("table.review", chat=chat)  # the same review read in a second tab
        first = table("table.submit", **commit_args(review))  # its answer is lost
        # Bob holds the very same words again for the next turn: the retry is still the first turn's, and the new
        # part stays held for the next one
        table("draft", chat=chat, actor=tamsin, by=BOB, text="*I check the lamp*")
        again = table("table.submit", **commit_args(review))
        assert again["done"] == {**first["done"], "already_applied": True}
        assert [item["text"] for item in again["scene"]["drafts"]] == ["*I check the lamp*"]
        # the second tab's copy of the same review is the same parts, posted once
        assert table("table.submit", **commit_args(twin))["done"]["turn"] == first["done"]["turn"]
        assert len(table("open", chat=chat)["turns"]) == len(first["scene"]["turns"])


def test_the_receipt_is_kept_in_memory_and_an_app_restart_loses_it(tmp_path):
    with played(tmp_path) as table:
        chat, anya = str(table.chat), table.cast["Anya Rook"]
        table("draft", chat=chat, actor=anya, text='"Is the lamp lit?"')
        args = commit_args(table("table.review", chat=chat))
        table("table.submit", **args)  # its answer is lost, and then the app restarts
        restarted = AppApi(TextApi(table.table.store, table.table.authority, owner=OWNER, local_players=[BOB],
                                   runtime=table.table.harness.runtime, policy=POLICY))
        turns = restarted.call("open", {"chat": chat})["turns"]
        with pytest.raises(ApiRefused) as caught:
            restarted.call("table.submit", args)
        assert caught.value.reason == "changed" and "not posted now" in str(caught.value)
        assert restarted.call("open", {"chat": chat})["turns"] == turns


def test_a_part_whose_character_changed_hands_is_flagged_and_bad_requests_are_refused(tmp_path):
    with played(tmp_path) as table:
        chat = str(table.chat)
        _, tamsin = two_parts(table)
        held = table("table.review", chat=chat)
        assert all(item["ready"] for item in held["drafts"])
        # Tamsin goes back to the director while Bob's part is held: the review says it is no longer his
        table("seat", chat=chat, actor=tamsin, player="director")
        after = table("table.review", chat=chat)
        assert [item["ready"] for item in after["drafts"]] == [True, False]
        # and 058 refuses to post it, with its reason; nothing is posted
        turns = table("open", chat=chat)["turns"]
        assert refused(table, "table.submit", **commit_args(after)).reason
        assert table("open", chat=chat)["turns"] == turns
        # an unknown chat or a malformed request is refused with its reason, never a server error
        assert refused(table, "table.review", chat="campaign:nope#chat/none").reason
        assert refused(table, "table.review", chat=5).reason == "invalid_field"
        assert refused(table, "table.submit", **{**commit_args(held), "head": 7}).reason == "invalid_field"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_two_players_draft_switch_chats_keep_their_drafts_and_resume_play(tmp_path):
    with played(tmp_path, [say("The lamp gutters."), say("Rain on the shutters.")]) as table:
        result = node_e2e(table, "table.e2e.mjs", {"GRIMOIRE_BOB": BOB}, NODE)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
