"""The play timeline (#198): every scene in play order, as a card, with the plot
beats that landed in it.

The continuity ledger (#117, ``routes.campaigns.get_ledger``) answers *what is
still open*. This answers the other half — *what happened, in what order* — and
it is the first reader of the per-scene beats ``plot.set_movement`` has been
writing since Phase 1. A thread's beats have always recorded the scene they
landed in; until now the only projection of them (``plot.open_threads``) kept
the latest one per thread and threw the history away.

**Derived, not parsed from ``timeline.md``.** ``chronicle.append_timeline``
writes an append-only prose file beside the chronicle, and
``docs/superpowers/PHASE-STATUS.md`` has flagged the missing reader for it since
Phase 1. This is not that reader: those lines carry no delimiters and no scene
ids, so parsing them back into events needs a format decision and a migration,
and it would buy a *worse* record than the one already on disk in structured
form. The gap stays open and stays flagged; nothing here depends on it.

What the ordering is, and why it is not the date:

Scene ids lead with a sequence number precisely so "lexicographic filename order
equals play order absolutely" (``store/scene_ids.py``), so that is the sort. In
fiction dates cannot substitute: a native date is ``<year>-<month key>-<day>``
where the month key is a *string* supplied by a calendar provider, so sorting
those strings is alphabetical by month name, and a flashback scene is out of
date order on purpose anyway.

What a card degrades to, which is the case that matters most:

``one_line``, ``summary`` and ``done`` are written by
``scenes.write.mark_absorbed``, and a campaign being played is normally a scene
or two ahead of its absorb — so the *ordinary* card has none of them. An
unabsorbed scene still gets its row, titled and dated from its own frontmatter,
because a timeline that showed only finished scenes would be missing the one you
are standing in.

Read-only. It takes ``campaign_lock`` for the reason the ledger and the briefing
do rather than the usual one: ``routes.scenes.put_chronicle`` records the
chronicle and then applies the absorb's plot edits under one hold, so an
unlocked pair of reads can catch that sequence half done and print a scene
marked absorbed beside a thread that has not moved yet.
"""

from __future__ import annotations

from . import chronicle, locks, plot
from .scenes import read as scenes_read


def _text(value, fallback: str = "") -> str:
    """A projected field as text. The same guard ``plot._field``,
    ``commitments._field`` and ``briefing._text`` apply, for the same reason:
    plot.json and chronicle.json are hand-editable and read by a bare
    ``json.loads``, so an object-valued ``title`` arrives here intact, and React
    refuses an object as a child — blanking the card rather than showing one odd
    row. No ``try`` around the READ catches that; the read succeeds.
    """
    return value.strip() if isinstance(value, str) else fallback


def _beats(threads, known: frozenset[str]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Every beat grouped by the scene it landed in, and the threads to offer as
    filters.

    Read from ``plot.read`` rather than ``plot.open_threads`` for two reasons,
    and each alone would be enough. ``open_threads`` projects only the LATEST
    beat, and the history is the whole point here. And it drops closed threads,
    which is right for a ledger of what is still owed and wrong for a record of
    what happened: a thread that resolved did not un-happen.

    Beats naming a scene this campaign no longer has are dropped rather than
    listed. A beat is rendered ON the card of the scene it names, so an orphan —
    a scene deleted since (``plot.repoint_scenes`` follows renames, so it is only
    ever deletion) — has nowhere to land. The thread goes with them: a filter
    chip that matches no card is worse than no chip.

    Wrong shapes are stepped over rather than trusted, the rule
    ``briefing._touched_scenes`` records: a beat whose ``scene`` is a list is
    *unhashable*, so testing it against a set RAISES rather than missing, and
    this projection runs outside its caller's tolerant read.
    """
    by_scene: dict[str, list[dict]] = {}
    roster: dict[str, dict] = {}
    # Sorted so a card's beats are grouped by thread in a fixed order. For a
    # file this app wrote that is already true — `plot._write` dumps with
    # `sort_keys=True` — so this is what holds for a hand-edited one, whose keys
    # arrive in whatever order they were typed. The alternative, ordering a
    # scene's beats by when they were recorded, is not available: plot.json
    # keeps beats per thread and no cross-thread sequence exists to read.
    #
    # Filtered before sorting, not by a sort key: `sorted` on the raw items
    # compares a non-string id against a string and raises, which would cost
    # every beat in the campaign for one hand-edited key.
    items = [(pid, rec) for pid, rec in (threads.items() if isinstance(threads, dict) else ())
             if isinstance(pid, str) and isinstance(rec, dict)]
    for pid, rec in sorted(items, key=lambda it: it[0]):
        title = _text(rec.get("title"), pid)
        status = _text(rec.get("status"), "open")
        raw = rec.get("beats")
        for beat in (raw if isinstance(raw, list) else ()):
            if not isinstance(beat, dict):
                continue
            sid, text = beat.get("scene"), _text(beat.get("text"))
            # An empty beat is not one `set_movement` can write — it appends
            # only when the text is non-blank — so this is the hand-edited case,
            # and a beat with nothing to say has nothing to show.
            if not isinstance(sid, str) or sid not in known or not text:
                continue
            by_scene.setdefault(sid, []).append(
                {"thread": pid, "title": title, "status": status, "text": text})
            roster[pid] = {"id": pid, "title": title, "status": status}
    # By title, so the filter reads as a list of threads rather than of slugs;
    # by id under it, because two threads can be titled the same and the order
    # still has to be one order.
    return by_scene, sorted(roster.values(), key=lambda t: (t["title"].lower(), t["id"]))


def build(cid: str) -> dict:
    """The whole timeline for one campaign: ``{"scenes": [...], "threads": [...]}``.

    Never raises on a garbled store file — a broken plot.json costs the beats,
    a broken chronicle.json costs the summaries, and the scenes survive both.
    That is the contract ``plot.render_open`` set and ``get_ledger`` and
    ``briefing.build`` kept.

    **Every** scene, uncapped, unlike the ledger's chronicle section (which
    keeps the last ``LEDGER_RECENT``). The two are answering different
    questions: that section is a recent-facts panel, where the tail is the whole
    value, and this is a play history, where a silent truncation would present a
    campaign's first fifty scenes as if they were all of it. The cost is bounded
    by what the shelf already pays — ``GET /campaigns`` runs ``list_scenes``
    over every campaign on it — and no transcript is opened here either. If this
    ever does need a window, it needs to be one the client can see and page,
    not a cap that looks like completeness.
    """
    def _tolerant(read, empty):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its part, not the view
            return empty

    with locks.campaign_lock(cid):
        # Frontmatter heads only — `list_scenes` never opens a transcript, which
        # is what makes a whole-campaign sweep affordable at play scale.
        scenes = _tolerant(lambda: scenes_read.list_scenes(cid), [])
        chron = _tolerant(lambda: chronicle.read_chronicle(cid), {})
        threads = _tolerant(lambda: plot.read(cid), {})
    # Unparseable is not the only way these files can be wrong: both are read by
    # a bare `json.loads`, so valid JSON of the wrong shape arrives without
    # raising. Checked where it is used rather than trusted from the read.
    if not isinstance(chron, dict):
        chron = {}

    known = frozenset(s["id"] for s in scenes)
    by_scene, roster = _beats(threads, known)

    out = []
    for meta in sorted(scenes, key=lambda m: m["id"]):
        sid = meta["id"]
        rec = chron.get(sid)
        rec = rec if isinstance(rec, dict) else {}   # a per-scene entry can be wrong too
        out.append({
            "id": sid,
            "title": _text(meta.get("title"), sid),
            # `one_line or summary`, the fallback every other chronicle consumer
            # uses (`context.story._story_entries`, `get_ledger`): a save may
            # leave `one_line` empty, and a card with only its title is a blank
            # line rather than a scene.
            #
            # The full `summary` is deliberately NOT shipped beside it. Nothing
            # renders it — a card is one line — and it is the largest field in
            # the record, so sending it would put the whole campaign's absorbed
            # prose on the wire for every load of a view that shows none of it.
            # The fallback above is what keeps its content reachable in the one
            # case it matters, a save that left `one_line` empty.
            "one_line": _text(rec.get("one_line")) or _text(rec.get("summary")),
            # The scene's OWN opening moment first (`time_history[0]`, which
            # `list_scenes` already projects as `date`). It is stamped when the
            # scene gets a datetime, absorbed or not, and it is when the scene
            # BEGAN — where the chronicle's `date` is its last moment and exists
            # only post-absorb. So the chronicle is the fallback, not the source.
            "date": _text(meta.get("date")) or _text(rec.get("date")),
            # The display name `chronicle.scene_facts` resolved at absorb time,
            # already flat text on disk. Deliberately not re-resolved from
            # `location_history` here: a card is a record of where the scene WAS,
            # and a location renamed since is still the place it happened.
            "location": _text(rec.get("location")),
            "done": bool(meta.get("done")),
            "pcless": bool(meta.get("pcless")),
            "beats": by_scene.get(sid, []),
        })
    return {"scenes": out, "threads": roster}
