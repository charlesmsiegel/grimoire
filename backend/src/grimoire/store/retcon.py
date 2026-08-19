"""Retcon: rewrite a past post, and say what the rewrite contradicts (#78).

Two things, and they are deliberately not one call.

**The rewrite.** `retcon` edits one post and then does what an edit inside an
already-absorbed scene has always needed doing: it puts back what that scene's
absorb wrote (`cascade.revert_scene`) and clears `done`, so the scene can be
extracted again over the text that is actually there. `PUT
/scenes/{sid}/messages/{index}` deliberately does not do that — it is the
in-place text fix, and un-absorbing a finished scene is not a side effect a
typo correction should carry — so the two live side by side and the caller says
which one it means.

**The contradiction pass.** Re-extracting an old scene produces claims about a
moment that later scenes have already played past, and the interesting rows are
the ones where the fresh extraction disagrees with a value some LATER scene
wrote. `contradictions` finds those, and it is the whole of what this module
adds to the review: `absorb.materialize` already stages `before` as the
record's current value, so a row whose `after` differs from its `before` is a
disagreement with what is stored, and the only remaining question is *who* put
that value there.

Which is answered from what the pipeline already tags with a scene id — the
same tags `store/cascade.py` reverses through, read here rather than written:

- **`provenance.json`** — one citation per applied edit, keyed
  ``"<kind>/<id>#<field>"``, carrying the scene it was quoted from (#112). The
  finest attribution there is, and the only one that reaches a relationship or
  a fact, so it is consulted first.
- **`changes.json`** — the latest write-back per browsable record
  (``"<kind>/<id>"``), carrying its scene. Coarser (a record, not a field) and
  rolling (only the last write survives), which is exactly why it is second.
- **`plot.json` / `commitments.json`** — each thread's `last_scene`, which is
  the one attribution those two carry on the record itself.

Nothing else is consulted, and a row nothing can attribute is not reported.
That is the same posture `absorb/conflicts.py` takes and for the same reason: a
badge that says "a later scene disagrees" has to be able to name the scene, or
it is an accusation with no evidence. Two consequences worth stating out loud,
because both are the issue's own gotchas:

- **A relationship or bond is attributable only as far as its citations go.**
  `relationships.json` has no scene field of its own (`since_scene` is
  schema-present and never populated), so a feeling written before citations
  existed, or by a row the model did not quote, cannot be blamed on a scene and
  is silently not flagged. It is not evidence of agreement.
- **Play order is derived, not stored.** Scene ids are number-first
  (`store/scene_ids.py`), so "later" is a number comparison when both ids parse.
  A legacy id outside that grammar falls back to `created`, and when even that
  cannot be compared the pair is left unordered — no claim.

Like `store/cascade.py`, this module is deliberately absent from
`locks.DOMAIN_MODULES`: it writes no file of its own — `retcon` takes the
campaign lock across the edit and the reversal because those must not be
separable, but every store it delegates to is classified in its own right, and
`test_lock_domain_guard.py` does not survey a module that mutates nothing.

**None of this is a gate.** Every row it produces is advisory: the reviewer
reads the badge and decides. Deciding for them would mean automatically
rewriting a later scene's continuity from an older scene's re-reading, which is
the one thing a retcon must not do on its own.
"""

from __future__ import annotations

from . import (
    alternates,
    cascade,
    changes,
    chronicle,
    commitments,
    commits,
    locks,
    plot,
    provenance,
    scene_ids,
    turnstate,
)
from .scenes import read as scenes_read
from .scenes import write as scenes_write

#: Where an attribution came from, in the order they are consulted. Exposed
#: because the row carries it: a reviewer told "scene 4 changed this" is owed
#: the difference between a quote from that scene and a record-level log entry.
SOURCES: tuple[str, ...] = ("citation", "changes", "thread")


def _play_order(scenes: list[dict]) -> dict[str, tuple]:
    """Each scene id mapped to a sort key, or absent when it has none.

    Two keys, and they never mix: an id inside the grammar sorts by its number,
    an id outside it by its `created` stamp. Comparing one of each would be
    comparing an integer with a date, so the caller requires the two keys it
    compares to be the same shape and declines the pair otherwise.
    """
    out: dict[str, tuple] = {}
    for meta in scenes:
        parsed = scene_ids.parse_sid(meta["id"])
        if parsed:
            out[meta["id"]] = ("n", parsed["number"])
        elif meta.get("created"):
            out[meta["id"]] = ("t", meta["created"])
    return out


def _later(scenes: list[dict], sid: str) -> set[str]:
    """`later_scenes`, over a scene list the caller already has."""
    order = _play_order(scenes)
    mine = order.get(sid)
    if mine is None:
        return set()
    return {other for other, key in order.items()
            if other != sid and key[0] == mine[0] and key[1] > mine[1]}


def later_scenes(cid: str, sid: str) -> set[str]:
    """Every scene that provably comes after `sid` in play order.

    "Provably" is the whole contract: a scene whose order relative to `sid`
    cannot be established is not in the set, so a contradiction is never
    reported against it. The alternative — assuming an unorderable scene is
    later — would put a badge on rows whose evidence is a coin toss.
    """
    return _later(scenes_read.list_scenes(cid), sid)


def _titles(scenes: list[dict], cid: str) -> dict[str, str]:
    """Scene id -> the label a badge should show. The chronicle's one-line
    summary where the scene has been absorbed, the scene title otherwise, and
    the bare id when it has neither."""
    out = {m["id"]: m.get("title") or m["id"] for m in scenes}
    chron = chronicle.read_chronicle(cid)
    for sid, rec in (chron.items() if isinstance(chron, dict) else ()):
        line = rec.get("one_line") if isinstance(rec, dict) else ""
        if isinstance(line, str) and line.strip():
            out[sid] = line.strip()
    return out


def _scene_of(rows: dict, key: str) -> str:
    """The scene one row of a scene-tagged store names, or `""`.

    Every store read here is a hand-editable file the user owns, so a row that
    is not a dict — or a `scene` that is not a string — has to mean "no
    attribution" rather than an exception out of a display pass. Same tolerance
    `changes.read` and `provenance.read` apply to the files themselves.
    """
    if not key or not isinstance(rows, dict):
        return ""
    row = rows.get(key)
    scene = row.get("scene") if isinstance(row, dict) else None
    return scene if isinstance(scene, str) else ""


#: Kinds whose `after` REPLACES the stored value, so `before` and `after` are
#: two answers to one question and differing is disagreeing. A state body, a
#: card field, a feeling and a bond type are each written whole.
#:
#: Deliberately NOT `conflicts.MERGEABLE`, which this was written from and which
#: is one kind wider: a `lore` row's `after` is its `before` plus an appended
#: paragraph (`materializer`), so it differs from what is stored every single
#: time and a comparison here would badge every lore row a later scene had
#: touched. Appending is not disagreeing, and no string comparison can tell
#: whether the paragraph an older scene wants to add contradicts one a later
#: scene already added — so this claims nothing about lore, which is the same
#: silence it keeps for a fact or a weather axis.
_COMPARABLE: frozenset[str] = frozenset({
    "character_state", "group_state", "authored", "relationship", "bond"})

#: Kinds whose disagreement is a STATUS, not a text. A plot row's `before` is
#: `conflicts.plot_line` — a rendering of the thread's status plus its last beat
#: — while its `after` is the new beat alone, so the two are never equal and a
#: text comparison would call every row a contradiction. What actually
#: contradicts a later scene there is the thread's state: this scene says
#: `closed` where the scene after it left the thread `open`. An added beat is
#: not a disagreement — beats accumulate.
_STATUS_KINDS: frozenset[str] = frozenset({"plot", "commitment"})


def _changed(edit: dict, threads: dict, promises: dict) -> bool:
    """Whether this edit actually disagrees with what is stored.

    Three answers, and the third one is the point of the split:

    - A **comparable** kind is compared as text: one whose apply REPLACES the
      stored value, so `before` and `after` are two answers to one question.
      Equality means the fresh extraction re-derived what is already there — a
      re-reading that agrees with the record contradicts nobody, whoever wrote
      it.
    - A **status** kind is compared on its status alone, for the reason
      `_STATUS_KINDS` gives.
    - Everything else — a lore append, a fact, a weather axis, a dossier, a
      sheet, a record this scene would CREATE — has no two comparable answers,
      so nothing is claimed. `fact_line` and `weather`'s rows render a fingerprint
      into `before` that `after` was never in the format of; comparing those
      would manufacture a disagreement out of a formatting difference, which is
      exactly what a badge must not do.

    An edit with no `before` at all has no basis and claims nothing either, the
    same silence `absorb/conflicts.py` keeps.
    """
    kind = edit.get("kind")
    if kind in _COMPARABLE:
        before, after = edit.get("before"), edit.get("after")
        if not isinstance(before, str) or not isinstance(after, str):
            return False
        return before.strip() != after.strip()
    if kind in _STATUS_KINDS:
        rec = _thread(edit, threads if kind == "plot" else promises)
        payload = edit.get("payload")
        proposed = payload.get("status") if isinstance(payload, dict) else None
        stored = rec.get("status") if isinstance(rec, dict) else None
        return (isinstance(proposed, str) and isinstance(stored, str)
                and proposed != stored)
    return False


def _target_ref(edit: dict) -> str:
    """``"<kind>/<id>"`` for an edit's target, or `""` when it names none. The
    key `absorb.apply` files a write-back under, rebuilt from the same fields
    (`f"{target['kind']}/{target['id']}"`) — stringified for the reason
    `provenance.key` stringifies its own: these come off a client PUT body."""
    target = edit.get("target")
    if not isinstance(target, dict):
        return ""
    kind, rid = target.get("kind"), target.get("id")
    if not isinstance(kind, str) or not isinstance(rid, str) or not kind or not rid:
        return ""
    return f"{kind}/{rid}"


def _thread(edit: dict, threads: dict) -> dict:
    """The stored thread an edit addresses, or `{}`."""
    target = edit.get("target")
    tid = target.get("id") if isinstance(target, dict) else None
    rec = threads.get(tid) if isinstance(tid, str) and isinstance(threads, dict) else None
    return rec if isinstance(rec, dict) else {}


def _thread_scene(edit: dict, threads: dict, kind: str) -> str:
    """The scene that last moved the thread this edit addresses, or `""`.

    `plot` and `commitments` are the two stores whose record carries its own
    scene attribution (`last_scene`), which is why they are consulted at all:
    a plot row's write-back is not browsable, so `changes.json` never held it.
    """
    if edit.get("kind") != kind:
        return ""
    last = _thread(edit, threads).get("last_scene")
    return last if isinstance(last, str) else ""


def contradictions(cid: str, sid: str, edits: list) -> list[dict]:
    """The rows of a re-extraction of `sid` that a later scene already answered.

    One row per contradicted edit::

        {"id": <edit id>, "scene": <sid of the later scene>,
         "label": <that scene's one-line or title>, "source": <SOURCES member>}

    Empty for the ordinary case — absorbing the newest scene, where there is no
    later scene to disagree with — which is why the pass is unconditional in the
    absorb route rather than a mode the caller has to know to ask for.

    Reads four files once each, whatever the batch size. It is called with the
    whole staged list on a path that has just made several LLM calls, so the
    cost that matters is the file reads, not the loop.
    """
    # ONE enumeration of the scene directory, shared by the ordering and the
    # labels. `list_scenes` parses the frontmatter of every scene in the
    # campaign, and this used to run it twice — a second full walk of a long
    # campaign's scenes to look up a handful of titles.
    scenes = scenes_read.list_scenes(cid)
    later = _later(scenes, sid)
    if not later:
        return []
    cites, log = provenance.read(cid), changes.read(cid)
    threads, promises = plot.read(cid), commitments.read(cid)
    labels = _titles(scenes, cid)
    out: list[dict] = []
    for edit in edits:
        if not isinstance(edit, dict) or not _changed(edit, threads, promises):
            continue
        ref = _target_ref(edit)
        # Order is the docstring's: the finest attribution that can answer wins.
        candidates = (
            ("citation", _scene_of(cites, provenance.key(edit) or "")),
            ("changes", _scene_of(log, ref)),
            ("thread", _thread_scene(edit, threads, "plot")
                       or _thread_scene(edit, promises, "commitment")),
        )
        for source, scene in candidates:
            if isinstance(scene, str) and scene in later:
                out.append({"id": edit.get("id", ""), "scene": scene,
                            "label": labels.get(scene, scene), "source": source})
                break
    return out


def retcon(cid: str, sid: str, index: int, content: str) -> dict:
    """Rewrite the post at `index`, and undo what this scene's absorb wrote.

    Returns `cascade.revert_scene`'s report with `later` on top — the scenes
    that come after this one, which is what tells the caller whether a
    re-extraction can contradict anything at all.

    Raises what the edit raises and nothing more: `scenes.SceneNotFound`,
    `IndexError` for an index outside the transcript, and
    `scenes.RollMessageImmutable` for a manual roll's line — the same three
    `PUT /messages/{index}` answers, because up to the edit this *is* that
    route. Past the edit nothing raises, for `cascade`'s reason: the post is
    already rewritten, and a request that 500s over a failed sweep leaves a
    scene whose chronicle record describes text that is gone.

    One lock across all of it. The edit and the reversal are not independently
    meaningful — a rewritten post beside a chronicle summary of the old one is
    the state this exists to prevent — and `cascade.revert_scene` takes the same
    reentrant lock again for its own span.

    The two writes beside the edit are the ones `PUT /messages/{index}` makes,
    and for its reasons rather than for this feature's: the transient-state
    ledger from this post on describes text that no longer exists
    (`turnstate.supersede`), and the reroll sidecar holds the pre-edit reply as
    its only copy until `alternates.reconcile` files it. Both are best-effort
    against `OSError` there and here — the edit is on disk, and a sidecar is not
    a reason to fail it.

    Macros are NOT expanded here, unlike that route: this takes the content it
    is given. The expansion is a route-layer concern (it needs the request's
    campaign and scene substitutions), and doing it in both places would double
    it for the one caller that reaches this through the route.
    """
    with locks.campaign_lock(cid):
        # Read BEFORE the edit, which is the only thing here that can be asked
        # of the caller. It does not depend on the edit — the scene list is the
        # same either side of it — and reading it afterwards would put a read
        # that can fail (an unreadable scenes directory) between a landed edit
        # and the reversal it must not be separated from.
        later = sorted(later_scenes(cid, sid))
        # FIRST, for `cascade.delete_from`'s reason and against the same
        # hazard: a review prepared from the pre-retcon transcript is holding a
        # valid commit token, and saving it would write that transcript's
        # summary and its edits straight back over the reversal below. Retiring
        # the scene's epoch fences it, and it goes ahead of the edit because a
        # fence that fails there costs a request, where one that fails after
        # leaves the post rewritten and the stale review free to save.
        commits.retire_scene(cid, sid)
        scenes_write.edit_message(cid, sid, index, content)
        try:
            turnstate.supersede(cid, sid, index)
        except OSError:
            pass
        try:
            alternates.reconcile(cid, sid)
        except OSError:
            pass
        return {"later": later, **cascade.revert_scene(cid, sid)}
