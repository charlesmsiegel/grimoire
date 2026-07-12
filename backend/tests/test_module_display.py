import json

from grimoire.store import module_display

# Two groups + two character types so per-type granularity is testable.
SHEETS = {
    "groups": {
        "attributes": {
            "label": "Attributes",
            "fields": [
                {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1},
                {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1},
            ],
            "derived": {"reflex": "min(vigor, wits)"},
        },
        "abilities": {
            "label": "Abilities",
            "fields": [{"key": "brawl", "label": "Brawl", "type": "dots", "max": 5}],
        },
    },
    "sheet_types": {
        "warden": {
            "label": "Warden", "kind": "characters",
            "groups": ["attributes", "abilities"],
            "fields": [{"key": "essence", "label": "Essence", "type": "resource", "max": 10}],
            "derived": {"surge": "reflex + essence_max - essence"},
        },
        "adept": {
            "label": "Adept", "kind": "characters",
            "groups": ["attributes"],
            "fields": [{"key": "focus", "label": "Focus", "type": "number", "default": 0}],
        },
    },
}

GOOD_LAYOUT = {
    "fragments": {
        "attr-block": {"group": "attributes", "grid": True, "title": "Attributes"},
    },
    "sheet_types": {
        "warden": {"column": [
            {"use": "attr-block"},
            {"row": [
                {"group": "abilities", "title": "Abilities"},
                {"column": [{"fields": ["essence"]}, {"derived": ["reflex", "surge"]}], "title": "Power"},
            ]},
        ]},
        "adept": {"column": [{"use": "attr-block"}, {"fields": ["focus"]}]},
    },
}


def write_layout(tmp_path, layout):
    (tmp_path / "layout.json").write_text(json.dumps(layout), encoding="utf-8")
    return tmp_path


def load(tmp_path):
    return module_display.load_display(tmp_path, SHEETS)


def layout_errors(tmp_path, layout):
    layout_out, _theme, errors = load(write_layout(tmp_path, layout))
    return layout_out, [e for e in errors if e["source"] == "layout"]


def test_no_display_files(tmp_path):
    layout, theme, errors = load(tmp_path)
    assert layout == {"sheet_types": {}}
    assert theme == {}
    assert errors == []


def test_good_layout_splices_fragments(tmp_path):
    layout, errors = layout_errors(tmp_path, GOOD_LAYOUT)
    assert errors == []
    assert set(layout["sheet_types"]) == {"warden", "adept"}
    warden = layout["sheet_types"]["warden"]
    spliced = warden["column"][0]
    assert spliced == {"group": "attributes", "grid": True, "title": "Attributes"}
    assert "use" not in json.dumps(layout)


def test_use_title_overrides_fragment_title(tmp_path):
    lay = {"fragments": {"f": {"group": "attributes", "title": "Original"}},
           "sheet_types": {"adept": {"column": [{"use": "f", "title": "Override"}]}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert errors == []
    assert layout["sheet_types"]["adept"]["column"][0]["title"] == "Override"


def test_unparseable_layout_is_file_level_error(tmp_path):
    (tmp_path / "layout.json").write_text("{nope", encoding="utf-8")
    layout, _theme, errors = load(tmp_path)
    assert layout == {"sheet_types": {}}
    assert errors and errors[0]["source"] == "layout" and errors[0]["sheet_type"] is None


def test_non_object_root(tmp_path):
    layout, errors = layout_errors(tmp_path, ["not", "an", "object"])
    assert layout["sheet_types"] == {} and errors[0]["sheet_type"] is None


def test_unknown_root_key(tmp_path):
    lay = dict(GOOD_LAYOUT, extra=1)
    layout, errors = layout_errors(tmp_path, lay)
    assert set(layout["sheet_types"]) == {"warden", "adept"}  # trees still load
    assert any("extra" in e["message"] and e["sheet_type"] is None for e in errors)


def test_unknown_sheet_type_key(tmp_path):
    lay = {"sheet_types": {"ghost": {"fields": []}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert layout["sheet_types"] == {}
    assert any("ghost" in e["message"] and e["sheet_type"] is None for e in errors)


def bad_tree_case(tmp_path, tree, needle):
    """One bad warden tree: warden dropped with a warden-tagged error; adept survives."""
    lay = {"sheet_types": {"warden": tree,
                           "adept": {"fields": ["focus"]}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "warden" not in layout["sheet_types"]
    assert "adept" in layout["sheet_types"]
    assert any(needle in e["message"] and e["sheet_type"] == "warden" for e in errors)


def test_node_not_object(tmp_path):
    bad_tree_case(tmp_path, {"column": ["x"]}, "object")


def test_node_needs_exactly_one_form(tmp_path):
    bad_tree_case(tmp_path, {"row": [], "column": []}, "exactly one")
    bad_tree_case(tmp_path, {"title": "no form"}, "exactly one")


def test_unknown_node_key(tmp_path):
    bad_tree_case(tmp_path, {"group": "attributes", "colour": "red"}, "colour")


def test_wrong_value_types(tmp_path):
    bad_tree_case(tmp_path, {"row": "x"}, "array")
    bad_tree_case(tmp_path, {"fields": [1]}, "fields")
    bad_tree_case(tmp_path, {"group": 7}, "group")
    bad_tree_case(tmp_path, {"group": "attributes", "grid": "yes"}, "boolean")
    bad_tree_case(tmp_path, {"group": "attributes", "title": {}}, "title")


def test_grid_only_on_group_or_fields(tmp_path):
    bad_tree_case(tmp_path, {"row": [], "grid": True}, "grid")


def test_unknown_refs(tmp_path):
    bad_tree_case(tmp_path, {"group": "ghost"}, "ghost")
    bad_tree_case(tmp_path, {"fields": ["ghost"]}, "ghost")
    bad_tree_case(tmp_path, {"derived": ["ghost"]}, "ghost")
    bad_tree_case(tmp_path, {"use": "ghost"}, "ghost")


def test_group_ref_must_be_in_sheet_types_groups(tmp_path):
    # abilities exists in sheets.json but adept does not include it
    lay = {"sheet_types": {"adept": {"group": "abilities"}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any(e["sheet_type"] == "adept" for e in errors)


def test_duplicate_placement(tmp_path):
    bad_tree_case(tmp_path, {"column": [{"group": "attributes"}, {"fields": ["vigor"]}]},
                  "once")
    bad_tree_case(tmp_path, {"column": [{"derived": ["reflex"]}, {"derived": ["reflex"]}]},
                  "once")


def test_fragment_cycle(tmp_path):
    lay = {"fragments": {"a": {"column": [{"use": "b"}]}, "b": {"column": [{"use": "a"}]}},
           "sheet_types": {"adept": {"use": "a"}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any("cycle" in e["message"] for e in errors)


def test_unused_broken_fragment_reported_but_drops_nothing(tmp_path):
    lay = dict(GOOD_LAYOUT, fragments=dict(GOOD_LAYOUT["fragments"], broken={"row": "x"}))
    layout, errors = layout_errors(tmp_path, lay)
    assert set(layout["sheet_types"]) == {"warden", "adept"}
    assert any("broken" in e["message"] and e["sheet_type"] is None for e in errors)


def test_used_broken_fragment_drops_user(tmp_path):
    lay = {"fragments": {"bad": {"row": "x"}},
           "sheet_types": {"adept": {"use": "bad"}, "warden": GOOD_LAYOUT["sheet_types"]["warden"]}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any(e["sheet_type"] == "adept" and "invalid" in e["message"] for e in errors)


def test_depth_cap(tmp_path):
    tree = {"fields": ["focus"]}
    for _ in range(40):
        tree = {"column": [tree]}
    lay = {"sheet_types": {"adept": tree}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any("depth" in e["message"] for e in errors)


def test_node_cap(tmp_path):
    lay = {"sheet_types": {"adept": {"row": [{"derived": []} for _ in range(1100)]}}}
    layout, errors = layout_errors(tmp_path, lay)
    assert "adept" not in layout["sheet_types"]
    assert any("node cap" in e["message"] for e in errors)


def test_pathologically_deep_json_never_raises(tmp_path):
    # deep enough to blow the JSON parser's recursion limit before our caps
    (tmp_path / "layout.json").write_text("[" * 100000 + "]" * 100000,
                                          encoding="utf-8")
    layout, _theme, errors = load(tmp_path)  # must not raise
    assert layout == {"sheet_types": {}}
    assert errors and errors[0]["source"] == "layout"
