"""BM25-shaped pure-Python ordering; only a signal's own admission ranks.

Document statistics are keyed by supplied live document digests. Corpus
frequency is per query: a warm cache cannot preserve yesterday's live set.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TypeGuard

from .. import compiled
from ..searchdocs import scene
from . import model


def _statistics(text: str) -> dict:
    words = re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())
    return {"counts": dict(Counter(words)), "length": len(words), "text": " ".join(words)}


def _valid(value: object) -> TypeGuard[dict]:
    return (
        isinstance(value, dict)
        and isinstance(value.get("counts"), dict)
        and all(
            isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in value["counts"].items()
        )
        and isinstance(value.get("length"), int)
        and value["length"] >= 0
        and isinstance(value.get("text"), str)
    )


def statistics(docs: Sequence[scene.SceneDocument]) -> list[dict]:
    keys = [
        compiled.make_key(compiled.HISTORY_TERMS, [], params={"document_key": d.key}) for d in docs
    ]
    with compiled.batch() as request:
        hits = request.lookup_many(keys).values
    out, pending = [], []
    for doc, key in zip(docs, keys, strict=True):
        hit = hits.get(key)
        value = hit.value if hit is not None and hit.hit else None
        if not _valid(value):
            value = _statistics(doc.text)
            pending.append((key, value))
        assert isinstance(value, dict)
        out.append(value)
    # 03 caps staged rows: every document still scores if the cache refuses.
    for start in range(0, len(pending), compiled.MAX_STAGED_ROWS):
        with compiled.batch() as request:
            for key, value in pending[start : start + compiled.MAX_STAGED_ROWS]:
                request.put(key, value)
    return out


def admits(signal: model.LexicalSignal, weights: Mapping[str, int] | None = None) -> bool:
    weights = weights or {}
    return any(weights.get(term, 1) > 1 for term in signal.matched) or len(signal.matched) >= 2


def score(
    terms: Sequence[str],
    docs: Sequence[scene.SceneDocument],
    *,
    weights: Mapping[str, int] | None = None,
    others: Sequence[scene.SceneDocument] = (),
) -> list[model.LexicalSignal]:
    """One signal per document of `docs`. `others` count toward the corpus
    statistics (document frequency, average length) and are not scored: tier 3
    scores each named source over every source's documents, so that raw scores
    from different campaigns are one ordering rather than several."""
    if not docs:
        return []
    weights = weights or {}
    terms = tuple(dict.fromkeys(terms))
    stats = statistics([*docs, *others])
    frequencies = []
    for term in terms:
        words = re.findall(r"\w+", unicodedata.normalize("NFKC", term).casefold())
        phrase = " ".join(words)
        if not phrase:
            frequencies.append([0] * len(stats))
        elif len(words) == 1:
            frequencies.append([s["counts"].get(phrase, 0) for s in stats])
        else:
            pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)")
            frequencies.append([len(pattern.findall(s["text"])) for s in stats])
    avg = sum(s["length"] for s in stats) / len(stats) or 1
    idfs = [
        math.log(1 + (len(stats) - sum(f > 0 for f in fs) + 0.5) / (sum(f > 0 for f in fs) + 0.5))
        for fs in frequencies
    ]
    out = []
    for i, stat in enumerate(stats[: len(docs)]):
        total, matched = 0.0, []
        for term, fs, idf in zip(terms, frequencies, idfs, strict=True):
            freq = fs[i]
            if not freq:
                continue
            matched.append(term)
            total += (
                weights.get(term, 1)
                * idf
                * freq
                * 2.2
                / (freq + 1.2 * (0.25 + 0.75 * stat["length"] / avg))
            )
        out.append(model.LexicalSignal(total, tuple(matched)))
    return out
