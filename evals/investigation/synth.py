"""The corpus, planted through the real store APIs (spec 10.2).

Theory: each recipe is a small campaign whose answer sits where the obvious
query does not reach -- a promise later referred to only by a nickname, two
records whose sameness, continuation or mere relation is said only in a
transcript, a debt settled without its title, a thread whose change is a
beat two scenes from its reason -- and whose gold is written down as it is planted. Placeholder
names only (Seraphine, Mara, Winifred, Realm, Saltmarch).

Every recipe is a long history (`LEADING_FILLER`, `FILLER_BETWEEN`): the
scenes that matter sit among everyday filler scenes that share no word with
any case.

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
    #: Words that appear only in hidden posts, meaningful ones a model might
    #: search for: none may steer a selection or reach written text.
    hidden_terms: tuple[str, ...] = ()
    #: Where each hidden post is, as (scene id, post index): what the
    #: hidden-route probe rewrites to ask the same search again without it.
    hidden_posts: tuple[tuple[str, int], ...] = ()
    #: A word visible in the scene that holds the hidden post, which the probe
    #: searches beside each hidden word: a lexical match needs more than one
    #: term to admit a scene, so a hidden word searched alone proves nothing.
    probe_anchor: str = ""


#: An opaque word a hidden post carries beside its meaningful one, so the
#: probe has a term nothing visible shares with it, lexically or in meaning.
HIDDEN_NONCE = "vexillqorn"

#: What the probe rewrites a hidden post to: neither hidden word survives.
UNPLANTED = "Behind the stall a note."


def unplant(planted: Planted) -> None:
    """Rewrite every hidden post to `UNPLANTED`, still hidden: the store as
    it would be had the hidden text never been written."""
    for sid, index in planted.hidden_posts:
        scenes.edit_message(planted.cid, sid, index, UNPLANTED)


#: Spec 10.2's "long history": every case's answer sits in an archive, not a
#: short campaign. Filler scenes are played before the story starts and
#: before each scene that matters, so the scene that answers is one among
#: many a search has to rank past. Sized structurally -- at least
#: `cases.LONG_HISTORY` scenes for the shortest recipe -- to be tuned
#: against live runs later.
LEADING_FILLER = 8
FILLER_BETWEEN = 1

#: Everyday scenes that share no word with any case's question, claim,
#: search or hidden term, cycled in order.
FILLER = (
    ("Rope and tar", "Seraphine mends a torn sail on the quay while the gulls quarrel.",
     "Seraphine mends a sail."),
    ("The bakery queue", "Winifred waits for the morning loaves and talks about the weather.",
     "Winifred buys bread."),
    ("A lesson in knots", "Mara teaches two children the bowline and the sheet bend.",
     "Mara teaches knots."),
    ("Lamps at dusk", "The lamplighter makes his slow round of the square.",
     "The square is lit for the evening."),
    ("Rain on slate", "A long grey rain keeps everyone indoors over cards.",
     "A rainy afternoon of cards."),
    ("The goat at the gate", "Seraphine chases a stray goat out of the herb garden.",
     "A goat in the garden."),
    ("Mending nets", "Old fishers sit in a row and sing while their needles work.",
     "The fishers sing at their nets."),
    ("A letter from inland", "Winifred reads a cousin's letter about the apple harvest.",
     "Winifred reads a family letter."),
    ("Fog morning", "Fog rolls over Saltmarch so thick the bells sound muffled.",
     "A foggy morning in Saltmarch."),
    ("The new mule", "Mara haggles for a patient grey mule and names her Pebble.",
     "Mara buys a mule."),
)


def _filler(cid: str, n: int) -> None:
    for _ in range(n):
        count = len(scenes.list_scenes(cid))
        title, text, summary = FILLER[count % len(FILLER)]
        _played(cid, title, [("assistant", text)], summary)


def _campaign(name: str) -> str:
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign(name, wid)
    _filler(cid, LEADING_FILLER)
    return cid


def _scene(cid: str, title: str, posts: list[tuple[str, str]], summary: str) -> str:
    """One scene that matters, after `FILLER_BETWEEN` that do not."""
    _filler(cid, FILLER_BETWEEN)
    return _played(cid, title, posts, summary)


def _played(cid: str, title: str, posts: list[tuple[str, str]], summary: str) -> str:
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
    key = _pair(cid, ["thread:find-the-harbour-ledger", "thread:recover-the-harbour-ledger"])
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
        ("assistant", f"Behind the stall a note reads: quittance, stamped {HIDDEN_NONCE}.")],
        "Winifred spends the morning at the market.")
    scenes.set_excluded(cid, second, 1, True)
    commitments.set_movement(cid, "ferry-debt", "Pay the ferryman", "debt", "open", None,
                             "Winifred owes the boatman for the crossing.", first)
    key = _file_lifecycle(cid, "possible_commitment_resolution", "commitment:ferry-debt")
    return Planted(cid, (first, second), frozenset({second}), candidate=key,
                   hidden_terms=("quittance", HIDDEN_NONCE), hidden_posts=((second, 1),),
                   probe_anchor="Winifred")


def _pair(cid: str, refs: list[str]) -> str:
    """Deterministic discovery's own pair finding over `refs`, as `same_errand`
    files one; the corpus refuses to plant a case discovery would not find."""
    found = reconcile.discover(cid, stamp=reconcile.generation("synth"), full=True, embed=False)
    reconcile.persist_found(cid, found)
    key = canon.candidate_id("possible_duplicate", refs)
    if key not in candidates.read(cid)["records"]:
        raise RuntimeError("the corpus expected discovery to find the pair")
    return key


def errand_continues() -> Planted:
    """Two threads, one the next leg of the other -- said only in a
    transcript, under a summary that says nothing of it."""
    cid = _campaign("Saltmarch Errand")
    first = _scene(cid, "Saltmarch docks", [
        ("assistant", "Mara searches the warehouses for the harbour ledger.")],
        "Mara searches the warehouses.")
    second = _scene(cid, "The guild steps", [
        ("assistant", ("Mara has the harbour ledger at last. The search is over; what is left "
                       "is carrying it up the guild steps -- the same errand, its next leg."))],
        "Mara climbs to the guild hall.")
    plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger", "open",
                      "Mara starts looking for the harbour ledger.", first)
    plot.set_movement(cid, "deliver-the-harbour-ledger", "Deliver the harbour ledger", "open",
                      "Mara takes on a new task.", second)
    key = _pair(cid, ["thread:deliver-the-harbour-ledger", "thread:find-the-harbour-ledger"])
    return Planted(cid, (first, second), frozenset({second}), candidate=key)


def errands_merely_related() -> Planted:
    """Two threads that share a book and a clerk and are different errands --
    the transcript says so, and nothing else does."""
    cid = _campaign("Saltmarch Fees")
    first = _scene(cid, "The guild counter", [
        ("user", "Are the harbour ledger fees the same business as the lost ledger?"),
        ("assistant", ("The clerk shakes his head at Winifred: the fees are another matter "
                       "entirely from the lost ledger, though the same clerk keeps both."))],
        "Winifred talks to a guild clerk.")
    plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger", "open",
                      "Winifred hears the harbour ledger is lost.", first)
    plot.set_movement(cid, "settle-the-harbour-ledger-fees", "Settle the harbour ledger fees",
                      "open", "Winifred owes the guild its harbour ledger fees.", first)
    key = _pair(cid, ["thread:find-the-harbour-ledger", "thread:settle-the-harbour-ledger-fees"])
    return Planted(cid, (first,), frozenset({first}), candidate=key)


def thread_changed() -> Planted:
    """"Why did this thread change?": the change is a beat, the reason a
    transcript two scenes before it, and neither scene's summary says so."""
    cid = _campaign("Saltmarch Ash")
    first = _scene(cid, "Saltmarch docks", [
        ("assistant", "The guild hires Mara to find the harbour ledger.")],
        "Mara takes a job from the guild.")
    second = _scene(cid, "The waterfront", [
        ("assistant", ("Mara finds only ash where the warehouse stood. The ledger burned "
                       "with it, and she swears to learn who set the fire."))],
        "Mara walks the waterfront.")
    third = _scene(cid, "Market day", [
        ("assistant", "Seraphine haggles over rope with a net-mender.")],
        "Seraphine buys rope.")
    fourth = _scene(cid, "The guild hall", [
        ("assistant", "Mara reports to the guild and asks for more time.")],
        "Mara reports to the guild.")
    plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger", "open",
                      "Mara takes the job.", first)
    plot.set_movement(cid, "find-the-harbour-ledger", "Find the harbour ledger", "open",
                      "The hunt changes course.", fourth)
    return Planted(cid, (first, second, third, fourth), frozenset({second}))


RECIPES = {"promise_by_nickname": promise_by_nickname, "same_errand": same_errand,
           "debt_without_title": debt_without_title, "errand_continues": errand_continues,
           "errands_merely_related": errands_merely_related, "thread_changed": thread_changed}
