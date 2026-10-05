"""The activation engine (`store/context/activation.py`): per-post key bitmaps
that agree with today's joined-text `keyword_hit`, the four key-logic
operators, per-entry scan depth, the sticky/cooldown machine replayed from
the start of the scene, and `run` -- one turn in levels, with recursion,
structural and activated presence, reasons and recall."""
import random

import pytest

from grimoire.store import lore_fields
from grimoire.store.context import activation, semantic
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


# --- run: one turn in levels (§5.2, §7) ---------------------------------------

def _e(kind, eid, keys=(), body="", owners=(), refs=None, secrecy="", **controls):
    e = {"body": body, "keys": list(keys), "owners": list(owners), "secrecy": secrecy,
         "kind": kind, "id": eid, "name": eid, "controls": Controls(**controls)}
    if refs is not None:
        e["refs"] = refs
    return e


def _run(entries, texts=(), seed="", present=None, *, rec=0, depth=4, loc=None,
         start=0, **kw):
    return activation.run(entries, _posts(texts, start), seed, present or {},
                          scan_depth=depth, recursion_depth=rec,
                          current_location=loc, **kw)


def _refs(hits):
    return [h.ref for h in hits]


def _by_ref(hits):
    return {h.ref: h for h in hits}


def test_order_is_gm_only_exclude_pin_owner_keys():
    seen = {}

    def spy(candidates, text):
        seen["refs"] = [activation._ref(c) for c in candidates]
        seen["text"] = text
        return []

    entries = [
        _e("lore", "gm", keys=["tide"], secrecy="gm-only"),       # pinned, still out
        _e("lore", "gone", keys=["tide"]),                          # excluded key hit
        _e("lore", "gone_miss", keys=["blade"]),                    # excluded miss
        _e("lore", "named", keys=["blade"], owners=["characters:mara"]),  # pinned
        _e("lore", "owned", keys=["tide"], owners=["characters:mara"]),
        _e("lore", "owned_miss", keys=["blade"], owners=["characters:mara"]),
        _e("lore", "hit", keys=["tide"]),
        _e("lore", "always"),
        _e("lore", "miss", keys=["blade"]),
    ]
    got = _run(entries, ["the tide turns"],
               pinned_refs=frozenset({"lore:gm", "lore:named"}),
               excluded_refs=frozenset({"lore:gone", "lore:gone_miss"}),
               recall=spy, recall_text="the tide turns")
    # gm-only beats a pin; a pin beats the owner gate; an absent owner beats keys.
    assert _refs(got.keyword) == ["lore:named", "lore:hit", "lore:always"]
    hits = _by_ref(got.keyword)
    assert hits["lore:named"].reason == {"type": "pinned"}
    assert hits["lore:hit"].reason["type"] == "key"
    assert hits["lore:always"].reason == {"type": "keyless"}
    # An exclude beats recall, and an absent owner's lore is never offered.
    assert seen == {"refs": ["lore:miss"], "text": "the tide turns"}
    assert got.recalled == [] and got.held_back == []


def test_activated_preserves_prompt_order():
    entries = [
        _e("lore", "owned", owners=["groups:guild"]),               # level 1, unlock
        _e("lore", "pulled", keys=["beacon"]),                      # level 1, recursion
        _e("groups", "guild", keys=["guild"], body="the beacon"),   # level 0
        _e("lore", "always"),                                       # level 0
    ]
    got = _run(entries, ["the guild meets"], rec=1)
    assert _refs(got.keyword) == ["lore:owned", "lore:pulled", "groups:guild", "lore:always"]
    assert [h.level for h in got.keyword] == [1, 1, 0, 0]


def test_recursion_depth_cap_and_cycle():
    entries = [_e("lore", "a", keys=["alpha"], body="it speaks of beta"),
               _e("lore", "b", keys=["beta"], body="it speaks of alpha")]
    assert _refs(_run(entries, ["alpha appears"], rec=0).keyword) == ["lore:a"]
    for depth in (1, 3):
        got = _run(entries, ["alpha appears"], rec=depth)
        assert _refs(got.keyword) == ["lore:a", "lore:b"]
        b = _by_ref(got.keyword)["lore:b"]
        assert b.reason == {"type": "recursion", "via": "lore:a", "key": "beta"}
        assert (b.level, b.direct, b.age) == (1, False, -1)


def test_a_pin_pulls_from_the_next_level():
    entries = [_e("lore", "a", keys=["nowhere"], body="beta"),
               _e("lore", "b", keys=["beta"])]
    pin = frozenset({"lore:a"})
    assert _refs(_run(entries, ["calm"], rec=0, pinned_refs=pin).keyword) == ["lore:a"]
    got = _run(entries, ["calm"], rec=1, pinned_refs=pin)
    assert [(h.ref, h.level) for h in got.keyword] == [("lore:a", 0), ("lore:b", 1)]
    assert got.keyword[1].reason == {"type": "recursion", "via": "lore:a", "key": "beta"}


def test_recursion_reaches_one_level_per_step():
    entries = [_e("lore", "a", keys=["alpha"], body="beta"),
               _e("lore", "b", keys=["beta"], body="gamma"),
               _e("lore", "c", keys=["gamma"], body="")]
    assert _refs(_run(entries, ["alpha"], rec=1).keyword) == ["lore:a", "lore:b"]
    got = _run(entries, ["alpha"], rec=2)
    assert [(h.ref, h.level) for h in got.keyword] == [
        ("lore:a", 0), ("lore:b", 1), ("lore:c", 2)]
    assert got.keyword[2].reason["via"] == "lore:b"


def test_recursion_via_is_first_puller_in_input_order():
    entries = [_e("lore", "pulled", keys=["beta"]),
               _e("lore", "second", keys=["two"], body="beta"),
               _e("lore", "first", keys=["one"], body="beta")]
    got = _run(entries, ["one two"], rec=1)
    assert _by_ref(got.keyword)["lore:pulled"].reason["via"] == "lore:second"


def test_recursion_applies_key_logic_to_the_body():
    pulled = _e("lore", "b", keys=["beta"], secondary_keys=("storm",), key_logic="and_any")
    alone = [_e("lore", "a", keys=["alpha"], body="beta only"), pulled]
    assert _refs(_run(alone, ["alpha storm"], rec=1).keyword) == ["lore:a"]
    both = [_e("lore", "a", keys=["alpha"], body="beta in a storm"), pulled]
    assert _refs(_run(both, ["alpha"], rec=1).keyword) == ["lore:a", "lore:b"]


@pytest.mark.parametrize("puller, pulled, expected", [
    ("both", "both", True),
    ("pulls_only", "both", True),
    ("pulled_only", "both", False),
    ("none", "both", False),
    ("both", "pulled_only", True),
    ("both", "pulls_only", False),
    ("both", "none", False),
])
def test_recursion_flags(puller, pulled, expected):
    entries = [_e("lore", "a", keys=["alpha"], body="beta", recursion=puller),
               _e("lore", "b", keys=["beta"], recursion=pulled)]
    got = _run(entries, ["alpha"], rec=1)
    assert ("lore:b" in _refs(got.keyword)) == expected


def test_recursion_flags_do_not_stop_a_direct_match():
    entries = [_e("lore", "a", keys=["alpha"], recursion="none")]
    assert _refs(_run(entries, ["alpha"], rec=1).keyword) == ["lore:a"]


def test_cooling_entry_cannot_be_pulled():
    # beta matched at boundary 1, so at the current boundary it is cooling.
    entries = [_e("lore", "a", keys=["alpha"], body="beta"),
               _e("lore", "b", keys=["beta"], cooldown=2)]
    got = _run(entries, ["beta", "alpha"], rec=1, depth=1, recall=lambda c, t: [(x, 0.9) for x in c],
               recall_text="beta")
    assert _refs(got.keyword) == ["lore:a"]
    assert got.recalled == []
    assert [(h.ref, h.reason) for h in got.held_back] == [
        ("lore:b", {"type": "cooldown", "remaining": 2})]
    # A pin is the reader saying yes, and it beats the cooldown.
    pinned = _run(entries, ["beta", "alpha"], rec=1, depth=1,
                  pinned_refs=frozenset({"lore:b"}))
    assert _by_ref(pinned.keyword)["lore:b"].reason == {"type": "pinned"}
    assert pinned.held_back == []


def test_presence_by_activation_unlocks_owned_lore_without_recursion():
    entries = [_e("groups", "guild", keys=["guild"]),
               _e("lore", "vault", keys=["vault"], owners=["groups:guild"])]
    got = _run(entries, ["the guild vault"], rec=0)
    assert _refs(got.keyword) == ["groups:guild", "lore:vault"]
    vault = _by_ref(got.keyword)["lore:vault"]
    assert vault.level == 1 and vault.direct is False and vault.age == -1
    assert vault.reason["type"] == "key" and vault.reason["key"] == "vault"
    assert vault.reason["owner"] == "groups:guild"
    assert vault.reason["owner_presence"] == {"type": "activated", "via": None}
    assert got.present["groups:guild"] == {"type": "activated", "via": None}
    # The guild unnamed: its lore stays silent.
    assert _refs(_run(entries, ["the vault"], rec=0).keyword) == []


def test_activated_lore_kind_is_not_present():
    entries = [_e("lore", "guild", keys=["guild"]),
               _e("lore", "vault", keys=["vault"], owners=["lore:guild"])]
    got = _run(entries, ["the guild vault"], rec=0)
    assert _refs(got.keyword) == ["lore:guild"]
    assert "lore:guild" not in got.present


def test_owner_reason_names_first_present_owner_in_entry_order():
    entries = [_e("lore", "x", owners=["characters:winifred", "characters:mara",
                                       "characters:seraphine"])]
    present = {"characters:seraphine": {"type": "cast"},
               "characters:mara": {"type": "cast"}}
    hit = _run(entries, [], present=present).keyword[0]
    assert hit.reason == {"type": "keyless", "owner": "characters:mara",
                          "owner_presence": {"type": "cast"}}
    assert hit.level == 0 and hit.direct is True


def _world():
    # Keyed, so none of them is always-on in `run`.
    return [
        _e("items", "lantern", keys=["lantern"], refs={"holder": ["groups:guild"]}),
        _e("items", "key", keys=["key"], refs={"holder": ["groups:guild", "characters:mara"]}),
        _e("items", "map", keys=["map"], refs={"holder": ["locations:saltmarch"]}),
        _e("items", "lost", keys=["lost"], refs={"holder": ["characters:winifred"]}),
        _e("groups", "guild", keys=["guild"], refs={"leader": ["characters:mara"]}),
        _e("lore", "note", keys=["note"]),
        _e("creatures", "gull", keys=["gull"]),
    ]


def test_structural_presence_fixed_point():
    base = {"characters:mara": {"type": "cast"},
            "locations:saltmarch": {"type": "current_location", "via": None}}
    expected = {
        **base,
        "groups:guild": {"type": "led_by", "via": "characters:mara"},
        "items:lantern": {"type": "held_by", "via": "groups:guild"},
        "items:key": {"type": "held_by", "via": "characters:mara"},
        "items:map": {"type": "held_by", "via": "locations:saltmarch"},
    }
    entries = _world()
    assert activation.structural_presence(entries, base, "saltmarch") == expected
    rng = random.Random(7)
    for _ in range(10):
        rng.shuffle(entries)
        assert activation.structural_presence(entries, base, "saltmarch") == expected
    assert base == {"characters:mara": {"type": "cast"},
                    "locations:saltmarch": {"type": "current_location", "via": None}}


def test_structural_presence_never_overwrites_a_reason():
    base = {"characters:mara": {"type": "cast"}, "groups:guild": {"type": "cast"}}
    got = activation.structural_presence(_world(), base, None)
    assert got["groups:guild"] == {"type": "cast"}


def test_structural_presence_unlocks_owned_lore():
    entries = [*_world(), _e("lore", "oil", owners=["items:lantern"])]
    got = _run(entries, [], present={"characters:mara": {"type": "cast"}})
    oil = _by_ref(got.keyword)["lore:oil"]
    assert oil.level == 0
    assert oil.reason["owner_presence"] == {"type": "held_by", "via": "groups:guild"}
    # Presence does not activate the item's own entry.
    assert _refs(got.keyword) == ["lore:oil"]


def test_group_present_by_headquarters():
    entries = [_e("groups", "watch", refs={"headquarters": ["locations:realm",
                                                            "locations:saltmarch"]})]
    got = activation.structural_presence(entries, {}, "saltmarch")
    assert got == {"groups:watch": {"type": "headquarters", "via": "locations:saltmarch"}}
    assert activation.structural_presence(entries, {}, "harbour") == {}
    assert activation.structural_presence(entries, {}, None) == {}


def test_creature_present_by_habitat():
    entries = [_e("creatures", "gull", refs={"habitat": ["locations:saltmarch"]})]
    got = activation.structural_presence(entries, {}, "saltmarch")
    assert got == {"creatures:gull": {"type": "habitat", "via": "locations:saltmarch"}}
    assert activation.structural_presence(entries, {}, "realm") == {}


def test_structural_rules_are_per_kind():
    # A lore entry with a holder, or an item with a leader, implies nothing.
    entries = [_e("lore", "odd", refs={"holder": ["characters:mara"]}),
               _e("items", "odd", refs={"leader": ["characters:mara"],
                                        "habitat": ["locations:saltmarch"]}),
               _e("items", "garbled", refs={"holder": "characters:mara"}),
               _e("items", "nothing", refs="characters:mara")]
    base = {"characters:mara": {"type": "cast"}}
    assert activation.structural_presence(entries, base, "saltmarch") == base


def test_absent_owner_beats_sticky():
    entries = [_e("lore", "x", keys=["lantern"], owners=["characters:mara"], sticky=3)]
    texts = ["the lantern", "calm"]
    present = {"characters:mara": {"type": "cast"}}
    here = _run(entries, texts, present=present, depth=1, start=10)
    assert here.keyword[0].reason == {
        "type": "sticky", "from_post": 10, "remaining": 3,
        "owner": "characters:mara", "owner_presence": {"type": "cast"}}
    gone = _run(entries, texts, depth=1, recall=lambda c, t: [(x, 1.0) for x in c],
                recall_text="lantern")
    assert gone.keyword == [] and gone.recalled == [] and gone.held_back == []


def test_recall_scores_reach_the_reason(monkeypatch):
    monkeypatch.setattr(semantic, "recall_scored", lambda c, t: [(c[0], 0.52)])
    entries = [_e("lore", "hit", keys=["tide"]), _e("lore", "miss", keys=["blade"])]
    got = _run(entries, ["the tide"], recall=semantic.recall_scored, recall_text="the tide")
    assert _refs(got.keyword) == ["lore:hit"]
    assert [(h.ref, h.reason) for h in got.recalled] == [
        ("lore:miss", {"type": "recall", "score": 0.52})]
    assert got.recalled[0].direct is False and got.recalled[0].age == -1


def test_recall_scored_is_recall_with_scores(monkeypatch):
    a, b = {"name": "A"}, {"name": "B"}
    monkeypatch.setattr(semantic, "recall_scored", lambda c, t: [(b, 0.9), (a, 0.5)])
    assert semantic.recall([a, b], "text") == [b, a]


def test_recall_gets_only_unactivated_gate_passing_keyed_entries():
    seen = {}

    def spy(candidates, text):
        seen["refs"] = [activation._ref(c) for c in candidates]
        seen["text"] = text
        return [(c, 0.5) for c in reversed(candidates)]

    entries = [
        _e("lore", "hit", keys=["tide"]),
        _e("lore", "always"),
        _e("lore", "miss", keys=["blade"]),
        _e("lore", "absent", keys=["blade"], owners=["characters:winifred"]),
        _e("lore", "owned", keys=["blade"], owners=["characters:mara"]),
        _e("lore", "cooling", keys=["storm"], cooldown=3),
        _e("lore", "pulled", keys=["anchor"]),
        _e("lore", "puller", keys=["tide"], body="anchor"),
        _e("lore", "gm", keys=["blade"], secrecy="gm-only"),
        _e("lore", "out", keys=["blade"]),
    ]
    got = _run(entries, ["storm", "tide"], present={"characters:mara": {"type": "cast"}},
               depth=1, rec=1, excluded_refs=frozenset({"lore:out"}), recall=spy,
               recall_text="the window")
    assert seen == {"refs": ["lore:miss", "lore:owned"], "text": "the window"}
    # In the order recall returned them, the gate's owner carried through.
    assert _refs(got.recalled) == ["lore:owned", "lore:miss"]
    assert got.recalled[0].reason == {"type": "recall", "score": 0.5,
                                      "owner": "characters:mara",
                                      "owner_presence": {"type": "cast"}}


def test_recall_is_not_called_without_candidates():
    def boom(candidates, text):  # pragma: no cover - a call here is the failure
        raise AssertionError("no candidates")

    got = _run([_e("lore", "hit", keys=["tide"])], ["tide"], recall=boom)
    assert _refs(got.keyword) == ["lore:hit"]


def test_recall_hits_add_no_presence():
    entries = [
        _e("groups", "guild", keys=["guild"], body="the anchor"),
        _e("lore", "vault", owners=["groups:guild"]),
        _e("lore", "anchor", keys=["anchor"]),
    ]
    got = _run(entries, ["calm"], rec=2,
               recall=lambda c, t: [(e, 0.7) for e in c if e["id"] == "guild"],
               recall_text="calm")
    assert _refs(got.recalled) == ["groups:guild"]
    assert got.keyword == []           # no owned lore unlocked, nothing pulled
    assert "groups:guild" not in got.present


def test_garbage_controls_behave_as_absent():
    garbage = lore_fields.parse({
        "priority": "high", "sticky": "-3", "cooldown": "soon", "key_logic": "maybe",
        "recursion": "sometimes", "scan_depth": "deep", "secondary_keys": 5,
        "keep": "perhaps", "known_by": "everyone"})
    assert garbage == Controls()

    def world(controls):
        out = [_e("lore", "a", keys=["alpha"], body="beta"),
               _e("lore", "b", keys=["beta"]),
               _e("lore", "c", keys=["gamma"])]
        for e in out:
            if controls is None:
                del e["controls"]
            else:
                e["controls"] = controls
        return out

    def summary(result):
        return [(h.ref, h.level, h.reason, h.direct, h.age) for h in result.keyword]

    texts = ["alpha", "alpha", "gamma long ago", "calm", "alpha"]
    plain = summary(_run(world(None), texts, rec=1, depth=2))
    assert plain == summary(_run(world(garbage), texts, rec=1, depth=2))
    assert plain == summary(_run(world({"sticky": 3, "recursion": "none"}), texts,
                                 rec=1, depth=2))
    assert [r for r, *_ in plain] == ["lore:a", "lore:b"]


def test_age_values():
    hits = _by_ref(_run([
        _e("lore", "post", keys=["Mara"]),
        _e("lore", "seed", keys=["Winifred"]),
        _e("lore", "both", keys=["Seraphine"]),
        _e("lore", "sticky", keys=["lantern"], sticky=5),
        _e("lore", "keyless"),
        _e("lore", "pinned", keys=["nowhere"]),
        _e("lore", "pulled", keys=["anchor"]),
        _e("groups", "guild", keys=["guild"], body="anchor"),
        _e("lore", "unlocked", owners=["groups:guild"]),
    ], ["the lantern", "calm", "Seraphine and Mara", "the guild"],
        seed="and Winifred, Seraphine?", start=40, rec=1, depth=2,
        pinned_refs=frozenset({"lore:pinned"})).keyword)
    # A transcript hit: the newest matching post's transcript index.
    assert hits["lore:post"].age == 42
    assert hits["lore:post"].reason == {"type": "key", "key": "Mara", "secondary": None,
                                        "post": 42, "seed": False}
    # A seed match: one past the last post, newest of all.
    assert hits["lore:seed"].age == 44
    assert hits["lore:seed"].reason == {"type": "key", "key": "Winifred",
                                        "secondary": None, "post": None, "seed": True}
    assert hits["lore:both"].age == 44 and hits["lore:both"].reason["seed"] is True
    # Sticky carry: the post that started it.
    assert hits["lore:sticky"].age == 40
    assert hits["lore:sticky"].reason == {"type": "sticky", "from_post": 40, "remaining": 3}
    for ref in ("lore:keyless", "lore:pinned", "lore:pulled", "lore:unlocked"):
        assert hits[ref].age == -1, ref
    assert [hits[r].direct for r in ("lore:post", "lore:seed", "lore:sticky",
                                     "lore:keyless", "lore:pinned")] == [True] * 5
    assert hits["lore:pulled"].direct is False and hits["lore:unlocked"].direct is False


def test_age_with_no_posts_and_a_seed():
    hit = _run([_e("lore", "x", keys=["Mara"])], [], seed="ask Mara").keyword[0]
    assert hit.age == 0
    assert hit.reason == {"type": "key", "key": "Mara", "secondary": None,
                          "post": None, "seed": True}


def test_empty_turn_activates_only_standing_entries():
    got = _run([_e("lore", "x", keys=["Mara"]), _e("lore", "always")], [], depth=0)
    assert _refs(got.keyword) == ["lore:always"]
    assert got.present == {}


def test_one_search_per_key_per_post(monkeypatch):
    log = []
    real = activation._compile

    class Counting:
        def __init__(self, pattern):
            self._pattern = pattern

        def search(self, text):
            log.append(text)
            return self._pattern.search(text)

    monkeypatch.setattr(activation, "_compile", lambda key: Counting(real(key)))
    n_entries, n_posts = 200, 300
    entries = []
    for i in range(n_entries):
        timed = {"sticky": 2, "cooldown": 3} if i % 2 == 0 else {}
        entries.append(_e("lore", f"e{i}", keys=[f"k{i}a", f"k{i}b", f"k{i}c"],
                          body=f"body {i} names k{(i + 1) % n_entries}b", **timed))
    texts = [f"post {j} k{(j * 7) % n_entries}a" if j % 3 else f"post {j} calm"
             for j in range(n_posts)]
    rec = 2
    got = _run(entries, texts, seed="seed text", rec=rec, depth=4)

    bodies = {e["body"] for e in entries}
    window = [t for t in log if t not in bodies]
    body = [t for t in log if t in bodies]
    distinct_keys = len({k for e in entries for k in e["keys"]})
    slots = n_posts + 1  # posts and the seed
    assert len(window) <= distinct_keys * slots
    activated = len(got.keyword)
    candidate_keys = sum(len(e["keys"]) for e in entries)
    assert any(h.reason["type"] == "recursion" for h in got.keyword)
    assert 0 < len(body) <= rec * activated * candidate_keys
