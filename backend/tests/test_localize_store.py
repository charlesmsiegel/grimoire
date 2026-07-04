import re as _re

from grimoire.store import assets, greetings, localize


def test_find_markdown_image():
    refs = localize.find_refs("see ![cat](https://h/cat.png) here")
    assert [r.url for r in refs] == ["https://h/cat.png"]


def test_find_markdown_image_with_title():
    refs = localize.find_refs('![a](https://h/a.png "title")')
    assert [r.url for r in refs] == ["https://h/a.png"]


def test_find_html_img():
    refs = localize.find_refs('<img alt="x" src="https://h/b.jpg" width="2">')
    assert [r.url for r in refs] == ["https://h/b.jpg"]


def test_find_data_uri():
    text = "x ![p](data:image/png;base64,AAAA) y"
    refs = localize.find_refs(text)
    assert [r.url for r in refs] == ["data:image/png;base64,AAAA"]


def test_find_bare_url():
    refs = localize.find_refs("look at https://h/pic.gif now")
    assert [r.url for r in refs] == ["https://h/pic.gif"]


def test_bare_url_does_not_double_match_markdown_url():
    # the URL inside the markdown image must be matched once, not also as a bare url
    refs = localize.find_refs("![a](https://h/a.png)")
    assert len(refs) == 1
    assert refs[0].url == "https://h/a.png"


def test_skips_already_local_ref():
    refs = localize.find_refs("![a](/api/worlds/w/characters/c/versions/v/images/embed-abc)")
    assert refs == []


def test_spans_are_non_overlapping_and_ordered():
    text = "![a](https://h/a.png) and <img src='https://h/b.png'>"
    refs = localize.find_refs(text)
    assert [r.url for r in refs] == ["https://h/a.png", "https://h/b.png"]
    assert all(refs[i].end <= refs[i + 1].start for i in range(len(refs) - 1))


def test_span_exactly_covers_url_so_rewrite_is_clean():
    # the span must equal the url text — not include trailing prose punctuation
    text = "see https://h/a.png. Next sentence."
    refs = localize.find_refs(text)
    assert len(refs) == 1
    r = refs[0]
    assert r.url == "https://h/a.png"
    assert text[r.start:r.end] == "https://h/a.png"  # period left intact
    # splicing a replacement preserves the surrounding text
    rewritten = text[:r.start] + "LOCAL" + text[r.end:]
    assert rewritten == "see LOCAL. Next sentence."


def test_find_standalone_data_uri():
    text = "prefix data:image/png;base64,AAAA suffix"
    refs = localize.find_refs(text)
    assert [r.url for r in refs] == ["data:image/png;base64,AAAA"]
    assert all(text[r.start:r.end] == r.url for r in refs)


def test_find_markdown_angle_bracket_url():
    refs = localize.find_refs("![a](<https://h/a.png>)")
    assert [r.url for r in refs] == ["https://h/a.png"]
    text = "![a](<https://h/a.png>)"
    assert all(text[r.start:r.end] == r.url for r in refs)


def test_empty_and_non_string_input():
    assert localize.find_refs("") == []
    assert localize.find_refs(None) == []  # type: ignore[arg-type]


def test_already_local_bare_url_is_claimed_not_rematched():
    # an https url that is actually a local serving url would be matched by the
    # bare-url pattern; the local-prefix skip must drop it (no Ref emitted)
    text = "/api/worlds/w/characters/c/versions/v/images/embed-abc and https://h/a.png"
    refs = localize.find_refs(text)
    assert [r.url for r in refs] == ["https://h/a.png"]


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _fake_fetch(mapping):
    def f(url):
        return mapping.get(url)
    return f


def _run(card, tmp_path, cid="c", vid="v", wid="w", **kw):
    return list(localize.localize_card(card, tmp_path, cid, vid, wid, **kw))


def test_localizes_markdown_image_and_rewrites(tmp_path):
    card = {"data": {"description": "x ![a](https://h/a.png) y", "alternate_greetings": []}}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png")})
    events = _run(card, tmp_path, fetch=fetch)
    assert events[0] == {"total": 1}
    summary = events[-1]["summary"]
    assert summary["localized"] == 1 and summary["failed"] == 0
    desc = card["data"]["description"]
    m = _re.search(r"/api/worlds/w/characters/c/versions/v/images/(embed-[0-9a-f]{12})", desc)
    assert m, desc
    assert assets.image_path(tmp_path, "c", "v", m.group(1)) is not None


def test_html_img_is_localized_as_markdown(tmp_path):
    # HTML <img> embeds must become markdown images so they render in the app
    # (react-markdown drops raw HTML). The whole tag is replaced.
    card = {"data": {"first_mes": 'hi <img alt="x" src="https://h/a.png" width="2"> bye',
                     "alternate_greetings": []}}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png")})
    _run(card, tmp_path, fetch=fetch)
    fm = card["data"]["first_mes"]
    assert "<img" not in fm  # the raw HTML tag is gone
    m = _re.search(r"!\[\]\((/api/worlds/w/characters/c/versions/v/images/embed-[0-9a-f]{12})\)", fm)
    assert m, fm
    assert fm.startswith("hi ![](") and fm.endswith(") bye")


def test_non_image_is_left_untouched(tmp_path):
    card = {"data": {"description": "see https://h/page now", "alternate_greetings": []}}
    fetch = _fake_fetch({})  # returns None -> not an image
    events = _run(card, tmp_path, fetch=fetch)
    assert card["data"]["description"] == "see https://h/page now"
    assert events[-1]["summary"]["skipped"] == 1
    assert events[-1]["summary"]["localized"] == 0


def test_dedupes_identical_bytes(tmp_path):
    card = {"data": {
        "description": "![a](https://h/a.png)",
        "personality": "![b](https://h/b.png)",
        "alternate_greetings": [],
    }}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png"), "https://h/b.png": (_PNG, "png")})
    _run(card, tmp_path, fetch=fetch)
    names = {p["name"] for p in assets.list_images(tmp_path, "c", "v")}
    assert len(names) == 1  # same bytes -> one stored file
    name = names.pop()
    assert name in card["data"]["description"]
    assert name in card["data"]["personality"]


def test_rescan_is_idempotent(tmp_path):
    card = {"data": {"description": "![a](https://h/a.png)", "alternate_greetings": []}}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png")})
    _run(card, tmp_path, fetch=fetch)
    after_first = card["data"]["description"]
    events = _run(card, tmp_path, fetch=fetch)  # second pass
    assert card["data"]["description"] == after_first
    assert events[0] == {"total": 0}  # nothing left to localize


def test_data_uri_is_decoded_without_fetch(tmp_path):
    uri = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
    card = {"data": {"description": f"![p]({uri})", "alternate_greetings": []}}

    def boom(url):  # fetch must NOT be called for data-uris
        raise AssertionError("fetch called for data-uri")

    _run(card, tmp_path, fetch=boom)
    assert "/api/worlds/w/characters/c/versions/v/images/embed-" in card["data"]["description"]


def test_cap_scales_with_greetings(tmp_path):
    # 0 alt greetings -> cap 10; make 12 refs, expect 10 localized + capped
    urls = [f"https://h/{i}.png" for i in range(12)]
    body = " ".join(f"![{i}]({u})" for i, u in enumerate(urls))
    card = {"data": {"description": body, "first_mes": "hi", "alternate_greetings": []}}
    # each distinct url -> distinct bytes so no dedupe masks the cap
    fetch = _fake_fetch({u: (_PNG[:8] + bytes([i]) + _PNG[9:], "png") for i, u in enumerate(urls)})
    events = _run(card, tmp_path, fetch=fetch)
    summary = events[-1]["summary"]
    assert summary["total"] == 12
    assert summary["localized"] == 10
    assert summary["skipped"] == 2
    assert summary["capped"] is True


def test_fetch_exception_counts_as_failed_and_never_raises(tmp_path):
    card = {"data": {"description": "![a](https://h/a.png)", "alternate_greetings": []}}

    def boom(url):
        raise RuntimeError("network down")

    events = _run(card, tmp_path, fetch=boom)  # must not raise
    summary = events[-1]["summary"]
    assert summary["failed"] == 1 and summary["localized"] == 0
    assert card["data"]["description"] == "![a](https://h/a.png)"  # untouched


def test_interrupted_run_keeps_rewrites_of_completed_fields(tmp_path):
    # A closed generator (client disconnect mid-stream) must not lose the
    # rewrites of fields whose refs already finished — they are applied as each
    # field completes, not all at the end.
    card = {"data": {
        "description": "![a](https://h/a.png)",
        "first_mes": "![b](https://h/b.png)",
        "alternate_greetings": [],
    }}
    fetch = _fake_fetch({"https://h/a.png": (_PNG, "png"),
                         "https://h/b.png": (_PNG[:8] + b"\x01" + _PNG[9:], "png")})
    gen = localize.localize_card(card, tmp_path, "c", "v", "w", fetch=fetch)
    assert next(gen) == {"total": 2}
    first_done = next(gen)  # description's only ref finished -> field applied
    assert first_done["applied"] == 1  # progress events expose applied count
    gen.close()  # stream interrupted here
    assert "/api/worlds/w/" in card["data"]["description"]
    assert card["data"]["first_mes"] == "![b](https://h/b.png)"  # untouched


def test_localizes_greetings_and_lorebook_entries(tmp_path):
    card = {"data": {
        "description": "",
        "alternate_greetings": ["hi ![g](https://h/g.png)"],
        "character_book": {"entries": [{"content": "lore ![l](https://h/l.png)"}]},
    }}
    fetch = _fake_fetch({"https://h/g.png": (_PNG, "png"),
                         "https://h/l.png": (_PNG[:8] + b"\x01" + _PNG[9:], "png")})
    events = _run(card, tmp_path, fetch=fetch)
    assert events[0] == {"total": 2}
    assert "/api/worlds/w/" in card["data"]["alternate_greetings"][0]
    assert "/api/worlds/w/" in card["data"]["character_book"]["entries"][0]["content"]


# ---- localize_greeting (world greeting bodies -> per-greeting assets) ----
def _greeting_with(tmp_path, body):
    return greetings.create_greeting(tmp_path, "Opener", "mira", "v1", body)


def test_localize_greeting_rewrites_body_and_stores_assets(tmp_path):
    gid = _greeting_with(tmp_path, "look ![art](https://h/a.png) done")
    fetch = _fake_fetch({"https://h/a.png": (b"png-bytes", "png")})
    summary = localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch)
    assert summary == {"total": 1, "localized": 1, "skipped": 0,
                       "failed": 0, "capped": False}
    body = greetings.read_greeting(tmp_path, gid)["body"]
    m = _re.search(r"!\[art\]\(/api/worlds/w1/greetings/%s/images/(embed-\w{12})\)" % gid, body)
    assert m, body
    name = m.group(1)
    p = assets.image_path(tmp_path, gid, "default", name, base="greetings")
    assert p is not None and p.read_bytes() == b"png-bytes"
    assert p.parent == tmp_path / "greetings" / gid / "assets" / "default"


def test_localize_greeting_data_uri_and_failed_fetch(tmp_path):
    # data-uri decodes and stores; the http ref fails; nothing raises
    body = ("pic ![d](data:image/png;base64,aGk=) and "
            "![b](https://h/broken.png) end")
    gid = _greeting_with(tmp_path, body)

    def boom(url):
        raise OSError("down")

    summary = localize.localize_greeting(tmp_path, gid, "w1", fetch=boom)
    assert summary["total"] == 2 and summary["localized"] == 1
    assert summary["failed"] == 1 and summary["skipped"] == 0
    new_body = greetings.read_greeting(tmp_path, gid)["body"]
    assert f"/api/worlds/w1/greetings/{gid}/images/embed-" in new_body
    assert "https://h/broken.png" in new_body  # failed ref left untouched


def test_localize_greeting_skips_local_refs_and_is_idempotent(tmp_path):
    gid = _greeting_with(tmp_path, "x ![a](https://h/a.png) y")
    fetch = _fake_fetch({"https://h/a.png": (b"raw", "png")})
    localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch)
    second = localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch)
    assert second == {"total": 0, "localized": 0, "skipped": 0,
                      "failed": 0, "capped": False}


def test_localize_greeting_respects_cap_and_dedups(tmp_path):
    # same URL twice = one download; cap=1 still localizes both spans
    gid = _greeting_with(
        tmp_path, "![a](https://h/a.png) ![b](https://h/a.png) ![c](https://h/c.png)")
    calls = []

    def fetch(url):
        calls.append(url)
        return (b"raw-" + url.encode(), "png")

    summary = localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch, cap=1)
    assert calls == ["https://h/a.png"]
    assert summary == {"total": 3, "localized": 2, "skipped": 1,
                       "failed": 0, "capped": True}
