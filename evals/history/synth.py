"""Seeded real-store recipes; gold is written down at planting time.

Theory: each case is a controlled archive with independently planted evidence
and forbidden history. More filler changes archive age, never the gold definition.
The corpus measures retrieval mechanics on synthetic vectors, not model quality.
"""
from dataclasses import dataclass, replace
import random
import re

from grimoire.store import campaigns, characters, chronicle, commitments, config, entities
from grimoire.store import pins, plot, relationship_history, scenes, worlds
from grimoire.store.appearances import transitions
from grimoire.store.scenes import paths as scene_paths
from grimoire.store.scenes import identity


@dataclass(frozen=True)
class Gold:
    sid: str
    identity: str | None
    start: int
    end: int

    @property
    def key(self):
        return self.identity or "sid:" + self.sid


@dataclass(frozen=True)
class Planted:
    cid: str
    sid: str
    gold: tuple[Gold, ...]
    forbidden: frozenset[str]
    fact: str
    shape: int
    eligible: int


def _scene(cid, text, summary, *, cast=()):
    sid = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, sid, "user", text)
    for aid, vid in cast:
        transitions.appear(cid, sid, "characters", aid, vid, "npc", narrate=False)
    chronicle.absorb(cid, {"id": sid, "one_line": summary, "summary": summary,
                          "keywords": [], "cast": [], "location": "", "date": ""})
    scenes.mark_absorbed(cid, sid, summary, summary)
    return sid


def build(case, *, long=False, seed=9):
    rng = random.Random(seed+case.shape)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch", wid)
    root = campaigns.campaign_root(cid)
    cast = tuple(characters.create_character(root, n, card={"data": {"name": n}})
                 for n in ("Mara", "Seraphine"))
    config.write_config(history_recall_depth="5", history_recall_budget="1200",
                        history_recall_threshold="0.6", context_budget="0",
                        recap_depth="2", archive_depth="0", scan_depth="4")
    fact = "amberseal"
    detail = "Mara entrusted Seraphine with amberseal."
    if case.shape not in (3, 4):
        detail += " The silver ledger identifies amberseal."
    gold, forbidden = [], set()
    count = (1100 if case.shape in (1, 3) else 80) if long else 16
    if case.shape in (9, 10):
        count = 1 if case.shape == 9 else 2
    # Group affiliation admits all case3 scenes; its old gold lies outside
    # the 64-slot pool but inside the total 1000-document widening window.
    if case.shape == 3:
        entities.create_entity(root, "groups", "Realm", fields={
            "leader": f"characters:{cast[0][0]}", "members": f"characters:{cast[1][0]}"})
    target = 150 if long and case.shape == 3 else 0
    old = None
    for n in range(count):
        is_gold = n == target or case.shape == 10
        shared = is_gold or case.shape == 3
        text = detail if is_gold else rng.choice(("The room is quiet.", "The ferryman waits."))
        summary = text
        if case.shape == 2 and n >= count-6:
            text = summary = "The harbour keeper copies a silver ledger."
            shared = False
        sid = _scene(cid, text, summary, cast=cast if shared else ())
        if is_gold:
            old = sid
            start = 0
            if case.shape == 5:
                for _ in range(24 if not long else 240):
                    scenes.append_message(cid, sid, "assistant", "The ferryman waits. "*12)
                start = 25 if not long else 241
                scenes.append_message(cid, sid, "user", detail)
                scenes.append_message(cid, sid, "assistant", "The room is quiet.")
                scenes.mark_absorbed(cid, sid, summary, summary)
            if case.shape == 10:
                path = scene_paths._scene_path(cid, sid)
                path.write_text(re.sub(r"(?m)^identity:.*\n", "", path.read_text(encoding="utf-8")),
                                encoding="utf-8")
            gold.append(Gold(sid, identity.scene_identity(cid, sid), start, start))
    assert old is not None
    # Scene creation may widen ordinal ids at 1000; planted identities follow
    # the store's rename contract without asking retrieval to define gold.
    gold = [replace(g, sid=identity.find_by_identity(cid, g.identity) or g.sid)
            if g.identity else g for g in gold]
    old = gold[0].sid
    # Populate the real structural ledgers, not a mock ranking table.
    if case.shape in (1, 2):
        plot.set_movement(cid, "ledger", "silver ledger", "open", detail, old)
        commitments.set_movement(cid, "seal", "amber seal", "promise", "open", None, detail, old)
        relationship_history.append(cid, [relationship_history.row("bond",
            f"characters/{cast[0][0]}", f"characters/{cast[1][0]}", label="trust",
            before="", after="trust", scene=old)])
    if case.shape == 6:
        scenes.set_branch_keys(cid, old, "branch")
        # Accepted sibling closes the planted gold without querying retrieval.
        accepted = _scene(cid, "The room is quiet.", "The room is quiet.")
        scenes.set_branch_keys(cid, accepted, "branch")
        scenes.unmark_absorbed(cid, old)
        forbidden.add(old)
        gold = []
    current = scenes.create_scene(cid, "Saltmarch")
    scenes.append_message(cid, current, "user", case.query)
    for aid, vid in cast:
        transitions.appear(cid, current, "characters", aid, vid, "npc", narrate=False)
    if case.shape == 7:
        future = _scene(cid, detail, detail, cast=cast)
        forbidden.add(future)
    if case.shape == 8:
        pins.set_rule(cid, f"characters:{cast[0][0]}", "exclude", sid=current)
        loc = entities.create_entity(root, "locations", "Saltmarch", secrecy="gm-only")
        scenes.set_location(cid, old, loc)
    return Planted(cid, current, tuple(gold), frozenset(forbidden), fact, case.shape,
                   count + int(case.shape == 6))
