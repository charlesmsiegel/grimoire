"""The readers behind an investigation's tools (spec 4.2, 4.3).

Each takes refs the model named and answers plain data, or raises
`NotFound` for a ref that is malformed, path-shaped, unknown or -- for a
gm-only body -- not to be read. A reader that fails any other way raises
whatever it raised, and the tool turns that into `unavailable`.

Two rules shape every reader here:

- **Scene-free and lock-free.** `casefile.build` and `timeline.build` take
  the full campaign lock and (the casefile) need the actor on stage, so
  `actor` and `timeline` re-read the same files themselves, as
  `continuity.graph` re-derives play order. The tool wrapper takes
  `best_effort_campaign_lock` around a call; nothing here takes a lock.
- **Transcript text is the prompt view over the whole transcript.** A window
  is viewed with its true offset and the transcript's length, so a
  depth-ranged rule applies where it applies in a prompt, and then hidden
  posts and director notes are removed. An excerpt is 08's `expand` in the
  prompt phase, which does both itself. `test_regex_prompt_guard` pins
  `window` and `excerpt` by name.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from .. import (
    calendars,
    chronicle,
    clock,
    dossiers,
    entities,
    events,
    facts,
    groupstate,
    knowledge,
    membership,
    overlay,
    paths,
    playstate,
    relationship_history,
    relationships,
)
from .. import search as store_search
from ..appearances import paths as appearances_paths
from ..campaigns import paths as campaigns_paths
from ..continuity import canon, effective, involvement, pressure
from ..regex import view as regex_view
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize
from ..searchdocs import expand as searchdocs_expand

T = TypeVar("T")

#: How many rows a history reader returns, newest last (spec 4.3). Argued from
#: the per-run result allowance (section 7); to be tuned against real prompts.
HISTORY_ROWS = 12

#: The entity kinds `get_record` reads (`overlay.read_entity`).
ENTITY_KINDS: tuple[str, ...] = entities.ENTITY_KINDS
#: The actor kinds `get_actor` reads.
ACTOR_KINDS: tuple[str, ...] = ("characters", "pcs")
#: The ledger kinds `get_record` reads.
LEDGER_KINDS: tuple[str, ...] = ("thread", "commitment", "fact", "event")

_ROLE_LABELS = {"user": "You", "assistant": "Grimoire"}


class NotFound(Exception):  # noqa: N818 - a tool's answer, not an error to log
    """A ref that names nothing this reader may return."""


def _soft(fn: Callable[..., T], fallback: T, *args: Any) -> T:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- one garbled side file empties its field, not the read
        return fallback


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


# ---- refs (rule 6) ----
def split(ref: object, kinds: tuple[str, ...]) -> tuple[str, str]:
    """`ref` as `(prefix, id)` when its prefix is one of `kinds` and its id a
    safe store id; `NotFound` for anything else -- a path, a second colon
    where none belongs, an unknown kind."""
    try:
        prefix, rid = canon.split_ref(ref)
    except ValueError:
        raise NotFound(ref) from None
    if prefix not in kinds or not paths.safe_id(rid):
        raise NotFound(ref)
    return prefix, rid


def _scene_rows(cid: str) -> list[dict]:
    """The campaign's scenes in play order: sorted ids, as the graph orders
    them (`continuity/graph.py`), never `list_scenes`' recency order. A scene
    a branch closed (`closed_by`, the sibling that was not kept) is no
    history: retrieval never admits one, and no reader returns one."""
    return sorted((r for r in scenes_read.list_scenes(cid) if not r.get("closed_by")),
                  key=lambda row: row["id"])


def scene_ids(cid: str) -> set[str]:
    """Every scene id the campaign holds now."""
    return {r["id"] for r in _scene_rows(cid)}


def scene(cid: str, ref: object) -> dict:
    """The scene `ref` (`scene:<sid>`) names, as its `list_scenes` row."""
    _, sid = split(ref, ("scene",))
    row = next((r for r in _scene_rows(cid) if r["id"] == sid), None)
    if row is None:
        raise NotFound(ref)
    return row


def _actor_name(cid: str, ref: str) -> str:
    return _soft(relationships.actor_name, ref.partition(":")[2], cid, ref) or ref


# ---- transcript text (rule 2) ----
def _label(m: dict) -> str:
    speaker = _text(m.get("speaker"))
    return speaker or _ROLE_LABELS.get(_text(m.get("role")), "Grimoire")


def window(cid: str, sid: str, *, before: int | None, limit: int) -> dict:
    """`limit` posts of scene `sid` ending before index `before` (None: the
    end), in the prompt view computed at the window's true depth, hidden
    posts and director notes removed. `{"posts": [(index, line)], "offset",
    "total", "has_older"}`; each line `[index] Speaker: text`."""
    read = scenes_read.read_scene_window(cid, sid, limit, before)
    offset, total = read["offset"], read["total"]
    viewed = regex_view.view(read["messages"], cid=cid, phase="prompt", offset=offset,
                             total=total)
    marked = [{**m, "_index": offset + i} for i, m in enumerate(viewed)]
    posts = [(m["_index"], f"[{m['_index']}] {_label(m)}: {_text(m.get('content')).strip()}")
             for m in scenes_serialize.in_context(marked)]
    return {"posts": posts, "offset": offset, "total": total, "has_older": read["has_older"]}


def excerpt(cid: str, sid: str, *, query: str, max_posts: int,
            max_bytes: int) -> searchdocs_expand.Expansion | None:
    """One excerpt of scene `sid` around `query`'s terms, through 08's `expand`
    in the prompt phase (08-C3b): `search.query_terms` capped at 08's
    `MAX_TERMS`, a window of `max_posts` posts centred on the match. None
    when the scene is gone or unreadable; an `empty` expansion when no term
    matched."""
    terms = searchdocs_expand.normalize_terms(store_search.query_terms(query))
    return searchdocs_expand.expand(cid, sid, phase="prompt", terms=terms,
                                    radius=max(0, (max_posts - 1) // 2), max_excerpts=1,
                                    max_posts=max_posts, max_bytes=max_bytes,
                                    fallback="none")


def summary(cid: str, sid: str) -> dict:
    """A scene's narrator-authored summary and its header facts: title, date,
    location and cast names, from the chronicle and the scene's frontmatter.
    No transcript is read."""
    meta = scenes_read.read_scene_meta(cid, sid)
    record = chronicle.get_record(cid, sid) or {}
    cast = [c for c in (record.get("cast") or []) if isinstance(c, str)]
    return {"title": _text(meta.get("title")) or sid,
            "date": _text(record.get("date")) or _text(meta.get("date")),
            "location": _text(record.get("location")) or _text(meta.get("place")),
            "cast": [_actor_name(cid, canon.actor_ref(c)) for c in cast],
            "summary": _text(record.get("summary")) or _text(record.get("one_line"))}


# ---- ledger records ----
def _ledger_record(cid: str, prefix: str, rid: str) -> dict:
    if prefix in ("thread", "commitment"):
        rec = effective.records(cid, prefix).get(f"{prefix}:{rid}")
        if rec is None:
            raise NotFound(f"{prefix}:{rid}")
        beats = rec.get("beats") or []
        latest = beats[-1] if beats and isinstance(beats[-1], dict) else {}
        out = {"kind": prefix, "title": _text(rec.get("title")),
               "status": _text(rec.get("status")),
               "latest_beat": _text(latest.get("text")) or _text(rec.get("latest_beat")),
               "latest_scene": _text(latest.get("scene")) or _text(rec.get("last_scene"))}
        if prefix == "commitment":
            out["due"] = _text(rec.get("due"))
        return out
    if prefix == "fact":
        rec = facts.get(cid, rid)
        if not isinstance(rec, dict):
            raise NotFound(f"fact:{rid}")
        return {"kind": "fact", "text": _text(rec.get("text")), "date": _text(rec.get("date")),
                "status": _text(rec.get("status")) or "active",
                "scene": _text(rec.get("scene"))}
    rec = events.get(cid, rid)
    if not isinstance(rec, dict):
        raise NotFound(f"event:{rid}")
    return {"kind": "event", "title": _text(rec.get("name")), "date": _text(rec.get("date")),
            "note": _text(rec.get("note"))}


def _entity(cid: str, kind: str, rid: str) -> dict:
    """An entity's name, kind and body -- the body never for a gm-only one
    (rule 3), whose name alone may be returned."""
    try:
        rec = overlay.read_entity(cid, kind, rid)
    except (entities.EntityNotFound, entities.UnknownKind):
        raise NotFound(f"{kind}:{rid}") from None
    meta = rec.get("meta") or {}
    secrecy = entities.normalize_secrecy(meta.get("secrecy"))
    out = {"kind": kind, "name": _text(meta.get("name")) or rid, "secrecy": secrecy}
    if secrecy != entities.GM_ONLY:
        out["body"] = _text(rec.get("body")).strip()
    return out


def record(cid: str, ref: object) -> dict:
    """A thread's or commitment's title, status and latest beat; a fact; an
    event; or an entity (rule 3 for its body)."""
    prefix, rid = split(ref, LEDGER_KINDS + ENTITY_KINDS)
    if prefix in LEDGER_KINDS:
        return _ledger_record(cid, prefix, rid)
    return _entity(cid, prefix, rid)


def _fact_chain(cid: str, rid: str) -> list[dict]:
    """The supersession chain through `fact:<rid>`, oldest first: the facts it
    superseded, itself, and what superseded it."""
    table = facts.read(cid)
    if not isinstance(table, dict) or not isinstance(table.get(rid), dict):
        raise NotFound(f"fact:{rid}")
    before = {r.get("superseded_by"): fid for fid, r in table.items()
              if isinstance(r, dict) and r.get("superseded_by")}
    chain = [rid]
    while before.get(chain[0]) and before[chain[0]] not in chain:
        chain.insert(0, before[chain[0]])
    while True:
        nxt = _text((table.get(chain[-1]) or {}).get("superseded_by"))
        if not nxt or nxt in chain or not isinstance(table.get(nxt), dict):
            break
        chain.append(nxt)
    return [{"id": f"fact:{fid}", "text": _text(table[fid].get("text")),
             "date": _text(table[fid].get("date")),
             "status": _text(table[fid].get("status")) or "active",
             "scene": _text(table[fid].get("scene"))} for fid in chain]


def record_history(cid: str, ref: object) -> list[dict]:
    """The latest `HISTORY_ROWS` rows of a record's history: a thread's or
    commitment's beats in play order, a fact's supersession chain, or a
    pair's relationship history (`pair:<a>+<b>`, each side an actor ref)."""
    if isinstance(ref, str) and ref.startswith("pair:"):
        a, sep, b = ref[len("pair:"):].partition("+")
        if not sep:
            raise NotFound(ref)
        sides = [split(a, ACTOR_KINDS), split(b, ACTOR_KINDS)]
        if not all(_exists(cid, k, i) for k, i in sides):
            # A side no roster holds is no pair, not one with nothing recorded.
            raise NotFound(ref)
        a_ref, b_ref = (f"{k}:{i}" for k, i in sides)
        rows = relationship_history.for_pair(cid, a_ref, b_ref)
        return [{"scene": _text(r.get("scene")), "kind": _text(r.get("kind")),
                 "label": _text(r.get("label")), "before": r.get("before"),
                 "after": r.get("after")}
                for r in rows if isinstance(r, dict)][-HISTORY_ROWS:]
    prefix, rid = split(ref, ("thread", "commitment", "fact"))
    if prefix == "fact":
        return _fact_chain(cid, rid)[-HISTORY_ROWS:]
    rec = effective.records(cid, prefix).get(f"{prefix}:{rid}")
    if rec is None:
        raise NotFound(ref)
    beats = effective.play_ordered([b for b in rec.get("beats") or [] if isinstance(b, dict)])
    return [{"scene": _text(b.get("scene")), "text": _text(b.get("text"))}
            for b in beats][-HISTORY_ROWS:]


def related(cid: str, ref: object) -> dict:
    """A record's reviewed links and the scenes and actors that touched it."""
    prefix, rid = split(ref, ("thread", "commitment", "event"))
    full = f"{prefix}:{rid}"
    if prefix == "event":
        if events.get(cid, rid) is None:
            raise NotFound(full)
    elif full not in effective.records(cid, prefix):
        raise NotFound(full)
    links = [{"a": link["a"], "relation": link["relation"], "b": link["b"]}
             for link in effective.links(cid) if full in (link.get("a"), link.get("b"))]
    touched = involvement.of(cid, [full]).get(full, {}) if prefix != "event" else {}
    return {"links": links[-HISTORY_ROWS:],
            "scenes": list(touched.get("scenes") or [])[-HISTORY_ROWS:],
            "actors": [_actor_name(cid, a) for a in touched.get("actors") or []][:HISTORY_ROWS]}


# ---- campaign-wide readers ----
def _provider(cid: str):
    return _soft(calendars.primary_provider, None, campaigns_paths.campaign_root(cid))


def _between(rows: list[dict], frm: str | None, to: str | None) -> list[dict]:
    ids = [r["id"] for r in rows]
    start = ids.index(frm) if frm in ids else 0
    end = ids.index(to) + 1 if to in ids else len(ids)
    return rows[start:end]


def timeline(cid: str, frm: object = None, to: object = None) -> dict:
    """Scenes in play order between two scene refs (each optional, inclusive),
    each with its date and one-line summary, and the campaign's dated events
    rendered by its calendar -- never date arithmetic of our own (capstone
    §3.8). Read without `timeline.build`'s full lock (1.2)."""
    bounds = [None if r is None else split(r, ("scene",))[1] for r in (frm, to)]
    rows = _scene_rows(cid)
    for bound in bounds:
        if bound is not None and bound not in {r["id"] for r in rows}:
            raise NotFound(f"scene:{bound}")
    chron: dict = _soft(chronicle.read_chronicle, {}, cid)
    scenes = []
    for row in _between(rows, *bounds):
        rec = chron.get(row["id"]) if isinstance(chron, dict) else None
        rec = rec if isinstance(rec, dict) else {}
        scenes.append({"scene": f"scene:{row['id']}", "title": _text(row.get("title")),
                       "date": _text(rec.get("date")) or _text(row.get("date")),
                       "one_line": _text(rec.get("one_line"))})
    none: list[dict] = []
    dated: list[dict] = _soft(events.list_events, none, cid, _provider(cid))
    return {"now": _soft(clock.now, "", cid), "scenes": scenes,
            "events": [{"event": f"event:{e['id']}", "title": _text(e.get("name")),
                        "date": _text(e.get("friendly")) or _text(e.get("date")),
                        "fired": bool(e.get("fired"))} for e in dated]}


def pressure_items(cid: str) -> dict:
    """Deadlines and staleness as the sweep sees them (`pressure.build`)."""
    built = pressure.build(cid)
    return {"now": _text(built.get("friendly")) or _text(built.get("now")),
            "items": [{"ref": _text(i.get("ref")), "kind": _text(i.get("kind")),
                       "label": _text(i.get("label")), "when": _text(i.get("friendly")),
                       "state": _text(i.get("state")), "due": _text(i.get("due_text"))}
                      for i in built.get("items") or [] if isinstance(i, dict)]}


def _exists(cid: str, kind: str, aid: str) -> bool:
    roster = overlay.pc_roster(cid) if kind == "pcs" else overlay.character_roster(cid)
    return any(r.get("id") == aid for r in roster)


def actor(cid: str, ref: object) -> dict:
    """An actor's play state, dossier, outgoing feelings and the knowledge
    overrides naming it (status and subject kind only, never the note) --
    scene-free, unlike `casefile.build` (1.2)."""
    kind, aid = split(ref, ACTOR_KINDS)
    if not _exists(cid, kind, aid):
        raise NotFound(ref)
    full = f"{kind}:{aid}"
    state = _soft(playstate.read_state, None, appearances_paths.locked_actor_root(cid),
                  aid, kind) or {}
    feelings: dict = _soft(lambda: relationships.read(cid)["feelings"], {})
    toward = []
    for key, f in (feelings.items() if isinstance(feelings, dict) else ()):
        a, _, b = key.partition("->")
        if a == full and isinstance(f, dict):
            toward.append({"toward": _actor_name(cid, b), **{k: f.get(k) for k in
                                                            ("trust", "affection", "tension")},
                           "note": _text(f.get("note"))})
    entries: dict[str, dict] = {}
    entries = _soft(knowledge.read, entries, cid)
    overrides = [{"status": e.get("status"),
                  "subject": _text((e.get("subject") or {}).get("kind"))}
                 for e in entries.values()
                 if isinstance(e, dict) and e.get("audience") == full]
    return {"name": _actor_name(cid, full),
            "current_state": _text(state.get("current_state")),
            "knows": _text(state.get("knows")) if not isinstance(state.get("knows"), list)
            else state.get("knows"),
            "suspects": _text(state.get("suspects")) if not isinstance(state.get("suspects"), list)
            else state.get("suspects"),
            "dossier": _soft(dossiers.read, "", campaigns_paths.campaign_root(cid), aid)
            if kind == "characters" else "",
            "feelings": toward[:HISTORY_ROWS], "overrides": overrides[:HISTORY_ROWS]}


def group(cid: str, ref: object, *, secrets: bool) -> dict:
    """A group's entity (rule 3 for its body), its members and leader (07),
    and its state -- a secret group's state, and any group's `secrets`, only
    when `secrets` (the maintenance modes, spec 4.3)."""
    _, gid = split(ref, ("groups",))
    out = _entity(cid, "groups", gid)
    roster = membership.campaign_index(cid).groups.get(f"groups:{gid}")
    if roster is not None:
        out["leader"] = _actor_name(cid, roster.leader) if roster.leader else ""
        out["members"] = [_actor_name(cid, m) for m in roster.members][:HISTORY_ROWS]
    if out["secrecy"] == entities.PUBLIC or secrets:
        state = _soft(groupstate.read_state, None, campaigns_paths.campaign_root(cid), gid) or {}
        out["state"] = {k: v for k, v in state.items()
                        if k != "updated" and (secrets or k != "secrets")}
    return out

