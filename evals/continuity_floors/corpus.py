"""The labelled pair corpus for the continuity candidate floors (`corpus.json`).

Each pair is two ledger records -- two plot threads, two commitments, or one
of each -- and the answer the reconcile judge should give about them, in its
own words (`reconcile.DECISIONS`): `duplicate`, `continuation`, `related` or
`pays_off`, each a pair the candidate filter should let through to the judge,
or `distinct`, one it should not. Records are written as a ledger stores them
and read through production's own identity text (`similarity.subject`), so
the corpus says nothing about the text a signal is computed from.

Two rules, the embedding corpus's own (`evals/embed/corpus.py`, whose
`content_words` this reuses), make the measurement say something. A
`paraphrase` pair shares no content word: the duplicate only an embedding can
find, so the lexical floors miss it by construction and the report shows by
how much. A `decoy` pair shares at least one: the names and words of a
different matter, which is what a loose lexical floor lets through. Every
other positive is `reworded` and shares some words. Names are only the
codebase's placeholders (Seraphine, Mara, Winifred, Realm, Saltmarch), and
the size is structural -- every judge answer and both shapes present -- to
be grown against real prompts later, never measured from a library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from evals.embed.corpus import content_words
from grimoire.store import commitments
from grimoire.store.continuity import reconcile, similarity

#: Where the corpus lives.
PATH = Path(__file__).resolve().parent / "corpus.json"

#: The judge's answer that a pair is not worth asking about: the negatives.
NEGATIVE = "distinct"

#: Each pair's vocabulary, by the kinds of its two records (`reconcile`'s).
VOCABULARY = {("thread", "thread"): "same_thread",
              ("commitment", "commitment"): "same_commitment",
              ("thread", "commitment"): "cross", ("commitment", "thread"): "cross"}

#: The answers the corpus must carry, so no shape of pair goes unmeasured.
RELATIONS = ("duplicate", "continuation", "related", "pays_off", NEGATIVE)

#: The shapes a pair may take, by whether it is a positive.
SHAPES = {True: ("reworded", "paraphrase"), False: ("decoy",)}


@dataclass(frozen=True)
class Pair:
    id: str
    relation: str
    shape: str
    a: dict
    b: dict

    @property
    def positive(self) -> bool:
        return self.relation != NEGATIVE

    def subjects(self, actors: tuple[str, ...] = ()) -> tuple[similarity.Subject,
                                                              similarity.Subject]:
        """Both records as similarity sees them, each involving `actors`."""
        return (_subject(f"{self.id}-a", self.a, actors),
                _subject(f"{self.id}-b", self.b, actors))


def _stored(record: dict) -> dict:
    """A corpus record as its ledger stores it: beats as `{"text": ...}`."""
    out = {key: value for key, value in record.items() if key not in ("record", "beats")}
    return {**out, "beats": [{"text": text} for text in record.get("beats", ())]}


def _subject(ref: str, record: dict, actors: tuple[str, ...]) -> similarity.Subject:
    return similarity.subject(record["record"], f"{record['record']}:{ref}",
                              _stored(record), actors=actors)


@dataclass(frozen=True)
class Corpus:
    pairs: tuple[Pair, ...]


def load(path: Path = PATH) -> Corpus:
    """The corpus at `path`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Corpus(tuple(Pair(p["id"], p["relation"], p["shape"], p["a"], p["b"])
                        for p in raw["pairs"]))


def _record_problems(pair: Pair) -> list[str]:
    out = []
    for side, record in (("a", pair.a), ("b", pair.b)):
        if record.get("record") not in ("thread", "commitment"):
            out.append(f"{pair.id}: {side} is no thread or commitment")
        elif record["record"] == "commitment" and record.get("kind") not in commitments.KINDS:
            out.append(f"{pair.id}: {side} is no kind of commitment")
        if not record.get("title") or not record.get("beats"):
            out.append(f"{pair.id}: {side} has no title or no beat")
    return out


def _label_problems(pair: Pair) -> list[str]:
    vocabulary = VOCABULARY.get((pair.a.get("record"), pair.b.get("record")))
    if vocabulary is None:
        return []
    out = []
    if pair.relation not in reconcile.DECISIONS[vocabulary] or pair.relation == "uncertain":
        out.append(f"{pair.id}: {pair.relation!r} is not an answer about {vocabulary}")
    if pair.shape not in SHAPES[pair.positive]:
        out.append(f"{pair.id}: a {'positive' if pair.positive else 'negative'} pair "
                   f"cannot be {pair.shape!r}")
    return out


def _word_problems(pair: Pair) -> list[str]:
    """A paraphrase that shares a content word, and a pair of any other shape
    that shares none -- over the bodies the lexical signals read."""
    a, b = (content_words(similarity.body(s.text)) for s in pair.subjects())
    shared = sorted(a & b)
    if pair.shape == "paraphrase" and shared:
        return [f"{pair.id}: a paraphrase shares {shared}"]
    if pair.shape != "paraphrase" and not shared:
        return [f"{pair.id}: a {pair.shape} pair shares no word"]
    return []


def problems(corpus: Corpus) -> list[str]:
    """Everything that breaks the corpus's rules (the module docstring)."""
    ids = [p.id for p in corpus.pairs]
    out = ["pair ids repeat"] if len(set(ids)) != len(ids) else []
    for pair in corpus.pairs:
        record = _record_problems(pair)
        out += record or [*_label_problems(pair), *_word_problems(pair)]
    out += [f"no pair is {relation!r}" for relation in RELATIONS
            if relation not in {p.relation for p in corpus.pairs}]
    out += [f"no pair is a {shape}" for shape in (*SHAPES[True], *SHAPES[False])
            if shape not in {p.shape for p in corpus.pairs}]
    return out
