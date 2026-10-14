"""Gold-based recall and deterministic planted-token consistency."""
import re


def recalls(gold, items):
    keys = {g.key for g in gold}
    return {k: len(keys.intersection(i.scene.key for i in items[:k])) / len(keys)
            if keys else 1.0 for k in (1, 3, 5)}


def excerpt_recall(gold, items, packed):
    found = 0
    for g in gold:
        found += any(i.scene.key == g.key and any(
            set(range(g.start, g.end+1)) <= {p.index for p in e.posts}
            and all(p.text in packed for p in e.posts if g.start <= p.index <= g.end)
            for e in i.excerpts) for i in items)
    return found/len(gold) if gold else 1.0


def consistent(output, fact):
    return bool(re.search(r"(?<!\w)"+re.escape(fact)+r"(?!\w)", output, re.IGNORECASE)) and not bool(
        re.search(r"\b(?:not|never|isn't|wasn't)\s+"+re.escape(fact)+r"\b", output, re.IGNORECASE))
