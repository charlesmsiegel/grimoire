"""Transcript expansion (roadmap 08-C3b, `store/searchdocs/expand.py`).

What these pin: the caller names the phase and nothing else is accepted; the
whole transcript is viewed before anything is matched; hidden posts and director
notes never come back; each post carries a key a cut cannot move; and every
budget holds whatever the transcript's length, with no cut splitting a word.
Placeholder names only.
"""

import pytest

from grimoire.store import campaigns, regex, scenes, worlds
from grimoire.store.scenes import serialize
from grimoire.store.searchdocs import expand

THINK = {"name": "Strip ledgers", "pattern": r"<ledger>[\s\S]*?</ledger>", "replacement": ""}


@pytest.fixture
def cid(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign("Run", wid)


def _scene(cid, messages, title="Saltmarch"):
    sid = scenes.create_scene(cid, title)
    scenes.append_messages(cid, sid, messages)
    return sid


def _user(text, n):
    return {"role": "user", "content": text, "post_id": f"post{n:02d}"}


def _reply(text, n, speaker="Mara", part=None):
    m = {"role": "assistant", "speaker": speaker, "content": text, "response_id": f"resp{n:02d}"}
    if part is not None:
        m["response_part"] = part
    return m


def _plain(n):
    return [_user(f"Seraphine asks about item {i}.", i) if i % 2 == 0
            else _reply(f"Mara answers about item {i}.", i) for i in range(n)]


# --- the phase ------------------------------------------------------------------

def test_the_phase_is_required_and_checked(cid):
    sid = _scene(cid, _plain(4))
    with pytest.raises(TypeError):
        expand.expand(cid, sid, terms=["item"])  # type: ignore[call-arg]
    for bad in ("store", "", "Prompt", None):
        with pytest.raises(ValueError):
            expand.expand(cid, sid, phase=bad, terms=["item"])
    with pytest.raises(ValueError):
        expand.expand(cid, sid, phase="prompt", terms=["item"], fallback="head")


def test_a_term_a_prompt_rule_strips_matches_only_in_display(cid):
    messages = _plain(6)
    messages[3] = _reply("Mara counts. <ledger>the tide tax twice</ledger> Done.", 3)
    sid = _scene(cid, messages)
    regex.layers.write_campaign(cid, {"rules": [{**THINK, "applies": ["prompt"]}]})
    prompt = expand.expand(cid, sid, phase="prompt", terms=["tide tax"])
    display = expand.expand(cid, sid, phase="display", terms=["tide tax"])
    assert prompt.mode == "empty" and not prompt.excerpts
    assert display.mode == "matched"
    assert "tide tax" in display.excerpts[0].text


def test_a_depth_rule_gives_the_same_text_as_the_context_builders_view(cid):
    """The context builder views the WHOLE transcript before projecting it, so
    a depth-bounded rule sees each post at its depth in the whole; so must the
    excerpts."""
    messages = _plain(10)
    messages[1] = _reply("Mara: <ledger>old entry</ledger> still here.", 1)
    messages[9] = _reply("Mara: <ledger>new entry</ledger> still here.", 9)
    sid = _scene(cid, messages)
    # Only the newest three messages are rewritten (depth 0..2).
    regex.layers.write_campaign(cid, {"rules": [{**THINK, "max_depth": 2}]})
    got = expand.expand(cid, sid, phase="prompt", terms=["still here"], max_excerpts=3,
                        max_posts=8)
    contents = {p.index: p.content for e in got.excerpts for p in e.posts}
    raw = scenes.read_scene(cid, sid)["messages"]
    viewed = regex.view.view(raw, cid=cid, phase="prompt")
    assert contents[9] == viewed[9]["content"] and "new entry" not in contents[9]
    assert contents[1] == viewed[1]["content"] and "old entry" in contents[1]


# --- eligibility and keys -------------------------------------------------------

def test_hidden_posts_and_director_notes_never_appear(cid):
    messages = _plain(8)
    messages[3]["excluded"] = "2026-10-01T00:00:00Z"
    messages[3]["content"] = "A hidden harbour word."
    messages.insert(5, {"role": "assistant", "speaker": serialize.DIRECTOR_SPEAKER,
                        "content": "Note: mention the harbour."})
    sid = _scene(cid, messages)
    for phase in ("prompt", "display"):
        got = expand.expand(cid, sid, phase=phase, terms=["harbour", "item"],
                            max_posts=20, max_excerpts=5, max_bytes=20000, allow_whole=True)
        assert got.mode == "whole" and got.total == 9 and got.eligible == 7
        indices = [p.index for e in got.excerpts for p in e.posts]
        assert 3 not in indices and 5 not in indices
        text = "\n".join(e.text for e in got.excerpts)
        assert "harbour" not in text
        assert indices == sorted(indices) and 8 in indices   # absolute indices


def test_keys_survive_a_cut_and_parts_share_their_reply(cid):
    messages = [
        {"role": "user", "content": "An old legacy line."},
        _user("Seraphine opens the ledger.", 1),
        _reply("Mara reads the first column.", 2, part=0),
        _reply("Winifred reads the second column.", 2, speaker="Winifred", part=1),
        _user("Seraphine closes the ledger.", 4),
    ]
    sid = _scene(cid, messages)
    got = expand.expand(cid, sid, phase="prompt", terms=["column", "ledger", "legacy"],
                        allow_whole=True, max_bytes=8000)
    posts = {p.index: p for e in got.excerpts for p in e.posts}
    assert posts[0].key == ""
    assert posts[1].key == "p-post01"
    assert posts[2].key == posts[3].key == "r-resp02"
    assert (posts[2].part, posts[3].part) == (0, 1)
    before = {p.key: p.index for p in posts.values() if p.key}

    # A cut ahead of the excerpt renumbers it; the key stays where it was. The
    # legacy line carries no metadata, so removing its block is the whole cut.
    path = scenes.paths._scene_path(cid, sid)
    raw = path.read_text(encoding="utf-8")
    assert raw.count("An old legacy line.") == 1
    path.write_text(raw.replace("**You:** An old legacy line.\n\n", "", 1), encoding="utf-8")
    after = expand.expand(cid, sid, phase="prompt", terms=["column"], max_bytes=8000)
    moved = {p.key: p.index for e in after.excerpts for p in e.posts if p.key}
    assert moved["r-resp02"] == before["r-resp02"] - 1


def test_images_are_alt_text_in_the_prompt_and_markdown_in_display(cid):
    messages = _plain(4)
    messages[1] = _reply("Mara shows ![a salt-stained ledger](image-refs/x.json) here.", 1)
    sid = _scene(cid, messages)
    prompt = expand.expand(cid, sid, phase="prompt", terms=["ledger"])
    display = expand.expand(cid, sid, phase="display", terms=["ledger"])
    hit_p = next(p for p in prompt.excerpts[0].posts if p.index == 1)
    hit_d = next(p for p in display.excerpts[0].posts if p.index == 1)
    assert "![" not in hit_p.content and "a salt-stained ledger" in hit_p.content
    assert "![a salt-stained ledger](image-refs/x.json)" in hit_d.content


# --- budgets --------------------------------------------------------------------

def _long_transcript(n=60):
    out = []
    filler = " ".join(f"word{k}" for k in range(500))
    for i in range(n):
        body = f"{filler} harbour-{i % 7} {filler}"
        out.append(_user(body, i) if i % 2 == 0 else _reply(body, i))
    return out


def test_the_budgets_hold_on_a_long_transcript(cid):
    sid = _scene(cid, _long_transcript())
    terms = [f"harbour-{k}" for k in range(7)]
    for max_bytes in (400, 1200, 4000, 9000):
        for max_posts, max_excerpts, radius in ((8, 3, 1), (3, 5, 2), (20, 2, 0)):
            got = expand.expand(cid, sid, phase="prompt", terms=terms, max_bytes=max_bytes,
                                max_posts=max_posts, max_excerpts=max_excerpts, radius=radius)
            assert got.bytes <= max_bytes
            assert got.bytes == sum(len(e.text.encode()) for e in got.excerpts)
            assert len(got.excerpts) <= max_excerpts
            assert sum(len(e.posts) for e in got.excerpts) <= max_posts
            for e in got.excerpts:
                for p in e.posts:
                    assert len(p.content.encode()) <= expand.MESSAGE_BYTES
                    body = p.content.strip("…")
                    # No cut splits a word: every token is one the post holds.
                    assert all(tok.startswith(("word", "harbour-")) for tok in body.split())
                    whole = set(_long_transcript()[p.index]["content"].split())
                    assert set(body.split()) <= whole
                    assert p.clipped
            again = expand.expand(cid, sid, phase="prompt", terms=terms, max_bytes=max_bytes,
                                  max_posts=max_posts, max_excerpts=max_excerpts, radius=radius)
            assert again == got


def test_windows_merge_and_respect_the_radius(cid):
    messages = _plain(20)
    messages[6] = _reply("Mara mentions the tide tax.", 6)
    messages[8] = _user("Seraphine repeats: the tide tax.", 8)
    messages[15] = _reply("Winifred: tide tax again.", 15, speaker="Winifred")
    sid = _scene(cid, messages)
    got = expand.expand(cid, sid, phase="prompt", terms=["tide tax"], radius=1,
                        max_posts=12, max_bytes=8000)
    ranges = [(e.start, e.end) for e in got.excerpts]
    assert ranges == [(5, 10), (14, 17)]        # 5..9 merged from two hits, adjacent
    assert all(e.matched == ("tide tax",) for e in got.excerpts)


def test_a_single_overlong_token_is_cut_at_a_character_boundary(cid):
    token = "ß" * 3000
    messages = _plain(3)
    messages[1] = _reply(f"seal {token}", 1)
    sid = _scene(cid, messages)
    got = expand.expand(cid, sid, phase="prompt", terms=["seal"], radius=0)
    post = got.excerpts[0].posts[0]
    assert post.clipped and len(post.content.encode()) <= expand.MESSAGE_BYTES
    post.content.encode("utf-8")   # a whole number of characters


def test_a_budget_below_the_floor_reports_exhaustion(cid):
    messages = _plain(4)
    messages[1] = _reply("Mara " + "speaks at length " * 60 + "of the tide tax.", 1)
    sid = _scene(cid, messages)
    got = expand.expand(cid, sid, phase="prompt", terms=["tide tax"],
                        max_bytes=expand.MIN_POST_BYTES - 1)
    assert got.mode == "empty" and got.budget_exhausted is True and not got.excerpts


# --- the scene ------------------------------------------------------------------

def test_a_renamed_scene_is_found_by_identity_and_a_deleted_one_is_none(cid):
    sid = _scene(cid, _plain(4))
    ident = scenes.ensure_identity(cid, sid)
    new_sid = scenes.rename_scene(cid, sid, "The ledger at Saltmarch")
    assert new_sid != sid
    got = expand.expand(cid, sid, phase="prompt", terms=["item"], identity=ident)
    assert got is not None and got.sid == new_sid and got.identity == ident
    scenes.delete_scene(cid, new_sid)
    assert expand.expand(cid, new_sid, phase="prompt", terms=["item"], identity=ident) is None
    assert expand.expand(cid, new_sid, phase="prompt", terms=["item"]) is None


def test_a_scene_that_took_the_id_is_not_the_one_asked_for(cid):
    sid = _scene(cid, _plain(4))
    ident = scenes.ensure_identity(cid, sid)
    scenes.delete_scene(cid, sid)
    again = _scene(cid, _plain(4))
    assert again == sid                          # the number was handed on
    scenes.ensure_identity(cid, again)
    assert expand.expand(cid, sid, phase="prompt", terms=["item"], identity=ident) is None


# --- no match -------------------------------------------------------------------

def test_no_match_is_empty_or_the_tail(cid):
    sid = _scene(cid, _plain(10))
    empty = expand.expand(cid, sid, phase="prompt", terms=["nothing here"])
    assert empty.mode == "empty" and not empty.excerpts and empty.budget_exhausted is False
    tail = expand.expand(cid, sid, phase="prompt", terms=["nothing here"], fallback="tail")
    assert tail.mode == "tail"
    assert [p.index for p in tail.excerpts[0].posts] == [7, 8, 9]


def test_whole_only_when_asked_and_when_it_fits(cid):
    sid = _scene(cid, _plain(6))
    assert expand.expand(cid, sid, phase="prompt", terms=["item 3"]).mode == "matched"
    whole = expand.expand(cid, sid, phase="prompt", terms=["item 3"], allow_whole=True)
    assert whole.mode == "whole" and [p.index for p in whole.excerpts[0].posts] == list(range(6))
    tight = expand.expand(cid, sid, phase="prompt", terms=["item 3"], allow_whole=True,
                          max_posts=4)
    assert tight.mode == "matched"


def test_excerpt_text_is_the_transcript_render(cid):
    sid = _scene(cid, _plain(3))
    got = expand.expand(cid, sid, phase="prompt", terms=["item 1"])
    text = got.excerpts[0].text
    assert "**Mara:** Mara answers about item 1." in text
    # An unstamped player post is rendered under the role label with nobody seated.
    assert "**You:** Seraphine asks about item 0." in text


def test_a_hit_is_framed_on_a_term_that_matches_only_by_case_folding(cid):
    """`casefold` maps "ß" to "ss", which a case-insensitive regex does not:
    a post scored a hit on "strasse" must be framed on its "Straße"."""
    filler = " ".join(f"word{k}" for k in range(600))
    messages = _plain(3)
    messages[1] = _reply(f"{filler} the old Hauptstraße gate {filler}", 1)
    sid = _scene(cid, messages)
    got = expand.expand(cid, sid, phase="prompt", terms=["hauptstrasse"], radius=0)
    post = got.excerpts[0].posts[0]
    assert post.clipped and "Hauptstraße" in post.content
    assert got.excerpts[0].matched == ("hauptstrasse",)


def test_damage_under_the_read_is_none_not_an_exception(cid):
    sid = _scene(cid, _plain(4))
    path = scenes.paths._scene_path(cid, sid)
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b"item 1", b"item \xff\xfe"))
    assert expand.expand(cid, sid, phase="prompt", terms=["item"]) is None
    path.write_bytes(raw)
    (campaigns.campaign_root(cid) / "appearances.json").write_text("[1, 2]", encoding="utf-8")
    assert expand.expand(cid, sid, phase="prompt", terms=["item"]) is None


def test_a_spent_budget_still_reports_that_matches_existed(cid):
    sid = _scene(cid, _plain(4))
    for limits in ({"max_bytes": 0}, {"max_posts": 0}, {"max_excerpts": 0}):
        hit = expand.expand(cid, sid, phase="prompt", terms=["item 1"], **limits)
        assert hit.mode == "empty" and hit.budget_exhausted is True, limits
        miss = expand.expand(cid, sid, phase="prompt", terms=["nowhere"], **limits)
        assert miss.mode == "empty" and miss.budget_exhausted is False, limits


def test_adjacent_hits_each_claim_their_own_context(cid):
    messages = _plain(12)
    messages[5] = _reply("Mara mentions the tide tax.", 5)
    messages[6] = _user("Seraphine repeats: the tide tax.", 6)
    sid = _scene(cid, messages)
    got = expand.expand(cid, sid, phase="prompt", terms=["tide tax"], radius=1,
                        max_bytes=8000, max_posts=8)
    assert [(e.start, e.end) for e in got.excerpts] == [(4, 8)]


def test_a_whole_transcript_that_exactly_fits_is_whole(cid):
    sid = _scene(cid, _plain(3))
    roomy = expand.expand(cid, sid, phase="prompt", terms=["item"], allow_whole=True,
                          max_bytes=8000)
    assert roomy.mode == "whole"
    exact = expand.expand(cid, sid, phase="prompt", terms=["item"], allow_whole=True,
                          max_bytes=roomy.bytes)
    assert exact.mode == "whole" and exact.bytes == roomy.bytes
