"""Whether a scene's stored rolling summary still describes its transcript.

The test that decides a fold's `prior` and the panel's `stale` used to live in
`routes.scenes`. It lives here so the route and `store/searchdocs` (roadmap
08, which may carry a running summary into a scene's search document only while
it is intact) ask one function the same question: a summary over a prefix the
player has since hidden, cut, edited or put under a new prompt rule describes
text the model is no longer shown, and must not reach a prompt, a document or an
embedding request.

It sits below both callers and above what it reads: `scenes` (the transcript),
`regex` (the prompt view the digest is taken over) and `rolling_summary` (the
digests). Not in `rolling_summary.py` itself, which `scenes.write` imports, so
importing `scenes` there would close a cycle.
"""

from __future__ import annotations

from . import rolling_summary
from .appearances import cast as appearances_cast
from .regex import view as regex_view
from .scenes import read as scenes_read
from .scenes import serialize as scenes_serialize


def view(cid: str, sid: str, scene: dict, facts: dict) -> dict:
    """The stored summary reconciled against the transcript as it stands now.

    Three things come out of this, and keeping them apart is the point:

    - `summary` is what is stored, ALWAYS, however stale. A summary whose posts
      were rerolled still describes most of the scene, and it is the best thing
      anyone has until the next refold; blanking it would leave the panel saying
      "no summary yet" about a scene that has one. Review caught that: it also
      made the panel's own staleness warning unreachable, since the warning
      renders beside prose there was then never any of.
    - `prior` is what a fold may build ON, which is `summary` only while the
      prefix it covered is still the prefix on disk. Otherwise "" -- start over
      from the whole transcript rather than carry prose about a post the player
      deleted.
    - `stale` is that same finding said out loud, so the panel can present the
      summary as behind rather than as current.

    An unsummarized scene is not stale: it has nothing to be stale about, and
    saying otherwise would put a warning on every scene nobody has summarized.
    """
    messages = scene["messages"]
    total = len(messages)
    # Out of the snapshot this was handed, NOT a second read of the same file.
    # Review caught that reading twice pairs one snapshot's transcript with
    # another's metadata: a commit landing between the two yields `at: 11`
    # against a 10-message transcript, and reports a summary that is exactly
    # current as stale -- a contradiction the panel then holds until something
    # else refreshes it.
    stored = scenes_read.rolling_summary_fields(scene["meta"])
    # `at > total` short-circuits rather than slicing: `messages[:at]` past the
    # end yields the whole list and would digest-match a transcript that has
    # since been trimmed back to exactly what it covered.
    #
    # BOTH digests, because a summary can go stale two ways and only one of them
    # is visible in the transcript. The facts half is the price of putting facts
    # in the prompt at all: a scene's first location and first date are set
    # SILENTLY, so they can change with no message appended, and a summary built
    # from the wrong location is as stale as one built from a deleted post.
    #
    # Coverage without prose is not coverage, which is why the emptiness test
    # comes first rather than being left to `prior` alone. `rolling_at` means
    # "how much of the transcript this summary describes", so with no summary it
    # describes nothing however well its digests still check out -- and every
    # store here is a hand-editable markdown tree, so a scene whose
    # `rolling_summary:` line was blanked while `rolling_at`/`rolling_digest`
    # survive is a file someone can actually produce. Review caught what that
    # cost: `prior` was correctly "" and `base` was NOT, so the fold took the
    # from-scratch branch of the prompt and was handed only `messages[base:]` --
    # a summary of the tail, written as if it were the whole scene, then stored
    # as covering all of it. The `upto` overtaken check below already tested
    # `ahead["summary"]` for this reason; this puts the rule in one place.
    has_summary = bool(stored["summary"])
    intact = (has_summary
              and stored["at"] <= total
              and prefix_digest(cid, sid, messages[:stored["at"]], total) == stored["digest"]
              and rolling_summary.facts_digest(facts) == stored["facts"])
    return {"summary": stored["summary"],
            # Reported as 0 for the same reason, so the panel cannot say
            # "covers 12 of 14 posts" beside "no summary yet".
            "at": stored["at"] if has_summary else 0, "total": total,
            "stale": has_summary and not intact,
            "prior": stored["summary"] if intact else "",
            "base": stored["at"] if intact else 0,
            # Pending posts a fold would actually SEE: one over hidden posts (or
            # director notes, which the render drops too) only would pay a
            # provider to restate the summary.
            "fresh": len(scenes_serialize.in_context(messages[stored["at"] if intact else 0:]))}


def prefix_digest(cid: str, sid: str, covered: list[dict], total: int) -> str:
    """`covered_digest` over the PROMPT view of the prefix a fold covers, which
    is what the fold was written from: a prompt rule added, edited or switched
    off afterwards changes what the summary describes as surely as an edit
    does. With no prompt rule the view is the raw text, so the digest is the
    one stored before rules existed.

    Depth counts over the transcript as it is now, `total` messages long --
    the prefix seen inside it, as the prompt sees it -- at the fold, at the
    commit's precondition and at every read alike. So an append that carries a
    covered post across a depth-bounded rule's boundary, where the rule now
    changes it, voids the summary: it describes text the model is no longer
    shown. That is the trade-off, taken for correctness: with such a rule
    rewriting covered posts, a scene refolds as each one crosses (from
    scratch, since the prior is void too), and an append that lands while a
    fold is at the provider can refuse its commit. A depth rule that changes
    no covered post, and every rule without depth bounds, changes nothing
    here, so an ordinary append still leaves the summary current."""
    shown = regex_view.view(covered, cid=cid, phase="prompt", total=total)
    return rolling_summary.covered_digest(shown, appearances_cast.player_label(cid, sid))
