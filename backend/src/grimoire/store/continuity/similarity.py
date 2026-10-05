"""Identity texts and the deterministic similarity signals (capstone spec §9).

**Scores rank candidates; they never write (§3.2).** Everything here answers
"which stored records are worth asking about", and nothing more: a high score
merges nothing, retargets nothing and is never shown as a verdict. The resolver
(and after it, the reader) decides; this module only chooses what they see.

**The identity text** (§9.1) is a plain, id-free rendering of a record -- a
type label, the title, the latest beat and a couple of earlier ones (and, for a
commitment, its due phrase) -- separate from every UI and prompt rendering so a
template edit cannot move it. Ids are left out because a slug is a lexical
accident of whoever named the record first, not a statement of what it is about.
The text is what an embedding is taken of; its first line, the type label, is
dropped by `body` before the lexical signals are computed, because it is
identical across every same-type pair and would only add a constant to every
score (a clarification of §9.2). A commitment's ``due`` prefix goes with it for
the same reason: every dated commitment carries that word, so it would admit
two unrelated same-cast commitments through the structural clause.

**The constants** below are candidate-generation parameters, not truth
thresholds. Each is justified by the identity-text layout and by the cost
model -- a false positive costs a share of one batched resolver call, a false
negative costs a duplicate record -- and every one is to be tuned against real
prompts later.

Slice D's reconcile sweep reuses `pool`, `lexical` and `nearest` unchanged
(and the embedding mechanics, `semantic`, that sit beside them), so a pair the
sweep raises was scored by exactly the rules absorb staged under.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from typing import Literal

from .. import embed_space, fieldtext, paths, vectors
from . import effective, involvement

#: The identity text's byte bound. The text is a label, a title, at most three
#: one-sentence beats and a due phrase; 2000 bytes holds that even in a
#: three-byte script, and sits far below `semantic.DOC_BYTES`, so an identity
#: text is never the input a provider rejects. To be tuned against real prompts
#: later.
CONTINUITY_IDENTITY_BYTES = 2000

#: Earlier beats carried after the latest one (spec §9.1): enough to say what a
#: record has been about without letting a long history drown its title.
PREVIOUS_BEATS = 2

#: Neighbours kept per proposed-new record (spec §9.3). Each one is a line in a
#: shared resolver prompt and a chip alternative a reviewer reads.
IDENTITY_TOP_K = 3

#: English function words removed before token Jaccard only. Every English
#: sentence shares them, so they say nothing about identity; left in, two
#: unrelated records sharing only "the" clear `WEAK_TOKEN`, and the structural
#: clause then makes nearly every same-cast pair plausible. "s" and "t" are the
#: residue `normalize` leaves of "'s" and "n't". Character 3-grams keep them,
#: and text with no spaces (CJK) has no such tokens at all, so it is unaffected.
STOPWORDS = frozenset({
    # articles
    "a", "an", "the",
    # common prepositions
    "of", "in", "on", "at", "to", "for", "from", "by", "with", "into", "onto",
    "over", "under", "about", "as", "upon",
    # conjunctions
    "and", "or", "but", "nor", "so", "yet",
    # possessive pronouns
    "my", "your", "his", "her", "its", "our", "their",
    # the residue of 's and n't
    "s", "t",
})

#: A loose lexical floor. On a short text the title is a large share of the
#: tokens, so sharing a title phrase alone approaches it; two records sharing
#: only incidental words, or a single long word, fall well below it. To be
#: tuned against real prompts later.
TOKEN_FLOOR = 0.25

#: The 3-gram floor, higher than the token one because 3-grams overlap on
#: shared affixes and short function syllables that tokens never do. It carries
#: the lexical score for text with no whitespace tokens. To be tuned against
#: real prompts later.
CHAR_FLOOR = 0.35

#: Loose by design: §9.3 keeps the recall threshold apart from the duplicate
#: threshold, and models differ in cosine scale. To be tuned against real
#: prompts later.
COSINE_FLOOR = 0.5

#: The weak floors a structural signal must be paired with. Shared actors alone
#: are a weak signal in a small cast, where every record shares someone. They
#: are also the lexical pre-filter that picks which uncached neighbours to warm.
#: To be tuned against real prompts later.
WEAK_TOKEN = 0.1
WEAK_CHAR = 0.15
WEAK_COSINE = 0.35

#: What the ledger readers raise on a file that will not parse or holds the
#: wrong shape -- the whole of what `pool` answers empty for.
_UNREADABLE = (OSError, ValueError, TypeError, KeyError, AttributeError)

Kind = Literal["thread", "commitment"]


# ------------------------------------------------------------ identity text


def _beat_texts(rec: dict) -> list[str]:
    beats = rec.get("beats")
    if not isinstance(beats, list):
        return []
    return [fieldtext.text(b.get("text")) for b in beats if isinstance(b, dict)]


def _beat_lines(rec: dict) -> tuple[str, list[str]]:
    """The latest beat, and up to `PREVIOUS_BEATS` earlier non-blank beats
    newest first, none equal to a beat already included."""
    texts = _beat_texts(rec)
    latest = fieldtext.text(rec.get("latest_beat")) or (texts[-1] if texts else "")
    included = {latest} if latest else set()
    previous: list[str] = []
    for text in reversed(texts[:-1]):
        if len(previous) >= PREVIOUS_BEATS:
            break
        if text and text not in included:
            previous.append(text)
            included.add(text)
    return latest, previous


def _join(lines: Iterable[str]) -> str:
    text = "\n".join(line for line in lines if line)
    return embed_space.clip(text, CONTINUITY_IDENTITY_BYTES)


def thread_identity_text(rec: dict) -> str:
    """The thread's identity text: label, title, latest beat, earlier beats."""
    latest, previous = _beat_lines(rec)
    return _join(["plot thread", fieldtext.text(rec.get("title")), latest, *previous])


def commitment_identity_text(rec: dict) -> str:
    """The commitment's identity text: kind label, title, latest beat, due,
    earlier beats. A blank kind is the default `promise`."""
    latest, previous = _beat_lines(rec)
    kind = fieldtext.text(rec.get("kind")) or "promise"
    due = fieldtext.text(rec.get("due"))
    return _join([f"{kind} commitment", fieldtext.text(rec.get("title")), latest,
                  f"due {due}" if due else "", *previous])


_TEXT = {"thread": thread_identity_text, "commitment": commitment_identity_text}


# ---------------------------------------------------------------- lexical


def _kept(ch: str) -> bool:
    """Alphanumerics, and combining marks: a Devanagari vowel sign or virama
    is part of its word, and splitting on it would leave bare consonants that
    any two texts in the script share."""
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def normalize(text: str) -> str:
    """NFKC, casefolded, every char that is neither alphanumeric nor a
    combining mark a space, whitespace collapsed."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join("".join(ch if _kept(ch) else " " for ch in folded).split())


def title_key(title) -> str:
    return normalize(fieldtext.text(title))


def titles_equal(a, b) -> bool:
    key = title_key(a)
    return key == title_key(b) != ""


def _whole_slug(title) -> str | None:
    """The title's slug when it spells every character of the title's key, else
    None. `paths.slugify` drops whatever is not ASCII, so "Mara的海図" and
    "Mara的灯台" both slug to ``mara``, and two different CJK titles both fall
    back to ``untitled``: a slug that lost part of its title says nothing."""
    slug = paths.slugify(fieldtext.text(title))
    key = title_key(title)
    if re.fullmatch(r"[a-z0-9 ]+", key) and key.replace(" ", "") == slug.replace("-", ""):
        return slug
    return None


def slug_equal(a_title, b_title) -> bool:
    """Equal slugs, counted only when each slug spells its whole title."""
    a = _whole_slug(a_title)
    return a is not None and a == _whole_slug(b_title)


def tokens(text: str) -> frozenset[str]:
    return frozenset(normalize(text).split()) - STOPWORDS


def trigrams(text: str) -> frozenset[str]:
    norm = normalize(text)
    if len(norm) < 3:
        return frozenset({norm}) if norm else frozenset()
    return frozenset(norm[i:i + 3] for i in range(len(norm) - 2))


def jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


#: The literal prefix of a commitment's due line (`commitment_identity_text`).
_DUE = "due "


def body(text: str) -> str:
    """The identity text without its type-label line and, in a commitment's,
    without the ``due`` prefix every dated commitment shares (the due phrase
    itself stays). A beat that happens to begin with lowercase "due " loses
    that one word too, which costs a single token."""
    label, _, rest = text.partition("\n")
    if not label.endswith(" commitment"):
        return rest
    return "\n".join(line.removeprefix(_DUE) for line in rest.split("\n"))


# ---------------------------------------------------------------- subjects


class Subject:
    """One record as similarity sees it: its identity text and the structure
    (actors, scenes, reviewed event anchors) it shares with others."""

    __slots__ = ("actors", "anchors", "kind", "live", "record", "ref", "scenes", "status",
                 "text", "title")

    def __init__(self, ref: str, kind: Kind, title: str, status: str, live: bool,
                 text: str, actors: frozenset[str], scenes: frozenset[str],
                 anchors: frozenset[str], record: dict):
        self.ref = ref
        self.kind = kind
        self.title = title
        self.status = status
        self.live = live
        self.text = text
        self.actors = actors
        self.scenes = scenes
        self.anchors = anchors
        self.record = record

    def __repr__(self) -> str:
        return f"Subject({self.ref!r})"


def subject(kind: Kind, ref: str, record: dict, *, actors: Iterable[str] = (),
            scenes: Iterable[str] = (), anchors: Iterable[str] = ()) -> Subject:
    status = fieldtext.text(record.get("status"))
    return Subject(ref, kind, fieldtext.text(record.get("title")), status,
                   effective.is_live(kind, status), _TEXT[kind](record),
                   frozenset(actors), frozenset(scenes), frozenset(anchors), record)


def _anchors(cid: str, refs: set[str]) -> dict[str, set[str]]:
    """Ref -> the ``event:*`` endpoints of the effective links touching it."""
    out: dict[str, set[str]] = {}
    for link in effective.links(cid):
        a, b = link.get("a"), link.get("b")
        for mine, other in ((a, b), (b, a)):
            if mine in refs and isinstance(other, str) and other.startswith("event:"):
                out.setdefault(mine, set()).add(other)
    return out


def pool(cid: str, kind: Kind) -> list[Subject]:
    """Every effective record of `kind` as a `Subject`, closed and resolved
    included, keyed by canonical ref and sorted by it. A ledger that will not
    read answers ``[]``: a pool is an enhancement, never a reason to fail."""
    try:
        recs = effective.records(cid, kind)
        refs = sorted(recs)
        touched = involvement.of(cid, refs)
        anchors = _anchors(cid, set(refs))
    except _UNREADABLE:
        return []
    return [subject(kind, ref, recs[ref], actors=touched[ref]["actors"],
                    scenes=touched[ref]["scenes"], anchors=anchors.get(ref, ()))
            for ref in refs]


# ----------------------------------------------------------------- signals


def lexical(a: Subject, b: Subject) -> dict:
    """The deterministic signals between two subjects (spec §9.2)."""
    body_a, body_b = body(a.text), body(b.text)
    return {
        "title_equal": titles_equal(a.title, b.title),
        "slug_equal": slug_equal(a.title, b.title),
        "tokens": round(jaccard(tokens(body_a), tokens(body_b)), 4),
        "chars": round(jaccard(trigrams(body_a), trigrams(body_b)), 4),
        "cosine": None,
        "actors": sorted(a.actors & b.actors),
        "scenes": sorted(a.scenes & b.scenes),
        "anchors": sorted(a.anchors & b.anchors),
    }


def with_cosine(signals: dict, a: Subject, b: Subject,
                vecs: dict[str, list[float]]) -> dict:
    """`signals` with ``cosine`` set when both texts have equal-width vectors in
    `vecs` (unit vectors, from `vectors`); otherwise ``cosine`` stays None."""
    va, vb = vecs.get(a.text), vecs.get(b.text)
    cosine = None
    if va and vb and len(va) == len(vb):
        value = vectors.dot(va, vb)
        cosine = round(value, 4) if math.isfinite(value) else None
    return {**signals, "cosine": cosine}


def _structure(signals: dict) -> bool:
    return bool(signals["actors"] or signals["scenes"] or signals["anchors"])


def weak(signals: dict) -> bool:
    cosine = signals["cosine"]
    return (signals["tokens"] >= WEAK_TOKEN or signals["chars"] >= WEAK_CHAR
            or (cosine is not None and cosine >= WEAK_COSINE))


def admitted_by(signals: dict) -> str | None:
    """Which clause admits the pair -- ``lexical``, ``structural`` or
    ``semantic`` (admitted ONLY through a cosine clause) -- or None."""
    if (signals["title_equal"] or signals["slug_equal"]
            or signals["tokens"] >= TOKEN_FLOOR or signals["chars"] >= CHAR_FLOOR):
        return "lexical"
    structure = _structure(signals)
    if structure and (signals["tokens"] >= WEAK_TOKEN or signals["chars"] >= WEAK_CHAR):
        return "structural"
    cosine = signals["cosine"]
    if cosine is not None and (cosine >= COSINE_FLOOR
                               or (structure and cosine >= WEAK_COSINE)):
        return "semantic"
    return None


def plausible(signals: dict) -> bool:
    return admitted_by(signals) is not None


def rank_key(signals: dict, ref: str) -> tuple:
    shared = len(signals["actors"]) + len(signals["scenes"]) + len(signals["anchors"])
    best = max(signals["tokens"], signals["chars"], signals["cosine"] or 0.0)
    return (-int(signals["title_equal"]), -int(signals["slug_equal"]), -best, -shared, ref)


def nearest(subject: Subject, candidates: list[Subject],
            vecs: dict[str, list[float]] | None = None, *,
            top_k: int = IDENTITY_TOP_K) -> list[tuple[Subject, dict]]:
    """The `top_k` plausible same-type candidates, best first, each with its
    signals and the clause that admitted it (``via``). Never padded: a pair is
    not shown because it is among the top k when every signal is weak."""
    kept: list[tuple[Subject, dict]] = []
    for other in candidates:
        if other.kind != subject.kind or other.ref == subject.ref:
            continue
        signals = lexical(subject, other)
        if vecs is not None:
            signals = with_cosine(signals, subject, other, vecs)
        via = admitted_by(signals)
        if via is not None:
            kept.append((other, {**signals, "via": via}))
    kept.sort(key=lambda pair: rank_key(pair[1], pair[0].ref))
    return kept[:top_k]
