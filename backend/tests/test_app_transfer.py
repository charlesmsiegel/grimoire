"""Sprint 116's copy of a chat into another campaign (084), through the ``transfer.*`` adapter ops and its page.

Over the synthetic card library: Mara Vell's card chat (Anya Rook as the owner's persona) is played at the app (the
card's greeting, a wave, Mara's kept reply, then a whisper to Mara), and the scenario chat's campaign, which has its
own Anya, is the target. The target also holds an alternate portrayal of Mara: another entity with Mara's name. The
proving scenario runs as the page does: the director scans the copy (the alternate is shown as a namesake and never
offered), creates Mara new and reuses the target's Anya, plans it (the whisper is omitted as private) and commits it
as reviewed; the new chat opens with the copied history, and the origin's files are byte for byte what they were.
A change between review and commit is refused; a retried commit writes nothing again. The page runs end to end
under Node (``js/transfer.e2e.mjs``). Every model call is scripted; nothing is billed.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

import pytest
from generation_fixtures import say
from scene_fixtures import MARA_VELL, POLICY, Table, card_library
from store_helpers import OWNER
from table_fixtures import Played, node_e2e

from grimoire.app.adapters.transfer import NOT_AVAILABLE
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.core.identity import InstanceRef, SourceRef
from grimoire.store import LinkSpec, RecordEnvelope, SourceManifest
from grimoire.testing.fileops import snapshot
from grimoire.textapi import ApiRefused, TextApi

NODE = shutil.which("node")
STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
DIRECTOR = {"audience": "director"}
GREETING = "You came back. I didn't think you would."  # the card's own first message
WAVE = "*I wave to Mara*"
KEPT = "Ledgers drown, and debts float to the top."
WHISPER = "The key is under the third crate."
TALES = "catalog:harbor-tales"
ALTERNATE = SourceRef(TALES, "character", "mara-of-the-lamps")


def seed_alternate(table: Table, campaign_id: str) -> InstanceRef:
    """Another portrayal of Mara, named like her, with an actor in the target campaign (as 084's own test seeds)."""
    alternate = InstanceRef(campaign_id, "character", "mara-of-the-lamps-1")
    txn = table.store.transaction()
    txn.put(SourceManifest(TALES, OWNER, "Harbor tales", links=(
        LinkSpec(str(ALTERNATE.at("r1")), "alternate_portrayal", str(MARA_VELL)),)), None)
    txn.put(RecordEnvelope(ALTERNATE.at("r1"), {"bio": "a lamplighter"}, name="Mara Vell"), None)
    txn.put(RecordEnvelope(alternate, {}, derived_from=str(ALTERNATE), base=str(ALTERNATE.at("r1"))), None)
    txn.commit(table.owner)
    return alternate


@contextlib.contextmanager
def origin(tmp_path: Path, steps=()):
    """The card chat played at the app, the alternate Mara seeded in the target, and the app over the library."""
    folder, chat, storm = card_library(tmp_path)
    with Table(folder, [say(KEPT), *steps]) as table:
        alternate = seed_alternate(table, storm.campaign_id)
        api = TextApi(table.store, table.authority, owner=OWNER, runtime=table.harness.runtime, policy=POLICY)
        app = AppApi(api)
        c = str(chat)
        scene = app.call("continue", {"chat": c})  # the card's greeting is the chat's first turn
        if scene["state"]["open_turn"]:
            app.call("keep", {"chat": c, "take": 1, "turn": scene["state"]["open_turn"]})
        app.call("post", {"chat": c, "text": WAVE})
        scene = app.call("generate", {"chat": c})
        app.call("keep", {"chat": c, "take": 1, "turn": scene["state"]["open_turn"]})
        scene = app.call("post", {"chat": c, "text": f'I whisper to Mara Vell, "{WHISPER}"'})
        scene = app.call("pass", {"chat": c, "turn": scene["state"]["open_turn"]})
        cast = {member["name"]: member["actor"] for member in scene["cast"]}
        played = Played(table, api, chat, storm, cast)
        played.alternate = alternate  # type: ignore[attr-defined]
        played.folder = folder  # type: ignore[attr-defined]
        yield played


def refused(played, op: str, **args) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        played(op, **args)
    return caught.value


def library_bytes(played) -> dict[str, bytes]:
    root = played.table.store.root
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*"))
            if path.is_file() and "authority" not in path.parts and not path.name.endswith((".lock", "-journal"))}


def origin_files(played) -> dict:
    return snapshot(played.table.store.root / "campaigns" / played.chat.campaign_id)


def scanned(played) -> dict:
    return played("transfer.scan", chat=str(played.chat), target=played.storm.campaign_id, **DIRECTOR)


def decisions(scan: dict, **by_name) -> dict[str, str]:
    return {member["actor"]: by_name[member["name"]] for member in scan["cast"]}


def request(played, cast: dict, operation: str = "op-copy-1", **more) -> dict:
    return {"chat": str(played.chat), "target": played.storm.campaign_id, "operation_id": operation, "cast": cast,
            **DIRECTOR, **more}


def test_the_page_registers_hidden_on_its_own_route_and_needs_its_ops():
    page = {item.id: item for item in load_pages(STATIC)}["transfer"]
    assert (page.route, page.slot, page.owner) == ("transfer", "hidden", "116")
    assert set(page.requires) == {"transfer.targets", "transfer.scan", "transfer.plan", "transfer.commit"}


def test_a_copy_is_the_directors_the_table_sees_none_of_it(tmp_path):
    with origin(tmp_path) as played:
        chat, target = str(played.chat), played.storm.campaign_id
        before = library_bytes(played)
        for op, args in (("transfer.targets", {}), ("transfer.scan", {"target": target}),
                         ("transfer.plan", {"target": target, "operation_id": "op-x", "cast": {}}),
                         ("transfer.commit", {"target": target, "operation_id": "op-x", "cast": {}, "review": "x"})):
            assert refused(played, op, chat=chat, **args).reason == "director_only"
            assert refused(played, op, chat=chat, audience="table", **args).reason == "director_only"
        assert refused(played, "transfer.targets", chat=chat, audience="everyone").reason == "invalid_field"
        assert library_bytes(played) == before


def test_the_director_copies_the_chat_as_reviewed_and_the_origin_is_never_written(tmp_path):
    with origin(tmp_path) as played:
        chat, target = str(played.chat), played.storm.campaign_id
        targets = played("transfer.targets", chat=chat, **DIRECTOR)
        assert [item["campaign"] for item in targets["targets"]] == [target], "never the origin's own campaign"
        assert targets["not_available"] == NOT_AVAILABLE
        before, untouched = library_bytes(played), origin_files(played)

        scan = scanned(played)
        by_name = {member["name"]: member for member in scan["cast"]}
        mara, anya = by_name["Mara Vell"], by_name["Anya Rook"]
        # a name is no match: the alternate portrayal is a namesake, shown and never a candidate
        assert mara["candidates"] == [] and [item["actor"] for item in mara["namesakes"]] == [str(played.alternate)]
        assert mara["namesakes"][0]["name"] == "Mara Vell" and mara["can_create"] is True
        assert [item["name"] for item in anya["candidates"]] == ["Anya Rook"] and anya["namesakes"] == []
        assert scan["history"] == {"pages": 0, "page_posts": 0, "turns": 3}
        assert scan["history_problems"] == [] and scan["provider_calls"] == 0
        assert [item["role"] for item in scan["sources"]] == ["lorebook"]
        target_anya = anya["candidates"][0]["actor"]

        # every actor is decided, and a namesake is refused as another individual, with 084's reason
        assert refused(played, "transfer.plan", **request(played, {mara["actor"]: "new"})).reason == "unreviewed_actor"
        assert refused(played, "transfer.plan", **request(played, decisions(
            scan, **{"Mara Vell": str(played.alternate), "Anya Rook": target_anya}))).reason == "different_individual"
        assert refused(played, "transfer.plan", **request(played, decisions(
            scan, **{"Mara Vell": "new", "Anya Rook": anya["actor"]}))).reason == "foreign_actor"
        assert refused(played, "transfer.plan", **request(played, decisions(
            scan, **{"Mara Vell": target_anya, "Anya Rook": "new"}))).reason == "actor_mismatch"

        cast = decisions(scan, **{"Mara Vell": "new", "Anya Rook": target_anya})
        args = request(played, cast, label="Copied from the quay")
        planned = played("transfer.plan", **args)
        plan = planned["plan"]
        assert planned["saved"] is False and plan["provider_calls"] == 0
        assert [post["text"] for post in plan["posts"]] == [GREETING, WAVE, KEPT]
        assert all(WHISPER not in (post["text"] or "") for post in plan["posts"])
        (omitted,) = plan["omitted"]
        assert omitted["reason"] == "private" and WHISPER in omitted["text"]
        mapped = {item["name"]: item for item in plan["mapping"]}
        assert mapped["Mara Vell"]["decision"] == "new" and mapped["Mara Vell"]["target"] != str(played.alternate)
        assert mapped["Anya Rook"]["decision"] == "reuse" and mapped["Anya Rook"]["target"] == target_anya
        assert plan["chat"].startswith(f"campaign:{target}#chat/")
        assert all(path.startswith(f"campaigns/{target}/") for path in plan["files"]), "nothing of the origin"
        assert library_bytes(played) == before, "a scan, a plan and every refusal write nothing"
        again = played("transfer.plan", **args)["plan"]
        assert again["review"] == plan["review"] and again["chat"] == plan["chat"], "the same plan, built again"

        done = played("transfer.commit", **args, review=plan["review"])["done"]
        assert done == {"chat": plan["chat"], "already_applied": False, "pages": 1, "posts": 3,
                        "files": plan["files"]}
        assert origin_files(played) == untouched, "the origin is byte for byte what it was"
        copy = played("open", chat=done["chat"])
        assert [item["text"] for item in copy["history"]] == [GREETING, WAVE, KEPT]
        assert copy["label"] == "Copied from the quay" and copy["persona"]["actor"] == target_anya
        assert str(played.alternate) not in {member["actor"] for member in copy["cast"]}

        # a commit retried after a lost answer finds the copy it made and writes nothing again
        after = library_bytes(played)
        assert played("transfer.commit", **args, review=plan["review"])["done"] == {
            "chat": plan["chat"], "already_applied": True, "pages": 1}
        assert library_bytes(played) == after


def test_a_change_after_the_review_refuses_the_commit_and_writes_nothing(tmp_path):
    with origin(tmp_path, steps=[say("Then we count the crates again.")]) as played:
        scan = scanned(played)
        target_anya = next(m for m in scan["cast"] if m["name"] == "Anya Rook")["candidates"][0]["actor"]
        args = request(played, decisions(scan, **{"Mara Vell": "new", "Anya Rook": target_anya}))
        plan = played("transfer.plan", **args)["plan"]
        # a review the page never saw is not this plan's
        assert refused(played, "transfer.commit", **args).reason == "review_required"
        assert refused(played, "transfer.commit", **args, review="not-the-review").reason == "changed"
        # the origin is played on after the review: the copy reviewed is no longer what would be copied
        played("post", chat=str(played.chat), text='"Any news from the quay?"')
        before = library_bytes(played)
        assert refused(played, "transfer.commit", **args, review=plan["review"]).reason == "changed"
        assert library_bytes(played) == before
        fresh = played("transfer.plan", **args)["plan"]
        assert fresh["review"] != plan["review"] and fresh["posts"][-1]["text"] == "Any news from the quay?"
        assert played("transfer.commit", **args, review=fresh["review"])["done"]["already_applied"] is False


def test_084s_refusals_keep_their_reason(tmp_path):
    with origin(tmp_path) as played:
        chat = str(played.chat)
        before = library_bytes(played)
        assert refused(played, "transfer.scan", chat=chat, target=played.chat.campaign_id,
                       **DIRECTOR).reason == "same_campaign"
        assert refused(played, "transfer.scan", chat=chat, target="no-such-campaign", **DIRECTOR).reason == "missing_scope"
        assert refused(played, "transfer.scan", chat=chat, target="Not A Campaign!", **DIRECTOR).reason == "invalid_field"
        missing = chat.rsplit("/", 1)[0] + "/t-none"
        assert refused(played, "transfer.scan", chat=missing, target=played.storm.campaign_id,
                       **DIRECTOR).reason == "missing_chat"
        scan = scanned(played)
        cast = decisions(scan, **{"Mara Vell": "new", "Anya Rook": "leave"})
        nobody = f"campaign:{played.chat.campaign_id}/character/nobody"
        assert refused(played, "transfer.plan", **request(played, {**cast, nobody: "new"})).reason == "unknown_actor"
        assert refused(played, "transfer.plan", **request(played, cast, sources=["card:nope/lorebook/x@r1"])
                       ).reason == "unknown_source"
        assert refused(played, "transfer.plan", **request(played, {**cast, scan["cast"][0]["actor"]: "keep"})
                       ).reason == "invalid_field"
        assert refused(played, "transfer.plan", **request(played, cast, operation="no spaces")).reason == "invalid_field"
        assert refused(played, "transfer.plan", **request(played, cast, label="x" * 201)).reason == "invalid_field"
        # a player's safety stop stands: a paused scene's history is not copied until it is resumed
        played("pause", chat=chat)
        paused = scanned(played)
        assert [item["kind"] for item in paused["history_problems"]] == ["paused"]
        assert refused(played, "transfer.plan", **request(played, cast)).reason == "history_incomplete"
        played("resume", chat=chat)
        before = library_bytes(played)
        # carrying no source: the lorebook stays out of the copy, as an opt-out
        plan = played("transfer.plan", **request(played, cast, sources=[]))["plan"]
        assert [(item["role"], item["detached"]) for item in plan["bindings"] if item["role"] == "lorebook"] == [
            ("lorebook", True)]
        assert library_bytes(played) == before


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_director_copies_the_chat_on_the_page(tmp_path):
    with origin(tmp_path) as played:
        untouched = origin_files(played)
        result = node_e2e(played, "transfer.e2e.mjs", {"GRIMOIRE_TARGET": played.storm.campaign_id}, NODE)
        assert origin_files(played) == untouched, "the page's copy never writes the origin"
        copies = [path.name for path in (played.table.store.root / "campaigns" / played.storm.campaign_id
                                         / "chats").iterdir() if path.name.startswith("t-copy-")]
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
    assert len(copies) == 1, "one copy, made once"
