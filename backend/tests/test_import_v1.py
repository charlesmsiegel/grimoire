"""Sprint 026 first apply proof: a reviewed V0.2.1 library imported into an isolated V2 library.

One synthetic world with two NPCs (one with an alternate portrayal) and a PC, a campaign with a
short scene, and one V1 picture reused by an avatar and an expression slot. The import is planned
from 011's reviewed manifest only, previewed, approved and applied as one transaction with a
receipt; a second run duplicates nothing; V2 edits and deletions survive re-runs; the V1 source
is never modified. No model is called and nothing is fetched.
"""

from __future__ import annotations

import json
import pytest

from grimoire.chats import NOT_WITNESSED, chat_from_payload, scope_from_payload
from grimoire.core.entity_codec import instance_from_payload
from grimoire.core.identity import ChatRef, ImageRef, InstanceRef, LegacyId, ScopeRef, SourceRef
from grimoire.effective import chat_snapshot, entity_decoder
from grimoire.images import Focus, Hidden, Shown
from grimoire.imagestore import ImageLibrary
from grimoire.import_v1 import (
    ImportRefused, Selection, chat_id_of, image_id_of, legacy_map, plan_import, read_receipts, receipt_source,
)
from grimoire.migrate_v1 import ManifestMismatch, Options, SourceChanged, open_verified
from grimoire.security import Approval, Authorizer, Grant, Permission, ResourcePath, payload_digest
from grimoire.store import RecordEnvelope, SourceManifest, StaleWrite, WriteDenied

from image_fixtures import handle
from import_fixtures import ARCHIVE, CAMPAIGN_ID, CLOCK, MARA_DEFAULT_REF, SALTMARCH, SERAPHINE_REF, WINIFRED_REF, Env
from store_helpers import OWNER
from v1_fixture import CAMPAIGN, RED_ID, SID, tree_digest

MARA = InstanceRef(CAMPAIGN_ID, "character", "mara")
SERAPHINE = InstanceRef(CAMPAIGN_ID, "character", "seraphine")
WINIFRED = InstanceRef(CAMPAIGN_ID, "character", "winifred")
SCENE = f"v1:{ARCHIVE}:{CAMPAIGN}/scenes/{SID}.md"
CHAT = ChatRef(CAMPAIGN_ID, chat_id_of(f"v1:{ARCHIVE}:{CAMPAIGN}/campaign.md", "00000000000000000000000000000001"))
RED_IMAGE = ImageRef(SALTMARCH, image_id_of(RED_ID))
MARA_OLDER = SourceRef(SALTMARCH, "character", "mara.older")
OTHER = SourceRef("card:other", "character", "x").at("r1")


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.store.close()
    assert tree_digest(environment.v1) == environment.v1_before  # the V1 source is never modified


def test_first_apply_imports_world_cast_scene_and_shared_art(env):
    plan = env.plan()
    review = plan.review()
    assert review["provider_calls"] == 0
    before = env.v2()
    preview = plan.preview()
    assert set(review["writes"]) <= set(preview)  # plus blobs, the receipt and its manifest
    assert env.v2() == before  # previewing writes nothing
    result = plan.apply()
    assert set(result.etags) == set(preview)
    view = env.store.load()

    # one definition per V1 portrayal, PC and NPC alike: an individual agent, the V1 text verbatim
    decoded = {ref: entity_decoder(view.index.resolve(ref)) for ref in (MARA_DEFAULT_REF, SERAPHINE_REF, WINIFRED_REF)}
    for ref, definition in decoded.items():
        assert definition.nature == "individual" and [f.name for f in definition.traits] == ["agent"]
        source_path = LegacyId.parse(definition.extras["v1"]["legacy_id"]).path
        assert definition.extras["v1"]["text"] == (env.v1 / source_path).read_text(encoding="utf-8")
    assert decoded[WINIFRED_REF].description == "A debtor."
    # the alternate portrayal is a separate entity joined by an authored link, never a revision
    links = view.sources[SALTMARCH].manifest.links
    assert [(link.relation, link.object.split("@")[0]) for link in links] == [
        ("alternate_portrayal", "world:saltmarch/character/mara.older")]
    # the same-named Mara of another world stays her own entity
    assert view.index.resolve(MARA_DEFAULT_REF.unpinned()).ref.source == SALTMARCH
    assert view.index.check(SourceRef("world:realm", "character", "mara.default")) is None

    # the campaign is a 107 play scope on the imported world, with one actor per appearance
    scope = scope_from_payload(ScopeRef(CAMPAIGN_ID), *_scope(env.store, ScopeRef(CAMPAIGN_ID)))
    assert (scope.title, scope.world.source) == ("The Drowned Ledger", SALTMARCH)
    assert scope.world.observed_manifest_etag == view.sources[SALTMARCH].etag
    for actor, base in ((MARA, MARA_DEFAULT_REF), (SERAPHINE, SERAPHINE_REF), (WINIFRED, WINIFRED_REF)):
        stored = view.instances[actor]
        assert (stored.envelope.derived_from, stored.envelope.base) == (str(base.unpinned()), str(base))
        assert stored.envelope.base_etag == view.index.resolve(base).etag
        assert instance_from_payload(actor, "grimoire.entity/0", stored.envelope.payload).source == base.unpinned()
    controls = {row["ref"]: row["control"] for row in result.review["mapping"]["actors"]}
    assert controls == {str(MARA): "director", str(SERAPHINE): "director", str(WINIFRED): "player"}

    # the scene is a chat with its cast bound and the V1 posts recorded, not a transcript
    captured = chat_snapshot(view, CHAT)
    assert captured.problems == () and captured.scope.world.source == SALTMARCH
    chat = captured.chat
    assert chat.label == "The tide comes in" and chat.launch.ref == MARA_DEFAULT_REF.unpinned()
    roles = {(b.role, b.actor) for b in chat.active_bindings()}
    assert roles == {("character", MARA), ("character", SERAPHINE), ("persona", WINIFRED)}
    assert {entry.prior_history for entry in chat.roster} == {NOT_WITNESSED}
    posts = chat.extras["v1_import"]["posts"]
    assert [(p["post_id"][-2:], p["speaker"]) for p in posts] == [("11", "Winifred"), ("12", "Mara"),
                                                                    ("13", "Seraphine")]

    # one V1 picture is one image object, reused by an avatar and an expression slot
    images = ImageLibrary(env.store, handle(), owner=OWNER)
    default = images.effective_placements(MARA_DEFAULT_REF.unpinned())
    older = images.effective_placements(MARA_OLDER)
    avatar, neutral = default.slot("avatar"), older.slot("expr.casual.neutral")
    assert avatar.image == neutral.image == RED_IMAGE and avatar.focus == Focus(5000, 4000)
    assert images.read_image(RED_IMAGE)[0].provenance["v1_object"] == RED_ID
    assert isinstance(default.slot("gallery_1"), Shown)
    campaign = images.effective_placements(MARA)  # the campaign re-crops the avatar and hides gallery_1
    assert (campaign.slot("avatar").layer, campaign.slot("avatar").focus) == ("campaign_adjusted", Focus(5000, 7000))
    assert campaign.slot("gallery_1") == Hidden("gallery_1")

    # what could not be carried is reported, never guessed
    reasons = {row["reason"] for row in result.review["unresolved"]}
    assert reasons == {"dangling_object"}
    assert {row["family"] for row in result.review["not_imported"]} >= {"lore", "locations", "groups", "greetings"}

    # the receipt maps every V1 file to what it became, and is 002's reviewed LegacyIdMap
    stored = env.store.read(result.receipt)
    mapping = legacy_map(stored.envelope.payload)
    assert mapping.resolve(LegacyId(ARCHIVE, "worlds/saltmarch/characters/seraphine/default.json")) == SERAPHINE_REF
    receipt = stored.envelope.payload
    assert receipt["run"] == 1 and receipt["provider_calls"] == 0 and receipt["applied_at"] == CLOCK
    assert set(receipt["written"]) == set(result.review["writes"])
    assert receipt["mapping"]["scenes"][0]["chat"] == str(CHAT)


def _scope(store, ref):
    stored = store.read(ref)
    return stored.record.schema, stored.record.payload


def test_a_second_run_writes_nothing_and_duplicates_nothing(env):
    env.plan().apply()
    after = env.v2()
    again = env.plan(clock="2026-12-01T00:00:00Z")  # a later clock changes no byte
    assert again.review()["writes"] == [] and again.preview() == {}
    assert set(again.review()["outcomes"].values()) == {"present"}
    assert again.apply().etags == {}
    assert env.v2() == after
    assert read_receipts(env.store, ARCHIVE).run == 1


def test_v2_edits_are_kept_deletions_stay_deleted_and_new_selections_add_only(env):
    env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID})).apply()
    edited = env.store.read(SERAPHINE)
    txn = env.store.transaction()  # a V2 edit of an imported actor
    txn.put(RecordEnvelope(SERAPHINE, {**edited.envelope.payload, "local_name": "Sera"},
                           derived_from=edited.envelope.derived_from, base=edited.envelope.base,
                           base_etag=edited.envelope.base_etag, owner=OWNER, extra=edited.envelope.extra), edited.etag)
    txn.commit(handle())
    hidden = env.store.root / "campaigns" / CAMPAIGN_ID / "placements" / "character" / "mara" / "gallery_1.json"
    hidden.unlink()  # a V2 deletion by hand
    before = env.v2()

    plan = env.plan()  # now with Realm too
    review = plan.review()
    outcomes = review["outcomes"]
    assert outcomes["campaigns/the-drowned-ledger/instances/character/seraphine.json"] == "kept"
    assert outcomes["campaigns/the-drowned-ledger/placements/character/mara/gallery_1.json"] == "removed"
    assert all(path.startswith(("sources/world/realm/", "sources/import/")) for path in review["writes"])
    plan.apply()
    after = env.v2()
    receipt_head = f"sources/import/{ARCHIVE}/_source/manifest.json"  # only the receipt head advances
    assert {key: value for key, value in after.items() if key in before and key != receipt_head} == \
        {key: value for key, value in before.items() if key != receipt_head}
    assert not hidden.exists()  # and nothing recreated
    assert env.store.read(SERAPHINE).envelope.payload["local_name"] == "Sera"
    receipts = read_receipts(env.store, ARCHIVE)
    assert receipts.run == 2
    assert "campaigns/the-drowned-ledger/scope.json" in receipts.written  # cumulative across runs


def test_an_untrusted_handle_needs_a_grant_and_an_approval_of_every_target(env):
    authority = Authorizer([OWNER])
    for number, scope in enumerate(("sources", "campaigns", "assets")):
        authority.add_grant(Grant(f"g{number}", "app:import", frozenset({Permission.APPLY}),
                                  ResourcePath.parse(scope), OWNER))
    worker = authority.handle_for("app:import", trusted=False)
    plan = env.plan(grant=worker)
    before = env.v2()
    with pytest.raises(WriteDenied):
        plan.apply()
    assert env.v2() == before
    approvals = {}
    for number, (address, data) in enumerate(sorted(plan.preview().items())):
        authority.register_approval(Approval(f"a{number}", "app:import", Permission.APPLY,
                                             ResourcePath.parse(address), payload_digest(data), OWNER))
        approvals[address] = f"a{number}"
    assert set(plan.apply(approvals).etags) == set(plan.preview())


def test_the_source_must_still_be_the_reviewed_one(env, tmp_path):
    before = env.v2()

    def refused(manifest):
        with pytest.raises(ManifestMismatch) as caught:
            env.plan(manifest=manifest)
        return caught.value.reason

    edited = json.loads(json.dumps(env.manifest))
    edited["actors"][0]["name"] = "Someone else"
    assert refused(edited) == "manifest_changed"
    assert refused({**env.manifest, "status": "blocked"}) == "blocked"
    assert refused({**env.manifest, "schema": "something/else"}) == "not_an_inventory"
    assert refused({**env.manifest, "roots": {**env.manifest["roots"], "destination": str(tmp_path / "x")}}) \
        == "destination_mismatch"
    (env.v1 / "worlds/saltmarch/lore/the-salt-pact.md").write_text("changed", encoding="utf-8")
    assert refused(env.manifest) == "source_changed"
    env.v1_before = tree_digest(env.v1)
    assert env.v2() == before  # a refused plan writes nothing


def test_a_source_that_changes_while_it_is_read_is_refused(env):
    def meddle():
        (env.v1 / "worlds/saltmarch/world.md").write_text("changed", encoding="utf-8")

    with pytest.raises(SourceChanged):
        plan_import(env.store, env.v1, env.manifest, handle=handle(), owner=OWNER, clock=lambda: CLOCK,
                    home=env.home, environ={}, _options=Options(after_first_pass=meddle))
    env.v1_before = tree_digest(env.v1)


def test_verified_reads_are_closed_with_the_source(env):
    with open_verified(env.v1, env.manifest, destination=env.store.root, home=env.home, environ={}) as source:
        assert source.read("worlds/saltmarch/world.md").startswith(b"---\n")
    with pytest.raises(ValueError):
        source.read("worlds/saltmarch/world.md")


def test_targets_this_archive_did_not_write_are_unresolved_with_their_dependents(env):
    txn = env.store.transaction()
    txn.put(SourceManifest(SALTMARCH, OWNER, "Someone's Saltmarch"), None)
    txn.commit(handle())
    review = env.plan().review()
    unresolved = {(row["reason"], row.get("detail")) for row in review["unresolved"]}
    assert ("source_exists", SALTMARCH) in unresolved
    assert ("campaign_world_not_imported", SALTMARCH) in unresolved
    assert not any(path.startswith(("sources/world/saltmarch/", "campaigns/")) for path in review["writes"])
    # mapped elsewhere, the same world imports beside the existing source
    plan = env.plan(Selection({"saltmarch": "world:saltmarch-v1"}, {"the-drowned-ledger": CAMPAIGN_ID}))
    assert any(path.startswith("sources/world/saltmarch-v1/") for path in plan.review()["writes"])
    plan.apply()


def test_an_existing_campaign_is_never_merged_into(env):
    txn = env.store.transaction()
    txn.put(SourceManifest("card:other", OWNER), None)
    txn.put(RecordEnvelope(OTHER, {}), None)
    txn.put(RecordEnvelope(InstanceRef(CAMPAIGN_ID, "character", "someone"), {}, derived_from=str(OTHER.unpinned())),
            None)
    txn.commit(handle())
    review = env.plan().review()
    assert ("campaign_exists", CAMPAIGN_ID) in {(row["reason"], row.get("detail")) for row in review["unresolved"]}
    assert not any(path.startswith("campaigns/") for path in review["writes"])
    renamed = env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": "drowned-v1"}))
    assert "campaigns/drowned-v1/scope.json" in renamed.review()["writes"]


def test_a_declined_portrayal_takes_its_actor_art_and_bindings_with_it(env):
    skip = frozenset({f"v1:{ARCHIVE}:worlds/saltmarch/characters/seraphine/default.json"})
    plan = env.plan(Selection({"saltmarch": SALTMARCH}, {"the-drowned-ledger": CAMPAIGN_ID}, skip))
    review = plan.review()
    assert review["skipped"] == sorted(skip)
    assert not any("seraphine" in path for path in review["writes"])
    plan.apply()
    chat = chat_from_payload(CHAT, *_scope(env.store, CHAT))
    assert {binding.actor for binding in chat.active_bindings()} == {MARA, WINIFRED}


def test_a_plan_is_refused_when_the_library_moved_under_it(env):
    plan = env.plan()
    txn = env.store.transaction()  # someone starts the campaign id meanwhile
    txn.put(SourceManifest("card:other", OWNER), None)
    txn.put(RecordEnvelope(OTHER, {}), None)
    txn.put(RecordEnvelope(InstanceRef(CAMPAIGN_ID, "character", "someone"), {}, derived_from=str(OTHER.unpinned())),
            None)
    txn.commit(handle())
    before = env.v2()
    with pytest.raises(StaleWrite):
        plan.apply()
    assert env.v2() == before


def test_a_selection_must_name_what_the_manifest_holds(env):
    with pytest.raises(ImportRefused) as caught:
        env.plan(Selection({"nowhere": "world:nowhere"}))
    assert caught.value.reason == "unknown_world"
    with pytest.raises(ImportRefused) as caught:
        env.plan(Selection({"saltmarch": receipt_source(ARCHIVE)}))
    assert caught.value.reason == "reserved_source"
    with pytest.raises(ImportRefused) as caught:
        env.plan(Selection({"saltmarch": "world:x", "realm": "world:x"}))
    assert caught.value.reason == "duplicate_target"
