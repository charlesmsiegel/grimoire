"""083's adapter ops over a synthetic library: cast voices, plan the card chat's accepted text and render it with
a test engine, with every refusal. Rendering changes no turn, source or campaign file, and a line keeps the voice
of the revision it was spoken in. No network, provider or bill: the engine here writes tones, and the Flite test
runs only where this ``ffmpeg`` has Flite (CI's voice job requires it)."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess

import pytest
from store_helpers import OWNER
from test_voice_performance import Tones
from textapi_fixtures import Device, desktop_library, keep, say

from grimoire.app.adapters.voicecast import configure
from grimoire.app.appapi import AppApi
from grimoire.textapi import ApiRefused
from grimoire.voicecast import wav_seconds
from grimoire.voiceengines.flite import FliteSynthesizer

MARA = "card:mara-vell/character/mara-vell"
GONE = "world:nowhere/character/nobody"


@pytest.fixture
def table(tmp_path):
    folder, chat = desktop_library(tmp_path / "desk")
    with Device(folder, [say("*Mara taps the ledger.* Twelve coins short."), say("Count them again.")]) as device:
        tones = Tones()
        configure(device.api, (tones,))
        api = AppApi(device.api)
        device.api.runner.seat(chat, OWNER)
        for text in ("Where is the rest?", "*I count them again.*"):
            device("post", chat=str(chat), text=text)
            device("generate", chat=str(chat))
            keep(device, str(chat))
        yield api, folder, str(chat), tones


def _refused(api: AppApi, op: str, args: dict, reason: str, status: int) -> ApiRefused:
    with pytest.raises(ApiRefused) as refused:
        api.call(op, args)
    assert (refused.value.reason, refused.value.status) == (reason, status), str(refused.value)
    return refused.value


def _actors(api: AppApi, chat: str) -> dict[str, str]:
    return {cue["speaker"]: cue["actor"] for cue in api.call("voicecast.plan", {"chat": chat})["cues"]}


def _cast(api: AppApi, **args) -> dict:
    return api.call("voicecast.cast", {"engine": "tone", "expected": api.call("voicecast.read", {})["etag"], **args})


def _snapshot(folder) -> dict[str, bytes]:
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob("*")
            if path.is_file() and "config" not in path.relative_to(folder).parts}


def test_the_ops_are_served_and_an_uncast_library_reads_its_scene_as_text(table):
    api, folder, chat, tones = table
    assert {"voicecast." + op for op in ("read", "cast", "uncast", "plan", "render")} <= set(api.ops)
    read = api.call("voicecast.read", {})
    assert (read["casting"], read["etag"], read["problem"]) == ([], None, None)
    assert read["engines"] == [{"engine": "tone", "voices": list(Tones.PITCH), "locality": "local", "network": False,
                                "billing": "free", "available": True}]
    plan = api.call("voicecast.plan", {"chat": chat})
    assert [(cue["kind"], cue["speaker"], cue["status"], cue["reason"]) for cue in plan["cues"]] == [
        ("post", "Anya Rook", "text_only", "not_cast"), ("line", "Mara Vell", "text_only", "not_cast"),
        ("post", "Anya Rook", "text_only", "action"), ("line", "Mara Vell", "text_only", "not_cast")]
    rendered = api.call("voicecast.render", {"chat": chat})
    assert tones.calls == [] and all(segment["audio"] is None for segment in rendered["segments"])
    assert not (folder / "config" / "voices").exists()  # reading and planning make nothing


def test_two_speakers_get_their_own_voices_and_rendering_changes_no_file(table):
    api, folder, chat, tones = table
    actors = _actors(api, chat)
    _cast(api, target="character", ref=MARA, voice="kal")
    cast = _cast(api, target="actor", ref=actors["Anya Rook"], voice="slt")
    assert [entry["describes"] for entry in cast["casting"]] == [
        f"the campaign's own choice for {actors['Anya Rook']}", f"{MARA} at any revision"]
    assert all(entry["available"] for entry in cast["casting"])
    plan = api.call("voicecast.plan", {"chat": chat})
    assert [(cue["speaker"], cue["status"], cue["voice"] and cue["voice"]["voice"]) for cue in plan["cues"]] == [
        ("Anya Rook", "voiced", "slt"), ("Mara Vell", "voiced", "kal"), ("Anya Rook", "text_only", "slt"),
        ("Mara Vell", "voiced", "kal")]
    assert plan["shared"] == [] and plan["cues"][1]["text"] == "*Mara taps the ledger.* Twelve coins short."
    assert plan["cues"][1]["script"] == "Twelve coins short." and plan["cues"][1]["why"].startswith("Mara Vell speaks")
    before = _snapshot(folder)
    first = api.call("voicecast.render", {"chat": chat, "count": 2})
    assert (first["total"], first["start"], first["next"]) == (4, 0, 2)
    rest = api.call("voicecast.render", {"chat": chat, "start": first["next"]})
    assert rest["next"] is None
    assert tones.calls == [("Where is the rest?", "slt"), ("Twelve coins short.", "kal"), ("Count them again.", "kal")]
    for segment in (*first["segments"], *rest["segments"]):
        cue = plan["cues"][segment["cue"]]
        assert (segment["text"], segment["digest"]) == (cue["text"], cue["digest"])  # the accepted text, untouched
        if segment["audio"] is not None:
            assert round(wav_seconds(base64.b64decode(segment["audio"])), 3) == segment["duration"]
    assert _snapshot(folder) == before  # no turn, source or campaign file changed
    off = api.call("voicecast.render", {"chat": chat, "audio": False})
    assert {segment["reason"] for segment in off["segments"] if segment["voice"]} == {"audio_off"}


def test_a_line_keeps_the_voice_of_the_revision_it_was_spoken_in(table):
    api, _folder, chat, _tones = table
    read = api.call("sources.read", {"ref": MARA})
    api.call("sources.save", {"ref": read["entity"], "head": read["head"], "etag": read["etag"],
                              "expected": read["manifest_etag"], "fields": {"description": "In her harbor coat now."}})
    assert api.call("sources.read", {"ref": MARA})["revisions"] == ["r1", "r2"]
    _cast(api, target="character", ref=MARA, voice="kal")
    _cast(api, target="character", ref=MARA + "@r2", voice="awb")  # the new outfit's own voice
    lines = [cue for cue in api.call("voicecast.plan", {"chat": chat})["cues"] if cue["kind"] == "line"]
    # both lines were spoken at r1: the source's head moving on changes neither line's voice
    assert [(cue["revision"], cue["voice"]["voice"]) for cue in lines] == [(MARA + "@r1", "kal")] * 2
    assert [item["rule"] for item in lines[0]["considered"]] == ["actor", "revision", "character_any_revision"]


def test_every_refusal_writes_nothing(table):
    api, folder, chat, _tones = table
    actors = _actors(api, chat)
    _refused(api, "voicecast.cast", {"target": "character", "ref": GONE, "engine": "tone", "voice": "kal",
                                     "expected": None}, "no_character", 404)
    _refused(api, "voicecast.cast", {"target": "actor", "ref": "campaign:nowhere/character/x-1", "engine": "tone",
                                     "voice": "kal", "expected": None}, "no_actor", 404)
    _refused(api, "voicecast.cast", {"target": "narrator", "campaign": "nowhere", "engine": "tone", "voice": "kal",
                                     "expected": None}, "no_campaign", 404)
    _refused(api, "voicecast.cast", {"target": "actor", "ref": actors["Mara Vell"], "engine": "piper", "voice": "jo",
                                     "rights": "consented", "expected": None}, "rights_unrecorded", 400)
    _refused(api, "voicecast.cast", {"target": "character", "ref": MARA, "engine": "tone", "voice": "Kal!",
                                     "expected": None}, "invalid_voice", 400)
    _refused(api, "voicecast.cast", {"target": "character", "ref": MARA, "engine": "tone", "voice": "kal"},
             "invalid_field", 400)  # a write names the etag it read
    _refused(api, "voicecast.cast", {"target": "voice", "ref": MARA, "engine": "tone", "voice": "kal",
                                     "expected": None}, "invalid_field", 400)
    _refused(api, "voicecast.uncast", {"target": "character", "ref": MARA, "expected": None}, "not_cast", 404)
    _refused(api, "voicecast.plan", {"chat": "not a chat"}, "invalid_field", 400)
    _refused(api, "voicecast.render", {"chat": chat, "count": 13}, "invalid_field", 400)
    _refused(api, "voicecast.render", {"chat": chat, "start": 5}, "invalid_field", 400)
    _refused(api, "voicecast.render", {"chat": chat, "audio": "yes"}, "invalid_field", 400)
    assert not (folder / "config" / "voices").exists()
    first = _cast(api, target="character", ref=MARA, voice="kal")
    _cast(api, target="narrator", voice="slt")
    _refused(api, "voicecast.cast", {"target": "narrator", "engine": "tone", "voice": "awb",
                                     "expected": first["etag"]}, "changed", 409)  # someone cast meanwhile
    consented = _cast(api, target="actor", ref=actors["Mara Vell"], engine="piper", voice="jo", rights="consented",
                      note="Jo recorded these samples for this table")
    entry = next(item for item in consented["casting"] if item["target"] == "actor")
    assert (entry["rights"], entry["available"]) == ("consented", False)  # this device has no such engine
    mara = [cue for cue in api.call("voicecast.plan", {"chat": chat})["cues"] if cue["speaker"] == "Mara Vell"]
    assert {cue["voice"]["engine"] for cue in mara} == {"piper"}
    rendered = api.call("voicecast.render", {"chat": chat})
    assert {segment["reason"] for segment in rendered["segments"] if segment["speaker"] == "Mara Vell"} == {
        "engine_unavailable"}  # never another speaker's voice, never another engine
    left = api.call("voicecast.uncast", {"target": "actor", "ref": actors["Mara Vell"],
                                         "expected": consented["etag"]})
    assert [entry["target"] for entry in left["casting"]] == ["character", "narrator"]


def test_a_casting_file_that_cannot_be_read_is_reported_and_left_alone(table):
    api, folder, chat, _tones = table
    target = folder / "config" / "voices" / "casting.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"{broken")
    read = api.call("voicecast.read", {})
    assert (read["casting"], read["problem"]["reason"]) == ([], "malformed")
    assert {"code": "casting_malformed", "detail": "config/voices/casting.json"} in api.call(
        "voicecast.plan", {"chat": chat})["problems"]
    _refused(api, "voicecast.cast", {"target": "narrator", "engine": "tone", "voice": "slt", "expected": None},
             "unreadable_record", 409)
    assert target.read_bytes() == b"{broken"


def _has_flite() -> bool:
    tool = shutil.which("ffmpeg")
    if tool is None:
        return False
    try:
        listed = subprocess.run([tool, "-hide_banner", "-filters"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return b" flite " in listed.stdout


REQUIRED = os.environ.get("GRIMOIRE_REQUIRE_VOICE") == "1"


@pytest.mark.skipif(not REQUIRED and not _has_flite(), reason="this ffmpeg has no Flite")
def test_flite_speaks_each_character_in_its_own_local_voice(tmp_path):
    flite = FliteSynthesizer()
    assert {"kal", "slt"} <= set(flite.voices()), "CI's voice job needs ffmpeg built with Flite"
    folder, chat = desktop_library(tmp_path / "desk")
    with Device(folder, [say("*Mara taps the ledger.* Twelve coins short: count them again.")]) as device:
        configure(device.api, (flite,))
        api = AppApi(device.api)
        device.api.runner.seat(chat, OWNER)
        device("post", chat=str(chat), text="Where is the rest?")
        device("generate", chat=str(chat))
        keep(device, str(chat))
        actors = _actors(api, str(chat))
        api.call("voicecast.cast", {"target": "character", "ref": MARA, "engine": "flite", "voice": "kal",
                                    "expected": None})
        api.call("voicecast.cast", {"target": "actor", "ref": actors["Anya Rook"], "engine": "flite", "voice": "slt",
                                    "expected": api.call("voicecast.read", {})["etag"]})
        rendered = api.call("voicecast.render", {"chat": str(chat)})
    audio = [segment for segment in rendered["segments"] if segment["audio"] is not None]
    assert [(segment["speaker"], segment["voice"]) for segment in audio] == [("Anya Rook", "flite/slt"),
                                                                            ("Mara Vell", "flite/kal")]
    assert all(segment["duration"] > 0.5 for segment in audio)
    assert audio[1]["start"] >= audio[0]["duration"]
    first, second = (base64.b64decode(segment["audio"]) for segment in audio)
    assert first != second


def test_a_players_post_speaks_in_the_voice_of_the_character_they_play(table):
    api, _folder, chat, tones = table
    _cast(api, target="character", ref="card:anya-rook/character/anya-rook", voice="awb")
    post = api.call("voicecast.plan", {"chat": chat})["cues"][0]
    assert (post["speaker"], post["status"], post["voice"]["voice"]) == ("Anya Rook", "voiced", "awb")
    assert [item["rule"] for item in post["considered"]][-1] == "character_any_revision"


def test_a_window_names_its_items_and_a_scene_that_moved_on_is_refused(table):
    api, _folder, chat, _tones = table
    _cast(api, target="character", ref=MARA, voice="kal")
    plan = api.call("voicecast.plan", {"chat": chat})
    first = api.call("voicecast.render", {"chat": chat, "count": 2, "fingerprint": plan["fingerprint"]})
    assert first["fingerprint"] == plan["fingerprint"]
    assert [(segment["turn"], segment["item"]) for segment in first["segments"]] == [
        (cue["turn"], cue["item"]) for cue in plan["cues"][:2]]
    _cast(api, target="character", ref=MARA, voice="rms")  # the casting changed between two windows
    _refused(api, "voicecast.render", {"chat": chat, "start": first["next"], "fingerprint": plan["fingerprint"]},
             "changed", 409)
    _refused(api, "voicecast.render", {"chat": chat, "fingerprint": 3}, "invalid_field", 400)


def test_no_campaign_is_refused_and_never_uncasts_every_campaigns_narrator(table):
    api, _folder, _chat, _tones = table
    cast = _cast(api, target="narrator", voice="slt")
    _refused(api, "voicecast.uncast", {"target": "narrator", "campaign": "", "expected": cast["etag"]},
             "invalid_target", 400)
    _refused(api, "voicecast.cast", {"target": "narrator", "campaign": "", "engine": "tone", "voice": "kal",
                                     "expected": cast["etag"]}, "invalid_target", 400)
    assert [entry["describes"] for entry in api.call("voicecast.read", {})["casting"]] == [
        "the narrator of every campaign"]


def test_flite_without_an_engine_offers_no_voices(tmp_path):
    missing = FliteSynthesizer(tool=str(tmp_path / "no-ffmpeg"))
    assert missing.voices() == ()
    if os.path.exists("/bin/false"):  # an ffmpeg that fails every run
        failing = FliteSynthesizer(tool="/bin/false")
        assert failing.voices() == ()
        with pytest.raises(ValueError):
            failing.synthesize("Hello.", "kal")


def test_a_card_chats_opening_greeting_speaks_in_the_characters_voice(tmp_path):
    folder, chat = desktop_library(tmp_path / "desk")
    with Device(folder, [say("Twelve coins short.")]) as device:
        configure(device.api, (Tones(),))
        api = AppApi(device.api)
        device.api.runner.seat(chat, OWNER)
        device.api.runner.begin(chat, device.api.player_handle)  # the card's greeting opens the chat
        keep(device, str(chat))
        device("post", chat=str(chat), text="Where is the rest?")
        device("generate", chat=str(chat))
        keep(device, str(chat))
        api.call("voicecast.cast", {"target": "character", "ref": MARA, "engine": "tone", "voice": "kal",
                                    "expected": None})
        lines = [cue for cue in api.call("voicecast.plan", {"chat": str(chat)})["cues"] if cue["kind"] == "line"]
    greeting, reply = lines
    assert greeting["revision"] is None or greeting["revision"].startswith(MARA)
    assert [(cue["status"], cue["voice"]["voice"]) for cue in lines] == [("voiced", "kal"), ("voiced", "kal")]
    assert greeting["why"].startswith("Mara Vell speaks with tone/kal")


def test_a_forked_chat_is_refused_and_the_time_budget_ends_a_window(table, monkeypatch):
    api, _folder, chat, tones = table
    _cast(api, target="character", ref=MARA, voice="kal")
    _cast(api, target="narrator", voice="slt")
    _cast(api, target="actor", ref=_actors(api, chat)["Anya Rook"], voice="slt")
    from grimoire.app.adapters import voicecast as adapter
    monkeypatch.setattr(adapter, "RENDER_BUDGET", 0.0)  # every synthesis after the first is over budget
    window = api.call("voicecast.render", {"chat": chat})
    assert [segment["status"] for segment in window["segments"]] == ["audio"] and window["next"] == 1
    runner = api.api.runner
    opened = runner.open

    def forked(ref):  # type: ignore[no-untyped-def]
        scene = opened(ref)
        return type("Forked", (), {"records": scene.records, "names": scene.names,
                                   "problems": (*scene.problems, ("turn_fork", "two devices played turn 2"))})()

    monkeypatch.setattr(runner, "open", forked)
    for op in ("voicecast.plan", "voicecast.render"):
        _refused(api, op, {"chat": chat}, "turn_fork", 409)
    _refused(api, "voicecast.render", {"chat": chat, "fingerprint": ""}, "invalid_field", 400)


def test_an_empty_flite_list_is_asked_again_only_after_a_while(monkeypatch):
    from grimoire.voiceengines import flite as module
    asked: list[int] = []
    engine = FliteSynthesizer(tool="ffmpeg-without-flite")
    monkeypatch.setattr(engine, "_list", lambda: asked.append(1) or ())
    now = [1000.0]
    monkeypatch.setattr(module, "_now", lambda: now[0])
    assert engine.voices() == () and engine.voices() == () and len(asked) == 1
    now[0] += module.RETRY_AFTER
    monkeypatch.setattr(engine, "_list", lambda: asked.append(1) or ("kal",))
    assert engine.voices() == ("kal",) and engine.voices() == ("kal",) and len(asked) == 2


def test_a_decided_outcome_is_read_by_the_narrator_as_the_table_read_it(tmp_path):
    from test_campaign_page import labelled_action
    folder, chat = desktop_library(tmp_path / "desk")
    labelled_action(folder, chat)
    with Device(folder, [say("Mara pockets the coins.")]) as device:
        configure(device.api, (Tones(),))
        api = AppApi(device.api)
        review = api.call("campaign.hub", {"chat": str(chat)})["open_action"]
        if api.call("campaign.confirm", {"chat": str(chat), "turn": review["turn"],
                                         "expected": review["expected"]})["done"]["outcome"] is None:
            review = api.call("campaign.hub", {"chat": str(chat)})["open_action"]
            api.call("campaign.decide", {"chat": str(chat), "audience": "director", "result": "success",
                                         "turn": review["turn"], "expected": review["expected"],
                                         "fingerprint": review["framed"]["fingerprint"]})
        device("generate", chat=str(chat))
        keep(device, str(chat))
        api.call("voicecast.cast", {"target": "narrator", "engine": "tone", "voice": "slt", "expected": None})
        plan = api.call("voicecast.plan", {"chat": str(chat)})
        rendered = api.call("voicecast.render", {"chat": str(chat), "fingerprint": plan["fingerprint"]})
    assert [cue["kind"] for cue in plan["cues"]] == ["post", "outcome", "line"]
    outcome = plan["cues"][1]
    # the table's full outcome line (who acted, what, the result and why), not the bare result an NPC hears
    assert outcome["text"].startswith("Anya Rook's action (") and "succeeds" in outcome["text"]
    assert (outcome["status"], outcome["voice"]["voice"]) == ("voiced", "slt")
    assert [(segment["kind"] if "kind" in segment else segment["item"].endswith(".outcome"), segment["status"])
            for segment in rendered["segments"]][1] == (True, "audio")
    assert not any(problem["code"] == "outcomes_unreadable" for problem in plan["problems"])
