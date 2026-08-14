"""Keyword search over everything the store holds — content and facts (#33).

The store is flat markdown and JSON with no index, so this is the index-free
half of the feature: every query walks the corpus, extracts each file's plain
text, and matches substrings against it. That is O(corpus) per query and buys
no ranking a real full-text engine would give, which is a deliberate trade at
this store's scale (hundreds of files, one user, local disk). What keeps it
cheap enough is `statcache.memo`: every file's *extraction* — frontmatter
parsing, card field selection, whitespace flattening — is memoized on its
(path, mtime, size) signature, so a repeat query re-reads only what changed,
including changes another process made by syncing the folder. It memoizes into
this module's own pool (`_POOL`), never the shared one: a sweep of the whole
store through the shared cache would evict every hash the sync path keeps
there.

The fact files are the deliberate exception — chronicle.json, timeline.md,
plot.json, facts.json and relationships.json are re-read and re-parsed per
query. They are read through the modules that own them, which take a campaign
id rather than a path, so memoizing on a signature here would mean writing
those five filenames down a second time — and a rename would then leave the
whole fact half silently unsearchable rather than failing. They are also the
small half of the corpus by an order of magnitude: a chronicle entry is a
one-liner and a summary, where a single scene transcript outweighs all five.

The corpus, by scope:

- **World** (`<home>/worlds/<wid>/`) — world.md, the five entity kinds, the
  greetings, every character card version and every PC persona version.
- **Campaign** (`<home>/campaigns/<cid>/`) — the same layout, plus the scene
  transcripts, plus the fact record: chronicle.json, timeline.md, plot.json's
  threads and beats, facts.json's standing/retired ledger, relationships.json's
  notes and bonds, and the per-actor `state.md` / `dossier.md` sidecars.

**A campaign is searched as the files it actually holds, not as what it
resolves to.** A campaign materializes a record only when it diverges from its
world, so a record read through `store/overlay.py` is usually the *world's*
file — and a store with one world and ten campaigns would then answer every
query with eleven copies of the same lore. Reading files instead means each
record is reported exactly once, under whichever scope holds the bytes that
matched: the world's original, or a campaign's divergent copy of it. That is
also why `scope` is on every hit and is filterable — a world record and a
campaign's fork of it share an id and are two different records (#33).

The consequence, stated rather than left implicit: a campaign that has never
edited a piece of inherited lore returns no campaign-scoped hit for it. The
world hit is that record, and the UI takes the reader to the campaign's own
view of it. Nothing here should be copied into code that resolves a record for
*reading* — that is what the overlay is for, and `tests/test_overlay_guard.py`
is what keeps the two apart.

Matching is plain case-folded substring, not whole-word: this is the
Ctrl-F a library page never had, and a reader who types "ledg" means "ledger".
Terms are ANDed, and a `"quoted phrase"` is one term. Ranking is a small fixed
formula (`_score`) rather than bm25 — a name hit outweighs a body hit, repeats
count a little, and the rest is a stable tie-break so two identical queries
never shuffle their results.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from . import chronicle, entities, facts, plot, relationships, statcache
from .frontmatter import parse_frontmatter
from .paths import home, natural_key, safe_id

WORLD = "world"
CAMPAIGN = "campaign"

#: The two halves of the library. A hit always names one, and a query may
#: narrow to one -- see the module docstring for why they are not merged.
SCOPES: tuple[str, ...] = (WORLD, CAMPAIGN)

#: Record kinds that hold authored content. `world`/`campaign` are the meta
#: files themselves (a world's premise, a campaign's pitch), which are records
#: a reader searches for exactly as often as anything inside them.
CONTENT_KINDS: tuple[str, ...] = entities.ENTITY_KINDS + (
    "greetings", "characters", "pcs", "scenes", WORLD, CAMPAIGN,
)

#: Kinds that hold what play established rather than what an author wrote.
#: Campaign-only by construction: nothing in a world has a chronicle.
FACT_KINDS: tuple[str, ...] = (
    "chronicle", "timeline", "plot", "facts", "relationships", "state", "dossier",
)

KINDS: tuple[str, ...] = CONTENT_KINDS + FACT_KINDS

#: Rank order for equally-scoring hits, so a page of results is grouped the way
#: the kinds are declared above rather than by dict iteration order.
_KIND_ORDER = {kind: i for i, kind in enumerate(KINDS)}

DEFAULT_LIMIT = 50
#: A ceiling on what one response carries. The scan itself is unbounded -- the
#: facet counts describe every hit -- so this caps the payload, not the search.
MAX_LIMIT = 200

#: This module's own `statcache` pool. A search touches every file in the
#: store on one request, so sharing the process-wide cache would evict every
#: entity and card hash in it and hand the next sync sweep a cold cache —
#: search making its own reads cheap at everyone else's expense. It also keeps
#: what a sweep holds in memory (whole flattened transcripts) inside a budget
#: that can be reasoned about on its own.
#:
#: That budget is `statcache.MAX_ENTRIES`, and it is a cliff rather than a
#: gradient: a store holding more files than that evicts in the same order it
#: walks, so the entry evicted is always the one the next query wants first and
#: the hit rate collapses to roughly zero. Search still answers correctly —
#: it degrades to the cost of a first query, which is the O(corpus) this design
#: already signs up for — but the memo stops helping entirely rather than
#: helping less. A store that large is the one that wants the FTS5 index.
_POOL: dict = {}

#: How much text a snippet shows, and how much of it sits before the match.
SNIPPET_CHARS = 180
_SNIPPET_LEAD = 60

#: Frontmatter keys worth matching. The rest of a record's frontmatter is
#: machinery -- version ids, flags, timestamps -- and folding it in turns a
#: search for "default" into every record in the store.
_META_KEYS = ("name", "title", "keys", "owners", "tags", "summary", "pronouns", "sd_prompt")

#: Card fields that hold prose. Everything else in a v2/v3 card is bookkeeping
#: (`spec`, `extensions`, avatar URIs) or duplicates one of these.
_CARD_FIELDS = ("name", "description", "personality", "scenario", "first_mes",
                "mes_example", "system_prompt", "post_history_instructions",
                "creator_notes")
_CARD_LISTS = ("alternate_greetings", "tags")

# A bare run of non-space characters, or a double-quoted phrase kept whole.
_TERM = re.compile(r'"([^"]*)"|(\S+)')

# Emphasis runs, dropped from a snippet after it is cut. A transcript is a
# script of `**Speaker:**` markers, so a snippet from one is mostly asterisks
# otherwise. Only RUNS go: a lone `*` or `_` is as likely to be prose or a slug
# as it is markup, and the snippet is shown as text rather than rendered.
_MARKUP = re.compile(r"[*_~`]{2,}")


class BadScope(Exception):
    """A scope that is not one of `SCOPES`."""


class BadKind(Exception):
    """A kind filter naming something the corpus has no such thing as."""


def query_terms(q: str) -> list[str]:
    """The query's terms, case-folded and de-duplicated, phrases kept whole.

    `casefold`, not `lower`, matching what `facts.restates`, `facts.find`,
    `plot.open_threads` and `commitments` already do for case-insensitive text:
    `"Straße".lower()` is `"straße"`, so a search for "strasse" finds nothing,
    while `casefold` maps both to `"strasse"` and the record is found.

    Returned to the caller as well as used here: the client highlights matches
    itself, and a second implementation of this splitting on that side would
    drift from this one the first time either learned a new operator.
    """
    out: list[str] = []
    seen: set[str] = set()
    for quoted, bare in _TERM.findall(q or ""):
        # `seen` rather than `term not in out`: the list membership test made
        # this quadratic in the word count, and the word count is not bounded
        # by anything a UI enforces -- pasting a document into the search box
        # is an ordinary accident, and a 60k-word paste spent six seconds here
        # before the sweep even started.
        term = " ".join((quoted or bare).split()).casefold()
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


# ---- text extraction (memoized per file) ----

def _read_text(path: Path) -> str:
    """A file's text, or "" for one that cannot be read as text.

    Search is a sweep over a hand-editable tree: a file that vanished mid-walk,
    one the OS refuses, and one holding bytes that are not UTF-8 are all
    "nothing to match here". Raising would cost the whole query one unreadable
    file, which is precisely the file a reader is most likely to be hunting.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return ""


def _flat(text: str) -> str:
    """Whitespace collapsed to single spaces.

    Done once at extraction so matching and snippets see the same string: a
    phrase that straddles a line break matches, and a snippet window can be
    measured in characters without a newline making it two lines tall.
    """
    return " ".join(text.split())


def _markdown_doc(path: Path) -> tuple[str, str, str]:
    """(display name, searchable text, prose) for any frontmatter-and-body file.

    Covers every markdown record the store writes -- entities, greetings,
    scenes, world/campaign meta, PC personas, actor `state.md` -- because they
    all share that shape. A plain-text file (a `dossier.md`) parses as a body
    with no frontmatter and works the same way.

    The prose is the body alone, and is what a snippet is cut from when the
    match is in it: the searchable text carries the frontmatter too, so a
    snippet taken from that would tail off into a comma-separated key list on
    every short record. A metadata-only hit still snippets from the metadata,
    because that is where its match is.
    """
    meta, body = parse_frontmatter(_read_text(path))
    name = str(meta.get("name") or meta.get("title") or path.stem)
    prose = _flat(body)
    parts = [prose] + [str(meta.get(key, "")) for key in _META_KEYS]
    return name, _flat(" ".join(p for p in parts if p)), prose


def _card_doc(path: Path) -> tuple[str, str, str]:
    """(display name, searchable text) for a character card version.

    Reads `data` when the card has one (v2/v3) and the card itself when it does
    not (v1), which is the same tolerance `store/cards.py` applies on import. A
    card that will not parse contributes nothing rather than failing the query.
    """
    try:
        card = json.loads(_read_text(path) or "{}")
    except ValueError:
        return path.stem, "", ""
    if not isinstance(card, dict):
        return path.stem, "", ""
    data = card.get("data") if isinstance(card.get("data"), dict) else card
    parts = [str(data.get(field, "")) for field in _CARD_FIELDS]
    for key in _CARD_LISTS:
        value = data.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if item)
    name = str(data.get("name") or path.stem)
    text = _flat(" ".join(p for p in parts if p))
    # A card is prose all the way down -- there is no metadata tail to keep out
    # of the snippet -- so the two are the same string.
    return name, text, text


def _doc(reader, path: Path) -> tuple[str, str, str] | None:
    """`reader(path)`, memoized on the file's stat signature. None if missing.

    The memo is what makes a re-query cheap: the second search of a session
    stats every file and parses none of the unchanged ones. A file whose mtime
    is inside `statcache`'s racy window is computed and not cached, so a record
    saved a moment ago is searched as it is now, not as it was.
    """
    sig = statcache.signature(path)
    if sig is None:
        return None
    return statcache.memo(f"search:{reader.__name__}", sig, lambda: reader(path), pool=_POOL)


def _s(value, fallback: str = "") -> str:
    """A stored field as text, or `fallback` for anything that is not a string.

    Every JSON file this module reads is hand-editable and read by a bare
    `json.loads`, so the same coercion `facts._field` and `plot._field` apply
    for the same reason: a list-valued `text` must cost its own row, not the
    query.
    """
    return value.strip() if isinstance(value, str) else fallback


# ---- the corpus walk ----

def _row(kind: str, rid: str, sub: str, name: str, text: str, prose: str) -> dict:
    """One searchable document.

    `text` is what a term has to appear in; `prose` is what a snippet is cut
    from when the term appears there. They differ only where a record carries
    machinery a reader would not want quoted back at them -- a markdown
    record's frontmatter -- and the split exists because a snippet is evidence
    that this is the right record, so it has to read like the record.
    """
    return {"kind": kind, "id": rid, "sub": sub, "name": name, "text": text, "prose": prose}


def _record_docs(root: Path, meta_file: str, self_kind: str, rid: str) -> Iterator[dict]:
    """Every searchable content document under one record root.

    `root` is a world directory or a campaign directory, and this walks the two
    identically because on disk they *are* identical for everything search
    reads -- which is also why it takes a bare path rather than an id: search
    reports the files a directory holds, and has no business resolving what a
    campaign inherits (see the module docstring).
    """
    doc = _doc(_markdown_doc, root / meta_file)
    if doc is not None:
        yield _row(self_kind, rid, "", doc[0], doc[1], doc[2])

    # Flat `<root>/<kind>/<id>.md` records. `scenes` rides along because it has
    # exactly that shape; a world simply has no such directory.
    for kind in entities.SYNCED_KINDS + ("scenes",):
        directory = root / kind
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if not safe_id(path.stem):   # enumeration agrees with the resolvers
                continue
            doc = _doc(_markdown_doc, path)
            if doc is not None:
                yield _row(kind, path.stem, "", doc[0], doc[1], doc[2])

    yield from _actor_docs(root)
    yield from _pc_docs(root)


def _actor_docs(root: Path) -> Iterator[dict]:
    """Character cards, one document per version, plus the campaign-local
    sidecars filed beside them.

    `character.md` and `tagline.md` are not documents of their own, for
    `_pc_docs`' reason: a row for either would sit directly above the version
    rows saying the same thing about the same character. Their text rides along
    on every version instead -- which is what makes a character findable by the
    name the app actually displays and by the one-line identity beside it,
    rather than only by whatever name each card happens to carry (#64). A card
    is free to be called something else: an era label, an alias, a name an
    import guessed.

    `state.md` and `dossier.md` are facts rather than content -- what play has
    established about someone, rewritten as it changes -- so they are yielded
    under their own kinds. They only exist campaign-side; a world root simply
    has none.
    """
    for meta_path in sorted((root / "characters").glob("*/character.md")):
        cid = meta_path.parent.name
        if not safe_id(cid):
            continue
        meta = _doc(_markdown_doc, meta_path)
        name = meta[0] if meta is not None else cid
        # The tagline is plain text with no frontmatter, so its *prose* is the
        # whole file; taking `[2]` rather than `[1]` keeps the parse identical
        # to every other markdown record's.
        tag = _doc(_markdown_doc, meta_path.parent / "tagline.md")
        identity = _flat(" ".join(p for p in ((meta[1] if meta is not None else ""),
                                              (tag[2] if tag is not None else "")) if p))
        for card_path in sorted(meta_path.parent.glob("*.json")):
            card = _doc(_card_doc, card_path)
            if card is not None:
                yield _row("characters", cid, card_path.stem, card[0] or name,
                           _flat(f"{card[1]} {identity}"), card[2])
        for filename, kind in (("state.md", "state"), ("dossier.md", "dossier")):
            side = _doc(_markdown_doc, meta_path.parent / filename)
            if side is not None and side[1]:
                yield _row(kind, cid, "", name, side[1], side[2])


def _pc_docs(root: Path) -> Iterator[dict]:
    """PC personas, one document per version.

    `pc.md` is not a document of its own -- it holds the display name, the
    default version and the tags, and a row for it would sit directly above the
    version rows saying the same name with nothing else to distinguish it. Its
    text rides along on every version instead, so a tag search still finds the
    PC. A record with no version file at all falls back to it, so nothing on
    disk is unsearchable.
    """
    for meta_path in sorted((root / "pcs").glob("*/pc.md")):
        pid = meta_path.parent.name
        if not safe_id(pid):
            continue
        meta = _doc(_markdown_doc, meta_path)
        name = meta[0] if meta is not None else pid
        extra = meta[1] if meta is not None else ""
        found = False
        for version_path in sorted(meta_path.parent.glob("*.md")):
            if version_path.name == "pc.md":
                continue
            version = _doc(_markdown_doc, version_path)
            if version is None:
                continue
            found = True
            yield _row("pcs", pid, version_path.stem, version[0] or name,
                       _flat(f"{version[1]} {extra}"), version[2])
        if not found and meta is not None:
            yield _row("pcs", pid, "", name, extra, meta[2])


def _mapping(read, *args) -> dict:
    """`read(*args)` when it yields a mapping, `{}` for anything else.

    Every fact store is a hand-editable JSON file behind a bare `json.loads`,
    so "unparseable" and "valid JSON of the wrong shape" are both live
    possibilities. The policy is `plot.render_open`'s: a broken file costs its
    own section of the results and nothing else.
    """
    try:
        data = read(*args)
    except Exception:  # noqa: BLE001 — a garbled fact file omits its own rows
        return {}
    return data if isinstance(data, dict) else {}


def _fact_docs(cid: str, root: Path) -> Iterator[dict]:
    """The campaign's fact record, one document per addressable thing in it.

    Per record rather than per file: a plot thread, a standing fact and a
    timeline line are each something a reader is looking *for*, and a hit that
    could only say "somewhere in plot.json" would be a worse answer than the
    file listing it replaced.
    """
    for sid, rec in sorted(_mapping(chronicle.read_chronicle, cid).items()):
        if not isinstance(rec, dict):
            continue
        keywords = rec.get("keywords")
        parts = [_s(rec.get("one_line")), _s(rec.get("summary")), _s(rec.get("location"))]
        if isinstance(keywords, list):
            parts.extend(_s(k) for k in keywords)
        text = _flat(" ".join(p for p in parts if p))
        if text:
            yield _row("chronicle", sid, "", _s(rec.get("one_line")) or sid, text, text)

    # The timeline is an append-only list of dated lines, so a line is the
    # record. The leading "- " and the heading are formatting, not content.
    for n, raw in enumerate(_read_text(root / "timeline.md").splitlines(), start=1):
        line = raw.strip().lstrip("-").strip()
        if not line or line.startswith("#"):
            continue
        yield _row("timeline", "timeline", str(n), "Timeline", _flat(line), _flat(line))

    for pid, thread in sorted(_mapping(plot.read, cid).items()):
        if not isinstance(thread, dict):
            continue
        beats = thread.get("beats")
        title = _s(thread.get("title")) or pid
        parts = [title, _s(thread.get("status"))]
        if isinstance(beats, list):
            parts.extend(_s(b.get("text")) for b in beats if isinstance(b, dict))
        text = _flat(" ".join(p for p in parts if p))
        yield _row("plot", pid, "", title, text, text)

    for fid, rec in sorted(_mapping(facts.read, cid).items()):
        if not isinstance(rec, dict):
            continue
        text = _s(rec.get("text"))
        if not text:
            continue
        yield _row("facts", fid, _s(rec.get("scene")), text,
                   _flat(" ".join([text, _s(rec.get("date"))])), text)

    yield from _relationship_docs(cid)


def _relationship_docs(cid: str) -> Iterator[dict]:
    """Feelings and bonds, named by the actors they are between.

    The actor names are resolved through the overlay (`relationships.actor_name`
    does it) because a thin campaign's cast is mostly inherited -- and they are
    cached for the length of this walk, since a campaign's relationship graph
    names the same handful of actors over and over.
    """
    data = _mapping(relationships.read, cid)
    names: dict[str, str] = {}

    def name_of(token: str) -> str:
        if token not in names:
            names[token] = relationships.actor_name(cid, token)
        return names[token]

    feelings = data.get("feelings")
    if isinstance(feelings, dict):
        for key, rec in sorted(feelings.items()):
            if not isinstance(rec, dict):
                continue
            a, _, b = key.partition("->")
            label = f"{name_of(a)} → {name_of(b)}"
            yield _row("relationships", key, "feeling", label,
                       _flat(" ".join([label, _s(rec.get("note"))])), _s(rec.get("note")))

    bonds = data.get("bonds")
    if isinstance(bonds, dict):
        for key, rec in sorted(bonds.items()):
            if not isinstance(rec, dict):
                continue
            a, _, b = key.partition("|")
            label = f"{name_of(a)} ↔ {name_of(b)}"
            yield _row("relationships", key, "bond", label,
                       _flat(" ".join([label, _s(rec.get("type"))])), _s(rec.get("type")))


def _roots(scope: str, root_id: str) -> Iterator[tuple[str, str, str, Path, str]]:
    """(scope, id, display name, directory, meta filename) for each record root
    the query covers, worlds before campaigns.

    Enumerated straight off the two directories rather than through
    `worlds.list_worlds` / `campaigns.list_campaigns`: those parse and count
    every record inside each one to describe it for a shelf, which is the whole
    corpus read twice for a name this walk is about to read anyway.
    """
    base = home()
    for scope_name, dirname, meta_file in ((WORLD, "worlds", "world.md"),
                                           (CAMPAIGN, "campaigns", "campaign.md")):
        if scope and scope != scope_name:
            continue
        try:
            entries = sorted((base / dirname).iterdir())
        except OSError:
            continue          # no such directory yet, or one we cannot read
        for entry in entries:
            rid = entry.name
            if (root_id and rid != root_id) or not safe_id(rid):
                continue
            meta = _doc(_markdown_doc, entry / meta_file)
            if meta is None:  # not a record root: no meta file, so nothing here
                continue
            yield scope_name, rid, meta[0] or rid, entry, meta_file


# ---- matching, ranking, snippets ----

def _score(name: str, text: str, terms: list[str]) -> float:
    """How well one document answers the query.

    A name hit counts for far more than a body hit: someone typing "salt pact"
    is looking for the record called that, not for the eleven scenes that
    mention it. Repeats inside the body count a little and cap quickly, so a
    long transcript cannot outrank the record it is talking about by sheer
    length. Whole-query matches on the name get the top of the list outright.
    """
    low_name, low_text = name.casefold(), text.casefold()
    total = 0.0
    for term in terms:
        if term in low_name:
            total += 8
        total += min(low_text.count(term), 5)
    whole = " ".join(terms)
    if low_name == whole:
        total += 20
    elif low_name.startswith(whole):
        total += 10
    return total


def _snippet(prose: str, text: str, terms: list[str]) -> str:
    """A one-line window around the query's first matching term.

    Cut from the record's prose when the match is in it, and from the whole
    searchable text otherwise -- which is what puts a metadata-only hit's
    snippet on the metadata that matched, without letting every other hit's
    snippet tail off into a frontmatter key list.

    Falls back to the head of the prose when the match was on the name alone --
    a hit whose snippet is blank reads as an empty record, and the opening of
    the body is what a reader would look at next anyway.
    """
    if terms and prose and any(term in prose.casefold() for term in terms):
        text = prose
    text = text or prose
    if not text:
        return ""
    # Where to frame. Offsets come from a case-INSENSITIVE regex over the raw
    # text rather than from `str.find` over a folded copy, because folding can
    # change the string's length -- `casefold` maps "ß" to "ss" -- and an index
    # into the folded copy is then not an index into the original. Slicing at
    # one drifts by the number of such characters before the match, so on a
    # long passage of German prose the window can land past the term it was
    # supposed to frame. `re.IGNORECASE` matches character-for-character, so
    # every offset it reports is an offset into `text`.
    #
    # The RAREST matching term, not the earliest: framing the earliest means a
    # query like "the salt pact" snippets around "the" at character 0 -- the
    # head of the document, with nothing distinctive in the window and often no
    # marked term visible at all. The term with the fewest occurrences is the
    # one that made this record a hit; ties fall to the earliest.
    hits = []
    for term in terms:
        # Counted by walking the iterator rather than materializing it: a term
        # occurring ten thousand times in a long transcript is ten thousand
        # match objects held at once, for two numbers.
        count, first = 0, -1
        for found in re.finditer(re.escape(term), text, re.IGNORECASE):
            if first < 0:
                first = found.start()
            count += 1
        if count:
            hits.append((count, first))
    start = max(0, min(hits)[1] - _SNIPPET_LEAD) if hits else 0

    # Neither end cuts a word in half: step forward to the next space at the
    # start (unless that would swallow the match) and back to the last one at
    # the end.
    if start > 0:
        space = text.find(" ", start)
        if 0 <= space <= start + 20:
            start = space + 1
    end = min(len(text), start + SNIPPET_CHARS)
    if end < len(text):
        space = text.rfind(" ", start, end)
        if space > start:
            end = space
    window = _MARKUP.sub("", text[start:end]).strip()
    return ("…" if start > 0 else "") + window + ("…" if end < len(text) else "")


def _hit(scope_name: str, rid: str, root_name: str, doc: dict, terms: list[str]) -> dict | None:
    """The document as a result row, or None when it does not match.

    Terms are ANDed over the name and the body together, so "seraphine salt"
    finds the record whose *name* is one term and whose body holds the other --
    the shape most two-word queries actually have.
    """
    haystack = f"{doc['name']} {doc['text']}".casefold()
    if not all(term in haystack for term in terms):
        return None
    # No snippet yet: a one-letter query matches most of the store, and cutting
    # a window for thousands of rows that the sort is about to drop is the one
    # per-hit cost in here that is pure waste. `_doc` carries the text through
    # so `_fill_snippets` can do it for the page that is actually returned.
    return {"scope": scope_name, "root": rid, "root_name": root_name,
            "kind": doc["kind"], "id": doc["id"], "sub": doc["sub"],
            "name": doc["name"], "score": _score(doc["name"], doc["text"], terms),
            "_doc": doc}


def _fill_snippets(hits: list[dict], terms: list[str]) -> list[dict]:
    """Cut each surviving hit's snippet and drop the document behind it."""
    out = []
    for hit in hits:
        doc = hit.pop("_doc")
        out.append({**hit, "snippet": _snippet(doc["prose"], doc["text"] or doc["name"], terms)})
    return out


def _sort_key(hit: dict) -> tuple:
    """Rank, then a total order over everything else, so two runs of one query
    return the same page in the same order."""
    return (-hit["score"], _KIND_ORDER.get(hit["kind"], len(KINDS)),
            hit["scope"], hit["root"], natural_key(hit["name"]), hit["id"], hit["sub"])


def search(q: str, *, scope: str = "", root: str = "",
           kinds: tuple[str, ...] = (), limit: int = DEFAULT_LIMIT) -> dict:
    """Rank every record whose text holds all of the query's terms.

    `scope` narrows to worlds or to campaigns; `root` narrows further to one
    world or one campaign (and means nothing without a scope, which is the
    caller's to enforce -- a bare id is ambiguous between the two). `kinds`
    keeps only those kinds.

    The returned `facets` count the hits *before* the kind filter, so the UI's
    kind chips can show what dropping the current filter would find rather than
    only ever counting the filter already applied. `total` is the count after
    it, and `hits` is that list cut to `limit`.

    An empty query is an empty result, not an error: a search box is empty far
    more often than it is wrong, and a 400 on every keystroke before the first
    letter would be the route shouting at its own UI.
    """
    if scope and scope not in SCOPES:
        raise BadScope(scope)
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise BadKind(", ".join(sorted(unknown)))

    terms = query_terms(q)
    if not terms:
        return {"q": q, "terms": [], "total": 0, "facets": {}, "scopes": {},
                "truncated": False, "hits": []}

    matched: list[dict] = []
    for scope_name, rid, root_name, root_dir, meta_file in _roots(scope, root):
        docs = _record_docs(root_dir, meta_file, scope_name, rid)
        for doc in docs:
            hit = _hit(scope_name, rid, root_name, doc, terms)
            if hit is not None:
                matched.append(hit)
        if scope_name != CAMPAIGN:
            continue
        for doc in _fact_docs(rid, root_dir):
            hit = _hit(scope_name, rid, root_name, doc, terms)
            if hit is not None:
                matched.append(hit)

    facets: dict[str, int] = {}
    scope_counts: dict[str, int] = {}
    for hit in matched:
        facets[hit["kind"]] = facets.get(hit["kind"], 0) + 1
        scope_counts[hit["scope"]] = scope_counts.get(hit["scope"], 0) + 1

    if kinds:
        wanted = frozenset(kinds)
        matched = [hit for hit in matched if hit["kind"] in wanted]
    matched.sort(key=_sort_key)
    capped = max(1, min(limit, MAX_LIMIT))
    return {"q": q, "terms": terms, "total": len(matched), "facets": facets,
            "scopes": scope_counts, "truncated": len(matched) > capped,
            "hits": _fill_snippets(matched[:capped], terms)}
