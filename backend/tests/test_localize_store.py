from grimoire.store import localize


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
