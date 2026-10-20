"""Bounded candidate selection and record/scene projections for the adjudication payload, without transcripts."""

from __future__ import annotations

from typing import Any

from .... import prompts
from ... import chronicle, clock, embed_space, fieldtext, relationships
from .. import candidates, involvement, pending, pressure, similarity, tolerance
from . import _answers, _discovery, _readouts

#: Findings one sweep sends. Each renders two short records, a few beats and a
#: signal line, and the question and evidence wording repeats once per item, so
#: the overhead grows linearly with the count; `decide()` chunks them, which
#: bounds each call. To be tuned against real prompts later.
RECONCILE_MAX_CANDIDATES = 24

#: Beats sent per record: the identity text's latest plus two earlier ones
#: (§9.1), and the same beats the review detail shows. To be tuned against real
#: prompts later.
RECONCILE_BEATS = pending.ROW_BEATS

#: Recent chronicle one-lines sent beside the beat scenes, enough to place the
#: candidates in the story's present. To be tuned against real prompts later.
RECONCILE_RECENT_SCENES = 6

#: Actor names sent per record, so a long thread with a large cast cannot
#: dominate the one prompt. To be tuned against real prompts later.
RECONCILE_ACTORS = 4

#: Bytes of stored text one record shows -- its id, kind, status, title, due,
#: beats, link titles and actor names together. The counts above bound how
#: many of each are sent, not how long they are, and a ledger route takes a
#: title or beat of any length; this is the bound the identity decision puts on
#: the same records (`similarity.CONTINUITY_IDENTITY_BYTES`), so a pasted note
#: can neither push a chunk's call past the model's context nor, by failing it
#: on every pass, keep the other candidates in it from ever being decided. To
#: be tuned against real prompts later.
RECONCILE_RECORD_BYTES = similarity.CONTINUITY_IDENTITY_BYTES

#: Bytes of one chronicle one-line, which the absorb pass writes as a single
#: sentence but a reader may rewrite at any length (the Ledger's chronicle
#: edit, the review save). A long sentence fits even in a three-byte script. To be tuned against real prompts
#: later.
RECONCILE_SCENE_LINE_BYTES = 500
_LETTERS = ("A", "B")
#: A record's type as its prompt line names it, so a letter can never be read
#: as the other record (a cross pair stores the commitment first). An event's
#: line already begins ``event:``.
_TYPE_OF = {"thread": "plot thread", "commitment": "commitment"}


# ---------------------------------------------------------------- selection


def _number(value) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _count(value) -> int:
    return len(value) if isinstance(value, list) else 0


def _priority(item: dict) -> tuple:
    """Decision 13: overdue resolutions, then duplicates strongest first, then
    the rest by kind; the id breaks every tie."""
    kind, signals = item["kind"], item["signals"]
    if kind == "possible_commitment_resolution" and signals.get("reason") == "overdue":
        return (0, (), item["id"])
    if kind == "possible_duplicate":
        shared = sum(_count(signals.get(key))
                     for key in ("shared_actors", "shared_scenes", "shared_anchors"))
        return (1, (-bool(signals.get("title_exact")), -bool(signals.get("slug_equal")),
                    -shared, -_number(signals.get("lexical")),
                    -_number(signals.get("cosine"))), item["id"])
    return (2, (candidates.KINDS.index(kind),), item["id"])


def _item(key: str, record: dict) -> dict:
    return {"id": key, "kind": record["kind"], "refs": list(record["refs"]),
            "signals": dict(record["signals"]), "fingerprint": record["fingerprint"]}


def select(cid: str, sweep: _discovery.Sweep) -> list[dict]:
    """What the sweep's `decide()` is asked (§11.1 step 3, Decision 13), at most
    `RECONCILE_MAX_CANDIDATES`, by priority.

    Read from the cache as persist 1 left it: every record with no proposal
    whose verdict is ``live``. Then the sweep's model-only nominations that are
    not cached with a proposal, each re-checked against the `pending.Current`
    loaded here: a dismissal or a link that landed after discovery is honoured,
    so nothing suppressed, satisfied or gone is ever sent. With continuity.json
    malformed nothing is."""
    if sweep.continuity == "malformed":
        return []
    current = tolerance.call(pending.Current.load, None, cid)
    if current is None or current.continuity_malformed:
        return []
    stored = candidates.read(cid)["records"]
    chosen: dict[str, dict] = {}
    for key, record in stored.items():
        if record["proposal"] is None and tolerance.call(pending.verdict, "", current, record) == "live":
            chosen[key] = _item(key, record)
    for key, record in sweep.model_only.items():
        cached = stored.get(key)
        if key in chosen or (cached is not None and cached["proposal"] is not None):
            continue
        if tolerance.call(pending.verdict, "", current, record) == "live":
            chosen[key] = _item(key, record)
    return sorted(chosen.values(), key=_priority)[:RECONCILE_MAX_CANDIDATES]


# ------------------------------------------------------------------ payload


def snippet_line(ref: str, fields: dict) -> str:
    """One record's line as the prompt shows it. A thread or commitment is its
    `snippets/` absorb line with no latest beat (the beats follow it, each with
    its scene); `fields` is `pending.side`'s ``{title, status, kind, due}``. An
    event is ``event: <name> (<date>)`` from ``{title, due}``."""
    prefix, _, rid = ref.partition(":")
    if prefix == "thread":
        return prompts.render("snippets/plot_thread_line/absorb.j2",
                              t={"id": rid, "title": fields["title"], "status": fields["status"],
                                 "latest_beat": ""})
    if prefix == "commitment":
        return prompts.render("snippets/commitment_line/absorb.j2",
                              c={"id": rid, "title": fields["title"],
                                 "kind": fields["kind"] or "promise", "status": fields["status"],
                                 "due": fields["due"], "latest_beat": ""})
    return f"event: {fields['title']} ({fields['due']})" if fields["due"] else \
        f"event: {fields['title']}"


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _when(days) -> str:
    """``in 3 days`` / ``today`` / ``5 days ago``, or "" for no day."""
    if not isinstance(days, int) or isinstance(days, bool):
        return ""
    if days == 0:
        return "today"
    return f"in {_days(days)}" if days > 0 else f"{_days(-days)} ago"


def _pressure_text(reading: dict | None) -> str:
    """A record's deadline or staleness in words; "" when nothing presses."""
    if not reading or (reading.get("state") == "ok" and reading.get("in_days") is None):
        return ""
    state = str(reading.get("state") or "").replace("_", " ")
    when = _when(reading.get("in_days"))
    return f"{state}, {when}" if when else state


class _Context:
    """What `build_payload` reads once for every record it shows."""

    def __init__(self, cid: str, refs: list[str], live: list[str]):
        self.cid = cid
        self.live = frozenset(live)
        self.current: pending.Current = pending.Current.load(cid)
        self.pressure = _readouts.pressure_by_ref(cid)
        ledgered = [r for r in refs if not r.startswith("event:")]
        self.involved: dict[str, dict] = tolerance.call(involvement.of, {}, cid, ledgered)
        self._names: dict[str, str] = {}

    def title(self, ref: str) -> str:
        return pending.label(self.current, [ref])

    def name(self, actor: str) -> str:
        if actor not in self._names:
            fallback = actor.partition(":")[2] or actor
            self._names[actor] = tolerance.call(relationships.actor_name, fallback, self.cid, actor) \
                or fallback
        return self._names[actor]


def _beats(rec: dict) -> list[dict]:
    return [{"scene": fieldtext.text(b.get("scene")), "text": fieldtext.text(b.get("text"))}
            for b in (rec.get("beats") or [])[-RECONCILE_BEATS:] if isinstance(b, dict)]


def _record_view(ctx: _Context, letter: str, ref: str) -> dict:
    """One record as the prompt shows it (§11.2): its line, last beats with
    their scenes, pressure, effective links and involvement actors. An event
    carries a line only.

    The stored text it shows shares one `RECONCILE_RECORD_BYTES` budget
    (`similarity.clip_fields`), in priority: the id, kind, status, title and
    due its line is made of, then its beats newest first, then its links and
    actor names. A beat, link or name the budget no longer reaches is left out
    rather than shown blank. The reply names a record by its letter, never its
    id, so a cut id loses nothing `proposals_of` reads."""
    prefix, _, rid = ref.partition(":")
    view: dict[str, Any] = {"letter": letter, "ref": ref, "type": _TYPE_OF.get(prefix, ""),
                            "line": similarity.clip_fields([ref], RECONCILE_RECORD_BYTES)[0],
                            "beats": [], "pressure": "", "links": [], "actors": []}
    side = pending.side(ctx.current, ref)
    if side is None:
        return view
    rec = ctx.current.records.get(ref)          # None for an event
    newest: list[dict] = []
    links: list[str] = []
    names: list[str] = []
    if rec is not None:
        actors = ctx.involved.get(ref, {}).get("actors") or []
        newest = [{**b, "scene": b["scene"] if b["scene"] in ctx.live else ""}
                  for b in reversed(_beats(rec))]
        links = [f"{ctx.title(link['a'])} {link['relation']} {ctx.title(link['b'])}"
                 for link in ctx.current.links if ref in (link["a"], link["b"])]
        names = [ctx.name(a) for a in actors[:RECONCILE_ACTORS]]
    rid, kind, status, title, due, *rest = similarity.clip_fields(
        [rid, side["kind"], side["status"], side["title"], side["due"],
         *(b["text"] for b in newest), *links, *names], RECONCILE_RECORD_BYTES)
    texts, rest = rest[:len(newest)], rest[len(newest):]
    view["line"] = snippet_line(f"{prefix}:{rid}", {**side, "kind": kind, "status": status,
                                                     "title": title, "due": due})
    if rec is None:
        return view
    beats = [{**b, "text": text} for b, text in zip(newest, texts, strict=True)
             if text or not b["text"]]
    view.update(beats=beats[::-1], pressure=_pressure_text(ctx.pressure.get(ref)),
                links=[text for text in rest[:len(links)] if text],
                actors=[text for text in rest[len(links):] if text])
    return view


def _lifecycle_text(signals: dict) -> str:
    reason = signals.get("reason")
    if reason == "stale":
        days = signals.get("days_since")
        if isinstance(days, int) and not isinstance(days, bool):
            return f"no new beat in {_days(days)}"
        return "no new beat for a long while"
    if reason == "overdue":
        text = ("the event it is tied to has been reached"
                if signals.get("via") == "linked_deadline" else "its due date has passed")
        when = _when(signals.get("in_days"))
        return f"{text} ({when})" if when.endswith("ago") else text
    if reason == "touched":
        return "moved in the latest scene"
    if reason == "temporal":
        when = _when(signals.get("in_days"))
        return ("the commitment's due could not be placed on the calendar; the event is "
                + (when or "upcoming"))
    return ""


def _pair_text(ctx: _Context, signals: dict) -> str:
    """A pair's similarity signals as fixed-order phrases: why it was found,
    never a verdict (the system prompt says so)."""
    parts: list[str] = []
    if signals.get("title_exact"):
        parts.append("same title")
    if signals.get("slug_equal"):
        parts.append("same slug")
    for key, label in (("lexical", "word overlap"), ("cosine", "meaning")):
        if _number(signals.get(key)):
            parts.append(f"{label} {_number(signals.get(key)):.2f}")
    actors = [a for a in signals.get("shared_actors") or [] if isinstance(a, str)]
    if actors:
        parts.append("shared characters: " + ", ".join(ctx.name(a) for a in actors))
    if _count(signals.get("shared_scenes")):
        parts.append(f"shared scenes: {_count(signals.get('shared_scenes'))}")
    anchors = [a for a in signals.get("shared_anchors") or [] if isinstance(a, str)]
    if anchors:
        parts.append("shared dates: " + ", ".join(ctx.title(a) for a in anchors))
    return "; ".join(parts)


def _signal_text(ctx: _Context, item: dict) -> str:
    signals = item["signals"]
    if item["kind"] in candidates.LIFECYCLE_KINDS or signals.get("reason") == "temporal":
        return _lifecycle_text(signals)
    return _pair_text(ctx, signals)


def _campaign_date(cid: str) -> str:
    built = tolerance.call(pressure.build, None, cid, None, None, frozenset())
    if built is None:
        return tolerance.call(clock.now, "", cid)
    return built["friendly"] or built["now"] or ""


def _recent(live: list[str]) -> list[str]:
    """The last `RECONCILE_RECENT_SCENES` live scenes, in play order (none
    when the window is zero: ``live[-0:]`` would be every scene)."""
    return live[-RECONCILE_RECENT_SCENES:] if RECONCILE_RECENT_SCENES > 0 else []


def _scene_lines(cid: str, beat_scenes: set[str], live: list[str]) -> list[dict]:
    """Chronicle one-lines for every beat scene shown and the last
    `RECONCILE_RECENT_SCENES` live scenes, in play order, each cut to
    `RECONCILE_SCENE_LINE_BYTES`. Only scenes that
    exist are shown (`delete_scene` leaves the chronicle line behind), and a
    scene with no line is not."""
    read: Any = tolerance.call(chronicle.read_chronicle, {}, cid)
    chron: dict = read if isinstance(read, dict) else {}
    recent = set(_recent(live))
    out: list[dict] = []
    for sid in live:
        if sid not in beat_scenes and sid not in recent:
            continue
        rec = chron.get(sid)
        line = fieldtext.text(rec.get("one_line")).strip() if isinstance(rec, dict) else ""
        line = embed_space.clip(line, RECONCILE_SCENE_LINE_BYTES)
        if line:
            out.append({"id": sid, "one_line": line})
    return out


def build_payload(cid: str, selected: list[dict]) -> dict:
    """The sweep's bounded input (§11.2, Decision 13) for `select`'s items,
    keyed ``c1``... in selection order. Only the records the candidates name
    are sent, with their last beats and the chronicle lines around them --
    never a transcript -- and every stored text is cut to a byte bound
    (`_record_view`, `_scene_lines`; a signal line to `RECONCILE_RECORD_BYTES`),
    so the counts that bound the selection bound the prompt too. `known_scenes` (beat scenes and chronicle lines shown)
    is the only evidence `proposals_of` accepts (§11.4). Both come from the
    scenes that exist: a beat in a deleted scene is shown without its scene,
    so the sweep never accepts evidence persist 1 would void. `recent` is the
    recent window itself (`_recent`, in play order), which `item_scenes` reads
    to give each item the chronicle lines it shows."""
    live = _readouts._live_scenes(cid)
    ctx = _Context(cid, sorted({ref for item in selected for ref in item["refs"]}), live)
    out: list[dict] = []
    for n, item in enumerate(selected, 1):
        records = [_record_view(ctx, letter, ref) for letter, ref in zip(_LETTERS, item["refs"], strict=False)]
        out.append({"key": f"c{n}", "id": item["id"], "vocabulary": _answers.vocabulary(item),
                    "records": records,
                    "signal_text": embed_space.clip(_signal_text(ctx, item),
                                                    RECONCILE_RECORD_BYTES)})
    beat_scenes = {b["scene"] for c in out for r in c["records"] for b in r["beats"]} - {""}
    lines = _scene_lines(cid, beat_scenes, live)
    return {"now": _campaign_date(cid), "chronicle": lines, "candidates": out,
            "known_scenes": sorted(beat_scenes | {line["id"] for line in lines}),
            "recent": _recent(live)}
