"""Destructive commands run only after an explicit, expiring, target- and revision-bound yes."""

import pytest

from grimoire.intent.commands import Command
from grimoire.intent.confirm import (
    ConfirmationResult, PendingConfirmation, request_confirmation, resolve_confirmation,
)
from grimoire.intent.model import RoutingContext, Utterance
from grimoire.intent.router import route

CTX = RoutingContext("pc-1", True)
TARGET, REVISION = "scene:s1", "r1"


def pending(now=100.0):
    return request_confirmation(route(Utterance("Delete the scene"), CTX), target=TARGET,
                                revision=REVISION, now=now)


def answer(text, now=110.0, current_target=TARGET, current_revision=REVISION, **utterance):
    return resolve_confirmation(pending(), Utterance(text, **utterance), now=now,
                                current_target=current_target, current_revision=current_revision,
                                context=CTX)


def test_request_binds_command_target_revision_and_expiry():
    request = pending()
    assert (request.command, request.target, request.revision) == (Command("delete_scene"), TARGET, REVISION)
    assert (request.issued_at, request.expires_at) == (100.0, 130.0)
    assert "yes" in request.prompt and "Delete the scene" in request.prompt


@pytest.mark.parametrize("text", ["yes", "Yes!", "YES.", "confirm", "yes, do it", "Yes, delete it"])
def test_confirmed(text):
    result = answer(text)
    assert (result.status, result.command, result.target, result.revision) == (
        "confirmed", Command("delete_scene"), TARGET, REVISION)


@pytest.mark.parametrize("text", ["no", "No!", "cancel", "never mind", "stop", "no thanks"])
def test_declined(text):
    assert answer(text) == ConfirmationResult("declined")


@pytest.mark.parametrize("text", ["maybe", "yes, delete everything", "open the map", "", "yes no"])
def test_unclear(text):
    assert answer(text) == ConfirmationResult("unclear")


def test_expired_and_stale():
    assert answer("yes", now=130.0).status == "expired"
    assert answer("yes", now=99.0).status == "expired"
    assert answer("yes", current_target="scene:s2").status == "stale"
    assert answer("yes", current_revision="r2").status == "stale"
    assert answer("no", now=500.0).status == "expired"


def test_confirmation_lanes():
    assert answer("yes", mode="speech").status == "unclear"
    assert answer("yes", mode="director").status == "unclear"
    assert answer("yes", mode="command").status == "confirmed"


def test_voice_confirmation_needs_confidence_and_agreement():
    def spoken(confidence, *alternatives):
        return answer("yes", origin="voice", confidence=confidence, alternatives=alternatives).status

    assert spoken(0.5) == "unclear"
    assert spoken(None) == "unclear"
    assert spoken(0.9, "yesterday") == "unclear"
    assert spoken(0.9, "yes") == "confirmed"
    assert spoken(0.9) == "confirmed"
    assert answer("no", origin="voice", confidence=0.2).status == "declined"


@pytest.mark.parametrize("build", [
    lambda: request_confirmation(route(Utterance("pause"), CTX), target=TARGET, revision=REVISION, now=0.0),
    lambda: request_confirmation(route(Utterance("hello"), CTX), target=TARGET, revision=REVISION, now=0.0),
    lambda: request_confirmation(route(Utterance("Delete the scene"), CTX), target="", revision=REVISION,
                                 now=0.0),
    lambda: request_confirmation(route(Utterance("Delete the scene"), CTX), target=TARGET, revision=" ",
                                 now=0.0),
    lambda: request_confirmation(route(Utterance("Delete the scene"), CTX), target=TARGET, revision=REVISION,
                                 now=0.0, ttl=0),
    lambda: request_confirmation(route(Utterance("Delete the scene"), CTX), target=TARGET, revision=REVISION,
                                 now=0.0, ttl=float("nan")),
    lambda: request_confirmation(route(Utterance("Delete the scene"), CTX), target=TARGET, revision=REVISION,
                                 now=float("inf")),
    lambda: resolve_confirmation(pending(), Utterance("yes"), now=float("nan"),
                                 current_target=TARGET, current_revision=REVISION, context=CTX),
    lambda: PendingConfirmation(Command("pause"), "t", "r", 0.0, 1.0, "p"),
    lambda: PendingConfirmation(Command("delete_scene"), "t", "r", 1.0, 1.0, "p"),
    lambda: PendingConfirmation(Command("delete_scene"), "t", "r", float("nan"), 1.0, "p"),
    lambda: PendingConfirmation(Command("delete_scene"), "t", "", 0.0, 1.0, "p"),
    lambda: ConfirmationResult("declined", Command("delete_scene"), "t", "r"),
    lambda: ConfirmationResult("confirmed", Command("delete_scene"), "t"),
    lambda: ConfirmationResult("confirmed"),
    lambda: ConfirmationResult("maybe"),
])
def test_validation(build):
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize("text", ["Yes?", "yes ?", "Confirm?", "do it?"])
def test_questioning_yes_is_not_consent(text):
    assert answer(text).status == "unclear"


def test_questioning_alternative_is_not_consent():
    result = answer("yes", origin="voice", confidence=0.95, alternatives=("Yes?",))
    assert result.status == "unclear"


def test_a_replayed_yes_after_the_command_ran_is_stale():
    # The check consumes nothing; single use comes from the executor advancing the
    # revision when it acts, so the same pending value and "yes" cannot run twice.
    request = pending()
    first = resolve_confirmation(request, Utterance("yes"), now=110.0, current_target=TARGET,
                                 current_revision=REVISION, context=CTX)
    assert first.status == "confirmed"
    after_delete = "r2"  # the executor acted on (TARGET, r1) and the scene moved on
    replay = resolve_confirmation(request, Utterance("yes"), now=111.0, current_target=TARGET,
                                  current_revision=after_delete, context=CTX)
    assert replay == ConfirmationResult("stale")

