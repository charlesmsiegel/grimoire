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
