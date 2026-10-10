"""The synthetic retrieval corpus (`corpus.json`) and its structural rules.

Three shapes, one per kind of production caller: `recall` (a scene's last
posts against lore entries), `search` (a reader's question against library
records) and `identity` (a reworded record against the record it restates,
compared symmetrically, as documents). Each query names its relevant
document ids and its lexical decoys. Every query ranks the WHOLE pool --
every shape's documents and the fillers -- so a ranking has at least ten
documents per unit of the largest k (`metrics.KS`); the size is structural,
to be tuned against real libraries later, never measured from one.

Two rules make a lexical scorer unable to pass: a paraphrase positive shares
no content word with its query (the miss recall exists for), and a decoy
shares at least one (a query's names and words, about something else).
Names are only the codebase's placeholders (Seraphine, Mara, Winifred,
Realm, Saltmarch).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: Where the corpus lives.
PATH = Path(__file__).resolve().parent / "corpus.json"

#: The shapes, each with the production embed task it stands for and whether
#: its query is sent as a query (`queries=`) or, symmetric, as a document.
SHAPES: dict[str, tuple[str, bool]] = {
    "recall": ("semantic-recall", True),
    "search": ("semantic-search", True),
    "identity": ("continuity-similarity", False),
}

#: Words that carry no content: what "shares no content word" ignores.
STOPWORDS = frozenset({
    "a", "about", "after", "again", "all", "an", "and", "any", "are", "as", "at", "be",
    "been", "before", "beneath", "beside", "between", "but", "by", "can", "cannot", "come",
    "comes", "did", "do", "does", "every", "for", "from", "had", "has", "her", "hers",
    "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "may", "me", "more",
    "most", "my", "never", "no", "nobody", "none", "nor", "not", "now", "of", "off", "on",
    "once", "one", "only", "or", "our", "out", "over", "she", "so", "some", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those", "through",
    "to", "toward", "under", "up", "upon", "was", "we", "were", "what", "when", "where",
    "whenever", "which", "while", "who", "whom", "whose", "why", "will", "with", "would",
    "yet", "you", "your",
})

_WORD = re.compile(r"[a-z]+")


def content_words(text: str) -> set[str]:
    """The content words of `text`: lower-cased letters-only words, without
    a possessive or plural `s`, minus `STOPWORDS`."""
    out = set()
    for word in _WORD.findall(text.lower().replace("'s", "")):
        if word in STOPWORDS or len(word) < 2:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss")
                else word)
    return out


@dataclass(frozen=True)
class Document:
    id: str
    shape: str
    text: str


@dataclass(frozen=True)
class Query:
    id: str
    shape: str
    text: str
    relevant: tuple[str, ...]
    decoys: tuple[str, ...]


@dataclass(frozen=True)
class Corpus:
    documents: tuple[Document, ...]
    queries: tuple[Query, ...]

    def doc(self, doc_id: str) -> Document:
        return next(d for d in self.documents if d.id == doc_id)


def load(path: Path = PATH) -> Corpus:
    """The corpus at `path`."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Corpus(
        tuple(Document(d["id"], d["shape"], d["text"]) for d in raw["documents"]),
        tuple(Query(q["id"], q["shape"], q["text"], tuple(q["relevant"]), tuple(q["decoys"]))
              for q in raw["queries"]))


def _id_problems(corpus: Corpus) -> list[str]:
    """Repeated ids, unknown shapes, and queries naming nothing or unknown ids."""
    ids = [d.id for d in corpus.documents]
    known = set(ids)
    out = ["document ids repeat"] if len(known) != len(ids) else []
    if len({q.id for q in corpus.queries}) != len(corpus.queries):
        out.append("query ids repeat")
    out += [f"{q.id}: unknown shape {q.shape!r}" for q in corpus.queries
            if q.shape not in SHAPES]
    out += [f"{q.id}: names no relevant document" for q in corpus.queries if not q.relevant]
    out += [f"{q.id}: unknown document {doc_id!r}" for q in corpus.queries
            for doc_id in (*q.relevant, *q.decoys) if doc_id not in known]
    return out


def _word_problems(corpus: Corpus) -> list[str]:
    """Positives that share a content word with their query, and decoys that
    share none."""
    texts = {d.id: content_words(d.text) for d in corpus.documents}
    out: list[str] = []
    for q in corpus.queries:
        words = content_words(q.text)
        out += [f"{q.id}: positive {doc_id} shares {sorted(words & texts[doc_id])}"
                for doc_id in q.relevant if doc_id in texts and words & texts[doc_id]]
        out += [f"{q.id}: decoy {doc_id} shares no word with its query"
                for doc_id in q.decoys if doc_id in texts and not words & texts[doc_id]]
    return out


def problems(corpus: Corpus) -> list[str]:
    """Everything that breaks the corpus's rules (the module docstring)."""
    return [*_id_problems(corpus), *_word_problems(corpus)]
