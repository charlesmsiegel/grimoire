"""`content_parts`: the grimoire-internal image reference and its two lowerings
(#377). The rule every case here leans on: the text parts of a composed
message concatenate to exactly the string today's code sends, so lowering to
text can never change what a text-only route receives."""

from grimoire import content_parts as cp

R = cp.ref("/api/campaigns/c/images/a", "a map", False)
C = cp.ref("/api/campaigns/c/images/b", "the hall", True)
DATA = "data:image/png;base64,AA"


def test_text_of_concatenates_text_parts_only():
    assert cp.text_of("plain") == "plain"
    assert cp.text_of([{"type": "text", "text": "x a map"}, R,
                       {"type": "text", "text": " y"}]) == "x a map y"


def test_ref_shape():
    assert R == {"type": "image_ref", "url": "/api/campaigns/c/images/a",
                 "alt": "a map", "carried": False}


def test_as_text_drops_carriers_keeps_empty_messages_and_never_mutates():
    msgs = [{"role": "user", "content": [{"type": "text", "text": ""}, R]},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": [C], "carrier": True}]
    before = repr(msgs)
    assert cp.as_text(msgs) == [{"role": "user", "content": ""},
                                {"role": "assistant", "content": "hi"}]
    assert repr(msgs) == before


def test_as_images_keeps_newest_n_counts_and_labels_carried():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "old a map"}, R]},
            {"role": "user", "content": [C, {"type": "text", "text": "new"}]}]
    out, n = cp.as_images(msgs, 1, lambda p: DATA)
    assert n == 1
    assert out[0]["content"] == "old a map"
    assert out[1]["content"] == [
        {"type": "text", "text": "[Image from the previous reply: the hall]"},
        {"type": "image_url", "image_url": {"url": DATA}},
        {"type": "text", "text": "new"}]


def test_as_images_keeps_inline_ref_after_its_text_and_strips_carrier_marker():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "see a map"}, R]},
            {"role": "user", "content": [C], "carrier": True}]
    out, n = cp.as_images(msgs, 5, lambda p: DATA)
    assert n == 2
    assert out[0]["content"] == [{"type": "text", "text": "see a map"},
                                 {"type": "image_url", "image_url": {"url": DATA}}]
    assert "carrier" not in out[1]


def test_as_images_load_failure_drops_image_and_empty_carrier():
    msgs = [{"role": "user", "content": [C], "carrier": True}]

    def boom(_p):
        raise OSError("gone")

    assert cp.as_images(msgs, 3, boom) == ([], 0)
    assert cp.as_images(msgs, 3, lambda _p: None) == ([], 0)


def test_as_images_with_keep_zero_is_the_text_lowering():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "a map"}, R]},
            {"role": "user", "content": [C], "carrier": True}]
    assert cp.as_images(msgs, 0, lambda _p: DATA) == (cp.as_text(msgs), 0)


def test_as_images_never_mutates():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "a map"}, R]}]
    before = repr(msgs)
    cp.as_images(msgs, 2, lambda _p: DATA)
    assert repr(msgs) == before


def test_unknown_parts_pass_through_and_are_not_lowerable():
    draft = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]}]
    assert not cp.lowerable(draft[0]["content"])
    assert not cp.needs_lowering(draft)
    assert cp.as_text(draft) == draft


def test_needs_lowering():
    assert cp.needs_lowering([{"role": "user", "content": [{"type": "text", "text": "a"}]}])
    assert cp.needs_lowering([{"role": "user", "content": [C], "carrier": True}])
    assert not cp.needs_lowering([{"role": "user", "content": "a"}])
    assert not cp.lowerable("a")


def test_has_refs_and_image_refs():
    msgs = [{"role": "user", "content": "x"},
            {"role": "user", "content": [{"type": "text", "text": "a map"}, R]}]
    assert cp.has_refs(msgs)
    assert not cp.has_refs([{"role": "user", "content": "x"}])
    assert cp.image_refs(msgs[1]["content"]) == [R]
    assert cp.image_refs("x") == []


def test_collapse():
    assert cp.collapse([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "ab"
    assert cp.collapse([{"type": "text", "text": "a"}, R]) == [{"type": "text", "text": "a"}, R]
    assert cp.collapse("s") == "s"


def test_scrub_elides_base64_payloads():
    assert (cp.scrub('bad "data:image/png;base64,iVBORw0KGgo=" x')
            == 'bad "data:image/png;base64,[elided]" x')
    assert cp.scrub("no payload here") == "no payload here"
