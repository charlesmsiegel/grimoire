"""SillyTavern chat import into a world-less 107 chat, end to end on the real store (Sprint 036 proving scenario),
and V0.2.1 scene transcripts carried by 026's import.

Every fixture is synthetic under ``tmp_path``; no real SillyTavern or V1 library is read. No model is called:
nothing here imports the provider package.
"""

from __future__ import annotations

import json
import sys

import pytest

from grimoire.archaeologystore import IMPORTED_PREMISE, ImportRefused, StImporter, read_transcript
from grimoire.chats import Launch, chat_from_payload
from grimoire.chatstore import ChatLibrary
from grimoire.core.identity import ChatRef, SourceRef
from grimoire.import_v1 import chat_id_of
from grimoire.migrate_v1 import parse_frontmatter, parse_posts
from grimoire.store import LibraryStore, StaleWrite
from grimoire.store.jsonfmt import MAX_FILE_BYTES
from grimoire.store.layout import ledger_folder
from grimoire.sync import identify, sync
from grimoire.testing.fileops import snapshot

from chat_fixtures import ANYA, LORE, MARA, NAMESAKE, SAM, Clock, Ids, seed_sources
from import_fixtures import ARCHIVE, CAMPAIGN_ID, Env
from store_helpers import OWNER, owner_handle
from v1_fixture import CAMPAIGN, SID

HEADER = {"user_name": "Sam", "character_name": "unused", "create_date": "2026-01-02@10h00m00s",
          "chat_metadata": {"note_prompt": "Keep it tense.", "world_info": "Harbor notes", "integrity": "abc"}}
GROUP = [
    HEADER,
    {"name": "Mara", "is_user": False, "send_date": "January 2, 2026 10:00am", "mes": "Welcome aboard.",
     "swipes": ["Who goes there?", "Welcome aboard."], "swipe_id": 1,
     "swipe_info": [{"send_date": "a"}, {"send_date": "b"}], "original_avatar": "mara.png", "extra": {}},
    {"name": "Mara", "is_user": False, "mes": "I'm Mara too, from the other ship.", "original_avatar": "mara-other.png"},
    {"name": "Sam", "is_user": True, "mes": "Hello, both of you.", "force_avatar": "User Avatars/sam.png"},
    {"name": "Mara", "is_user": False, "mes": "The tide turns at dusk (edited).",
     "swipes": ["The tide turns at dusk.", "We sail at dawn."], "swipe_id": 0, "original_avatar": "mara.png"},
    {"name": "Sam", "is_user": True, "mes": "*whispers to Mara* The key is under the boat.",
     "force_avatar": "User Avatars/sam.png"},
    {"name": "Mara", "is_user": False, "is_system": True, "mes": "(a hidden aside)", "original_avatar": "mara-other.png"},
    {"name": "SillyTavern System", "is_user": False, "mes": "Fog rolls in.", "extra": {"type": "narrator"}},
]


def export(rows=GROUP) -> bytes:
    return ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode("utf-8")


@pytest.fixture
def store(tmp_path):
    with LibraryStore.create(tmp_path / "lib") as opened:
        seed_sources(opened)
        yield opened


def importer(store) -> StImporter:
    return StImporter(store, owner_handle(), clock=Clock(), owner=OWNER)


def ids(scan) -> dict[str, str]:
    return {speaker.avatar: speaker.id for speaker in scan.speakers}


def mapping(scan) -> dict[str, SourceRef]:
    found = ids(scan)
    return {found["mara.png"]: MARA, found["mara-other.png"]: NAMESAKE, found["User Avatars/sam.png"]: SAM}


def campaign_files(store) -> list[str]:
    return sorted(path for path in snapshot(store.root) if path.startswith("campaigns/") and path.endswith(".json"))


# -- the proving scenario -------------------------------------------------------------------------

def test_proving_scenario_group_chat_with_two_maras_a_persona_swipes_edits_and_a_private_line(tmp_path):
    source = tmp_path / "group.jsonl"
    source.write_bytes(export())
    data = source.read_bytes()
    root = tmp_path / "lib"
    with LibraryStore.create(root) as store:
        seed_sources(store)
        scan = importer(store).scan(data)
        assert scan.counts == {"posts": 7, "variants": 10, "unchosen_variants": 3, "hidden": 1, "speakers": 4}
        assert sorted(s.label for s in scan.speakers if s.role == "character") == ["Mara", "Mara"]
        assert campaign_files(store) == []  # scanning writes nothing
        plan = importer(store).plan(data, speakers=mapping(scan), lorebooks=[LORE], name="group.jsonl")
        assert plan.status == "new" and plan.provider_calls == 0
        reasons = {(loss.path, loss.reason) for loss in plan.losses}
        assert ("name", "same_label_speakers") in reasons and ("mes", "edited_text_differs") in reasons
        assert ("speaker", "unmapped_speaker") in reasons  # the narrator stays a label
        preview = plan.preview()
        assert campaign_files(store) == []  # previewing writes nothing
        result = plan.commit()
        assert set(result.etags) == set(preview)
    assert source.read_bytes() == data  # the export is never touched

    with LibraryStore.open(root) as store:  # save, close, reopen
        chats = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER)
        view = chats.open_chat(plan.chat)
        assert view.scope.world is None and view.problems == () and view.broken == ()
        assert view.record.launch == Launch("character", MARA)
        assert view.record.persona.ref == SAM
        assert {(b.role, b.ref) for b in view.record.active_bindings()} == {
            ("character", MARA), ("character", NAMESAKE), ("persona", SAM), ("lorebook", LORE)}
        assert {entry.prior_history for entry in view.record.roster} == {"not_witnessed"}
        pages, problems = read_transcript(store, plan.chat)
        assert problems == () and len(pages) == 1

        posts = pages[0].posts
        # every source message, in order, with the exact text SillyTavern showed
        assert [p.text for p in posts] == [row["mes"] for row in GROUP[1:]]
        # two same-named characters from different cards are two actors, never merged by name
        maras = {p.speaker.actor for p in posts if p.speaker.label == "Mara"}
        assert maras == {plan.actors[ids(scan)["mara.png"]], plan.actors[ids(scan)["mara-other.png"]]}
        assert len(maras) == 2
        assert posts[2].speaker.actor == plan.actors[ids(scan)["User Avatars/sam.png"]]
        assert posts[6].speaker.actor is None and posts[6].speaker.role == "narrator"
        # swipes and the edit: every variant is recoverable, the shown one is active, the rest are history
        assert [v.text for v in posts[0].variants] == GROUP[1]["swipes"] and posts[0].active == 1
        assert [v.text for v in posts[3].variants] == [*GROUP[4]["swipes"], GROUP[4]["mes"]]
        assert [v.text for v in posts[3].unchosen] == GROUP[4]["swipes"]
        # the private line: no audience is inferred, and the hidden aside keeps its flag
        assert all(p.audience == "unknown" for p in posts)
        assert posts[5].hidden and not posts[4].hidden
        # the author's note and chat lorebook name are kept verbatim, not bound or obeyed
        assert pages[0].header["chat_metadata"] == HEADER["chat_metadata"]
        assert pages[0].origin["sha256"] and pages[0].origin["name"] == "group.jsonl"

        # nothing but the scope, chat, actors and transcript: no events, evidence, relations or beliefs
        families = {path.split("/")[2] for path in campaign_files(store)}
        assert families == {"scope.json", "chats", "instances", "transcripts"}
        view = store.load()
        assert view.problems == ()


def test_a_repeat_of_the_same_export_is_already_imported_and_writes_nothing(store):
    data = export()
    scan = importer(store).scan(data)
    first = importer(store).plan(data, speakers=mapping(scan))
    first.commit()
    before = snapshot(store.root)
    again = importer(store).plan(data, speakers=mapping(scan))
    assert again.status == "already_imported" and again.change is None and again.preview() == {}
    assert again.commit() is None and again.chat == first.chat and again.actors == first.actors
    assert snapshot(store.root) == before


def test_a_repeat_with_another_mapping_is_refused(store):
    data = export()
    scan = importer(store).scan(data)
    importer(store).plan(data, speakers=mapping(scan)).commit()
    other = {**mapping(scan), ids(scan)["mara-other.png"]: ANYA}
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data, speakers=other)
    assert caught.value.reason == "imported_differently"


def test_two_plans_of_one_export_commit_once(store):
    data = export()
    scan = importer(store).scan(data)
    first, second = (importer(store).plan(data, speakers=mapping(scan)) for _ in range(2))
    first.commit()
    with pytest.raises(StaleWrite):
        second.commit()


def test_an_unmapped_chat_launches_from_a_premise_with_label_only_speakers(store):
    data = export()
    plan = importer(store).plan(data)
    plan.commit()
    view = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER).open_chat(plan.chat)
    assert view.record.launch == Launch("premise", premise=IMPORTED_PREMISE) and view.record.roster == ()
    pages, _ = read_transcript(store, plan.chat)
    assert all(p.speaker.actor is None for p in pages[0].posts)
    assert sum(loss.reason == "unmapped_speaker" for loss in plan.losses) == 4


def test_a_lorebook_alone_launches_the_chat(store):
    plan = importer(store).plan(export(), lorebooks=[LORE])
    plan.commit()
    view = ChatLibrary(store, owner_handle(), clock=Clock(), new_id=Ids(), owner=OWNER).open_chat(plan.chat)
    assert view.record.launch == Launch("lorebook", LORE)


@pytest.mark.parametrize("bad,reason", [
    ("unknown", "unknown_speaker"),
    ("narrator", "role_kind"),
    ("lorebook", "role_kind"),
])
def test_a_mapping_that_cannot_hold_is_refused(store, bad, reason):
    data = export()
    scan = importer(store).scan(data)
    narrator = next(s.id for s in scan.speakers if s.role == "narrator")
    speakers = {"unknown": {"s-000000000000": MARA}, "narrator": {narrator: MARA},
                "lorebook": {ids(scan)["mara.png"]: LORE}}[bad]
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data, speakers=speakers)
    assert caught.value.reason == reason
    assert campaign_files(store) == []


def test_one_definition_cannot_be_both_persona_and_character(store):
    data = export()
    scan = importer(store).scan(data)
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data, speakers={ids(scan)["mara.png"]: SAM, ids(scan)["User Avatars/sam.png"]: SAM})
    assert caught.value.reason == "persona_is_character"


def test_two_user_speakers_mapped_to_two_personas_are_refused(store):
    rows = [*GROUP, {"name": "Sam", "is_user": True, "mes": "Switched persona.", "force_avatar": "User Avatars/alt.png"}]
    data = export(rows)
    scan = importer(store).scan(data)
    found = ids(scan)
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data, speakers={found["User Avatars/sam.png"]: SAM, found["User Avatars/alt.png"]: ANYA})
    assert caught.value.reason == "second_persona"


def test_one_card_mapped_at_two_revisions_is_refused(store):
    data = export()
    scan = importer(store).scan(data)
    found = ids(scan)
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data, speakers={found["mara.png"]: MARA, found["mara-other.png"]: MARA.unpinned()})
    assert caught.value.reason == "conflicting_revisions"


def test_two_speakers_may_be_mapped_to_one_card_on_purpose(store):
    data = export()
    scan = importer(store).scan(data)
    found = ids(scan)
    plan = importer(store).plan(data, speakers={found["mara.png"]: MARA, found["mara-other.png"]: MARA})
    plan.commit()
    assert plan.actors[found["mara.png"]] == plan.actors[found["mara-other.png"]]


def test_a_missing_card_is_refused_not_matched_by_name(store):
    data = export()
    scan = importer(store).scan(data)
    gone = SourceRef("catalog:cards", "character", "nobody", "r1")
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data, speakers={ids(scan)["mara.png"]: gone})
    assert caught.value.reason in {"missing_entity", "missing_revision"}


def test_a_campaign_folder_this_import_did_not_write_is_refused(store):
    data = export()
    scan = importer(store).scan(data)
    (store.root / "campaigns" / scan.campaign_id).mkdir(parents=True)
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(data)
    assert caught.value.reason == "campaign_exists"


@pytest.mark.parametrize("data,reason,line", [
    (b'{"name": "Mara", "mes": "ok"}\nnot json\n', "invalid_json", 2),
    (b'{"name": "Mara", "mes": "ok", "name": "dup"}\n', "invalid_json", 1),
    (b'{"name": "Mara", "mes": NaN}\n', "invalid_json", 1),
    (b'\n\n', "no_messages", 0),
    (b'{"name": "Mara"}\n', "invalid_message", 1),
])
def test_a_malformed_export_is_refused_with_its_line(store, data, reason, line):
    with pytest.raises(ImportRefused) as caught:
        importer(store).scan(data)
    assert (caught.value.reason, caught.value.line) == (reason, line)


def test_crlf_a_byte_order_mark_and_imprecise_numbers_are_read_and_reported(store):
    data = b"\xef\xbb\xbf" + export().replace(b"\n", b"\r\n")
    data = data.replace(b'"extra": {}', b'"extra": {"score": 0.1000000000000000055511151231257827}', 1)
    scan = importer(store).scan(data)
    assert scan.counts["posts"] == 7
    assert {(loss.line, loss.reason) for loss in scan.losses} >= {(1, "byte_order_mark"), (2, "imprecise_number")}


def test_a_long_chat_is_split_into_numbered_pages(store):
    rows = [HEADER] + [{"name": "Mara", "is_user": False, "mes": f"line {i}"} for i in range(2500)]
    plan = importer(store).plan(export(rows))
    assert [len(p.posts) for p in plan.pages] == [1000, 1000, 500]
    assert [p.ref.seq for p in plan.pages] == [1, 2, 3]
    assert plan.pages[0].header is not None and plan.pages[1].header is None
    plan.commit()
    pages, problems = read_transcript(store, plan.chat)
    assert problems == () and [p.post_id for page in pages for p in page.posts][-1] == "p002500"


def test_page_one_budgets_for_its_header_and_a_huge_header_is_refused(store):
    roomy = dict(HEADER, chat_metadata={"note_prompt": "n" * (1024 * 1024)})
    rows = [roomy] + [{"name": "Mara", "is_user": False, "mes": "x" * 200_000} for _ in range(20)]
    plan = importer(store).plan(export(rows))
    assert len(plan.pages) == 2  # the header's megabyte pushes posts to page 2
    assert all(len(data) <= MAX_FILE_BYTES for data in plan.preview().values())
    huge = [dict(HEADER, chat_metadata={"note_prompt": "n" * (3 * 1024 * 1024)})] + rows[1:]
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(export(huge))
    assert caught.value.reason == "header_too_large"


def test_a_header_that_renders_past_the_store_limit_is_refused_not_raised(store):
    # two bytes a number on the export's line, about ten once rendered with indentation
    dense = dict(HEADER, chat_metadata={"marks": [0] * (1024 * 1024)})
    rows = [dense, {"name": "Mara", "is_user": False, "mes": "Hi."}]
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(export(rows))
    assert caught.value.reason == "header_too_large"


def test_a_transcript_missing_its_last_page_is_not_a_complete_import(store):
    rows = [HEADER] + [{"name": "Mara", "is_user": False, "mes": f"line {i}"} for i in range(1500)]
    plan = importer(store).plan(export(rows))
    plan.commit()
    last = plan.pages[-1].ref
    (store.root / "campaigns" / last.campaign_id / "transcripts" / last.chat_id / f"{last.seq:010d}.json").unlink()
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(export(rows))
    assert caught.value.reason == "campaign_exists"


def test_a_repeat_with_other_lorebooks_or_label_is_refused(store):
    importer(store).plan(export()).commit()
    for change in ({"lorebooks": [LORE]}, {"label": "Harbor"}):
        with pytest.raises(ImportRefused) as caught:
            importer(store).plan(export(), **change)
        assert caught.value.reason == "imported_differently"


def test_a_ledger_that_outlived_the_campaign_folder_refuses_the_import(store):
    plan = importer(store).plan(export())
    folder = store.root.joinpath(*ledger_folder(f"campaigns/{plan.chat.campaign_id}"))
    folder.mkdir(parents=True)
    (folder / "0000000001.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(export())
    assert caught.value.reason == "campaign_exists"


def test_two_paired_devices_importing_one_export_make_two_chats_that_sync_cleanly(tmp_path):
    left, right = LibraryStore.create(tmp_path / "desktop"), LibraryStore.create(tmp_path / "phone")
    try:
        seed_sources(left)
        identify(left, "desktop")
        identify(right, "phone")
        handles = {"desktop": owner_handle(), "phone": owner_handle()}
        sync(left, right, handles)
        chats = []
        for device in (left, right):
            scan = importer(device).scan(export())
            plan = importer(device).plan(export(), speakers=mapping(scan))
            plan.commit()
            chats.append(plan.chat)
        assert chats[0] != chats[1] and all(c.campaign_id.startswith(scan.campaign_id + "-") for c in chats)
        assert sync(left, right, handles).conflicts == ()
        for device in (left, right):
            for chat in chats:  # each device holds both imports, each whole
                assert read_transcript(device, chat)[1] == ()
            again = importer(device).plan(export(), speakers=mapping(scan))
            assert again.status == "already_imported"
    finally:
        left.close()
        right.close()


def test_an_import_made_before_the_library_had_a_device_identity_is_still_found(store):
    first = importer(store).plan(export())
    first.commit()
    identify(store, "desktop")
    again = importer(store).plan(export())
    assert again.status == "already_imported" and again.chat == first.chat


def test_a_message_too_large_for_a_page_is_refused_not_raised(store):
    rows = [HEADER, {"name": "Mara", "is_user": False, "mes": "x", "swipes": ["x", "y" * (6 * 1024 * 1024)],
                     "swipe_id": 0}]  # one line under the store's limit, one post over a page
    with pytest.raises(ImportRefused) as caught:
        importer(store).plan(export(rows))
    assert caught.value.reason == "post_too_large"


def test_the_importer_never_imports_a_provider():
    assert not any(name == "grimoire.providers" or name.startswith("grimoire.providers.") for name in sys.modules
                   if name.startswith("grimoire.archaeology"))
    import grimoire.archaeologystore.library as module
    assert "grimoire.providers" not in module.__dict__


# -- V0.2.1 transcripts through 026 ---------------------------------------------------------------

V1_CHAT = ChatRef(CAMPAIGN_ID, chat_id_of(f"v1:{ARCHIVE}:{CAMPAIGN}/campaign.md", "00000000000000000000000000000001"))


@pytest.fixture
def env(tmp_path):
    environment = Env(tmp_path)
    yield environment
    environment.store.close()


def v1_posts(env) -> list:
    body = parse_frontmatter((env.v1 / CAMPAIGN / "scenes" / f"{SID}.md").read_text(encoding="utf-8"))[1]
    return parse_posts(body, ["Winifred"])[0]


def test_a_v1_scene_keeps_its_exact_posts_as_a_transcript_with_provenance(env):
    result = env.plan().apply()
    pages, problems = read_transcript(env.store, V1_CHAT)
    assert problems == () and len(pages) == 1
    posts = pages[0].posts
    expected = v1_posts(env)
    assert [p.text for p in posts] == [post.content for post in expected]
    assert [(p.speaker.role, p.speaker.label) for p in posts] == [
        ("user", "Winifred"), ("assistant", "Mara"), ("assistant", "Seraphine")]
    # never matched to an actor by name, and no audience inferred
    assert all(p.speaker.actor is None and p.audience == "unknown" for p in posts)
    assert [p.source["v1_post_id"] for p in posts] == [post.post_id for post in expected]
    stored = env.store.read(V1_CHAT).record
    chat_posts = chat_from_payload(V1_CHAT, stored.schema, stored.payload).extras["v1_import"]["posts"]
    assert [p.source["content_sha256"] for p in posts] == [row["content_sha256"] for row in chat_posts]
    assert pages[0].origin["format"] == "grimoire-v0.2.1-scene" and pages[0].origin["sha256"]
    path = "/".join(("campaigns", CAMPAIGN_ID, "transcripts", V1_CHAT.chat_id, "0000000001.json"))
    assert path in result.review["writes"]
    assert tree_unchanged(env)


def tree_unchanged(env) -> bool:
    from v1_fixture import tree_digest
    return tree_digest(env.v1) == env.v1_before


def test_a_rerun_finds_the_transcript_present(env):
    env.plan().apply()
    review = env.plan().review()
    pages = [path for path in review["outcomes"] if "/transcripts/" in path]
    assert pages and all(review["outcomes"][path] == "present" for path in pages)
    assert review["writes"] == []


def test_a_chat_imported_before_036_gets_its_transcript_on_the_next_run(env, monkeypatch):
    from grimoire.import_v1 import _plan
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", lambda self, scene: None)
    env.plan().apply()
    assert read_transcript(env.store, V1_CHAT)[0] == ()
    monkeypatch.undo()
    review = env.plan().review()
    assert [path for path in review["writes"] if "/transcripts/" in path]
    env.plan().apply()
    assert len(read_transcript(env.store, V1_CHAT)[0][0].posts) == 3


def test_a_v1_scene_changed_since_import_keeps_its_pages_and_is_reported(env):
    env.plan().apply()
    scene = env.v1 / CAMPAIGN / "scenes" / f"{SID}.md"
    scene.write_text(scene.read_text(encoding="utf-8") + '\n**Mara:** <!-- grimoire-response {"post_id": '
                     '"00000000000000000000000000000014"} -->\nThe tide is in.\n', encoding="utf-8")
    before = read_transcript(env.store, V1_CHAT)[0]
    review = env.plan(manifest=env.rescan()).review()
    pages = [path for path in review["outcomes"] if "/transcripts/" in path]
    assert {("v1_changed", path) for path in pages} <= {(row["reason"], row.get("detail")) for row in review["unresolved"]}
    assert review["writes"] == [] and read_transcript(env.store, V1_CHAT)[0] == before


def test_a_scene_whose_posts_do_not_match_the_review_imports_without_a_transcript(env, monkeypatch):
    from grimoire.import_v1 import _map

    def altered(body, players):  # a parser that disagrees with 011's reviewed rows
        posts, preamble = parse_posts(body, players)
        posts[0].content += " (changed)"
        return posts, preamble

    monkeypatch.setattr(_map, "parse_posts", altered)
    review = env.plan().review()
    assert "transcript_unverified" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if "/transcripts/" in path]
    assert [path for path in review["writes"] if "/chats/" in path]  # the chat itself still imports


INSERTED = ('**Mara:** <!-- grimoire-response {"post_id": "00000000000000000000000000000010"} -->\nA bell rings.\n\n')


def insert_first_post(env) -> None:
    scene = env.v1 / CAMPAIGN / "scenes" / f"{SID}.md"
    text = scene.read_text(encoding="utf-8")
    at = text.index("**Winifred:**")
    scene.write_text(text[:at] + INSERTED + text[at:], encoding="utf-8")


def test_a_chat_imported_before_036_whose_scene_changed_gets_no_transcript(env, monkeypatch):
    from grimoire.import_v1 import _plan
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", lambda self, scene: None)
    env.plan().apply()
    monkeypatch.undo()
    insert_first_post(env)
    review = env.plan(manifest=env.rescan()).review()
    assert "v1_changed" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if "/transcripts/" in path]  # never the unreviewed version


def test_a_scene_that_grows_past_a_page_boundary_writes_no_shifted_page(env, monkeypatch):
    from grimoire.import_v1 import _plan
    real = _plan.paginate
    monkeypatch.setattr(_plan, "paginate", lambda posts, measure, budget: real(posts, measure, budget, max_posts=3))
    env.plan().apply()
    before = read_transcript(env.store, V1_CHAT)[0]
    insert_first_post(env)
    plan = env.plan(manifest=env.rescan())
    assert not [path for path in plan.review()["writes"] if "/transcripts/" in path]
    plan.apply()
    assert read_transcript(env.store, V1_CHAT)[0] == before


def test_a_foreign_first_page_blocks_the_whole_transcript(env, monkeypatch):
    from grimoire.import_v1 import _plan
    from grimoire.core.identity import TranscriptRef
    from grimoire.store import ScopeRecord
    from grimoire.transcripts import TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptPage, Variant, page_to_payload
    real = _plan.paginate
    monkeypatch.setattr(_plan, "paginate", lambda posts, measure, budget: real(posts, measure, budget, max_posts=2))
    planner = _plan.ImportPlan._plan_transcript
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", lambda self, scene: None)
    env.plan().apply()  # the chat, imported without pages
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", planner)
    ref = TranscriptRef(V1_CHAT.campaign_id, V1_CHAT.chat_id, 1)
    page = TranscriptPage(ref, (Post("p1", Speaker("user"), (Variant("someone else's"),), 0),))
    txn = env.store.transaction()
    txn.put(ScopeRecord(ref, TRANSCRIPT_SCHEMA, page_to_payload(page), owner=OWNER), None)
    txn.commit(owner_handle())
    review = env.plan().review()
    assert "transcript_exists" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if "/transcripts/" in path]  # not page 2 behind it either


def test_a_foreign_page_past_the_planned_split_blocks_the_whole_transcript(env, monkeypatch):
    from grimoire.import_v1 import _plan
    from grimoire.core.identity import TranscriptRef
    from grimoire.store import ScopeRecord
    from grimoire.transcripts import TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptPage, Variant, page_to_payload
    planner = _plan.ImportPlan._plan_transcript
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", lambda self, scene: None)
    env.plan().apply()  # the chat, imported without pages
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", planner)
    ref = TranscriptRef(V1_CHAT.campaign_id, V1_CHAT.chat_id, 2)  # the import plans page 1 only
    page = TranscriptPage(ref, (Post("p1", Speaker("user"), (Variant("someone else's"),), 0),))
    txn = env.store.transaction()
    txn.put(ScopeRecord(ref, TRANSCRIPT_SCHEMA, page_to_payload(page), owner=OWNER), None)
    txn.commit(owner_handle())
    plan = env.plan()
    review = plan.review()
    assert "transcript_exists" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if "/transcripts/" in path]
    plan.apply()
    assert [ref.seq for ref in env.store.chat_transcript(V1_CHAT).records] == [2]


def test_a_page_added_between_plan_and_apply_makes_the_apply_stale(env, monkeypatch):
    from grimoire.import_v1 import _plan
    from grimoire.core.identity import TranscriptRef
    from grimoire.store import ScopeRecord, StaleWrite
    from grimoire.transcripts import TRANSCRIPT_SCHEMA, Post, Speaker, TranscriptPage, Variant, page_to_payload
    planner = _plan.ImportPlan._plan_transcript
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", lambda self, scene: None)
    env.plan().apply()  # the chat, imported without pages
    monkeypatch.setattr(_plan.ImportPlan, "_plan_transcript", planner)
    plan = env.plan()
    assert [path for path in plan.review()["writes"] if "/transcripts/" in path]  # page 1 is planned
    ref = TranscriptRef(V1_CHAT.campaign_id, V1_CHAT.chat_id, 2)
    page = TranscriptPage(ref, (Post("p1", Speaker("user"), (Variant("someone else's"),), 0),))
    txn = env.store.transaction()
    txn.put(ScopeRecord(ref, TRANSCRIPT_SCHEMA, page_to_payload(page), owner=OWNER), None)
    txn.commit(owner_handle())  # a sync or another writer, after the review
    with pytest.raises(StaleWrite):
        plan.apply()
    assert [found.seq for found in env.store.chat_transcript(V1_CHAT).records] == [2]


def test_a_v1_post_past_the_store_limit_leaves_the_scene_without_a_transcript(env):
    scene = env.v1 / CAMPAIGN / "scenes" / f"{SID}.md"
    text = scene.read_text(encoding="utf-8")
    at = text.index("**Winifred:**")
    huge = ('**Mara:** <!-- grimoire-response {"post_id": "00000000000000000000000000000010"} -->\n'
            + "w" * (9 * 1024 * 1024) + "\n\n")
    scene.write_text(text[:at] + huge + text[at:], encoding="utf-8")
    review = env.plan(manifest=env.rescan()).review()
    assert "too_large" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if "/transcripts/" in path]



def test_a_v1_speaker_label_past_the_limit_leaves_the_scene_without_a_transcript(env):
    scene = env.v1 / CAMPAIGN / "scenes" / f"{SID}.md"
    text = scene.read_text(encoding="utf-8")
    at = text.index("**Winifred:**")
    long = ("**Mara (" + "m" * 1200 + '):** <!-- grimoire-response {"post_id": "00000000000000000000000000000010"} -->\n'
            "A bell rings.\n\n")
    scene.write_text(text[:at] + long + text[at:], encoding="utf-8")
    review = env.plan(manifest=env.rescan()).review()  # reported, not raised
    assert "transcript_unrepresentable" in {row["reason"] for row in review["unresolved"]}
    assert not [path for path in review["writes"] if "/transcripts/" in path]
    assert [path for path in review["writes"] if path.endswith(f"/chats/{V1_CHAT.chat_id}.json")]  # the chat still imports
