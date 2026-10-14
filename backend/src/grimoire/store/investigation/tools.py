"""An investigation's tools (spec 4.1-4.3; 12-C1): 01g `ToolSpec`s in one
`Toolset` per mode, each answering a JSON envelope.

    {"ok": true, "items": [{"id", "ref", "title", "text", "truncated"}]}
    {"ok": false, "error": "not_found" | "not_in_mode" | "bad_args" | "unavailable"}

Errors are results, never exceptions: the loop goes on and the step is
traced. `not_found` is also the answer for a ref this perspective may not
read, and a search that finds nothing visible answers exactly as a search
over nothing -- a distinct `withheld` would let the model probe for what it
may not see.

Every reading tool takes `best_effort_campaign_lock` for its own reads
(never the full lock, and none across a model call) -- `search_history`
for the query it builds, while 09's `retrieve` gathers its signals outside
any campaign hold by design, as a turn's recall does -- clips its text to
`TOOL_RESULT_CHARS` at a post or line boundary saying so in `truncated`, and
notes a trace step (`trace.step`) on the run's state. A terminal tool is
never executed by the loop -- the call is handed back for `final` to check --
so its function only refuses.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import anyio

from ... import tool_calls
from .. import locks
from ..history import budget as history_budget
from ..history import model as history_model
from ..history import query as history_query
from ..history import retrieval as history_retrieval
from ..scenes import identity as scenes_identity
from . import readers, state, trace

log = logging.getLogger(__name__)

#: The most characters one tool result carries (01g's `max_result_chars`).
#: Argued from the per-run totals of section 7; to be tuned against real
#: prompts.
TOOL_RESULT_CHARS = 6000
#: The longest query a search or excerpt takes.
QUERY_CHARS = 200
#: The most scenes one `search_history` returns.
SEARCH_MAX = 6
#: The most posts one `get_scene_excerpt` returns.
EXCERPT_POSTS = 8
#: The most posts one `read_scene_window` returns.
WINDOW_POSTS = 12
#: How long `search_history` waits on 09's semantic stage, at most.
SEARCH_SECONDS = 8.0
#: 09's rendered-history ceiling for one search, in tokens: the result's
#: character cap at the packer's four characters a token.
SEARCH_TOKENS = TOOL_RESULT_CHARS // 4

ERRORS: tuple[str, ...] = ("not_found", "not_in_mode", "bad_args", "unavailable")

#: The tools whose result is a scene's own text: what `inspected` -- the
#: evidence floor -- is made of (spec 6.2). Each names the scene in its
#: result's refs only when it returned that scene's text.
READS: frozenset[str] = frozenset({"get_scene_summary", "get_scene_excerpt",
                                   "read_scene_window"})

#: The terminal tools, by name (spec 4.3).
FINISH_EVIDENCE = "finish_evidence"
PROPOSE = "propose"
ANSWER = "answer"
FINISH_NO_CHANGE = "finish_no_change"
FINISH_INSUFFICIENT = "finish_insufficient_evidence"


class BadArgs(Exception):  # noqa: N818 - a tool's answer, not an error to log
    """Arguments the schema let through that the tool cannot take."""


# ---- the envelope ----
def envelope_ok(items: list[dict]) -> str:
    """The success envelope, held to `TOOL_RESULT_CHARS`: the longest item's
    text is cut at a line boundary, `truncated` set, until it fits, and items
    are dropped from the end only when no text is left to cut."""
    items = [dict(i) for i in items]
    for _ in range(64):
        text = json.dumps({"ok": True, "items": items}, ensure_ascii=False)
        excess = len(text) - TOOL_RESULT_CHARS
        if excess <= 0:
            return text
        longest = max(items, key=lambda i: len(i["text"]), default=None)
        if longest is None or not longest["text"]:
            items.pop()
            continue
        longest["text"] = _cut(longest["text"], max(0, len(longest["text"]) - excess - 64))
        longest["truncated"] = True
    return json.dumps({"ok": True, "items": []}, ensure_ascii=False)


def envelope_error(code: str) -> str:
    if code not in ERRORS:
        raise ValueError(f"not a tool error: {code!r}")
    return json.dumps({"ok": False, "error": code})


def _cut(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters at the last line boundary
    inside it (mid-line only when the first line alone is longer)."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    at = head.rfind("\n")
    return head[:at] if at > 0 else head


#: The longest title an item carries: a scene's title is unbounded where it
#: is set, and an item's metadata must leave room for its text.
TITLE_CHARS = 200


def item(id_: str, ref: str, title: str, text: str, truncated: bool = False) -> dict:
    return {"id": id_, "ref": ref, "title": title[:TITLE_CHARS], "text": text,
            "truncated": truncated}


def _lines(payload: Any, indent: str = "") -> str:
    """A reader's dict or list as plain `key: value` lines -- text a model
    reads, without a JSON string inside the JSON envelope."""
    out: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                if value:
                    out.append(f"{indent}{key}:")
                    out.append(_lines(value, indent + "  "))
            elif value not in (None, ""):
                out.append(f"{indent}{key}: {value}")
    elif isinstance(payload, list):
        for value in payload:
            if isinstance(value, (dict, list)):
                out.append(f"{indent}-")
                out.append(_lines(value, indent + "  "))
            else:
                out.append(f"{indent}- {value}")
    else:
        out.append(f"{indent}{payload}")
    return "\n".join(line for line in out if line)


# ---- a tool's answer, and its trace step ----
@dataclass(frozen=True)
class Answer:
    """What a tool body found: its items, the refs it inspected, and the
    scenes whose text it returned (the run's `inspected`)."""
    items: list[dict]
    refs: tuple[str, ...] = ()
    read: tuple[str, ...] = ()


def _args_for_trace(args: dict) -> dict:
    out: dict = {}
    for key, value in args.items():
        if isinstance(value, str):
            out[key] = value[:QUERY_CHARS]
        elif isinstance(value, (int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list):
            out[key] = [v[:QUERY_CHARS] if isinstance(v, str) else v for v in value][:8]
    return out


def _settled(run: state.RunState, name: str, args: dict, ctx: tool_calls.ToolContext,
             body: Callable[[], Answer]) -> tool_calls.ToolOutput:
    """`body` answered as an envelope, its failures as error results, and
    its step noted on the run."""
    found: Answer | None = None
    try:
        found = body()
    except readers.NotFound:
        text = envelope_error("not_found")
    except BadArgs:
        text = envelope_error("bad_args")
    except Exception as exc:  # noqa: BLE001 -- a reader failing is the model's to hear
        log.warning("investigation tool %s failed in run %s: %s", name, ctx.run_id,
                    type(exc).__name__)
        text = envelope_error("unavailable")
    else:
        text = envelope_ok(found.items)
    delivered = json.loads(text).get("items", ())
    sent = {i.get("ref") for i in delivered}
    # `envelope_ok` drops an item whole only when nothing of it fits. Every
    # item delivered keeps its refs -- an aggregate item (`timeline`,
    # `pressure`) names the scenes or records its text was built from -- and
    # a dropped scene item's are dropped with it: the model saw none of it.
    dropped = found is not None and len(delivered) < len(found.items)
    if found is not None:
        for sid in found.read:
            if not dropped or f"scene:{sid}" in sent:
                run.inspect(sid)
    ok = found is not None
    truncated = ok and any(i.get("truncated") for i in delivered)
    refs = (tuple(r for r in found.refs if not dropped or r in sent)
            if found is not None else ())
    run.note(trace.step(ctx.turn, name, _args_for_trace(args), ok=ok, refs=refs,
                        chars=len(text), truncated=truncated,
                        error="" if ok else json.loads(text)["error"]))
    return tool_calls.ToolOutput(text, refs=refs)


def _sync(run: state.RunState, name: str, body: Callable[[dict], Answer]):
    def fn(args: dict, ctx: tool_calls.ToolContext) -> tool_calls.ToolOutput:
        with locks.best_effort_campaign_lock(run.cid):
            return _settled(run, name, args, ctx, lambda: body(args))
    return fn


# ---- argument helpers ----
def _query(args: dict) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip() or len(query) > QUERY_CHARS:
        raise BadArgs("query")
    return query.strip()


def _count(args: dict, key: str, default: int, most: int) -> int:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= most:
        raise BadArgs(key)
    return value


def _scene_ref(sid: str) -> str:
    return f"scene:{sid}"


def _scene_ref_of(item: history_model.EvidenceItem) -> str:
    return _scene_ref(item.scene.sid)


# ---- the reading tools ----
def _search(run: state.RunState):
    """`search_history` (spec 4.3): 09's retrieval over the whole campaign,
    for the run's perspective, pinned to this campaign (`tier_limit=2`, rule
    8). Async: retrieval runs its file stages on history's own threads.

    The query is built under `best_effort_campaign_lock`, like every other
    reading tool's reads. `retrieve` itself is not: it is 09's contract that
    it gathers outside campaign holds (a turn's recall calls it the same
    way), each stage reading files the store writes atomically, and a
    thread lock held across its awaits would be owned by whichever worker
    took it. Rendering reads no campaign file.

    Under a spend ceiling the search is lexical and structural only: an
    embedding is metered apart from the loop, so its cost is neither in
    the price check a run passes before it starts nor in the room the loop
    projects each turn against -- and a search the ceiling cannot see could
    carry a run past it. With no ceiling the run's embeddings client is
    used, and its rows reach the trace (`routes.investigation._filed`)."""

    def query_for(text: str, most: int) -> history_model.Query:
        with locks.best_effort_campaign_lock(run.cid):
            return replace(history_query.for_text(run.cid, text, perspective=run.perspective),
                           tier_limit=2, max_scenes=most)

    async def fn(args: dict, ctx: tool_calls.ToolContext) -> tool_calls.ToolOutput:
        try:
            query, most = _query(args), _count(args, "max", 4, SEARCH_MAX)
        except BadArgs:
            return _settled(run, "search_history", args, ctx, _raise(BadArgs()))
        try:
            q = await anyio.to_thread.run_sync(query_for, query, most)
            ceilinged = ctx.run is not None and ctx.run.room_usd() is not None
            evidence = await history_retrieval.retrieve(
                run.cid, run.sid, q, ceiling=SEARCH_TOKENS,
                embed_client=None if ceilinged else run.embed_client,
                deadline=min(ctx.deadline, time.monotonic() + SEARCH_SECONDS),
                origin="investigation", run_id=ctx.run_id, depth=most)
            rendered = await anyio.to_thread.run_sync(
                lambda: [(i, history_budget.render_item(i)) for i in evidence.items])
        except Exception as exc:  # noqa: BLE001 -- see `_settled`
            failure = exc
            return _settled(run, "search_history", args, ctx, _raise(failure))

        def answer() -> Answer:
            items = [item(run.hold(i), _scene_ref_of(i), i.title, text)
                     for i, text in rendered]
            return Answer(items, refs=tuple(dict.fromkeys(_scene_ref_of(i)
                                                          for i, _ in rendered)))
        return _settled(run, "search_history", args, ctx, answer)

    return fn


def _raise(exc: BaseException) -> Callable[[], Answer]:
    def body() -> Answer:
        raise exc
    return body


def _summary(run: state.RunState, args: dict) -> Answer:
    row = readers.scene(run.cid, args.get("scene"))
    sid = row["id"]
    found = readers.summary(run.cid, sid)
    if not found["summary"]:
        return Answer([])
    held = history_model.EvidenceItem(
        history_model.SceneRef(sid, scenes_identity.scene_identity(run.cid, sid)),
        found["title"], found["date"], found["location"], found["summary"])
    text = _lines({k: v for k, v in found.items() if k != "title"})
    return Answer([item(run.hold(held), _scene_ref(sid), found["title"], text)],
                  refs=(_scene_ref(sid),), read=(sid,))


def _excerpt(run: state.RunState, args: dict) -> Answer:
    query = _query(args)
    posts = _count(args, "max_posts", 6, EXCERPT_POSTS)
    row = readers.scene(run.cid, args.get("scene"))
    sid = row["id"]
    expansion = readers.excerpt(run.cid, sid, query=query, max_posts=posts,
                                max_bytes=TOOL_RESULT_CHARS)
    if expansion is None:
        raise readers.NotFound(args.get("scene"))
    if not expansion.excerpts:
        return Answer([])
    found = expansion.excerpts[0]
    held = history_model.EvidenceItem(
        history_model.SceneRef(sid, expansion.identity or None),
        str(row.get("title") or sid),
        excerpts=(history_model.Excerpt(tuple(
            history_model.EvidencePost(p.index, p.key, p.part, p.speaker, p.content)
            for p in found.posts), found.text),))
    clipped = any(p.clipped for p in found.posts) or expansion.budget_exhausted
    return Answer([item(run.hold(held), _scene_ref(sid), held.title, found.text, clipped)],
                  refs=(_scene_ref(sid),), read=(sid,))


def _window(run: state.RunState, args: dict) -> Answer:
    row = readers.scene(run.cid, args.get("scene"))
    sid = row["id"]
    limit = _count(args, "limit", WINDOW_POSTS, WINDOW_POSTS)
    before = args.get("before")
    if before is not None and (isinstance(before, bool) or not isinstance(before, int)
                               or before < 0):
        raise BadArgs("before")
    found = readers.window(run.cid, sid, before=before, limit=limit)
    lines = [line for _, line in found["posts"]]
    budget = TOOL_RESULT_CHARS - 400
    kept: list[str] = []
    clipped = False
    for line in reversed(lines):
        if sum(len(x) + 1 for x in kept) + len(line) + 1 > budget:
            if not kept:
                # One post longer than the whole result: its head, cut, rather
                # than a page that skips it and can never show it.
                kept, clipped = [line[:budget - 20] + " [post cut short]"], True
            break
        kept.insert(0, line)
    truncated = clipped or len(kept) < len(lines)
    if kept:
        first = found["posts"][len(lines) - len(kept)][0]
        head = f"posts {first}-{found['posts'][-1][0]} of {found['total']}"
    else:
        first = found["offset"]
        head = f"no posts to show before {first + limit} of {found['total']}"
    if first > 0:
        head += f"; earlier posts exist: call again with before={first}"
    text = "\n".join([head, *kept])
    return Answer([item(f"w{sid}@{first}", _scene_ref(sid), str(row.get("title") or sid), text,
                        truncated)],
                  refs=(_scene_ref(sid),) if kept else (), read=(sid,) if kept else ())


def _record(run: state.RunState, args: dict) -> Answer:
    ref = args.get("ref")
    found = readers.record(run.cid, ref)
    title = str(found.get("title") or found.get("name") or found.get("text", ""))[:120]
    return Answer([item(str(ref), str(ref), title, _lines(found))], refs=(str(ref),))


def _history(run: state.RunState, args: dict) -> Answer:
    ref = args.get("ref")
    rows = readers.record_history(run.cid, ref)
    return Answer([item(str(ref), str(ref), str(ref), _lines(rows))], refs=(str(ref),))


def _related(run: state.RunState, args: dict) -> Answer:
    ref = args.get("ref")
    found = readers.related(run.cid, ref)
    return Answer([item(str(ref), str(ref), str(ref), _lines(found))], refs=(str(ref),))


def _timeline(run: state.RunState, args: dict) -> Answer:
    found = readers.timeline(run.cid, args.get("from"), args.get("to"))
    return Answer([item("timeline", "timeline", "Timeline", _lines(found))],
                  refs=tuple(s["scene"] for s in found["scenes"]))


def _pressure(run: state.RunState, args: dict) -> Answer:
    found = readers.pressure_items(run.cid)
    return Answer([item("pressure", "pressure", "Pressure", _lines(found))],
                  refs=tuple(i["ref"] for i in found["items"] if i["ref"]))


def _actor(run: state.RunState, args: dict) -> Answer:
    ref = args.get("ref")
    found = readers.actor(run.cid, ref)
    return Answer([item(str(ref), str(ref), found["name"], _lines(found))], refs=(str(ref),))


def _group(run: state.RunState, args: dict) -> Answer:
    ref = args.get("ref")
    found = readers.group(run.cid, ref, secrets=run.mode in ("continuity", "question"))
    return Answer([item(str(ref), str(ref), found["name"], _lines(found))], refs=(str(ref),))


# ---- schemas ----
def _obj(**props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


_STR = {"type": "string"}
_NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_REFS = {"type": "array", "items": {"type": "string"}}


def _desc(schema: dict, text: str) -> dict:
    return {**schema, "description": text}


@dataclass(frozen=True)
class _Def:
    name: str
    description: str
    parameters: dict
    modes: tuple[str, ...]
    body: Callable[[state.RunState, dict], Answer] | None = None
    terminal: bool = False
    effect: str = "read"


_N, _C, _Q = "rp_narrator", "continuity", "question"

DEFINITIONS: tuple[_Def, ...] = (
    _Def("search_history",
         "Search this campaign's earlier scenes for a topic. Returns matching scenes with "
         "their header, summary and the most relevant posts. Each result has an evidence id.",
         _obj(query=_desc(_STR, f"what to look for, at most {QUERY_CHARS} characters"),
              max=_desc(_NULLABLE_INT, f"how many scenes, 1-{SEARCH_MAX} (default 4)")),
         (_N, _C, _Q)),
    _Def("get_scene_summary", "A scene's summary, date, location and cast.",
         _obj(scene=_desc(_STR, "a scene ref, scene:<id>")), (_N, _C, _Q), _summary),
    _Def("get_scene_excerpt",
         "The posts of one scene around the words of a query, as the story reads them.",
         _obj(scene=_desc(_STR, "a scene ref, scene:<id>"),
              query=_desc(_STR, f"words to find, at most {QUERY_CHARS} characters"),
              max_posts=_desc(_NULLABLE_INT, f"posts to return, 1-{EXCERPT_POSTS} (default 6)")),
         (_N, _C, _Q), _excerpt),
    _Def("read_scene_window",
         "Read a scene's transcript page by page, newest first: up to "
         f"{WINDOW_POSTS} posts ending before a post index. Expensive: prefer an excerpt.",
         _obj(scene=_desc(_STR, "a scene ref, scene:<id>"),
              before=_desc(_NULLABLE_INT, "the post index the page ends before; null for the end"),
              limit=_desc(_NULLABLE_INT, f"posts to return, 1-{WINDOW_POSTS}")),
         (_C, _Q), _window),
    _Def("get_record",
         "A thread's or commitment's title, status and latest beat; a fact; an event; or a "
         "lore, location, item, group or creature entry.",
         _obj(ref=_desc(_STR, "thread:<id>, commitment:<id>, fact:<id>, event:<id>, "
                              "lore:<id>, locations:<id>, items:<id>, groups:<id> or "
                              "creatures:<id>")),
         (_N, _C, _Q), _record),
    _Def("get_record_history",
         "A thread's or commitment's beats, a fact's supersession chain, or the "
         "relationship history of a pair (pair:<actor ref>+<actor ref>).",
         _obj(ref=_desc(_STR, "thread:<id>, commitment:<id>, fact:<id> or "
                              "pair:characters:<id>+characters:<id>")),
         (_N, _C, _Q), _history),
    _Def("get_related", "A record's reviewed links, and the scenes and characters that touched it.",
         _obj(ref=_desc(_STR, "thread:<id>, commitment:<id> or event:<id>")),
         (_N, _C, _Q), _related),
    _Def("get_timeline",
         "Scenes in play order with their dates and one-line summaries, and the dated events.",
         _obj(**{"from": _desc(_NULLABLE_STR, "the first scene ref, or null"),
                 "to": _desc(_NULLABLE_STR, "the last scene ref, or null")}),
         (_N, _C, _Q), _timeline),
    _Def("get_pressure", "Deadlines that are near or past, and threads gone quiet.",
         _obj(), (_C,), _pressure),
    _Def("get_actor",
         "A character's current state, what they know and suspect, their dossier and feelings.",
         _obj(ref=_desc(_STR, "characters:<id> or pcs:<id>")), (_N, _C, _Q), _actor),
    _Def("get_group", "A group's entry, members, leader and state.",
         _obj(ref=_desc(_STR, "groups:<id>")), (_N, _C, _Q), _group),
    _Def(FINISH_EVIDENCE,
         "Finish: name the evidence ids, from this run's searches, summaries and excerpts, "
         "that bear on the scene.",
         _obj(ids=_desc(_REFS, "at most 6 evidence ids"),
              note=_desc(_STR, "at most 200 characters, for the record only")),
         (_N,), terminal=True),
    _Def(PROPOSE,
         "Finish with one decision for the finding, citing the scenes you read that show it.",
         _obj(decision=_desc(_STR, "one of the finding's decisions"),
              **{"from": _desc(_STR, "A or B, where the decision has a direction; else \"\""),
                 "to": _desc(_STR, "A or B, where the decision has a direction; else \"\"")},
              relation=_desc(_STR, "the relation word, or \"\""),
              status=_desc(_STR, "the status word, or \"\""),
              evidence_scenes=_desc(_REFS, "at most 3 scene refs you read"),
              reason=_desc(_STR, "at most 280 characters")),
         (_C,), terminal=True, effect="propose"),
    _Def(ANSWER, "Finish with the answer, citing the refs you read that support it.",
         _obj(text=_desc(_STR, "the answer, at most 1200 characters"),
              citations=_desc(_REFS, "at most 6 refs you read")),
         (_Q,), terminal=True),
    _Def(FINISH_NO_CHANGE, "Finish: the finding needs no change.",
         _obj(reason=_desc(_STR, "at most 280 characters")), (_C,), terminal=True),
    _Def(FINISH_INSUFFICIENT,
         "Finish: what you could read is not enough to answer.",
         _obj(reason=_desc(_STR, "at most 280 characters")), (_N, _C, _Q), terminal=True),
)

#: Each mode's terminal tools.
TERMINAL: dict[str, frozenset[str]] = {
    mode: frozenset(d.name for d in DEFINITIONS if d.terminal and mode in d.modes)
    for mode in state.MODES}


def _terminal_fn(name: str):
    def fn(args: dict, ctx: tool_calls.ToolContext) -> tool_calls.ToolOutput:
        # The loop hands a terminal call back unexecuted; reaching here is a bug.
        raise tool_calls.ToolError(f"{name} ends the run and is never executed")
    return fn


def names(mode: str) -> tuple[str, ...]:
    """The tools a mode offers, in order (not counting `decide`)."""
    return tuple(d.name for d in DEFINITIONS if mode in d.modes)


def toolset(run: state.RunState, *, extra: tuple[tool_calls.ToolSpec, ...] = ()
            ) -> tool_calls.Toolset:
    """The run's mode's tools, bound to `run`, with `extra` (the decide tool,
    12-S4) after the readers."""
    specs: list[tool_calls.ToolSpec] = []
    for d in DEFINITIONS:
        if run.mode not in d.modes:
            continue
        if d.terminal:
            fn: Any = _terminal_fn(d.name)
        elif d.name == "search_history":
            fn = _search(run)
        else:
            assert d.body is not None
            fn = _sync(run, d.name, _bound(run, d.body))
        specs.append(tool_calls.ToolSpec(
            d.name, d.description, d.parameters, fn, effect=d.effect,  # type: ignore[arg-type]
            terminal=d.terminal, max_result_chars=TOOL_RESULT_CHARS))
    readers_end = sum(1 for s in specs if not s.terminal)
    return tool_calls.Toolset(tuple(specs[:readers_end]) + tuple(extra)
                              + tuple(specs[readers_end:]))


def _bound(run: state.RunState, body: Callable[[state.RunState, dict], Answer]
           ) -> Callable[[dict], Answer]:
    return lambda args: body(run, args)
