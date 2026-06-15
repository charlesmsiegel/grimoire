"""Unit tests for the shared ``fact_subject_kwargs`` helper (#523)."""

from __future__ import annotations

import pytest

from grimoire.transient_state.service import fact_subject_kwargs
from grimoire.types.transient import EntityKind


@pytest.mark.parametrize(
    ("kind", "expected_field"),
    [
        (EntityKind.CHARACTER, "character_ids"),
        (EntityKind.LOCATION, "location_ids"),
        (EntityKind.FACTION, "faction_ids"),
    ],
)
def test_fact_subject_kwargs_maps_kind_to_id_list(kind: EntityKind, expected_field: str) -> None:
    assert fact_subject_kwargs(kind, "ent-1") == {expected_field: ["ent-1"]}


def test_fact_subject_kwargs_unmapped_kind_is_empty() -> None:
    # SCENE has no FactSubject id-list field, so it yields no kwargs.
    assert fact_subject_kwargs(EntityKind.SCENE, "scene-1") == {}
