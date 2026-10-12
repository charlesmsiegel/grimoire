"""The deterministic snapshots the extraction prompt is primed with.

Each one renders stored state the model must rewrite from rather than recall:
present-cast feelings and bonds, open plot threads, unresolved commitments,
standing facts, every group's state, and the present NPCs' and PCs' standing. All are read-only. Every
one but `state_snapshot` and `group_snapshot` is tolerant of a garbled file -- a
broken snapshot omits its block instead of failing the absorb; `state_snapshot`
catches only a missing character record, and `group_snapshot` lets a group
identity failure refuse the absorb before anything is paid for (spec 07).
"""

from __future__ import annotations

from ... import prompts
from .. import (
    characters,
    facts,
    group_refs,
    groupstate,
    membership,
    playstate,
    relationships,
    steering,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..continuity import effective


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
    prompt so the model advances the right thread. Effective rows: a merged-away
    thread is folded into its canonical, so only the canonical id is offered.
    Campaign-wide (not scene-scoped); tolerant of a garbled plot.json."""
    return "\n".join(effective.render_threads(cid, with_id=True))


def commitment_snapshot(cid: str) -> str:
    """Rendered unresolved commitments (id + title + kind + status + due + latest
    beat) — feeds the prompt so the model resolves the commitment the scene
    actually paid off rather than opening a duplicate. Effective rows, canonical
    ids only, as `plot_snapshot`. Campaign-wide (not scene-scoped); tolerant of a
    garbled commitments.json."""
    return "\n".join(effective.render_commitments(cid, with_id=True))


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


#: The preface the secret block carries (spec 07 section 9.2). Only that block
#: asks `prompt_visible(..., secret_framed=True)`; without it, the rows go too.
SECRET_GROUPS_PREFACE = ("GM context: these groups and affiliations are unknown to "
                         "uninvolved characters; do not have them voice or act on "
                         "this information.")


def _affiliates_here(cid: str, roster: membership.Roster, present: set[str]) -> str:
    """Present affiliates only, leader first then members in stored order, each
    spelled as the Present line spells it and with every role it holds."""
    entries = []
    if roster.leader is not None and roster.leader in present:
        roles = "leader, member" if roster.leader in roster.members else "leader"
        entries.append((roster.leader, roles))
    entries.extend((actor, "member") for actor in roster.members
                   if actor in present and actor != roster.leader)
    return ", ".join(f"{relationships.actor_name(cid, actor)} ({actor.replace(':', '/', 1)}, {roles})"
                     for actor, roles in entries)


def group_snapshot(cid: str, cast=(), *, snapshot: membership.GroupSnapshot | None = None) -> str:
    """Every campaign group with its current state, and its present affiliates --
    feeds the absorb prompt so the model uses real ids, rewrites from stored
    values, and does not re-propose a standing membership.

    Affiliations come from the coupled inverse (`membership.campaign_snapshot`),
    so the detached rule holds here as on every other reader, and identity
    failures propagate before any paid generation. Only actors on the `Present:`
    line (`cast`) are named, so a group's size costs nothing; a group with no
    present affiliate renders exactly as it did before membership existed.

    `secrecy: gm-only` groups never appear (#49): this is a second prompt, built
    by a different module from the scene context, so the gate in
    `context.activate` does not reach it. Secret groups, their state and their
    affiliations render only in a separate block under `SECRET_GROUPS_PREFACE`.
    """
    snapshot = snapshot if snapshot is not None else membership.campaign_snapshot(cid)
    croot = campaigns_paths.campaign_root(cid)
    present = {ref for actor in cast
               if (ref := group_refs.actor_ref(str(actor).replace("/", ":", 1))) is not None}

    def line(roster: membership.Roster) -> str:
        try:
            st = groupstate.read_state(croot, roster.ref.partition(":")[2])
        except (OSError, ValueError):   # a garbled state file costs its state, not the absorb
            st = None
        parts = [f"{groupstate.LABELS[key]}: {st[key]}" for key in groupstate.FIELDS
                 if st and st.get(key, "").strip()] if st else []
        text = f"- {roster.ref.replace(':', '/', 1)} ({roster.name}): {' | '.join(parts) or '(no state)'}"
        here = _affiliates_here(cid, roster, present)
        return f"{text} | Affiliates here: {here}" if here else text

    rosters = list(snapshot.index.groups.values())
    public = [line(r) for r in rosters if membership.prompt_visible(r)]
    secret = [line(r) for r in rosters
              if not membership.prompt_visible(r) and membership.prompt_visible(r, secret_framed=True)]
    if secret:
        public += ["Secret groups:", SECRET_GROUPS_PREFACE, *secret]
    return "\n".join(public)


def steering_snapshot(cid: str, sid: str) -> str:
    """Rendered reroll-steering notes, oldest first — feeds the prompt so the
    model checks the lore the player had to correct mid-scene (the system
    prompt's "Player steering notes" contract: pointers to sharpen or extend
    records, never citable). "" for a scene with no log; `steering.texts` is
    already tolerant of a garbled file, so this cannot fail the extraction."""
    return "\n".join(f"- {t}" for t in steering.texts(cid, sid))


def state_snapshot(cid: str, sid: str) -> dict:
    """Present NPCs' existing standing snapshot — current_state with any Knows/Suspects
    folded in (via _snapshot_line) — keyed by display name (feeds the prompt).

    The present player characters' too: absorb now proposes a PC's current state,
    and rewriting the FULL snapshot means seeing the one that stands. A PC's file
    holds a current state only, so its line is just that."""
    aroot = appearances_paths.locked_actor_root(cid)   # cast actors are locked, so campaign-side
    out: dict[str, str] = {}
    for a in appearances_cast.scene_cast(cid, sid):
        if a["kind"] == "pcs":
            st = playstate.read_state(aroot, a["id"], "pcs")
            if st and st["current_state"]:
                out[a["name"]] = _snapshot_line({**st, "knows": "", "suspects": ""})
            continue
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
