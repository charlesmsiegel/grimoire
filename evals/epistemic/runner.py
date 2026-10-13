"""Run the offline leakage suite: plant each case in a fresh store, compose
every checked reader's prompt with the view built for it, and grade."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager

from grimoire.store import campaigns, config, overlay, worlds
from grimoire.store.context import assemble
from grimoire.store.context import history as history_section

from . import cases, graders, synth

NAMES = ("Mara", "Winifred", "Ilse", "Seraphine")


@contextmanager
def _home():
    previous = os.environ.get("GRIMOIRE_HOME")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["GRIMOIRE_HOME"] = tmp
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("GRIMOIRE_HOME", None)
            else:
                os.environ["GRIMOIRE_HOME"] = previous


def _cast() -> cases.Cast:
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    config.write_config(history_recall_depth="8", history_recall_budget="6000",
                        recap_depth="0")
    ids = {name: overlay.create_character(cid, name) for name in NAMES}
    # Built after the cast, so every source scene a recipe plays is older.
    return cases.Cast(synth.Stage(cid, "Now"), f"characters:{ids['Mara'][0]}",
                      f"characters:{ids['Winifred'][0]}", ids)


def _seat(cast: cases.Cast) -> None:
    """Everyone on stage now: the NPCs, and Seraphine as the player."""
    for name in NAMES:
        aid, vid = cast.ids[name]
        synth.enter(cast.now, aid, vid, role="player" if name == "Seraphine" else "npc")
    cast.now.say("Seraphine looks around the room.")


def run_case(case: cases.Case) -> list[dict]:
    with _home():
        cast = _cast()
        items, checks = case.build(cast)
        _seat(cast)
        records = []
        for check in checks:
            view = synth.for_compose(cast.now.cid, cast.now.sid, check.reader, items,
                                     ceiling=6000)
            messages, detail = assemble.compose_turn(cast.now.cid, cast.now.sid,
                                                     actor_ref=check.reader, history_view=view)
            sections = {s["id"]: s["text"] for s in (detail or {}).get("sections", [])
                        if s["id"] in history_section.FRAMES}
            records.append({
                "case": case.id, "label": case.label, "reader": check.reader or "narrator",
                "leaks": graders.leaks(messages, check.forbidden),
                "missing": graders.missing(messages, check.allowed),
                "misplaced": graders.misplaced(sections, check.sections),
            })
        return records


def run_all() -> list[dict]:
    return [r for case in cases.CASES for r in run_case(case)]


def passed(record: dict) -> bool:
    return not (record["leaks"] or record["missing"] or record["misplaced"])
