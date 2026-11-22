"""The 0.2.1 driver's request accounting (its pure part; the driver imports 0.2.1 lazily)."""

import gzip
import importlib.util
import json
import re
import zlib
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parents[1] / "tools" / "v021_driver.py"


def driver():
    spec = importlib.util.spec_from_file_location("v021_driver_under_test", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(*attempts):
    return [{"kind": "llm", "task": "chat", **({"attempts": a} if a != 1 else {})}
            for a in attempts] + [{"kind": "rename"}]


def test_a_retried_call_is_accounted_not_fatal():
    d = driver()
    d.check_accounting(rows(1, 2, 1), forwarded=4, planned=3, budget=9)


@pytest.mark.parametrize("forwarded, attempts, says", [
    (2, (1, 1), "fewer"),            # a planned call never went out
    (4, (1, 1, 1), "do not add up"),  # a request no row accounts for
    (10, (4, 3, 3), "budget"),
])
def test_unaccounted_requests_fail(forwarded, attempts, says):
    d = driver()
    with pytest.raises(d.DriverError, match=says):
        d.check_accounting(rows(*attempts), forwarded=forwarded, planned=3, budget=9)


def test_partial_ledger_is_counts_only():
    d = driver()
    summary = d.partial_ledger([{"kind": "llm", "task": "chat", "response_id": "r1",
                                 "prompt_tokens": 10, "completion_tokens": 2, "status": "ok",
                                 "model": "m", "content": "secret text"}])
    assert summary == [{"task": "chat", "response_id": "r1", "attempts": 1, "status": "ok",
                        "prompt_tokens": 10, "completion_tokens": 2, "cache_read_tokens": None,
                        "cache_write_tokens": None, "cost_usd": None}]


def test_any_driver_crash_reports_the_partial_ledger(tmp_path, monkeypatch, capsys):
    d = driver()
    usage = tmp_path / "usage"
    usage.mkdir()
    (usage / "2026-10.jsonl").write_text('{"kind": "llm", "task": "chat", "prompt_tokens": 5}\n')
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))

    def crash(argv):
        raise KeyError("unexpected")
    monkeypatch.setattr(d, "main", crash)
    with pytest.raises(SystemExit) as exit_:
        d.run(["--whatever"])
    assert exit_.value.code == 1
    err = capsys.readouterr().err
    assert "partial ledger" in err and '"prompt_tokens": 5' in err


def test_evidence_is_written_as_ascii_json_whatever_the_locale():
    # A Windows pipe uses the locale code page; escaped JSON is the same bytes everywhere.
    d = driver()
    data = d.encode_bundle({"text": "“Hush,” she said — ¡olé! 鍵"})
    assert data.isascii()
    import json
    assert json.loads(data.decode("ascii"))["text"] == "“Hush,” she said — ¡olé! 鍵"


# 0.2.1's speaker-label matcher at the pinned commit (store/scenes/serialize.py), for these
# tests only; the driver passes 0.2.1's own function.
_MARKER = re.compile(r"^\*\*([^*\n]{1,64}?)(?: \(([^)\n]+)\))?:\*\*[ ]?", re.MULTILINE)


def markers(body):
    return [m for m in _MARKER.finditer(body)
            if m.start() == 0 or body[max(0, m.start() - 2):m.start()] == "\n\n"]


def stream(*frames):
    return "".join(f"id: {i}\ndata: {json.dumps(f)}\n\n" for i, f in enumerate(frames))


ACTORS = {"characters:oda": "npc-oda", "characters:bellamy": "npc-bellamy"}
LABELS = {"Oda": "npc-oda", "Bellamy": "npc-bellamy", "Wren": "pc-wren"}


def read(text):
    """A stand-in for 0.2.1's own reading: the prose before a handoff fence, which is named."""
    prose, fence, _ = text.partition("```handoff")
    return prose.strip(), ["handoff"] if fence else []


def describe(d, text, calls=(), **kwargs):
    return d.turn_failure(text, ACTORS, calls=list(calls), labels=LABELS, markers=markers,
                          read=kwargs.pop("read", read), **kwargs)


def test_turn_failure_names_the_kind_and_each_reply_s_shape():
    d = driver()
    text = stream(
        {"response_start": {"id": "r1", "actor_ref": "characters:oda"}},
        {"delta": "Coin first."}, {"response_end": {"id": "r1"}},
        {"response_start": {"id": "r2", "actor_ref": "characters:bellamy"}},
        {"delta": "\n\n"},
        {"error": {"kind": "empty_response", "detail": "The model returned no response prose."}})
    calls = [(429, None), (200, "Coin first.\n\n```handoff\n{\"next\": null}\n```"),
             (200, "\n\n**Wren:** I read it.\n\n**Mystery (aside):** Hm."),
             (200, "```handoff\n{}\n```\n\n**Wren:** after the fence"),
             (200, None), (None, None)]
    assert describe(d, text, calls) == (
        "0.2.1 error empty_response; npc-oda's reply: 11 characters streamed; "
        "npc-bellamy's reply: 2 characters streamed; "
        "provider reply 1: HTTP 429; "
        "provider reply 2: 42 characters, 11 of prose, fences handoff, prose under no label; "
        "provider reply 3: 46 characters, 46 of prose, no fence, "
        "prose under the labels pc-wren, another name; "
        "provider reply 4: 44 characters, 0 of prose, fences handoff, prose under no label; "
        "provider reply 5: unreadable; provider reply 6: no response")


def test_labels_are_read_from_the_prose_0_2_1_reads():
    # 0.2.1 expands {{user}} before its authority check, so that line is the player's.
    d = driver()
    said = describe(d, "", [(200, "**{{user}}:** I take it.")],
                    read=lambda text: (text.replace("{{user}}", "Wren").strip(), []))
    assert said.endswith("provider reply 1: 24 characters, 20 of prose, no fence, "
                         "prose under the label pc-wren")


def test_a_reply_that_cannot_be_read_still_gets_described():
    d = driver()

    def broken(text):
        raise ValueError("secret")

    said = describe(d, "", [(200, "secret words")], read=broken)
    assert said == "0.2.1 error not reported; provider reply 1: 12 characters, not parsed"


def test_turn_failure_never_echoes_text():
    d = driver()
    text = stream({"response_start": {"actor_ref": "characters:stranger"}},
                  {"delta": "**You:** secret words"},
                  {"error": {"kind": "Secret Words!", "detail": "secret words"}},
                  "not a frame", {"error": "secret"})
    text += "data: {not json\n"
    said = describe(d, text, [(200, "**You:** secret words")])
    assert said == ("0.2.1 error unrecognised; unknown's reply: 21 characters streamed; "
                    "provider reply 1: 21 characters, 21 of prose, no fence, "
                    "prose under the label you")
    assert "secret" not in said.lower()


def test_a_refused_turn_reports_0_2_1_s_kind():
    d = driver()
    body = json.dumps({"detail": {"kind": "scene_busy", "message": "secret"}})
    assert describe(d, body, status=409) == "0.2.1 error scene_busy"
    assert describe(d, "<html>secret</html>", status=502) == "0.2.1 error unrecognised"


def test_turn_failure_with_nothing_reported():
    d = driver()
    assert describe(d, "") == "0.2.1 error not reported"


FRAMES = {
    "anthropic": [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a"}},
                  {"type": "content_block_delta", "delta": {"type": "input_json_delta"}},
                  {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "b"}}],
    "openrouter": [{"choices": [{"delta": {"content": "a"}}]}, {"choices": []},
                   {"choices": [{"delta": {"content": "b"}}]}],
}


@pytest.mark.parametrize("provider", sorted(FRAMES))
@pytest.mark.parametrize("encoding", ["", "identity", "gzip", "deflate", "raw-deflate"])
def test_provider_text_reads_the_streamed_reply(provider, encoding):
    d = driver()
    body = ("".join(f"data: {json.dumps(f)}\n\n" for f in FRAMES[provider])
            + "data: [DONE]\n\n").encode()
    if encoding == "gzip":
        body = gzip.compress(body)
    elif encoding == "deflate":
        body = zlib.compress(body)
    elif encoding == "raw-deflate":
        packer = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        body, encoding = packer.compress(body) + packer.flush(), "deflate"
    assert d.provider_text({"body": bytearray(body), "encoding": encoding}, provider) == "ab"


def test_provider_text_refuses_what_it_cannot_decode():
    d = driver()
    assert d.provider_text({"body": None}, "anthropic") is None
    assert d.provider_text({"body": bytearray(b"x"), "encoding": "br"}, "anthropic") is None
    assert d.provider_text({"body": bytearray(b"not gzip"), "encoding": "gzip"},
                           "anthropic") is None
