from grimoire.hud.widgets import core_widget_by_id, core_widget_ids


def test_inventory_widget_registered():
    assert "core.inventory" in core_widget_ids()
    w = core_widget_by_id("core.inventory")
    assert w is not None
    assert "inventory_changed" in w.refresh_on
    assert w.owner_module == "inventory"
