"""Trusted provider configuration, secrets, request bytes and the cancel token."""

import json
import math
import threading

import pytest

from grimoire.providers import (
    AdapterLimits, CancelToken, ChatMessage, EnvironmentCredentials, ModelRequest,
    ProviderEndpoint, ProviderRegistry, RouteChoice, Secret, StaticCredentials, Timeouts,
    envelope, open_envelope, request_body, request_digest, route_label,
)
from grimoire.security import payload_digest

KEY = "sk-test-0123456789"


def _request(**overrides):
    values = dict(messages=[ChatMessage("system", "Be brief."), ChatMessage("user", "Hello?")],
                  max_output_tokens=64)
    values.update(overrides)
    return ModelRequest(**values)


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://localhost:11434/v1",
                                 "http://[::1]:8080/v1", "https://openrouter.ai/api/v1"])
def test_http_endpoint_must_be_loopback(url):
    ProviderEndpoint("local", url)


@pytest.mark.parametrize("url", ["http://example.com/v1", "http://10.0.0.5:11434/v1",
                                 "ftp://127.0.0.1/v1", "openrouter.ai/v1", "https:///v1"])
def test_non_loopback_http_and_other_schemes_are_refused(url):
    with pytest.raises(ValueError):
        ProviderEndpoint("remote", url)


@pytest.mark.parametrize("url", ["https://user:pw@openrouter.ai/v1", "https://openrouter.ai/v1?x=1",
                                 "https://openrouter.ai/v1#frag"])
def test_endpoint_rejects_userinfo_query_fragment(url):
    with pytest.raises(ValueError):
        ProviderEndpoint("remote", url)


def test_endpoint_and_route_validation():
    for bad in ("", "Open Router", "../x", "secrets/x", 7):
        with pytest.raises(ValueError):
            ProviderEndpoint(bad, "https://openrouter.ai/api/v1")
    with pytest.raises(ValueError):
        ProviderEndpoint("x", "https://openrouter.ai/api/v1", max_tokens_field="tokens")
    for model in ("", "gpt 4", "llamaé", "m\n", 3):
        with pytest.raises(ValueError):
            RouteChoice("x", model)
    with pytest.raises(ValueError):
        ChatMessage("narrator", "hi")
    with pytest.raises(ValueError):
        ChatMessage("user", b"hi")
    for bad in (0, -1, math.inf, math.nan, True):
        with pytest.raises(ValueError):
            Timeouts(connect=bad)
    with pytest.raises(ValueError):
        AdapterLimits(max_line_bytes=0)
    slash = ProviderEndpoint("x", "https://openrouter.ai/api/v1/")
    assert slash.completions_url == "https://openrouter.ai/api/v1/chat/completions"
    with pytest.raises(ValueError):
        ProviderRegistry([slash, ProviderEndpoint("x", "https://other.example/v1")])
    registry = ProviderRegistry([slash])
    assert registry.get("x") is slash
    with pytest.raises(KeyError):
        registry.get("y")


@pytest.mark.parametrize("bad", ["sk- x", "sk x", "sk\r\nx", "", "sk-é", "sk\x7fx"])
def test_secret_is_redacted_and_ascii_only(bad):
    with pytest.raises(ValueError) as caught:
        Secret(bad)
    assert bad.strip() == "" or bad not in str(caught.value)
    secret = Secret(KEY)
    assert KEY not in repr(secret) and KEY not in str(secret) and KEY not in f"{secret!r}"
    assert secret.reveal() == KEY


def test_route_label_shows_host():
    local = ProviderEndpoint("local", "http://127.0.0.1:11434/v1", needs_key=False)
    assert route_label(local, "llama3") == "local@127.0.0.1:11434/llama3"
    v6 = ProviderEndpoint("x", "http://[::1]:8080/v1")
    assert route_label(v6, "m") == "x@[::1]:8080/m"
    hosted = ProviderEndpoint("openrouter", "https://openrouter.ai/api/v1")
    assert route_label(hosted, "a/b") == "openrouter@openrouter.ai/a/b"
    assert hosted.loopback_literal is False and local.loopback_literal is True


def test_body_is_deterministic_ascii_and_bounded():
    endpoint = ProviderEndpoint("x", "https://openrouter.ai/api/v1")
    request = _request(messages=[ChatMessage("user", "Café 😀")], temperature=0.7)
    body = request_body(request, endpoint, "m")
    assert body == request_body(request, endpoint, "m")
    assert body.isascii()
    decoded = json.loads(body)
    assert decoded == {
        "model": "m", "messages": [{"role": "user", "content": "Café 😀"}],
        "stream": True, "stream_options": {"include_usage": True}, "max_tokens": 64,
        "temperature": 0.7,
    }
    reasoning = ProviderEndpoint("o", "https://api.openai.com/v1",
                                 max_tokens_field="max_completion_tokens")
    assert json.loads(request_body(_request(), reasoning, "m"))["max_completion_tokens"] == 64
    lone = request_body(_request(messages=[ChatMessage("user", "\ud800")]), endpoint, "m")
    assert lone.isascii()
    for bad in (True, 0, -3, 1.5, None):
        with pytest.raises(ValueError):
            _request(max_output_tokens=bad)
    for bad in (math.nan, math.inf, "0.5", True):
        with pytest.raises(ValueError):
            _request(temperature=bad)
    with pytest.raises(ValueError):
        _request(messages=[])
    with pytest.raises(ValueError):
        _request(messages="hello")


def test_request_copies_its_messages():
    messages = [ChatMessage("user", "zxq-one")]
    request = _request(messages=messages)
    messages.append(ChatMessage("user", "two"))
    assert len(request.messages) == 1
    assert request_digest(request) == request_digest(_request(messages=[ChatMessage("user", "zxq-one")]))
    assert request_digest(request) != request_digest(_request(messages=[ChatMessage("user", "two")]))
    assert "zxq-one" not in repr(request)


def test_envelope_round_trips_and_binds_url():
    body = b'{"model":"m"}'
    one = envelope("https://a.example/v1/chat/completions", body)
    other = envelope("https://b.example/v1/chat/completions", body)
    assert open_envelope(one) == ("https://a.example/v1/chat/completions", body)
    assert payload_digest(one) != payload_digest(other)
    assert payload_digest(one) != payload_digest(envelope("https://a.example/v1/chat/completions",
                                                          b'{"model":"n"}'))
    assert one.isascii()


def test_environment_credentials_read_at_call_time(monkeypatch):
    credentials = EnvironmentCredentials({"openrouter": "GRIMOIRE_TEST_KEY"})
    monkeypatch.delenv("GRIMOIRE_TEST_KEY", raising=False)
    assert credentials.api_key("openrouter") is None
    monkeypatch.setenv("GRIMOIRE_TEST_KEY", KEY)
    assert credentials.api_key("openrouter").reveal() == KEY
    assert credentials.api_key("other") is None
    assert KEY not in repr(credentials)
    static = StaticCredentials({"x": KEY})
    assert static.api_key("x").reveal() == KEY and KEY not in repr(static)


def test_cancel_token_runs_late_callback_and_wait_returns_on_cancel():
    token = CancelToken()
    seen = []
    handle = token._on_cancel(lambda: seen.append("early"))
    assert token.wait(0) is False
    timer = threading.Timer(0.05, token.cancel)
    timer.start()
    assert token.wait(10) is True
    timer.join()
    assert token.cancelled and seen == ["early"]
    token._clear(handle)
    token._on_cancel(lambda: seen.append("late"))
    assert seen == ["early", "late"]
    token.cancel()
    assert seen == ["early", "late"]


def test_cancel_callbacks_may_touch_the_token():
    token = CancelToken()
    token._on_cancel(lambda: token._on_cancel(lambda: None))
    token.cancel()
    assert token.cancelled
