"""Invented recognition events, NOT recordings or accent-quality measurements."""
from grimoire.intent import Command, RoutingContext, Utterance
from grimoire.speech import Recognition
from grimoire.speech.evaluation import Case

CTX = RoutingContext("pc-1", True)


def sample(id, text, category, expected_kind, command=None, confidence=.9, alternatives=(), **metadata):
    e = Recognition(Utterance(text, origin="voice", confidence=confidence, alternatives=alternatives),
                    **(dict(attribution="foreground", engaged=True, final=True) | metadata))
    return Case(id, e, CTX, expected_kind, command,
                "pc-1" if expected_kind in {"pc_speech", "pc_action"} else None, category)


CORPUS = (
    sample("clear-open", "open inventory", "clean", "ui_command", Command("navigate", "inventory")),
    sample("clear-pause", "pause", "clean", "ui_command", Command("pause")),
    sample("clear-speech", '"hello"', "clean", "pc_speech"),
    sample("quiet-open", "open inventory", "quiet", "uncertain", confidence=.2),
    sample("quiet-stop", "stop", "quiet", "ui_command", Command("stop"), confidence=.1),
    sample("disfluent", "um open uh inventory", "disfluency", "pc_speech"),
    sample("variant", "open inventry", "speech_variant", "uncertain", alternatives=("open inventory",)),
    sample("recognizer-error", "open inventory", "recognizer_error", "uncertain", alternatives=("open map",)),
    sample("noise-delete", "delete scene", "noise", "uncertain", confidence=.1),
    sample("background-delete", "Grimoire, delete scene", "background", "blocked", attribution="background"),
    sample("background-yes", "yes", "background", "blocked", attribution="background"),
    sample("unattributed", "open map", "background", "blocked", attribution="unknown"),
    sample("overlap-delete", "delete scene", "simultaneous", "blocked", overlap=True),
    sample("overlap-stop", "stop", "simultaneous", "blocked", overlap=True),
    sample("interrupted", "open inventory", "interruption", "blocked", final=False),
    sample("not-engaged", "pause", "interruption", "blocked", engaged=False),
    sample("quoted-delete", '"delete scene"', "ambiguous", "pc_speech"),
    sample("polite-delete", "could you delete scene?", "ambiguous", "uncertain"),
    sample("intentional-delete", "delete scene", "clean", "ui_command", Command("delete_scene")),
)
