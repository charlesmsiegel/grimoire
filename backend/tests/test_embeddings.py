import dataclasses
import json
import threading
import time

import httpx
import pytest

from grimoire import embeddings
from grimoire.embeddings import BATCH, READ_SLICE, TIMEOUT, EmbeddingsClient, EmbeddingsError

BASE = "https://vectors.example/v1"


def make_client(handler):
    return EmbeddingsClient(http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_posts_to_the_embeddings_path_and_returns_vectors_in_input_order():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]},
                                                  {"index": 1, "embedding": [0.0, 1.0]}]})

    out = make_client(handler).embed(["a", "b"], "embed-1", "sk-x", BASE)
    assert out == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["url"] == "https://vectors.example/v1/embeddings"
    assert seen["body"] == {"model": "embed-1", "input": ["a", "b"]}


def test_a_trailing_slash_on_the_base_url_does_not_double():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    make_client(handler).embed(["a"], "m", "", BASE + "/")
    assert seen["url"] == "https://vectors.example/v1/embeddings"


def test_vectors_are_returned_in_input_order_even_when_the_provider_reorders():
    # The OpenAI schema carries an explicit `index` precisely because response
    # order is not promised; trusting arrival order would pair every entry with
    # somebody else's vector, silently.
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.0, 1.0]},
                                                  {"index": 0, "embedding": [1.0, 0.0]}]})

    assert make_client(handler).embed(["a", "b"], "m", "", BASE) == [[1.0, 0.0], [0.0, 1.0]]


def test_long_input_is_split_into_batches_and_rejoined_in_order():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(t)]}
                                                  for i, t in enumerate(body["input"])]})

    texts = [str(n) for n in range(BATCH + 3)]
    out = make_client(handler).embed(texts, "m", "", BASE)
    assert out == [[float(n)] for n in range(BATCH + 3)]
    assert [len(c) for c in calls] == [BATCH, 3]


def test_empty_input_makes_no_request():
    def handler(request):  # pragma: no cover - a call here is the failure
        raise AssertionError("embed([]) must not reach the network")

    assert make_client(handler).embed([], "m", "", BASE) == []


def test_missing_base_url_is_a_configuration_error():
    def handler(request):  # pragma: no cover - a call here is the failure
        raise AssertionError("no endpoint to call")

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", "")
    assert exc.value.kind == "missing_key"


def test_missing_model_is_a_configuration_error():
    def handler(request):  # pragma: no cover - a call here is the failure
        raise AssertionError("no model to embed with")

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "", "", BASE)
    assert exc.value.kind == "missing_key"


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"),
                                         (500, "network"), (404, "bad_response")])
def test_status_codes_map_to_the_shared_error_taxonomy(status, kind):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert (exc.value.kind, exc.value.detail) == (kind, "nope")


def test_error_detail_falls_back_to_raw_text():
    def handler(request):
        return httpx.Response(500, text="upstream exploded")

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.detail == "upstream exploded"


def test_transport_failure_is_a_network_error():
    def handler(request):
        raise httpx.ConnectError("no route to host")

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "network"


@pytest.mark.parametrize("body", [
    {},                                                        # no data at all
    {"data": [{"index": 0, "embedding": [1.0]}]},              # fewer than asked for
    {"data": [{"index": 0, "embedding": [1.0]},
              {"index": 0, "embedding": [1.0]}]},              # duplicate index, one slot unfilled
    {"data": [{"index": 0, "embedding": [1.0]},
              {"index": 5, "embedding": [1.0]}]},              # index outside the batch
    {"data": [{"index": 0, "embedding": [1.0]},
              {"index": 1, "embedding": "not-a-vector"}]},
    {"data": [{"index": 0, "embedding": [1.0]},
              {"index": 1, "embedding": [1.0, "x"]}]},
])
def test_a_malformed_body_is_a_bad_response_not_a_crash(body):
    def handler(request):
        return httpx.Response(200, json=body)

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b"], "m", "", BASE)
    assert exc.value.kind == "bad_response"


def test_a_body_that_is_not_json_is_a_bad_response():
    def handler(request):
        return httpx.Response(200, text="<html>proxy login</html>")

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "bad_response"


def test_the_api_key_is_sent_when_set_and_omitted_when_not():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    client = make_client(handler)
    client.embed(["a"], "m", "sk-x", BASE)
    client.embed(["a"], "m", "", BASE)
    assert seen == ["Bearer sk-x", None]


def test_integer_embeddings_are_accepted_as_floats():
    # Some endpoints serialize a 0 component as the JSON integer 0.
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0]}]})

    assert make_client(handler).embed(["a"], "m", "", BASE) == [[1.0, 0.0]]


def test_booleans_are_not_numbers():
    # bool is a subclass of int in Python; a JSON `true` in a vector is a
    # malformed body, not the number 1.
    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [True, 0]}]})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "bad_response"


def test_an_integer_too_large_for_a_float_is_a_bad_response():
    # JSON bounds neither integers nor Python's int, so a component of 10**400
    # arrives as an int no float can hold and `float()` raises OverflowError.
    # `_vectors` runs after `_post`'s handlers and `semantic.recall` catches
    # only LLMError/OSError, so uncaught this failed the whole context build
    # instead of falling back to keyword activation.
    def handler(request):
        return httpx.Response(200, content=b'{"data":[{"index":0,"embedding":[1' + b"0" * 400 + b"]}]}")

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "bad_response"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_are_provider_failures_not_bad_responses(status):
    # `bad_response` is the one kind `semantic._embed` retries, because it is
    # the one a document in the batch could have caused. A 5xx is the server
    # failing, so classifying it as `bad_response` made every context build
    # during an outage send a second request to the same failing endpoint.
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "upstream is down"}})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "network"


@pytest.mark.parametrize("status", [400, 404, 422])
def test_client_errors_are_still_bad_responses(status):
    # The complement: a 4xx really can be caused by what was sent, so it stays
    # the kind that earns a retry of the query alone.
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "bad_response"


def test_a_redirect_names_where_the_endpoint_moved():
    # A FastAPI-based server whose route is `/embeddings/` answers the path
    # this module builds with a 307. The client does not follow redirects, so
    # without this the empty body reached the JSON parse and the user was told
    # "response is not JSON" about a perfectly good endpoint.
    def handler(request):
        return httpx.Response(307, headers={"Location": f"{BASE}/embeddings/"})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "missing_key"          # configured, but not usably -- and not retried
    assert f"{BASE}/embeddings/" in exc.value.detail


def test_a_redirect_without_a_location_still_says_it_was_a_redirect():
    def handler(request):
        return httpx.Response(302)

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert "redirected (302)" in exc.value.detail


def test_an_absurdly_wide_vector_is_rejected_before_it_is_materialized(monkeypatch):
    # MAX_BYTES bounds the body but not how it is distributed, and one row
    # holding the whole budget costs a Python float per component plus two
    # lists of pointers to them -- measured at 99MB peak for an 8MB body, so
    # ~400MB at the full bound. Same escape route as the OverflowError above:
    # `_vectors` runs after `_post`'s handlers, so the MemoryError would fail
    # the context build rather than falling back to keyword activation.
    monkeypatch.setattr("grimoire.embeddings.MAX_DIMS", 4)

    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 5}]})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "bad_response" and "5 components" in exc.value.detail


def test_a_vector_at_the_dimension_limit_is_accepted(monkeypatch):
    # The bound is on absurdity, not on width: no real model may be turned away
    # by it, so the boundary itself has to pass.
    monkeypatch.setattr("grimoire.embeddings.MAX_DIMS", 4)

    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 4}]})

    assert make_client(handler).embed(["a"], "m", "", BASE) == [[1.0] * 4]


def test_rows_that_are_each_within_the_limit_are_still_bounded_in_total(monkeypatch):
    # A per-row bound is not a bound. A full batch of 64 rows each exactly at
    # MAX_DIMS is 2.1M components in 8.4MB of JSON: under MAX_BYTES, and every
    # row passes the per-row check. Measured, `_vectors`' own share of that is
    # 18MB and the bound cuts it to 4.7MB -- its share specifically, because it
    # runs outside `_post`'s handlers and so is the part that would fail the
    # context build rather than degrade to keyword-only.
    monkeypatch.setattr("grimoire.embeddings.MAX_DIMS", 4)
    monkeypatch.setattr("grimoire.embeddings.MAX_TOTAL_DIMS", 9)

    def handler(request):           # 3 rows x 4 = 12 components, none over 4
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0] * 4}
                                                  for i in range(3)]})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b", "c"], "m", "", BASE)
    assert exc.value.kind == "bad_response" and "in total" in exc.value.detail


def test_a_response_at_the_total_limit_is_accepted(monkeypatch):
    monkeypatch.setattr("grimoire.embeddings.MAX_DIMS", 4)
    monkeypatch.setattr("grimoire.embeddings.MAX_TOTAL_DIMS", 12)

    def handler(request):           # exactly 12
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0] * 4}
                                                  for i in range(3)]})

    assert make_client(handler).embed(["a", "b", "c"], "m", "", BASE) == [[1.0] * 4] * 3


def test_the_total_limit_clears_a_full_batch_of_the_widest_model():
    # Same reasoning as the per-vector limit, one level up: a full BATCH of
    # vectors from a real model must never be turned away. 8192 is past every
    # embedding model shipping today (3072 for text-embedding-3-large).
    assert embeddings.MAX_TOTAL_DIMS >= BATCH * 8192


def test_the_dimension_limit_clears_every_model_in_common_use():
    # A literal here rather than an inequality against a constant: the point is
    # that the number was chosen against real models, so if someone lowers it
    # this should fail rather than quietly follow it down. 3072 is
    # text-embedding-3-large, the widest in common use.
    assert embeddings.MAX_DIMS >= 3072 * 10


class _Drip(httpx.SyncByteStream):
    """A body that arrives a byte at a time, forever."""

    def __init__(self, gap: float):
        self.gap = gap

    def __iter__(self):
        while True:
            time.sleep(self.gap)
            yield b" "


def test_a_drip_feeding_server_is_cut_off_at_the_deadline(monkeypatch):
    # httpx's timeout bounds each network *operation*: every arriving byte
    # resets the read timer, so a server that emits one byte per interval holds
    # the request open indefinitely and pins a threadpool worker with it.
    monkeypatch.setattr("grimoire.embeddings.TIMEOUT", 0.5)

    def handler(request):
        return httpx.Response(200, stream=_Drip(0.05))

    started = time.monotonic()
    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "network"
    assert time.monotonic() - started < 3.0     # bounded, not "eventually"


def test_a_single_read_cannot_outlive_the_deadline_by_more_than_a_slice():
    # httpx sets one timeout per *request*, not per read, so a read already in
    # flight when the deadline passes runs to its own timeout. Handing it the
    # whole remaining budget let a server chunking just under each read timeout
    # overrun the deadline by a further TIMEOUT. The read timeout is what
    # bounds that overrun, so it is the read timeout this asserts on.
    seen = {}

    def handler(request):
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    make_client(handler).embed(["a"], "m", "", BASE)
    assert seen["timeout"]["read"] <= READ_SLICE
    assert READ_SLICE < TIMEOUT           # or the slice bounds nothing


def test_an_unbounded_response_body_is_cut_off(monkeypatch):
    monkeypatch.setattr("grimoire.embeddings.MAX_BYTES", 512)

    def handler(request):
        return httpx.Response(200, content=b"[" + b"0" * 4096)

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "bad_response"


def test_the_deadline_covers_every_batch_not_each_one(monkeypatch):
    # A per-request bound would let ten batches take ten times TIMEOUT, which
    # is the same unbounded stall by another route.
    monkeypatch.setattr("grimoire.embeddings.TIMEOUT", 0.4)
    calls = []

    def handler(request):
        calls.append(1)
        time.sleep(0.25)
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0]}
                                                  for i, _ in enumerate(body["input"])]})

    started = time.monotonic()
    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed([str(n) for n in range(BATCH * 4)], "m", "", BASE)
    assert exc.value.kind == "network"
    assert len(calls) < 4                       # gave up partway, not after all four
    assert time.monotonic() - started < 2.0


def test_a_supplied_deadline_replaces_the_fresh_one(monkeypatch):
    # For a caller making several calls under one budget. Without this the
    # callee resets the bound the caller just checked, so the caller's budget
    # is worth its own value plus a whole further TIMEOUT.
    monkeypatch.setattr("grimoire.embeddings.TIMEOUT", 30.0)

    def handler(request):
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    client = make_client(handler)
    with pytest.raises(EmbeddingsError) as exc:
        client.embed(["a"], "m", "", BASE, deadline=time.monotonic() - 1)
    assert exc.value.kind == "network"        # already past, so no request at all
    assert client.embed(["a"], "m", "", BASE,
                        deadline=time.monotonic() + 30) == [[1.0]]


def test_concurrent_first_use_builds_exactly_one_client(monkeypatch):
    # The production instance is a module global in context/semantic.py, and
    # `build_messages` runs on a threadpool worker for every sync route
    # handler. An unguarded lazy init hands each racing thread its own client
    # and drops all but one of them un-closed, leaking a connection pool per
    # race. Measured at 8-for-8 before the lock.
    built = []
    real = httpx.Client

    class Counting(real):
        def __init__(self, *a, **k):
            built.append(1)
            time.sleep(0.01)   # widen the window the race needs
            super().__init__(*a, **k)

    monkeypatch.setattr(httpx, "Client", Counting)
    client = EmbeddingsClient()
    threads = [threading.Thread(target=client._client) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1
    client.close()


def test_close_is_a_no_op_for_an_injected_client():
    handled = []

    def handler(request):
        handled.append(1)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EmbeddingsClient(http=http)
    client.close()
    client.embed(["a"], "m", "", BASE)  # the caller still owns it, so it still works
    assert handled == [1]


# --- what each request spent -------------------------------------------------


def _usage_handler(bodies):
    """Answer successive requests with `bodies[n]` merged into a valid reply."""
    calls = []

    def handler(request):
        n = len(calls)
        calls.append(1)
        sent = json.loads(request.content)["input"]
        reply = {"data": [{"index": i, "embedding": [1.0]} for i, _ in enumerate(sent)]}
        reply.update(bodies[n])
        return httpx.Response(200, json=reply)

    return handler, calls


def test_usage_is_summed_across_batches(monkeypatch):
    monkeypatch.setattr("grimoire.embeddings.BATCH", 2)
    handler, _ = _usage_handler([{"usage": {"prompt_tokens": 3, "cost": 0.001}},
                                 {"usage": {"prompt_tokens": 2, "cost": 0.002}}])
    holder: dict = {}
    out = make_client(handler).embed(["a", "b", "c"], "m", "", BASE, usage=holder)
    assert len(out) == 3
    assert holder["prompt_tokens"] == 5
    assert holder["cost_usd"] == pytest.approx(0.003)
    assert holder["cost_basis"] == "billed"
    assert "completion_tokens" not in holder        # an embedding generates nothing


def test_a_batch_without_usage_leaves_the_count_absent(monkeypatch):
    # A partial sum is not the call's count, and it stays absent even when a
    # later batch does report.
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)
    handler, _ = _usage_handler([{"usage": {"prompt_tokens": 3}}, {},
                                 {"usage": {"prompt_tokens": 4}}])
    holder: dict = {}
    make_client(handler).embed(["a", "b", "c"], "m", "", BASE, usage=holder)
    assert "prompt_tokens" not in holder


def test_a_batch_without_a_price_leaves_the_price_absent(monkeypatch):
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)
    handler, _ = _usage_handler([{"usage": {"prompt_tokens": 3, "cost": 0.001}},
                                 {"usage": {"prompt_tokens": 2}}])
    holder: dict = {}
    make_client(handler).embed(["a", "b"], "m", "", BASE, usage=holder)
    assert holder["prompt_tokens"] == 5
    assert "cost_usd" not in holder
    assert "cost_basis" not in holder


def test_usage_is_kept_when_a_body_is_malformed():
    # Billed but unusable: the money was spent either way.
    def handler(request):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 3},
                                         "data": [{"index": 0, "embedding": [1.0]}]})

    holder: dict = {}
    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b"], "m", "", BASE, usage=holder)
    assert exc.value.kind == "bad_response"
    assert holder["prompt_tokens"] == 3


def test_usage_is_kept_when_a_later_batch_fails(monkeypatch):
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 2:
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json={"usage": {"prompt_tokens": 3},
                                         "data": [{"index": 0, "embedding": [1.0]}]})

    holder: dict = {}
    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b"], "m", "", BASE, usage=holder)
    assert exc.value.kind == "network"
    assert holder["prompt_tokens"] == 3


def _later_batch(fail):
    """BATCH=1: the first batch answers with counts, the second calls `fail`."""
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 2:
            return fail(request)
        return httpx.Response(200, json={"usage": {"prompt_tokens": 3, "cost": 0.001},
                                         "data": [{"index": 0, "embedding": [1.0]}]})

    return handler, calls


@pytest.mark.parametrize("why", ["too_large", "transport"])
def test_a_later_batch_lost_after_it_was_sent_drops_the_sum(monkeypatch, why):
    """The second request went out and may have been billed, but its body (and
    its usage block) was never read: batch one's count is a floor, not the
    call's, so it is written as absent (CLAUDE.md, Costs)."""
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)

    def fail(request):
        if why == "transport":
            raise httpx.ReadError("connection reset", request=request)
        monkeypatch.setattr("grimoire.embeddings.MAX_BYTES", 8)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    handler, calls = _later_batch(fail)
    holder: dict = {}
    with pytest.raises(EmbeddingsError):
        make_client(handler).embed(["a", "b"], "m", "", BASE, usage=holder)
    assert len(calls) == 2
    assert "prompt_tokens" not in holder
    assert "cost_usd" not in holder
    assert "cost_basis" not in holder


def test_a_later_batch_never_sent_keeps_the_sum(monkeypatch):
    """A batch the deadline stopped BEFORE its request: everything that was
    sent was read, so batch one's count is the call's."""
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)
    clock = {"now": 1000.0}
    monkeypatch.setattr("grimoire.embeddings.time.monotonic", lambda: clock["now"])

    body = json.dumps({"usage": {"prompt_tokens": 3},
                       "data": [{"index": 0, "embedding": [1.0]}]}).encode()

    class _ThenLate(httpx.SyncByteStream):
        """The whole body, and only then the clock passes the deadline."""

        def __iter__(self):
            yield body
            clock["now"] += 100.0

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, stream=_ThenLate())

    holder: dict = {}
    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b"], "m", "", BASE, deadline=1050.0, usage=holder)
    assert len(calls) == 1
    assert exc.value.code == embeddings.DEADLINE
    assert holder["prompt_tokens"] == 3


def test_a_read_the_deadline_cut_is_marked_deadline(monkeypatch):
    """Cut mid-body by the call's deadline: `DEADLINE`, so a caller whose own
    clock that was can tell it from a provider failure."""
    monkeypatch.setattr("grimoire.embeddings.TIMEOUT", 0.3)

    def handler(request):
        return httpx.Response(200, stream=_Drip(0.05))

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE)
    assert exc.value.kind == "network"
    assert exc.value.code == embeddings.DEADLINE


def test_a_usage_block_of_non_numbers_is_absent():
    handler, _ = _usage_handler([{"usage": {"prompt_tokens": "lots", "cost": True}}])
    holder: dict = {}
    make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
    assert "prompt_tokens" not in holder
    assert "cost_usd" not in holder
    assert "cost_basis" not in holder


@pytest.mark.parametrize("usage", [None, "x", [1], 3])
def test_a_usage_block_that_is_not_an_object_is_absent(usage):
    handler, _ = _usage_handler([{"usage": usage}])
    holder: dict = {}
    make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
    assert holder == {}


def test_the_reported_model_is_kept():
    handler, _ = _usage_handler([{"model": "vendor/embed-2"}])
    holder: dict = {}
    make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
    assert holder["model"] == "vendor/embed-2"


def test_an_empty_or_non_string_model_is_not_kept():
    for model in ("", 7, None):
        handler, _ = _usage_handler([{"model": model}])
        holder: dict = {}
        make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
        assert "model" not in holder


def test_without_a_holder_nothing_changes():
    handler, _ = _usage_handler([{"usage": {"prompt_tokens": 3}}])
    assert make_client(handler).embed(["a"], "m", "", BASE) == [[1.0]]


def test_a_first_request_never_sent_is_marked():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a"], "m", "", BASE, deadline=time.monotonic() - 1)
    assert exc.value.kind == "network"
    assert exc.value.code == embeddings.NOT_SENT == "not_sent"
    assert seen == []


def test_a_later_batch_past_the_deadline_is_not_marked(monkeypatch):
    # Batch one went out and was billed, so "nothing was sent" would be false.
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)
    clock = {"now": 1000.0}
    monkeypatch.setattr("grimoire.embeddings.time.monotonic", lambda: clock["now"])
    calls = []

    def handler(request):
        calls.append(1)
        clock["now"] += 100.0                       # past the deadline
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b"], "m", "", BASE, deadline=1050.0)
    assert exc.value.kind == "network"
    # The deadline's, but not `NOT_SENT`: batch one went out.
    assert exc.value.code == embeddings.DEADLINE
    assert len(calls) == 1


def test_a_body_that_is_not_an_object_drops_the_count(monkeypatch):
    # Read and billable, but it reported nothing: the earlier batch's 3 is a
    # partial sum, not the call's count.
    monkeypatch.setattr("grimoire.embeddings.BATCH", 1)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json={"usage": {"prompt_tokens": 3, "cost": 0.001},
                                             "data": [{"index": 0, "embedding": [1.0]}]})
        return httpx.Response(200, json=[1, 2])

    holder: dict = {}
    with pytest.raises(EmbeddingsError) as exc:
        make_client(handler).embed(["a", "b"], "m", "", BASE, usage=holder)
    assert exc.value.kind == "bad_response"
    assert "prompt_tokens" not in holder
    assert "cost_usd" not in holder
    assert "cost_basis" not in holder


def test_a_prefilled_holder_keeps_nothing_stale():
    stale = {"prompt_tokens": 99, "cost_usd": 1.0, "cost_basis": "billed", "model": "old"}

    def failing(request):
        return httpx.Response(503, json={"error": "down"})

    holder = dict(stale)
    with pytest.raises(EmbeddingsError):
        make_client(failing).embed(["a"], "m", "", BASE, usage=holder)
    assert holder == {}

    handler, _ = _usage_handler([{}])                 # a body that says nothing
    holder = dict(stale)
    make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
    assert holder == {}


def test_a_holder_reused_across_calls_is_overwritten_not_added_to():
    handler, _ = _usage_handler([{"usage": {"prompt_tokens": 3, "cost": 0.001}, "model": "x/a"},
                                 {"usage": {"prompt_tokens": 2, "cost": 0.002}}])
    client = make_client(handler)
    holder: dict = {}
    client.embed(["a"], "m", "", BASE, usage=holder)
    client.embed(["a"], "m", "", BASE, usage=holder)
    assert holder["prompt_tokens"] == 2
    assert holder["cost_usd"] == pytest.approx(0.002)
    assert "model" not in holder                      # the first call's does not survive


def test_an_overlong_model_is_not_kept():
    handler, _ = _usage_handler([{"model": "m" * (embeddings.MAX_MODEL_CHARS + 1)}])
    holder: dict = {}
    make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
    assert "model" not in holder
    handler, _ = _usage_handler([{"model": "m" * embeddings.MAX_MODEL_CHARS}])
    make_client(handler).embed(["a"], "m", "", BASE, usage=holder)
    assert len(holder["model"]) == embeddings.MAX_MODEL_CHARS


# ---- embedding options (01h-S2) ----

NOMIC = embeddings.wire.EmbedOptions(input="prefix", query_prefix="search_query: ",
                                     document_prefix="search_document: ")


def _recording(seen):
    def handler(request):
        seen.append(request)
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, 0.0]}
                                                  for i in range(len(inputs))]})
    return handler


@pytest.mark.parametrize("extra", [{}, {"options": embeddings.wire.EmbedOptions(), "queries": 1},
                                   {"options": None, "queries": 2}])
def test_no_options_sends_todays_exact_bytes(extra):
    seen = []
    make_client(_recording(seen)).embed(["a", "b"], "embed-1", "sk-x", BASE, **extra)
    assert len(seen) == 1
    assert seen[0].content == httpx.Request(
        "POST", BASE, json={"model": "embed-1", "input": ["a", "b"]}).content


def test_prefix_mode_prefixes_each_input_in_one_request():
    seen = []
    texts = ["Where is Mara?", "Mara crossed the Saltmarch.", "Seraphine waits."]
    out = make_client(_recording(seen)).embed(texts, "embed-1", "sk-x", BASE,
                                              options=NOMIC, queries=1)
    assert len(out) == 3 and len(seen) == 1
    body = json.loads(seen[0].content)
    assert set(body) == {"model", "input"}
    assert body["input"] == ["search_query: Where is Mara?",
                             "search_document: Mara crossed the Saltmarch.",
                             "search_document: Seraphine waits."]


def test_a_query_only_prefix_leaves_documents_bare():
    seen = []
    bge = embeddings.wire.EmbedOptions(input="prefix", query_prefix="Represent: ")
    make_client(_recording(seen)).embed(["q", "d"], "m", "", BASE, options=bge, queries=1)
    assert json.loads(seen[0].content)["input"] == ["Represent: q", "d"]


def test_prefixes_survive_batching():
    seen = []
    texts = [f"t{i}" for i in range(BATCH + 2)]
    make_client(_recording(seen)).embed(texts, "m", "", BASE, options=NOMIC, queries=1)
    first, second = (json.loads(r.content)["input"] for r in seen)
    assert first[0] == "search_query: t0"
    assert all(t.startswith("search_document: ") for t in first[1:] + second)
    assert len(first) == BATCH and len(second) == 2


@pytest.mark.parametrize("queries", [3, -1, True])
def test_queries_out_of_range_is_a_value_error_before_sending(queries):
    seen = []
    with pytest.raises(ValueError):
        make_client(_recording(seen)).embed(["a", "b"], "m", "", BASE, options=NOMIC,
                                            queries=queries)
    assert seen == []


@pytest.mark.parametrize("options", [
    embeddings.wire.EmbedOptions(input="param", param_field="input_type",
                                 query_value="query"),
    embeddings.wire.EmbedOptions(input="param", query_value="q", document_value="d"),
    embeddings.wire.EmbedOptions(dimensions=0),
    embeddings.wire.EmbedOptions(dimensions=True),
    embeddings.wire.EmbedOptions(dimensions=512, dimensions_field=""),
    embeddings.wire.EmbedOptions(input="both"),
    "prefix",
])
def test_options_that_cannot_be_sent_are_refused_before_sending(options):
    seen = []
    with pytest.raises(ValueError):
        make_client(_recording(seen)).embed(["a"], "m", "", BASE, options=options)
    assert seen == []


# ---- the request field and dimensions (01h-S3) ----

JINA = embeddings.wire.EmbedOptions(input="param", param_field="task",
                                    query_value="retrieval.query",
                                    document_value="retrieval.passage")


def _indexed(seen, width=None):
    """A handler answering each input with a vector naming its text, or a
    vector `width` wide."""
    def handler(request):
        seen.append(request)
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [float(len(t))] * (width or 1)}
            for i, t in enumerate(inputs)]})
    return handler


def test_param_mode_sends_the_query_then_the_documents():
    seen = []
    texts = ["q", "dd", "ddd"]
    out = make_client(_indexed(seen)).embed(texts, "m", "", BASE, options=JINA, queries=1)
    bodies = [json.loads(r.content) for r in seen]
    assert bodies == [
        {"model": "m", "input": ["q"], "task": "retrieval.query"},
        {"model": "m", "input": ["dd", "ddd"], "task": "retrieval.passage"},
    ]
    assert out == [[1.0], [2.0], [3.0]]


@pytest.mark.parametrize(("queries", "sides"), [(0, ["retrieval.passage"]),
                                                (2, ["retrieval.query"])])
def test_param_mode_with_one_side_is_one_request(queries, sides):
    seen = []
    make_client(_indexed(seen)).embed(["a", "b"], "m", "", BASE, options=JINA, queries=queries)
    assert [json.loads(r.content)["task"] for r in seen] == sides


def test_param_mode_batches_each_side():
    seen = []
    texts = [str(n) for n in range(BATCH + 3)]
    out = make_client(_indexed(seen)).embed(texts, "m", "", BASE, options=JINA, queries=2)
    sizes = [(len(json.loads(r.content)["input"]), json.loads(r.content)["task"]) for r in seen]
    assert sizes == [(2, "retrieval.query"), (BATCH, "retrieval.passage"),
                     (1, "retrieval.passage")]
    assert out == [[float(len(t))] for t in texts]


def test_param_mode_sums_usage_over_both_requests():
    def handler(request):
        inputs = json.loads(request.content)["input"]
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": [1.0]} for i in range(len(inputs))],
            "usage": {"prompt_tokens": len(inputs)}})
    holder: dict = {}
    make_client(handler).embed(["a", "b", "c"], "m", "", BASE, usage=holder, options=JINA,
                               queries=1)
    assert holder["prompt_tokens"] == 3


@pytest.mark.parametrize(("options", "extra"), [
    (embeddings.wire.EmbedOptions(dimensions=4), {"dimensions": 4}),
    (embeddings.wire.EmbedOptions(dimensions=4, dimensions_field="output_dimension"),
     {"output_dimension": 4}),
    (embeddings.wire.EmbedOptions(input="prefix", document_prefix="passage: ", dimensions=4),
     {"dimensions": 4}),
])
def test_dimensions_ride_every_request(options, extra):
    seen = []
    make_client(_indexed(seen, width=4)).embed(["a"], "m", "", BASE, options=options)
    body = json.loads(seen[0].content)
    assert {k: v for k, v in body.items() if k not in ("model", "input")} == extra


def test_param_and_dimensions_together():
    seen = []
    both = embeddings.wire.EmbedOptions(input="param", param_field="input_type",
                                        query_value="query", document_value="document",
                                        dimensions=4)
    make_client(_indexed(seen, width=4)).embed(["q", "d"], "m", "", BASE, options=both,
                                               queries=1)
    assert [json.loads(r.content) for r in seen] == [
        {"model": "m", "input": ["q"], "input_type": "query", "dimensions": 4},
        {"model": "m", "input": ["d"], "input_type": "document", "dimensions": 4}]


def test_a_reply_that_ignored_dimensions_is_a_mismatch():
    seen = []
    holder: dict = {}

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 6}],
                                         "usage": {"prompt_tokens": 3}})
    with pytest.raises(EmbeddingsError) as got:
        make_client(handler).embed(["a"], "m", "", BASE, usage=holder,
                                   options=embeddings.wire.EmbedOptions(dimensions=4))
    exc = got.value
    assert isinstance(exc, embeddings.DimensionsMismatchError)
    assert (exc.kind, exc.code) == ("missing_key", embeddings.DIMENSIONS_MISMATCH)
    assert (exc.requested_dims, exc.returned_dims) == (4, 6)
    assert exc.status is None
    # The reply was read and billed: its count is kept.
    assert holder["prompt_tokens"] == 3
    assert len(seen) == 1


def test_a_mismatch_on_the_query_request_sends_no_documents():
    seen = []
    with pytest.raises(embeddings.DimensionsMismatchError):
        make_client(_indexed(seen, width=6)).embed(
            ["q", "d"], "m", "", BASE, queries=1,
            options=dataclasses.replace(JINA, dimensions=4))
    assert len(seen) == 1
