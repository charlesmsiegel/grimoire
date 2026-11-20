"""The built-in consistency auditor: a deterministic multi-step worker for one source (082 milestone 1).

It lists the source, reads one entity per step and, after each read, compares the new record with every
record it has already read, reporting what it finds at once, so a run that is cancelled or runs out of
budget still hands over what it found until then. It calls no model. It looks for three things, each a
*possible* problem for a human to judge, never a fact:

- ``dangling_reference``: a payload value that is a reference into this source (``world:harbor/location/
  old-hall``) names an entity, or a pinned revision, that the source does not have;
- ``possible_duplicate``: two entities of one kind whose display names are the same once case, spacing,
  punctuation and a leading "the" are set aside, and neither refers to the other (a derived portrayal refers
  to its base, so it is not a duplicate);
- ``missing_link``: one entity's text names another entity of the source by its display name, as whole words,
  and neither record refers to the other ("Wren is sworn to the Ashen Guild", with no reference to the guild).

Every finding cites the exact revisions it compared, quoting the words it matched.
"""

from __future__ import annotations

import re
import unicodedata

from grimoire.core.identity import SourceRef

from .evidence import strings
from .model import (
    MAX_FIELD,
    MAX_QUOTE,
    MAX_SUMMARY,
    Citation,
    Finding,
    Finish,
    ListRecords,
    Observation,
    ReadRecord,
    RecordRead,
    Report,
)

#: Names shorter than this are too common to count as a mention of one entity.
MIN_MENTION = 3
_NON_WORD = re.compile(r"[\W_]+")


def name_key(name: str | None) -> str:
    """A display name with case, spacing, punctuation and a leading "the" set aside ("" for none)."""
    if not name:
        return ""
    text = _NON_WORD.sub(" ", unicodedata.normalize("NFKC", name).casefold()).strip()
    return text.removeprefix("the ")


def _ref(text: str) -> SourceRef | None:
    try:
        return SourceRef.parse(text)
    except ValueError:
        return None


class _Seen:
    """What the auditor knows of one record it read: its references into the source and its text."""

    def __init__(self, read: RecordRead, source: str) -> None:
        self.read = read
        self.refs: list[tuple[str, str, SourceRef]] = []  # pointer, text, ref into this source
        self.text: list[tuple[str, str]] = []  # pointer, text (references to any source excluded)
        self.uncitable = 0  # fields whose JSON pointer is longer than a citation may carry: not judged
        for pointer, value in strings(read.payload, "/payload"):
            if len(pointer) > MAX_FIELD:
                self.uncitable += 1
                continue
            ref = _ref(value)
            if ref is None:
                self.text.append((pointer, value))
            elif ref.source == source:  # a reference to another source is someone else, not a mention
                self.refs.append((pointer, value, ref))
        self.links = frozenset((ref.kind, ref.entity_id) for _, _, ref in self.refs)
        name = (read.name or "").strip()
        #: how another record's text names this one: its whole display name, as words, in any case
        self.mention = (re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)
                        if len(name) >= MIN_MENTION else None)

    def links_to(self, other: _Seen) -> bool:
        return other.read.record in self.links


class ConsistencyAuditor:
    """A worker for ``run_job``. ``source`` is the job's source, the one its references are resolved in."""

    def __init__(self, source: str) -> None:
        self.source = source
        self._known: dict[tuple[str, str], tuple[str, ...]] | None = None
        self._queue: list[tuple[str, str]] = []
        self._seen: list[_Seen] = []
        self._pending: list[Finding] = []
        self._uncitable = 0

    def next_action(self, observation: Observation | None) -> object:
        if observation is None:
            return ListRecords()
        action, result = observation.action, observation.result
        if type(action) is ListRecords:
            if result.outcome != "ok":
                return Finish(f"the listing was refused: {result.outcome}")
            self._known = {(item.kind, item.entity_id): item.revisions for item in result.listed}
            self._queue = sorted((item.kind, item.entity_id) for item in result.listed if item.head is not None)
        elif type(action) is ReadRecord and result.outcome == "ok" and result.read is not None:
            self._absorb(result.read)
        if self._pending:
            return Report(self._pending.pop(0))
        if self._queue:
            kind, entity_id = self._queue.pop(0)
            return ReadRecord(kind, entity_id)
        if self._uncitable:
            return Finish(f"{self._uncitable} field(s) with a path too long to cite were not checked")
        return Finish()

    # -- the checks --------------------------------------------------------------------------------------
    def _absorb(self, read: RecordRead) -> None:
        new = _Seen(read, self.source)
        self._uncitable += new.uncitable
        self._pending += self._dangling(new)
        for old in self._seen:
            duplicate = self._duplicate(new, old)
            if duplicate is not None:
                self._pending.append(duplicate)
                continue
            for mentioning, named in ((new, old), (old, new)):
                found = self._mention(mentioning, named)
                if found is not None:
                    self._pending.append(found)
                    break
        self._seen.append(new)

    def _dangling(self, new: _Seen) -> list[Finding]:
        found = []
        known = self._known or {}
        label = _label(new.read)
        for pointer, text, ref in new.refs:
            revisions = known.get((ref.kind, ref.entity_id))
            if revisions is None:
                summary = f"{label} refers to {ref.kind}/{ref.entity_id}, which this source does not have"
            elif ref.revision is not None and ref.revision not in revisions:
                summary = f"{label} refers to revision {ref.revision} of {ref.kind}/{ref.entity_id}, which does not exist"
            else:
                continue
            found.append(_finding("dangling_reference", summary, (new.read.cite(pointer, _quote(text)),)))
        return found

    def _duplicate(self, new: _Seen, old: _Seen) -> Finding | None:
        a, b = new.read, old.read
        if a.kind != b.kind or not name_key(a.name) or name_key(a.name) != name_key(b.name):
            return None
        if new.links_to(old) or old.links_to(new):
            return None
        first, second = sorted((a, b), key=lambda read: read.entity_id)
        summary = (f'{first.kind} "{first.name}" ({first.entity_id}) and "{second.name}" ({second.entity_id}) '
                   f"may be the same {first.kind}")
        return _finding("possible_duplicate", summary,
                       (first.cite("/name", _quote(first.name)), second.cite("/name", _quote(second.name))))

    def _mention(self, mentioning: _Seen, named: _Seen) -> Finding | None:
        if named.mention is None or mentioning.links_to(named) or named.links_to(mentioning):
            return None
        for pointer, text in mentioning.text:
            match = named.mention.search(text)
            if match is None:
                continue
            summary = (f"{_label(mentioning.read)} names {_label(named.read)}, but neither record refers to "
                       "the other")
            return _finding("missing_link", summary,
                           (mentioning.read.cite(pointer, _quote(match.group(0))),
                            named.read.cite("/name", _quote(named.read.name or ""))))
        return None


def _label(read: RecordRead) -> str:
    return f'{read.kind} "{read.name}"' if read.name else f"{read.kind}/{read.entity_id}"


def _quote(text: str) -> str:
    """A quote within the citation limit: the text itself, or its start (still a verbatim substring)."""
    return text[:MAX_QUOTE]


def _finding(category: str, summary: str, citations: tuple[Citation, ...]) -> Finding:
    if len(summary) > MAX_SUMMARY:  # long display names: the citations carry them whole
        summary = summary[:MAX_SUMMARY - 1] + "\u2026"
    return Finding(category, summary, citations)


__all__ = ["MIN_MENTION", "ConsistencyAuditor", "name_key"]
