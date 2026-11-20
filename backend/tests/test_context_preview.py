"""085's proof: a player previews which archived promise and which lore an NPC's context likely recalls for an
unsent draft, with nothing posted, written or sent, and the estimate is the context the turn gets once posted.

Synthetic card chat (Mara Vell, with Anya Rook as the persona and the card's harbor lorebook); no model is
configured, so any provider call would fail the run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from scene_fixtures import ANA, LIGHTHOUSE, MARA, Table, attach_tamsin, card_library
from store_helpers import OWNER
from textapi_fixtures import V1_CHAT, Device, desktop_library

from grimoire.app.adapters.providers import configure
from grimoire.app.appapi import AppApi
from grimoire.context import Budget
from grimoire.core import Moment, Proposition, Report
from grimoire.evidencestore import EvidenceLibrary
from grimoire.play.preview import DraftRefused, preview_context
from grimoire.textapi import ApiRefused

HERE = Path(__file__).parent
PANEL = HERE.parent / "src" / "grimoire" / "app" / "static" / "pages" / "providers" / "context.js"
NODE = shutil.which("node")
PROMISE = "Anya promised to bring back the silver compass before the spring tide."
DRAFT = '"Mara, I kept my word about the silver compass. Is the lighthouse still dark?"'


def files(folder):
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def archive(table, chat, mara, *, chatter=0):
    """The promise Mara was told long ago, then ``chatter`` newer, unrelated things she was told."""
    c = chat.campaign_id
    records = [Proposition(c, "promise", PROMISE, Moment(1), "director"),
               Report(c, "promised", mara, "promise", "told", Moment(1), "scene:quay", confirmed=True)]
    for n in range(chatter):
        records += [Proposition(c, f"gossip-{n}", f"The fishmonger raised the price of eels, rumor {n}.", Moment(2 + n),
                                "director"),
                    Report(c, f"heard-{n}", mara, f"gossip-{n}", "told", Moment(2 + n), "scene:market", confirmed=True)]
    EvidenceLibrary(table.store, table.owner, owner=OWNER).append(c, records).commit()


@pytest.fixture
def chat_with_mara(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        mara = next(iter(table.runner.open(chat).npcs))
        archive(table, chat, mara)
    return folder, chat, mara


def test_a_draft_previews_the_recalled_promise_and_triggered_lore_without_any_write(chat_with_mara):
    folder, chat, mara = chat_with_mara
    with Table(folder, runtime=False) as table:
        before = files(folder)
        current = table.runner.inspect_context(chat, mara)
        preview = preview_context(table.runner, chat, table.player(), mara, DRAFT)
        assert files(folder) == before  # nothing posted, kept or written
        assert not table.harness.prompts()  # and nothing sent
        assert preview["estimated"] is True and preview["draft"] == {"heard": True, "kind": "speech"}
        # the archived promise is recalled by the words the draft shares with it, and it is in the context
        [promise] = [r for r in preview["recalls"] if r["lane"] == "memory"]
        assert promise["line"] == f"You were told: {PROMISE}" and promise["included"] is True
        assert promise["why"] == "shared_words" and promise["words"] == ["compass", "silver"]
        # the lighthouse lore is not in the saved context: the draft triggers it
        assert LIGHTHOUSE not in [item["line"] for item in current["items"]]
        [lore] = [r for r in preview["recalls"] if r["lane"] == "lore"]
        assert (lore["line"], lore["why"], lore["included"]) == (LIGHTHOUSE, "triggered", True)
        assert [item["line"] for item in preview["changes"]["added"]] == [LIGHTHOUSE]
        assert preview["changes"]["removed"] == []
        assert preview["tokens"]["with_draft"] > preview["tokens"]["now"]
        assert "silver compass" in preview["context"]["heard"]  # the draft is what Mara would hear
        assert "silver compass" not in current["heard"]
        # once posted, the turn's context is the one previewed
        table.runner.post(chat, table.player(), DRAFT)
        posted = table.runner.inspect_context(chat, mara)
        assert posted["items"] == preview["context"]["items"]
        assert posted["omitted"] == preview["context"]["omitted"]
        assert posted["heard"] == preview["context"]["heard"] and posted["system"] == preview["context"]["system"]


def test_a_recalled_memory_the_budget_sheds_is_a_warning_with_its_line(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        mara = next(iter(table.runner.open(chat).npcs))
        archive(table, chat, mara, chatter=6)
    small = Budget(1500, lane_caps={"memory": 60})  # room for the newest few memories only
    with Table(folder, runtime=False, budget=small) as table:
        preview = preview_context(table.runner, chat, table.player(), mara, DRAFT)
        [promise] = [r for r in preview["recalls"] if r["lane"] == "memory"]
        assert promise["line"] == f"You were told: {PROMISE}"
        assert (promise["included"], promise["reason"]) == (False, "lane_cap")
        assert PROMISE not in preview["context"]["system"]
        # an unrelated memory is not offered as a recall
        assert not any("eels" in r["line"] for r in preview["recalls"])


def test_an_npc_that_would_not_hear_the_draft_keeps_its_saved_context(tmp_path):
    folder, chat, _ = card_library(tmp_path)
    with Table(folder, runtime=False) as table:
        table.runner.seat(chat, ANA)
        mara = next(iter(table.runner.open(chat).npcs))
        attach_tamsin(table.store, table.owner, chat)
        archive(table, chat, mara)
    with Table(folder, runtime=False) as table:
        whisper = 'I whisper to Tamsin Hale, "The silver compass is gone and the lighthouse is dark."'
        current = table.runner.inspect_context(chat, mara)
        preview = preview_context(table.runner, chat, table.player(), mara, whisper)
        assert preview["draft"]["heard"] is False
        assert preview["recalls"] == [] and preview["changes"] == {"added": [], "removed": []}
        assert preview["context"] == current  # neither the whisper nor the lore it would trigger reaches Mara
        assert "Tamsin" not in str(preview["draft"])  # who would hear it is not said


@pytest.mark.parametrize(("draft", "reason"), [("Grimoire, pause", "table_request"), ("   ", "no_content")])
def test_a_draft_post_would_refuse_is_refused_with_its_reason(chat_with_mara, draft, reason):
    folder, chat, mara = chat_with_mara
    with Table(folder, runtime=False) as table:
        before = files(folder)
        with pytest.raises(DraftRefused) as refused:
            preview_context(table.runner, chat, table.player(), mara, draft)
        assert refused.value.reason == reason
        assert files(folder) == before


def test_a_draft_cannot_be_previewed_for_a_character_another_player_controls(chat_with_mara):
    folder, chat, mara = chat_with_mara
    with Table(folder, runtime=False) as table:
        with pytest.raises(DraftRefused) as refused:
            preview_context(table.runner, chat, table.player("human:bob"), mara, DRAFT)
        assert refused.value.reason == "not_at_table"


def test_the_app_offers_the_preview_beside_the_inspector_and_writes_nothing(tmp_path):
    folder, _ = desktop_library(tmp_path / "fixture")
    settings = tmp_path / "model.json"
    with Device(folder, runtime=False) as device:
        configure(device.api, settings, environ={})
        app = AppApi(device.api)
        app.call("continue", {"chat": str(V1_CHAT)})
        before = files(folder)
        preview = app.call("providers.preview_context",
                           {"chat": str(V1_CHAT), "actor": str(MARA), "draft": '"Where is the ledger kept?"'})
        assert (preview["sent"], preview["written"], preview["estimated"]) == (False, False, True)
        assert preview["draft"]["heard"] is True and "Where is the ledger kept?" in preview["context"]["heard"]
        persona = str(device.api.runner.open(V1_CHAT).persona)
        refused = {"no_content": {"actor": str(MARA), "draft": " "},
                   "table_request": {"actor": str(MARA), "draft": "Grimoire, pause"},
                   "not_npc": {"actor": persona, "draft": "Hello"},
                   "invalid_field": {"actor": "not a ref", "draft": "Hello"}}
        for reason, args in refused.items():
            with pytest.raises(ApiRefused) as error:
                app.call("providers.preview_context", {"chat": str(V1_CHAT), **args})
            assert error.value.reason == reason
        assert files(folder) == before and not settings.exists()
        assert not device.prompts()


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_the_inspector_page_previews_a_draft_as_an_estimate_and_warns_of_shed_recalls():
    done = subprocess.run([NODE, str(HERE / "pages" / "context_preview_dom.mjs"), PANEL.as_uri()], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
