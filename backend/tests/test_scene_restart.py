"""035's exit gate across real process restarts: every phase runs in a new Python process that is killed.

``test_scene_proof.py`` reopens in the same interpreter after clean closes. Here each phase runs this file as
a fresh ``sys.executable`` process and ends with ``os._exit``: no store, lock, authority or runtime is closed,
as when the app is killed. One phase is killed inside ``generate``, after its ``running`` take is written and
before the model answers. The three chats (a migrated V1 campaign, a world-less card chat and a scenario-only
chat) go on in the next process exactly where they stopped, and the scenario-only chat gets a new director
reply. Synthetic libraries; every model call is a scripted fake.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SRC = TESTS.parent / "src"


def _usage(record) -> int:
    return sum(a.usage.input_tokens or 0 for t in record.takes for c in t.calls for a in c.attempts)


def _dump(record) -> str:
    """Everything persisted about a turn, for an exact before/after comparison."""
    from grimoire.scene import turn_to_payload
    return json.dumps(turn_to_payload(record), sort_keys=True)


def _done(out: Path, result: dict) -> None:
    out.write_text(json.dumps(result), encoding="utf-8")
    os._exit(0)  # killed: nothing is closed


def phase(name: str, root: Path, out: Path) -> None:
    """One process's part. Each writes its findings to ``out`` and is killed, never closed."""
    from grimoire.core.identity import ChatRef
    from scene_fixtures import (ANA, MARA_EXAMPLE, MARA_VOICE, SERAPHINE_EXAMPLE, V1_CHAT, VELL_EXAMPLE, Table,
                                attach_tamsin, say)
    chats = json.loads((root / "chats.json").read_text(encoding="utf-8"))
    card, storm = ChatRef(*chats["card"]), ChatRef(*chats["storm"])
    result: dict[str, object] = {}
    if name == "play":
        v1 = Table(root / "v2", [say("Counted, and short by three."), say("Ask the lamps, they saw it go."),
                                 say("Then we dig at dawn."), say("Something stirs below the quay.")],
                   voices=MARA_VOICE)
        runner, ana = v1.runner, v1.player()
        runner.seat(V1_CHAT, ANA)
        runner.post(V1_CHAT, ana, '"Where is the ledger?"')
        runner.generate(V1_CHAT, ana)
        runner.keep(V1_CHAT, ana, 1)
        runner.post(V1_CHAT, ana, 'I whisper to Mara, "The ledger is under the floor."')
        runner.generate(V1_CHAT, ana)
        runner.keep(V1_CHAT, ana, 1)
        runner.post(V1_CHAT, ana, '"And the debts?"')  # saved, never answered before the kill
        result["v1"] = [_dump(r) for r in runner.open(V1_CHAT).records]
        cards = Table(root / "cards", [say("Dark, and the harbor pays for it."),
                                       say("Lightning shows a ship with no lamps.")])
        runner, ana = cards.runner, cards.player()
        runner.seat(card, ANA)
        runner.begin(card, ana)
        runner.keep(card, ana, 1)
        runner.post(card, ana, '"Is the lighthouse still dark?"')
        runner.generate(card, ana)
        runner.keep(card, ana, 1)
        runner.post(card, ana, 'I whisper to Mara Vell, "The ledger is under the floor."')
        runner.pass_turn(card, ana)
        result["card"] = [_dump(r) for r in runner.open(card).records]
        runner.seat(storm, ANA)
        runner.begin(storm, ana)
        runner.keep(storm, ana, 1)
        runner.post(storm, ana, "*I shake the rain from my coat*")
        runner.generate(storm, ana, director=True)
        runner.keep(storm, ana, 1)
        runner.post(storm, ana, '"Whose ship is that?"')
        result["storm"] = [_dump(r) for r in runner.open(storm).records]
        _done(out, result)
    if name == "resume":
        before = json.loads((root / "play.json").read_text(encoding="utf-8"))
        v1 = Table(root / "v2", [say("Paid in salt, every one."), say("The lamps keep their counsel.")],
                   voices=MARA_VOICE)
        runner, ana = v1.runner, v1.player()
        scene = runner.open(V1_CHAT)
        result["v1_exact"] = [_dump(r) for r in scene.records] == before["v1"]
        result["v1_pending"] = scene.pending is not None and scene.pending.post.text == "And the debts?"
        runner.generate(V1_CHAT, ana)
        prompts = v1.harness.prompts()
        mara = [p for p in prompts if MARA_EXAMPLE in p]
        seraphine = [p for p in prompts if SERAPHINE_EXAMPLE in p]
        result["v1_whisper_only_mara"] = (len(mara) == 1 and len(seraphine) == 1 and "under the floor" in mara[0]
                                          and "under the floor" not in seraphine[0])
        runner.keep(V1_CHAT, ana, 1)
        cards = Table(root / "cards", [say("Noted, and filed."), say("Papers first, then gossip."),
                                       say("A Harbor Guild hull, by the lines of her.")])
        runner, ana = cards.runner, cards.player()
        result["card_exact"] = [_dump(r) for r in runner.open(card).records] == before["card"]
        attach_tamsin(cards.store, cards.owner, card)
        runner.seat(card, ANA)
        runner.post(card, ana, '"Who keeps the customs house now?"')
        take = runner.generate(card, ana)
        prompts = cards.harness.prompts()
        late = [p for p in prompts if "Every crate has a story" in p]
        vell = [p for p in prompts if VELL_EXAMPLE in p]
        result["card_late_joiner_blind"] = (len(take.lines) == 2 and "under the floor" in vell[0]
                                            and "under the floor" not in late[0])
        runner.keep(card, ana, 1)
        scene = runner.open(storm)
        result["storm_exact"] = [_dump(r) for r in scene.records] == before["storm"]
        result["storm_pending"] = scene.pending is not None and scene.pending.post.text == "Whose ship is that?"
        take = runner.generate(storm, ana, director=True)
        result["storm_director_after_restart"] = (len(take.lines) == 1 and take.lines[0].actor is None
                                                  and take.lines[0].text == "A Harbor Guild hull, by the lines of her.")
        runner.keep(storm, ana, 1)
        runner.post(card, ana, '"One more thing."')
        out.write_text(json.dumps(result), encoding="utf-8")
        runner.runtime.run_turn = lambda *args, **kwargs: os._exit(0)  # killed inside generate
        runner.generate(card, ana)
        raise SystemExit("unreachable: generate returned after its process was killed")
    if name == "after_kill":
        cards = Table(root / "cards", [say("Mind the tide.")])
        runner, ana = cards.runner, cards.player()
        scene = runner.open(card)
        result["kill_seen_interrupted"] = scene.interrupted and ("interrupted", scene.pending.turn_id) in scene.problems
        take = runner.generate(card, ana)
        pending = runner.open(card).pending
        result["kill_superseded_then_answered"] = ([t.state for t in pending.takes] == ["superseded", "proposed"]
                                                   and [line.text for line in take.lines] == ["Mind the tide."])
        killed = pending.takes[0]
        result["killed_run_usage_unknown"] = killed.state == "superseded" and not killed.attempts()
        records = runner.open(card).records
        result["usage_on_called_turns_only"] = [_usage(r) > 0 for r in records] == [False, True, False, True, True]
        result["storm_continued"] = [r.kept is not None for r in runner.open(storm).records] == [True, True, True]
        _done(out, result)
    raise SystemExit(f"unknown phase {name}")


def _run(name: str, root: Path) -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(TESTS), str(SRC), *filter(None, [os.environ.get("PYTHONPATH")])])}
    out = root / f"{name}.json"
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), name, str(root), str(out)], env=env,
                          capture_output=True, text=True, timeout=300, cwd=str(root))
    assert proc.returncode == 0 and out.exists(), f"phase {name}: rc={proc.returncode}\n{proc.stderr[-3000:]}"


def test_three_chats_survive_being_killed_between_and_inside_calls(tmp_path):
    from scene_fixtures import card_library, v1_library
    v1_library(tmp_path)
    _, card, storm = card_library(tmp_path)
    (tmp_path / "chats.json").write_text(json.dumps({"card": [card.campaign_id, card.chat_id],
                                                     "storm": [storm.campaign_id, storm.chat_id]}), encoding="utf-8")
    _run("play", tmp_path)
    _run("resume", tmp_path)
    resumed = json.loads((tmp_path / "resume.json").read_text(encoding="utf-8"))
    assert resumed == dict.fromkeys(resumed, True) and len(resumed) == 8, resumed
    _run("after_kill", tmp_path)
    after = json.loads((tmp_path / "after_kill.json").read_text(encoding="utf-8"))
    assert after == dict.fromkeys(after, True) and len(after) == 5, after


if __name__ == "__main__":
    phase(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
