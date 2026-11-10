"""Sprint 110's review API: exact-bytes proposals over the real 037, 036, 011/026, 042 and 043 services.

Proving scenario: preview and approve a synthetic 0.2.1 library import and a separate V3 card; rerunning
each never duplicates records or changes the source. Refusals: changed staged bytes and a moved library
invalidate an approval, a rejected change set leaves canonical facts as they were, and imported text creates
no accepted event or actor memory. Every fixture is synthetic; no model is called.
"""

from __future__ import annotations

import json
import shutil

import pytest

from card_fixtures import PORTRAIT, V3, card, encode
from changeset_fixtures import MANIFEST, move_the_guild, seed_world
from chat_fixtures import Clock, seed_sources
from import_fixtures import ARCHIVE, build_two_npc_library
from ledger_helpers import fixed_clock
from rebase_fixtures import WORLD, envelope, put, wren
from review_fixtures import Desk, b64, secured
from store_helpers import OWNER, owner_handle
from test_archaeologystore_scenarios import export, mapping
from v1_fixture import tree_digest

from grimoire.archaeologystore import scan_st_export
from grimoire.changesets import encode as encode_changeset
from grimoire.changesetstore import ChangeSetLibrary
from grimoire.chats import Launch
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import SourceRef
from grimoire.ledgers import DurableAuthority
from grimoire.reviewapi import ReviewRefused, preview_digest
from grimoire.security import Grant, Permission, ResourcePath
from grimoire.store import LibraryStore, SourceManifest
from grimoire.testing.fileops import snapshot

CARD = encode(V3)


def files_of(folder) -> dict:
    """The library's canonical files (the authority's own ledgers aside)."""
    return {path: data for path, data in snapshot(folder).items()
            if not path.startswith((".", "authority/"))}


def apply_checked(desk, proposal) -> dict:
    """Apply as shown, and check that every file now holds exactly the bytes the proposal showed."""
    shown = desk.api.proposals.get(proposal["proposal"]).preview()
    applied = desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"])
    now = snapshot(desk.folder)
    for path, data in shown.items():
        assert now.get(path) == data, path
    return applied


def refused(call) -> ReviewRefused:
    with pytest.raises(ReviewRefused) as caught:
        call()
    return caught.value


@pytest.fixture
def desk(tmp_path):
    with Desk(secured(tmp_path / "lib")) as opened:
        yield opened


# -- the proving scenario ---------------------------------------------------------------------------

def test_proving_scenario_a_v1_library_and_a_separate_card_are_previewed_approved_and_never_duplicated(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    v1 = build_two_npc_library(tmp_path / "v1")
    v1_before = tree_digest(v1)
    with Desk(secured(tmp_path / "lib"), home=home) as desk:
        scanned = desk("v1.scan", source=str(v1), archive_id=ARCHIVE)
        assert scanned["status"] == "ready_for_review" and scanned["provider_calls"] == 0
        assert {row["id"] for row in scanned["worlds"]} == {"realm", "saltmarch"}
        assert scanned["proposed_selection"]["worlds"] == {"realm": "world:realm", "saltmarch": "world:saltmarch"}
        assert scanned["identities"]["total"] > 0

        proposal = desk("v1.preview", scan=scanned["scan"])
        assert proposal["kind"] == "v1" and proposal["writes"] > 0
        assert proposal["report"]["provider_calls"] == 0
        assert all(row["action"] in ("create", "write") for row in proposal["files"])
        before = files_of(desk.folder)
        assert not any(path.startswith("sources/world/") for path in before)  # a preview writes nothing

        applied = apply_checked(desk, proposal)
        assert applied["state"] == "applied" and applied["result"]["receipt"].startswith("import:")
        after = files_of(desk.folder)
        assert {row["path"] for row in proposal["files"]} <= set(after)
        assert desk("v1.receipts", archive_id=ARCHIVE)["runs"] == 1

        again = desk("v1.preview", scan=desk("v1.scan", source=str(v1), archive_id=ARCHIVE)["scan"])
        assert again["writes"] == 0 and again["files"] == []
        apply_checked(desk, again)
        assert files_of(desk.folder) == after  # the rerun duplicated nothing

        card_proposal = desk("card.preview", card=b64(CARD), origin="mara.json", portrait=b64(PORTRAIT))
        report = card_proposal["report"]
        assert report["status"] == "new" and report["accepted_events"] == []
        assert {piece["role"] for piece in report["pieces"]} >= {"authored_description", "opening_greeting"}
        apply_checked(desk, card_proposal)
        with_card = files_of(desk.folder)
        assert any(path.startswith("sources/card/mara-vell/") for path in with_card)

        rerun = desk("card.preview", card=b64(CARD), origin="mara.json", portrait=b64(PORTRAIT))
        assert rerun["report"]["status"] == "unchanged" and rerun["files"] == []
        apply_checked(desk, rerun)
        assert files_of(desk.folder) == with_card
        assert not any(path.startswith("sources/card/mara-vell-2") for path in with_card)
    assert tree_digest(v1) == v1_before  # the V1 source was only read


# -- approvals bind exact bytes ---------------------------------------------------------------------

def test_an_approval_of_other_bytes_writes_nothing(desk):
    proposal = desk("card.preview", card=b64(CARD))
    before = files_of(desk.folder)
    other = preview_digest("card", {"sources/x": b"other bytes"})
    error = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=other))
    assert error.reason == "stale_approval" and error.status == 409
    assert files_of(desk.folder) == before
    assert desk("proposal.get", proposal=proposal["proposal"])["state"] == "open"  # still approvable as shown


def test_the_digest_covers_every_file_and_its_bytes(desk):
    proposal = desk("card.preview", card=b64(CARD), portrait=b64(PORTRAIT))
    shown = desk.api.proposals.get(proposal["proposal"]).preview()
    assert preview_digest("card", shown) == proposal["digest"]
    changed = dict(shown)
    path = min(changed)
    changed[path] = changed[path] + b" "
    assert preview_digest("card", changed) != proposal["digest"]
    assert preview_digest("card", {k: v for k, v in shown.items() if k != path}) != proposal["digest"]
    assert preview_digest("v1", shown) != proposal["digest"]
    for row in proposal["files"]:
        assert row["size"] == len(shown[row["path"]])


def test_a_library_that_moved_since_the_preview_refuses_the_apply_and_writes_nothing(desk):
    first = desk("card.preview", card=b64(CARD))
    second = desk("card.preview", card=b64(CARD))  # the same destination, planned before the first landed
    apply_checked(desk, first)
    landed = files_of(desk.folder)
    error = refused(lambda: desk("proposal.apply", proposal=second["proposal"], digest=second["digest"]))
    assert error.reason == "stale" and error.details
    assert files_of(desk.folder) == landed
    assert refused(lambda: desk("proposal.get", proposal=second["proposal"])).reason == "no_proposal"


def test_a_proposal_is_applied_once(desk):
    proposal = desk("card.preview", card=b64(CARD))
    apply_checked(desk, proposal)
    error = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"]))
    assert error.reason == "no_proposal" and error.status == 404


def test_only_a_bounded_few_proposals_are_held(desk):
    made = [desk("card.preview", card=b64(encode(card(name=f"Card {n}")))) for n in range(10)]
    assert len(desk.api.proposals) == 8
    assert refused(lambda: desk("proposal.get", proposal=made[0]["proposal"])).reason == "no_proposal"
    assert desk("proposal.get", proposal=made[-1]["proposal"])["state"] == "open"


# -- what imports report and refuse ------------------------------------------------------------------

@pytest.mark.parametrize("data, detail", [(b"\x89PNG\r\n\x1a\nrest", "png"), (b"PK\x03\x04rest", "charx"),
                                          (encode({"spec": "chara_card_v2", "data": {"name": "Old"}}),
                                           "chara_card_v2")])
def test_unsupported_card_formats_are_refused_by_name_before_anything_is_staged(desk, data, detail):
    before = files_of(desk.folder)
    error = refused(lambda: desk("card.preview", card=b64(data)))
    assert error.reason == "unsupported_format" and error.status == 400 and error.details["detail"] == detail
    assert files_of(desk.folder) == before and len(desk.api.proposals) == 0


def test_a_card_without_its_pictures_reports_the_missing_art_and_its_losses(desk):
    proposal = desk("card.preview", card=b64(CARD))  # its main icon is ccdefault: with no portrait given
    report = proposal["report"]
    slots = {row["slot"]: row for row in report["art"]}
    assert slots["avatar"]["unresolved"] and slots["background"]["unresolved"] == "not_fetched"
    assert {loss["reason"] for loss in report["losses"]} >= {"not_fetched", "unavailable"}


def test_malformed_arguments_are_refused(desk):
    assert refused(lambda: desk("card.preview", card="not base64!")).reason == "invalid_field"
    assert refused(lambda: desk("card.preview", card=b64(CARD), as_scenario="yes")).reason == "invalid_field"
    assert refused(lambda: desk("nope")).reason == "unknown_op"
    assert refused(lambda: desk("v1.preview", scan="scan-404")).reason == "no_scan"


def test_imported_history_is_previewed_and_creates_no_memory_or_event(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_sources)) as desk:
        data = export()
        scanned = desk("history.scan", export=b64(data))
        assert scanned["counts"]["posts"] > 0
        speakers = {sid: str(ref) for sid, ref in mapping(scan_st_export(data)).items()}
        proposal = desk("history.preview", export=b64(data), speakers=speakers, name="tide.jsonl")
        assert proposal["report"]["status"] == "new" and proposal["report"]["provider_calls"] == 0
        report = proposal["report"]
        assert report["staged"].get("transcript") and report["staged"].get("chat") == 1
        assert report["accepted_events"] == [] and report["memories"] == [] and report["turns"] == []
        applied = apply_checked(desk, proposal)
        written = files_of(desk.folder)
        made = [path for path in written if path.startswith("campaigns/")]
        assert made and not any(f"/{folder}/" in path for path in made
                                for folder in ("accepted", "wrapups", "turns", "knowledge", "evidence", "events"))
        again = desk("history.preview", export=b64(data), speakers=speakers, name="tide.jsonl")
        assert again["report"]["status"] == "already_imported" and again["files"] == []
        assert applied["result"]["chat"] == again["report"]["chat"]


def test_a_v1_source_changed_after_its_scan_is_refused_at_preview(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    v1 = build_two_npc_library(tmp_path / "v1")
    with Desk(secured(tmp_path / "lib"), home=home) as desk:
        scanned = desk("v1.scan", source=str(v1), archive_id=ARCHIVE)
        world = v1 / "worlds" / "saltmarch" / "world.md"
        world.write_bytes(world.read_bytes() + b"\nedited after the scan\n")
        error = refused(lambda: desk("v1.preview", scan=scanned["scan"]))
        assert error.reason == "source_changed"
        assert not any(path.startswith("sources/world/") for path in files_of(desk.folder))


def test_a_v1_scan_refuses_a_folder_that_is_no_v1_library_and_the_v2_library_itself(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "empty").mkdir()
    with Desk(secured(tmp_path / "lib"), home=home) as desk:
        assert refused(lambda: desk("v1.scan", source=str(tmp_path / "empty"),
                                    archive_id=ARCHIVE)).reason == "not_a_v1_library"
        assert refused(lambda: desk("v1.scan", source=str(desk.folder),
                                    archive_id=ARCHIVE)).reason == "unsafe_paths"


def test_a_v1_selection_can_leave_a_world_out(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    v1 = build_two_npc_library(tmp_path / "v1")
    with Desk(secured(tmp_path / "lib"), home=home) as desk:
        scanned = desk("v1.scan", source=str(v1), archive_id=ARCHIVE)
        proposal = desk("v1.preview", scan=scanned["scan"], selection={"worlds": {"realm": "world:realm"}})
        assert not any(row["path"].startswith("sources/world/saltmarch") for row in proposal["files"])
        bad = refused(lambda: desk("v1.preview", scan=scanned["scan"], selection={"worlds": {"realm": 3}}))
        assert bad.reason == "invalid_field"


# -- change sets ------------------------------------------------------------------------------------

def _changeset(store) -> dict:
    return encode_changeset(move_the_guild(ChangeSetLibrary(store, owner_handle())))


def test_a_rejected_change_set_leaves_the_world_as_it_was(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_world)) as desk:
        before = files_of(desk.folder)
        proposal = desk("changeset.review", changeset=_changeset(desk.store))
        report = proposal["report"]
        assert report["applicable"] and report["conflicts"] == []
        assert any(row["diff"] for row in proposal["files"])  # the revised guild and the manifest read as diffs
        assert desk("proposal.reject", proposal=proposal["proposal"])["state"] == "rejected"
        assert files_of(desk.folder) == before
        assert refused(lambda: desk("proposal.apply", proposal=proposal["proposal"],
                                    digest=proposal["digest"])).reason == "no_proposal"


def test_an_approved_change_set_writes_exactly_its_files(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_world)) as desk:
        proposal = desk("changeset.review", changeset=_changeset(desk.store))
        shown = desk.api.proposals.get(proposal["proposal"]).preview()
        applied = apply_checked(desk, proposal)
        assert applied["result"]["changeset"] == "cs-1"
        after = files_of(desk.folder)
        for path, data in shown.items():
            assert after[path] == data


def test_a_change_set_over_a_world_edited_since_review_is_refused(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_world)) as desk:
        proposal = desk("changeset.review", changeset=_changeset(desk.store))
        manifest = desk.folder / MANIFEST
        document = json.loads(manifest.read_text(encoding="utf-8"))
        document["title"] = "Harbor (edited)"
        manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        edited = files_of(desk.folder)
        error = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"]))
        assert error.reason in ("conflict", "stale")
        assert files_of(desk.folder) == edited


def test_a_change_set_document_with_unknown_fields_is_refused(tmp_path):
    with Desk(secured(tmp_path / "lib", seed_world)) as desk:
        document = {**_changeset(desk.store), "sneaky": True}
        assert refused(lambda: desk("changeset.review", changeset=document)).reason == "invalid_field"


# -- rebase -----------------------------------------------------------------------------------------

def _wren_chat(store):
    put(store, SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r1"}), envelope(wren()))
    chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=lambda prefix: f"{prefix}-1", owner=OWNER)
    started = chats.start_chat(Launch("character", SourceRef(WORLD, "character", "wren"))).commit()
    _STARTED.append(started)


_STARTED: list = []


def test_a_rebase_is_reviewed_decided_and_applied_as_one_proposal(tmp_path):
    with Desk(secured(tmp_path / "lib", _wren_chat)) as desk:
        chat_ref = _STARTED.pop().chat
        r2 = wren(SourceRef(WORLD, "character", "wren", "r2"), name="Wren Tide")
        txn = desk.store.transaction()
        txn.put(envelope(r2), None)
        txn.put(SourceManifest(WORLD, OWNER, "Harbor", {"character/wren": "r2"}), desk.store.check_source(WORLD).etag)
        txn.commit(desk.api.handle)
        candidates = desk("rebase.candidates", chat=str(chat_ref))
        [row] = candidates["actors"]
        assert row["behind"] and row["head"].endswith("@r2")
        review = desk("rebase.review", actor=row["actor"])
        assert "name" in review["decisions_needed"]
        undecided = refused(lambda: desk("rebase.preview", review=review["review"], decisions={}))
        assert undecided.reason == "undecided"
        proposal = desk("rebase.preview", review=review["review"],
                        decisions={name: "take" for name in review["decisions_needed"]})
        applied = apply_checked(desk, proposal)
        assert applied["result"]["upstream"].endswith("@r2")
        assert not desk("rebase.candidates", chat=str(chat_ref))["actors"][0]["behind"]


def test_capabilities_name_what_is_unavailable_and_why(desk):
    found = desk("review.capabilities")
    unavailable = {row["feature"]: row for row in found["unavailable"]}
    assert "png_and_charx_cards" in unavailable and unavailable["png_and_charx_cards"]["blocked_by"]
    assert set(found["ops"]) == set(desk.api.ops)


def test_the_v1_library_is_never_written_even_when_the_import_is_applied_twice(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    v1 = build_two_npc_library(tmp_path / "v1")
    copy = shutil.copytree(v1, tmp_path / "v1-copy")
    with Desk(secured(tmp_path / "lib"), home=home) as desk:
        for _ in range(2):
            proposal = desk("v1.preview", scan=desk("v1.scan", source=str(v1), archive_id=ARCHIVE)["scan"])
            apply_checked(desk, proposal)
    assert tree_digest(v1) == tree_digest(copy)


def test_an_owner_without_a_grant_for_the_destination_is_refused_and_nothing_is_staged(tmp_path):
    folder = tmp_path / "lib"
    LibraryStore.create(folder).close()
    with LibraryStore.open(folder) as store:  # an authority opened, but no grant made for anyone
        DurableAuthority.open(store, [OWNER], clock=fixed_clock()).close()
    with Desk(folder) as desk:
        denied = refused(lambda: desk("card.preview", card=b64(CARD), origin="mara.json"))
        assert denied.reason == "denied" and denied.details
        assert len(desk.api.proposals) == 0


def test_a_portrait_that_is_no_picture_is_refused_by_name(desk):
    error = refused(lambda: desk("card.preview", card=b64(CARD), portrait=b64(b"not a picture")))
    assert error.reason == "invalid_portrait" and error.status == 400 and error.details == {"detail": "portrait"}


def test_a_v1_folder_that_is_missing_or_gone_since_its_scan_is_refused(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    with Desk(secured(tmp_path / "lib"), home=home) as desk:
        missing = refused(lambda: desk("v1.scan", source=str(tmp_path / "nowhere"), archive_id=ARCHIVE))
        assert missing.reason == "no_source" and missing.status == 404
        v1 = build_two_npc_library(tmp_path / "v1")
        scanned = desk("v1.scan", source=str(v1), archive_id=ARCHIVE)
        shutil.rmtree(v1)
        gone = refused(lambda: desk("v1.preview", scan=scanned["scan"]))
        assert gone.reason in ("no_source", "not_a_v1_library", "source_changed")
        assert refused(lambda: desk("v1.receipts", archive_id="A/B")).reason == "invalid_field"


def test_a_denied_apply_writes_nothing_and_stays_open_to_retry(desk):
    proposal = desk("card.preview", card=b64(CARD))
    before = files_of(desk.folder)
    desk.authority.revoke_grant("owner-sources")
    denied = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"]))
    assert denied.reason == "denied" and files_of(desk.folder) == before
    assert desk("proposal.get", proposal=proposal["proposal"])["state"] == "open"
    desk.authority.add_grant(Grant("owner-sources-again", OWNER, frozenset({Permission.APPLY}),
                                   ResourcePath.parse("sources"), OWNER))
    assert apply_checked(desk, proposal)["state"] == "applied"


def test_a_rejected_card_import_writes_nothing(desk):
    before = files_of(desk.folder)
    proposal = desk("card.preview", card=b64(CARD), portrait=b64(PORTRAIT))
    assert desk("proposal.reject", proposal=proposal["proposal"])["state"] == "rejected"
    assert files_of(desk.folder) == before
    gone = refused(lambda: desk("proposal.apply", proposal=proposal["proposal"], digest=proposal["digest"]))
    assert gone.reason == "no_proposal"


def test_a_preview_records_one_authority_decision_per_staged_path(desk):
    before = len(desk.authority.audit())
    proposal = desk("card.preview", card=b64(CARD), portrait=b64(PORTRAIT))
    assert len(desk.authority.audit()) - before == proposal["writes"]
