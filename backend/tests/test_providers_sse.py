"""OpenAI-compatible SSE parsing and usage normalization (no I/O)."""

import json

import pytest

from grimoire.core.inference import TokenUsage
from grimoire.providers import ProviderError
from grimoire.providers.sse import (
    StreamState, classify_non_sse, feed_line, finished, normalize_usage,
)


def data(obj):
    return b"data: " + json.dumps(obj).encode() + b"\n"


def chunk(content=None, finish=None, **extra):
    return data({"choices": [{"delta": {"content": content}, "finish_reason": finish}], **extra})


def feed(*lines):
    state = StreamState()
    deltas = [feed_line(state, line) for line in lines]
    return state, [d for d in deltas if d is not None]


def test_null_finish_reason_is_not_finished():
    state, deltas = feed(chunk("Hi"), chunk(" there"))
    assert deltas == ["Hi", " there"]
    assert state.finish_reason is None and not finished(state)
    state, _ = feed(chunk("Hi"), chunk(None, "stop"))
    assert finished(state) and state.finish_reason == "stop"
    state, _ = feed(chunk("Hi", ""))
    assert not finished(state)


def test_usage_only_final_chunk_is_recorded():
    usage = {"prompt_tokens": 12, "completion_tokens": 3}
    state, _ = feed(chunk("x", usage=None), chunk(None, "stop", usage=None),
                    data({"choices": [], "usage": usage}), b"data: [DONE]\n")
    assert state.usage_block == usage and state.done


def test_null_content_and_null_usage_are_ignored():
    usage = {"prompt_tokens": 5, "completion_tokens": 1}
    state, deltas = feed(data({"choices": [{"delta": {"role": "assistant", "content": None},
                                            "finish_reason": None}]}),
                         data({"choices": [], "usage": usage}), chunk("late", usage=None))
    assert deltas == ["late"]
    assert state.usage_block == usage


def test_cached_tokens_are_a_slice_of_prompt():
    usage, note = normalize_usage({"prompt_tokens": 100, "completion_tokens": 20,
                                   "prompt_tokens_details": {"cached_tokens": 40},
                                   "completion_tokens_details": {"reasoning_tokens": 5}})
    assert usage == TokenUsage(100, 20, cache_read_tokens=40, reasoning_tokens=5)
    assert note is None


def test_sum_overflowing_slices_are_nulled_with_note():
    usage, note = normalize_usage({"prompt_tokens": 100, "completion_tokens": 20,
                                   "prompt_tokens_details": {"cached_tokens": 160}})
    assert usage == TokenUsage(100, 20)
    assert note and "cache" in note
    kept, kept_note = normalize_usage({"prompt_tokens": 100, "completion_tokens": 20,
                                       "prompt_tokens_details": {"cached_tokens": 60}})
    assert kept.cache_read_tokens == 60 and kept_note is None


def test_reasoning_over_output_is_nulled():
    usage, note = normalize_usage({"prompt_tokens": 10, "completion_tokens": 4,
                                   "completion_tokens_details": {"reasoning_tokens": 9}})
    assert usage == TokenUsage(10, 4) and note and "reasoning" in note


@pytest.mark.parametrize("value", [True, -1, "7", 7.0, None, [], {}])
def test_bad_counts_are_none_not_zero(value):
    usage, _ = normalize_usage({"prompt_tokens": value, "completion_tokens": value})
    assert usage.input_tokens is None and usage.output_tokens is None
    assert normalize_usage(None)[0] == TokenUsage(None, None)
    assert normalize_usage("junk")[0] == TokenUsage(None, None)


def test_anthropic_fields_are_not_mapped():
    usage, _ = normalize_usage({"prompt_tokens": 10, "completion_tokens": 2,
                                "cache_read_input_tokens": 4, "cache_creation_input_tokens": 3})
    assert usage == TokenUsage(10, 2)


def test_done_comment_keepalive_and_event_lines():
    state, deltas = feed(b": OPENROUTER PROCESSING\n", b"\n", b"event: message\n", b"id: 3\n",
                         b"retry: 100\n", chunk("a"), b"data: [DONE]\n", chunk("after"))
    assert deltas == ["a"] and state.done


def test_data_without_space_and_crlf():
    state, deltas = feed(b'data:{"choices":[{"delta":{"content":"x"},"finish_reason":null}]}\r\n')
    assert deltas == ["x"]


def test_non_sse_first_line_is_bad_response():
    with pytest.raises(ProviderError) as caught:
        feed(b'{"id": "x"}\n')
    assert caught.value.kind == "bad_response" and caught.value.error_type == "not_sse"


@pytest.mark.parametrize("line", [
    b"data: {not json\n", b"data: 5\n", b'data: "text"\n', b'data: {"choices": [null]}\n',
    b'data: {"choices": {}}\n', b'data: {"choices": [{"delta": {"content": 5}}]}\n',
    b'data: {"choices": [{"delta": []}]}\n', b"data: \xff\xfe\n",
])
def test_structurally_malformed_chunks_are_bad_response(line):
    with pytest.raises(ProviderError) as caught:
        feed(line)
    assert caught.value.kind == "bad_response"


@pytest.mark.parametrize("code,kind", [(429, "rate_limit"), (502, "server_error"),
                                       ("503", "server_error"), (400, "provider_error"),
                                       ("context_length_exceeded", "provider_error"),
                                       (None, "provider_error")])
def test_in_stream_error_code_classification(code, kind):
    with pytest.raises(ProviderError) as caught:
        feed(chunk("a"), data({"error": {"code": code, "message": "upstream said no " * 40}}))
    assert caught.value.kind == kind
    assert "upstream" not in str(caught.value) and "upstream" not in repr(caught.value)
    assert caught.value.diagnostic.startswith("upstream")


def test_json_error_first_line_is_classified():
    error = classify_non_sse(b'{\n  "error": {"code": 429, "message": "slow down"}\n}\n')
    assert error.kind == "rate_limit"
    assert classify_non_sse(b'{"error": {"code": 401}}').kind == "provider_error"
    assert classify_non_sse(b"<html>gateway</html>").kind == "bad_response"
    assert classify_non_sse(b'{"choices": []}').kind == "bad_response"


def test_served_model_recorded():
    state, _ = feed(chunk("a", model="vendor/model-2024"))
    assert state.served_model == "vendor/model-2024"
    state, _ = feed(chunk("a", model=7))
    assert state.served_model is None


def test_non_ascii_digit_codes_do_not_crash():
    with pytest.raises(ProviderError) as caught:
        feed(data({"error": {"code": "²"}}))
    assert caught.value.kind == "provider_error"
    assert classify_non_sse(b'{"error": {"code": "\\u0663"}}').kind == "provider_error"


def test_a_bad_delta_beside_a_finish_reason_is_rejected_before_the_finish_counts():
    state = StreamState()
    with pytest.raises(ProviderError):
        feed_line(state, chunk(5, "stop"))
    assert not finished(state)
