"""Prompt-view query preparation and the private live catalog (09 sections4/5).

The public Query carries strings, not scoring policy. Catalog names privately
supply double weights, and protected names are removed before ordinary words or
quoted phrases are extracted: a name must not sneak back as a plain token.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from .. import (
    actor_names,
    campaign_settings,
    entities,
    export,
    membership,
    overlay,
    pins,
    relationships,
    search,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..continuity import effective, involvement
from ..regex import view as regex_view
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize
from . import model, refs

QUERY_BYTES = 3000
STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "hers",
        "him",
        "his",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "that",
        "the",
        "their",
        "them",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
    ]
)


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def mentions(text: str, name: str) -> bool:
    return bool(
        name and re.search(r"(?<!\w)" + re.escape(normalized(name)) + r"(?!\w)", normalized(text))
    )


@dataclass(frozen=True)
class Catalog:
    names: dict[str, tuple[str, ...]]
    excluded: frozenset[str]
    protected: tuple[str, ...]
    present: frozenset[str]
    location: str
    groups: membership.Index
    threads: dict[str, dict] = field(default_factory=dict)
    #: The ``characters:`` ref of an actor perspective (11-C2), "" for the
    #: narrator. An actor's catalog names no thread or commitment.
    actor: str = ""

    def allowed(self, ref: str) -> bool:
        return refs.norm(ref) in self.names and refs.norm(ref) not in self.excluded

    def weights(self, terms: tuple[str, ...]) -> dict[str, int]:
        weighted = {
            normalized(n)
            for ref, names in self.names.items()
            if self.allowed(ref)
            for n in names
            if n
        }
        return {t: 2 for t in terms if normalized(t) in weighted}

    def filter_terms(self, terms: Sequence[str]) -> tuple[str, ...]:
        """Names protect their words too, including words in quoted phrases."""
        blocked = {word for name in self.protected for word in re.findall(r"\w+", normalized(name))}
        return tuple(
            dict.fromkeys(
                term
                for raw in terms
                if (term := normalized(raw)) and not blocked.intersection(re.findall(r"\w+", term))
            )
        )

    def clean(self, text: str) -> str:
        text = normalized(text)
        # Long names first: remove a whole phrase before testing overlapping
        # aliases. filter_terms independently gates every constituent word.
        for name in sorted(self.protected, key=len, reverse=True):
            text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", " ", text)
        return text


def _actor_catalog(cid: str, kind: str) -> dict[str, tuple[str, ...]]:
    listing = overlay.character_roster if kind == "characters" else overlay.pc_roster
    reader = overlay.read_character if kind == "characters" else overlay.read_pc
    try:
        rows = listing(cid)
    except Exception:  # noqa: BLE001 -- isolate one unreadable actor catalog
        return {}
    out = {}
    for row in rows:
        found = [str(row.get("name") or row["id"])]
        try:
            detail = reader(cid, row["id"])
            versions = detail.get("versions", [])
        except Exception:  # noqa: BLE001 -- roster name still answers without versions
            versions = []
        for version in versions:
            alternate = (
                actor_names.card_name(version.get("card", {}))
                if kind == "characters"
                else actor_names.persona_name(version.get("persona", {}))
            )
            if alternate:
                found.append(alternate)
        out[f"{kind}:{row['id']}"] = tuple(dict.fromkeys(normalized(n) for n in found if n))
    return out


def _entity_catalog(cid: str, kind: str) -> tuple[dict[str, tuple[str, ...]], set[str]]:
    try:
        rows = overlay.list_entities(cid, kind)
    except Exception:  # noqa: BLE001 -- isolate one unreadable entity kind
        return {}, set()
    names: dict[str, tuple[str, ...]] = {}
    blocked: set[str] = set()
    for row in rows:
        ref = f"{kind}:{row['id']}"
        names[ref] = (normalized(str(row.get("name") or row["id"])),)
        if entities.normalize_secrecy(row.get("secrecy")) == entities.GM_ONLY:
            blocked.add(ref)
    return names, blocked


def _thread_catalog(cid: str, kind: str) -> tuple[dict[str, tuple[str, ...]], dict[str, dict]]:
    reader = effective.threads if kind == "thread" else effective.commitments
    try:
        rows = reader(cid)
    except Exception:  # noqa: BLE001 -- one garbled ledger does not hide another
        return {}, {}
    names, records = {}, {}
    for row in rows:
        ref = f"{kind}:{row['id']}"
        aliases = [str(a.get("title") or "") for a in row.get("aliases", []) if isinstance(a, dict)]
        names[ref] = tuple(normalized(n) for n in [str(row.get("title") or ""), *aliases] if n)
        records[ref] = row
    return names, records


def _groups(cid: str) -> membership.Index:
    try:
        return membership.campaign_index(cid)
    except Exception:  # noqa: BLE001 -- membership is optional evidence
        return membership.index_from_rows([])


def _present(cid: str, sid: str) -> frozenset[str]:
    try:
        return frozenset(f"{a['kind']}:{a['id']}" for a in appearances_cast.scene_cast(cid, sid))
    except Exception:  # noqa: BLE001 -- appearances can fail independently of chronicle
        return frozenset()


def _location(cid: str, sid: str) -> str:
    try:
        history = scenes_read.get_location_history(cid, sid)
        return f"locations:{history[-1]}" if history else ""
    except Exception:  # noqa: BLE001 -- one unreadable head contributes no location
        return ""


def catalog(cid: str, sid: str, posts: int = 0) -> Catalog:
    names: dict[str, tuple[str, ...]] = {}
    try:
        blocked = {refs.norm(r) for r in pins.active(cid, sid, posts)["excluded"]}
    except Exception:  # noqa: BLE001 -- a broken context rule costs its signal only
        blocked = set()
    for kind in ("characters", "pcs"):
        names.update(_actor_catalog(cid, kind))
    for kind in ("locations", "items", "groups"):
        entity_names, hidden = _entity_catalog(cid, kind)
        names.update(entity_names)
        blocked.update(hidden)
    threads = {}
    for kind in ("thread", "commitment"):
        thread_names, records = _thread_catalog(cid, kind)
        names.update(thread_names)
        threads.update(records)
    groups = _groups(cid)
    for ref, roster in groups.groups.items():
        names[ref] = (normalized(roster.name),)
        if roster.secrecy == entities.GM_ONLY:
            blocked.add(ref)
    location = _location(cid, sid)
    protected = tuple(dict.fromkeys(n for ref in blocked for n in names.get(ref, ())))
    return Catalog(
        names,
        frozenset(blocked),
        protected,
        _present(cid, sid) - blocked,
        "" if location in blocked else location,
        groups,
        threads,
    )


def scoped(cat: Catalog, perspective: str) -> Catalog:
    """`cat` as `perspective` may search with it. The narrator's is whole. An
    actor's drops every thread and commitment (their names and records are
    the ledger's, written from the narrator's chair), so none can be a term,
    a weight or a seed, and it carries the actor, which is what makes
    `subjects` return an actor query's own seeds and `structural` read only
    the actor's own relationship history."""
    actor = model.actor_of(perspective)
    if actor is None:
        return cat
    ledger = ("thread:", "commitment:")
    return replace(cat, names={r: n for r, n in cat.names.items() if not r.startswith(ledger)},
                   threads={}, actor=actor)


def _relationship_targets(cid: str, actor: str) -> set[str]:
    """Who `actor` itself has a feeling about, or a bond with: its own
    relationship rows, never another pair's (`actor.own_relationships`)."""
    try:
        data = relationships.read(cid)
    except Exception:  # noqa: BLE001 -- a garbled relationship file costs these seeds only
        return set()
    found = set()
    for key in data.get("feelings", {}):
        source, _, target = str(key).partition("->")
        if source == actor and target:
            found.add(target)
    for key in data.get("bonds", {}):
        pair = str(key).split("|")
        if len(pair) == 2 and actor in pair:
            found.update(r for r in pair if r != actor)
    return found


def _actor_subjects(cid: str, cat: Catalog, text: str) -> tuple[str, ...]:
    """An actor query's seeds (11-S3): who is on stage, where it stands, whom
    it has a relationship with, and what it heard named. No group, thread or
    involvement join: those are inferences over records the actor never saw."""
    found = set(cat.present)
    if cat.location:
        found.add(cat.location)
    found.update(refs.norm(r) for r in _relationship_targets(cid, cat.actor))
    found.update(
        ref
        for ref, names in cat.names.items()
        if cat.allowed(ref) and any(mentions(text, n) for n in names)
    )
    return tuple(sorted(ref for ref in found if cat.allowed(ref)))


def subjects(cid: str, cat: Catalog, text: str, extras: tuple[str, ...] = ()) -> tuple[str, ...]:
    if cat.actor:
        # An actor's seeds were built once, from its own window, by `prepare`:
        # re-deriving them here would re-add the narrator's joins.
        return tuple(sorted({refs.norm(r) for r in extras} & {r for r in cat.names if cat.allowed(r)}))
    found = set(cat.present)
    if cat.location:
        found.add(cat.location)
    found.update(
        ref
        for ref, names in cat.names.items()
        if cat.allowed(ref) and any(mentions(text, n) for n in names)
    )
    for actor in cat.present:
        found.update(
            a.group for a in membership.groups_for(cat.groups, actor) if cat.allowed(a.group)
        )
    if cat.threads:
        # Involvement owns merged-group history. Failures cost only these seeds.
        try:
            touched = involvement.of(cid, tuple(cat.threads))
        except Exception:  # noqa: BLE001 -- advisory ledger join
            touched = {}
        found.update(
            ref
            for ref, value in touched.items()
            if cat.present.intersection(refs.norm(a) for a in value.get("actors", []))
        )
    found.update(refs.norm(ref) for ref in extras if cat.allowed(ref))
    return tuple(sorted(ref for ref in found if cat.allowed(ref)))


def _terms(cat: Catalog, clean: str) -> tuple[str, ...]:
    weighted = list(
        dict.fromkeys(
            n
            for ref, names in cat.names.items()
            if cat.allowed(ref)
            for n in names
            if mentions(clean, n)
        )
    )
    phrases = [t for t in search.query_terms(clean) if " " in t]
    plain = [t for t in re.findall(r"\w+", clean) if len(t) > 1 and t not in STOPWORDS]
    return cat.filter_terms([*weighted, *phrases, *plain])


@dataclass(frozen=True)
class Prepared:
    query: model.Query
    scan_text: str
    # Private archive activation input, not an embedding query. Legacy compose
    # counts stored director notes in its window and leaves image URLs intact;
    # omission must reproduce that exact producer without weakening Query.
    archive_scan_text: str
    weights: dict[str, int]
    catalog: Catalog


def prepare(cid: str, sid: str, *, seed: str = "", actor_ref: str | None = None) -> Prepared:
    """The query a turn retrieves with. `actor_ref` None, ``"grimoire"`` or
    ``"narrator"`` is the narrator's; a ``characters:`` ref is that NPC's
    (11-S3), built only from posts it was present for (its observed window, by
    the same intervals as its prompt) and from its own seeds."""
    perspective = (model.NARRATOR if actor_ref in (None, "grimoire", model.NARRATOR)
                   else model.ACTOR_PREFIX + str(actor_ref))
    actor = model.actor_of(perspective)
    scene = scenes_read.read_scene(cid, sid)
    shown = regex_view.view(scene["messages"], cid=cid, phase="prompt")
    window = shown
    if actor is not None:
        # Windowed over every post before anything is filtered: an interval
        # counts hidden posts and director notes too.
        window = [shown[i]
                  for i in appearances_paths.observed_indices(cid, sid, actor, shown)]
    visible = [
        m
        for m in window
        if not scenes_serialize.is_excluded(m) and not scenes_serialize.is_director_note(m)
    ]
    depth = campaign_settings.count(cid, "context_scan_depth")
    scan = "\n".join(export.drop_images(m["content"]) for m in visible[-depth:]) if depth else ""
    scan = (scan + "\n" + seed).strip() if seed else scan
    archive_visible = scenes_serialize.without_excluded(window)
    archive_scan = "\n".join(m["content"] for m in archive_visible[-depth:]) if depth else ""
    archive_scan = (archive_scan + "\n" + seed).strip() if seed else archive_scan
    cat = scoped(catalog(cid, sid, len(shown)), perspective)
    clean = cat.clean(scan)
    raw = scan.encode("utf-8", errors="replace")
    clipped = raw[-QUERY_BYTES:].decode("utf-8", errors="ignore")
    # The lexical terms obey the same bound as the semantic text: the recent
    # end is what the reply answers, and a long post must not buy a term list
    # (and a scoring pass) the size of everything it says. A cut word is not a
    # term -- it could be the tail of a protected name -- so a clipped window
    # starts at its first whole word.
    before = raw[-QUERY_BYTES - 1 : -QUERY_BYTES].decode("utf-8", errors="ignore")
    cut = len(raw) > QUERY_BYTES and not re.match(r"\W", before)
    tail = re.sub(r"^\w*", "", clipped) if cut else clipped
    terms = _terms(cat, cat.clean(tail))
    # Speaker stamps are separate from prose. Hidden/director notes confer no
    # speaker seed; an unstamped player label is not treated as an actor name.
    speaker = str(visible[-1].get("speaker") or "") if visible else ""
    if actor is not None:
        seeds = _actor_subjects(cid, cat, clean + "\n" + speaker)
        scopes = model.RELATIONS - {"thread"}
    else:
        seeds = subjects(cid, cat, clean)
        if visible:
            seeds = subjects(cid, cat, clean + "\n" + speaker, seeds)
        scopes = model.RELATIONS
    q = model.Query(
        (clipped,), terms=terms, subjects=seeds, scopes=scopes, perspective=perspective,
        turn_index=len(shown) - 1 if shown else None,
    )
    return Prepared(q, scan, archive_scan, cat.weights(terms), cat)


def build(cid: str, sid: str, *, seed: str = "", actor_ref: str | None = None) -> model.Query:
    return prepare(cid, sid, seed=seed, actor_ref=actor_ref).query


def for_text(cid: str, text: str, *, perspective: str = model.NARRATOR) -> model.Query:
    """A query from free text alone, with no scene to scan: what an
    investigation's `search_history` asks (roadmap 12-S1). Its terms and
    seeds come from `text` by the same catalog rules a turn's do -- weighted
    names, phrases and plain words, protected names removed -- scoped to
    `perspective`, with no present cast and no location to seed from."""
    cat = scoped(catalog(cid, ""), perspective)
    clean = cat.clean(text)
    return model.Query((text,), terms=_terms(cat, clean), subjects=subjects(cid, cat, clean),
                       perspective=perspective)
