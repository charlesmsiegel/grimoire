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


@dataclass(frozen=True)
class Case:
    id: str
    recipe: str
    mode: str
    hypothesis: str
    #: E2: the decision word gold expects.
    decision: str = ""
    #: E3: the reader's question, and words one of which a correct answer holds.
    question: str = ""
    needles: tuple[str, ...] = ()
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


CASES: tuple[Case, ...] = (
    Case("nickname-promise-resolution", "promise_by_nickname", "continuity",
         "a promise kept by someone called only by a nickname is found kept",
         decision="fulfilled",
         script=lambda p: [_search("silver compass returned"), _search("Tidewalker"),
                           _excerpt(p.sids[2], "Tidewalker brass Seraphine"),
                           _propose("fulfilled", p.sids[2], reason="The Tidewalker is Mara.")]),
    Case("nickname-promise-question", "promise_by_nickname", "question",
         "a question about the promise is answered from the scene that kept it",
         question="Did Mara give back what she borrowed from Seraphine?",
         needles=("compass", "brass", "tidewalker"),
         script=lambda p: [_search("Mara borrowed Seraphine"),
                           _excerpt(p.sids[2], "Tidewalker brass"),
                           _answer("Yes: as the Tidewalker she returned the silver compass "
                                   "at the quay before the tide.", p.sids[2])]),
    Case("same-errand-duplicate", "same_errand", "continuity",
         "two records whose sameness is said only in a transcript are found one",
         decision="duplicate",
         script=lambda p: [_search("harbour ledger one errand"),
                           _excerpt(p.sids[0], "same book errand"),
                           _propose("duplicate", p.sids[0], frm="B", to="A",
                                    reason="Mara says the two hunts are one errand.")]),
    Case("unnamed-debt-resolution", "debt_without_title", "continuity",
         "a debt settled without its title is found settled; a hidden word steers nothing",
         decision="fulfilled",
         script=lambda p: [_search("quittance"), _search("boatman coins"),
                           _excerpt(p.sids[1], "boatman coins palm"),
                           _propose("fulfilled", p.sids[1],
                                    reason="Winifred pays the boatman.")]),
)

#: Each case's forbidden steering words: a hidden post's.
def forbidden(planted: synth.Planted) -> tuple[str, ...]:
    return planted.hidden_terms
