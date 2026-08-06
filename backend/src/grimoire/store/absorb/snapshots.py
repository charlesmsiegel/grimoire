"""The deterministic snapshots the extraction prompt is primed with.

Each one renders stored state the model must rewrite from rather than recall:
present-cast feelings and bonds, open plot threads, unresolved commitments,
standing facts, every group's state, and the present NPCs' standing. All are read-only. Every
one but `state_snapshot` is tolerant of a garbled file -- a broken snapshot
omits its block instead of failing the absorb; `state_snapshot` catches only a
missing character record.
"""

from __future__ import annotations

from ... import prompts
from .. import (characters, commitments, facts, groupstate, overlay, playstate,
                plot, relationships)
from ..appearances import cast as appearances_cast, paths as appearances_paths
from ..campaigns import paths as campaigns_paths


def relationships_snapshot(cid: str, sid: str) -> str:
    """Rendered present-cast feelings/bonds block (feeds the prompt). Tolerant of a
    garbled relationships.json (returns "" rather than failing the extraction)."""
    try:
        tokens = [f"{a['kind']}:{a['id']}" for a in appearances_cast.scene_cast(cid, sid)]
        return "\n".join(relationships.render_present(cid, tokens, lambda t: relationships.actor_name(cid, t)))
    except Exception:  # noqa: BLE001 — garbled relationships.json: omit, don't crash
        return ""


def plot_snapshot(cid: str) -> str:
    """Rendered open/advanced plot threads (id + title + status + latest beat) — feeds the
    prompt so the model advances the right thread. Campaign-wide (not scene-scoped);
    tolerant of a garbled plot.json."""
    return "\n".join(plot.render_open(cid, with_id=True))


def commitment_snapshot(cid: str) -> str:
    """Rendered unresolved commitments (id + title + kind + status + due + latest
    beat) — feeds the prompt so the model resolves the commitment the scene
    actually paid off rather than opening a duplicate. Campaign-wide (not
    scene-scoped); tolerant of a garbled commitments.json."""
    return "\n".join(commitments.render_open(cid, with_id=True))


#: How many standing facts the extraction prompt is primed with. The one
#: snapshot here that needs a ceiling: a plot thread closes and a commitment
#: resolves, so those lists are self-limiting, but a fact leaves the standing
#: list only when a later scene explicitly contradicts it -- and most never are.
#: Nothing else bounds this. The absorb budget is wall-clock (`_Budget` in
#: routes/scenes.py), not tokens, so an uncapped snapshot would grow by a few
#: lines every absorb for the life of the campaign until the extraction call
#: stopped fitting in the model's context -- at which point every absorb fails
#: and the only way out is editing facts.json by hand.
#:
#: Set high rather than tight on purpose. It is a backstop against that runaway,
#: not a context-packing policy: a fact past the cap can no longer be superseded,
#: so a number small enough to bite an ordinary campaign would trade a real
#: failure for a quiet one. A library that genuinely stands 200 uncontradicted
#: facts wants retrieval or a retirement pass (#111's contradiction detection is
#: the natural home), not a larger constant.
FACT_SNAPSHOT_LIMIT = 200


def fact_snapshot(cid: str, sid: str | None = None) -> str:
    """Rendered standing facts (id + text + date) — feeds the prompt so the
    model can cite the id of a fact this scene made untrue rather than quietly
    contradicting it. Most recent `FACT_SNAPSHOT_LIMIT` only; tolerant of a
    garbled facts.json.

    Campaign-wide but AS OF `sid`, unlike its siblings here, which are all
    "now": absorbing an older scene — out of order, or `force`-re-absorbed —
    reads a ledger that later scenes have already moved, and showing it those
    later facts invites the model to supersede one from a scene that ran before
    it existed. `facts.record` refuses to write that, so the snapshot and the
    store agree on which facts this scene can end rather than the reviewer being
    offered a retirement that will not happen. `sid` of None keeps the whole
    standing ledger, for a caller with no scene in hand."""
    return "\n".join(facts.render_active(cid, FACT_SNAPSHOT_LIMIT, sid))


def group_snapshot(cid: str) -> str:
    """Every campaign group with its current state — feeds the absorb prompt so
    the model uses real ids and rewrites from stored values, not from memory."""
    try:
        croot = campaigns_paths.campaign_root(cid)
        lines = []
        for meta in overlay.list_entities(cid, "groups"):
            st = groupstate.read_state(croot, meta["id"])
            parts = [f"{groupstate.LABELS[k]}: {st[k]}" for k in groupstate.FIELDS
                     if st and st.get(k, "").strip()] if st else []
            state = " | ".join(parts) if parts else "(no state)"
            lines.append(f"- groups/{meta['id']} ({meta.get('name', meta['id'])}): {state}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — garbled store: omit, don't fail the extraction
        return ""


def state_snapshot(cid: str, sid: str) -> dict:
    """Present NPCs' existing standing snapshot — current_state with any Knows/Suspects
    folded in (via _snapshot_line) — keyed by display name (feeds the prompt)."""
    aroot = appearances_paths.locked_actor_root(cid)   # cast actors are locked, so campaign-side
    out: dict[str, str] = {}
    for a in appearances_cast.scene_cast(cid, sid):
        if a["role"] != "npc" or a["kind"] != "characters":
            continue
        st = playstate.read_state(aroot, a["id"])
        if st and (st["current_state"] or st["knows"] or st["suspects"]):
            try:
                name = characters.read_character(aroot, a["id"])["meta"].get("name", a["id"])
            except characters.CharacterNotFound:
                name = a["id"]
            out[name] = _snapshot_line(st)
    return out


def _snapshot_line(st: dict) -> str:
    return prompts.render("snippets/state_snapshot_line.j2", st=st)
