"""The scene SearchDocument (roadmap 08-C1): bounded text, metadata for
structural prefiltering, and a key over the scene's slices.

`render` is a pure function of the slices it is handed: it reads nothing. The
text is a fixed sequence of labelled lines, an empty one omitted; each item is
byte-clipped to its cap (a suffix or separator inside an item counts within
it), items in a line are joined with "; ", and the line caps sum so a document
is at most `MAX_TEXT_BYTES` by construction. `DOC_BYTES` is a backstop that a
test holds never fires. The caps are structural, not measured, and are to be
tuned against 09's evals.

**Nothing a later scene can change about this scene's fiction is rendered.** A
thread's or commitment's status, and group membership, are current state, so
they live in `meta` (labelled as current), never in the text: rendering
"(broken)" on the scene where a promise was made would date the outcome onto
the scene that set it up, and re-embed every scene of the thread each time it
moved. What can still move this scene's text from elsewhere is a shared
record's title, a rename of an actor or place, an alias merge and
relationship-history retention.

No transcript message reaches the document (`meta.phase == "none"`): an open
scene's transcript changes every post, transcript text that is sent would need
the prompt view and so every rule file in the key, and what makes a scene
findable is already summarized. The transcript is read after selection, through
`expand`.

The key: each slice's digest is canonical JSON (`slices.digest`), and the
document key is 03-C1's composite: a digest of the kind,
`SEARCH_DOCUMENT_VERSION`, 03's `BUILD` fingerprint and the slice digests. So
an upgrade that changes rendering moves every key -- 09 keys term statistics by
it, and a key that survived a rendering change would hand back statistics of
the old text -- while the vectors, keyed by text, survive whenever the text
did.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .. import compiled, embed_space
from . import slices as slices_mod

KIND = compiled.SEARCHDOCS_SCENE.name
#: 03's registry kind's version: one number, so the cache key and the document
#: key cannot disagree about which rendering they name.
SEARCH_DOCUMENT_VERSION = compiled.SEARCHDOCS_SCENE.version
DOC_BYTES = 6000            # context/semantic.py's DOC_BYTES, for its reason
MAX_META_REFS = 64
ITEM_SEPARATOR = "; "

#: (label, items, bytes per item, line cap), in render order (spec 4.3).
LINES = (
    ("Scene:", 1, 160, 160),
    ("When:", 2, 48, 100),
    ("Where:", 4, 64, 264),
    ("Cast:", 12, 48, 600),
    ("In brief:", 1, 240, 240),
    ("Summary:", 1, 1500, 1500),     # or "Running summary:", same caps
    ("Keywords:", 12, 32, 408),
    ("Threads:", 5, 144, 730),
    ("Commitments:", 3, 144, 438),
    ("Facts established:", 4, 144, 584),
    ("Facts retired:", 2, 144, 292),
    ("Relationships:", 3, 112, 342),
)
#: The largest text `render` can produce: every line at its cap, each label
#: (the summary's longer spelling) and its space, and the newlines between --
#: 5658 + 145 = 5803 bytes.
MAX_TEXT_BYTES = (sum(cap for *_, cap in LINES)
                  + sum(len(("Running summary:" if label == "Summary:" else label).encode()) + 1
                        for label, *_ in LINES)
                  + len(LINES) - 1)
_PLAYER = " (player)"


@dataclass(frozen=True)
class SceneDocument:
    key: str
    text: str
    meta: dict


def _clip(text: str, cap: int) -> str:
    """`text` within `cap` bytes; a cut one ends in `…`, inside the cap."""
    if len(text.encode("utf-8")) <= cap:
        return text
    return embed_space.clip(text, cap - 3) + "…"


def _line(label: str, items: list[str], count: int, item_cap: int, line_cap: int) -> str:
    """`count` items at `item_cap` each, joined: within `line_cap` by
    construction (`count * item_cap` plus the separators fits every cap)."""
    kept = [_clip(i, item_cap) for i in items if i][:count]
    return f"{label} {ITEM_SEPARATOR.join(kept)}" if kept else ""


def _dedupe_folded(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        folded = item.casefold()
        if item and folded not in seen:
            seen.add(folded)
            out.append(item)
    return out


def _when(values: dict) -> list[str]:
    moments = values["head"]["moments"]
    if moments:
        first, last = (m.split("T", 1)[0] for m in (moments[0], moments[-1]))
        return [first] if first == last else [first, last]
    chron = values["chronicle"] or {}
    return [chron.get("date", "")] if chron.get("date") else []


def _where(values: dict) -> list[str]:
    names = [loc["name"] for loc in values["locations"]]
    chron = values["chronicle"] or {}
    folded = {n.casefold() for n in names}
    if chron.get("location") and chron["location"].casefold() not in folded:
        names.append(chron["location"])
    return _dedupe_folded(names)[-4:]


def _cast(values: dict) -> list[str]:
    out = []
    for actor in values["cast"]:
        name = actor["name"] or actor["id"]
        if actor["role"] == "player":
            out.append(_clip(name, 48 - len(_PLAYER.encode())) + _PLAYER)
        else:
            out.append(name)
    return out


def _summary(values: dict) -> tuple[str, str, str]:
    """(label, text, source)."""
    chron = values["chronicle"] or {}
    if chron.get("summary"):
        return "Summary:", chron["summary"], chron.get("source", "chronicle")
    if values["rolling"]:
        return "Running summary:", values["rolling"], "rolling"
    return "Summary:", "", "none"


def _records(rows: list[dict]) -> list[str]:
    return [f"{r['title'] or r['ref']}: {' / '.join(r['beats'])}" for r in rows]


def _text(values: dict) -> tuple[str, str]:
    """(text, summary source)."""
    chron = values["chronicle"] or {}
    label, summary, source = _summary(values)
    items = {
        "Scene:": [values["head"]["title"]],
        "Where:": _where(values),
        "Cast:": _cast(values),
        "In brief:": [chron.get("one_line", "")],
        "Summary:": [summary],
        "Keywords:": _dedupe_folded(chron.get("keywords", [])),
        "Threads:": _records(values["threads"]),
        "Commitments:": _records(values["commitments"]),
        "Facts established:": [f["text"] for f in values["facts"]["recorded"]],
        "Facts retired:": [f["text"] for f in values["facts"]["retired"]],
        "Relationships:": [f"{r['label']}: {r['before']} -> {r['after']}"
                           for r in values["relationships"]],
    }
    lines = []
    for name, count, item_cap, line_cap in LINES:
        if name == "When:":
            # One item of up to two dates: each date is its own 48-byte item.
            dates = [_clip(d, item_cap) for d in _when(values)]
            line = f"When: {' to '.join(dates)}" if dates else ""
        else:
            line = _line(label if name == "Summary:" else name, items[name], count,
                         item_cap, line_cap)
        if line:
            lines.append(line)
    return "\n".join(lines), source


def _capped(refs, limit: int = MAX_META_REFS) -> tuple[list, bool]:
    refs = list(refs)
    return refs[:limit], len(refs) > limit


def _meta(values: dict, digests: dict[str, str], degraded: tuple[str, ...],
          text: str, backstop: bool, source: str) -> dict:
    head, chron = values["head"], values["chronicle"] or {}
    truncated: list[str] = []

    def bounded(name: str, refs) -> list:
        kept, cut = _capped(refs)
        if cut:
            truncated.append(name)
        return kept

    cast = values["cast"]
    threads = bounded("threads", (r["ref"] for r in values["threads"]))
    commitments = bounded("commitments", (r["ref"] for r in values["commitments"]))
    ref = slices_mod.CAPS["ref"]
    meta = {
        "kind": "scene",
        "version": SEARCH_DOCUMENT_VERSION,
        "phase": "none",
        "identity": head["identity"],
        "title": head["title"],
        "absorbed": head["done"],
        "pcless": head["pcless"],
        "branch_group": head["branch_group"],
        "summary_source": source,
        "moments": {"first": head["moments"][0] if head["moments"] else "",
                    "last": head["moments"][-1] if head["moments"] else ""},
        "chronicle_date": chron.get("date", ""),
        "locations": bounded("locations", (f"locations/{x}" for x in head["locations"])),
        # Actor refs are capped here, in the metadata, and never in the slice:
        # the slice's full ref is what 07's membership index joins on.
        "cast": bounded("cast", (slices_mod.clip(a["ref"], ref) for a in cast)),
        "players": bounded("players", (slices_mod.clip(a["ref"], ref) for a in cast
                                       if a["role"] == "player")),
        "departed": bounded("departed", (slices_mod.clip(a["ref"], ref) for a in cast
                                         if not a["present"] and a["role"])),
        "threads": threads,
        "commitments": commitments,
        # The statuses of exactly the refs the two lists kept.
        "current_status": {r: values["status"][r] for r in threads + commitments
                           if r in values["status"]},
        "links": bounded("links", values["links"]),
        "facts": {"recorded": bounded("facts.recorded",
                                      (f["id"] for f in values["facts"]["recorded"])),
                  "retired": bounded("facts.retired",
                                     (f["id"] for f in values["facts"]["retired"]))},
        "relationship_pairs": bounded("relationship_pairs", sorted(
            {tuple(r["pair"]) for r in values["relationships"] if len(r["pair"]) == 2})),
        "groups": list(values["groups"]),           # current affiliation; never truncated
        "keywords": bounded("keywords", _dedupe_folded(
            [k.casefold() for k in chron.get("keywords", [])])),
        "degraded": bounded("degraded", degraded),
        "bytes": len(text.encode("utf-8")),
        "backstop": backstop,
        "slices": dict(digests),
        "truncated": truncated,
    }
    meta["relationship_pairs"] = [list(p) for p in meta["relationship_pairs"]]
    return meta


def key_of(sl: slices_mod.Slices) -> str:
    """The document key: the kind, the version, `BUILD` and every slice
    digest, plus what would not read (a lone surrogate replaced reads like the
    character that replaced it; the degraded flag keeps the two apart). A
    process that cannot compute `BUILD` keys with "" -- consistently, within
    itself."""
    blob = json.dumps([KIND, SEARCH_DOCUMENT_VERSION, compiled.build_fingerprint() or "",
                       sl.digests, list(sl.degraded)],
                      sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def render(sl: slices_mod.Slices) -> SceneDocument:
    """The document for these slices. Pure: it reads nothing."""
    text, source = _text(sl.values)
    backstop = len(text.encode("utf-8")) > DOC_BYTES
    if backstop:
        text = embed_space.clip(text, DOC_BYTES)
    return SceneDocument(key=key_of(sl), text=text,
                         meta=_meta(sl.values, sl.digests, sl.degraded, text, backstop, source))
