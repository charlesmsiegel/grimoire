"""Unit tests for the terminal wire logger (`observability.wire_log`)."""

from __future__ import annotations

import io
import logging
import sys

import pytest
from pydantic import BaseModel

from grimoire.observability import wire_log


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("verbose", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("off", False),
        ("Off", False),
        ("", False),
        ("  off  ", False),
    ],
)
def test_enabled_parses_env(monkeypatch: pytest.MonkeyPatch, value: str, expected: bool) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", value)
    assert wire_log.enabled() is expected


def test_enabled_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRIMOIRE_WIRE_LOG", raising=False)
    assert wire_log.enabled() is True


def test_sanitize_replaces_bytes_recursively() -> None:
    payload = {
        "a": b"12",
        "b": [b"345", {"c": bytearray(b"6789")}],
        "d": "ok",
        "e": 5,
        "f": (b"xy", "z"),
    }
    assert wire_log._sanitize(payload) == {
        "a": "<bytes: 2>",
        "b": ["<bytes: 3>", {"c": "<bytes: 4>"}],
        "d": "ok",
        "e": 5,
        # tuples are normalized to lists
        "f": ["<bytes: 2>", "z"],
    }


def test_jsonable_dumps_pydantic_and_sanitizes_bytes() -> None:
    class _Req(BaseModel):
        prompt: str
        blob: bytes

    assert wire_log._jsonable(_Req(prompt="hi", blob=b"abc")) == {
        "prompt": "hi",
        "blob": "<bytes: 3>",
    }


def test_dump_falls_back_to_repr_for_unserializable() -> None:
    class _Boom:
        def __repr__(self) -> str:
            return "<boom>"

    # default=str handles most objects; a non-dict/list/model object still
    # round-trips through json via default=str rather than raising.
    assert "boom" in wire_log._dump(_Boom())


def test_stdout_handler_writes_to_current_stdout() -> None:
    """The handler must resolve ``sys.stdout`` at emit time, not construction.

    pytest swaps and closes stdout between tests; binding the stream once
    (like ``logging.StreamHandler``) would write to a stale, closed stream.
    """
    handler = wire_log._StdoutHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("t", logging.INFO, __file__, 0, "hello-stream", None, None)

    captured = io.StringIO()
    original = sys.stdout
    sys.stdout = captured
    try:
        handler.emit(record)
    finally:
        sys.stdout = original

    assert "hello-stream" in captured.getvalue()


def test_log_request_writes_payload_when_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", "1")
    wire_log.log_request("llm.complete", payload={"prompt": "hello"}, task="dialogue")
    out = capsys.readouterr().out
    assert ">> llm.complete request" in out
    assert "task=dialogue" in out
    assert "hello" in out


def test_log_response_writes_payload_when_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", "1")
    wire_log.log_response("llm.complete", payload={"text": "world"}, task="dialogue")
    out = capsys.readouterr().out
    assert "<< llm.complete response" in out
    assert "world" in out


def test_log_error_writes_error_when_enabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", "1")
    wire_log.log_error("imagegen", error="ProviderError: down", backend="sd")
    out = capsys.readouterr().out
    assert "!! imagegen error" in out
    assert "backend=sd" in out
    assert "ProviderError: down" in out


def test_log_request_sanitizes_bytes_in_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", "1")
    wire_log.log_request("imagegen", payload={"image": b"\x00\x01\x02\x03"})
    out = capsys.readouterr().out
    assert "<bytes: 4>" in out
    # raw bytes must not be dumped as escaped content
    assert "\\u0000" not in out


def test_meta_drops_none_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", "1")
    wire_log.log_request("llm.complete", payload={}, task="t", campaign_id=None, model="gpt")
    out = capsys.readouterr().out
    assert "task=t" in out
    assert "model=gpt" in out
    assert "campaign_id" not in out


def test_log_functions_are_noops_when_disabled(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GRIMOIRE_WIRE_LOG", "0")
    wire_log.log_request("llm.complete", payload={"x": 1})
    wire_log.log_response("llm.complete", payload={"x": 1})
    wire_log.log_error("llm.complete", error="boom")
    assert capsys.readouterr().out == ""
