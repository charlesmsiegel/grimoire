"""Quick replies: entry validation, the world and campaign sets on disk, their
layering, and the routes (store/quick_replies.py, routes/quick_replies.py)."""

import json
import re

import pytest

from grimoire.store import quick_replies as qr


def test_send_is_normalised_to_its_fields_and_gets_an_id():
    out = qr.normalize({"label": " Look around ", "kind": "send", "text": "I take in the room.",
                        "notation": "2d6", "task": None}, campaign=False)
    assert set(out) == {"id", "label", "kind", "text", "mode"}
    assert out["label"] == "Look around" and out["mode"] == "send"
    assert re.fullmatch(r"[0-9a-f]{32}", out["id"])


def test_text_is_stored_unstripped_and_a_given_id_is_kept():
    out = qr.normalize({"id": "keep-me_1", "label": "L", "kind": "direct",
                        "text": "  steer gently\n", "mode": "insert"}, campaign=False)
    assert out == {"id": "keep-me_1", "label": "L", "kind": "direct",
                   "text": "  steer gently\n", "mode": "insert"}


def test_roll_label_is_absent_when_empty_and_collapsed_when_given():
    out = qr.normalize({"label": "Roll", "kind": "roll", "notation": " 2d6+3 "}, campaign=False)
    assert "roll_label" not in out and out["notation"] == "2d6+3"
    assert qr.normalize({"label": "Roll", "kind": "roll", "notation": "1d20",
                         "roll_label": "Perception\n\ncheck"}, campaign=False)["roll_label"] == "Perception check"


def test_task_and_opener_carry_only_their_fields():
    assert set(qr.normalize({"label": "T", "kind": "task", "task": "scene_break", "text": "x"},
                            campaign=False)) == {"id", "label", "kind", "task"}
    assert set(qr.normalize({"label": "O", "kind": "opener", "mode": "send"},
                            campaign=False)) == {"id", "label", "kind"}


@pytest.mark.parametrize("bad", [
    {"label": "x" * 41, "kind": "send", "text": "hi"},
    {"label": "", "kind": "send", "text": "hi"},
    {"label": "   ", "kind": "send", "text": "hi"},
    {"label": 7, "kind": "send", "text": "hi"},
    {"label": "Hi", "kind": "send", "text": "x" * 2001},
    {"label": "Hi", "kind": "direct", "text": "   "},
    {"label": "Hi", "kind": "send"},
    {"label": "Hi", "kind": "send", "text": "hi", "mode": "later"},
    {"label": "Hi", "kind": "roll", "notation": "two dice"},
    {"label": "Hi", "kind": "roll"},
    {"label": "Hi", "kind": "roll", "notation": "1d6", "roll_label": "x" * 81},
    {"label": "Hi", "kind": "task", "task": "absorb"},
    {"label": "Hi", "kind": "teleport"},
    {"label": "Hi"},
    {"id": "has space", "label": "Hi", "kind": "opener"},
    {"id": 5, "label": "Hi", "kind": "opener"},
    "not an object",
])
def test_rule_violations_are_quick_reply_errors(bad):
    with pytest.raises(qr.QuickReplyError) as exc:
        qr.normalize(bad, campaign=False)
    assert exc.value.code == "invalid_quick_reply"


def test_plugin_is_refused_with_its_own_code():
    with pytest.raises(qr.QuickReplyError) as exc:
        qr.normalize({"label": "Run", "kind": "plugin", "command": "x"}, campaign=False)
    assert exc.value.code == "plugin_api_unavailable"


def test_hide_entries_are_exact_and_campaign_only():
    assert qr.normalize({"id": "abc", "hidden": True}, campaign=True) == {"id": "abc", "hidden": True}
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"id": "abc", "hidden": True, "label": "x"}, campaign=True)
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"hidden": True}, campaign=True)
    with pytest.raises(qr.QuickReplyError):
        qr.normalize({"id": "abc", "hidden": True}, campaign=False)


def test_set_rules():
    one = {"id": "a", "label": "A", "kind": "opener"}
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set([one, dict(one)], campaign=False)          # duplicate id
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set([{"label": f"R{i}", "kind": "opener"} for i in range(51)], campaign=False)
    with pytest.raises(qr.QuickReplyError):
        qr.validate_set({"replies": []}, campaign=False)            # not a list
    assert len(qr.validate_set([{"label": f"R{i}", "kind": "opener"} for i in range(50)],
                               campaign=False)) == 50
