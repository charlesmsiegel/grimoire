"""Prove the prompt builders and templates/ agree byte-for-byte.

The store modules render prompts from templates/ — this harness verifies the
WIRING (each builder passes the documented variables to the right template)
and the DATA CONTRACT (the gather() mirror below assembles the same data from
public store reads that context._assemble gathers; templates/README.md
documents that contract). It builds a throwaway store (GRIMOIRE_HOME -> temp
dir) exercising every context section, then compares direct template renders
against the live builders. It never pins template text to literals, so
editing a prompt in templates/ cannot fail it. Run after touching either
side:

    backend/.venv/Scripts/python.exe scripts/verify_templates.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend" / "src"))
os.environ["GRIMOIRE_HOME"] = tempfile.mkdtemp(prefix="grimoire-verify-")

from jinja2 import Environment, FileSystemLoader, StrictUndefined  # noqa: E402

env = Environment(loader=FileSystemLoader(str(REPO / "templates")),
                  undefined=StrictUndefined)

class Report:
    """The running tally: how many comparisons were made, and which failed.

    A class rather than two module-level names mutated through `global`, which
    is what this was. The counter and the failure list only ever move together
    -- every comparison bumps one and may append to the other -- so a reader
    asking "what does a failed check do to the tally" had to find two `global`
    statements in two functions to answer it. Owning both here also means the
    epilogue reports out of one object rather than reaching for two module
    variables it hopes nothing else wrote.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def compare(self, label: str, expected: str, actual: str) -> None:
        """One byte-for-byte comparison, counted whether or not it passes."""
        self.checks += 1
        if expected == actual:
            return
        i = next((n for n, (a, b) in enumerate(zip(expected, actual)) if a != b),
                 min(len(expected), len(actual)))
        self.failures.append(
            f"{label}: mismatch at char {i}\n"
            f"  expected …{expected[max(0, i - 60):i + 60]!r}…\n"
            f"  actual   …{actual[max(0, i - 60):i + 60]!r}…")

    def require(self, label: str, ok: bool, message: str) -> None:
        """A comparison that is not byte-for-byte -- a fragment that must be
        present -- counted whether or not it passes."""
        self.checks += 1
        if not ok:
            self.failures.append(f"{label}: {message}")

    def note(self, label: str, message: str) -> None:
        """A failure that is not a text mismatch -- a role shape that differs,
        where comparing the contents would be comparing different things."""
        self.checks += 1
        self.failures.append(f"{label}: {message}")

    def verdict(self) -> str:
        return (f"all {self.checks} checks passed — builders and templates/ agree byte-for-byte"
                if not self.failures else
                f"{len(self.failures)}/{self.checks} checks FAILED\n\n"
                + "\n\n".join(self.failures))


REPORT = Report()


def render(_template: str, **vars) -> str:
    return env.get_template(_template).render(**vars)


def check(label: str, expected: str, actual: str) -> None:
    REPORT.compare(label, expected, actual)


def check_messages(label: str, expected: list[dict], actual: list[dict]) -> None:
    if [m["role"] for m in expected] != [m["role"] for m in actual]:
        REPORT.note(label, f"role shape {[m['role'] for m in expected]} != "
                           f"{[m['role'] for m in actual]}")
        return
    for n, (e, a) in enumerate(zip(expected, actual)):
        check(f"{label}[{n}] ({e['role']})", e["content"], a["content"])


# ---------------------------------------------------------------- pure checks

from grimoire.store import (  # noqa: E402
    absorb,
    chronicle,
    context,
    dossiers,
    overlay,
    relationships,
    response_protocol,
    rolling_summary,
    scenario,
    scene_break,
    suggest,
    taglines,
    voice_anchors,
    voice_drift,
)

card = {"name": "Seraphine Vale", "description": "Tall, sharp-eyed smuggler.",
        "personality": "Wry and wary.", "scenario": "Runs the night dock."}
exp = taglines.build_prompt(card)
check("tagline system", exp[0]["content"], render("tagline/system.j2"))
check("tagline user", exp[1]["content"], render("tagline/user.j2", card=card))
sparse = {"name": "Bob", "description": "", "scenario": "A bar."}  # missing keys too
exp = taglines.build_prompt(sparse)
check("tagline user (sparse)", exp[1]["content"], render("tagline/user.j2", card=sparse))

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

transcript = "**You:** Where is it?\n\n**Seraphine Vale:** Gone."
for prior in ("", "She ran the dock and owed the Guild."):
    exp = dossiers.build_prompt("Seraphine Vale", prior, transcript)
    check(f"dossier system (prior={bool(prior)})", exp[0]["content"], render("dossier/system.j2"))
    check(f"dossier user (prior={bool(prior)})", exp[1]["content"],
          render("dossier/user.j2", name="Seraphine Vale", prior=prior, transcript=transcript))

# `build_prompt` clips the description before rendering, so the comparison has to
# mirror that transformation. Rendering the RAW card here would compare the
# builder's clipped output against an unclipped render — and for a fixture short
# enough that the clip does nothing, would pass either way.
def _voice_user(c):
    return render("voice_anchor/user.j2",
                  card={**c, "description": voice_anchors.truncate(
                      str(c.get("description") or ""), voice_anchors.VOICE_SOURCE_CAP)})


voice_card = {**card, "mes_example": "<START>\n**Seraphine Vale:** Try me.",
              "system_prompt": "Voice Seraphine with dry wit."}
exp = voice_anchors.build_prompt(voice_card)
check("voice anchor system", exp[0]["content"], render("voice_anchor/system.j2"))
check("voice anchor user", exp[1]["content"], _voice_user(voice_card))
# The sparse card exercises the "(none)" fallbacks — a card with no example
# dialogue is the common case for a character that has never been played.
exp = voice_anchors.build_prompt(sparse)
check("voice anchor user (sparse)", exp[1]["content"], _voice_user(sparse))
# An over-cap description proves the clip is real, for the same reason the
# scenario fixture asserts its own two clips: a comparison whose fixture always
# fits cannot tell a working clip from a deleted one.
long_card = {**voice_card,
             "description": "She never finishes a sentence. " * 400 + "TAIL-SENTINEL"}
long_user = voice_anchors.build_prompt(long_card)[1]["content"]
check("voice anchor user (clipped)", long_user, _voice_user(long_card))
MSG1 = "the voice-anchor fixture no longer exercises VOICE_SOURCE_CAP"
assert "TAIL-SENTINEL" not in long_user, MSG1
MSG2 = "voice anchor user is unbounded again -- a long description dominates the call"
assert len(long_user) < voice_anchors.VOICE_SOURCE_CAP + 1000, MSG2

anchor = "Clipped. Never uses contractions.\nAnswers questions with questions."
# The voice-drift judge is a decision item (slice F), checked with the other
# decide-era templates below. BOTH branches of its optional correction, because
# they are different prompts and only one of them is the ordinary case. The
# scene prompt tells the writer a correction outranks the anchor, so a judge
# that could not see it would flag the model for obeying its instructions --
# the block exists for that, and a verifier that only rendered the empty branch
# would not notice it disappearing.
correction = "Use contractions; the last scene was too stiff."

# Both folds, because the user template branches on `prior` and the two branches
# label the transcript differently -- a from-scratch fold that said "posts since
# that summary" would be asking the model to fold onto a summary it never got.
# ...and both fact heads, since a scene with no location, date or cast yet must
# render no head at all rather than an empty one.
ROLLING_FACTS = {"location": "Night Dock", "date": "2026-07-05",
                 "cast": ["characters/seraphine", "pcs/hero"]}
for prior in ("", "Seraphine held the dock; the ledger was still missing."):
    for facts in (None, {"location": "", "date": "", "cast": []}, ROLLING_FACTS):
        exp = rolling_summary.build_prompt(prior, transcript, facts)
        check(f"rolling summary system (prior={bool(prior)})", exp[0]["content"],
              render("rolling_summary/system.j2"))
        check(f"rolling summary user (prior={bool(prior)}, facts={bool(facts)})",
              exp[1]["content"],
              render("rolling_summary/user.j2", prior=prior, transcript=transcript,
                     facts=facts))

# The scene-break question as a decision item (slice F, spec 7.4). Every head
# combination, because the context template builds its head out of four
# independently-optional parts and a scene with none of them must render no
# head at all rather than an empty one -- and both signal states, since a
# forced question can reach the model having crossed no threshold, which is
# the one case that renders the reason list empty. Its one predicate's
# instructions are `question.j2`. The title is a second call's -- with and
# without the verdict's reason, over the same heads.
BREAK_SIGNALS = [{"kind": "length", "weight": 2, "detail": "44 posts since this was last considered"},
                 {"kind": "time", "weight": 2, "detail": "the clock advanced 15 hours — a long skip"}]
for signals in ([], BREAK_SIGNALS):
    for title in ("", "The Long Walk Back"):
        for facts in (None, {"location": "", "date": "", "cast": []}, ROLLING_FACTS):
            label = f"signals={bool(signals)}, title={bool(title)}, facts={bool(facts)}"
            item = scene_break.build_item(transcript, signals, facts, title)
            check(f"scene break item context ({label})", item.context,
                  render("scene_break/user.j2", transcript=transcript, signals=signals,
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
                                                   transcript=transcript, facts=facts,
                                                   title=title, reason=reason)}],
                               scene_break.build_title_prompt(transcript, facts, title, reason))
check("scene break explain", scene_break.explain(), render("scene_break/explain.j2"))


def _source(name: str) -> str:
    """A template's source with its `{# #}` comments removed and every
    whitespace run collapsed to one space: what a fragment is looked for in. A
    comment is documentation, so a fragment quoted there carries nothing."""
    text = (REPO / "templates" / name).read_text(encoding="utf-8")
    return " ".join(re.sub(r"\{#.*?#\}", " ", text, flags=re.DOTALL).split())


# Coverage of a legacy one-call prompt while it exists (slice F's, shared since
# slice G): every sentence of it is either inside one fragment, or is fragments
# joined by glue -- delete each fragment it contains, then a JSON key
# (`"verdict":`), and nothing but punctuation and a connective may be left.
# Containing ONE fragment is not enough: a format fragment can sit beside a
# criterion in one sentence, so a criterion missing from a table would hide
# behind its neighbour. With this rule, a carried fragment deleted from the
# table leaves its words behind and fails. A multi-sentence fragment covers
# sentence by sentence.
_GLUE_WORDS = frozenset({"and"})
_SENTENCE = re.compile(r"(?<=[.?!])\s+")


def _sentences(text: str) -> list[str]:
    return [sentence for paragraph in re.split(r"\n\s*\n", text.strip())
            for sentence in _SENTENCE.split(" ".join(paragraph.split()))]


def _leftover(source: str, fragments: list[str]) -> list[tuple[str, list[str]]]:
    """Each sentence of `source`, with the words it says beyond `fragments`
    and glue (none when it is covered)."""
    out = []
    by_length = sorted(fragments, key=len, reverse=True)
    for sentence in _sentences(source):
        if any(sentence in f for f in fragments):
            out.append((sentence, []))
            continue
        rest = sentence
        for f in by_length:
            rest = rest.replace(f, " ")
        rest = re.sub(r'"\w+"\s*:', " ", rest)
        out.append((sentence, [w for w in re.findall(r"[A-Za-z]+", rest)
                               if w.lower() not in _GLUE_WORDS]))
    return out


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
for fragment, target in SCENE_BREAK_CARRIED:
    REPORT.require(f"scene break carried ({target}: {fragment[:40]}…)",
                   fragment in _source(target), f"not in {target}")

# The voice-drift judge as a decision item (slice F, spec 7.4), as the absorb
# phase sends it. Both correction branches (`correction`, above): the item's
# context is `user.j2`, the legacy user message unchanged. Its one choice is
# `question.j2`'s, and each of its three options is described by `option.j2`
# for that verdict.
for corr in ("", correction):
    label = f"correction={bool(corr)}"
    item = voice_drift.build_item("Seraphine Vale", anchor, transcript, correction=corr)
    check(f"voice drift item context ({label})", item.context,
          render("voice_drift/user.j2", name="Seraphine Vale", anchor=anchor,
                 transcript=transcript, correction=corr))
    REPORT.require(f"voice drift item questions ({label})",
                   [q.id for q in item.questions] == [voice_drift.QUESTION_ID],
                   f"asks {[q.id for q in item.questions]}")
    (choice,) = item.questions
    check(f"voice drift item question ({label})", choice.instructions,
          render("voice_drift/question.j2"))
    REPORT.require(f"voice drift item options ({label})",
                   [o.id for o in choice.options]
                   == [voice_drift.DRIFT, voice_drift.IN_VOICE, voice_drift.NOT_ENOUGH]
                   and not choice.allow_none,
                   f"offers {[o.id for o in choice.options]} (allow_none={choice.allow_none})")
    for opt in choice.options:
        check(f"voice drift option {opt.id} ({label})", opt.description,
              render("voice_drift/option.j2", verdict=opt.id))
        REPORT.require(f"voice drift option {opt.id} described ({label})",
                       bool(opt.description.strip()), "option.j2 rendered nothing")
check("voice drift explain", voice_drift.explain(), render("voice_drift/explain.j2"))

#: The voice-drift judge's standard and criteria, carried out of its legacy
#: one-call prompt (the `voice_drift/system.j2` slice F deleted) into the
#: decide-era templates word for word (I8): (fragment, the template it went
#: to). Invent no criterion, and drop none. While the legacy prompt existed, a
#: coverage check held that every sentence of it was inside these fragments,
#: the verdict bullets (`VOICE_DRIFT_OPTIONS`, under their labels) or its
#: reply format, which `decide/system.j2` owns now.
VOICE_DRIFT_CARRIED = (
    (("You are checking one character's dialogue in a played scene against their voice "
      "standard: their voice anchor, as modified by any outstanding correction shown with "
      "it."), "voice_drift/question.j2"),
    (("Judge ONLY how the character sounds. A character may do anything, feel anything, or "
      "change their mind; that is the story, not drift. Drift is unsupported register, "
      "diction, or rhythm — flattening into generic narrator prose, acquiring vocabulary or "
      "formality the standard excludes, violating an explicit enduring speech constraint, or "
      "persistently sounding interchangeable with the rest of the cast. Judge in context: "
      "topic, familiarity, urgency, or emotion can change delivery. Omitting a habitual "
      "catchphrase, using a plain shared answer, or speaking sincerely instead of joking is "
      "not sufficient evidence of drift. Do not turn tendencies or example lines into "
      "compulsory routines."), "voice_drift/question.j2"),
    (("Where a correction is shown, it SUPERSEDES the anchor on expression wherever the two "
      "conflict. Lines obeying it are not drift even where the anchor alone would rule them "
      "out. Neither a correction nor an anchor can require an earlier decision, demand, or "
      "event to recur, or override current facts, player control, or knowledge limits. Do "
      "not issue a corrective that would undo the story to restore a verbal habit."),
     "voice_drift/question.j2"),
    (("Be conservative. If the lines are consistent with that standard, report `in_voice`. "
      "A false alarm costs the next scene a correction it did not need."),
     "voice_drift/question.j2"),
    (("the corrective the model will be given on its next turn: one or two sentences, "
      "addressed to the writer, naming the specific way the voice slipped and what to do "
      "instead."), "voice_drift/explain.j2"),
    ("Quote a short offending line if it helps.", "voice_drift/explain.j2"),
    ("empty unless the verdict is `drift`.", "voice_drift/explain.j2"),
)
#: Each verdict's description, carried out of its legacy bullet word for word
#: after the label, keyed by the verdict it describes: `option.j2` must render
#: exactly this text for exactly this verdict. A description that moved to the
#: wrong verdict inverts the judge, and the gate (parser-only) cannot see it.
VOICE_DRIFT_OPTIONS = {
    voice_drift.DRIFT: "they spoke, and they sounded wrong against that standard.",
    voice_drift.IN_VOICE: ("they spoke enough to judge, and they sounded right against "
                           "that standard."),
    voice_drift.NOT_ENOUGH: (
        "they were silent, or said too little to tell. This is a real answer, not a "
        "fallback: use it whenever you cannot actually hear the voice in this scene. Do "
        "NOT report `in_voice` for a character who barely spoke — saying nothing is not "
        "evidence of sounding right, and a standing correction stays in force until a "
        "scene shows otherwise."),
}
for fragment, target in VOICE_DRIFT_CARRIED:
    REPORT.require(f"voice drift carried ({target}: {fragment[:40]}…)",
                   fragment in _source(target), f"not in {target}")
for verdict, text in VOICE_DRIFT_OPTIONS.items():
    check(f"voice drift option carried ({verdict})",
          text, render("voice_drift/option.j2", verdict=verdict))
REPORT.require("voice drift options are the item's",
               list(VOICE_DRIFT_OPTIONS)
               == [o.id for o in voice_drift.build_item(
                   "Seraphine Vale", anchor, transcript).questions[0].options],
               f"carried descriptions for {list(VOICE_DRIFT_OPTIONS)}")

# The speaker pick as a decision item (slice F, spec 7.4), which `_select`
# sends through `decide()`. Both branches of the round's user direction,
# since the context renders its line only when there is one. Its one choice
# is `response_selector_question.j2`'s, over the round's eligible refs (each
# described by its name) and `grimoire` (described by
# `response_selector_grimoire.j2`), with null allowed: null is the hand-back.
SELECTOR_ROSTER = [{"ref": "characters:mara", "name": "Mara"},
                   {"ref": "characters:winifred", "name": "Winifred"}]
SELECTOR_CONVERSATION = [{"speaker": "You", "content": "Winifred, where were you?"},
                         {"speaker": "Mara", "content": "Tell her <nothing> & go."}]
for note in ("", "Let Mara answer first."):
    label = f"note={bool(note)}"
    item = response_protocol.selector_item(SELECTOR_ROSTER, SELECTOR_CONVERSATION, note)
    check(f"selector item context ({label})", item.context,
          render("scene/response_selector_context.j2", conversation=SELECTOR_CONVERSATION,
                 note=note))
    REPORT.require(f"selector item user direction ({label})",
                   ("User direction:" in item.context) == bool(note),
                   "the user direction line does not follow the round's note")
    REPORT.require(f"selector item questions ({label})",
                   [q.id for q in item.questions] == [response_protocol.SELECTOR_QUESTION],
                   f"asks {[q.id for q in item.questions]}")
    (choice,) = item.questions
    check(f"selector item question ({label})", choice.instructions,
          render("scene/response_selector_question.j2"))
    REPORT.require(f"selector item options ({label})",
                   [(o.id, o.description) for o in choice.options[:-1]]
                   == [(e["ref"], e["name"]) for e in SELECTOR_ROSTER]
                   and choice.options[-1].id == response_protocol.GRIMOIRE_REF
                   and choice.allow_none,
                   f"offers {[o.id for o in choice.options]} (allow_none={choice.allow_none})")
    check(f"selector option grimoire ({label})", choice.options[-1].description,
          render("scene/response_selector_grimoire.j2"))

#: The selector's criteria, carried out of its retired one-call prompt into
#: the decide-era templates word for word (I8): (fragment, the template it
#: went to). Invent no criterion, and drop none. Two were deliberately
#: reworded, not dropped (controller ruling, Task 9 fix round 1), because the
#: decide prompt lays the same material out differently: `decide/user.j2`
#: puts the item's context ABOVE its questions, so the transcript is no
#: longer "below", and the roster is the choice's options rather than an
#: "Available NPCs:" listing, so a character is missing "from the options".
SELECTOR_CARRIED = (
    ("Choose at most one initial speaker for the observable conversation above.",
     "scene/response_selector_question.j2"),
    ("Choose a listed NPC reference", "scene/response_selector_question.j2"),
    ("or null to return control to the player.", "scene/response_selector_question.j2"),
    (("An established NPC's actions or physical reactions belong to that NPC, even without "
      "speech."), "scene/response_selector_question.j2"),
    ("Choose their reference for those contributions.",
     "scene/response_selector_question.j2"),
    ("Grimoire does not continue their actions or provide a closing recap.",
     "scene/response_selector_question.j2"),
    (("A character already established in the cast or transcript is not new just because "
      "they are missing from the options."), "scene/response_selector_question.j2"),
    (("Choose null when there is no distinct contribution or the player's decision is "
      "needed."), "scene/response_selector_question.j2"),
    ("Never invent private knowledge or script reactions.",
     "scene/response_selector_question.j2"),
    ("Observable transcript:", "scene/response_selector_context.j2"),
    ("User direction:", "scene/response_selector_context.j2"),
)
#: What the retired prompt said `grimoire` is chosen for, keyed by the option
#: it describes: `response_selector_grimoire.j2` must render exactly this text,
#: and it must be the `grimoire` option's. The listed NPCs' options are the
#: roster's own, each described by its name.
SELECTOR_OPTIONS = {
    response_protocol.GRIMOIRE_REF: ("general scene information, an independent scene event "
                                     "or a genuinely new character's entrance"),
}
for fragment, target in SELECTOR_CARRIED:
    REPORT.require(f"selector carried ({target}: {fragment[:40]}…)",
                   fragment in _source(target), f"not in {target}")
for ref, text in SELECTOR_OPTIONS.items():
    check(f"selector option carried ({ref})", text,
          render("scene/response_selector_grimoire.j2"))
REPORT.require("selector options are the item's",
               list(SELECTOR_OPTIONS)
               == [o.id for o in response_protocol.selector_item(
                   SELECTOR_ROSTER, SELECTOR_CONVERSATION).questions[0].options][
                       len(SELECTOR_ROSTER):],
               f"carried descriptions for {list(SELECTOR_OPTIONS)}")

#: The driver keys of the suggestion snapshot (`build_snapshot(drivers=True)`,
#: which has no `upcoming`), empty.
DRIVER_KEYS = {"commitments": [], "timeline": [], "driver_index": [], "anchors": [],
               "links": [], "fixed": None, "near_days": 7, "sooner_ref": ""}
EMPTY_SNAP = {"now": "", "friendly": "",
              "notation": {"example": "", "months": []},
              "holidays_today": [], "events_today": [],
              "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
              "available_locations": [], **DRIVER_KEYS}


def _pressure_item(ref, kind, label, in_days, state, **extra):
    """One `pressure.build` item, in its shape, on a fixed day 1000 + in_days."""
    item = {"ref": ref, "kind": kind, "label": label, "native": "", "friendly": "",
            "fixed": None, "in_days": in_days, "relation": "on", "state": state,
            "subject": None, "due_text": "", "precision": None, "actor": None, "age": None}
    if in_days is not None:
        item.update(native=f"1492-Mirtul-{5 + in_days:02d}",
                    friendly=f"{5 + in_days} Mirtul 1492", fixed=1000 + in_days,
                    precision="exact")
    item.update(extra)
    return item


def _driver_row(ref, label, state, in_days=None, dormancy=None, links=()):
    """One `driver_index` row: a `drivers.snapshot` driver plus `dormancy`."""
    return {"ref": ref, "kind": ref.split(":", 1)[0], "label": label, "summary": "",
            "actors": [], "status": "",
            "pressure": {"state": state, "in_days": in_days, "friendly": ""},
            "time_anchors": [], "links": list(links), "dormancy": dormancy}
FULL_SNAP = {"now": "2026-07-05", "friendly": "July 5, 2026",
             # A non-Gregorian notation on purpose: the format lesson exists
             # because `friendly` is not a form the parser reads back, and a
             # fixture whose example matched ISO-8601 would render the same
             # whether or not the lesson was wired up at all.
             "notation": {"example": "1492-Mirtul-05",
                          "months": ["Hammer", "Alturiak", "Ches", "Mirtul"]},
             "holidays_today": ["Founding Day"],
             # A campaign-scheduled event (#101) beside the calendar's holiday:
             # the snapshot merges the two, so a fixture with only holidays
             # would leave the merged half of the line unrendered here.
             "events_today": ["The Envoy arrives"],
             "birthdays": [{"name": "Hero", "age": 26, "when": "today"},
                           {"name": "Mora", "age": 51, "when": "in 2 days"}],
             "story_so_far": [{"one_line": "Hero followed Seraphine into the fog.",
                               "location": "Night Dock", "date": "2026-07-05"},
                              {"one_line": "Hero met Kessler.", "location": "", "date": "2026-07-01"}],
             "open_threads": [{"id": "find-the-ledger", "ref": "thread:find-the-ledger",
                               "title": "Find the ledger", "status": "open",
                               "latest_beat": "Hero learned it exists.", "dormancy": 0},
                              {"id": "the-debt", "ref": "thread:the-debt",
                               "title": "The Guild debt", "status": "advanced", "latest_beat": "",
                               "dormancy": 3}],
             "cast": [{"token": "characters:mora", "name": "Mora",
                       "tagline": "A fortune-teller who deals in secrets.",
                       "status": "appeared", "role": "npc"},
                      {"token": "characters:silent-jim", "name": "Silent Jim", "tagline": "",
                       "status": "unseen", "role": "npc"},
                      {"token": "pcs:hero", "name": "Hero", "tagline": "", "status": "present",
                       "role": "player"}],
             "available_locations": [{"id": "night-dock", "name": "Night Dock"}],
             # The driver half: a commitment, a timeline holding every item
             # kind's line (an event, a holiday, a month-only birthday, a
             # deadline and a linked one), a three-row index, its anchor and
             # one reviewed link.
             "commitments": [{"id": "mara-s-oath", "ref": "commitment:mara-s-oath",
                              "title": "Mara's oath", "kind": "promise", "status": "open",
                              "due": "1492-Mirtul-07", "latest_beat": "Mara swore it.",
                              "dormancy": 1,
                              "pressure": {"state": "due_soon", "in_days": 2,
                                           "friendly": "7 Mirtul 1492"}},
                             {"id": "salt-owed", "ref": "commitment:salt-owed",
                              "title": "Salt owed", "kind": "debt", "status": "open",
                              "due": "", "latest_beat": "", "dormancy": 6,
                              "pressure": {"state": "due_soon", "in_days": 2,
                                           "friendly": "7 Mirtul 1492"}}],
             "timeline": [
                 _pressure_item("holiday:1001:Saltmarch Eve", "holiday", "Saltmarch Eve", 1,
                                "upcoming"),
                 _pressure_item("commitment:mara-s-oath", "deadline", "Mara's oath", 2,
                                "due_soon", subject="commitment:mara-s-oath",
                                due_text="1492-Mirtul-07"),
                 _pressure_item("event:the-coronation", "linked_deadline", "Salt owed", 2,
                                "due_soon", subject="commitment:salt-owed", relation="before"),
                 _pressure_item("event:the-coronation", "event", "The coronation", 3, "upcoming"),
                 _pressure_item("birthday:characters:winifred:month:1492-Mirtul", "birthday",
                                "Winifred", None, "ok", precision="month", friendly="Mirtul 1492",
                                actor="characters:winifred")],
             "driver_index": [
                 _driver_row("commitment:mara-s-oath", "Mara's oath", "due_soon", 2, 1,
                             links=[{"id": "l-1", "relation": "pays_off",
                                     "other": "thread:find-the-ledger", "direction": "in"}]),
                 _driver_row("event:the-coronation", "The coronation", "upcoming", 3),
                 _driver_row("thread:find-the-ledger", "Find the ledger", "ok", None, 0,
                             links=[{"id": "l-1", "relation": "pays_off",
                                     "other": "commitment:mara-s-oath", "direction": "out"}])],
             "anchors": [{"ref": "event:the-coronation", "kind": "event",
                          "label": "The coronation", "native": "1492-Mirtul-08",
                          "friendly": "8 Mirtul 1492", "fixed": 1003, "in_days": 3,
                          "precision": "exact"}],
             "links": [{"id": "l-1", "a": "thread:find-the-ledger",
                        "b": "commitment:mara-s-oath", "relation": "pays_off"}],
             "fixed": 1000, "near_days": 7, "sooner_ref": "holiday:1001:Saltmarch Eve"}
#: Past the cap on both axes: 45 thread drivers and a 45-item timeline, the
#: Upcoming pick the LAST item in rank order, so a focus row renders both "and
#: N more" lines and the pinned pick past the cut.
CAPPED_SNAP = {**EMPTY_SNAP, "now": "2026-07-05", "friendly": "July 5, 2026",
               "driver_index": [_driver_row(f"thread:saltmarch-{n}", f"Saltmarch thread {n}",
                                            "ok", None, 100 - n) for n in range(45)],
               "timeline": [_pressure_item(f"event:saltmarch-fair-{n}", "event",
                                           f"Saltmarch fair {n}", n + 1, "upcoming")
                            for n in range(45)],
               "sooner_ref": "event:saltmarch-fair-44"}
GREETINGS = [{"id": "g1", "name": "Storm greeting", "excerpt": "Rain hammers the piers."},
             {"id": "g2", "name": "Quiet morning", "excerpt": "The dock sleeps."},
             {"id": "g3", "name": "Debt call", "excerpt": "A collector knocks."}]
NONE = suggest.NO_CONTROLS
STEERED = suggest.Controls(focus=("thread:find-the-ledger",), avoid=("event:the-coronation",),
                           must=("commitment:mara-s-oath",))
for label, snap, cands, off, direction, controls in (
        ("empty", EMPTY_SNAP, None, False, "", NONE),
        ("full", FULL_SNAP, None, False, "", NONE),
        ("full+greetings", FULL_SNAP, GREETINGS, False, "", NONE),
        ("offscreen", FULL_SNAP, None, True, "", NONE),
        ("direction", FULL_SNAP, None, False, "something at sea", NONE),
        ("direction+greetings", FULL_SNAP, GREETINGS, False, "something at sea", NONE),
        ("full+focus/avoid/must", FULL_SNAP, None, False, "", STEERED),
        ("full+near", FULL_SNAP, None, False, "", suggest.Controls(time_mode="near")),
        ("full+move", FULL_SNAP, None, False, "", suggest.Controls(time_mode="move")),
        ("full+anchor", FULL_SNAP, None, False, "",
         suggest.Controls(time_mode="anchor", anchor="event:the-coronation",
                          relation="before")),
        ("capped+focus", CAPPED_SNAP, None, False, "",
         suggest.Controls(focus=("thread:saltmarch-44",)))):
    exp = suggest.build_prompt(snap, cands, offscreen=off, direction=direction,
                               controls=controls)
    view = suggest.driver_view(snap, controls)
    check(f"suggestions system ({label})", exp[0]["content"],
          render("scene_suggestions/system.j2", s=snap, offscreen=off,
                 greeting_candidates=cands, direction=direction, drivers=True, view=view))
    check(f"suggestions user ({label})", exp[1]["content"],
          render("scene_suggestions/user.j2", s=snap, offscreen=off,
                 greeting_candidates=cands, direction=direction, drivers=True, view=view))

#: The scene tracker's final state as `routes.scenes._absorb_tracked` hands it
#: over (`tracker.view.lines_for` for the narrator, empty lines dropped).
TRACKED = [{"ref": "characters:seraphine-vale", "id": "characters/seraphine-vale",
            "name": "Seraphine Vale", "own": False, "values": [
               {"label": "Visible mood", "text": "fear", "private": False},
               {"label": "Concealed", "text": "the ledger", "private": True}]},
           {"ref": "pcs:hero", "id": "pcs/hero", "name": "Hero", "own": False, "values": [
               {"label": "Holding", "text": "a lantern", "private": False}]}]

for label, facts, st, rel, plt, grp, cmt, fct, strg, trk in (
        ("bare", {}, None, None, None, None, None, None, None, None),
        ("tracked", {}, None, None, None, None, None, None, None, TRACKED),
        ("full", {"location": "Night Dock", "date": "2026-07-05",
                  "cast": ["characters/seraphine-vale", "pcs/hero"]},
         {"Seraphine Vale": "Wounded. Knows: The ledger is real."},
         "Seraphine Vale → Hero: trust 2, affection 3, tension 4 (suspects a tail)",
         "find-the-ledger: Find the ledger (open) — Hero learned it exists.",
         "- groups/salt-circle (Salt Circle): Goals: Expand.",
         "the-deadline: Midnight deadline (threat, open), due midnight "
         "— Hero was given until midnight.",
         "f1: The warehouse belongs to the Salt Circle. (the third night)",
         "- Seraphine was told about the tail at the Night Dock", TRACKED)):
    exp = absorb.build_prompt(transcript, facts, st, rel, plt, grp, cmt, fct, strg,
                              tracked_snapshot=trk)
    check(f"absorb system ({label})", exp[0]["content"],
          render("absorb/system.j2", steering=bool(strg)))
    assert '"why_new"' in exp[0]["content"], \
        f"absorb system ({label}) no longer asks new-record rows for \"why_new\""
    if strg:
        assert "Treat them as pointers, not as story" in exp[0]["content"], \
            f"absorb system ({label}) missing the steering contract paragraph"
    else:
        assert "Player steering notes" not in exp[0]["content"], \
            f"absorb system ({label}) carries the steering paragraph with no notes"
    check(f"absorb user ({label})", exp[1]["content"],
          render("absorb/user.j2", facts=facts, state_snapshot=st, rel_snapshot=rel,
                 plot_snapshot=plt, group_snapshot=grp, commitment_snapshot=cmt,
                 fact_snapshot=fct, steering_snapshot=strg,
                 tracked_snapshot=trk or [], transcript=transcript))
    if trk:
        assert ("Final tracked state (as the scene ended; private values marked):\n"
                "- Seraphine Vale (characters/seraphine-vale): Visible mood: fear; "
                "Concealed: the ledger (private)\n"
                "- Hero (pcs/hero): Holding: a lantern") in exp[1]["content"], \
            f"absorb user ({label}) missing the Final tracked state block"
    else:
        assert "Final tracked state" not in exp[1]["content"], \
            f"absorb user ({label}) renders a tracked-state block with no tracked state"
        assert exp[1]["content"] == absorb.build_prompt(
            transcript, facts, st, rel, plt, grp, cmt, fct, strg,
            tracked_snapshot=[])[1]["content"], \
            f"absorb user ({label}) changes with an empty tracked state"
    if grp:
        assert "Groups:" in exp[1]["content"], f"absorb user ({label}) missing Groups: head line"
    if cmt:
        assert "Open commitments:" in exp[1]["content"], \
            f"absorb user ({label}) missing Open commitments: head line"
    if fct:
        assert "Standing facts:" in exp[1]["content"], \
            f"absorb user ({label}) missing Standing facts: head line"
    if strg:
        assert "Player steering notes" in exp[1]["content"], \
            f"absorb user ({label}) missing Player steering notes head line"

msgs = [{"role": "user", "content": "hi"},
        {"role": "user", "speaker": "Hero", "content": "yo"},
        {"role": "assistant", "speaker": "Seraphine Vale", "content": "Try me."},
        {"role": "assistant", "content": "Fog rolls in."}]
check("transcript", chronicle.transcript_text(msgs), render("snippets/transcript.j2", messages=msgs))

for st in ({"current_state": "Wounded.", "knows": "The ledger is real.", "suspects": "Bob lied."},
           {"current_state": "", "knows": "The ledger is real.", "suspects": ""},
           {"current_state": "Hiding.", "knows": "", "suspects": ""}):
    check(f"state snapshot line ({st['current_state'] or 'no-cs'})",
          absorb._snapshot_line(st), render("snippets/state_snapshot_line.j2", st=st))

routes_src = "\n".join(p.read_text(encoding="utf-8")
                       for p in sorted((REPO / "backend/src/grimoire/routes").glob("*.py")))
assert 'prompts.render("scene/director_note.j2")' in routes_src, \
    "the routes package no longer renders the director-note template"
assert 'prompts.render("scene/regenerate_guidance.j2"' in routes_src, \
    "the routes package no longer renders the regenerate-guidance template"
assert 'prompts.render(\n        "scene/roll_result.j2"' in routes_src \
    or 'prompts.render("scene/roll_result.j2"' in routes_src, \
    "the routes package no longer renders the roll-result continuation template (#162)"
assert 'prompts.render("scene/roll_declined.j2")' in routes_src, \
    "the routes package no longer renders the roll-declined continuation template (#162)"

# The continuity identity resolver (capstone spec §10.2). Rows are
# `Examination.prompt_rows()`-shaped; three inputs so every optional branch of
# `user.j2` is taken both ways: every optional field, none, and a closed
# neighbour carrying earlier beats and every signal.
from grimoire.store.continuity import identity  # noqa: E402


def _identity_signals(**over):
    return {"title_equal": False, "slug_equal": False, "tokens": 0.0, "chars": 0.0,
            "cosine": None, "actors": [], "scenes": [], "anchors": [], "via": "lexical",
            **over}


def _identity_row(key, kind, title, candidates, **over):
    return {"key": key, "kind": kind, "title": title, "beat": "Winifred went looking.",
            "status": "", "commitment_kind": "", "due": "", "quote": "", "speaker": "",
            "certainty": None, "why_new": "", "distinguished_from": [],
            "candidates": candidates, **over}


def _identity_candidate(cid_, title, **over):
    return {"id": cid_, "title": title, "status": "open", "kind": "", "due": "",
            "latest_beat": "", "earlier": [], "signals": _identity_signals(), **over}


IDENTITY_INPUTS = {
    "thread+commitment": [
        _identity_row("r1", "thread", "Recover the harbour ledger", [
            _identity_candidate("find-the-ledger", "Find the ledger", status="advanced",
                                latest_beat="Winifred learned the harbour ledger exists.",
                                signals=_identity_signals(tokens=0.4, chars=0.52))],
            status="open", quote="I want that ledger.", speaker="Winifred", certainty=0.8,
            why_new="The search is for a different ledger.",
            distinguished_from=["find-the-ledger"]),
        _identity_row("r2", "commitment", "Seraphine's midnight deadline", [
            _identity_candidate("the-midnight-deadline", "The midnight deadline",
                                due="midnight",
                                latest_beat="Seraphine must pay by midnight.",
                                signals=_identity_signals(tokens=0.5, cosine=0.81,
                                                          actors=["characters:seraphine"]))],
            status="open", commitment_kind="threat", due="midnight",
            speaker="Seraphine", why_new="A second deadline.")],
    "bare": [
        _identity_row("r1", "commitment", "The midnight deadline", [
            _identity_candidate("the-midnight-deadline", "The midnight deadline",
                                signals=_identity_signals(title_equal=True,
                                                          slug_equal=True))])],
    "closed-neighbour": [
        _identity_row("r1", "thread", "Find the ledger", [
            _identity_candidate("find-the-ledger", "Find the ledger", status="closed",
                                latest_beat="Winifred burned the ledger.",
                                earlier=["Winifred found the first page.",
                                         "Winifred learned the harbour ledger exists."],
                                signals=_identity_signals(
                                    title_equal=True, slug_equal=True, tokens=0.6,
                                    chars=0.7, cosine=0.9, actors=["characters:winifred"],
                                    scenes=["saltmarch-docks"], anchors=["event:e1"]))],
            status="open", certainty=0.5)],
}
for label, rows in IDENTITY_INPUTS.items():
    exp = identity.build_prompt(rows)
    check(f"continuity identity system ({label})", exp[0]["content"],
          render("continuity_identity/system.j2"))
    shown = identity.template_rows(rows)
    check(f"continuity identity user ({label})", exp[1]["content"],
          render("continuity_identity/user.j2", rows=shown))
    for row in shown:
        for cand in row["candidates"]:
            if row["kind"] == "thread":
                want = render("snippets/plot_thread_line/absorb.j2",
                              t={"id": cand["id"], "title": cand["title"],
                                 "status": cand["status"], "latest_beat": cand["latest_beat"]})
            else:
                want = render("snippets/commitment_line/absorb.j2",
                              c={"id": cand["id"], "title": cand["title"],
                                 "kind": cand["kind"] or "promise", "status": cand["status"],
                                 "due": cand["due"], "latest_beat": cand["latest_beat"]})
            check(f"continuity identity line ({label}, {cand['id']})", want, cand["line"])
            assert cand["line"] in exp[1]["content"], \
                f"continuity identity user ({label}) does not show {cand['id']}'s line"

# The continuity identity resolver as decision items (slice G, spec 7.4):
# `identity.build_items` over the same row sets, against direct renders. Each
# item's context is `item.j2` over its row; its `decision` choice is
# `question.j2`'s with each option described by `option.j2`; its `id` choice is
# `record.j2`'s over the row's candidates, each described by its title. While
# the legacy `user.j2` exists, a context below its heading is that template's
# row block byte for byte.
for label, rows in IDENTITY_INPUTS.items():
    items = identity.build_items(rows, {})
    for item, shown in zip(items, identity.template_rows(rows), strict=True):
        tag = f"{label}, {shown['key']}"
        check(f"continuity identity item context ({tag})", item.context,
              render("continuity_identity/item.j2", r=shown))
        heading, _, body = item.context.partition("\n")
        legacy_head, _, legacy_body = render("continuity_identity/user.j2",
                                             rows=[shown]).partition("\n")
        check(f"continuity identity item body is today's row ({tag})", body, legacy_body)
        REPORT.require(f"continuity identity item heading ({tag})",
                       heading == f"Proposed {legacy_head.partition(' — proposed ')[2]}",
                       f"{heading!r} does not name what {legacy_head!r} does")
        REPORT.require(f"continuity identity item questions ({tag})",
                       [q.id for q in item.questions]
                       == [identity.DECISION_ID, identity.RECORD_ID],
                       f"asks {[q.id for q in item.questions]}")
        decision, record = item.questions
        check(f"continuity identity decision question ({tag})", decision.instructions,
              render("continuity_identity/question.j2"))
        for opt in decision.options:
            check(f"continuity identity option {opt.id} ({tag})", opt.description,
                  render("continuity_identity/option.j2", decision=opt.id))
        check(f"continuity identity record question ({tag})", record.instructions,
              render("continuity_identity/record.j2"))
        REPORT.require(f"continuity identity record options ({tag})",
                       [(o.id, o.description) for o in record.options]
                       == [(c["id"], c["title"] or c["id"]) for c in shown["candidates"]]
                       and record.allow_none and not decision.allow_none,
                       f"offers {[(o.id, o.description) for o in record.options]}")
check("continuity identity explain", identity.explain(),
      render("continuity_identity/explain.j2"))

#: The duplicate check's criteria, carried out of its legacy one-call prompt
#: (`continuity_identity/system.j2`, until the switch retires it) into the
#: decide-era templates word for word (I8): (fragment, the template it went
#: to). Invent no criterion, and drop none.
IDENTITY_CARRIED = (
    ("You are checking whether newly proposed story records already exist.",
     "continuity_identity/question.j2"),
    (("A plot thread is an open narrative question; a commitment is an obligation someone "
      "owes (a promise, a threat or a piece of foreshadowing)."),
     "continuity_identity/question.j2"),
    (('A closed or resolved candidate is never "existing": it is listed so you can see that '
      "business was already settled, and a later development is a new record, not that "
      'one. A row\'s "distinguished_from" ids and the signals under each candidate are '
      "hints, not proof: shared words, characters, scenes or dates make two records worth "
      "comparing, never the same record."), "continuity_identity/question.j2"),
    ('Give that candidate\'s "id" exactly as it is listed.', "continuity_identity/record.j2"),
    ("one short sentence", "continuity_identity/explain.j2"),
)
#: Each decision's description, carried out of its legacy bullet word for word
#: after the quoted word, keyed by the decision it describes: `option.j2` must
#: render exactly this text for exactly this decision. A description moved to
#: another word would turn the check around, and the gate (parser-only) cannot
#: see it. The existing bullet's id sentence is `record.j2`'s (above).
IDENTITY_OPTIONS = {
    "existing": ("only when a listed candidate is the same narrative question or "
                 "obligation, so the row's beat simply moves that record forward."),
    "new": ("when the row is a different question, a continuation, or a related subplot: "
            "something that grew out of a listed record but is business of its own "
            "deserves a record of its own."),
    "uncertain": ("when the transcript cannot tell: the cited evidence and the beats do not "
                  "settle whether the row and a candidate are the same business."),
}
#: Sentences the decide prompt lays out differently, reworded rather than
#: dropped: (old, new, the template `new` went to, why).
IDENTITY_REWORDED = (
    (("A scene was just absorbed, and the extraction proposed opening some NEW plot threads "
      "or commitments."),
     ("A scene was just absorbed, and the extraction proposed opening a NEW plot thread or "
      "commitment."),
     "continuity_identity/question.j2", "one item asks about one row"),
    (("Each proposed record is listed below as a row: its title, the beat the scene gave "
      "it, the transcript evidence the extraction cited, why the extraction called it new, "
      "and up to three existing records of the same type that look similar, each with its "
      "id, status, latest beats and the similarity signals that put it on the list."),
     ("The proposed record is shown above: its title, the beat the scene gave it, the "
      "transcript evidence the extraction cited, why the extraction called it new, and up "
      "to three existing records of the same type that look similar, each with its id, "
      "status, latest beats and the similarity signals that put it on the list."),
     "continuity_identity/question.j2",
     "decide/user.j2 renders the item's context above its questions"),
    ('Leave "id" empty unless the decision is "existing".',
     'Answer null for "id" unless the decision is "existing".',
     "continuity_identity/record.j2", "an empty string is not an option; null is"),
)
#: The legacy prompt's reply format, which `decide/system.j2` owns now: no
#: criterion lives in these. The example is cut around its "reason"
#: placeholder, which is the carried rationale instruction (`explain.j2`), so
#: that fragment's coverage is its own rather than the example's.
IDENTITY_FORMAT = (
    "Give exactly one decision per row:",
    "Reply with ONLY a JSON object, no prose around it:",
    ('{"decisions": [{"row": "<row key>", "decision": "existing" | "new" | "uncertain", '
     '"id": "<candidate id, for existing>", "reason": "<'),
    '>"}]}',
    '"row" is the key after "Row" (for "Row r1", write "r1").',
)
for fragment, target in IDENTITY_CARRIED:
    REPORT.require(f"continuity identity carried ({target}: {fragment[:40]}…)",
                   fragment in _source(target), f"not in {target}")
for word, text in IDENTITY_OPTIONS.items():
    check(f"continuity identity option carried ({word})",
          text, render("continuity_identity/option.j2", decision=word))
REPORT.require("continuity identity options are the item's",
               list(IDENTITY_OPTIONS)
               == [o.id for o in identity.build_items(
                   IDENTITY_INPUTS["bare"], {})[0].questions[0].options],
               f"carried descriptions for {list(IDENTITY_OPTIONS)}")
for _old, new, target, why in IDENTITY_REWORDED:
    REPORT.require(f"continuity identity reworded ({target}: {new[:40]}…)",
                   new in _source(target), f"not in {target} ({why})")

#: Each decision's legacy bullet, quoted word and description together: while
#: the legacy prompt exists, the pairing above must be its pairing.
IDENTITY_BULLETS = [f'- "{word}" {text}' for word, text in IDENTITY_OPTIONS.items()]
_LEGACY_IDENTITY = (REPO / "templates" / "continuity_identity" / "system.j2").read_text(
    encoding="utf-8")
_IDENTITY_BITES = ([f for f, _ in IDENTITY_CARRIED] + IDENTITY_BULLETS
                   + [old for old, _, _, _ in IDENTITY_REWORDED])
for fragment in _IDENTITY_BITES + list(IDENTITY_FORMAT):
    REPORT.require(f"continuity identity fragment is the old prompt's ({fragment[:40]}…)",
                   fragment in _source("continuity_identity/system.j2"),
                   "not in continuity_identity/system.j2")


def _identity_fragments(without: str = "") -> list[str]:
    return [s for f in _IDENTITY_BITES if f != without
            for s in _sentences(f)] + list(IDENTITY_FORMAT)


for sentence, words in _leftover(_LEGACY_IDENTITY, _identity_fragments()):
    REPORT.require(f"continuity identity coverage ({sentence[:40]}…)", not words,
                   "continuity_identity/system.j2 says what no fragment carries: "
                   f"{' '.join(words)!r}")
# Coverage that bites: with any one carried, option or reworded fragment gone
# from the tables, some sentence of the legacy prompt is left uncovered.
for fragment in _IDENTITY_BITES:
    REPORT.require(f"continuity identity coverage misses ({fragment[:40]}…)",
                   any(words for _, words in _leftover(_LEGACY_IDENTITY,
                                                       _identity_fragments(fragment))),
                   "coverage passes without it, so the tables do not hold it")

# The reconciliation sweep (capstone spec §11.2). Payloads are
# `reconcile.build_payload`-shaped; three inputs so every optional branch of
# `user.j2` is taken both ways: pairs and lifecycle findings with every optional
# field, none, and a temporal pair whose second record is an event.
from grimoire.store.continuity import reconcile  # noqa: E402


def _reconcile_fields(title, status="open", kind="", due=""):
    return {"title": title, "status": status, "kind": kind, "due": due}


def _reconcile_record(letter, ref, fields, **over):
    kind = {"thread": "plot thread", "commitment": "commitment"}.get(ref.partition(":")[0], "")
    return {"letter": letter, "ref": ref, "type": kind,
            "line": reconcile.snippet_line(ref, fields),
            "beats": [], "pressure": "", "links": [], "actors": [], "_fields": fields, **over}


def _reconcile_candidate(key, vocabulary, records, signal_text=""):
    return {"key": key, "id": f"candidate-{key}", "vocabulary": vocabulary,
            "records": records, "signal_text": signal_text}


RECONCILE_INPUTS = {
    "pairs+lifecycle": {
        "now": "the twelfth of May",
        "known_scenes": ["001--saltmarch-docks", "002--realm-road"],
        "chronicle": [{"id": "001--saltmarch-docks", "one_line": "Mara came ashore."},
                      {"id": "002--realm-road", "one_line": "The road was long."}],
        "recent": ["002--realm-road"],
        "candidates": [
            _reconcile_candidate("c1", "same_thread", [
                _reconcile_record("A", "thread:find-the-ledger",
                                  _reconcile_fields("Find the ledger", "advanced"),
                                  beats=[{"scene": "001--saltmarch-docks",
                                          "text": "Winifred learned the ledger exists."},
                                         {"scene": "", "text": "Winifred went looking."}],
                                  pressure="stale", actors=["Winifred", "Mara"],
                                  links=["Find the ledger pays_off The midnight deadline"]),
                _reconcile_record("B", "thread:recover-the-harbour-ledger",
                                  _reconcile_fields("Recover the harbour ledger"))],
                "same title; word overlap 0.42; shared characters: Mara"),
            _reconcile_candidate("c2", "commitment", [
                _reconcile_record("A", "commitment:the-midnight-deadline",
                                  _reconcile_fields("The midnight deadline", kind="threat",
                                                    due="2026-05-05"),
                                  pressure="overdue, 5 days ago", actors=["Seraphine"])],
                "its due date has passed (5 days ago)")]},
    "bare": {
        "now": "", "known_scenes": [], "chronicle": [], "recent": [],
        "candidates": [_reconcile_candidate("c1", "thread", [
            _reconcile_record("A", "thread:mara-s-map", _reconcile_fields("Mara's map"))])]},
    "temporal": {
        "now": "Saltmarch Eve", "known_scenes": [], "chronicle": [], "recent": [],
        "candidates": [_reconcile_candidate("c1", "temporal", [
            _reconcile_record("A", "commitment:mara-s-oath",
                              _reconcile_fields("Mara's oath", due="before the bells stop")),
            _reconcile_record("B", "event:the-coronation",
                              {"title": "The coronation", "status": "", "kind": "",
                               "due": "2026-05-13"})],
            "the commitment's due could not be placed on the calendar; the event is in 3 days")]},
}
for label, payload in RECONCILE_INPUTS.items():
    exp = reconcile.build_prompt(payload)
    check(f"continuity reconcile system ({label})", exp[0]["content"],
          render("continuity_reconcile/system.j2"))
    check(f"continuity reconcile user ({label})", exp[1]["content"],
          render("continuity_reconcile/user.j2", **reconcile.template_vars(payload)))
    for cand in payload["candidates"]:
        for rec in cand["records"]:
            fields, (prefix, _, rid) = rec["_fields"], rec["ref"].partition(":")
            if prefix == "thread":
                want = render("snippets/plot_thread_line/absorb.j2",
                              t={"id": rid, "title": fields["title"],
                                 "status": fields["status"], "latest_beat": ""})
            elif prefix == "commitment":
                want = render("snippets/commitment_line/absorb.j2",
                              c={"id": rid, "title": fields["title"],
                                 "kind": fields["kind"] or "promise", "status": fields["status"],
                                 "due": fields["due"], "latest_beat": ""})
            else:
                want = f"event: {fields['title']} ({fields['due']})"
            check(f"continuity reconcile line ({label}, {rec['ref']})", want, rec["line"])
            shown = f" ({rec['type']})" if rec["type"] else ""
            assert f"{rec['letter']}{shown}: {rec['line']}" in exp[1]["content"], \
                f"continuity reconcile user ({label}) does not show {rec['ref']}'s line"

# The reconciliation sweep as decision items (slice G, spec 7.4):
# `reconcile.build_items` over the same payloads, against direct renders. Each
# item's context is `item.j2` over its candidate and the scene lines it shows;
# its `decision` choice is `question.j2`'s under its vocabulary, each option
# labelled by its own word; a pair's `from` and `to` are `direction.j2`'s and
# `direction_to.j2`'s over `record_option.j2`; and one evidence choice per
# shown scene, up to `EVIDENCE_SCENES`, is `evidence.j2`'s and then
# `evidence_more.j2`'s over `scene_option.j2`. While the legacy `user.j2`
# exists, a context's preamble is that template's over the item's lines, and
# its body below the heading that template's candidate block byte for byte.
for label, payload in RECONCILE_INPUTS.items():
    items = reconcile.build_items(payload)
    for item, cand in zip(items, payload["candidates"], strict=True):
        tag, vocab = f"{label}, {cand['key']}", cand["vocabulary"]
        scenes_shown = reconcile.item_scenes(payload, cand)
        lines = [line for line in payload["chronicle"] if line["id"] in scenes_shown]
        check(f"continuity reconcile item context ({tag})", item.context,
              render("continuity_reconcile/item.j2", now=payload["now"], chronicle=lines,
                     c={"label": reconcile.LABELS[vocab], "records": cand["records"],
                        "signal_text": cand["signal_text"]}))
        heading = f"Candidate — {reconcile.LABELS[vocab]}"
        preamble, _, body = item.context.partition(heading)
        legacy_head, _, legacy_body = render(
            "continuity_reconcile/user.j2", now="", chronicle=[],
            candidates=reconcile.template_vars({**payload, "candidates": [cand]})["candidates"],
        ).partition("\n")
        check(f"continuity reconcile item body is today's candidate ({tag})", body,
              "\n" + legacy_body if legacy_body else "")
        REPORT.require(f"continuity reconcile item heading ({tag})",
                       legacy_head.endswith(f" — {reconcile.LABELS[vocab]} (answer with: "
                                            + ", ".join(f'"{w}"' for w in
                                                        reconcile.DECISIONS[vocab]) + ")"),
                       f"{heading!r} does not name what {legacy_head!r} does")
        today = render("continuity_reconcile/user.j2", now=payload["now"], chronicle=lines,
                       candidates=[])
        REPORT.require(f"continuity reconcile item preamble is today's ({tag})",
                       preamble == (today + "\n" if today else ""),
                       f"{preamble!r} is not {today!r}")
        decision, *rest = item.questions
        check(f"continuity reconcile decision question ({tag})", decision.instructions,
              render("continuity_reconcile/question.j2", vocabulary=vocab))
        # Options tied to ids: each labelled by its own word, in DECISIONS order.
        REPORT.require(f"continuity reconcile decision options ({tag})",
                       decision.id == reconcile.DECISION_ID and not decision.allow_none
                       and [(o.id, o.description) for o in decision.options]
                       == [(w, w.replace("_", " ")) for w in reconcile.DECISIONS[vocab]],
                       f"offers {[(o.id, o.description) for o in decision.options]}")
        # A direction on, and only on, a pair; then the evidence slots.
        pair = vocab in reconcile.PAIR_VOCABULARIES
        direction, evidence = (rest[:2], rest[2:]) if pair else ([], rest)
        REPORT.require(f"continuity reconcile direction asked on pairs only ({tag})",
                       [q.id for q in direction]
                       == ([reconcile.FROM_ID, reconcile.TO_ID] if pair else []),
                       f"asks {[q.id for q in item.questions]}")
        letters = [(r["letter"], render("continuity_reconcile/record_option.j2",
                                        letter=r["letter"])) for r in cand["records"]]
        for q, template in zip(direction, ("direction.j2", "direction_to.j2"), strict=False):
            check(f"continuity reconcile {q.id} question ({tag})", q.instructions,
                  render(f"continuity_reconcile/{template}"))
            REPORT.require(f"continuity reconcile {q.id} options ({tag})",
                           q.allow_none and [(o.id, o.description) for o in q.options] == letters,
                           f"offers {[(o.id, o.description) for o in q.options]}")
        count = min(len(scenes_shown), reconcile.EVIDENCE_SCENES)
        REPORT.require(f"continuity reconcile evidence slots ({tag})",
                       [q.id for q in evidence] == list(reconcile.EVIDENCE_IDS[:count]),
                       f"asks {[q.id for q in evidence]} over {scenes_shown}")
        offered = [(sid, render("continuity_reconcile/scene_option.j2", sid=sid))
                   for sid in scenes_shown]
        for k, q in enumerate(evidence):
            check(f"continuity reconcile {q.id} question ({tag})", q.instructions,
                  render("continuity_reconcile/evidence.j2" if k == 0
                         else "continuity_reconcile/evidence_more.j2"))
            REPORT.require(f"continuity reconcile {q.id} options ({tag})",
                           q.allow_none and [(o.id, o.description) for o in q.options]
                           == offered, f"offers {[(o.id, o.description) for o in q.options]}")
        # No option repeats its context (M1).
        repeated = [o.description for q in rest for o in q.options
                    if len(o.description) > len(o.id) and o.description in item.context]
        REPORT.require(f"continuity reconcile options do not repeat the context ({tag})",
                       not repeated, f"{repeated} already in the context")
check("continuity reconcile explain", reconcile.explain(),
      render("continuity_reconcile/explain.j2"))
REPORT.require("continuity reconcile fixtures ask every kind of question",
               {len(reconcile.item_scenes(p, c)) for p in RECONCILE_INPUTS.values()
                for c in p["candidates"]} >= {0, 1, 2}
               and {c["vocabulary"] for p in RECONCILE_INPUTS.values()
                    for c in p["candidates"]} & set(reconcile.PAIR_VOCABULARIES) != set(),
               "no fixture shows two scenes, or none asks a direction")

#: The sweep's criteria, carried out of its legacy one-call prompt
#: (`continuity_reconcile/system.j2`, until the switch retires it) into the
#: decide-era templates word for word (I8): (fragment, the template it went
#: to). Invent no criterion, and drop none.
RECONCILE_CARRIED = (
    (("A plot thread is an open narrative question; a commitment is an obligation someone "
      "owes (a promise, a debt, a threat or a piece of foreshadowing); an event is a dated "
      "occasion on the campaign calendar."), "continuity_reconcile/question.j2"),
    ("Signals say why a candidate is worth a look, never what the answer is.",
     "continuity_reconcile/question.j2"),
    ('When what is shown cannot settle a candidate, answer "uncertain".',
     "continuity_reconcile/question.j2"),
    (('for "duplicate", "from" is the record to fold away and "to" the one to keep; "from" '
      'continues "to", or is a subthread of "to"; for "pays_off", "from" is the plot thread '
      'and "to" the commitment.'), "continuity_reconcile/direction.j2"),
    ("one short sentence", "continuity_reconcile/explain.j2"),
)
#: Each vocabulary's legacy bullet, whole, keyed by the vocabulary it states
#: the criteria of: `question.j2` must render exactly this bullet for exactly
#: this vocabulary (and two commitments, judged by "the same rules", the plot
#: threads' bullet before their own). A bullet moved to another vocabulary
#: would ask one candidate another's question, and the gate (parser-only)
#: cannot see it.
RECONCILE_BULLETS = {
    "same_thread": ('- Two plot threads ("duplicate", "continuation", "subthread", "related", '
                    '"distinct", "uncertain"): "duplicate" only when both records are the same '
                    "question or obligation, so the two should be read as one record; a "
                    'narrower or later question is "continuation" or "subthread", not '
                    '"duplicate". "related" when they bear on each other but are separate '
                    'business, "distinct" when they do not.'),
    "same_commitment": ('- Two commitments ("duplicate", "related", "distinct", "uncertain"): '
                        "the same rules, except that a commitment is never a continuation or "
                        "a subthread of another."),
    "cross": ('- A plot thread and a commitment ("pays_off", "related", "distinct", '
              '"uncertain"): "pays_off" when settling the thread is how the commitment is '
              'settled. A thread and a commitment are never "duplicate".'),
    "thread": ('- Whether a plot thread is finished ("close", "keep_open", "uncertain"): '
               '"close" only when a beat or a scene line shows the question answered. Age '
               "alone is never evidence that a thread is finished."),
    "commitment": ('- Whether a commitment is resolved ("fulfilled", "broken", "expired", '
                   '"keep_open", "uncertain"): "expired" when its occasion went by with nobody '
                   "keeping or breaking it. A passed deadline alone is never evidence that a "
                   "promise was kept or broken."),
    "temporal": ('- A commitment and a dated event ("before", "on", "after", "by", "unrelated", '
                 '"uncertain"): whether the commitment falls due before, on, after or by that '
                 "event. Do not invent a date: when nothing shown ties the commitment to the "
                 'event, answer "unrelated" or "uncertain".'),
}
#: The bullets a vocabulary's question carries: its own, after the one its own
#: refers back to.
_RECONCILE_ASKS = {vocab: (("same_thread",) if vocab == "same_commitment" else ()) + (vocab,)
                   for vocab in reconcile.DECISIONS}
#: Sentences the decide prompt lays out differently, reworded rather than
#: dropped: (old, new, the template `new` went to, why).
RECONCILE_REWORDED = (
    (("You are reviewing a campaign's story ledger for records that may overlap or be "
      "finished."),
     ("You are reviewing a campaign's story ledger for records that may overlap or be "
      "finished."),
     "continuity_reconcile/question.j2",
     "kept verbatim; listed beside the sentence after it, which moves"),
    (("Each candidate below shows one or two records, lettered A and B: the record's line, "
      "its latest beats with the scene each happened in, its deadline or staleness, the "
      "links already recorded on it and the people involved, and then the signals that put "
      "it on the list."),
     ("The candidate above shows one or two records, lettered A and B: the record's line, "
      "its latest beats with the scene each happened in, its deadline or staleness, the "
      "links already recorded on it and the people involved, and then the signals that put "
      "it on the list."),
     "continuity_reconcile/question.j2",
     "decide/user.j2 renders the item's context above its questions"),
    (('For "duplicate", "continuation", "subthread" and "pays_off", give the direction as '
      'letters in "from" and "to":'),
     ('For "duplicate", "continuation", "subthread" and "pays_off", give the direction as '
      'letters in "from" and "to", and null for both when the decision has no direction:'),
     "continuity_reconcile/direction.j2", "an empty string is not an option; null is"),
    (('For "close", "fulfilled", "broken" and "expired", give a reason and name at least one '
      'evidence scene id from the lines shown; without both, the answer counts as '
      '"uncertain".'),
     ('For "close", "fulfilled", "broken" and "expired", name at least one evidence scene id '
      'from the lines shown; without one, the answer counts as "uncertain".'),
     "continuity_reconcile/evidence.j2",
     ("a verdict stands without a rationale (spec 7.4, I4), which explain.j2 still asks "
      "for; 'at least one' stays, as up to EVIDENCE_SCENES are asked for (I2)")),
)
#: Words the decide prompt adds that the legacy prompt never said: each a
#: pointer or a label, never a criterion. Printed, so they stay visible.
RECONCILE_ADDED = (
    ('The record the direction runs to; see "from".', "continuity_reconcile/direction_to.j2"),
    ("Another evidence scene id from the lines shown, or null; see the first.",
     "continuity_reconcile/evidence_more.j2"),
    ("record {{ letter }}", "continuity_reconcile/record_option.j2"),
    ("the scene listed above as {{ sid }}", "continuity_reconcile/scene_option.j2"),
)
#: The legacy prompt's reply format, which `decide/system.j2` owns now, and
#: its two "leave it empty" rules, which the reworded direction and evidence
#: sentences carry as null: no criterion lives in these. The example is cut
#: around its "reason" placeholder, which is the carried rationale
#: instruction (`explain.j2`), so that fragment's coverage is its own.
RECONCILE_FORMAT = (
    "Give exactly one decision per candidate, using only the words its line offers:",
    "Reply with ONLY a JSON object, no prose around it:",
    ('{"decisions": [{"candidate": "<key>", "decision": "<word>", "from": "A" | "B", '
     '"to": "A" | "B", "reason": "<'),
    '>", "evidence_scenes": ["<scene id>"]}]}',
    '"candidate" is the key after "Candidate" (for "Candidate c1", write "c1").',
    ('Leave "from" and "to" empty when the decision has no direction, and "evidence_scenes" '
     "empty when no scene settles it."),
)
for fragment, target in RECONCILE_CARRIED:
    REPORT.require(f"continuity reconcile carried ({target}: {fragment[:40]}…)",
                   fragment in _source(target), f"not in {target}")
for vocab, bullet in RECONCILE_BULLETS.items():
    REPORT.require(f"continuity reconcile bullet names its vocabulary ({vocab})",
                   bullet.startswith(
                       f"- {reconcile.LABELS[vocab][0].upper()}{reconcile.LABELS[vocab][1:]} ("
                       + ", ".join(f'"{w}"' for w in reconcile.DECISIONS[vocab]) + "):"),
                   "its label or its words are not this vocabulary's")
for vocab, asks in _RECONCILE_ASKS.items():
    question = render("continuity_reconcile/question.j2", vocabulary=vocab)
    REPORT.require(f"continuity reconcile question carries its bullets ({vocab})",
                   all(RECONCILE_BULLETS[w] in question for w in asks)
                   and not any(b in question for w, b in RECONCILE_BULLETS.items()
                               if w not in asks)
                   and [question.index(RECONCILE_BULLETS[w]) for w in asks]
                   == sorted(question.index(RECONCILE_BULLETS[w]) for w in asks),
                   f"carries {[w for w, b in RECONCILE_BULLETS.items() if b in question]}")
REPORT.require("continuity reconcile bullets are the vocabularies'",
               list(RECONCILE_BULLETS) == list(reconcile.DECISIONS),
               f"bullets for {list(RECONCILE_BULLETS)}")
for _old, new, target, why in RECONCILE_REWORDED:
    REPORT.require(f"continuity reconcile reworded ({target}: {new[:40]}…)",
                   new in _source(target), f"not in {target} ({why})")
for text, target in RECONCILE_ADDED:
    REPORT.require(f"continuity reconcile added ({target})", text in _source(target),
                   f"not in {target}")
    print(f"continuity reconcile adds, in {target}: {text}")

_LEGACY_RECONCILE = (REPO / "templates" / "continuity_reconcile" / "system.j2").read_text(
    encoding="utf-8")
_RECONCILE_BITES = ([f for f, _ in RECONCILE_CARRIED] + list(RECONCILE_BULLETS.values())
                    + [old for old, _, _, _ in RECONCILE_REWORDED])
for fragment in _RECONCILE_BITES + list(RECONCILE_FORMAT):
    REPORT.require(f"continuity reconcile fragment is the old prompt's ({fragment[:40]}…)",
                   fragment in _source("continuity_reconcile/system.j2"),
                   "not in continuity_reconcile/system.j2")


def _reconcile_fragments(without: str = "") -> list[str]:
    return [s for f in _RECONCILE_BITES if f != without
            for s in _sentences(f)] + list(RECONCILE_FORMAT)


for sentence, words in _leftover(_LEGACY_RECONCILE, _reconcile_fragments()):
    REPORT.require(f"continuity reconcile coverage ({sentence[:40]}…)", not words,
                   "continuity_reconcile/system.j2 says what no fragment carries: "
                   f"{' '.join(words)!r}")
# Coverage that bites: with any one carried, bullet or reworded fragment gone
# from the tables, some sentence of the legacy prompt is left uncovered.
for fragment in _RECONCILE_BITES:
    REPORT.require(f"continuity reconcile coverage misses ({fragment[:40]}…)",
                   any(words for _, words in _leftover(_LEGACY_RECONCILE,
                                                       _reconcile_fragments(fragment))),
                   "coverage passes without it, so the tables do not hold it")

# Structured decisions (slice F, spec 7.4): `inference.structured_messages`
# against direct renders of `decide/`. Every branch the templates take: the
# rationale on and off; a predicate, a choice with and without `allow_none`, and
# a score; one item and two -- so a branch that moved cannot hide behind one
# that did not.
from grimoire import decisions as dec  # noqa: E402
from grimoire import inference  # noqa: E402

_DECIDE_ITEMS = [
    dec.Item("Mara closes the door behind her.\nThe lamp gutters.", (
        dec.Predicate("over", "Is the scene over?"),
        dec.Choice("next", "Who speaks next?",
                   (dec.Option("seraphine", "Seraphine, at the window"),
                    dec.Option("mara", "Mara", aliases=("Mara Vale",))),
                   allow_none=True),
        dec.Score("tone", "How tense is the room?", ("calm", "uneasy", "tense")))),
    dec.Item("Winifred counts the stalls of Saltmarch.", (
        dec.Choice("verdict", "Has Winifred's voice drifted?",
                   (dec.Option("drift", "Drifted from the anchor"),
                    dec.Option("in_voice", "In voice"))),)),
]
for _explain in ("", "Say in one sentence what settled it."):
    for _n in (1, 2):
        _items = _DECIDE_ITEMS[:_n]
        _label = f"{_n} item(s), explain={bool(_explain)}"
        check_messages(f"decide ({_label})",
                       [{"role": "system",
                         "content": render("decide/system.j2",
                                           schema=dec.schema(_items, explain=bool(_explain)),
                                           explain=bool(_explain))},
                        {"role": "user",
                         "content": render("decide/user.j2", items=_items, explain=_explain)}],
                       inference.structured_messages(_items, explain=_explain))

# ------------------------------------------------------------- store fixture

from grimoire.store import appearances as ap  # noqa: E402
from grimoire.store import (  # noqa: E402
    audit,
    calendars,
    campaigns,
    characters,
    checks,
    commitments,
    config,
    entities,
    events,
    groupstate,
    modules,
    pcs,
    playstate,
    plot,
    response_presets,
    response_targets,
    scenes,
    sheets,
    steering,
    styles,
    worlds,
)
from grimoire.store import authors_notes as anstore  # noqa: E402
from grimoire.store import dossiers as dstore
from grimoire.store import facts as fstore
from grimoire.store import taglines as tstore
from grimoire.store import voice_anchors as vastore
from grimoire.store import voice_drift as vdstore
from grimoire.store import weather as wstore
from grimoire.store.continuity import doc as continuity_doc  # noqa: E402
from grimoire.store.continuity import effective  # noqa: E402
from grimoire.store.tracker import fields as tfields  # noqa: E402
from grimoire.store.tracker import records as trecords  # noqa: E402
from grimoire.store.tracker import settings as tsettings  # noqa: E402
from grimoire.store.tracker import view as tview  # noqa: E402
from grimoire.store.tracker import walk as twalk  # noqa: E402

# recap_depth=1 narrows the recap window to the newest absorbed scene, which is
# what leaves an older one outside it for archive retrieval (#127) to recall —
# the archive section is empty by construction while every record is in recap.
# speaker_turn_taking is on for exactly that reason too (#29) — it ships off,
# and the multi-NPC scenes below are what make its section non-empty.
config.write_config(system_prompt="Global GM rules: be vivid, be fair.", recap_depth="1",
                    speaker_turn_taking="on")

# a bound mechanics module (#162 Task 6): one sheet type, one check, one
# always-on rules doc -- so mechanics_rules/mechanics_sheets/mechanics_checks
# (and their three system.j2 sections) are all non-empty below.
mod_dir = Path(os.environ["GRIMOIRE_HOME"]) / "modules" / "keeper-arts"
(mod_dir / "rules").mkdir(parents=True)
(mod_dir / "module.md").write_text("---\nname: Keeper Arts\n---\n", encoding="utf-8")
(mod_dir / "sheets.json").write_text(json.dumps({
    "groups": {},
    "sheet_types": {
        "keeper": {"label": "Keeper", "kind": "characters", "groups": [],
                  "fields": [{"key": "grit", "label": "Grit", "type": "resource", "max": 5}]},
    },
}), encoding="utf-8")
(mod_dir / "checks.json").write_text(json.dumps({
    "steady-hand": {"label": "Steady Hand", "requires": [], "roll": "1d20"},
}), encoding="utf-8")
(mod_dir / "rules" / "core.md").write_text("---\nalways: true\n---\nKeep every roll honest.\n",
                                           encoding="utf-8")

# A world profile (#38), so the World overview section renders here.
wid = worlds.create_world("W", genre="Coastal gothic", tone="Wry dread",
                          themes=["salt", "debt"], description="A drowned coast of toll-roads.")
cid = campaigns.create_campaign("Run", wid, module="keeper-arts")
croot = campaigns.campaign_root(cid)
sid = scenes.create_scene(cid, "S1")

ncard = characters.blank_card("Seraphine Vale")
ncard["data"].update({"description": "Tall, sharp-eyed smuggler.", "personality": "Wry and wary.",
                      "scenario": "Runs the night dock.", "system_prompt": "Voice Seraphine with dry wit.",
                      "mes_example": "<START>\n**Seraphine Vale:** Try me.",
                      "post_history_instructions": "Keep replies under four paragraphs."})
sera, _ = characters.create_character(croot, "Seraphine Vale", "default", ncard)
ap.appear(cid, sid, "characters", sera, "default", "npc")
playstate.write_state(croot, sera, playstate.compose_body(
    "Wounded and hiding.", "The ledger is real.\nIt names the harbormaster.", "The Guild watches her."))
sheets.write(cid, "characters", sera, "keeper", {"grit": {"current": 2, "max": 5}},
             expected=None)

persona = pcs.blank_persona("Hero")
persona.update({"pronouns": "she/her", "summary": "A debt-ridden courier.",
                "description": "Quick, kind, unlucky.", "birthdate": "2000-07-05",
                # the profile fields (#65), so pc_block renders its last lines
                "goals": "Clear the debt.", "player_notes": "Never speak for her."})
pid, _ = pcs.create_pc(croot, "Hero", [], persona=persona)
ap.appear(cid, sid, "pcs", pid, "default", "player")

kcard = characters.blank_card("Doc Kessler")
kcard["data"].update({"description": "A weary back-alley medic."})
kessler, _ = characters.create_character(croot, "Doc Kessler", "default", kcard)
sid0 = scenes.create_scene(cid, "S0")
ap.appear(cid, sid0, "characters", kessler, "default", "npc")
dstore.write(croot, kessler, "Kessler patches up smugglers and quietly owes the Guild.")

# An anchored, PRESENT NPC carrying an unresolved voice-drift flag, so
# post_history.j2 renders the voice corrective in every scene comparison below
# rather than the empty string. BOTH halves are required: context._assemble
# honours a flag only while the character still has an anchor, so dropping the
# anchor here would silently reduce the comparison to "" == "".
vastore.write(croot, sera, "Clipped. Never uses contractions. Answers a question with a question.")
vdstore.write(croot, sera, "She used contractions and hedged twice; Seraphine never softens a refusal.")

mcard = characters.blank_card("Mora")
mora, _ = characters.create_character(croot, "Mora", "default", mcard)
tstore.write(croot, mora, "A fortune-teller who deals in secrets.")

dock = entities.create_entity(croot, "locations", "Night Dock",
                              "Fog-slick piers stacked with contraband.", keys="dock, pier")
entities.create_entity(croot, "locations", "Bonded Warehouse",
                       "Crates to the ceiling, one door.", keys="warehouse")
entities.create_entity(croot, "lore", "Salt Pact", "The Pact taxes every crossing.")
entities.create_entity(croot, "lore", "The Ledger", "The ledger lists a decade of bribes.",
                       keys="ledger")
entities.create_entity(croot, "lore", "Sera secret", "She was exiled from the Guild.",
                       owners=f"characters:{sera}")
# Secrecy (#49): a `secret` entry so the World info section renders its labelled
# block here, and a `gm-only` one that must never appear in any render at all.
entities.create_entity(croot, "lore", "The Ledger's true owner",
                       "The Guildmaster keeps the ledger himself.",
                       keys="ledger", secrecy="secret")
entities.create_entity(croot, "lore", "Referee note", "The warehouse burns on day nine.",
                       secrecy="gm-only")
circle = entities.create_entity(croot, "groups", "Salt Circle",
                                "A quiet cabal moving contraband.")  # keyless -> always-on
groupstate.write_state(croot, circle, "## Goals\nCorner the ledger before the Guild does.")
# A secret group WITH state, so the Group state section renders both of its
# blocks here: state is the half of a group worth gating (FIELDS ends in
# `secrets`), and it must not depend on World info to carry the heading.
cabal = entities.create_entity(croot, "groups", "The Quiet Office",
                               "Nobody admits it exists.", secrecy="secret")
groupstate.write_state(croot, cabal, "## Secrets\nThey hold the Guildmaster's debt.")
scenes.set_location(cid, sid, dock)
sid = scenes.set_datetime(cid, sid, "2026-07-05")["id"]  # first date set renames the scene

scenes.append_message(cid, sid, "user", "Where is the ledger?")
scenes.append_message(cid, sid, "assistant", "Seraphine glances toward the warehouse.",
                      speaker="Seraphine Vale")
scenes.append_message(cid, sid, "assistant", "Fog rolls in off the water.")
scenes.append_message(cid, sid, "user", "I follow her.", speaker="Hero", post_id="c" * 32)

# A campaign author's note (play controls V), so every composition below carries
# one: at depth 1 it sits before the closing player post, in the offscreen
# scene's single assistant line it sits at the start, and an opener places it
# after its prompt. No macros, so the mirror needs no expansion.
AUTHORS_NOTE = {"text": "Keep the storm audible in every scene.", "depth": 1, "every": 1}
anstore.set_campaign(cid, AUTHORS_NOTE)

# One tracker record, on the closing player post, so the Scene state section
# renders here: two characters, both with a value anyone present can see and
# Seraphine with a private one -- which the narrator's render labels, and which
# is the branch of the template a leak would hide in.
# The campaign opts in itself rather than leaning on the shipped default, which
# the test suite switches off.
campaigns.set_campaign_tracker(cid, "on")
_tracker_key = twalk.ordered_keys(cid, sid)[-1][1]
trecords.save(cid, scenes.ensure_identity(cid, sid), _tracker_key, {
    f"characters:{sera}": {"present": True, "fields": {
        "pose": {"value": "leaning on a piling", "aware": "present"},
        "intent": {"value": "lose the tail before the warehouse", "aware": []}}},
    f"pcs:{pid}": {"present": True, "fields": {
        "holding": {"value": "a shuttered lantern", "aware": "present"},
        "condition": {"value": ["soaked", "winded"], "aware": "present"}}},
}, changed=[], fields_digest="fixture", model="fixture")

relationships.set_feeling(cid, f"characters:{sera}", f"pcs:{pid}", 2, 3, 4, "suspects a tail")
relationships.set_bond(cid, f"characters:{sera}", f"pcs:{pid}", "reluctant allies")
plot.set_movement(cid, "find-the-ledger", "Find the ledger", "open", "Hero learned it exists.", sid)
commitments.set_movement(cid, "the-deadline", "Midnight deadline", "threat", "open",
                         "midnight", "Hero was given until midnight.", sid)
fstore.record(cid, "The warehouse belongs to the Salt Circle.", "the third night", sid)
chronicle.absorb(cid, {"id": sid0, "one_line": "Hero met Kessler.",
                       "summary": "Hero met Doc Kessler in his clinic and traded a favor for gossip.",
                       "keywords": ["clinic"], "cast": [f"characters/{kessler}"],
                       "location": "", "date": "2026-07-01"})
# Older than the recap window and keyed on a word the live scene says out loud,
# so this one — and only this one — comes back through the archive section.
# `chronicle.recent` orders by id and scene ids carry an ordinal prefix, so the
# id has to sort below the real scenes' (`001--…`, `002--…`) to be "older".
chronicle.absorb(cid, {"id": "000--2026-06-20--harbor-run",
                       "one_line": "The warehouse changed hands.",
                       "summary": "The bonded warehouse changed hands after a bad night on the pier.",
                       "keywords": ["warehouse"], "cast": [], "location": "", "date": "2026-06-20"})


def _secrecy_of(meta: dict) -> str:
    """`entities.normalize_secrecy`, re-implemented rather than imported: this
    harness is the independent copy of the data contract, so it spells the rule
    out the way the templates/README.md description does."""
    level = (meta.get("secrecy") or "public").strip().lower()
    return level if level in ("public", "secret", "gm-only") else "public"



def _cast_blocks(cid, npc_cards, npc_ids):
    """Mirror of context.assemble's cast_blocks. Kept here rather than imported
    for the reason this whole script exists: an independent reconstruction is
    what makes "the builder and the template agree" mean something."""
    def _str(card, key):
        v = card.get(key)
        return v.strip() if isinstance(v, str) else ""

    shown = context.cast.voice_safe_names([_str(d, "name") for d in npc_cards])
    out = []
    for name, card, char_id in zip(shown, npc_cards, npc_ids, strict=True):
        parts = [_str(card, "description"), _str(card, "personality"), _str(card, "scenario")]
        out.append({
            "name": name,
            "description": "\n".join(p for p in parts if p),
            "anchor": voice_anchors.effective(overlay.voice_anchor_record(cid, char_id)["text"]),
            "example": voice_anchors.truncate(_str(card, "mes_example"),
                                              voice_anchors.VOICE_EXAMPLE_CAP),
        })
    return out

def gather(scene_id: str, pcless: bool, wi_seed: str = "", full_recap: int = 0) -> dict:
    """Mirror context._assemble's data gathering through public store reads —
    this is the data contract documented in templates/README.md."""
    cfg = config.read_config()
    scene = scenes.read_scene(cid, scene_id)
    cast = ap.scene_cast(cid, scene_id)

    npc_cards, npc_ids, states = [], [], []
    for a in cast:
        if a["role"] != "npc":
            continue
        vid = ap.locked_version(cid, a["kind"], a["id"])
        npc_cards.append(characters.read_card(croot, a["id"], vid)["data"])
        npc_ids.append(a["id"])
        st = playstate.read_state(croot, a["id"])
        if st and (st["current_state"] or st["knows"] or st["suspects"]):
            name = characters.read_character(croot, a["id"])["meta"].get("name", a["id"])
            states.append({"name": name, **st})

    players, player_names = [], []
    for a in cast:
        if a["role"] != "player":
            continue
        vid = ap.locked_version(cid, a["kind"], a["id"])
        if a["kind"] == "pcs":
            p = pcs.read_persona(croot, a["id"], vid)
            players.append({"kind": "pcs", **p})
            player_names.append(p.get("name", a["id"]))
        else:
            d = characters.read_card(croot, a["id"], vid)["data"]
            players.append({"kind": "characters", **d})
            player_names.append(d.get("name", a["id"]))

    refs, ref_names = [], []
    if pcless:
        for a in ap.roster(cid):
            if a["role"] != "player":
                continue
            if a["kind"] == "pcs":
                p = pcs.read_persona(croot, a["id"], a["version"])
                refs.append({"kind": "pcs", **p})
                ref_names.append(p.get("name", a["id"]))
            else:
                d = characters.read_card(croot, a["id"], a["version"])["data"]
                refs.append({"kind": "characters", **d})
                ref_names.append(d.get("name", a["id"]))

    tokens = [f"{a['kind']}:{a['id']}" for a in cast]
    relationship_lines = relationships.render_present(
        cid, tokens, lambda t: relationships.actor_name(cid, t))

    depth = full_recap or max(int(cfg.get("recap_depth", "5")), 0)
    records = chronicle.recent(cid, depth) if depth > 0 else []
    story_entries = [((r.get("summary") or r.get("one_line") or "") if full_recap
                      else (r.get("one_line") or r.get("summary") or "")).strip()
                     for r in records]

    history_ids = scenes.get_location_history(cid, scene_id)
    current_loc = history_ids[-1] if history_ids else None
    current_setting = ""
    current_setting_secret = False
    if current_loc:
        loc_meta = entities.read_entity(croot, "locations", current_loc)
        loc_secrecy = _secrecy_of(loc_meta["meta"])
        if loc_secrecy != "gm-only":
            current_setting = loc_meta["body"].strip()
            current_setting_secret = loc_secrecy == "secret"

    scan = max(int(cfg.get("context_scan_depth", "8")), 0)
    recent_text = "\n".join(m["content"] for m in scene["messages"][-scan:]) if scan else ""
    if wi_seed:
        recent_text = (recent_text + "\n" + wi_seed).strip()

    # Archive retrieval (#127): absorbed scenes OUTSIDE the recap window whose
    # keywords the scan window says, newest id first, capped at archive_depth.
    # The recap window and the scene being played are excluded so no scene can
    # arrive twice.
    seen = {r.get("id", "") for r in records} | {scene_id}
    archive_entries = []
    for r in chronicle.read_chronicle(cid).values():
        rid = r.get("id", "")
        keys = [str(k).strip() for k in (r.get("keywords") or []) if str(k).strip()]
        text = (r.get("summary") or r.get("one_line") or "").strip()
        if not rid or rid in seen or not keys or not text:
            continue
        if any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys):
            archive_entries.append({"id": rid, "date": (r.get("date") or "").strip(), "text": text})
    archive_entries.sort(key=lambda h: h["id"], reverse=True)
    archive_entries = archive_entries[:max(int(cfg.get("archive_depth", "3")), 0)]

    entries = []
    for kind in ("lore", "locations", "items", "groups", "creatures"):
        for meta in entities.list_entities(croot, kind):
            if kind == "locations" and meta["id"] == current_loc:
                continue
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys:
                continue
            entries.append({"body": e["body"].strip(), "keys": keys, "owners": owners,
                            "secrecy": _secrecy_of(e["meta"]), "kind": kind, "id": meta["id"],
                            "name": e["meta"].get("name", meta["id"])})
    present = set(tokens) | ({f"locations:{current_loc}"} if current_loc else set())
    activated = context.activate(entries, recent_text, frozenset(present))
    world_info_bodies = [e["body"] for e in activated if e["secrecy"] != "secret"]
    secret_world_info_bodies = [e["body"] for e in activated if e["secrecy"] == "secret"]
    recalled_lore_bodies = []   # recall is off in this harness, as by default
    secret_recalled_lore_bodies = []
    group_states, secret_group_states = [], []
    for e in activated:
        if e["kind"] != "groups":
            continue
        st = groupstate.read_state(croot, e["id"])
        if st and any(st[k] for k in groupstate.FIELDS):
            bucket = secret_group_states if e["secrecy"] == "secret" else group_states
            bucket.append({"name": e["name"], **st})

    mid = modules.resolve(cid)
    mechanics_rules, mechanics_sheets, mechanics_checks = [], [], []
    if mid is not None:
        pack = modules.load_pack(mid)
        sheets_def = pack["sheets"] if isinstance(pack["sheets"], dict) else {}
        actors = [(a["kind"], a["id"], a["name"]) for a in cast]
        if current_loc:
            try:
                loc = entities.read_entity(croot, "locations", current_loc)
                actors.append(("locations", current_loc, loc["meta"].get("name", current_loc)))
            except entities.EntityNotFound:
                pass
        present_types = set()
        for kind, eid, label in actors:
            sh = sheets.read(cid, kind, eid)
            if sh is None:
                continue
            type_id = sh["sheet_type"]
            st = sheets_def.get("sheet_types", {}).get(type_id) if isinstance(type_id, str) else None
            type_label = (st.get("label", type_id) if isinstance(st, dict)
                          else (type_id if isinstance(type_id, str) else ""))
            if sh["errors"]:
                mechanics_sheets.append({"ref": f"{kind}:{eid}", "label": label,
                                         "type_label": type_label, "lines": ["(sheet invalid)"]})
                continue
            if isinstance(type_id, str):
                present_types.add(type_id)
            defaults = sheets.default_fields(sheets_def, type_id) if isinstance(type_id, str) else {}
            merged = {**defaults, **sh["fields"]}
            line_entries = []
            for f in (modules.assembled_fields(sheets_def, type_id) if isinstance(type_id, str) else []):
                key = f.get("key")
                if not isinstance(key, str) or not key:
                    continue
                v = merged.get(key)
                if f.get("type") == "resource" and isinstance(v, dict):
                    line_entries.append(f"{key} {v.get('current')}/{v.get('max')}")
                else:
                    line_entries.append(f"{key} {v}")
            for name, value in sh["derived"].items():
                line_entries.append(f"{name} {value}")
            lines = [" · ".join(line_entries[i:i + 4]) for i in range(0, len(line_entries), 4)]
            mechanics_sheets.append({"ref": f"{kind}:{eid}", "label": label,
                                     "type_label": type_label, "lines": lines})

        always_docs, type_docs, key_docs = [], [], []
        for doc in pack["rules"]:
            if doc["always"]:
                always_docs.append(doc)
            elif set(doc["sheet_types"]) & present_types:
                type_docs.append(doc)
            elif doc["keys"] and any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE)
                                     for k in doc["keys"]):
                key_docs.append(doc)
        for doc in always_docs + type_docs + key_docs[:6]:
            rule = modules.read_rule(mid, doc["id"])
            if rule is not None:
                mechanics_rules.append(rule["body"].strip())
        mechanics_checks = checks.available_checks(cid, scene_id)

    today = None
    time_history = scenes.get_time_history(cid, scene_id)
    if time_history:
        facts = calendars.today_facts(calendars.read_calendar(croot), time_history[-1])
        # Mirrors context.world_state._today_data, scheduled events included:
        # that function merges the campaign's own dated events into the same two
        # fields, so a mirror that skipped them would render a different Today
        # block for any campaign that has one.
        scheduled = events.day_facts(cid, croot, time_history[-1])
        today = {"friendly": facts["friendly"], "weekday": facts["weekday"],
                 "secondary_friendly": facts["secondary_friendly"],
                 "holidays_today": facts["holidays_today"],
                 "events_today": scheduled["events_today"],
                 "upcoming": events.sooner(facts["upcoming"], scheduled["upcoming"]),
                 "cast": context.cast_datetime_facts(cid, scene_id, time_history[-1])}

    # Mirrors context._weather_data. Derived rather than fixtured: a constant
    # would disagree with the real assembly for every scene that has no
    # location or no moment, which is most of the scenarios below.
    weather_now = None
    location_history = scenes.get_location_history(cid, scene_id)
    if location_history and time_history:
        got = wstore.current_weather(cid, location_history[-1], time_history[-1])
        if got:
            weather_now = {k: got[k] for k in ("condition", "temperature", "wind")}
            weather_now["notes"] = got.get("notes") or []

    present_chars = {a["id"] for a in cast if a["kind"] == "characters"}
    roster = ap.roster(cid)
    roster_ids = {a["id"] for a in roster if a["kind"] == "characters"}
    offscene_active = []
    for a in roster:
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in present_chars:
            continue
        body = dstore.read(croot, a["id"])
        if body:
            name = characters.read_character(croot, a["id"])["meta"]["name"]
            offscene_active.append({"name": name, "dossier": body})
    offscene_known = []
    for char_id in characters.character_refs(croot):
        if char_id in roster_ids or char_id in present_chars:
            continue
        tag = tstore.read(croot, char_id)
        if not tag:
            continue
        ch = characters.read_character(croot, char_id)
        offscene_known.append({"id": char_id, "name": ch["meta"]["name"], "tagline": tag,
                               "versions": [v["id"] for v in ch["versions"]]})
    # context.cast._scope_known cuts this tier to `offscene_known_limit` and
    # ranks the survivors by relevance, which is a judgement about a scene and
    # not a data read this mirror can honestly reproduce. The fixture store
    # below stays well under the ceiling so the two agree without it -- asserted
    # rather than assumed, because a fixture that grew past the ceiling would
    # otherwise fail as an unexplained byte mismatch in the section join.
    # A raised error rather than an `assert`, which -O strips: this is a
    # precondition of the comparison below, not a check the harness collects.
    limit = int(cfg.get("offscene_known_limit", config.DEFAULT_OFFSCENE_KNOWN_LIMIT) or 0)
    if limit and len(offscene_known) > limit:
        raise SystemExit(
            f"verify fixture has {len(offscene_known)} tier-3 characters, over the "
            f"offscene_known_limit of {limit}; this mirror does not implement the cut, "
            f"so shrink the fixture or teach gather() the relevance rule")

    # Mirrors context._assemble's scene state: the record at the transcript's
    # tail, seen by the narrator (this harness composes with no assigned actor),
    # over the whole scene cast; nothing at all with the tracker off.
    tracker_lines = []
    if tsettings.enabled(cid):
        _key, snapshot = twalk.current(cid, scene_id)
        if snapshot:
            tracker_lines = tview.lines_for(snapshot, tfields.effective(cid, scene_id), None,
                                            twalk.roster(cid, scene_id))

    campaign_meta = campaigns.read_campaign(cid)["meta"]
    # Mirrors context._assemble: style keeps its legacy cascade; the new
    # continuation target resolves separately from it.
    budget = response_presets.resolve(scene_meta=scene["meta"],
                                      campaign_meta=campaign_meta, config=cfg)
    targets = response_targets.resolve(scene_meta=scene["meta"],
                                       campaign_meta=campaign_meta, config=cfg)
    try:
        resolved_style = styles.read_style(budget["style_id"]) if budget["style_id"] else None
    except styles.StyleNotFound:
        resolved_style = None
    world = worlds.read_world(campaign_meta["world"])
    return {"global_system_prompt": cfg.get("system_prompt", ""),
            # Mirrors context._assemble: the campaign world's profile (#38).
            "world_overview": {"genre": world["meta"]["genre"], "tone": world["meta"]["tone"],
                               "themes": world["meta"]["themes"],
                               "description": world["body"].strip()},
            "budget": targets["continuation"],
            "prose_style_name": resolved_style["meta"]["name"] if resolved_style else "",
            "prose_style_body": resolved_style["body"].strip() if resolved_style else "",
            "npc_cards": npc_cards,
            # Mirror of context.assemble's cast_blocks -- the one structure
            # character_descriptions and the three voice sections all read.
            "cast_blocks": _cast_blocks(cid, npc_cards, npc_ids),
            "named_npc_count": sum(
                1 for b in _cast_blocks(cid, npc_cards, npc_ids) if b["name"]),
            "states": states,
            "tracker_lines": tracker_lines, "tracker_narrator": True,
            # Mirrors context._assemble: the global switch, on by default. Only
            # an NPC-assigned response_actor.j2 reads it; no case here is one.
            "perception_rider": True,
            # Mirrors context._assemble: derived from the present NPCs' card
            # names and the raw transcript, and None while the toggle is off.
            "speaker": (context.speaker.nominate(
                [d.get("name", "") for d in npc_cards if d.get("name")],
                [dict(m) for m in scene["messages"]])
                if config.speaker_turn_taking() else None),
            "relationship_lines": relationship_lines, "players": players,
            "ref_names": ref_names, "refs": refs, "story_entries": story_entries,
            "archive_entries": archive_entries,
            "plot_lines": effective.render_threads(cid, with_id=False),
            "commitment_lines": effective.render_commitments(cid, with_id=False), "today": today,
            "weather": weather_now,
            "current_setting": current_setting,
            "current_setting_secret": current_setting_secret,
            "world_info_bodies": world_info_bodies,
            "secret_world_info_bodies": secret_world_info_bodies,
            "recalled_lore_bodies": recalled_lore_bodies,
            "secret_recalled_lore_bodies": secret_recalled_lore_bodies,
            "group_states": group_states,
            "secret_group_states": secret_group_states,
            "offscene_active": offscene_active, "offscene_known": offscene_known,
            "player_names": player_names, "pcless": pcless,
            "story_full": bool(full_recap), "opener": False,
            "mechanics_rules": mechanics_rules, "mechanics_sheets": mechanics_sheets,
            "mechanics_checks": mechanics_checks}


#: `Section.heading`, spelled out independently for the same reason the order
#: below is: a block that was split into several sections for the token
#: breakdown's sake shares one heading, and the render path opens each
#: contiguous run of them with it (context.assemble is the other copy).
#: Template path -> the heading template it shares.
_SHARED_HEADINGS = {
    "scene/sections/off_scene_cast_active.j2": "scene/off_scene_cast_heading.j2",
    "scene/sections/off_scene_cast_known.j2": "scene/off_scene_cast_heading.j2",
}


def rendered_system(data: dict, opener: bool = False) -> str:
    """Mirror of context.assemble._render_sections + scene/system.j2: render
    each section, drop the empty ones, join with blank lines.

    The order is spelled out here rather than read off `context.SECTIONS`,
    which is the point — the prompt's section order is now a single list in
    code, and this is the independent copy that makes reordering it a
    deliberate two-sided change instead of a silent one. `_SHARED_HEADINGS` is
    the same deal for `Section.heading`.
    """
    data = {"model_guidance": "", **data}
    names = []
    if opener:
        names.append("scene/opener_instruction/"
                     + ("adapt_" if data.get("opener_adapt") else "")
                     + ("offscreen" if data["pcless"] else "standard") + ".j2")
    names += ["scene/sections/global_system_prompt.j2",
              "scene/sections/prose_style.j2",
              "scene/sections/natural_prose.j2",
              "scene/sections/model_guidance.j2",
              "scene/sections/card_system_prompts.j2",
              "scene/sections/world_overview.j2",
              "scene/sections/character_descriptions.j2",
              # Catalog order: the voice block sits between who a character IS
              # and what is true of them right now (context.assemble.SECTIONS).
              "scene/sections/voice_policy.j2",
              "scene/sections/voice_anchors.j2",
              "scene/sections/voice_examples.j2",
              "scene/sections/character_state.j2"]
    if not opener:                    # Section(except_opener=True)
        names.append("scene/sections/tracker_state.j2")
    names += ["scene/sections/active_speaker.j2",
              "scene/sections/relationships.j2",
              "scene/sections/player_personas.j2"]
    if data["pcless"]:
        names += ["scene/sections/offscreen_scene.j2", "scene/sections/absent_players.j2"]
    names += [
              "scene/sections/story_so_far/" + ("full" if data["story_full"] else "compact") + ".j2",
              "scene/sections/archive.j2",
              "scene/sections/plot_threads.j2",
              "scene/sections/commitments.j2",
              "scene/sections/today.j2",
              "scene/sections/weather.j2",
              "scene/sections/current_setting.j2",
              "scene/sections/world_info.j2",
              "scene/sections/recalled_lore.j2",
              "scene/sections/group_state.j2",
              "scene/sections/mechanics_rules.j2",
              "scene/sections/mechanics_sheets.j2",
              "scene/sections/off_scene_cast_active.j2",
              "scene/sections/off_scene_cast_known.j2",
              "scene/sections/mechanics_response_format.j2",
              "scene/sections/response_format.j2"]
    names.append("scene/sections/response_budget.j2")
    sections: list[str] = []
    last_head = None
    for n in names:
        body = render(n, **data).strip()
        if not body:                      # an empty section does not split a run
            continue
        head = _SHARED_HEADINGS.get(n)
        if head and head != last_head:    # one copy per CONTIGUOUS run
            body = render(head, **data).strip() + "\n\n" + body
        sections.append(body)
        last_head = head
    if data.get("response_actor") and not opener:
        sections.append(render("scene/response_actor.j2", **data).strip())
    return render("scene/system.j2", sections=sections).strip()


NARRATOR_INSTRUCTION = "Set the scene as narrator. Do not write any NPC or PC actions or dialogue."
ADAPT_NARRATOR_INSTRUCTION = ("Set the scene as narrator, from the greeting's setting and situation. "
                              "Do not write any NPC or PC actions or dialogue.")


def _note_split(messages: list[dict], depth: int) -> int:
    """Where an author's note at `depth` goes: before the `depth`-th most
    recent post (0 = after the last), clamped to the start, then snapped back
    to the start of a run of player posts, or the start. Written out here
    rather than imported -- this script is a mirror of the real path."""
    if depth <= 0:
        return len(messages)
    for i in range(max(len(messages) - depth, 0), -1, -1):
        if (i < len(messages) and messages[i]["role"] == "user"
                and (i == 0 or messages[i - 1]["role"] != "user")):
            return i
    return 0


def _authors_note() -> dict:
    return {"role": "system", "content": render("scene/authors_note.j2", level="campaign",
                                                name="", text=AUTHORS_NOTE["text"])}


def _history_with_note(messages: list[dict]) -> list[dict]:
    """The projected history, the fixture's author's note at its split point."""
    out: list[dict] = []
    split = _note_split(messages, AUTHORS_NOTE["depth"])
    for n, m in enumerate(messages):
        if n == split:
            out.append(_authors_note())
        line = render("scene/history_line.j2", m=m)
        if out and out[-1]["role"] == m["role"] and out[-1]["role"] != "system":
            out[-1]["content"] += "\n\n" + line
        else:
            out.append({"role": m["role"], "content": line})
    if split == len(messages):
        out.append(_authors_note())
    return out


def rendered_messages(scene_id: str, data: dict, note: str | None = None,
                      opener_prompt: str | None = None) -> list[dict]:
    out = []
    system = rendered_system(data, opener=opener_prompt is not None)
    if system:
        out.append({"role": "system", "content": system})
    if opener_prompt is None:
        out += _history_with_note(scenes.read_scene(cid, scene_id)["messages"])
    if note is not None:
        out.append({"role": "user", "content": note})
    if opener_prompt is not None:
        out.append({"role": "user", "content": opener_prompt})
        # An opener has no history: the note follows its prompt.
        out.append(_authors_note())
    # Mirrors context._assemble exactly — this script's whole point is proving
    # the templates render what the real path renders, so the corrective is
    # measured from the same scene rather than injected from a fixture.
    # This mirror has no assigned response actor, so the actor-scoped drift
    # corrective is absent just as it is in context._assemble.
    correction = ""
    # Same mirroring for the voice corrective: read off the scene's own cast and
    # flag files, not injected, so a change to either half shows up here.
    voice_notes = [{"name": characters.read_character(croot, a["id"])["meta"]["name"],
                    "note": vdstore.read(croot, a["id"])}
                   for a in ap.scene_cast(cid, scene_id)
                   if a["kind"] == "characters" and a["role"] == "npc"
                   and vdstore.read(croot, a["id"]) and vastore.read(croot, a["id"])]
    voice = render("scene/voice_correction.j2", voice_notes=voice_notes) if voice_notes else ""
    post = render("scene/post_history.j2", npc_cards=data["npc_cards"],
                  voice_correction=voice, length_correction=correction)
    if post:
        out.append({"role": "system", "content": post})
    if opener_prompt is not None:
        out.append({"role": "system", "content": ADAPT_NARRATOR_INSTRUCTION
                    if data.get("opener_adapt") else NARRATOR_INSTRUCTION})
    return out


# --------------------------------------------------- context builder checks

data = gather(sid, pcless=False)
# A byte-for-byte check over an empty section proves nothing, and the archive
# section is empty unless the fixture keeps a keyed record outside the recap
# window — so say so here rather than let the check quietly go vacuous.
assert data["archive_entries"], "fixture no longer exercises the archive section"
check_messages("chat", context.build_messages(cid, sid), rendered_messages(sid, data))
profile_data = {**data, "model_guidance": render("scene/model_guidance/glm-5.3.j2", **data)}
for model_id in ("glm-5.3", "z-ai/glm-5.3"):
    check_messages("chat model profile " + model_id,
                   context.build_messages(cid, sid, model=model_id),
                   rendered_messages(sid, profile_data))
check_messages("chat unknown model", context.build_messages(cid, sid, model="vendor/unknown"),
               rendered_messages(sid, data))
# The fixture's present NPC is both anchored and flagged, so the voice corrective
# really is inside the post-history the comparison above covers. Asserted rather
# than assumed: without an anchor the corrective renders "", and comparing "" to
# "" passes while proving nothing about scene/voice_correction.j2.
assert any("drifted out of voice" in m["content"] for m in context.build_messages(cid, sid)), \
    "the voice corrective is missing from the assembled prompt -- check the fixture (#59)"
# Same reasoning for the Scene state section: a byte-for-byte check over an
# empty section proves nothing, and its private-value branch is the one a leak
# would hide in.
assert any("# Scene state" in m["content"] and "(private: never state or imply in narration)"
           in m["content"] for m in context.build_messages(cid, sid)), \
    "the Scene state section is missing from the assembled prompt -- check the tracker fixture"
note = render("scene/director_note.j2")
check_messages("director", context.build_director_messages(cid, sid, note),
               rendered_messages(sid, data, note=note))

opener_prompt = "A storm rolls in while they reach the warehouse ledger."
odata = {**gather(sid, pcless=False, wi_seed=opener_prompt, full_recap=context.OPENER_RECAP_DEPTH),
         "opener": True, "budget": response_targets.resolve()["opening"],
         "response_actor": {"ref": "grimoire", "name": "Grimoire"}}
check_messages("opener", context.build_opener_messages(cid, sid, opener_prompt),
               rendered_messages(sid, odata, opener_prompt=opener_prompt))
# The adapted greeting (#91) is the same opener with the greeting's body as its
# prompt and the instruction's `adapt_` variant: nothing else may differ.
greeting_body = "The warehouse door stands open; the ledger waits on the desk."
adata = {**gather(sid, pcless=False, wi_seed=greeting_body, full_recap=context.OPENER_RECAP_DEPTH),
         "opener": True, "opener_adapt": True, "budget": response_targets.resolve()["opening"],
         "response_actor": {"ref": "grimoire", "name": "Grimoire"}}
check_messages("adapted opener",
               context.compose_opener(cid, sid, greeting_body, describe=False, adapt=True)[0],
               rendered_messages(sid, adata, opener_prompt=greeting_body))

sid_off = scenes.create_scene(cid, "Offscreen", pcless=True)
ap.appear(cid, sid_off, "characters", sera, "default", "npc")
ap.appear(cid, sid_off, "characters", kessler, "default", "npc")
scenes.append_message(cid, sid_off, "assistant", "Kessler locks the clinic door.",
                      speaker="Doc Kessler")
off_data = gather(sid_off, pcless=True)
check_messages("offscreen chat", context.build_messages(cid, sid_off),
               rendered_messages(sid_off, off_data))
off_odata = {**gather(sid_off, pcless=True, wi_seed="The pact at the pier.",
                      full_recap=context.OPENER_RECAP_DEPTH), "opener": True,
             "budget": response_targets.resolve()["opening"],
             "response_actor": {"ref": "grimoire", "name": "Grimoire"}}
check_messages("offscreen opener",
               context.build_opener_messages(cid, sid_off, "The pact at the pier."),
               rendered_messages(sid_off, off_odata, opener_prompt="The pact at the pier."))
check_messages("offscreen adapted opener",
               context.compose_opener(cid, sid_off, "The pact at the pier.", describe=False,
                                      adapt=True)[0],
               rendered_messages(sid_off, {**off_odata, "opener_adapt": True},
                                 opener_prompt="The pact at the pier."))

# ------------------------------------------------ store-level simple prompts

snap = suggest.build_snapshot(cid)
exp = suggest.build_prompt(snap, None)
store_view = suggest.driver_view(snap, suggest.NO_CONTROLS)
check("suggestions system (store)", exp[0]["content"],
      render("scene_suggestions/system.j2", s=snap, offscreen=False,
             greeting_candidates=None, direction="", drivers=True, view=store_view))
check("suggestions user (store)", exp[1]["content"],
      render("scene_suggestions/user.j2", s=snap, offscreen=False,
             greeting_candidates=None, direction="", drivers=True, view=store_view))

# The intent prompt renders the legacy snapshot with `drivers` off (spec §15).
TYPED = "the morning after, back at the marsh house"
iexp = suggest.build_intent_prompt(cid, TYPED)
isnap = suggest.build_snapshot(cid, drivers=False)
check("intent system (store)", iexp[0]["content"],
      render("scene_intent/system.j2", s=isnap, offscreen=False,
             greeting_candidates=None, direction="", drivers=False, view=None, typed=TYPED))
check("intent user (store)", iexp[1]["content"],
      render("scene_intent/user.j2", s=isnap, offscreen=False,
             greeting_candidates=None, direction="", drivers=False, view=None, typed=TYPED))

ioff_exp = suggest.build_intent_prompt(cid, TYPED, offscreen=True)
ioff_snap = suggest.build_snapshot(cid, offscreen=True, drivers=False)
check("intent user (store, offscreen)", ioff_exp[1]["content"],
      render("scene_intent/user.j2", s=ioff_snap, offscreen=True,
             greeting_candidates=None, direction="", drivers=False, view=None, typed=TYPED))

scene_msgs = scenes.read_scene(cid, sid)["messages"]
tr = render("snippets/transcript.j2", messages=scene_msgs)
check("transcript (store)", chronicle.transcript_text(scene_msgs), tr)
facts = chronicle.scene_facts(cid, sid)
st_snap = absorb.state_snapshot(cid, sid)
rel_snap = absorb.relationships_snapshot(cid, sid)
plot_snap = absorb.plot_snapshot(cid)
grp_snap = absorb.group_snapshot(cid)
cmt_snap = absorb.commitment_snapshot(cid)
fct_snap = absorb.fact_snapshot(cid)
steering.record(cid, sid, "Seraphine was told about the tail at the Night Dock")
strg_snap = absorb.steering_snapshot(cid, sid)
exp = absorb.build_prompt(tr, facts, st_snap, rel_snap, plot_snap, grp_snap, cmt_snap, fct_snap,
                          strg_snap)
check("absorb user (store)", exp[1]["content"],
      render("absorb/user.j2", facts=facts, state_snapshot=st_snap, rel_snapshot=rel_snap,
             plot_snapshot=plot_snap, group_snapshot=grp_snap,
             commitment_snapshot=cmt_snap, fact_snapshot=fct_snap,
             steering_snapshot=strg_snap, tracked_snapshot=[], transcript=tr))
for name, line in st_snap.items():
    st = playstate.read_state(croot, sera)
    check(f"state snapshot line (store, {name})", line,
          render("snippets/state_snapshot_line.j2", st=st))

blocks, _excluded = audit.sheet_blocks(cid, sid)
roll_log = audit.roll_lines(cid, sid)
audit_exp = audit.build_prompt(tr, blocks, roll_log)
check("audit system (store)", audit_exp[0]["content"], render("audit/system.j2"))
check("audit user (store)", audit_exp[1]["content"],
      render("audit/user.j2", sheet_blocks=blocks, roll_lines=roll_log, transcript=tr))

threads = plot.open_threads(cid)
check("plot lines (context form)", "\n".join(plot.render_open(cid, with_id=False)),
      "\n".join(render("snippets/plot_thread_line/context.j2", t=t) for t in threads))
check("plot lines (absorb form)", "\n".join(plot.render_open(cid, with_id=True)),
      "\n".join(render("snippets/plot_thread_line/absorb.j2", t=t) for t in threads))

owed = commitments.open_commitments(cid)
check("commitment lines (context form)", "\n".join(commitments.render_open(cid, with_id=False)),
      "\n".join(render("snippets/commitment_line/context.j2", c=c) for c in owed))
check("commitment lines (absorb form)", "\n".join(commitments.render_open(cid, with_id=True)),
      "\n".join(render("snippets/commitment_line/absorb.j2", c=c) for c in owed))

# The identity law (capstone spec §7.1) on the full fixture: with no alias, the
# effective renders the prompts now read ARE the physical ones.
for w in (False, True):
    form = "absorb" if w else "context"
    check(f"plot lines (effective == physical, no aliases, {form})",
          "\n".join(effective.render_threads(cid, w)), "\n".join(plot.render_open(cid, w)))
    check(f"commitment lines (effective == physical, no aliases, {form})",
          "\n".join(effective.render_commitments(cid, w)),
          "\n".join(commitments.render_open(cid, w)))

standing = fstore.active(cid)
check("fact lines", "\n".join(fstore.render_active(cid)),
      "\n".join(render("snippets/fact_line.j2", f=f) for f in standing))

# A merged campaign, created after every other check so nothing else sees it.
# The expectations are written out by hand rather than rendered over
# `effective.threads` -- that is what the helper does, so the comparison could
# not fail. The canonical's beat is the earlier scene and the SOURCE's the later
# one, so the merged latest beat must come from the source; the canonical's
# status and title are its own, so a source's winning fails too.
cid_merged = campaigns.create_campaign("Saltmarch", wid)
plot.set_movement(cid_merged, "winifred-s-chart", "Winifred's chart", "advanced",
                  "Winifred inks the coastline", "001--gate")
plot.set_movement(cid_merged, "mara-s-map", "Mara's map", "open",
                  "The map names the Saltmarch causeway", "002--causeway")
commitments.set_movement(cid_merged, "winifred-s-promise", "Winifred's promise", "promise",
                         "open", None, "Winifred swore it at dawn", "001--gate")
commitments.set_movement(cid_merged, "mara-s-oath", "Mara's oath", "promise",
                         "open", None, "Mara swore it at the gate", "002--causeway")
for src, to in (("thread:mara-s-map", "thread:winifred-s-chart"),
                ("commitment:mara-s-oath", "commitment:winifred-s-promise")):
    continuity_doc.put_alias(cid_merged, src,
                             {"to": to, "created": "", "source": "manual", "note": ""})
check("plot lines (effective, merged, absorb)",
      "winifred-s-chart: Winifred's chart (advanced) — The map names the Saltmarch causeway",
      "\n".join(effective.render_threads(cid_merged, True)))
check("plot lines (effective, merged, context)",
      "Winifred's chart (advanced): The map names the Saltmarch causeway",
      "\n".join(effective.render_threads(cid_merged, False)))
check("commitment lines (effective, merged, absorb)",
      "winifred-s-promise: Winifred's promise (promise, open) — Mara swore it at the gate",
      "\n".join(effective.render_commitments(cid_merged, True)))
check("commitment lines (effective, merged, context)",
      "Winifred's promise (promise, open): Mara swore it at the gate",
      "\n".join(effective.render_commitments(cid_merged, False)))
check("merged source hidden (threads)", "False",
      str(any("mara-s-map" in line for line in effective.render_threads(cid_merged, True))))
check("merged source hidden (commitments)", "False",
      str(any("mara-s-oath" in line for line in effective.render_commitments(cid_merged, True))))
check("merged ids are canonical", "winifred-s-chart",
      ",".join(t["id"] for t in effective.threads(cid_merged)))

# ---------------------------------------------------------------------------

print(REPORT.verdict())
if REPORT.failures:
    sys.exit(1)
