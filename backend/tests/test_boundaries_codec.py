"""035 milestone 2: the codec for 016's session zero (``grimoire.session-zero/1``), stored in a chat's play scope.

Strict and canonical: every key required, unknown keys refused at every level, 016's own checks applied, and a
payload that is not exactly the form this version writes refused as ``tampered``. Synthetic ids only.
"""

from __future__ import annotations

import copy

import pytest

from grimoire.core.boundaries import Boundary, SessionZero
from grimoire.core.boundaries_codec import (
    MAX_BOUNDARIES, MAX_NOTE, SESSION_ZERO_SCHEMA, SessionZeroCodecError, session_zero_from_payload,
    session_zero_to_payload,
)
from grimoire.core.json_value import freeze_object

P1, P2 = "human:p1", "human:p2"
CONFIG = SessionZero((
    Boundary("b-campaign", "campaign", "c1", "graphic_violence", "fade_to_black", P1, agreed_by=frozenset({P2})),
    Boundary("b-player", "player", P2, "spiders", "avoid", P2, private_note="a private reason"),
    Boundary("b-world", "world", "w1", "romance", "allow", P1, attributed=True),
))


def payload(config: SessionZero = CONFIG, revision: int = 3) -> dict:
    return session_zero_to_payload(config, revision)


def refused(reason: str, value) -> SessionZeroCodecError:
    with pytest.raises(SessionZeroCodecError) as error:
        session_zero_from_payload(value)
    assert error.value.reason == reason, error.value
    return error.value


def test_a_configuration_round_trips_with_its_revision():
    stored = session_zero_from_payload(payload())
    assert stored.config.boundaries == CONFIG.boundaries and stored.revision == 3
    assert payload()["schema"] == SESSION_ZERO_SCHEMA
    assert payload()["boundaries"][0]["agreed_by"] == sorted({P1, P2})  # the setter's own agreement is written
    assert session_zero_from_payload(freeze_object(payload())).config == stored.config  # as the scope keeps it
    assert session_zero_from_payload(payload(SessionZero(), 1)).config == SessionZero()  # all withdrawn: empty


def test_an_unknown_or_missing_schema_is_never_read_as_this_one():
    for schema in ("grimoire.session-zero/2", None, 1):
        data = payload()
        data["schema"] = schema
        refused("unsupported_schema", data)
    data = payload()
    del data["schema"]
    refused("unsupported_schema", data)
    refused("invalid_field", ["not", "an", "object"])


@pytest.mark.parametrize("level", ["top", "boundary"])
def test_every_key_is_required_and_no_unknown_key_is_kept(level):
    data = payload()
    target = data if level == "top" else data["boundaries"][1]
    extra = copy.deepcopy(data)
    (extra if level == "top" else extra["boundaries"][1])["mood"] = "calm"
    refused("invalid_field", extra)
    for key in [key for key in target if key != "schema"]:
        missing = copy.deepcopy(data)
        del (missing if level == "top" else missing["boundaries"][1])[key]
        refused("invalid_field", missing)


@pytest.mark.parametrize("field, value", [
    ("revision", 0), ("revision", True), ("revision", 2.0), ("revision", "3"), ("boundaries", {}),
    ("boundaries", "b-player"),
])
def test_malformed_top_level_values_are_refused(field, value):
    data = payload()
    data[field] = value
    refused("invalid_field", data)


@pytest.mark.parametrize("field, value", [
    ("treatment", "ban"), ("scope", "galaxy"), ("theme", "Graphic Violence"), ("attributed", 1),
    ("private_note", None), ("agreed_by", P1), ("set_by", "director"), ("boundary", ""), ("scope_id", 7),
    ("private_note", "x" * (MAX_NOTE + 1)), ("theme", "a" * 65),
])
def test_values_016_refuses_are_refused(field, value):
    data = payload()
    data["boundaries"][0][field] = value
    refused("invalid_field", data)


def test_016s_ownership_shapes_cannot_be_stored_by_hand():
    data = payload()
    data["boundaries"][1]["set_by"] = P1  # P1 planting a player boundary for P2
    refused("invalid_field", data)
    data = payload()
    data["boundaries"][1]["agreed_by"] = [P1]  # an agreement on a player boundary
    refused("invalid_field", data)
    data = payload()
    data["boundaries"][2]["boundary"] = "b-campaign"  # two boundaries under one id
    refused("invalid_field", data)


def test_what_016_would_silently_normalise_is_refused_as_tampered():
    for agreed in ([P2], [P2, P1], [P1, P1, P2]):  # the setter left out, unsorted, repeated
        data = payload()
        data["boundaries"][0]["agreed_by"] = agreed
        refused("tampered", data)


def test_a_setting_boundary_is_neither_written_nor_read_until_settings_exist():
    setting = SessionZero((Boundary("b-setting", "setting", "s1", "storms", "allow", P1),))
    with pytest.raises(SessionZeroCodecError) as error:
        session_zero_to_payload(setting, 1)
    assert error.value.reason == "invalid_field"
    data = payload()
    data["boundaries"][2].update(scope="setting", scope_id="s1")
    refused("invalid_field", data)


def test_bounds_hold_both_ways():
    many = SessionZero(tuple(Boundary(f"b{n}", "player", P1, "spiders", "avoid", P1) for n in range(MAX_BOUNDARIES)))
    assert len(session_zero_from_payload(payload(many)).config.boundaries) == MAX_BOUNDARIES
    data = payload(many)
    data["boundaries"].append({**data["boundaries"][0], "boundary": "one-more"})
    refused("too_many_boundaries", data)
    more = SessionZero((*many.boundaries, Boundary("one-more", "player", P1, "spiders", "avoid", P1)))
    with pytest.raises(SessionZeroCodecError) as error:
        session_zero_to_payload(more, 1)
    assert error.value.reason == "too_many_boundaries"
    with pytest.raises(SessionZeroCodecError) as error:  # nothing is written that no reader accepts
        session_zero_to_payload(SessionZero((Boundary("b", "player", P1, "spiders", "avoid", P1,
                                                      private_note="x" * (MAX_NOTE + 1)),)), 1)
    assert error.value.reason == "invalid_field"
    for revision in (0, True):
        with pytest.raises(SessionZeroCodecError):
            session_zero_to_payload(CONFIG, revision)


def test_no_refusal_or_repr_names_an_owner_an_id_or_a_note():
    cases = []
    for field, value in (("treatment", "ban"), ("set_by", "director"), ("agreed_by", [P2])):
        data = payload()
        data["boundaries"][0][field] = value
        cases.append(data)
    data = payload()
    data["boundaries"][2]["boundary"] = "b-campaign"
    cases.append(data)
    for data in cases:
        with pytest.raises(SessionZeroCodecError) as error:
            session_zero_from_payload(data)
        assert not any(secret in str(error.value) for secret in (P1, P2, "b-campaign", "a private reason"))
    assert "a private reason" not in repr(session_zero_from_payload(payload()))
