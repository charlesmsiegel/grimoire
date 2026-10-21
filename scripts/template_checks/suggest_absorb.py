"""The storeless suggestion and absorb prompts, over hand-built snapshots, and
the snippets both share."""

from __future__ import annotations

from grimoire.store import absorb, chronicle, suggest
from grimoire.store.absorb import prompt as absorb_prompt

from . import REPO
from .harness import check, render
from .prompts import TRANSCRIPT

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
SUGGEST_CASES = (
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
     suggest.Controls(focus=("thread:saltmarch-44",))))

#: The scene tracker's final state as `routes.scenes._absorb_tracked` hands it
#: over (`tracker.view.lines_for` for the narrator, empty lines dropped).
TRACKED = [{"ref": "characters:seraphine-vale", "id": "characters/seraphine-vale",
            "name": "Seraphine Vale", "own": False, "values": [
               {"label": "Visible mood", "text": "fear", "private": False},
               {"label": "Concealed", "text": "the ledger", "private": True}]},
           {"ref": "pcs:hero", "id": "pcs/hero", "name": "Hero", "own": False, "values": [
               {"label": "Holding", "text": "a lantern", "private": False}]}]

#: (label, facts, then each snapshot `absorb.build_prompt` takes, by keyword).
ABSORB_CASES = (
    ("bare", {}, None, None, None, None, None, None, None, None),
    ("tracked", {}, None, None, None, None, None, None, None, TRACKED),
    ("full", {"location": "Night Dock", "date": "2026-07-05",
              "cast": ["characters/seraphine-vale", "pcs/hero"]},
     {"Seraphine Vale": "Wounded. Knows: The ledger is real."},
     "Seraphine Vale → Hero: trust 2, affection 3, tension 4 (suspects a tail)",
     "find-the-ledger: Find the ledger (open) — Hero learned it exists.",
     "- groups/salt-circle (Salt Circle): Goals: Expand.",
     ("the-deadline: Midnight deadline (threat, open), due midnight "
      "— Hero was given until midnight."),
     "f1: The warehouse belongs to the Salt Circle. (the third night)",
     "- Seraphine was told about the tail at the Night Dock", TRACKED))
_SNAPSHOTS = ("state_snapshot", "rel_snapshot", "plot_snapshot", "group_snapshot",
              "commitment_snapshot", "fact_snapshot", "steering_snapshot", "tracked_snapshot")
#: Each snapshot's head line in the absorb user message, where it has one.
_HEADS = {"group_snapshot": "Groups:", "commitment_snapshot": "Open commitments:",
          "fact_snapshot": "Standing facts:", "steering_snapshot": "Player steering notes"}

MSGS = [{"role": "user", "content": "hi"},
        {"role": "user", "speaker": "Hero", "content": "yo"},
        {"role": "assistant", "speaker": "Seraphine Vale", "content": "Try me."},
        {"role": "assistant", "content": "Fog rolls in."}]


def check_suggestions() -> None:
    for label, snap, cands, off, direction, controls in SUGGEST_CASES:
        exp = suggest.build_prompt(snap, cands, offscreen=off, direction=direction,
                                   controls=controls)
        view = suggest.driver_view(snap, controls)
        check(f"suggestions system ({label})", exp[0]["content"],
              render("scene_suggestions/system.j2", s=snap, offscreen=off,
                     greeting_candidates=cands, direction=direction, drivers=True, view=view,
                     default_sections=[]))
        check(f"suggestions user ({label})", exp[1]["content"],
              render("scene_suggestions/user.j2", s=snap, offscreen=off,
                     greeting_candidates=cands, direction=direction, drivers=True, view=view))


def _absorb_system_asserts(label: str, system: str, steering) -> None:
    assert '"why_new"' in system, \
        f"absorb system ({label}) no longer asks new-record rows for \"why_new\""
    if steering:
        assert "Treat them as pointers, not as story" in system, \
            f"absorb system ({label}) missing the steering contract paragraph"
    else:
        assert "Player steering notes" not in system, \
            f"absorb system ({label}) carries the steering paragraph with no notes"


def _absorb_user_asserts(label: str, user: str, facts, snaps: dict) -> None:
    if snaps["tracked_snapshot"]:
        assert ("Final tracked state (as the scene ended; private values marked):\n"
                "- Seraphine Vale (characters/seraphine-vale): Visible mood: fear; "
                "Concealed: the ledger (private)\n"
                "- Hero (pcs/hero): Holding: a lantern") in user, \
            f"absorb user ({label}) missing the Final tracked state block"
    else:
        assert "Final tracked state" not in user, \
            f"absorb user ({label}) renders a tracked-state block with no tracked state"
        assert user == absorb.build_prompt(
            TRANSCRIPT, facts, **{**snaps, "tracked_snapshot": []})[1]["content"], \
            f"absorb user ({label}) changes with an empty tracked state"
    for key, head in _HEADS.items():
        if snaps[key]:
            assert head in user, f"absorb user ({label}) missing {head} head line"


def check_absorb() -> None:
    for label, facts, *values in ABSORB_CASES:
        snaps = dict(zip(_SNAPSHOTS, values, strict=True))
        exp = absorb.build_prompt(TRANSCRIPT, facts, **snaps)
        check(f"absorb system ({label})", exp[0]["content"],
              render("absorb/system.j2", steering=bool(snaps["steering_snapshot"]),
                     schema=absorb_prompt.extraction_schema()))
        _absorb_system_asserts(label, exp[0]["content"], snaps["steering_snapshot"])
        check(f"absorb user ({label})", exp[1]["content"],
              render("absorb/user.j2", facts=facts,
                     **{**snaps, "tracked_snapshot": snaps["tracked_snapshot"] or []},
                     transcript=TRANSCRIPT))
        _absorb_user_asserts(label, exp[1]["content"], facts, snaps)


def check_snippets() -> None:
    check("transcript", chronicle.transcript_text(MSGS),
          render("snippets/transcript.j2", messages=MSGS))
    for st in ({"current_state": "Wounded.", "knows": "The ledger is real.",
                "suspects": "Bob lied."},
               {"current_state": "", "knows": "The ledger is real.", "suspects": ""},
               {"current_state": "Hiding.", "knows": "", "suspects": ""}):
        check(f"state snapshot line ({st['current_state'] or 'no-cs'})",
              absorb._snapshot_line(st), render("snippets/state_snapshot_line.j2", st=st))


def check_routes_render() -> None:
    routes_src = "\n".join(p.read_text(encoding="utf-8")
                           for p in sorted((REPO / "backend/src/grimoire/routes").rglob("*.py")))
    assert 'prompts.render("scene/director_note.j2")' in routes_src, \
        "the routes package no longer renders the director-note template"
    assert 'prompts.render("scene/regenerate_guidance.j2"' in routes_src, \
        "the routes package no longer renders the regenerate-guidance template"
    assert 'prompts.render(\n        "scene/roll_result.j2"' in routes_src \
        or 'prompts.render("scene/roll_result.j2"' in routes_src, \
        "the routes package no longer renders the roll-result continuation template (#162)"
    assert 'prompts.render("scene/roll_declined.j2")' in routes_src, \
        "the routes package no longer renders the roll-declined continuation template (#162)"


CHECKS = (check_suggestions, check_absorb, check_snippets, check_routes_render)
