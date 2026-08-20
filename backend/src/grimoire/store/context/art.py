"""Art the narrator may reach for: which described images this moment could
use, and what a model writes to use one.

Grimoire has never put an image in front of the model. `scenario.strip_images`
removes every reference before a card's openers reach the extraction prompt,
and nothing in this package has ever emitted one. This module is the other
half: `store.image_descriptions` lets an author say what a picture depicts, and
here that text is ranked against the scene and offered as a droppable section,
with a handle grammar the model writes back.

## The pool is the turn, not the library

Ranking every described image in a world of hundreds of characters, on every
turn, is the cost that would sink this. It is also the wrong answer: art
belonging to somebody who is not in the scene should not be offered. So the
pool is assembled from what `_assemble` already holds — the on-stage cast at
their locked versions, the current setting's location, the entities world info
activated or recalled, and the campaign's own image library, which belongs to
no record and is always in scope.

Cost therefore scales with the SCENE for the record half — a handful of small
JSON reads, the same order as `image_subjects.appearances`, which the store
already treats as cheap.

The campaign's own library is the exception, and it is stated rather than
hidden: it has no record to be in scope through, so it is included whole. An
earlier draft of this docstring claimed cost scaled with scene size and not
library size, which was simply false of that half. Measured, a 300-image
described library costs ~9ms to assemble and ~4ms to rank by keyword, and
~19ms more to read its cached vectors when an embeddings endpoint is
configured. That is a real per-turn cost on a blocking path, and it is the one
that would grow without bound if somebody kept a thousand described maps — so
if this ever needs a limit, the library half is where it goes, and it should be
a limit the reader can see rather than a silent truncation.

## What this layer does not decide

**Visibility rules are inherited, never re-implemented.** An actor the reader
excluded is out of `cast` before this module sees it; a GM-only location never
becomes the current setting; owner-gated lore never reaches the recalled list.
As in `semantic.py`, the absence of an `owners` check in this file is
structural — there is no path in here that could need one — rather than an
omission.

## Ranking: keyword first, semantic as an upgrade

Keyword scoring always works and needs nothing configured: the description's
content words are matched against the scan window, with a bonus when the
owning record's own name is in play. With an embeddings endpoint configured
(`embed_space.resolve`, the same one search and recall use) the ranking is by
cosine instead, over `vectors.py`'s on-disk cache.

**It falls back to keyword, never to an error.** No connection, a dead
endpoint, a rate limit, a wrong-width vector: the keyword ranking stands and
the turn proceeds. This is a strictly easier promise than `semantic.py`'s,
because there the fallback is "recall nothing" while here it is a real
ranking — which is why this module does not need that one's careful
partial-failure retry.

## The handle, and why it is addressable

The section lists each candidate as ``[[art:<kind>:<id>:<name>]]`` (or
``[[art:campaign:<name>]]`` for the library, which has no record) and its
description. The model writes one back; `resolve_handles` rewrites it into
markdown before the reply is split into posts, so no handle is ever stored.

The handle is *addressable* rather than an opaque token because
`_persist_reply` — the single funnel every generation passes through — takes
only ``(cid, sid, text)`` and is reached from five call sites. Carrying the
offered catalogue to all of them, through the streaming finalizers, is a much
larger change than the feature; resolving statelessly needs nothing carried.

What statelessness costs is that "the model wrote a handle" and "the model was
offered that handle" stop being the same question. `resolve_handles` therefore
carries its OWN gate rather than inheriting the pool's — the first draft of this
module claimed visibility rules were inherited here too, and that claim was
simply false: a handle naming a `gm-only` location's art resolved, though the
catalogue would never offer it and that location's body never reaches a prompt
at all. A handle is not a lucky guess to be indulged. So resolution requires:

1. the image exists and is visible in this campaign (`overlay.image_root`);
2. it carries a **non-empty description** — so only art an author deliberately
   wrote up is reachable, never any file in the store;
3. its record is one this scene could legitimately show — a `gm-only` entity is
   refused outright, and, when the caller passes `sid`, an actor must actually
   be cast in that scene.

What rule 3 deliberately does NOT re-derive is the scene-scoped part of world-
info gating (owner gating, and which entries activated). Those depend on state
this function is not given, and the honest description of the guarantee is the
three rules above rather than a fourth one that only looks like the catalogue's.

The version is deliberately absent from the handle. It is not the model's to
choose: resolution uses the campaign's locked version, the same one the
catalogue was built from and the one that is actually speaking.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ... import embeddings
from ...llm_errors import LLMError
from .. import (
    assets,
    campaign_images,
    characters,
    config,
    embed_space,
    entities,
    image_descriptions,
    overlay,
    pcs,
    vectors,
)
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..appearances import versions as appearances_versions
from . import world_state

#: The library's own kind, in the handle grammar. Not an entity kind and not an
#: actor kind -- `store.campaign_images` is art belonging to the campaign and to
#: no record, which is why it needs a name here at all.
LIBRARY = "campaign"

#: Every kind a handle may name. Actors carry versions; entity kinds are stored
#: at `vid="default"`; the library has neither an id nor a version.
ACTOR_KINDS: tuple[str, ...] = ("characters", "pcs")
RECORD_KINDS: tuple[str, ...] = ACTOR_KINDS + entities.ENTITY_KINDS

#: ``[[art:kind:id:name]]`` / ``[[art:campaign:name]]``.
#:
#: The field pattern is a denylist (anything but ``:`` and ``]``) rather than an
#: ASCII allowlist, for the reason `campaign_images.UNADDRESSABLE` states: a
#: library is not English and an image name in any script is a perfectly good
#: name. Neither excluded character can occur in a real one -- `paths.safe_id`
#: rejects the colon outright, and `assets.storable` rejects the glob
#: metacharacters, which include ``]`` -- so the grammar cannot be made
#: ambiguous by a legitimately-named image.
#: A MATCHED pair of backticks around the handle is consumed with it. The
#: section prints handles bare now, but markdown habit is strong and a model
#: that fences one would otherwise get `` `![alt](url)` `` -- a code span that
#: renders as literal text, so the picture silently never appears. Matched via
#: a backreference, so a lone backtick belonging to something else is left
#: where it is.
HANDLE = re.compile(
    r"(?P<tick>`?)\[\[art:([^:\]\[]+):([^:\]\[]+)(?::([^:\]\[]+))?\]\](?P=tick)")

#: The two knobs' defaults, read from `config` rather than spelled again here.
#: They have to agree with the values `read_config` materializes into a fresh
#: config.md -- a second copy would disagree the first time either moved, and
#: the disagreement would show up as "the file says 4 and the code uses 6".
DEFAULT_DEPTH = int(config.DEFAULT_ART_CATALOG_DEPTH)
DEFAULT_THRESHOLD = float(config.DEFAULT_ART_CATALOG_THRESHOLD)

#: Content words a description must share with the scan window to be offered at
#: all in keyword mode. Two, because one is noise -- a description mentioning
#: "night" would otherwise surface in every night scene in the campaign.
KEYWORD_MIN_TERMS = 2

#: What being named in the scan window is worth. One, so a named record with no
#: shared words still ranks below a description that shares two -- naming is
#: evidence, not a trump card.
NAME_BONUS = 1.0

#: What a named record is worth in SEMANTIC mode when the cosine did not reach
#: it. Positive, so it is offered at all; far below any usable cosine threshold,
#: so it never outranks art the embedding actually matched.
NAMED_FLOOR = 1e-3

#: UTF-8 bytes of the scan window that get embedded, and of a description. Both
#: bounds are `semantic.py`'s and are bytes for its reason: characters are the
#: wrong unit for a token window in a non-Latin script.
QUERY_BYTES = 3000
DOC_BYTES = 2000

#: Words too common to carry a match. Deliberately tiny -- this is not a
#: stoplist for an index, it is the handful of words that would otherwise let
#: any two sentences of English "share content".
_STOP = frozenset((
    "about", "above", "after", "again", "against", "because", "been", "before",
    "being", "below", "between", "both", "came", "come", "does", "down",
    "during", "each", "from", "further", "have", "here", "into", "more",
    "most", "much", "must", "once", "only", "other", "over", "same", "some",
    "such", "than", "that", "them", "then", "there", "these", "they", "this",
    "those", "through", "under", "until", "very", "were", "what", "when",
    "where", "which", "while", "with", "your",
))

#: Words shorter than this never count as a shared term. With `_STOP` above,
#: this is what keeps "the grey quay" from matching on "grey".
_MIN_WORD = 4

_WORD = re.compile(r"\w+", re.UNICODE)

#: A name `\b` can actually bound: ASCII word characters plus the punctuation
#: that shows up inside one (``Mara O'Dell``, ``Jean-Luc``, ``Dr. Winifred``).
#: Anything else — a name in a script without word spacing — falls back to a
#: substring test, because `\b` sits between a word character and a non-word
#: one and two adjacent CJK characters are both word characters, so the
#: boundary never appears and the name would simply never match.
_BOUNDED_NAME = re.compile(r"^[\w\s'\-.]+$", re.ASCII)


def _is_named(name: str, text: str) -> bool:
    """Is `name` present in `text` as a name rather than as a substring?

    `world_state.keyword_hit` is the rule wherever it can apply -- it exists,
    in its own words, so archive retrieval "selects by exactly these semantics
    rather than a lookalike that drifts from them", and a substring test here
    was precisely that lookalike. Looser, too: it made a character called Rain
    count as named by the word "training", and short names (Ash, Ari, Ivo) are
    common enough that this is a steady source of art nobody asked for.

    The fallback is not a loophole but the CJK case: see `_BOUNDED_NAME`.
    """
    if not name:
        return False
    if _BOUNDED_NAME.match(name):
        return world_state.keyword_hit([name], text)
    return name.casefold() in text.casefold()

_CLIENT = embeddings.EmbeddingsClient()


# ---- the handle grammar ----------------------------------------------------

def handle_for(kind: str, rid: str, name: str) -> str:
    """The handle the section prints for one candidate."""
    if kind == LIBRARY:
        return f"[[art:{LIBRARY}:{name}]]"
    return f"[[art:{kind}:{rid}:{name}]]"


def parse_handle(match: re.Match) -> tuple[str, str, str] | None:
    """``(kind, rid, name)`` for one `HANDLE` match, or None if it names a kind
    this store has no images under. The library form has two fields and every
    other has three, so arity alone decides which was written."""
    a, b, c = match.group(2), match.group(3), match.group(4)
    if c is None:
        return (LIBRARY, "", b) if a == LIBRARY else None
    return (a, b, c) if a in RECORD_KINDS else None


def _version(cid: str, kind: str, rid: str) -> str | None:
    """The version a handle resolves against: the campaign's LOCKED version for
    an actor, ``default`` for an entity kind. None when an actor is not cast,
    which makes the handle unresolvable -- correctly, since the catalogue only
    ever offers art of actors who are on stage."""
    if kind in ACTOR_KINDS:
        return appearances_versions.locked_version(cid, kind, rid)
    return "default"


def url_for(cid: str, kind: str, rid: str, vid: str, name: str) -> str:
    """The campaign-scoped serving URL for one image.

    Campaign-scoped for every kind, including art the campaign inherits from
    its world: a post carrying a world URL is the one image shape that does not
    follow a campaign which later diverges, which is the reason
    `PostImagePicker` refuses to offer greeting art at all.

    Bare, with no ``?v=`` token, though the picker uses one for its thumbnails.
    A ``?v=`` URL is answered ``immutable, max-age=1y`` and this one is about to
    be written into a transcript that outlives every cache: replacing the image
    under the same name would leave the post pinned for a year to bytes that are
    gone. Bare revalidates, which an ETag answers with a 304.

    **Percent-encoded, segment by segment.** `campaign_images.addressable` keeps
    the library's names inside what a markdown link can carry, but the other
    three surfaces have no such rule: `assets.storable` is `safe_id` plus a
    glob-metacharacter ban, and it accepts ``art(1)``, ``my art`` and ``a#b``
    -- each of which ends a markdown destination early and leaves the rest of
    the URL loose in the prose. Encoding rather than refusing, because the image
    is real and the author's: `quote` with no safe set turns the name into
    something a link can hold, and the serving route decodes the path parameter
    straight back. (`safe=""` also encodes ``/``, so a name cannot invent a path
    segment.)
    """
    def e(seg: str) -> str:
        return quote(str(seg), safe="")

    base = f"/api/campaigns/{cid}"
    if kind == LIBRARY:
        return f"{base}/images/{e(name)}"
    if kind in ACTOR_KINDS:
        return f"{base}/{kind}/{e(rid)}/versions/{e(vid)}/images/{e(name)}"
    return f"{base}/{kind}/{e(rid)}/images/{e(name)}"


# ---- the candidate pool ----------------------------------------------------

def _record_candidates(cid: str, kind: str, rid: str) -> list[dict]:
    """Every described, visible image of one record, at its locked version."""
    vid = _version(cid, kind, rid)
    if vid is None:
        return []
    out = []
    for name, text in sorted(overlay.read_descriptions(cid, rid, vid, base=kind).items()):
        if not text.strip():
            continue   # reviewed-empty: deliberately not offered
        out.append({"kind": kind, "id": rid, "vid": vid, "name": name,
                    "description": text.strip(),
                    "handle": handle_for(kind, rid, name),
                    "url": url_for(cid, kind, rid, vid, name)})
    return out


def _library_candidates(cid: str) -> list[dict]:
    d = campaign_images.images_dir(cid)
    names = {i["name"] for i in campaign_images.list_images(cid)}
    out = []
    for name, text in sorted(image_descriptions.read_in(d, names=names).items()):
        if not text.strip():
            continue
        out.append({"kind": LIBRARY, "id": "", "vid": "", "name": name,
                    "description": text.strip(),
                    "handle": handle_for(LIBRARY, "", name),
                    "url": url_for(cid, LIBRARY, "", "", name)})
    return out


def candidates(cid: str, cast: list[dict], current_loc: str | None,
               wi_entries: list[dict]) -> list[dict]:
    """The art this turn could offer, before ranking.

    Deduplicated on (kind, id, name): a location can be both the current
    setting and a world-info activation, and an entry can be in both the
    keyword and the recalled list, so the same picture would otherwise be
    offered twice and take two of `depth`'s slots.
    """
    refs: list[tuple[str, str]] = [(a["kind"], a["id"]) for a in cast
                                   if a.get("kind") in ACTOR_KINDS]
    if current_loc:
        refs.append(("locations", current_loc))
    refs += [(e["kind"], e["id"]) for e in wi_entries
             if e.get("kind") in entities.ENTITY_KINDS]

    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for kind, rid in refs:
        for c in _record_candidates(cid, kind, rid):
            key = (c["kind"], c["id"], c["name"])
            if key not in seen:
                seen.add(key)
                out.append(c)
    for c in _library_candidates(cid):
        key = (c["kind"], c["id"], c["name"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ---- ranking ---------------------------------------------------------------

def _terms(text: str) -> set[str]:
    """The content words of `text`, case-folded. Short words and the tiny
    stoplist drop out — see `_STOP`."""
    return {w for w in (m.group(0).casefold() for m in _WORD.finditer(text))
            if len(w) >= _MIN_WORD and w not in _STOP}


def _record_name(cid: str, cand: dict) -> str:
    """The display name of the record a candidate hangs off, or ``""`` for the
    library. Used only for the name bonus below, so a read that fails is a
    missing bonus rather than a missing candidate."""
    try:
        if cand["kind"] == "pcs":
            root = appearances_paths.locked_actor_root(cid)
            return str(pcs.read_persona(root, cand["id"], cand["vid"]).get("name", ""))
        if cand["kind"] == "characters":
            root = appearances_paths.locked_actor_root(cid)
            return str(characters.read_card(root, cand["id"],
                                            cand["vid"])["data"].get("name", ""))
        if cand["kind"] in entities.ENTITY_KINDS:
            return str(overlay.read_entity(cid, cand["kind"],
                                           cand["id"])["meta"].get("name", ""))
    except (characters.CharacterNotFound, characters.VersionNotFound,
            pcs.PCNotFound, pcs.PCVersionNotFound, entities.EntityNotFound,
            OSError, UnicodeDecodeError, KeyError, TypeError):
        return ""
    return ""


def _keyword_scores(cid: str, cands: list[dict],
                    recent_text: str) -> tuple[list[float], list[bool]]:
    """Shared content words, plus a whole-record bonus.

    Two ways in, and the first one is the point:

    - **The record is NAMED in the scan window.** That alone makes its art
      eligible, scored above an equal number of shared words. "Seraphine draws
      her blade" surfaces Seraphine's art even when her picture's description
      happens to share no vocabulary with the post — the record being named is
      itself the evidence, and it is the commonest way this feature is useful.
      (An earlier version still demanded one shared word here, which made the
      sentence above false of the exact example it used.)
    - **Two shared content words**, for a record nothing named. One is noise: a
      description mentioning "night" would otherwise surface in every night
      scene in the campaign.

    Offering four pictures of whoever just spoke, when nothing else matches, is
    the accepted cost of the first rule. `depth` caps the menu and the section
    tells the model most replies should use none of it.

    **The shared-word half only works in a script that separates words.** The
    scan splits on runs of word characters, so an unsegmented Japanese or
    Chinese clause comes back as
    one enormous token and never matches another — meaning a CJK library gets
    the name rule and nothing else from keyword mode. Stated rather than left
    to be discovered: an embeddings endpoint is the answer for those languages,
    and a good one, since the models handle them natively. Segmenting properly
    is a dependency and a judgement call this module should not make alone.

    Names are resolved once per RECORD, not once per candidate: a record with a
    gallery contributes one candidate per picture, and `_record_name` opens a
    card file — so the naive version re-read one character's whole card a dozen
    times inside a single turn.
    """
    window = _terms(recent_text)
    names: dict[tuple[str, str, str], str] = {}
    out, was_named = [], []
    for c in cands:
        shared = len(_terms(c["description"]) & window)
        key = (c["kind"], c["id"], c["vid"])
        if key not in names:
            names[key] = _record_name(cid, c)
        name = names[key]
        named = _is_named(name, recent_text)
        was_named.append(named)
        if named:
            out.append(float(shared) + NAME_BONUS)
        else:
            out.append(float(shared) if shared >= KEYWORD_MIN_TERMS else 0.0)
    # The flags are returned as well as folded into the scores because semantic
    # mode replaces the SCORES and must not lose the RULE -- see `rank`.
    return out, was_named


def _semantic_scores(cands: list[dict], recent_text: str, cfg: dict) -> list[float] | None:
    """Cosines against the scan window, or None if the provider could not be
    reached — in which case the caller keeps the keyword ranking.

    Bounded the way `semantic.py` bounds its warm run: whatever does not fit in
    one request sits out this turn and is picked up by a later one, so turning
    an embeddings endpoint on over a described library costs a little latency
    per turn for a while rather than one enormous stall.
    """
    texts = [embed_space.clip(c["description"], DOC_BYTES) for c in cands]
    query_text = embed_space.clip(recent_text.strip(), QUERY_BYTES, tail=True)
    known = vectors.load(cfg["space"], texts)
    uncached = list(dict.fromkeys(t for t in texts if t not in known))
    missing = embed_space.warm_window(uncached, query_text, embeddings.BATCH - 1)
    try:
        got = _CLIENT.embed([query_text, *missing], cfg["model"], cfg["key"], cfg["base_url"])
    except (LLMError, OSError):
        # Deliberately silent, and deliberately not fatal: this runs on every
        # turn, so a provider that is down would otherwise write one identical
        # line per message -- and the keyword ranking the caller already has is
        # a real answer, not a degraded one.
        return None
    if len(got) != 1 + len(missing):
        return None
    query = vectors.unit(got[0])
    if query is None:
        return None
    for text, raw in zip(missing, got[1:], strict=False):
        vectors.save(cfg["space"], text, raw)
        fresh = vectors.unit(raw)
        if fresh is not None:
            known[text] = fresh

    out = []
    for text in texts:
        vector = known.get(text)
        if vector is None or len(vector) != len(query):
            if vector is not None:
                # The endpoint now answers this model id at a different width.
                # Evicting rather than merely skipping is what heals it: a stale
                # vector is still a cache HIT, so it would never be re-embedded
                # and its image would drop out of the catalogue permanently.
                vectors.forget(cfg["space"], text)
            out.append(0.0)
            continue
        score = vectors.dot(query, vector)
        if not -1.0 <= score <= 1.0:
            vectors.forget(cfg["space"], text)   # a cache file corrupted in place
            out.append(0.0)
            continue
        out.append(score if score >= cfg["threshold"] else 0.0)
    return out


def _int(value: object, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: object, default: float) -> float:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    # nan fails every comparison, so a hand-edited "nan" would read as "offer
    # nothing" rather than as the mistake it is.
    return out if -1.0 <= out <= 1.0 else default


def settings() -> dict:
    """Depth and threshold, read the tolerant way every config reader here does.

    Never None and never raising: unlike semantic recall, this layer has no
    off switch of its own — the prompt layout is the off switch, per the design
    — so there is no configuration state that means "disabled".
    """
    try:
        cfg = config.read_config()
    except (OSError, UnicodeDecodeError, ValueError):
        return {"depth": DEFAULT_DEPTH, "threshold": DEFAULT_THRESHOLD}
    return {"depth": max(_int(cfg.get("art_catalog_depth"), DEFAULT_DEPTH), 0),
            "threshold": _float(cfg.get("art_catalog_threshold"), DEFAULT_THRESHOLD),
            "cfg": cfg}


def rank(cid: str, cands: list[dict], recent_text: str) -> list[dict]:
    """The best `depth` candidates for this moment, best first.

    Keyword scores are computed first and always: they are the fallback, and
    computing them costs a set intersection per candidate. A configured
    embeddings endpoint then replaces the SCORES rather than adding to them —
    the two are not on one scale, and averaging them would mean neither
    threshold meant anything.

    It does not replace the *rule* that a record named in the scan window has
    its art offered. Wholesale replacement did, which made "semantic as an
    upgrade" false: configuring an endpoint silently switched off the commonest
    reason this feature is useful, because a description that never mentions
    Seraphine is not close to a sentence about her either. So a named record
    that the cosine did not reach keeps a floor — positive, and below any
    sensible threshold, so it is offered last and only when `depth` has room
    left over.
    """
    if not cands or not recent_text.strip():
        return []
    opts = settings()
    if opts["depth"] <= 0:
        return []
    scores, named = _keyword_scores(cid, cands, recent_text)
    space = embed_space.resolve(opts.get("cfg"))
    if space is not None:
        semantic = _semantic_scores(cands, recent_text,
                                    {**space, "threshold": opts["threshold"]})
        if semantic is not None:
            scores = [s if s > 0.0 else (NAMED_FLOOR if was else 0.0)
                      for s, was in zip(semantic, named, strict=True)]
    ranked = sorted(((-s, i) for i, s in enumerate(scores) if s > 0.0))
    return [cands[i] for _, i in ranked[:opts["depth"]]]


def catalogue(cid: str, cast: list[dict], current_loc: str | None,
              wi_entries: list[dict], recent_text: str) -> list[dict]:
    """What the ``available_art`` section renders, or ``[]``.

    Never raises. A store being synced under us, a half-written sidecar, a
    campaign whose world went away: none of them is worth losing a turn to, and
    the section simply does not render.
    """
    try:
        return rank(cid, candidates(cid, cast, current_loc, wi_entries), recent_text)
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError,
            entities.EntityNotFound):
        return []


# ---- the return path -------------------------------------------------------

def _showable(cid: str, kind: str, rid: str, sid: str | None) -> bool:
    """Could this scene legitimately show something belonging to `rid`?

    Two rules, both cheap enough to run on every handle:

    - **A `gm-only` entity is refused.** Its body never reaches a prompt (see
      `world_state.activate`), so a picture of it appearing in a post the player
      reads is a straight leak. `secret` is deliberately NOT refused: a secret
      entry's body *does* reach the prompt, the catalogue does offer its art,
      and refusing it here would make the two halves disagree.
    - **An actor must be cast in this scene**, when `sid` is given. That is the
      catalogue's own rule for actors (`_version` returns the LOCKED version,
      which only a cast actor has), restated where statelessness would
      otherwise drop it: without this, art of anyone the campaign has ever cast
      resolves in any scene.
    """
    if kind in ACTOR_KINDS:
        if sid is None:
            return True   # no scene in hand; rules 1 and 2 still stand
        return any(a["kind"] == kind and a["id"] == rid
                   for a in appearances_cast.scene_cast(cid, sid))
    if kind in entities.ENTITY_KINDS:
        try:
            meta = overlay.read_entity(cid, kind, rid)["meta"]
        except (entities.EntityNotFound, OSError, UnicodeDecodeError, KeyError):
            return False
        return entities.normalize_secrecy(meta.get("secrecy")) != entities.GM_ONLY
    return True


def _resolved(cid: str, kind: str, rid: str, name: str,
              sid: str | None = None) -> dict | None:
    """The image a handle names, if all three rules in the module docstring
    hold. None otherwise."""
    if kind != LIBRARY and not _showable(cid, kind, rid, sid):
        return None
    if kind == LIBRARY:
        if campaign_images.image_path(cid, name) is None:
            return None
        d = campaign_images.images_dir(cid)
        names = {i["name"] for i in campaign_images.list_images(cid)}
        text = image_descriptions.read_in(d, names=names).get(name, "")
        return {"url": url_for(cid, LIBRARY, "", "", name),
                "description": text.strip()} if text.strip() else None
    vid = _version(cid, kind, rid)
    if vid is None:
        return None
    root = overlay.image_root(cid, rid, vid, name, base=kind)
    if assets.image_path(root, rid, vid, name, base=kind) is None:
        return None
    text = overlay.read_description(cid, rid, vid, name, base=kind).strip()
    return {"url": url_for(cid, kind, rid, vid, name), "description": text} if text else None


def resolve_handles(cid: str, text: str, sid: str | None = None) -> str:
    """`text` with every art handle rewritten to markdown, unknown ones removed.

    Runs once per generation, inside `_persist_reply`, before the reply is split
    into posts — so a handle never becomes a post of its own and none is ever
    written to a transcript.

    The alt text is the description rather than the image's name, and it is not
    optional decoration: a plain-text export, and a model later sent this
    transcript as text, get the alt text and nothing else. `PostImagePicker
    .insertion` makes the same choice for the same reason.

    **At most one picture per reply**, which is what the section asks for --
    enforced here rather than left as advice. Every other clause of that
    contract is a rule resolution applies (the handle must name a real,
    described, showable image); leaving the count to the model's goodwill made
    this one the exception, and a model offered four candidates has an obvious
    way to use all four. The first resolvable handle wins and later ones are
    deleted, exactly as an unresolvable one is.

    Neither half of the markdown needs escaping. ``]`` would close the alt text
    and ``)`` the destination: `assets.storable` refuses the glob
    metacharacters (so no name holds ``]``) and `url_for` percent-encodes what
    it writes. A DESCRIPTION may hold either, so it is the one thing escaped.
    """
    if "[[art:" not in text:
        return text   # the overwhelmingly common case, at the cost of one scan

    used = False

    def sub(m: re.Match) -> str:
        nonlocal used
        parsed = parse_handle(m)
        if parsed is None or used:
            return ""
        hit = _resolved(cid, *parsed, sid=sid)
        if hit is None:
            return ""
        used = True
        alt = hit["description"].replace("[", "(").replace("]", ")").replace("\n", " ")
        return f"![{alt}]({hit['url']})"

    return HANDLE.sub(sub, text)
