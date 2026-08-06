"""How `home/` was born — provenance for the frozen fixture, not a refresh tool.

Run this ONCE to mint a frozen store tree; the result is committed and then
never regenerated (README.md says why). It exists so a reviewer can see what the
tree is made of without reading forty markdown files, and so a *new* frozen
fixture can be minted later at a newer on-disk format without hand-authoring
one — the old one stays frozen beside it.

    PYTHONPATH=backend/src backend/.venv/bin/python \
        backend/tests/fixtures/frozen_campaign/build.py --out /tmp/frozen-home

Every timestamp comes from a fake clock (`Clock`) rather than the wall clock,
so the tree carries obviously-synthetic dates instead of the day someone
happened to build it. The clock is installed by replacing `store.paths`'
`datetime`, not `paths.now_iso`: a dozen modules bind `now_iso` by value at
import and would go on calling the real one.

Names are invented placeholders, reusing the ones the codebase already uses for
fixtures (Saltmarch, Seraphine, Mara, Winifred). Real campaign content never
enters this repository — see CLAUDE.md, "Privacy".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


class Clock:
    """A stand-in for `datetime` inside `store.paths`: every `now()` returns the
    next minute of a fixed synthetic day, so stamps are reproducible and
    strictly increasing (world and campaign listings sort on `updated`, and
    equal stamps would leave that order up to the filesystem)."""

    START = datetime(2026, 1, 1, 9, 0, 0, tzinfo=timezone.utc)

    def __init__(self) -> None:
        self.ticks = 0

    def now(self, tz=None):
        self.ticks += 1
        return self.START + timedelta(minutes=self.ticks)


WORLD_NAME = "Saltmarch"
CAMPAIGN_NAME = "The Drowned Ledger"
# A scene file named under the pre-migration real-date grammar, so the harness
# has something `migrations.migrate_scene_ids()` actually has to migrate. Every
# reference below points at this stem too, so the migration's `scene_refs`
# repoint sweep is exercised rather than just the rename.
LEGACY_SID = "2026-01-02-the-long-quay"


def _card(characters, name: str, **fields) -> dict:
    card = characters.blank_card(name)
    card["data"].update(fields)
    return card


def build(store) -> dict:
    """Populate the store rooted at GRIMOIRE_HOME: one world, one campaign."""
    wid, tag, keep = _world(store)
    cid, pid = _campaign(store, wid, tag)
    sid = _scenes(store, cid, pid, keep)
    return {"world": wid, "campaign": cid, "scene": sid,
            "legacy_scene": LEGACY_SID, "pc": pid}


def _world(store) -> tuple[str, str, str]:
    """Two characters (one with a second version), a location, keyed lore, two
    greetings joined by a plotmap edge, and a tag vocabulary."""
    worlds, characters = store.worlds, store.characters
    entities, greetings = store.entities, store.greetings

    wid = worlds.create_world(WORLD_NAME)
    wroot = worlds.world_root(wid)
    tag = store.tags.add_tag(wroot, "Coastal")

    characters.create_character(
        wroot, "Seraphine", "default",
        _card(characters, "Seraphine",
              description="The drowned keeper of the tide ledger.",
              personality="Patient, exacting, unhurried by weather or grief.",
              first_mes="The ledger is open. Say your name to it.",
              tags=["keeper"]))
    # A second version of the same character: cast members resolve per version,
    # so a one-version fixture would never exercise that path.
    characters.create_version(wroot, "seraphine", "veiled",
                              _card(characters, "Seraphine",
                                    description="The keeper, hooded, off the record."))
    characters.create_character(
        wroot, "Mara", "default",
        _card(characters, "Mara",
              description="A quay-side runner who owes the ledger."))

    keep = entities.create_entity(
        wroot, "locations", "Tidewatch Keep",
        "A stone keep the tide reaches twice a day.")
    entities.create_entity(
        wroot, "lore", "The Salt Pact",
        "Debts written in salt are owed to the sea, not the lender.",
        keys="pact, salt")

    gid = greetings.create_greeting(
        wroot, "At the ledger", "seraphine", "default",
        body='Seraphine does not look up. "Name and debt," she says.')
    gid2 = greetings.create_greeting(
        wroot, "After the tide", "seraphine", "default",
        body="The water has gone out. The ledger is wet through.",
        requires_tags=[tag])
    greetings.set_edges(wroot, gid, leads_to=[gid2])
    return wid, tag, keep


def _campaign(store, wid: str, tag: str) -> tuple[str, str]:
    """A PC, lore owned by that PC, and a bound mechanics module with one
    filled sheet."""
    cid = store.campaigns.create_campaign(CAMPAIGN_NAME, wid, module="d20-basic")
    croot = store.campaigns.campaign_root(cid)
    pid, _pv = store.pcs.create_pc(croot, "Winifred", [tag],
                                   persona={**store.pcs.blank_persona("Winifred"),
                                            "pronouns": "she/her",
                                            "summary": "A debtor with a good memory."})
    store.entities.create_entity(
        croot, "lore", "Winifred's debt",
        "She signed the ledger in salt and has never said for what.",
        owners=f"pcs:{pid}")
    store.sheets.write(cid, "characters", "seraphine", "warrior",
                       {"hp": {"current": 9, "max": 12}, "gear": ["tide ledger"],
                        "strength": 8, "dexterity": 11, "mind": 16},
                       expected=None)
    return cid, pid


def _scenes(store, cid: str, pid: str, keep: str) -> str:
    """Scene one under the current id grammar, scene two under the legacy one,
    and every campaign record that references either."""
    scenes, appearances = store.scenes, store.appearances
    chronicle, plot = store.chronicle, store.plot
    croot = store.campaigns.campaign_root(cid)

    sid = scenes.create_scene(cid, "The Tide Comes In")
    scenes.set_location(cid, sid, keep)
    appearances.appear(cid, sid, "characters", "seraphine", "default", "npc")
    appearances.appear(cid, sid, "pcs", pid, "default", "player")
    scenes.append_message(cid, sid, "user", "I put my hand on the ledger.")
    scenes.append_message(cid, sid, "assistant",
                          'Seraphine turns the page. "Salt first," she says.')
    chronicle.absorb(cid, {"id": sid, "one_line": "Winifred touched the ledger.",
                           "summary": "Winifred came to Tidewatch Keep and put a hand on "
                                      "the tide ledger; Seraphine asked for salt.",
                           "keywords": ["ledger", "salt"]})
    chronicle.append_timeline(cid, [{"date": "2026-01-01", "text": "The ledger was opened."}])
    plot.set_movement(cid, "the-debt", "The debt in salt", "open",
                      "Winifred's name is in the ledger and she will not say why.", sid)
    plot.set_movement(cid, "the-tide-table", "The tide table", "closed",
                      "The table was copied out and hung by the door.", sid)
    store.commitments.set_movement(cid, "salt-owed", "Bring salt to the keep", "promise",
                                   "open", "2026-01-05", "Seraphine asked for salt.", sid)
    store.relationships.set_feeling(cid, f"pcs:{pid}", "characters:seraphine",
                                    trust=1, affection=0, tension=2,
                                    note="Owes her a page.")
    store.relationships.set_bond(cid, f"pcs:{pid}", "characters:seraphine", "creditor", sid)
    store.dossiers.write(croot, "seraphine",
                         "Keeps the tide ledger. Speaks in debts. Has not left the keep.")

    # ---- scene two: legacy real-date grammar, written straight to disk ----
    _legacy_scene(store, cid)
    appearances.appear(cid, LEGACY_SID, "characters", "mara", "default", "npc")
    scenes.append_message(cid, LEGACY_SID, "user", "I walk the quay looking for Mara.")
    scenes.append_message(cid, LEGACY_SID, "assistant",
                          "Mara is counting crates and pretending not to see you.")
    chronicle.absorb(cid, {"id": LEGACY_SID, "one_line": "Winifred found Mara on the quay.",
                           "summary": "A short meeting on the Long Quay.",
                           "keywords": ["quay"]})
    plot.set_movement(cid, "the-debt", "", "open",
                      "Mara knows what the debt was for.", LEGACY_SID)
    return sid


def _legacy_scene(store, cid: str) -> None:
    """The pre-migration on-disk shape: a scene file named for its real-world
    date. Written by hand because no code path produces this grammar any more —
    which is exactly why one has to be frozen."""
    d = store.campaigns.campaign_root(cid) / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    meta = {"title": "The Long Quay", "model": "openai/gpt-4o-mini",
            "created": "2026-01-02T09:30:00Z", "updated": "2026-01-02T09:30:00Z",
            "time_history": "2026-01-02"}
    (d / f"{LEGACY_SID}.md").write_text(
        store.dump_frontmatter(meta, ""), encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Mint a frozen grimoire store tree.")
    ap.add_argument("--out", required=True, type=Path,
                    help="directory to write the store tree into (must not exist)")
    args = ap.parse_args(argv)

    if args.out.exists():
        print(f"error: {args.out} exists — refusing to overwrite", file=sys.stderr)
        return 2

    # Imported after GRIMOIRE_HOME is set, deliberately: this script exists to
    # write a store somewhere specific, and an import that ever resolved the
    # home at module scope would resolve the wrong one.
    os.environ["GRIMOIRE_HOME"] = str(args.out)
    from grimoire import store
    from grimoire.store import paths

    paths.datetime = Clock()          # every stamp from the synthetic clock
    store.ensure_home()
    ids = build(store)

    for lock in args.out.rglob("*.lock"):   # runtime debris, not fixture content
        lock.unlink()
    print(json.dumps({"ids": ids,
                      "files": sorted(str(p.relative_to(args.out))
                                      for p in args.out.rglob("*") if p.is_file())},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
