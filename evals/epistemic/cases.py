"""Spec 11 §8.1's offline cases, as store recipes with needles.

Each recipe plays its source scenes into a fresh campaign and returns the
evidence 09 would hand over and the checks: per reader (an NPC ref, or None
for the narrator), the needles its prompt must hold, the needles that must
appear in no message of it, and the section an allowed needle must sit in.
Needles are invented tokens, so a match is never a coincidence. Case 3 is
offline-visible by design, and only a live reply can show the perception
contract holding.

Cases 7, 15 and 15b are the Decision stage's (11-S6): offline the part stays
withheld, as every unknown part does, and a check's `gate` names the needle
whose part the stage asks about with the answers its gold allows -- which
`decision.py` grades live as the Decision gate. Cases 8 and 10 carry gates
too: a summary nobody saw (a negative) and a legacy scene (a positive).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from grimoire.store import overlay, responses, scenes

from . import synth


@dataclass(frozen=True)
class Check:
    reader: str | None
    allowed: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    #: An allowed needle's section: it must be in that one and no other.
    sections: Mapping[str, str] = field(default_factory=dict)
    #: The live arm (11-S5): Seraphine's line to this NPC, in the story,
    #: inviting what it was given. Only a check with one is asked; there is
    #: no default, so no case label or title can reach a question.
    ask: str = ""
    #: Needles this NPC's prompt may hold but its reply must not use (case
    #: 3's unmarked whisper: visible offline, unheard in the fiction).
    live_forbidden: tuple[str, ...] = ()
    #: Each `live_forbidden` token's secret as a plain sentence, for the live
    #: arm's judge to recognise a paraphrase a token check cannot see (11-S6).
    facts: Mapping[str, str] = field(default_factory=dict)
    #: The Decision gate (11-S6): the needle whose part is asked about...
    gate: str = ""
    #: ...and the `access_of` answers its gold allows.
    gold: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cast:
    """Who is on stage in the scene being played now."""
    now: synth.Stage
    mara: str
    win: str
    ids: Mapping[str, tuple[str, str]]


@dataclass(frozen=True)
class Case:
    id: str
    label: str
    build: Callable[[Cast], tuple[tuple, tuple[Check, ...]]]


def _src(cast: Cast, title: str) -> synth.Stage:
    return synth.Stage(cast.now.cid, title)


def _here(st: synth.Stage, cast: Cast, *names: str) -> None:
    for name in names:
        aid, vid = cast.ids[name]
        synth.enter(st, aid, vid)


# --------------------------------------------------------------------- recipes


def _not_present(cast):
    st = _src(cast, "The Quay")
    _here(st, cast, "Winifred")
    st.say("Winifred learns the REVEALNEEDLE1 at the quay.")
    _here(st, cast, "Mara")
    st.say("Mara arrives as the AFTERNEEDLE1 tide turns.")
    return (st.item(),), (
        Check(cast.mara, allowed=("AFTERNEEDLE1",), forbidden=("REVEALNEEDLE1",),
              sections={"AFTERNEEDLE1": "history_known"},
              ask='Seraphine turns to Mara. "What do you remember of the tide at the quay?"'),
        Check(cast.win, allowed=("REVEALNEEDLE1", "AFTERNEEDLE1")),
    )


#: What the gate's answers mean: a grant, or anything that withholds.
PERMISSIVE = ("known", "suspected", "experienced")
WITHHOLDING = ("narrator_only", "unknown")

#: Case 3's whisper, as the sentence the judge reads beside its token.
WHISPER_FACT = "The chapel's silver chalice is hidden beneath the altar stone."


def _whisper(cast, override: bool, n: int):
    st = _src(cast, "The Chapel")
    _here(st, cast, "Mara", "Winifred")
    st.say(f"The bells ring for the OPENNEEDLE{n} vigil.")
    st.reply(cast.win, "Winifred", f"Winifred whispers the WHISPERNEEDLE{n} to the priest: "
                                   "the chapel's silver chalice is hidden beneath the "
                                   "altar stone.")
    whisper = st.index_of(f"WHISPERNEEDLE{n}")
    if override:
        synth.grant(st, cast.mara, "Mara", "unaware", keys=(synth.key(st, whisper),))
    # The whisper reaches Mara's prompt only without the override (R3), and a
    # whisper to the priest is nothing she could hear: live, she must not
    # repeat it either way.
    ask = 'Seraphine leans toward Mara. "What did you make of the vigil at the chapel?"'
    mara = (Check(cast.mara, allowed=(f"OPENNEEDLE{n}",), forbidden=(f"WHISPERNEEDLE{n}",),
                  ask=ask)
            if override else Check(cast.mara, allowed=(f"OPENNEEDLE{n}", f"WHISPERNEEDLE{n}"),
                                   ask=ask, live_forbidden=(f"WHISPERNEEDLE{n}",),
                                   facts={f"WHISPERNEEDLE{n}": WHISPER_FACT}))
    return (st.item(),), (mara, Check(cast.win, allowed=(f"WHISPERNEEDLE{n}",)))


def _knows(cast):
    st = _src(cast, "The Archive")
    st.say("The clerk files the KNOWNNEEDLE4 under seal.")
    synth.grant(st, cast.mara, "Mara", "knows", keys=(synth.key(st, 0),))
    return (st.item(),), (
        Check(cast.mara, allowed=("KNOWNNEEDLE4", "You know this:"),
              sections={"KNOWNNEEDLE4": "history_known"},
              ask='Seraphine asks Mara, "Do you know what the clerk keeps under seal?"'),
        Check(cast.win, forbidden=("KNOWNNEEDLE4",)),
    )


def _suspects(cast):
    st = _src(cast, "The Cellar")
    st.say("Someone moves the SUSPECTNEEDLE5 in the dark.")
    synth.grant(st, cast.mara, "Mara", "suspects", keys=(synth.key(st, 0),))
    return (st.item(),), (
        Check(cast.mara, allowed=("SUSPECTNEEDLE5", "Suspected, unconfirmed:"),
              sections={"SUSPECTNEEDLE5": "history_suspected"}),
        Check(cast.win, forbidden=("SUSPECTNEEDLE5",)),
    )


def _withheld(cast):
    st = _src(cast, "The Vault")
    st.say("Seraphine seals the WITHHELDNEEDLE6 in the vault.")
    synth.grant(st, cast.mara, "Mara", "unaware")
    return (st.item(),), (
        Check(None, allowed=("WITHHELDNEEDLE6",),
              sections={"WITHHELDNEEDLE6": "history_narrator_only"}),
        Check(cast.mara, forbidden=("WITHHELDNEEDLE6", "kept from the cast")),
        Check(cast.win, forbidden=("WITHHELDNEEDLE6", "kept from the cast")),
    )


def _whole_attendee(cast):
    st = _src(cast, "The Gallery")
    _here(st, cast, "Mara")
    st.say("Mara watches the SEENNEEDLE8 auction close.")
    summary = "Unseen by anyone, Seraphine swaps the SECRETNEEDLE8 for a fake."
    st.absorb(summary)
    return (st.item(summary=summary, title="The Gallery"),), (
        Check(cast.mara, allowed=("SEENNEEDLE8",),
              forbidden=("SECRETNEEDLE8", "The Gallery"),
              ask='Seraphine asks Mara, "How did the auction end, as you saw it?"'),
        Check(None, allowed=("SECRETNEEDLE8", "Known to: Mara")),
        # The Decision gate's summary negative: Mara stood there, so the
        # stage may ask about the summary, and nobody saw the swap.
        Check(cast.mara, forbidden=("SECRETNEEDLE8",), gate="SECRETNEEDLE8",
              gold=WITHHOLDING),
    )


def _partial(cast):
    st = _src(cast, "The Market")
    aid, _vid = cast.ids["Mara"]
    _here(st, cast, "Mara")
    st.say("Mara buys the BEFORENEEDLE9 at the first stall.")
    synth.leave(st, aid)
    st.say("After Mara leaves, the AFTERNEEDLE9 changes hands.")
    return (st.item(),), (
        Check(cast.mara, allowed=("BEFORENEEDLE9",), forbidden=("AFTERNEEDLE9",),
              ask='Seraphine asks Mara, "What happened at the market stalls?"'),
    )


def _legacy(cast):
    st = _src(cast, "The Old Road")
    aid, _vid = cast.ids["Mara"]
    _here(st, cast, "Mara")
    st.say("Long ago, the LEGACYNEEDLE10 was buried by the road.")
    synth.forget_intervals(st.cid, aid, st.sid)
    return (st.item(),), (
        Check(cast.mara, forbidden=("LEGACYNEEDLE10",), gate="LEGACYNEEDLE10",
              gold=PERMISSIVE),
        Check(None, allowed=("LEGACYNEEDLE10",), forbidden=("Known to:",),
              sections={"LEGACYNEEDLE10": "history_recall"}),
    )


def _pc_legacy(cast):
    st = _src(cast, "The Watchtower")
    paid, pvid = cast.ids["Seraphine"]
    synth.enter(st, paid, pvid, role="player")
    st.say("I light the PCNEEDLE11 beacon.", speaker="Seraphine")
    synth.forget_intervals(st.cid, paid, st.sid)
    return (st.item(),), (
        Check(None, allowed=("PCNEEDLE11", "Known to: Seraphine"),
              sections={"PCNEEDLE11": "history_recall"}),
    )


def _group(cast):
    cid = cast.now.cid
    st = _src(cast, "The Barracks")
    st.say("The watch is told the GROUPNEEDLE12 password.")
    ilse = cast.ids["Ilse"][0]
    gid = overlay.create_entity(cid, "groups", "The Saltmarch Watch",
                                fields={"leader": f"characters:{ilse}", "members": cast.win})
    synth.grant(st, f"groups:{gid}", "The Saltmarch Watch", "knows", keys=(synth.key(st, 0),))
    return (st.item(),), (
        Check(cast.win, allowed=("GROUPNEEDLE12",)),
        Check(f"characters:{ilse}", allowed=("GROUPNEEDLE12",)),
        Check(cast.mara, forbidden=("GROUPNEEDLE12",)),
    )


def _group_joined_later(cast):
    cid = cast.now.cid
    st = _src(cast, "The Guardhouse")
    st.say("The watch is told the JOINEDNEEDLE12 countersign.")
    gid = overlay.create_entity(cid, "groups", "The Harbour Watch",
                                fields={"leader": "", "members": cast.win})
    synth.grant(st, f"groups:{gid}", "The Harbour Watch", "knows", keys=(synth.key(st, 0),))
    overlay.update_entity(cid, "groups", gid,
                          fields={"leader": "", "members": f"{cast.win}, {cast.mara}"})
    return (st.item(),), (Check(cast.mara, allowed=("JOINEDNEEDLE12",)),)


def _group_recreated(cast):
    cid = cast.now.cid
    st = _src(cast, "The Gatehouse")
    st.say("The guard is told the RECREATEDNEEDLE12 signal.")
    gid = overlay.create_entity(cid, "groups", "The Gate Guard",
                                fields={"leader": "", "members": cast.win})
    synth.grant(st, f"groups:{gid}", "The Gate Guard", "knows", keys=(synth.key(st, 0),))
    overlay.delete_entity(cid, "groups", gid)
    overlay.create_entity(cid, "groups", "The New Gate Guard",
                          fields={"leader": "", "members": cast.win})
    return (st.item(),), (Check(cast.win, forbidden=("RECREATEDNEEDLE12",)),)


def _fingerprints(cast):
    cid = cast.now.cid
    swiped = _src(cast, "The Ferry")
    rid = swiped.reply(cast.win, "Winifred", "Winifred mentions the weather.")
    synth.grant(swiped, cast.mara, "Mara", "knows", keys=(synth.key(swiped, 0),))
    responses.save_variant(cid, swiped.sid, rid, "Winifred reveals the SWIPENEEDLE13.",
                           "complete")
    edited = _src(cast, "The Lighthouse")
    edited.say("The keeper trims the wick.")
    synth.grant(edited, cast.mara, "Mara", "knows")
    scenes.edit_message(cid, edited.sid, 0, "The keeper hides the EDITNEEDLE13.")
    kept = _src(cast, "The Mill")
    _here(kept, cast, "Mara")
    rid2 = kept.reply(cast.win, "Winifred", "Winifred names the miller.")
    i = kept.index_of("names the miller")
    synth.grant(kept, cast.mara, "Mara", "unaware", keys=(synth.key(kept, i),))
    responses.save_variant(cid, kept.sid, rid2, "Winifred names the UNAWARENEEDLE13.",
                           "complete")
    held = _src(cast, "The Weir")
    _here(held, cast, "Mara")
    held.say("The weir keeper counts the eels.")
    synth.grant(held, cast.mara, "Mara", "unaware", keys=(synth.key(held, 0),))
    scenes.edit_message(cid, held.sid, 0, "The weir keeper hides the HELDNEEDLE13.")
    # The control: the same grants, unchanged, do reach her -- so the
    # forbidden needles above are absent because the text moved, not because
    # a grant of this shape never matches.
    steady = _src(cast, "The Toll")
    steady.reply(cast.win, "Winifred", "Winifred reveals the STEADYNEEDLE13.")
    synth.grant(steady, cast.mara, "Mara", "knows", keys=(synth.key(steady, 0),))
    still = _src(cast, "The Beacon")
    still.say("The keeper lights the STILLNEEDLE13.")
    synth.grant(still, cast.mara, "Mara", "knows")
    return (swiped.item(), edited.item(), kept.item(), held.item(), steady.item(),
            still.item()), (
        Check(cast.mara, allowed=("STEADYNEEDLE13", "STILLNEEDLE13"),
              forbidden=("SWIPENEEDLE13", "EDITNEEDLE13", "UNAWARENEEDLE13", "HELDNEEDLE13")),
    )


def _cut(cast):
    cid = cast.now.cid
    st = _src(cast, "The Stair")
    aid, vid = cast.ids["Winifred"]
    first = st.reply(cast.mara, "Mara", "Mara speaks first.")
    st.say("A CUTNEEDLE14 secret, before Winifred arrives.")
    synth.enter(st, aid, vid)
    st.say("Winifred is told about the weather.")
    item = st.item()
    responses.delete(cid, st.sid, first)   # renumbers posts and remaps presence
    return (item,), (Check(cast.win, forbidden=("CUTNEEDLE14",)),)


def _told_later(cast):
    st = _src(cast, "The Customs House")
    _here(st, cast, "Winifred")
    st.say("Winifred watches the TOLDNEEDLE7 crates slip past the customs seal.")
    later = _src(cast, "The Tavern")
    _here(later, cast, "Mara", "Winifred")
    later.say("Over the TAVERNNEEDLE7 stew, Winifred tells Mara exactly which crates "
              "slipped past the customs seal.")
    return (st.item(), later.item()), (
        Check(cast.mara, allowed=("TAVERNNEEDLE7",), forbidden=("TOLDNEEDLE7",),
              gate="TOLDNEEDLE7", gold=PERMISSIVE),
        Check(cast.win, allowed=("TOLDNEEDLE7", "TAVERNNEEDLE7")),
    )


def _not_told(cast):
    st = _src(cast, "The Bonded Store")
    _here(st, cast, "Winifred")
    st.say("Winifred counts the KEPTNEEDLE15 casks hidden behind the bonded wall.")
    later = _src(cast, "The Ropewalk")
    _here(later, cast, "Mara", "Winifred")
    later.say("Mara and Winifred splice the MEETNEEDLE15 rope in silence; Winifred says "
              "nothing of the bonded store, and Mara never asks.")
    return (st.item(), later.item()), (
        Check(cast.mara, allowed=("MEETNEEDLE15",), forbidden=("KEPTNEEDLE15",),
              gate="KEPTNEEDLE15", gold=WITHHOLDING),
    )


def _gone_before(cast):
    st = _src(cast, "The Net Loft")
    aid, _vid = cast.ids["Mara"]
    _here(st, cast, "Mara")
    st.say("Long after Mara had gone home for the night, the GONENEEDLE15 crate was "
           "pried open in the empty loft.")
    synth.forget_intervals(st.cid, aid, st.sid)
    return (st.item(),), (
        Check(cast.mara, forbidden=("GONENEEDLE15",), gate="GONENEEDLE15",
              gold=WITHHOLDING),
    )


CASES = (
    Case("1", "not recorded present for a revelation (R4)", _not_present),
    Case("2", "a whisper the actor is marked unaware of (R1 over R3)",
         lambda c: _whisper(c, True, 2)),
    Case("3", "the same whisper unmarked: visible offline (R3)",
         lambda c: _whisper(c, False, 3)),
    Case("4", "a knows override", _knows),
    Case("5", "a suspects override", _suspects),
    Case("6", "a withheld item", _withheld),
    Case("7", "told in a later scene by someone who was there", _told_later),
    Case("8", "a whole-scene attendee and a secret summary (S1)", _whole_attendee),
    Case("9", "a partial attendee", _partial),
    Case("10", "a legacy scene with no intervals (R5)", _legacy),
    Case("11", "a PC-only legacy scene for the narrator (R2')", _pc_legacy),
    Case("12", "a group audience: member, leader, non-member", _group),
    Case("12b", "a group member added after the grant", _group_joined_later),
    Case("12c", "a group recreated under another name", _group_recreated),
    Case("13", "fingerprints: a swipe and an edit stop a grant, neither stops an unaware",
         _fingerprints),
    Case("14", "a cut between retrieval and classification", _cut),
    Case("15", "met a teller later, and was not told", _not_told),
    Case("15b", "a legacy cast member who had already gone", _gone_before),
)
