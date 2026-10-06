"""The inputs behind `fixtures/suggest_golden.json`, and the script that wrote it.

Capstone spec §15 and §15.1 promise two prompts unchanged while the scene
suggestions learn about story drivers: the intent prompt, whole, for every
snapshot; and the suggestion prompt's instruction section (the system message)
when no control is set. A promise like that is only kept where something fails
when it breaks, so the bytes were recorded here on the code as it stood BEFORE
the change, and `test_suggest_golden.py` holds every later change to them.

Two kinds of input:

- `SYSTEM_SNAPS`: hand-built snapshots for the system message, which reads
  only `now`, `notation` and the selectors (offscreen, greeting candidates,
  direction). Each carries today's keys AND the driver keys Slice E adds, as
  empty values, so the same dict renders on both sides of the change.
- `intent_campaigns()`: real campaigns, because the intent prompt renders the
  whole snapshot `build_snapshot` reads out of a store -- the Upcoming line's
  merge of a holiday and an event (`events.sooner`, ties to the holiday) is the
  part most at risk, and only a store exercises it. `today` puts a holiday and
  an event on the clock date itself, so the "Today:" and "Scheduled today:"
  branches are pinned too.

Run as `python -m tests.suggest_golden` from `backend/` to rewrite the file.
That is for a DELIBERATE change to one of these prompts, reviewed as such --
never to make the test pass.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from grimoire.store import (
    appearances,
    calendars,
    campaigns,
    characters,
    clock,
    commitments,
    events,
    overlay,
    pcs,
    plot,
    scenes,
    suggest,
    worlds,
)

GOLDEN = Path(__file__).parent / "fixtures" / "suggest_golden.json"

#: What the game master typed, for every intent variant.
TYPED = "the morning after, back at Saltmarch"

#: The keys Slice E adds to the suggestion snapshot, empty. A pre-change
#: template ignores them; a post-change one renders nothing for them.
_DRIVER_KEYS: dict = {
    "commitments": [], "timeline": [], "driver_index": [], "anchors": [],
    "links": [], "fixed": None, "near_days": 7, "sooner_ref": "",
}


def _snap(**over) -> dict:
    base = {
        "now": "", "friendly": "", "notation": {"example": "", "months": []},
        "holidays_today": [], "events_today": [], "upcoming": None, "birthdays": [],
        "story_so_far": [], "open_threads": [], "cast": [], "available_locations": [],
        **_DRIVER_KEYS,
    }
    return {**base, **over}


_DATED = _snap(
    now="2026-07-05", friendly="5 Mirtul 1492",
    notation={"example": "1492-Mirtul-05", "months": ["Thaw", "Mirtul", "Highsun"]},
    upcoming={"name": "Saltmarch Eve", "in_days": 4},
    open_threads=[{"id": "find-the-ledger", "title": "Mara's map", "status": "open",
                   "latest_beat": "The map turned up in Saltmarch.", "dormancy": 2}],
    cast=[{"token": "characters:mara", "name": "Mara", "tagline": "a cartographer",
           "status": "present", "role": "npc"}],
    available_locations=[{"id": "saltmarch", "name": "Saltmarch"}],
)

_GREETINGS = [
    {"id": "gate", "name": "At the gate", "excerpt": "Seraphine waits at the Saltmarch gate."},
    {"id": "map", "name": "Mara's map", "excerpt": "Mara unrolls the map on the table."},
    {"id": "oath", "name": "Mara's oath", "excerpt": "Winifred reminds Mara what she swore."},
]

_DIRECTION = "something quiet at sea"

#: label -> (snapshot, greeting_candidates, offscreen, direction)
SYSTEM_SNAPS: dict[str, tuple[dict, list | None, bool, str]] = {
    "empty": (_snap(), None, False, ""),
    "dated": (_DATED, None, False, ""),
    "dated+greetings": (_DATED, _GREETINGS, False, ""),
    "offscreen": (_DATED, None, True, ""),
    "direction": (_snap(), None, False, _DIRECTION),
    "direction+greetings": (_DATED, _GREETINGS, False, _DIRECTION),
}


def _campaign(wid: str, name: str, *, calendar: str = "gregorian",
              holidays: tuple[dict, ...] = (), now: str = "2026-05-10") -> str:
    """A campaign whose calendar observes exactly `holidays` (`region=""`
    switches the holiday library off), its clock at `now`."""
    cid = campaigns.create_campaign(name, wid, calendar=calendar)
    root = campaigns.campaign_root(cid)
    cfg = calendars.read_calendar(root)
    cfg["primary"] = {**cfg["primary"], "region": "", "custom_holidays": list(holidays)}
    calendars.write_calendar(root, cfg)
    clock.advance(cid, to=now)
    return cid


def intent_campaigns() -> dict[str, tuple[str, bool]]:
    """Build the intent campaigns under the caller's `GRIMOIRE_HOME`.

    Returns `label -> (cid, offscreen)`. The clock stands at 2026-05-10 on the
    Gregorian ones, so every "in N days" below is fixed."""
    wid = worlds.create_world("Realm")

    greg = _campaign(wid, "Saltmarch", holidays=(
        {"name": "Saltmarch Eve", "month": "05", "day": 15},))
    events.create(greg, "The coronation", "2026-05-13")
    plot.set_movement(greg, "mara-s-map", "Mara's map", "open",
                      "The map turned up in Saltmarch.", "001--saltmarch")
    commitments.set_movement(greg, "mara-s-oath", "Mara's oath", "promise", "open",
                             "2026-05-14", "Mara swore it at the gate.", "001--saltmarch")
    mara, _vid = overlay.create_character(greg, "Mara")
    characters.set_birthdate(campaigns.campaign_root(greg), mara, "1990-05-12")
    overlay.create_entity(greg, "locations", "Saltmarch")
    # A seated player character, so the offscreen variant differs: it drops
    # the PC from the cast, which is what makes it a separate pin.
    win, _ = pcs.create_pc(campaigns.campaign_root(greg), "Winifred", [],
                           persona=pcs.blank_persona("Winifred"))
    sid = scenes.create_scene(greg, "Saltmarch gate")
    appearances.appear(greg, sid, "pcs", win, "default", "player")

    tie = _campaign(wid, "Saltmarch tie", holidays=(
        {"name": "Saltmarch Eve", "month": "05", "day": 13},))
    events.create(tie, "The coronation", "2026-05-13")

    hebrew = _campaign(wid, "Realm hebrew", calendar="hebrew", now="5786-Kislev-24")

    # A holiday and an event ON the clock date, so the "Today:" and "Scheduled
    # today:" branches render -- nothing above lands on the date itself.
    today = _campaign(wid, "Saltmarch today", holidays=(
        {"name": "Saltmarch Eve", "month": "05", "day": 10},))
    events.create(today, "The coronation", "2026-05-10")
    events.create(today, "The debt", "2026-05-20")

    return {"gregorian": (greg, False), "gregorian-offscreen": (greg, True),
            "tie": (tie, False), "hebrew": (hebrew, False), "today": (today, False)}


def variants() -> dict[str, list[dict]]:
    """Every pinned prompt, keyed as the golden file keys it."""
    out: dict[str, list[dict]] = {}
    for label, (snap, cands, offscreen, direction) in SYSTEM_SNAPS.items():
        out[f"system/{label}"] = [suggest.build_prompt(snap, cands, offscreen, direction)[0]]
    for label, (cid, offscreen) in intent_campaigns().items():
        out[f"intent/{label}"] = suggest.build_intent_prompt(cid, TYPED, offscreen=offscreen)
    return out


def main() -> None:
    with tempfile.TemporaryDirectory() as home:
        os.environ["GRIMOIRE_HOME"] = home
        found = variants()
    GOLDEN.write_text(json.dumps(found, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"wrote {len(found)} variants to {GOLDEN}\n")


if __name__ == "__main__":
    main()
