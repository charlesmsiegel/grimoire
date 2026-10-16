"""The investigation corpus's cases and their gold (spec 10.2).

Each case names its recipe, its entry point, what a correct result is, the
words that must never steer a selection (a hidden post's), and -- for the
offline arm -- the tool calls a model is scripted to make. The script is
canned, never recorded: it proves the loop, the checks and the graders handle
a run, never that a model would make it (`--live` is the only thing that
answers that).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import synth

#: The arms, per entry point (spec 10.1). E2's incumbent is the reconcile
#: sweep's own answer; E3's is one retrieval and nothing read further. E1's
#: arms (09 alone, 09 plus 10's planner, then 12 on 10's failure) wait on
#: 12-S6, and are reported absent.
ARMS: dict[str, tuple[str, ...]] = {
    "continuity": ("sweep", "investigation"),
    "question": ("retrieval", "investigation"),
}

#: The correctness margin by which the investigation arm must beat its
#: incumbent before an entry point's switch may default on (spec 10.4),
#: stated here in advance of any run: a fifth of the corpus.
MARGIN = 0.2

#: The other half of that bar, stated beside it and, like it, before any live
#: run -- moving it after one would be fitting the bar to the result. A margin
#: of rates says how far apart the arms finished, not whether that is more
#: than luck: over five continuity cases one case changing hands is the whole
#: margin, and over two question cases it is half the scale. So each case is
#: also paired with itself across the arms, and the challenger must win
#: enough of the cases they disagree on that an even coin, deciding each of
#: those cases, would give it as many wins this rarely at most: a one-sided
#: exact sign test, ties dropped (`runner.sign_test`). At 0.05 no record
#: passes on fewer than five cases won, so an entry point whose corpus is
#: smaller than that cannot be adopted until it grows.
SIGN_ALPHA = 0.05


@dataclass(frozen=True)
class Case:
    id: str
    recipe: str
    mode: str
    hypothesis: str
    #: Which of spec 10.2's corpus items the case stands for (`COVERAGE`).
    covers: str = ""
    #: E2: the decision word gold expects, and for a directed word its
    #: `(from, to)` refs (the proposal's own, after the sweep's rebuild).
    decision: str = ""
    direction: tuple[str, str] = ()
    #: E3: the reader's question, and the claim a correct answer states:
    #: groups of words, each group met by any one of them, every group met
    #: (`graders.answer`, which also refuses an answer that denies it).
    question: str = ""
    claim: tuple[tuple[str, ...], ...] = ()
    #: E3: phrases that state this case's claim the wrong way round without
    #: a general denial ("kept it instead of giving it back"), graded wrong
    #: beside `graders.DENIALS`.
    refutes: tuple[str, ...] = ()
    #: The offline arm's script: tool turns as `(text, [(name, args)])`, built
    #: from the planted campaign (the scene ids are only known once planted).
    script: Callable[[synth.Planted], list[tuple[str, list[tuple[str, dict]]]]] | None = None


def _search(query: str) -> tuple[str, list[tuple[str, dict]]]:
    return ("", [("search_history", {"query": query, "max": 4})])


def _excerpt(sid: str, query: str) -> tuple[str, list[tuple[str, dict]]]:
    return ("", [("get_scene_excerpt", {"scene": f"scene:{sid}", "query": query,
                                        "max_posts": 4})])


def _propose(decision: str, sid: str, *, frm: str = "", to: str = "",
             reason: str = "") -> tuple[str, list[tuple[str, dict]]]:
    return ("", [("propose", {"decision": decision, "from": frm, "to": to, "relation": "",
                              "status": "", "evidence_scenes": [f"scene:{sid}"],
                              "reason": reason})])


def _answer(text: str, sid: str) -> tuple[str, list[tuple[str, dict]]]:
    return ("", [("answer", {"text": text, "citations": [f"scene:{sid}"]})])


#: Spec 10.2's corpus items, per entry point: the gate refuses an entry point
#: whose run did not cover every one (`runner.gate`), so a case dropped from
#: `CASES` -- or a `--case`-narrowed run -- can never pass for the whole
#: corpus. E1's items (a callback, 11-C4's leakage cases, the hidden route
#: for an actor) wait on 12-S6 with its arm.
COVERAGE: dict[str, frozenset[str]] = {
    "continuity": frozenset({"nickname_promise", "duplicate", "continuation", "related",
                             "untitled_resolution"}),
    "question": frozenset({"nickname_promise", "thread_change"}),
}

#: The fewest scenes a planted campaign may hold for its case to count
#: (spec 10.2's "long history"; `synth.LEADING_FILLER`): the gate refuses an
#: entry point any of whose cases ran in a shorter one.
LONG_HISTORY = 10

THREADS = ("thread:deliver-the-harbour-ledger", "thread:find-the-harbour-ledger")

CASES: tuple[Case, ...] = (
    Case("nickname-promise-resolution", "promise_by_nickname", "continuity",
         "a promise kept by someone called only by a nickname is found kept",
         covers="nickname_promise", decision="fulfilled",
         script=lambda p: [_search("silver compass returned"), _search("Tidewalker"),
                           _excerpt(p.sids[2], "Tidewalker brass Seraphine"),
                           _propose("fulfilled", p.sids[2], reason="The Tidewalker is Mara.")]),
    Case("nickname-promise-question", "promise_by_nickname", "question",
         "a question about the promise is answered from the scene that kept it",
         covers="nickname_promise",
         question="Did Mara give back what she borrowed from Seraphine?",
         claim=(("compass", "brass"), ("return", "back")),
         refutes=("kept", "instead of", "still has", "lost", "broke", "not yet"),
         script=lambda p: [_search("Mara borrowed Seraphine"),
                           _excerpt(p.sids[2], "Tidewalker brass"),
                           _answer("Yes: as the Tidewalker she returned the silver compass "
                                   "at the quay before the tide.", p.sids[2])]),
    Case("same-errand-duplicate", "same_errand", "continuity",
         "two records whose sameness is said only in a transcript are found one",
         covers="duplicate", decision="duplicate",
         script=lambda p: [_search("harbour ledger one errand"),
                           _excerpt(p.sids[0], "same book errand"),
                           _propose("duplicate", p.sids[0], frm="B", to="A",
                                    reason="Mara says the two hunts are one errand.")]),
    Case("unnamed-debt-resolution", "debt_without_title", "continuity",
         "a debt settled without its title is found settled; a hidden word steers nothing",
         covers="untitled_resolution", decision="fulfilled",
         script=lambda p: [_search("quittance"), _search("boatman coins"),
                           _excerpt(p.sids[1], "boatman coins palm"),
                           _propose("fulfilled", p.sids[1],
                                    reason="Winifred pays the boatman.")]),
    Case("errand-continuation", "errand_continues", "continuity",
         "a thread that is the next leg of another, said only in a transcript, continues it",
         covers="continuation", decision="continuation", direction=THREADS,
         script=lambda p: [_search("harbour ledger guild steps"),
                           _excerpt(p.sids[1], "ledger errand next leg"),
                           _propose("continuation", p.sids[1], frm="A", to="B",
                                    reason="Delivering the ledger is the search's next leg.")]),
    Case("errands-merely-related", "errands_merely_related", "continuity",
         "two threads sharing a book and a clerk, said in a transcript to be different "
         "errands, are related and not one",
         covers="related", decision="related",
         script=lambda p: [_search("harbour ledger fees lost ledger"),
                           _excerpt(p.sids[0], "fees another matter clerk"),
                           _propose("related", p.sids[0], frm="A", to="B",
                                    reason="The clerk says the fees are another matter.")]),
    Case("thread-change-question", "thread_changed", "question",
         "why a thread changed is answered from a transcript two scenes before the beat",
         covers="thread_change",
         question="Why did the hunt for the harbour ledger change course?",
         claim=(("burn", "fire", "ash"), ("ledger", "warehouse")),
         refutes=("untouched", "intact", "survived", "spared", "unharmed", "safe"),
         script=lambda p: [_search("harbour ledger hunt changes course"),
                           _excerpt(p.sids[1], "ledger burned fire"),
                           _answer("The ledger burned with the warehouse, so Mara now hunts "
                                   "whoever set the fire.", p.sids[1])]),
)

#: Each case's forbidden steering words: a hidden post's.
def forbidden(planted: synth.Planted) -> tuple[str, ...]:
    return planted.hidden_terms
