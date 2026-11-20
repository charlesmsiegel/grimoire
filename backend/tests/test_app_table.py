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
from table_fixtures import BOB, OWNER, node_e2e, played, say

from grimoire.app.adapters.table import etag_of
from grimoire.app.pages import load_pages
from grimoire.textapi import ApiRefused

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
        # a player drafts only for the character they play: Bob cannot write Anya's part
        assert refused(table, "draft", chat=chat, actor=anya, by=BOB, text="hi").reason
        assert table("table.review", chat=chat)["etag"] == review["etag"]


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
        assert done["done"] == {"already_applied": False, "posts": 2}
        turns = done["scene"]["turns"]
        assert len(turns) == before + 1 and done["scene"]["drafts"] == [] and done["review"]["drafts"] == []
        assert [(post["actor"], post["by"]) for post in turns[-1]["posts"]] == [(anya, OWNER), (tamsin, BOB)]

        # the same submission again (its answer lost): the turn after the reviewed head holds it already
        again = table("table.submit", **args)
        assert again["done"]["already_applied"] is True
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
