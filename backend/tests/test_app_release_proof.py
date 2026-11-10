"""Wave 7's end-to-end release proof (docs/roadmap/UI_COVERAGE.md, "End-to-end release proof"), run against the app
as a person runs it: ``python -m grimoire.app serve`` as a real process, driven only over its loopback HTTP API
with the serve's token, the way the shell's pages call it. Nothing is set up with a CLI command or by editing a
file once the first app is launched; launch flags (``--model``, ``--sync-port``) are how the app is started.

Every library, card, V1 library, model and picture here is synthetic. The model is a loopback OpenAI-compatible
server that answers each NPC in its own voice and records every request, so a hidden model call is counted.

What a browser would add (a keyboard-only and screen-reader pass, touch sizes on a phone, a draft surviving
navigation in a real tab) is not run here: there is no browser in this test. The shell's draft and focus modules
have their own Node tests (``tests/js``).
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
from card_fixtures import PORTRAIT, PORTRAIT_2, V3, card, encode
from grimoire.app.server import TOKEN_HEADER
from grimoire.testing.fileops import snapshot
from import_fixtures import build_two_npc_library
from v1_fixture import sha

SRC = Path(__file__).resolve().parent.parent / "src"
OWNER = "human:owner"
#: The rooted folders a refused op must leave as they were (the security audit may record a decision).
CANONICAL = ("sources", "campaigns", "assets", "kept")


# -- the model: one loopback server, one voice per NPC -------------------------------------------------------

class _ModelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.server.guard:
            self.server.requests.append(body)
            count = len(self.server.requests)
        system = " ".join(message["content"] for message in body["messages"] if message["role"] == "system")
        found = re.search(r"You are ([^,.\n]+)", system)
        speaker = found.group(1) if found else "the director"
        text = f"[{speaker}] {VOICES.get(speaker, 'Says nothing much.')}"
        usage = {"prompt_tokens": 100 + count, "completion_tokens": 9}
        events = [{"model": body["model"], "choices": [{"delta": {"content": text}, "finish_reason": None}]},
                  {"model": body["model"], "choices": [{"delta": {}, "finish_reason": "stop"}]},
                  {"model": body["model"], "choices": [], "usage": usage}]
        data = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


VOICES = {"Mara Vell": "Keep your voice down; the harbor master listens.",
          "Seraphine Lark": "I light the lamps. Who are you to ask?",
          "Mara": "Salt keeps. Ledgers don't.", "Seraphine": "Ask me again, softly."}


@contextlib.contextmanager
def model_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ModelHandler)
    server.requests, server.guard = [], threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def speakers(requests: list[dict]) -> list[str]:
    """Whom each recorded model request was for, from its system prompt."""
    found = []
    for request in requests:
        system = " ".join(message["content"] for message in request["messages"] if message["role"] == "system")
        match = re.search(r"You are ([^,.\n]+)", system)
        found.append(match.group(1) if match else "?")
    return found


# -- the app, as a process -----------------------------------------------------------------------------------

class Refused(Exception):
    def __init__(self, status: int, answer: dict) -> None:
        super().__init__(status, answer)
        self.status, self.reason, self.message = status, answer.get("error"), answer.get("message")


class App:
    """One ``serve`` process and its token; ``app(op, **args)`` is ``POST /api/<op>``."""

    def __init__(self, home: Path, *args: str) -> None:
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GRIMOIRE")}
        environment.update(PYTHONPATH=os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]),
                           HOME=str(home), USERPROFILE=str(home), GRIMOIRE_V2_HOME=str(home / ".grimoire-v2"))
        self.process = subprocess.Popen([sys.executable, "-m", "grimoire.app", "serve", *args, "--owner", OWNER,
                                         "--port", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        env=environment)
        seen = []
        while True:
            line = self.process.stdout.readline()
            if not line:
                self.process.wait(30)
                raise AssertionError("".join(seen) + self.process.stderr.read())
            seen.append(line)
            match = re.match(r"open http://127\.0\.0\.1:(\d+)/#token=(\S+)", line)
            if match:
                break
        self.port, self.token = int(match.group(1)), match.group(2)

    def request(self, method: str, path: str, body: bytes | None = None, *, token: bool = True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            headers[TOKEN_HEADER] = self.token
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def __call__(self, op: str, **args) -> dict:
        status, data = self.request("POST", f"/api/{op}", json.dumps(args).encode("utf-8"))
        answer = json.loads(data)
        if status != 200:
            raise Refused(status, answer)
        return answer

    def refused(self, reason: str, op: str, **args) -> Refused:
        with pytest.raises(Refused) as error:
            self(op, **args)
        assert error.value.reason == reason, (error.value.reason, error.value.message)
        assert error.value.message, "a refusal says what to do"  # step 9: actionable, never bare
        return error.value

    def picture(self, ref: str) -> bytes:
        status, data = self.request("GET", "/api/image?ref=" + quote(ref, safe=""))
        assert status == 200, data
        return data

    def stop(self) -> str:
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(30)
        self.process.stdout.close()
        errors = self.process.stderr.read()
        self.process.stderr.close()
        return errors


@contextlib.contextmanager
def serving(home: Path, *args: str):
    app = App(home, *args)
    try:
        yield app
    finally:
        errors = app.stop()
        assert "an op failed" not in errors, errors  # no op hit a bug (a 500) while the app ran


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def use_model(app: App, model) -> None:
    """Point the app's Models and costs settings at the loopback model (the same file across restarts)."""
    shown = app("providers.models")
    settings = {"provider_id": "local", "base_url": f"http://127.0.0.1:{model.server_address[1]}/v1",
                "model": "synthetic-model", "director": 1, "max_output_tokens": 1024}
    if shown["settings"] is not None:
        settings["max_output_tokens"] = shown["settings"]["max_output_tokens"]
    app("providers.save_models", settings=settings, expected=shown["etag"])


def canonical(library: Path) -> dict:
    return {root: snapshot(library / root) if (library / root).exists() else None for root in CANONICAL}


def play_turn(app: App, chat: str, text: str, *, mode: str = "speech") -> dict:
    """Post, have every NPC answer, keep the first take: the kept turn."""
    posted = app("play.post", chat=chat, text=text, mode=mode)
    turn = posted["state"]["open_turn"]
    assert turn, posted["state"]
    generated = app("generate", chat=chat)
    (offered,) = [record for record in generated["turns"] if record["turn"] == turn]
    take = offered["offered"][-1]["take"]
    kept = app("keep", chat=chat, turn=turn, take=take)
    (record,) = [record for record in kept["turns"] if record["turn"] == turn]
    assert record["kept"]["take"] == take
    return record


def same_chat(left: dict, right: dict) -> bool:
    keep = ("chat", "scope", "launch", "persona", "turns", "set_aside", "fork")
    return {key: left[key] for key in keep} == {key: right[key] for key in keep}


# -- a V0.2.1 library whose NPCs have text to speak from -----------------------------------------------------

V1_VOICES = {"Mara": "A tide-reader who keeps the drowned ledger. She speaks in short, salt-dry sentences.",
             "Seraphine": "A lamplighter of Saltmarch. She talks softly and asks questions back."}


def voiced_v1_library(root: Path) -> Path:
    """``import_fixtures``' two-NPC 0.2.1 library with a description for each NPC (the fixture's cards are blank,
    so its NPCs have nothing to speak from: 027's ``no_voice_text``)."""
    build_two_npc_library(root)
    swaps = {}
    for path in sorted(root.rglob("default.json")):
        old = path.read_text(encoding="utf-8")
        name = json.loads(old)["data"]["name"]
        if name in V1_VOICES:
            data = {"name": name, "description": V1_VOICES[name], "personality": "", "scenario": "", "first_mes": "",
                    "mes_example": "", "alternate_greetings": [], "tags": [], "extensions": {}}
            new = json.dumps({"spec": "chara_card_v3", "spec_version": "3.0", "data": data}, indent=2,
                             sort_keys=True, ensure_ascii=False) + "\n"
            path.write_text(new, encoding="utf-8")
            swaps[sha(old)] = sha(new)
    for path in root.rglob("appearances.json"):
        text = path.read_text(encoding="utf-8")
        for old, new in swaps.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    return root


SERAPHINE_CARD = card(name="Seraphine Lark", description="A lamplighter who distrusts smugglers.",
                      personality="Soft-spoken, curious, stubborn.", first_mes="Light's out.",
                      alternate_greetings=[], group_only_greetings=[], mes_example="<START>\n{{char}}: Mind the oil.")
SERAPHINE_CARD["data"].pop("character_book")


# -- the proof -------------------------------------------------------------------------------------------------

@pytest.fixture
def places(tmp_path):
    desk, phone, v1 = tmp_path / "desk-home", tmp_path / "phone-home", tmp_path / "v1-elsewhere"
    desk.mkdir()
    phone.mkdir()
    # a V1 library where V1 keeps one, beside where V2 would make its own: V2 must never pick it or touch it
    v1_home = build_two_npc_library(desk / ".grimoire")
    voiced_v1_library(v1)
    return desk, phone, v1, v1_home


def test_the_wave_7_release_proof(places):
    desk_home, phone_home, v1, v1_home = places
    v1_before, v1_home_before = snapshot(v1), snapshot(v1_home)
    settings = desk_home / "settings" / "model.json"
    phone_settings = phone_home / "settings" / "model.json"
    with model_server() as model:
        # 1. First run: a V2-isolated library, a model endpoint, no world.
        with serving(desk_home, "--model", str(settings)) as app:
            assert app.request("GET", "/", token=False)[0] == 200
            status = app("setup.status")
            suggested = Path(status["suggested"])
            assert not status["open"] and suggested == (desk_home / ".grimoire-v2").resolve()
            app.refused("no_library", "library")
            # V1's library is never checked, made, opened or written as a V2 one
            app.refused("v1_library", "setup.check", path=str(v1_home))
            app.refused("v1_library", "setup.create", path=str(v1_home), device="desktop")
            app.refused("v1_library", "setup.open", path=str(v1_home))
            assert app("setup.check", path=str(suggested))["state"] == "new"
            created = app("setup.create", path=str(suggested), device="desktop")
            assert created["open"] and created["device"] == "desktop" and created["generation"] is False
            library = suggested
            draft = {"provider_id": "local", "base_url": f"http://127.0.0.1:{model.server_address[1]}/v1",
                     "model": "synthetic-model", "director": 1}
            checked = app("providers.check_models", settings=draft)
            assert checked["ok"] and checked["sent"] is False and checked["local"]
            shown = app("providers.models")
            assert shown["generation"]["problem"]["code"] == "not_configured"
            app("providers.save_models", settings=draft, expected=shown["etag"])
            assert app("library")["generation"] is True
            assert app("sources.catalog")["sources"] == []  # no world was made, and none is needed
            assert model.requests == []  # setting a model up sends nothing

            # 2. Import a V3 card with its picture and lorebook; make a persona by hand; inspect both.
            preview = app("imports.card_preview", card=b64(encode(V3)), portrait=b64(PORTRAIT), origin="mara.json")
            paths = [row["path"] for row in preview["files"]]
            assert any(path.startswith("assets/blobs/") for path in paths)
            assert any("/records/lorebook/" in path for path in paths) and preview["report"]["losses"]
            assert app("sources.catalog")["sources"] == []  # a preview writes nothing
            app.refused("stale_approval", "imports.proposal_apply", proposal=preview["proposal"],
                        digest="sha256:" + "0" * 64)
            assert app("sources.catalog")["sources"] == []  # nor does an approval of other bytes
            app("imports.proposal_apply", proposal=preview["proposal"], digest=preview["digest"])
            mara_read = app("sources.read", ref="card:mara-vell/character/mara-vell")
            assert mara_read["revision"] == "r1" and mara_read["is_head"] and mara_read["effective"]["fields"]
            provenance = app("sources.read", ref="card:mara-vell/card-import/import")
            assert provenance["family"] == "other" and "chara_card_v3" in json.dumps(provenance["payload"])
            mine = app("sources.create_source", source="local:mine", title="My characters")
            app("sources.create", source="local:mine", kind="character", id="wren",
                fields={"name": "Wren", "description": "A courier new to Port Vell."}, expected=mine["manifest_etag"])
            listed = app("play.sources")
            refs = {item["name"]: item["ref"] for item in listed["characters"]}
            mara, wren = refs["Mara Vell"], refs["Wren"]
            assert [item["name"] for item in listed["lorebooks"]] == ["Harbor lore"]

            # 3. A world-less chat with a chosen greeting and persona; a second NPC joins after play began.
            options = app("play.options", kind="character", ref=mara)
            assert options["sources"] == [mara + "@r1"]  # what the chat will bind, pinned
            opening = next(item for item in options["openings"] if item["id"] != options["default_opening"]
                           and not item["group_only"])
            app.refused("no_persona", "play.start", kind="character", ref=mara)
            assert app("chats")["chats"] == []  # a refused start writes nothing
            started = app("play.start", kind="character", ref=mara, persona=wren, opening=opening["id"])
            chat = started["chat"]
            assert started["scope"]["world"] is None and started["persona"]["name"] == "Wren"
            assert started["turns"][0]["kept"]["lines"][0]["text"] == opening["text"]
            first = play_turn(app, chat, "I set the ledger on the table. The word on its cover is BRINE.")
            assert [line["speaker"] for line in first["kept"]["lines"]] == ["Mara Vell"]
            seraphine = app("imports.card_preview", card=b64(encode(SERAPHINE_CARD)), origin="seraphine.json")
            app("imports.proposal_apply", proposal=seraphine["proposal"], digest=seraphine["digest"])
            seraphine_ref = next(item["ref"] for item in app("play.sources")["characters"]
                                 if item["name"] == "Seraphine Lark")
            joined = app("play.add_card", chat=chat, ref=seraphine_ref)
            assert {item["name"]: item["role"] for item in joined["cast"]} == \
                {"Mara Vell": "npc", "Seraphine Lark": "npc", "Wren": "player"}
            heard = len(model.requests)

            # 4. Separate NPC calls in distinguishable voices, metered; keep a take; change a setting.
            second = play_turn(app, chat, "Who else is here tonight?")
            calls = model.requests[heard:]
            assert sorted(speakers(calls)) == ["Mara Vell", "Seraphine Lark"]  # one call per speaking NPC
            newcomer = calls[speakers(calls).index("Seraphine Lark")]
            assert "BRINE" not in json.dumps(newcomer) and opening["text"] not in json.dumps(newcomer)
            assert "Who else is here tonight?" in json.dumps(newcomer)  # she hears what is said from now on
            lines = {line["speaker"]: line["text"] for line in second["kept"]["lines"]}
            assert lines["Mara Vell"] != lines["Seraphine Lark"]
            assert lines["Seraphine Lark"].startswith("[Seraphine Lark]")
            usage = app("providers.usage", chat=chat)
            (metered,) = [turn for turn in usage["turns"] if turn["turn"] == second["turn"]]
            assert sorted(call["name"] for call in metered["calls"]) == ["Mara Vell", "Seraphine Lark"]
            for call in metered["calls"]:
                (attempt,) = call["attempts"]
                assert call["stage"] == "npc_generation" and attempt["measurement"] == "provider_reported"
                assert attempt["tokens"]["input_tokens"] and attempt["cost"] is None
                assert attempt["cost_state"] == "unpriced"  # unknown, never shown as free
            assert usage["total"]["attempts"] == len(model.requests)
            shown = app("providers.models")
            changed = {**shown["settings"], "max_output_tokens": 512}
            changed.pop("key_set", None)
            app.refused("changed", "providers.save_models", settings=changed, expected="sha256:" + "0" * 64)
            app("providers.save_models", settings=changed, expected=shown["etag"])
            before_restart = app("open", chat=chat)
        with serving(desk_home, str(library), "--model", str(settings)) as app:
            assert same_chat(app("open", chat=chat), before_restart)  # the same chat, after a restart
            assert app("providers.models")["settings"]["max_output_tokens"] == 512
            heard = len(model.requests)
            play_turn(app, chat, "*I give Mara the ledger*", mode="action")
            assert {request["max_tokens"] for request in model.requests[heard:]} == {512}
            action = app("open", chat=chat)["turns"][-1]

            # 5. Source context and events by audience; an effect accepted at the exact operations shown.
            hub = app("campaign.hub", chat=chat, audience="director")
            pins = {item["actor"]["name"]: item for item in hub["sources"]}
            assert pins["Mara Vell"]["pinned"] and pins["Mara Vell"]["resolved"] == mara + "@r1"
            actors = {item["name"]: item["actor"] for item in hub["cast"]}
            operation = {"kind": "begin", "predicate": "knows", "subject": actors["Mara Vell"],
                         "object": actors["Wren"], "agent": actors["Wren"], "backed_by": action["turn"]}
            app.refused("pc_authorship", "campaign.propose_effect", chat=chat, turn=action["turn"],
                        summary="Wren and Mara strike a deal.", operations=[{**operation, "backed_by": None}])
            proposed = app("campaign.propose_effect", chat=chat, turn=action["turn"],
                           summary="Wren and Mara strike a deal.", operations=[operation])["done"]["effect"]
            assert proposed["state"] == "proposed" and len(proposed["endorsers"]) == 1  # the player's own key
            app("campaign.approve_effect", chat=chat, turn=action["turn"], number=proposed["number"])
            held = app("campaign.hub", chat=chat, audience="director")
            assert held["truth"]["events"] == []  # the director's key alone accepts nothing
            app.refused("fingerprint_mismatch", "campaign.endorse_effect", chat=chat, turn=action["turn"],
                        number=proposed["number"], fingerprint="sha256:" + "0" * 64)
            assert app("campaign.hub", chat=chat, audience="director") == held  # nothing was written
            endorsed = app("campaign.endorse_effect", chat=chat, turn=action["turn"], number=proposed["number"],
                           fingerprint=proposed["fingerprint"])["done"]["effect"]
            assert endorsed["state"] == "accepted"
            director = app("campaign.hub", chat=chat, audience="director")
            (event,) = director["truth"]["events"]
            assert event["summary"] == "Wren and Mara strike a deal."
            table = app("campaign.hub", chat=chat)
            assert "truth" not in table and "goals" not in table  # world truth is the director's
            app.refused("director_only", "campaign.knowledge", chat=chat, actor=actors["Seraphine Lark"])
            own = app("campaign.knowledge", chat=chat, actor=actors["Wren"])
            assert own["audience"] == "table" and own["actors"][0]["actor"]["name"] == "Wren"
            theirs = app("campaign.knowledge", chat=chat, actor=actors["Seraphine Lark"], audience="director")
            assert theirs["perspective"] == "actor" and theirs["table_events"] == []

            # 6. A reviewed V0.2.1 import from a separate, read-only folder; play the migrated scene.
            app.refused("unsafe_paths", "imports.v1_scan", source=str(library), archive_id="itself")
            scan = app("imports.v1_scan", source=str(v1), archive_id="fixture")
            assert {row["code"] for row in scan["findings"]["rows"]} >= {"dangling_object", "dangling_ref"}
            assert scan["provider_calls"] == 0
            plan = app("imports.v1_preview", scan=scan["scan"])
            assert plan["report"]["unresolved"]["rows"] and plan["report"]["not_imported"]["rows"]
            heard = len(model.requests)
            app("imports.proposal_apply", proposal=plan["proposal"], digest=plan["digest"])
            assert len(model.requests) == heard  # an import calls no model
            assert app("imports.v1_receipts", archive_id="fixture")["runs"] == 1
            migrated = next(item["chat"] for item in app("chats")["chats"]
                            if item["scope"]["campaign"] == "the-drowned-ledger")
            opened = app("continue", chat=migrated)
            assert [line["speaker"] for line in opened["history"]] == ["Winifred", "Mara", "Seraphine"]
            assert opened["problems"] == []
            migrated_turn = play_turn(app, migrated, '"What happened to the ledger?"')
            assert {line["speaker"] for line in migrated_turn["kept"]["lines"]} == {"Mara", "Seraphine"}
            missing = app("media.art", subject="world:realm/character/mara.default")["slots"]
            assert [(slot["slot"], slot["state"], slot["reason"]) for slot in missing] == \
                [("avatar", "unavailable", "unresolved")]  # the dangling V1 picture stays named, not replaced
            adjusted = {slot["slot"]: slot for slot in
                        app("media.art", subject="campaign:the-drowned-ledger/character/mara")["slots"]}
            assert adjusted["avatar"]["state"] == "shown" and adjusted["avatar"]["provenance"]["legacy_id"]
            assert adjusted["gallery_1"]["state"] == "hidden"
        assert snapshot(v1) == v1_before and snapshot(v1_home) == v1_home_before  # V1 was only read
        with serving(desk_home, str(library), "--model", str(settings)) as app:
            resumed = app("open", chat=migrated)
            assert resumed["turns"][-1]["turn"] == migrated_turn["turn"] and resumed["state"]["seated"]
            play_turn(app, migrated, '"And the tide?"')
            assert app("imports.v1_receipts", archive_id="fixture")["runs"] == 1

        # 7. Art in an instance slot; pair two devices; sync the picture and the chat; settle an offline fork.
        with serving(phone_home, "--model", str(phone_settings)) as phone:
            phone_library = Path(phone("setup.status")["suggested"])
            phone("setup.create", path=str(phone_library), device="phone")
        sync_port = free_port()
        with serving(phone_home, str(phone_library), "--model", str(phone_settings), "--sync-port", str(sync_port)) \
                as phone, serving(desk_home, str(library), "--model", str(settings)) as app:
            use_model(phone, model)
            paired = app("media.pair", peer="phone", address=f"127.0.0.1:{sync_port}")
            assert paired["paired"] and len(paired["key"]) == 64
            phone("media.pair", peer="desktop", key=paired["key"])
            lamp = actors["Seraphine Lark"]
            art = app("media.art", subject=lamp)
            assert "set" in art["modes"]
            placed = app("media.upload", scope="campaign:" + chat.split(":", 1)[1].split("#", 1)[0],
                         media=b64(PORTRAIT_2), note="the lamplighter", origin="added in the app", subject=lamp,
                         slot="portrait", expected=None)
            picture = placed["image"]["image"]
            assert app.picture(picture) == PORTRAIT_2
            heard = len(model.requests)
            report = app("media.sync", peer="phone")
            assert report["status"] == "synced", report
            assert len(model.requests) == heard  # a sync calls no model
            there = phone("open", chat=chat)
            here = app("open", chat=chat)
            assert same_chat(there, here)
            (shown,) = [slot for actor in there["cast"] if actor["actor"] == lamp for slot in actor["art"]["slots"]
                        if slot["slot"] == "portrait"]
            assert shown["state"] == "shown" and phone.picture(shown["image"]) == PORTRAIT_2
            mine = play_turn(app, chat, '"Where is the lamp?"')
            assert there["state"]["seated"] is False  # the chat page shows "no seat yet" on a new device
            phone("continue", chat=chat)
            theirs = play_turn(phone, chat, '"Who lit the lamp?"')
            forked = app("media.sync", peer="phone")
            assert forked["status"] == "synced", forked  # two offline turns are a fork, not a lost write
            waiting = app("media.forks")
            (fork,) = [item for item in waiting["forks"] if item["chat"] == chat]
            branches = {branch["head"] for branch in fork["fork"]["branches"]}
            assert {mine["turn"], theirs["turn"]} <= branches, fork
            chosen = app("media.choose", chat=chat, turn=mine["turn"])
            assert [item["turn"] for item in chosen["set_aside"]] == [theirs["turn"]]  # reported, never dropped
            after = app("open", chat=chat)
            assert after["fork"] is None and theirs["turn"] in {item["turn"] for item in after["set_aside"]}
            assert app("media.sync", peer="phone")["status"] == "synced"
            assert same_chat(phone("open", chat=chat), app("open", chat=chat))

        # 8. The same canonical chat from Chats, from its character and from its scenario.
        with serving(desk_home, str(library), "--model", str(settings)) as app:
            read = app("sources.read", ref="local:mine/character/wren")
            scenario = app("sources.create", source="local:mine", kind="scenario", id="harbor-night",
                           fields={"title": "A night at the harbor", "premise": "Rain keeps everyone indoors.",
                                   "openings": [{"text": "The rain will not stop."}]},
                           expected=read["manifest_etag"])["ref"]
            app("play.add_card", chat=chat, ref=scenario.split("@", 1)[0], role="scenario")
            recent = [item["chat"] for item in app("chats")["chats"]]
            assert recent.count(chat) == 1
            by_character = app("chats", filter="character", source=mara)["chats"]
            by_scenario = app("chats", filter="scenario", source=scenario.split("@", 1)[0])["chats"]
            assert [item["chat"] for item in by_character] == [chat]
            assert [item["chat"] for item in by_scenario] == [chat]
            assert same_chat(app("open", chat=by_character[0]["chat"]), app("open", chat=by_scenario[0]["chat"]))

            # 9. No hidden model call, no write from a refusal, no fake card, no V1 change; refusals say why.
            before = canonical(library)
            heard = len(model.requests)
            app.refused("invalid_field", "play.post", chat=chat, text="", mode="speech")
            app.refused("unsupported_format", "imports.card_preview", card=b64(b'{"spec": "nonsense"}'))
            app.refused("no_chat", "open", chat=chat.rsplit("/", 1)[0] + "/t-000000000000")
            app.refused("invalid_selection", "play.start", kind="premise", persona=wren)
            app.refused("changed", "sources.save", ref="local:mine/character/wren", head="r1",
                        etag="sha256:" + "0" * 64, expected=read["manifest_etag"], fields={"description": "x"})
            app("imports.card_preview", card=b64(encode(card(name="Never Saved"))), origin="never.json")
            assert canonical(library) == before  # refusals and an unapproved preview wrote nothing
            assert len(model.requests) == heard
            assert "Never Saved" not in json.dumps(app("sources.catalog"))
            booked = sum(app("providers.usage", chat=one)["total"]["attempts"] for one in (chat, migrated))
            # every request the model ever got is a planned call booked on a chat (the phone's turn came over
            # with the sync): no model call happened anywhere else, in setup, import, sync or art
            assert len(model.requests) == booked
            assert app.request("POST", "/api/library", b"{}", token=False)[0] == 401
    assert snapshot(v1) == v1_before and snapshot(v1_home) == v1_home_before
