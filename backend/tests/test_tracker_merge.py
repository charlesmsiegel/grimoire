"""Merging the tracker's reply into a snapshot, and filtering one for a viewer.

Both modules are pure: they take dicts and return dicts, so nothing here
touches a store."""

import pytest

from grimoire.store import scenes
from grimoire.store.tracker import fields, merge, view

FIELDS = list(fields.DEFAULT_FIELDS)
ROSTER = {"characters:mara": "Mara", "characters:winifred": "Winifred"}
PRESENT = {"characters:mara", "characters:winifred"}


def _reply(prev, reply, present=PRESENT, roster=ROSTER, flds=FIELDS):
    return merge.apply_reply(prev, reply, flds, roster, set(present), scenes.match_name)


def test_parse_reply_tolerates_prose_and_fences():
    assert merge.parse_reply('Sure:\n```json\n{"changes": {}}\n```')["changes"] == {}
    with pytest.raises(merge.TrackerReplyError):
        merge.parse_reply("no json here")


def test_parse_reply_fills_missing_keys_and_rejects_non_objects():
    assert merge.parse_reply("{}") == {"changes": {}, "awareness": {}}
    assert merge.parse_reply('{"changes": []}')["changes"] == {}
    with pytest.raises(merge.TrackerReplyError):
        merge.parse_reply('[{"changes": {}}]')


def test_parse_reply_balances_braces_inside_strings():
    got = merge.parse_reply('Here: {"changes": {"Mara": {"pose": "a } b"}}} trailing {')
    assert got["changes"] == {"Mara": {"pose": "a } b"}}


def test_parse_reply_rejects_a_truncated_reply():
    # Cut off by the token limit: the inner object is balanced but is not a reply.
    with pytest.raises(merge.TrackerReplyError):
        merge.parse_reply('{"changes": {"Mara": {"pose": "x"}}')
    with pytest.raises(merge.TrackerReplyError):
        merge.parse_reply('Sure: {"changes": {"Mara": {"pose": "x"}}, "awareness": {"Mara.concea')
    with pytest.raises(merge.TrackerReplyError):
        merge.parse_reply('```json\n{"changes": {"Mara": {"pose": "x"}}\n')


def test_changes_apply_with_default_awareness():
    snap, changed = _reply({}, {"changes": {"Mara": {"visible_mood": "fear", "concealed": "a letter"}}})
    m = snap["characters:mara"]["fields"]
    assert m["visible_mood"] == {"value": "fear", "aware": "present"} and m["concealed"]["aware"] == []
    assert ["characters:mara", "visible_mood", "fear"] in changed


def test_invalid_entries_are_dropped():
    reply = {"changes": {"Nobody": {"pose": "x"},
                         "Mara": {"visible_mood": "furious", "bogus": "y", "condition": "wet"}}}
    snap, changed = _reply({}, reply)
    assert changed == [] and snap["characters:mara"]["fields"] == {}


def test_switched_off_field_is_not_writable():
    flds = [dict(f, off=True) if f["key"] == "pose" else f for f in FIELDS]
    snap, changed = _reply({}, {"changes": {"Mara": {"pose": "kneeling"}}}, flds=flds)
    assert changed == [] and snap["characters:mara"]["fields"] == {}


def test_non_present_character_is_not_writable():
    snap, changed = _reply({}, {"changes": {"Winifred": {"pose": "x"}}}, present={"characters:mara"})
    assert changed == [] and "characters:winifred" not in snap


def test_names_resolve_by_prefix_through_match_name():
    roster = {"characters:winifred": "Winifred Vance"}
    snap, _ = _reply({}, {"changes": {"winifred": {"pose": "seated"}}},
                     present={"characters:winifred"}, roster=roster)
    assert snap["characters:winifred"]["fields"]["pose"]["value"] == "seated"


def test_changed_value_clears_user_marker_and_resets_awareness():
    prev = {"characters:mara": {"present": True, "fields": {
        "true_mood": {"value": "calm", "aware": ["characters:winifred"], "set_by": "user"}}}}
    snap, _ = _reply(prev, {"changes": {"Mara": {"true_mood": "afraid"}}})
    assert snap["characters:mara"]["fields"]["true_mood"] == {"value": "afraid", "aware": []}


def test_unchanged_value_is_a_no_op_and_keeps_awareness():
    prev = {"characters:mara": {"present": True, "fields": {
        "true_mood": {"value": "calm", "aware": ["characters:winifred"], "set_by": "user"}}}}
    snap, changed = _reply(prev, {"changes": {"Mara": {"true_mood": "calm"}}})
    assert changed == [] and snap["characters:mara"]["fields"]["true_mood"] == \
        prev["characters:mara"]["fields"]["true_mood"]


def test_unmentioned_user_value_survives():
    prev = {"characters:mara": {"present": True, "fields": {
        "clothing": {"value": "grey cloak", "aware": "present", "set_by": "user"}}}}
    snap, changed = _reply(prev, {"changes": {}})
    assert snap["characters:mara"]["fields"]["clothing"]["set_by"] == "user" and changed == []


def test_prev_is_not_mutated():
    prev = {"characters:mara": {"present": True, "fields": {
        "pose": {"value": "standing", "aware": "present"}}}}
    _reply(prev, {"changes": {"Mara": {"pose": "kneeling"}}})
    assert prev["characters:mara"]["fields"]["pose"]["value"] == "standing"


def test_awareness_addition():
    prev = {"characters:mara": {"present": True, "fields": {"concealed": {"value": "a letter", "aware": []}}}}
    snap, _ = _reply(prev, {"awareness": {"Mara.concealed": ["Winifred"]}})
    assert snap["characters:mara"]["fields"]["concealed"]["aware"] == ["characters:winifred"]


def test_awareness_ignores_owner_absent_and_present_aware_values():
    prev = {"characters:mara": {"present": True, "fields": {
        "concealed": {"value": "a letter", "aware": []},
        "pose": {"value": "standing", "aware": "present"}}}}
    snap, _ = _reply(prev, {"awareness": {"Mara.concealed": ["Mara", "Nobody"],
                                          "Mara.pose": ["Winifred"]}})
    f = snap["characters:mara"]["fields"]
    assert f["concealed"]["aware"] == [] and f["pose"]["aware"] == "present"
    snap, _ = _reply(prev, {"awareness": {"Mara.concealed": ["Winifred"]}}, present={"characters:mara"})
    assert snap["characters:mara"]["fields"]["concealed"]["aware"] == []


def test_awareness_applies_to_a_value_set_in_the_same_reply():
    snap, _ = _reply({}, {"changes": {"Mara": {"concealed": "a letter"}},
                          "awareness": {"Mara.concealed": ["Winifred"]}})
    assert snap["characters:mara"]["fields"]["concealed"]["aware"] == ["characters:winifred"]


def test_departed_character_kept_not_present():
    prev = {"characters:mara": {"present": True, "fields": {"pose": {"value": "x", "aware": "present"}}},
            "characters:winifred": {"present": True, "fields": {"pose": {"value": "y", "aware": "present"}}}}
    snap, _ = _reply(prev, {"changes": {}}, present={"characters:mara"})
    assert snap["characters:winifred"]["present"] is False
    assert snap["characters:winifred"]["fields"]["pose"]["value"] == "y"
    assert snap["characters:mara"]["present"] is True


def test_text_truncated_at_200():
    long = "word " * 100
    snap, _ = _reply({}, {"changes": {"Mara": {"pose": "a\n b\t c", "holding": long,
                                               "condition": ["x" * 300, "", "  soaked \n"]}}})
    f = snap["characters:mara"]["fields"]
    assert f["pose"]["value"] == "a b c"
    assert len(f["holding"]["value"]) <= merge.MAX_TEXT == 200
    assert f["condition"]["value"] == ["x" * 200, "soaked"]


def test_non_list_for_list_field_dropped():
    _, changed = _reply({}, {"changes": {"Mara": {"condition": "wet"}}})
    assert changed == []


@pytest.mark.parametrize("items", [[1], ["x", 5], [{"state": "wet"}]])
def test_non_string_list_items_drop_the_value_and_keep_the_stored_list(items):
    prev = {"characters:mara": {"present": True, "fields": {
        "condition": {"value": ["soaked"], "aware": "present"}}}}
    snap, changed = _reply(prev, {"changes": {"Mara": {"condition": items}}})
    assert changed == [] and snap["characters:mara"]["fields"]["condition"]["value"] == ["soaked"]


@pytest.mark.parametrize("items", [[1], ["x", 5], [{"state": "wet"}]])
def test_apply_edit_rejects_non_string_list_items(items):
    prev = {"characters:mara": {"present": True, "fields": {}}}
    with pytest.raises(ValueError):
        merge.apply_edit(prev, {"characters:mara": {"condition": {"value": items}}}, FIELDS)


def test_apply_edit_marks_user_and_rejects_bad_enum():
    prev = {"characters:mara": {"present": True, "fields": {
        "pose": {"value": "standing", "aware": "present"}}},
        "characters:winifred": {"present": True, "fields": {}}}
    snap, changed = merge.apply_edit(prev, {"characters:mara": {
        "pose": {"value": "kneeling"},
        "concealed": {"value": "a key", "aware": ["characters:winifred"]}}}, FIELDS)
    f = snap["characters:mara"]["fields"]
    assert f["pose"] == {"value": "kneeling", "aware": "present", "set_by": "user"}
    assert f["concealed"] == {"value": "a key", "aware": ["characters:winifred"], "set_by": "user"}
    assert ["characters:mara", "pose", "kneeling"] in changed
    assert prev["characters:mara"]["fields"]["pose"]["value"] == "standing"
    with pytest.raises(ValueError):
        merge.apply_edit(prev, {"characters:mara": {"visible_mood": {"value": "furious"}}}, FIELDS)


def test_apply_edit_aware_only_change_sets_user_and_is_not_a_value_change():
    prev = {"characters:mara": {"present": True, "fields": {
        "concealed": {"value": "a key", "aware": []}}},
        "characters:winifred": {"present": True, "fields": {}}}
    snap, changed = merge.apply_edit(
        prev, {"characters:mara": {"concealed": {"aware": ["characters:winifred"]}}}, FIELDS)
    assert snap["characters:mara"]["fields"]["concealed"] == {
        "value": "a key", "aware": ["characters:winifred"], "set_by": "user"}
    assert changed == []


def test_apply_edit_unchanged_is_a_no_op():
    prev = {"characters:mara": {"present": True, "fields": {
        "pose": {"value": "standing", "aware": "present"}}}}
    snap, changed = merge.apply_edit(prev, {"characters:mara": {"pose": {"value": "standing"}}}, FIELDS)
    assert snap == prev and changed == []


@pytest.mark.parametrize("edits", [
    {"characters:nobody": {"pose": {"value": "x"}}},
    {"characters:mara": {"bogus": {"value": "x"}}},
    {"characters:mara": {"condition": {"value": "wet"}}},
    {"characters:mara": {"pose": {"value": ["x"]}}},
    {"characters:mara": {"pose": {}}},
    {"characters:mara": {"pose": {"value": "x", "aware": "everyone"}}},
    {"characters:mara": {"concealed": {"value": "x", "aware": ["characters:nobody"]}}},
])
def test_apply_edit_raises_on_invalid(edits):
    prev = {"characters:mara": {"present": True, "fields": {}}}
    with pytest.raises(ValueError):
        merge.apply_edit(prev, edits, FIELDS)


def test_apply_edit_rejects_switched_off_field():
    flds = [dict(f, off=True) if f["key"] == "pose" else f for f in FIELDS]
    prev = {"characters:mara": {"present": True, "fields": {}}}
    with pytest.raises(ValueError):
        merge.apply_edit(prev, {"characters:mara": {"pose": {"value": "x"}}}, flds)


SNAP = {
    "characters:mara": {"present": True, "fields": {
        "concealed": {"value": "a letter", "aware": []},
        "visible_mood": {"value": "fear", "aware": "present"},
        "condition": {"value": ["soaked", "shivering"], "aware": "present"},
        "pose": {"value": "", "aware": "present"}}},
    "characters:winifred": {"present": True, "fields": {
        "true_mood": {"value": "wary", "aware": ["characters:mara"]}}},
    "characters:gone": {"present": True, "fields": {"pose": {"value": "x", "aware": "present"}}},
}


def test_view_filters_by_viewer():
    mara = view.lines_for(SNAP, FIELDS, "characters:mara", ROSTER)
    assert mara[0]["name"] == "Mara" and mara[0]["own"]
    win = next(l for l in mara if l["name"] == "Winifred")
    assert any(v["private"] for v in win["values"])        # known to Mara
    wview = view.lines_for(SNAP, FIELDS, "characters:winifred", ROSTER)
    mara_seen = next(l for l in wview if l["name"] == "Mara")
    assert all(v["label"] != "Concealed" for v in mara_seen["values"])
    narr = view.lines_for(SNAP, FIELDS, "grimoire", ROSTER)
    assert any(v["label"] == "Concealed" and v["private"] for l in narr for v in l["values"])


def test_view_joins_lists_skips_empty_and_unknown_refs():
    narr = view.lines_for(SNAP, FIELDS, None, ROSTER)
    assert [l["name"] for l in narr] == ["Mara", "Winifred"]
    assert not any(l["own"] for l in narr)
    mara = narr[0]["values"]
    assert {"label": "Condition", "text": "soaked, shivering", "private": False} in mara
    assert all(v["label"] != "Pose" for v in mara)


def test_view_puts_viewer_first_and_skips_absent_and_off_fields():
    snap = {**SNAP, "characters:mara": {**SNAP["characters:mara"], "present": False}}
    lines = view.lines_for(snap, FIELDS, "characters:winifred", ROSTER)
    assert [l["name"] for l in lines] == ["Winifred"]
    flds = [dict(f, off=True) if f["key"] == "visible_mood" else f for f in FIELDS]
    narr = view.lines_for(SNAP, flds, None, ROSTER)
    assert all(v["label"] != "Visible mood" for l in narr for v in l["values"])
