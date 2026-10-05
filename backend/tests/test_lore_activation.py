"""The activation engine (`store/context/activation.py`): per-post key bitmaps
that agree with today's joined-text `keyword_hit`, the four key-logic
operators, per-entry scan depth, and the sticky/cooldown machine replayed from
the start of the scene."""
import pytest

from grimoire.store.context import activation
from grimoire.store.context.activation import KeyIndex, direct_match, timed_state
from grimoire.store.context.world_state import keyword_hit
from grimoire.store.lore_fields import Controls


def _posts(texts, start=0):
    return [(start + i, t) for i, t in enumerate(texts)]


def _entry(keys, **controls):
    return {"body": "lore", "keys": list(keys), "owners": [], "secrecy": "",
            "kind": "lore", "id": "x", "name": "X",
            "controls": Controls(**controls)}


def _state_at(texts, b, entry, depth, seed=""):
    """The machine's state at boundary `b`, as the current turn would see it
    with only the first `b` posts written."""
    return timed_state(entry, KeyIndex(_posts(texts[:b]), seed), depth)


# --- §5.4: the bitmap is exact ------------------------------------------------

# "gate c" would match only across the join of the last two posts -- which the
# newline that joins them prevents, so per-post matching must refuse it too.
EQUIV_KEYS = ["Saltmarch's", "C++", "Café", "harbour", "gate c"]
EQUIV_POSTS = ["at Saltmarch's gate", "c++ notes", "CAFÉ", "harbourmaster",
               "at the gate", "c notes"]


@pytest.mark.parametrize("seed", ["", "the harbour"])
@pytest.mark.parametrize("depth", range(len(EQUIV_POSTS) + 1))
def test_bitmap_matches_joined_text(depth, seed):
    idx = KeyIndex(_posts(EQUIV_POSTS), seed)
    window_posts = EQUIV_POSTS[-depth:] if depth else []
    joined = "\n".join([*window_posts, seed]).strip()
    # The whole list, and each key alone, so a key that matches nowhere cannot
    # hide behind one that matches everywhere.
    for keys in [EQUIV_KEYS, *([k] for k in EQUIV_KEYS)]:
        got = idx.newest(keys, idx.n, depth) is not None
        assert got == keyword_hit(keys, joined), (keys, depth, seed)


def test_newest_reports_the_newest_post_slot():
    idx = KeyIndex(_posts(["Saltmarch", "nothing", "Saltmarch again", "quiet"]), "")
    assert idx.newest(["saltmarch"], idx.n, 4) == ("saltmarch", 2)
    assert idx.newest(["saltmarch"], idx.n, 1) is None
    assert idx.post_index(2) == 2


def test_newest_prefers_the_seed_when_it_matches():
    idx = KeyIndex(_posts(["Saltmarch"]), "toward Saltmarch")
    seed_slot = idx.n - 1
    assert idx.newest(["Saltmarch"], idx.n, 1) == ("Saltmarch", seed_slot)
    assert idx.post_index(seed_slot) is None
    # A boundary short of the current one never sees the seed.
    assert idx.newest(["Saltmarch"], 1, 1) == ("Saltmarch", 0)


def test_hits_are_memoised_per_key(monkeypatch):
    calls = []
    real = activation._compile

    def counting(key):
        calls.append(key)
        return real(key)

    monkeypatch.setattr(activation, "_compile", counting)
    idx = KeyIndex(_posts(["a", "b", "c"]), "")
    idx.hits("Mara")
    idx.hits("Mara")
    idx.newest(["Mara"], idx.n, 3)
    assert calls == ["Mara"]


# --- key logic ---------------------------------------------------------------

@pytest.mark.parametrize("logic, hit", [
    ("and_any", True),
    ("and_all", False),
    ("not_any", False),
    ("not_all", True),
])
def test_key_logic_four_operators(logic, hit):
    idx = KeyIndex(_posts(["tide and storm"]), "")
    entry = _entry(["tide"], secondary_keys=("storm", "ship"), key_logic=logic)
    got = direct_match(entry, idx, idx.n, 1)
    assert (got is not None) == hit
    if got is not None:
        assert got["key"] == "tide" and got["slot"] == 0
        assert got["secondary"] == ("storm" if logic == "and_any" else None)


@pytest.mark.parametrize("logic", ["and_any", "and_all", "not_any", "not_all"])
def test_key_logic_without_secondary_is_the_primary_alone(logic):
    on = KeyIndex(_posts(["tide and storm"]), "")
    off = KeyIndex(_posts(["calm water"]), "")
    entry = _entry(["tide"], key_logic=logic)
    assert direct_match(entry, on, on.n, 1) == {"key": "tide", "secondary": None, "slot": 0}
    assert direct_match(entry, off, off.n, 1) is None


def test_and_all_reports_the_newest_secondary():
    idx = KeyIndex(_posts(["ship", "storm", "tide"]), "")
    entry = _entry(["tide"], secondary_keys=("storm", "ship"), key_logic="and_all")
    assert direct_match(entry, idx, idx.n, 3) == {"key": "tide", "secondary": "storm", "slot": 2}


def test_secondary_is_matched_within_the_same_window():
    # The secondary key is one post older than the entry's window reaches.
    idx = KeyIndex(_posts(["storm", "tide"]), "")
    entry = _entry(["tide"], secondary_keys=("storm",), key_logic="and_any")
    assert direct_match(entry, idx, idx.n, 1) is None
    assert direct_match(entry, idx, idx.n, 2) is not None


def test_keyless_entry_is_not_a_direct_match():
    idx = KeyIndex(_posts(["anything"]), "")
    assert direct_match(_entry([]), idx, idx.n, 1) is None


def test_entry_without_controls_reads_as_defaults():
    idx = KeyIndex(_posts(["tide"]), "")
    entry = {"body": "lore", "keys": ["tide"], "owners": []}
    assert direct_match(entry, idx, idx.n, 1) == {"key": "tide", "secondary": None, "slot": 0}
    assert timed_state(entry, idx, 1)["state"] == "active"


# --- windows -------------------------------------------------------------------

def test_per_entry_scan_depth_overrides_global():
    texts = ["calm", "calm", "calm", "Winifred", "calm"]  # key in post n-2
    idx = KeyIndex(_posts(texts), "")
    assert direct_match(_entry(["Winifred"], scan_depth=1), idx, idx.n, 3) is None
    assert direct_match(_entry(["Winifred"], scan_depth=3), idx, idx.n, 1) == {
        "key": "Winifred", "secondary": None, "slot": 3}


def test_scan_depth_zero_sees_only_seed():
    idx = KeyIndex(_posts(["Seraphine was here"]), "ask about Mara")
    assert direct_match(_entry(["Seraphine"]), idx, idx.n, 0) is None
    assert direct_match(_entry(["Mara"]), idx, idx.n, 0) == {
        "key": "Mara", "secondary": None, "slot": 1}
    assert direct_match(_entry(["Seraphine"], scan_depth=0), idx, idx.n, 5) is None
    no_seed = KeyIndex(_posts(["Seraphine was here"]), "")
    assert direct_match(_entry(["Seraphine"]), no_seed, no_seed.n, 0) is None


def test_empty_scene_with_seed():
    idx = KeyIndex([], "the road to Saltmarch")
    assert idx.n == 1
    assert idx.boundaries() == [1]
    assert idx.age(0) == 0
    assert idx.post_index(0) is None
    entry = _entry(["Saltmarch"], sticky=2, cooldown=1)
    assert direct_match(entry, idx, idx.n, 4) == {"key": "Saltmarch", "secondary": None, "slot": 0}
    assert timed_state(entry, idx, 4) == {
        "state": "active", "fresh": True, "from_slot": 0, "remaining": 3}


def test_empty_scene_without_seed_is_ready():
    idx = KeyIndex([], "")
    assert idx.n == 0
    entry = _entry(["Saltmarch"], sticky=2)
    assert direct_match(entry, idx, idx.n, 4) is None
    assert timed_state(entry, idx, 4) == {
        "state": "ready", "fresh": False, "from_slot": None, "remaining": 0}


def test_seed_age_follows_transcript_indices():
    idx = KeyIndex([(4, "Mara speaks"), (5, "Mara listens")], "and Mara?")
    seed_slot = idx.n - 1
    assert seed_slot == 2
    assert idx.age(0) == 4 and idx.age(1) == 5
    assert idx.age(seed_slot) == 6
    assert idx.age(seed_slot) > idx.age(1)


# --- §5.3: the derived machine ----------------------------------------------

def test_sticky_carries_then_expires():
    texts = ["calm"] * 8
    texts[3] = "the lantern glows"
    entry = _entry(["lantern"], sticky=2)
    assert _state_at(texts, 4, entry, 1) == {
        "state": "active", "fresh": True, "from_slot": 3, "remaining": 3}
    assert _state_at(texts, 5, entry, 1) == {
        "state": "active", "fresh": False, "from_slot": 3, "remaining": 2}
    assert _state_at(texts, 6, entry, 1) == {
        "state": "active", "fresh": False, "from_slot": 3, "remaining": 1}
    assert _state_at(texts, 7, entry, 1)["state"] == "ready"
    assert _state_at(texts, 8, entry, 1)["state"] == "ready"


def test_cooldown_blocks_a_match():
    texts = ["calm"] * 8
    for i in (3, 4, 5, 6):
        texts[i] = "the lantern glows"
    entry = _entry(["lantern"], cooldown=2)
    assert _state_at(texts, 4, entry, 1) == {
        "state": "active", "fresh": True, "from_slot": 3, "remaining": 1}
    # The key matches at boundaries 5 and 6 as well; the cooldown says no.
    assert _state_at(texts, 5, entry, 1) == {
        "state": "cooling", "fresh": False, "from_slot": None, "remaining": 2}
    assert _state_at(texts, 6, entry, 1) == {
        "state": "cooling", "fresh": False, "from_slot": None, "remaining": 1}
    assert _state_at(texts, 7, entry, 1) == {
        "state": "active", "fresh": True, "from_slot": 6, "remaining": 1}


def test_cooldown_expiry_without_a_match_is_ready():
    texts = ["calm"] * 8
    for i in (3, 4, 5):
        texts[i] = "the lantern glows"
    entry = _entry(["lantern"], cooldown=2)
    assert _state_at(texts, 7, entry, 1)["state"] == "ready"


def test_sticky_is_not_refreshed_by_a_match_while_active():
    texts = ["lantern", "lantern", "lantern", "calm"]
    entry = _entry(["lantern"], sticky=1)
    assert _state_at(texts, 2, entry, 1) == {
        "state": "active", "fresh": False, "from_slot": 0, "remaining": 1}
    # Exhausted at boundary 3: it ages out first, then the match re-arms it.
    assert _state_at(texts, 3, entry, 1) == {
        "state": "active", "fresh": True, "from_slot": 2, "remaining": 2}


def test_sticky_then_cooldown_ages_before_matching():
    # Exhausted active cools before the match at the same boundary is considered.
    texts = ["lantern", "lantern", "lantern"]
    entry = _entry(["lantern"], sticky=1, cooldown=1)
    assert _state_at(texts, 3, entry, 1) == {
        "state": "cooling", "fresh": False, "from_slot": None, "remaining": 1}


def test_replay_is_exact_from_scene_start():
    texts = ["the lantern glows", "calm", "calm", "the lantern again"]
    entry = _entry(["lantern"], sticky=1, cooldown=3)
    # A replay that started "ready" a post or two back would re-trigger here.
    assert _state_at(texts, 4, entry, 1) == {
        "state": "cooling", "fresh": False, "from_slot": None, "remaining": 2}


def test_director_note_counts_as_a_post():
    texts = ["calm", "[Director] bring the lantern in", "calm"]
    entry = _entry(["lantern"], sticky=1)
    assert _state_at(texts, 2, entry, 1) == {
        "state": "active", "fresh": True, "from_slot": 1, "remaining": 2}
    assert _state_at(texts, 3, entry, 1) == {
        "state": "active", "fresh": False, "from_slot": 1, "remaining": 1}


def test_seed_boundary_is_the_last_one_replayed():
    assert KeyIndex(_posts(["a", "b", "c"]), "").boundaries() == [1, 2, 3]
    assert KeyIndex(_posts(["a", "b", "c"]), "seed").boundaries() == [1, 2, 4]
    assert KeyIndex([], "").boundaries() == []
    entry = _entry(["lantern"], sticky=1)
    idx = KeyIndex(_posts(["the lantern", "calm"]), "")
    assert timed_state(entry, idx, 1)["fresh"] is False
    seeded = KeyIndex(_posts(["calm", "calm"]), "light the lantern")
    assert timed_state(entry, seeded, 1) == {
        "state": "active", "fresh": True, "from_slot": 2, "remaining": 2}


def test_keyless_entry_is_never_timed():
    idx = KeyIndex(_posts(["calm"]), "")
    assert timed_state(_entry([], sticky=3), idx, 1) == {
        "state": "ready", "fresh": False, "from_slot": None, "remaining": 0}


@pytest.mark.parametrize("controls, expected", [
    ({"cooldown": 1}, {"state": "active", "fresh": True, "from_slot": 1, "remaining": 1}),
    ({"sticky": 1}, {"state": "active", "fresh": True, "from_slot": 1, "remaining": 2}),
])
def test_a_non_matching_seed_replaces_the_last_boundary(controls, expected):
    # A seed is the current turn's text, not a turn of its own: the seeded
    # boundary stands where boundary P would, so the newest post is counted
    # once and nothing ages an extra step.
    posts = _posts(["calm", "the lantern"])
    entry = _entry(["lantern"], **controls)
    unseeded = timed_state(entry, KeyIndex(posts, ""), 1)
    seeded = timed_state(entry, KeyIndex(posts, "go on"), 1)
    assert unseeded == expected
    assert seeded == unseeded


def test_a_matching_seed_activates_at_the_current_boundary():
    posts = _posts(["the lantern", "calm"])
    entry = _entry(["lantern"], sticky=1, cooldown=1)
    # The seeded boundary replaces boundary 2, where the post-0 activation is
    # still carried by sticky -- not boundary 3, where it would be cooling.
    assert timed_state(entry, KeyIndex(posts, "go on"), 1) == {
        "state": "active", "fresh": False, "from_slot": 0, "remaining": 1}
    later = _posts(["the lantern", "calm", "calm", "calm"])
    assert timed_state(entry, KeyIndex(later, "light the lantern"), 1) == {
        "state": "active", "fresh": True, "from_slot": 4, "remaining": 2}


def test_negative_cooldown_cannot_cool_forever():
    # Hand-built: `parse` never yields one, but the machine must still end.
    texts = ["the lantern", "calm", "calm", "the lantern"]
    entry = _entry(["lantern"], cooldown=-1)
    assert _state_at(texts, 4, entry, 1)["state"] == "active"
