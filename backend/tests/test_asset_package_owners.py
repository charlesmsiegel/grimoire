"""Typed image inventory remains the same facade and shared implementation."""

from typing import get_overloads

from grimoire.store import assets


def test_names_inventory_retains_both_typed_overloads():
    assert len(get_overloads(assets.names_in)) == 2
    assert assets.names_in is assets._lookup.names_in
