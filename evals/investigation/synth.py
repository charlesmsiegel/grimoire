"""The corpus, planted through the real store APIs (spec 10.2).

Theory: each recipe is a small campaign whose answer sits where the obvious
query does not reach -- a promise later referred to only by a nickname, two
records whose sameness is said only in a transcript, a debt settled without
its title -- and whose gold is written down as it is planted. Placeholder
names only (Seraphine, Mara, Winifred, Realm, Saltmarch).

A continuity finding is filed as the sweep files one: a pair by deterministic
discovery, a lifecycle finding at the fingerprint `pending` computes for it
now (a lifecycle record is otherwise nominated only by the calendar or by a
model, neither of which an offline corpus has).
"""

from __future__ import annotations

from dataclasses import dataclass

from grimoire.store import (
    campaigns,
    chronicle,
    commitments,
    paths,
    plot,
    scenes,
    worlds,
)
from grimoire.store.continuity import candidates, canon, pending, reconcile


@dataclass(frozen=True)
class Planted:
    cid: str
    #: Scene ids in play order.
    sids: tuple[str, ...]
    #: The scenes whose text settles the case.
    gold_scenes: frozenset[str]
    #: E2: the finding's id; "" for a question case.
    candidate: str = ""
    #: Words that appear only in hidden posts: a search over one must answer
    #: exactly as a search over a word that appears nowhere.
    hidden_terms: tuple[str, ...] = ()


def _campaign(name: str) -> str:
    wid = worlds.create_world("Realm")
    return campaigns.create_campaign(name, wid)


def _scene(cid: str, title: str, posts: list[tuple[str, str]], summary: str) -> str:
    sid = scenes.create_scene(cid, title)
    for role, text in posts:
        scenes.append_message(cid, sid, role, text)
    chronicle.absorb(cid, {"id": sid, "one_line": summary, "summary": summary,
                           "keywords": [], "cast": [], "location": "", "date": ""})
    scenes.mark_absorbed(cid, sid, summary, summary)
    return sid


def _file_lifecycle(cid: str, kind: str, ref: str) -> str:
    """File one lifecycle finding at its current fingerprint, as persist 1
    files a deterministic one."""
    current = pending.Current.load(cid)
    fingerprint = pending.fingerprint(current, kind, [ref])
    key = canon.candidate_id(kind, [ref])
    data = candidates.read(cid)
    data["records"][key] = {"kind": kind, "refs": [ref], "fingerprint": fingerprint,
                            "signals": {"reason": "touched"}, "proposal": None,
                            "created": paths.now_iso()}
    candidates.write(cid, data)
    return key


def promise_by_nickname() -> Planted:
    """A promise made early, kept later by someone called only by a nickname
    introduced in between."""
    cid = _campaign("Saltmarch Oath")
    first = _scene(cid, "The pier at dusk", [
        ("user", "Mara takes the silver compass from Seraphine."),
        ("assistant", "Mara swears she will bring the silver compass back before the spring tide.")],
        "Mara borrows Seraphine's silver compass and swears to return it.")
    second = _scene(cid, "The net-mender's song", [
        ("assistant", "The fishers have started calling Mara the Tidewalker, after her night swims.")],
        "The fishers give Mara a nickname.")
    third = _scene(cid, "Spring tide", [
        ("assistant", ("At the quay the Tidewalker presses the cold brass into Seraphine's "
                       "hand. \"Back before the tide, as sworn.\""))],
        "A quiet morning at the quay as the spring tide comes in.")
    commitments.set_movement(cid, "compass-oath", "Return the silver compass", "promise",
                             "open", None, "Mara swears to return the compass.", first)
    key = _file_lifecycle(cid, "possible_commitment_resolution", "commitment:compass-oath")
    return Planted(cid, (first, second, third), frozenset({third}), candidate=key)


def same_errand() -> Planted:
    """Two threads whose sameness is said only in a transcript."""
    cid = _campaign("Saltmarch Ledger")
    first = _scene(cid, "Saltmarch docks", [
        ("user", "Where is the harbour ledger?"),
        ("assistant", ("Mara shrugs: the ledger we hunt and the one the guild lost are one "
                       "and the same book, and it is one errand."))],
        "Mara talks about the harbour ledger.")
    plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger", "open",
                      "Mara starts looking for the harbour ledger.", first)
    plot.set_movement(cid, "recover-the-harbour-ledger", "Recover the harbour ledger", "open",
                      "The guild asks for the harbour ledger back.", first)
    found = reconcile.discover(cid, stamp=reconcile.generation("synth"), full=True, embed=False)
    reconcile.persist_found(cid, found)
    key = canon.candidate_id("possible_duplicate", ["thread:find-the-harbour-ledger",
                                                    "thread:recover-the-harbour-ledger"])
    if key not in candidates.read(cid)["records"]:
        raise RuntimeError("the corpus expected discovery to find the pair")
    return Planted(cid, (first,), frozenset({first}), candidate=key)


def debt_without_title() -> Planted:
    """A debt settled in a scene that never names it, beside a hidden post
    whose one word would point straight at the answer."""
    cid = _campaign("Saltmarch Ferry")
    first = _scene(cid, "The crossing", [
        ("assistant", "Winifred cannot pay for the crossing; the boatman lets her owe him.")],
        "Winifred crosses the river on credit.")
    second = _scene(cid, "Market day", [
        ("assistant", ("Winifred presses three coins into the old boatman's palm and he "
                       "nods, satisfied at last.")),
        ("assistant", "Behind the stall a note reads: quittance.")],
        "Winifred spends the morning at the market.")
    scenes.set_excluded(cid, second, 1, True)
    commitments.set_movement(cid, "ferry-debt", "Pay the ferryman", "debt", "open", None,
                             "Winifred owes the boatman for the crossing.", first)
    key = _file_lifecycle(cid, "possible_commitment_resolution", "commitment:ferry-debt")
    return Planted(cid, (first, second), frozenset({second}), candidate=key,
                   hidden_terms=("quittance",))


RECIPES = {"promise_by_nickname": promise_by_nickname, "same_errand": same_errand,
           "debt_without_title": debt_without_title}
