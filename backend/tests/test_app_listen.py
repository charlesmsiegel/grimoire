"""Sprint 116's group D1: a chat listened to in its cast voices (083) and the composer's context preview (085).

The proving scenario runs over a synthetic card chat with a narrator (a bound scenario), two NPCs (Mara Vell and
Tamsin Hale) and the persona (Anya Rook), whose kept turns the test writes before the app serves it. The casting is
changed only as reviewed: ``voices.review`` writes nothing and says what ``voicecast.cast``/``uncast`` will do,
refusing as they would; the write names the etag the review read, so a change made in between refuses it. Listening
plans the scene, renders it in windows that name the plan's fingerprint (a casting change between two windows is
refused ``changed``), and with audio off reads as text. The draft preview shows the lore a draft triggers and a
memory it recalls, and writes nothing. The pages run end to end under Node (``js/listen.e2e.mjs``) where Node is
installed. No model is called beyond the scripted fake; the voice engine writes tones; nothing is billed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from scene_fixtures import LIGHTHOUSE, STORM, attach_tamsin, card_library
from test_context_preview import PROMISE, archive
from test_voice_performance import Tones
from textapi_fixtures import Device, keep, say

from grimoire.app import AppServer
from grimoire.app.adapters.voicecast import configure
from grimoire.app.appapi import AppApi
from grimoire.app.pages import load_pages
from grimoire.core.identity import InstanceRef
from grimoire.textapi import ApiRefused

STATIC = Path(__file__).resolve().parents[1] / "src" / "grimoire" / "app" / "static"
NODE = shutil.which("node")
MARA = "card:mara-vell/character/mara-vell"
TAMSIN_REF = "card:tamsin-hale/character/tamsin-hale"
DRAFT = '"Mara, I kept my word about the silver compass. Is the lighthouse still dark?"'
REPLIES = ("*Mara taps the ledger.* Twelve coins short.", "Customs saw nothing come ashore.",
           "Fog rolls in over the quay.", "Then count them again.")


def files(folder: Path, *, casting: bool = True) -> dict[str, bytes]:
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*"))
            if path.is_file() and (casting or "config" not in path.relative_to(folder).parts)}


@pytest.fixture
def scene(tmp_path):
    """The card chat, seated, with the storm scenario bound (so the director narrates) and Tamsin attached, four
    kept turns (Mara, Tamsin, the narrator, Mara) and a promise Mara was told. The tone engine is installed."""
    folder, chat, _storm = card_library(tmp_path)
    with Device(folder, [say(text) for text in REPLIES]) as device:
        configure(device.api, (Tones(),))
        app = AppApi(device.api)
        tamsin = attach_tamsin(device.table.store, device.table.owner, chat)
        app.call("continue", {"chat": str(chat)})
        app.call("play.add_card", {"chat": str(chat), "role": "scenario", "ref": str(STORM)})
        cast = {member["name"]: member["actor"] for member in device("open", chat=str(chat))["cast"]}
        for text, args in (("Where is the rest of it?", {"speakers": [cast["Mara Vell"]]}),
                           ("*I look at the clerk.*", {"speakers": [str(tamsin)]}),
                           ("What happens now?", {"director": True, "speakers": []}),
                           ("Count them with me.", {"speakers": [cast["Mara Vell"]]})):
            device("post", chat=str(chat), text=text)
            device("generate", chat=str(chat), **args)
            keep(device, str(chat))
        archive(device.table, chat, InstanceRef.parse(cast["Mara Vell"]))
        yield device, app, folder, chat, cast


def refused(app: AppApi, op: str, args: dict) -> ApiRefused:
    with pytest.raises(ApiRefused) as caught:
        app.call(op, args)
    return caught.value


def reviewed(app: AppApi, kind: str, **args) -> dict:
    """Review a change, then make it exactly as reviewed (the etag the review read)."""
    review = app.call("voices.review", {"kind": kind, **args})
    assert review["written"] is False
    app.call("voicecast." + kind, {**args, "expected": review["etag"]})
    return review


def cast_scene(app: AppApi, chat, cast) -> None:
    reviewed(app, "cast", target="narrator", campaign=chat.campaign_id, engine="tone", voice="slt")
    reviewed(app, "cast", target="character", ref=MARA, engine="tone", voice="kal")
    reviewed(app, "cast", target="actor", ref=cast["Tamsin Hale"], engine="tone", voice="rms")


def test_the_voices_page_registers_in_settings_and_the_chat_page_keeps_its_contract():
    pages = {page.id: page for page in load_pages(STATIC)}
    voices = pages["voices"]
    assert (voices.route, voices.slot, voices.owner, voices.title) == ("voices", "settings", "116", "Voices")
    assert set(voices.requires) == {"voicecast.read", "voicecast.plan", "voicecast.cast", "voicecast.uncast",
                                    "voices.review"}
    assert pages["voice"].owner == "061" and pages["chat"].owner == "111"  # 061's and 111's pages stay theirs


def test_a_review_writes_nothing_and_says_what_the_write_will_do(scene):
    _device, app, folder, chat, cast = scene
    before = files(folder)
    review = app.call("voices.review", {"kind": "cast", "target": "character", "ref": MARA, "engine": "tone",
                                        "voice": "kal"})
    assert (review["change"], review["before"], review["after"]["key"], review["etag"]) == ("new", None, "tone/kal", None)
    assert (review["describes"], review["writes"], review["available"]) == (f"{MARA} at any revision",
                                                                          ["config/voices/casting.json"], True)
    assert files(folder) == before  # the review wrote nothing, not even the casting
    app.call("voicecast.cast", {"target": "character", "ref": MARA, "engine": "tone", "voice": "kal",
                                "expected": review["etag"]})
    again = app.call("voices.review", {"kind": "cast", "target": "character", "ref": MARA, "engine": "tone",
                                       "voice": "kal"})
    assert (again["change"], again["writes"]) == ("same", [])
    other = app.call("voices.review", {"kind": "cast", "target": "character", "ref": MARA, "engine": "tone",
                                       "voice": "awb"})
    assert (other["change"], other["before"]["key"], other["after"]["key"]) == ("replace", "tone/kal", "tone/awb")
    gone = app.call("voices.review", {"kind": "uncast", "target": "character", "ref": MARA})
    assert (gone["change"], gone["after"], gone["removes_file"]) == ("remove", None, True)
    unheard = app.call("voices.review", {"kind": "cast", "target": "narrator", "engine": "piper", "voice": "jo"})
    assert unheard["available"] is False  # kept as cast, but this device has no such voice


def test_a_review_refuses_as_the_write_would_and_writes_nothing(scene):
    _device, app, folder, chat, cast = scene
    before = files(folder)
    cases = {
        "invalid_field": {"kind": "recast", "target": "narrator", "engine": "tone", "voice": "kal"},
        "rights_unrecorded": {"kind": "cast", "target": "actor", "ref": cast["Mara Vell"], "engine": "tone",
                              "voice": "kal", "rights": "consented"},
        "no_character": {"kind": "cast", "target": "character", "ref": "world:nowhere/character/nobody",
                         "engine": "tone", "voice": "kal"},
        "no_campaign": {"kind": "cast", "target": "narrator", "campaign": "nowhere", "engine": "tone", "voice": "kal"},
        "invalid_voice": {"kind": "cast", "target": "character", "ref": MARA, "engine": "tone", "voice": "Kal!"},
        "not_cast": {"kind": "uncast", "target": "character", "ref": MARA},
    }
    for reason, args in cases.items():
        assert refused(app, "voices.review", args).reason == reason, reason
        if args["kind"] in ("cast", "uncast"):  # and the write itself refuses it the same way
            write = {key: value for key, value in args.items() if key != "kind"}
            assert refused(app, "voicecast." + args["kind"], {**write, "expected": None}).reason == reason
    assert files(folder) == before
    broken = folder / "config" / "voices" / "casting.json"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"{not a casting")
    # with a casting file that cannot be read, the review and the write refuse in the same order, with the same reason
    ordered = (
        ("unreadable_record", {"kind": "cast", "target": "narrator", "engine": "tone", "voice": "slt"}),
        ("invalid_voice", {"kind": "cast", "target": "character", "ref": MARA, "engine": "tone", "voice": "Kal!"}),
        ("no_campaign", {"kind": "cast", "target": "narrator", "campaign": "nowhere", "engine": "tone", "voice": "kal"}),
        ("unreadable_record", {"kind": "uncast", "target": "character", "ref": MARA}),
    )
    for reason, args in ordered:
        write = {key: value for key, value in args.items() if key != "kind"}
        assert refused(app, "voices.review", args).reason == reason, args
        assert refused(app, "voicecast." + args["kind"], {**write, "expected": None}).reason == reason, args
    assert broken.read_bytes() == b"{not a casting"
    broken.unlink()
    elsewhere = folder.parent / "elsewhere.json"
    elsewhere.write_bytes(b"{}")
    broken.symlink_to(elsewhere)  # a link is refused `link` by both, and never followed
    args = {"kind": "cast", "target": "narrator", "engine": "tone", "voice": "slt"}
    assert refused(app, "voices.review", args).reason == "link"
    assert refused(app, "voicecast.cast", {**{k: v for k, v in args.items() if k != "kind"}, "expected": None}).reason == "link"
    assert elsewhere.read_bytes() == b"{}"


def test_a_change_made_after_the_review_refuses_it_and_a_retried_write_is_not_made_twice(scene):
    _device, app, folder, chat, cast = scene
    review = app.call("voices.review", {"kind": "cast", "target": "narrator", "campaign": chat.campaign_id,
                                        "engine": "tone", "voice": "slt"})
    app.call("voicecast.cast", {"target": "character", "ref": MARA, "engine": "tone", "voice": "kal",
                                "expected": review["etag"]})  # another screen cast meanwhile
    before = files(folder)
    stale = refused(app, "voicecast.cast", {"target": "narrator", "campaign": chat.campaign_id, "engine": "tone",
                                            "voice": "slt", "expected": review["etag"]})
    assert stale.reason == "changed" and files(folder) == before
    again = app.call("voices.review", {"kind": "cast", "target": "narrator", "campaign": chat.campaign_id,
                                       "engine": "tone", "voice": "slt"})
    args = {"target": "narrator", "campaign": chat.campaign_id, "engine": "tone", "voice": "slt",
            "expected": again["etag"]}
    made = app.call("voicecast.cast", args)
    after = files(folder)
    assert refused(app, "voicecast.cast", args).reason == "changed"  # the same write retried is not made again
    assert files(folder) == after
    assert [entry["describes"] for entry in made["casting"]] == [f"{MARA} at any revision",
                                                                 f"the narrator of {chat.campaign_id}"]


def test_the_scene_is_planned_with_each_voice_and_why_and_played_in_windows(scene):
    _device, app, folder, chat, cast = scene
    cast_scene(app, chat, cast)
    before = files(folder)
    plan = app.call("voicecast.plan", {"chat": str(chat)})
    heard = [(cue["speaker"], cue["status"], cue["reason"], cue["voice"] and cue["voice"]["voice"]) for cue in plan["cues"]]
    assert ("Mara Vell", "voiced", None, "kal") in heard and ("Tamsin Hale", "voiced", None, "rms") in heard
    assert ("Narrator", "voiced", None, "slt") in heard
    assert {(status, reason) for name, status, reason, _ in heard if name == "Anya Rook"} == {
        ("text_only", "not_cast"), ("text_only", "action")}  # the uncast speaker stays text, never another voice
    assert plan["shared"] == []
    tamsin = next(cue for cue in plan["cues"] if cue["speaker"] == "Tamsin Hale")
    assert tamsin["why"].startswith("Tamsin Hale speaks with tone/rms, cast for the campaign's own choice")
    assert [item["rule"] for item in tamsin["considered"]][0] == "actor"
    # play: windows in order, each naming the plan's fingerprint, the accepted text untouched
    windows, start = [], 0
    while start is not None and len(windows) < 2:
        window = app.call("voicecast.render", {"chat": str(chat), "start": start, "count": 4,
                                               "fingerprint": plan["fingerprint"]})
        windows.append(window)
        start = window["next"]
    assert [window["start"] for window in windows] == [0, 4]
    played = [segment for window in windows for segment in window["segments"]]
    assert [segment["cue"] for segment in played] == list(range(8))
    assert all(segment["text"] == plan["cues"][segment["cue"]]["text"] for segment in played)
    assert {segment["status"] for segment in played if plan["cues"][segment["cue"]]["status"] == "voiced"} == {"audio"}
    # the casting changes mid-play: the next window is refused, and a new plan names the new voice
    reviewed(app, "cast", target="actor", ref=cast["Tamsin Hale"], engine="tone", voice="awb")
    assert refused(app, "voicecast.render", {"chat": str(chat), "start": start, "count": 4,
                                             "fingerprint": plan["fingerprint"]}).reason == "changed"
    again = app.call("voicecast.plan", {"chat": str(chat)})
    assert again["fingerprint"] != plan["fingerprint"]
    assert next(cue for cue in again["cues"] if cue["speaker"] == "Tamsin Hale")["voice"]["voice"] == "awb"
    # audio off reads the scene as text: nothing is synthesized
    off = app.call("voicecast.render", {"chat": str(chat), "count": 12, "audio": False})
    assert all(segment["audio"] is None for segment in off["segments"])
    assert {segment["reason"] for segment in off["segments"] if segment["voice"]} == {"audio_off"}
    # planning and playing wrote no turn, source or chat (the casting is the only file that changed)
    assert files(folder, casting=False) == {name: data for name, data in before.items() if not name.startswith("config/")}


def test_a_private_message_is_never_cued(scene):
    device, app, _folder, chat, _cast = scene
    count = len(app.call("voicecast.plan", {"chat": str(chat)})["cues"])
    device("post", chat=str(chat), text='I whisper to Tamsin Hale, "The ledger is a forgery."')
    cues = app.call("voicecast.plan", {"chat": str(chat)})["cues"]
    assert len(cues) == count and not any("forgery" in cue["text"] for cue in cues)


def test_the_draft_preview_shows_the_lore_and_the_memory_and_writes_nothing(scene):
    device, app, folder, chat, cast = scene
    before = files(folder)
    turns = device("open", chat=str(chat))["turns"]
    preview = app.call("providers.preview_context", {"chat": str(chat), "actor": cast["Mara Vell"], "draft": DRAFT})
    assert (preview["sent"], preview["written"], preview["estimated"], preview["draft"]["heard"]) == (False, False, True, True)
    lore = [item for item in preview["recalls"] if item["lane"] == "lore"]
    memory = [item for item in preview["recalls"] if item["lane"] == "memory"]
    assert [(item["line"], item["why"]) for item in lore] == [(LIGHTHOUSE, "triggered")]
    assert [item["line"] for item in memory] == [f"You were told: {PROMISE}"]
    assert files(folder) == before and device("open", chat=str(chat))["turns"] == turns  # nothing was posted
    # the composer's mode is read as play.post reads it: the same draft as speech, as an action, or refused
    kinds = {mode: app.call("providers.preview_context", {"chat": str(chat), "actor": cast["Mara Vell"], "draft": DRAFT,
                                                          "mode": mode})["draft"]["kind"] for mode in ("speech", "action")}
    assert kinds == {"speech": "speech", "action": "action"}
    assert refused(app, "providers.preview_context", {"chat": str(chat), "actor": cast["Mara Vell"], "draft": DRAFT,
                                                      "mode": "shout"}).reason == "invalid_field"
    # an NPC that would not hear the draft keeps its saved context; the persona is no NPC to preview
    whisper = app.call("providers.preview_context", {"chat": str(chat), "actor": cast["Mara Vell"],
                                                    "draft": 'I whisper to Tamsin Hale, "The lighthouse is dark."'})
    assert whisper["draft"]["heard"] is False and whisper["recalls"] == []
    assert refused(app, "providers.preview_context", {"chat": str(chat), "actor": cast["Anya Rook"],
                                                      "draft": DRAFT}).reason == "not_npc"
    assert files(folder) == before
    assert not device.prompts()[len(REPLIES):]  # no model was asked anything for a preview


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
def test_listening_and_the_draft_preview_end_to_end(scene):
    device, app_api, folder, chat, cast = scene
    # the narrator and Tamsin are cast here; the page casts Mara through its own review
    reviewed(app_api, "cast", target="narrator", campaign=chat.campaign_id, engine="tone", voice="slt")
    reviewed(app_api, "cast", target="actor", ref=cast["Tamsin Hale"], engine="tone", voice="rms")
    before = files(folder, casting=False)
    turns = device("open", chat=str(chat))["turns"]
    app = AppServer(device.api)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = app.address
        env = {**os.environ, "GRIMOIRE_APP": f"http://{host}:{port}", "GRIMOIRE_TOKEN": app.token,
               "GRIMOIRE_CHAT": str(chat), "GRIMOIRE_TAMSIN": cast["Tamsin Hale"], "GRIMOIRE_MARA": cast["Mara Vell"]}
        result = subprocess.run([NODE, "--test", "--test-reporter=tap", str(Path(__file__).with_name("js") / "listen.e2e.mjs")],
                                capture_output=True, text=True, timeout=240, check=False, env=env)
    finally:
        app.close()
        thread.join(10)
    assert result.returncode == 0, result.stdout[-8000:] + result.stderr[-3000:]
    assert "# pass 1" in result.stdout
    # the page cast through review and played and previewed: no turn, source or chat changed, nothing was posted
    assert files(folder, casting=False) == before
    assert device("open", chat=str(chat))["turns"] == turns
    assert not device.prompts()[len(REPLIES):]
    casting = AppApi(device.api).call("voicecast.read", {})["casting"]
    assert {(entry["target"], entry["voice"]) for entry in casting} == {("narrator", "slt"), ("character", "kal"),
                                                                        ("actor", "awb")}
