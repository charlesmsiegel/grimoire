"""The one-call prompt builders that need no store: imports, taglines,
scenarios, dossiers, voice anchors, rolling summaries and the scene break."""

from __future__ import annotations

from grimoire.store import (
    _plaintext,
    dossiers,
    rolling_summary,
    scenario,
    scene_break,
    taglines,
    voice_anchors,
)

from .harness import REPORT, check, check_messages, render, require_carried

CARD = {"name": "Seraphine Vale", "description": "Tall, sharp-eyed smuggler.",
        "personality": "Wry and wary.", "scenario": "Runs the night dock."}
SPARSE = {"name": "Bob", "description": "", "scenario": "A bar."}  # missing keys too
TRANSCRIPT = "**You:** Where is it?\n\n**Seraphine Vale:** Gone."

# Scenario-card extraction (#217). Both cards on purpose: the full one renders
# every heading, and the bare one proves an absent field contributes none —
# `user.j2` branches per field, and a comparison that only ever took the
# populated branch would not see the other one move.
SCENARIO_CARD = {"data": {
    "name": "Saltmarch",
    "description": "A drowned town where Mara keeps the tide-gate.",
    "personality": "Wary, tidal.",
    "scenario": "The gate has not opened in nine days.",
    "creator_notes": "Play it slow.",
    "mes_example": "<START>\n**Mara:** The gate stays shut.",
    "first_mes": "Mara is waiting at the tide-gate.",
    "alternate_greetings": [
        "The square is empty.",
        "![](data:image/png;base64,AAAA)\n\nWinifred counts the stalls.",
        # Past GREETING_PROMPT_CHARS, so the clip is exercised rather than
        # asserted about: every body in this fixture used to be short enough
        # that `_clip` was the identity, and a harness that only ever takes the
        # no-op branch proves nothing about the branch that moves.
        "Winifred opens the ledger. " + "The tide came in. " * 120,
    ],
    "character_book": {"entries": [
        {"keys": ["gate"], "name": "The Tide-Gate", "content": "Iron and barnacle.",
         "enabled": True},
        {"keys": ["ledger"], "name": "The Ledger",   # ...and past ENTRY_PROMPT_CHARS
         "content": "Six stalls and a scale. " + "It lists every crossing. " * 60,
         "enabled": True}]},
}}

# Both folds, because the user template branches on `prior` and the two branches
# label the transcript differently -- a from-scratch fold that said "posts since
# that summary" would be asking the model to fold onto a summary it never got.
# ...and both fact heads, since a scene with no location, date or cast yet must
# render no head at all rather than an empty one.
ROLLING_FACTS = {"location": "Night Dock", "date": "2026-07-05",
                 "cast": ["characters/seraphine", "pcs/hero"]}

BREAK_SIGNALS = [{"kind": "length", "weight": 2, "detail": "44 posts since this was last considered"},
                 {"kind": "time", "weight": 2, "detail": "the clock advanced 15 hours — a long skip"}]

#: Scene-break's criteria, carried out of the legacy one-call prompt (the
#: `scene_break/system.j2` slice F deleted) into the decide-era templates word
#: for word (I8): (fragment, the template it went to). Invent no criterion, and
#: drop none. While the legacy prompt existed, a coverage check held that every
#: sentence of it was inside these fragments or its reply format, which
#: `decide/system.j2` owns now.
SCENE_BREAK_CARRIED = (
    (("You are watching a role-play scene that is still being played, and answering one "
      "question about it: has the scene reached a natural place to stop?"),
     "scene_break/question.j2"),
    (("A scene ends when the beat it was about has resolved — an argument has said what "
      "it had to say, a journey has arrived, a decision has been taken, a confrontation "
      "has broken off. A scene has NOT ended merely because the characters walked into "
      "another room, because the clock moved, or because a lot of posts have gone by. "
      "Movement in the middle of an unresolved beat is pacing, not a boundary."),
     "scene_break/question.j2"),
    (("You will be told which mechanical signals prompted the question. Treat them as the "
      "reason you are being asked, never as evidence for a yes: they are counts, and they "
      "cannot see whether anything was settled."),
     "scene_break/question.j2"),
    ("one sentence saying what resolved", "scene_break/explain.j2"),
    ("when the scene is still mid-beat", "scene_break/explain.j2"),
    ("one sentence saying what is still unresolved", "scene_break/explain.j2"),
    ("a title for the scene that would start next", "scene_break_title/system.j2"),
    (("a short phrase — no more than about six words, no quotation marks, no trailing "
      "punctuation"), "scene_break_title/system.j2"),
    (("Do not invent events the transcript does not show, and do not describe what "
      "happens next beyond naming the scene it would be."), "scene_break_title/system.j2"),
)


def check_plaintext_and_taglines() -> None:
    text_card = _plaintext.parse("Name: Seraphine\nPersonality: Patient.")
    exp = _plaintext.build_prompt("Name: Seraphine\nPersonality: Patient.", text_card)
    check("plaintext system", exp[0]["content"],
          render("plaintext_import/system.j2", schema=_plaintext.SCHEMA))
    check("plaintext user", exp[1]["content"], render("plaintext_import/user.j2",
          text="Name: Seraphine\nPersonality: Patient.", draft=_plaintext.draft_data(text_card)))
    exp = taglines.build_prompt(CARD)
    check("tagline system", exp[0]["content"], render("tagline/system.j2"))
    check("tagline user", exp[1]["content"], render("tagline/user.j2", card=CARD))
    exp = taglines.build_prompt(SPARSE)
    check("tagline user (sparse)", exp[1]["content"], render("tagline/user.j2", card=SPARSE))


def check_scenario() -> None:
    for label, scard in (("full", SCENARIO_CARD), ("bare", {"data": {"name": "Saltmarch"}})):
        exp = scenario.build_prompt(scard)
        check(f"scenario system ({label})", exp[0]["content"], render("scenario/system.j2"))
        check(f"scenario user ({label})", exp[1]["content"],
              render("scenario/user.j2", card=scard["data"], fields=scenario.PROMPT_FIELDS,
                     entries=scenario.prompt_entries(scard),
                     greetings=scenario.prompt_greetings(scard)))
    scenario_user = scenario.build_prompt(SCENARIO_CARD)[1]["content"]
    assert "Existing entries:" in scenario_user, \
        "scenario user no longer lists the card's own world-info -- the model cannot re-file it"
    assert "base64" not in scenario_user, \
        "scenario user is carrying an opener's embedded image into the prompt"
    # The two clipped bodies really were clipped, so the comparison above covered
    # the clipping helper rather than a fixture that happened to fit.
    assert scenario_user.count(" …") == 2, \
        f"the scenario fixture no longer exercises _clip (found {scenario_user.count(' …')} clips)"
    assert len(scenario_user) < 4000, \
        "scenario user is unbounded again -- a big card would blow the context window"


def check_dossiers() -> None:
    for prior in ("", "She ran the dock and owed the Guild."):
        exp = dossiers.build_prompt("Seraphine Vale", prior, TRANSCRIPT)
        check(f"dossier system (prior={bool(prior)})", exp[0]["content"],
              render("dossier/system.j2"))
        check(f"dossier user (prior={bool(prior)})", exp[1]["content"],
              render("dossier/user.j2", name="Seraphine Vale", prior=prior,
                     transcript=TRANSCRIPT))


# `build_prompt` clips the description before rendering, so the comparison has to
# mirror that transformation. Rendering the RAW card here would compare the
# builder's clipped output against an unclipped render — and for a fixture short
# enough that the clip does nothing, would pass either way.
def _voice_user(c):
    return render("voice_anchor/user.j2",
                  card={**c, "description": voice_anchors.truncate(
                      str(c.get("description") or ""), voice_anchors.VOICE_SOURCE_CAP)})


def check_voice_anchors() -> None:
    voice_card = {**CARD, "mes_example": "<START>\n**Seraphine Vale:** Try me.",
                  "system_prompt": "Voice Seraphine with dry wit."}
    exp = voice_anchors.build_prompt(voice_card)
    check("voice anchor system", exp[0]["content"], render("voice_anchor/system.j2"))
    check("voice anchor user", exp[1]["content"], _voice_user(voice_card))
    # The sparse card exercises the "(none)" fallbacks — a card with no example
    # dialogue is the common case for a character that has never been played.
    exp = voice_anchors.build_prompt(SPARSE)
    check("voice anchor user (sparse)", exp[1]["content"], _voice_user(SPARSE))
    # An over-cap description proves the clip is real, for the same reason the
    # scenario fixture asserts its own two clips: a comparison whose fixture always
    # fits cannot tell a working clip from a deleted one.
    long_card = {**voice_card,
                 "description": "She never finishes a sentence. " * 400 + "TAIL-SENTINEL"}
    long_user = voice_anchors.build_prompt(long_card)[1]["content"]
    check("voice anchor user (clipped)", long_user, _voice_user(long_card))
    msg1 = "the voice-anchor fixture no longer exercises VOICE_SOURCE_CAP"
    assert "TAIL-SENTINEL" not in long_user, msg1
    msg2 = "voice anchor user is unbounded again -- a long description dominates the call"
    assert len(long_user) < voice_anchors.VOICE_SOURCE_CAP + 1000, msg2


def check_rolling_summary() -> None:
    for prior in ("", "Seraphine held the dock; the ledger was still missing."):
        for facts in (None, {"location": "", "date": "", "cast": []}, ROLLING_FACTS):
            exp = rolling_summary.build_prompt(prior, TRANSCRIPT, facts)
            check(f"rolling summary system (prior={bool(prior)})", exp[0]["content"],
                  render("rolling_summary/system.j2"))
            check(f"rolling summary user (prior={bool(prior)}, facts={bool(facts)})",
                  exp[1]["content"],
                  render("rolling_summary/user.j2", prior=prior, transcript=TRANSCRIPT,
                         facts=facts))


def _scene_break_case(signals, title, facts) -> None:
    """One head combination: the item, its one question, and the title call
    with and without the verdict's reason."""
    label = f"signals={bool(signals)}, title={bool(title)}, facts={bool(facts)}"
    item = scene_break.build_item(TRANSCRIPT, signals, facts, title)
    check(f"scene break item context ({label})", item.context,
          render("scene_break/user.j2", transcript=TRANSCRIPT, signals=signals,
                 facts=facts, title=title))
    REPORT.require(f"scene break item questions ({label})",
                   [q.id for q in item.questions] == [scene_break.QUESTION_ID],
                   f"asks {[q.id for q in item.questions]}")
    check(f"scene break item question ({label})", item.questions[0].instructions,
          render("scene_break/question.j2"))
    for reason in ("", "The ledger changed hands."):
        check_messages(f"scene break title ({label}, reason={bool(reason)})",
                       [{"role": "system",
                         "content": render("scene_break_title/system.j2")},
                        {"role": "user",
                         "content": render("scene_break_title/user.j2",
                                           transcript=TRANSCRIPT, facts=facts,
                                           title=title, reason=reason)}],
                       scene_break.build_title_prompt(TRANSCRIPT, facts, title, reason))


def check_scene_break() -> None:
    """The scene-break question as a decision item (slice F, spec 7.4). Every head
    combination, because the context template builds its head out of four
    independently-optional parts and a scene with none of them must render no
    head at all rather than an empty one -- and both signal states, since a
    forced question can reach the model having crossed no threshold, which is
    the one case that renders the reason list empty. Its one predicate's
    instructions are `question.j2`. The title is a second call's -- with and
    without the verdict's reason, over the same heads."""
    for signals in ([], BREAK_SIGNALS):
        for title in ("", "The Long Walk Back"):
            for facts in (None, {"location": "", "date": "", "cast": []}, ROLLING_FACTS):
                _scene_break_case(signals, title, facts)
    check("scene break explain", scene_break.explain(), render("scene_break/explain.j2"))
    require_carried("scene break", SCENE_BREAK_CARRIED)


CHECKS = (check_plaintext_and_taglines, check_scenario, check_dossiers, check_voice_anchors,
          check_rolling_summary, check_scene_break)
