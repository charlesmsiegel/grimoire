"""Package-level guarantees: public API and determinism (purity: test_purity.py)."""

import grimoire.intent as intent

PUBLIC = {
    "Utterance", "RoutingContext", "Intent", "Command", "CommandSpec", "PendingConfirmation",
    "ConfirmationResult", "route", "request_confirmation", "resolve_confirmation", "COMMANDS",
    "SAFE_STOP_COMMANDS", "REASONS", "InputMode", "Origin", "IntentKind", "Gate",
}


def test_public_api():
    assert PUBLIC <= set(intent.__all__)
    for name in intent.__all__:
        assert hasattr(intent, name), name


def test_routing_is_deterministic():
    context = intent.RoutingContext("pc-1", True)
    inputs = [
        intent.Utterance("Open the inventory"), intent.Utterance("I open the chest"),
        intent.Utterance("Director, make this tense"), intent.Utterance("Delete the scene"),
        intent.Utterance("director make this tense"), intent.Utterance("Could you undo that?"),
        intent.Utterance("Hello there. Delete the scene."), intent.Utterance("hi", mode="speech"),
        intent.Utterance("stop", origin="voice", confidence=0.3, alternatives=("shop",)),
        intent.Utterance("I kill the king", origin="voice", confidence=0.9,
                         alternatives=("I kiss the king",)),
    ]
    assert [intent.route(u, context) for u in inputs] == [intent.route(u, context) for u in inputs]
