"""Reading a grimoire transcript back in as a scene (#92): the parse/fill split.

The parser's whole job is to be a *review* step, so most of what is asserted
here is what it refuses to guess -- an ambiguous header bit, a location this
campaign does not have, a date its calendar cannot read -- and that nothing it
does is a write.
"""

from __future__ import annotations

import pytest

from grimoire.store import (
    appearances,
    campaigns,
    characters,
    entities,
    overlay,
    pcs,
    scene_import,
    scenes,
    worlds,
)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return wid, campaigns.create_campaign("Saltmarch", wid)


def _character(wid: str, name: str) -> str:
    aid, _ = characters.create_character(worlds.world_root(wid), name, "main")
    return aid


STORED = """---
title: The Long Quay
created: '2026-01-02T09:30:00Z'
updated: '2026-01-02T09:41:00Z'
time_history: 2026-01-02
location_history: the-quay
---

**You:** I walk the quay looking for Mara.

**Mara:** "You found me. Now what?"

**Grimoire:** The gulls scatter.
"""

CHAPTER = """# 3. The Long Quay

*2 January 2026 — The Quay*

**Cast:** Mara, Winifred Vance

> They counted crates until the tide turned.

**You:** I walk the quay looking for Mara.

**Mara:** "You found me. Now what?"
"""


# ---- parse: the stored scene file ----
def test_parse_reads_a_stored_scene_file(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    overlay.create_entity(cid, "locations", "The Quay")

    draft = scene_import.parse(cid, STORED.encode())

    assert draft["title"] == "The Long Quay"
    assert draft["date"] == "2026-01-02"
    assert draft["location"] == "the-quay"
    assert [m.get("speaker") for m in draft["messages"]] == [None, "Mara", None]
    assert [m["role"] for m in draft["messages"]] == ["user", "assistant", "assistant"]
    assert draft["messages"][0]["content"] == "I walk the quay looking for Mara."
    assert draft["warnings"] == []


def test_parse_writes_nothing(monkeypatch, tmp_path):
    """The review gate is only real if the parse half is inert."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    scene_import.parse(cid, STORED.encode())
    assert scenes.list_scenes(cid) == []
    assert appearances.roster(cid) == []


def test_parse_rejects_a_file_with_no_speaker_blocks(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scene_import.SceneImportError):
        scene_import.parse(cid, b"Just some prose about a quay.\n")


def test_parse_rejects_a_file_that_is_not_text(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scene_import.SceneImportError):
        scene_import.parse(cid, b"\xff\xfe\x00\x01**You:** hi")


def test_parse_survives_a_bom_and_crlf(monkeypatch, tmp_path):
    """What an editor on Windows writes. A leading BOM hides the frontmatter
    fence, and a `\\r` left in a label breaks the marker only once it is on disk."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(cid, ("﻿" + STORED.replace("\n", "\r\n")).encode())
    assert draft["title"] == "The Long Quay"
    assert len(draft["messages"]) == 3
    assert draft["messages"][1]["speaker"] == "Mara"


# ---- parse: the export bundle's chapter file ----
def test_parse_reads_a_bundle_chapter(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    overlay.create_entity(cid, "locations", "The Quay")
    _character(wid, "Mara")

    draft = scene_import.parse(cid, CHAPTER.encode())

    assert draft["title"] == "The Long Quay"        # the chapter number is not a title
    assert draft["location"] == "the-quay"
    assert len(draft["messages"]) == 2
    # A chapter carries the calendar's *friendly* rendering ("2 January 2026"),
    # which no provider in the store reads back -- `friendly` has no inverse. So
    # the date is the one header field an import cannot recover: it is named in
    # a warning, verbatim, for the reviewer to set.
    assert draft["date"] == ""
    assert any("2 January 2026" in w for w in draft["warnings"])


def test_a_cast_header_line_is_not_a_speaker(monkeypatch, tmp_path):
    """`**Cast:** Mara, Winifred Vance` matches the marker grammar as cleanly as
    any speaker does. Read as one, it becomes a message from someone called
    Cast -- and the names it lists are exactly the cast the review step wants."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    _character(wid, "Mara")
    _character(wid, "Winifred Vance")

    draft = scene_import.parse(cid, CHAPTER.encode())

    assert "Cast" not in [m.get("speaker") for m in draft["messages"]]
    assert not any("Winifred Vance" in m["content"] for m in draft["messages"])
    assert sorted(c["id"] for c in draft["cast"]) == ["mara", "winifred-vance"]


def test_an_epigraph_is_not_transcript(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(cid, CHAPTER.encode())
    assert not any("tide turned" in m["content"] for m in draft["messages"])


def test_a_single_header_bit_is_reported_rather_than_guessed(monkeypatch, tmp_path):
    """`_header_lines` drops whichever of date/location the scene lacked, so one
    bit could be either. Guessing puts a location in the date field."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(cid, b"# 1. Somewhere\n\n*The Quay*\n\n**You:** hello\n")
    assert draft["date"] == "" and draft["location"] == ""
    assert any("either the date or the location" in w for w in draft["warnings"])


def test_an_unknown_location_is_reported_not_invented(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(cid, CHAPTER.encode())
    assert draft["location"] == ""
    assert any("The Quay" in w for w in draft["warnings"])


def test_an_unreadable_date_is_dropped_with_a_warning(monkeypatch, tmp_path):
    """The draft's date has to be one the commit can actually set -- a date the
    provider rejects would fail deep inside `fill`, after the scene exists."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(
        cid, b"# 1. Somewhere\n\n*25 Kislev 5786 \xe2\x80\x94 The Quay*\n\n**You:** hello\n")
    assert draft["date"] == ""
    assert any("calendar can read" in w for w in draft["warnings"])


# ---- parse: what the marker grammar will not read ----
def test_text_before_the_first_marker_is_reported(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(cid, b"Notes I typed at the top.\n\n**You:** hello\n")
    assert len(draft["messages"]) == 1
    assert any("before the first" in w for w in draft["warnings"])


def test_a_marker_without_a_blank_line_above_it_is_reported(monkeypatch, tmp_path):
    """`_markers` requires the separator the serializer always writes, so a
    label pasted under the previous line is read as that speaker's content. It
    still imports -- but the reviewer is told, not left to find out by reading."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    draft = scene_import.parse(cid, b"**You:** hello\n**Mara:** and back\n")
    assert len(draft["messages"]) == 1
    assert any("blank line" in w for w in draft["warnings"])


# ---- parse: the cast ----
def test_cast_is_matched_by_the_resolver_the_transcript_is_read_with(monkeypatch, tmp_path):
    """A model writes `**Winifred:**` for a character carded "Winifred Vance",
    and `match_name` is what decides who that is everywhere else."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    _character(wid, "Winifred Vance")
    draft = scene_import.parse(cid, b"**Winifred:** I counted them twice.\n")
    assert draft["cast"] == [{"label": "Winifred", "kind": "characters",
                              "id": "winifred-vance", "name": "Winifred Vance", "role": "npc"}]
    assert draft["unmatched"] == []


def test_an_ambiguous_label_matches_nobody(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    _character(wid, "Winifred Vance")
    _character(wid, "Winifred Vale")
    draft = scene_import.parse(cid, b"**Winifred:** which one?\n")
    assert draft["cast"] == [] and draft["unmatched"] == ["Winifred"]


def test_a_pc_speaker_is_offered_as_the_player(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    pcs.create_pc(worlds.world_root(wid), "Seraphine", [], "main")
    draft = scene_import.parse(cid, b"**Seraphine:** I put my hand on the ledger.\n")
    assert draft["cast"] == [{"label": "Seraphine", "kind": "pcs", "id": "seraphine",
                              "name": "Seraphine", "role": "player"}]


def test_a_character_locked_to_player_keeps_that_role(monkeypatch, tmp_path):
    """`appear` refuses a role that disagrees with the campaign lock, so
    defaulting a character-kind player to "npc" would drop them at commit."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    _character(wid, "Mara")
    prior = scenes.create_scene(cid, "Earlier")
    appearances.appear(cid, prior, "characters", "mara", "main", "player", narrate=False)
    draft = scene_import.parse(cid, b"**Mara:** again.\n")
    assert draft["cast"][0]["role"] == "player"


def test_synthetic_speakers_are_never_offered_as_cast(monkeypatch, tmp_path):
    """`⁣Scene` and `⁣Roll` tag lines no actor said."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    body = (f"**{scenes.TRANSITION_SPEAKER}:** *The scene moves to the quay.*\n\n"
            f"**{scenes.ROLL_SPEAKER}:** Athletics: 3 successes\n").encode()
    draft = scene_import.parse(cid, body)
    assert draft["cast"] == [] and draft["unmatched"] == []
    assert [m["speaker"] for m in draft["messages"]] == [scenes.TRANSITION_SPEAKER,
                                                         scenes.ROLL_SPEAKER]


def test_a_sub_speaker_label_resolves_to_its_base(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    _character(wid, "Mara")
    draft = scene_import.parse(cid, b"**Mara (aside):** to nobody in particular.\n")
    assert draft["messages"][0]["speaker"] == "Mara (aside)"
    assert [c["id"] for c in draft["cast"]] == ["mara"]


# ---- commit ----
def test_commit_writes_the_transcript_verbatim(monkeypatch, tmp_path):
    wid, cid = _campaign(monkeypatch, tmp_path)
    overlay.create_entity(cid, "locations", "The Quay")
    _character(wid, "Mara")
    draft = scene_import.parse(cid, STORED.encode())

    sid = scenes.create_scene(cid, draft["title"])
    out = scene_import.commit(cid, sid, draft["messages"], date=draft["date"],
                            location=draft["location"],
                            cast=[{**c, "version": "main"} for c in draft["cast"]])

    scene = scenes.read_scene(cid, out["id"])
    assert [(m["role"], m.get("speaker"), m["content"]) for m in scene["messages"]] == [
        ("user", None, "I walk the quay looking for Mara."),
        ("assistant", "Mara", '"You found me. Now what?"'),
        ("assistant", None, "The gulls scatter."),
    ]
    assert scenes.get_time_history(cid, out["id"]) == ["2026-01-02"]
    assert scenes.get_location_history(cid, out["id"]) == ["the-quay"]
    assert [a["id"] for a in appearances.scene_cast(cid, out["id"])] == ["mara"]


def test_commit_narrates_nothing(monkeypatch, tmp_path):
    """Seating a cast member appends "*X joins the scene.*" to a transcript that
    already has messages, and a moment or a place that CHANGES appends a
    transition line. An imported log contained none of that."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    overlay.create_entity(cid, "locations", "The Quay")
    _character(wid, "Mara")
    draft = scene_import.parse(cid, STORED.encode())

    sid = scenes.create_scene(cid, draft["title"])
    out = scene_import.commit(cid, sid, draft["messages"], date=draft["date"],
                            location=draft["location"],
                            cast=[{**c, "version": "main"} for c in draft["cast"]])

    scene = scenes.read_scene(cid, out["id"])
    assert len(scene["messages"]) == len(draft["messages"])
    assert not any(m.get("speaker") == scenes.TRANSITION_SPEAKER for m in scene["messages"])


def test_a_seated_player_makes_their_own_lines_user_side(monkeypatch, tmp_path):
    """Role is not stored -- a message is user-side iff its speaker is a seated
    player -- so the round trip only closes once the cast is seated."""
    wid, cid = _campaign(monkeypatch, tmp_path)
    pcs.create_pc(worlds.world_root(wid), "Seraphine", [], "main")
    draft = scene_import.parse(cid, b"**Seraphine:** I put my hand on the ledger.\n")
    assert draft["messages"][0]["role"] == "assistant"      # nothing is seated yet

    sid = scenes.create_scene(cid, "Imported")
    out = scene_import.commit(cid, sid, draft["messages"],
                            cast=[{**c, "version": "main"} for c in draft["cast"]])
    assert scenes.read_scene(cid, out["id"])["messages"][0]["role"] == "user"


def test_commit_reports_the_id_the_date_renamed_the_scene_to(monkeypatch, tmp_path):
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Imported")
    out = scene_import.commit(cid, sid, [{"role": "user", "content": "hello"}],
                            date="2026-01-02")
    assert out["id"] != sid and "2026-01-02" in out["id"]
    assert scenes.read_scene(cid, out["id"])["messages"][0]["content"] == "hello"


def test_commit_removes_the_scene_it_could_not_finish(monkeypatch, tmp_path):
    """All or nothing -- and specifically after the rename. `set_datetime`
    stamps the start date into the filename, so a step that fails *after* it
    has to remove the scene by the id the scene now has, not the one the caller
    created. Deleting the stale id silently leaves the half-import standing."""
    _wid, cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Imported")

    with pytest.raises(entities.EntityNotFound):
        scene_import.commit(cid, sid, [{"role": "user", "content": "hello"}],
                            date="2026-01-02", location="no-such-place")

    assert scenes.list_scenes(cid) == []


def test_a_bundle_chapter_round_trips_through_the_exporter(monkeypatch, tmp_path):
    """The one claim that covers the whole format at once: export a scene, feed
    the chapter file back in, and get the same transcript."""
    from grimoire.store import export

    wid, cid = _campaign(monkeypatch, tmp_path)
    _character(wid, "Mara")
    sid = scenes.create_scene(cid, "The Long Quay")
    appearances.appear(cid, sid, "characters", "mara", "main", "npc", narrate=False)
    scenes.append_message(cid, sid, "user", "I walk the quay looking for Mara.")
    scenes.append_message(cid, sid, "assistant", '"You found me."', speaker="Mara")

    data = export.collect(cid)
    chapter = "\n\n".join([f"# {export.toc_label(data['chapters'][0])}",
                           *export._header_lines(data["chapters"][0]),
                           export.chronicle.transcript_text(data["chapters"][0]["messages"])])

    draft = scene_import.parse(cid, chapter.encode())
    assert draft["title"] == "The Long Quay"
    assert [(m["role"], m.get("speaker"), m["content"]) for m in draft["messages"]] == [
        ("user", None, "I walk the quay looking for Mara."),
        ("assistant", "Mara", '"You found me."'),
    ]
    assert [c["id"] for c in draft["cast"]] == ["mara"]
