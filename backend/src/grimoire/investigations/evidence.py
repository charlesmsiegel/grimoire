"""Checking a finding's citations against what the job actually read (082).

A worker can say anything; the job keeps only findings whose every citation is something it read in this
run: the same entity, at the same revision and etag, a field that exists in that revision, and a quote that
appears verbatim in that field. So a finding can never rest on a record the worker did not read, a revision
it imagined, or words the record does not contain. Whether the *claim* follows from the evidence is the
reviewer's call; the job only guarantees that the evidence is real and exactly attributable.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from grimoire.core.json_value import JsonObject, JsonValue

from .model import Citation, Finding, RecordRead


def envelope_view(read: RecordRead) -> JsonObject:
    """What a citation's field pointer is resolved against: ``{"name": ..., "payload": ...}``."""
    return JsonObject({"name": read.name, "payload": read.payload})


def _unescape(token: str) -> str | None:
    """An RFC 6901 reference token, or ``None`` for a ``~`` not followed by ``0`` or ``1``."""
    out, index = [], 0
    while index < len(token):
        char = token[index]
        if char == "~":
            following = token[index + 1:index + 2]
            if following not in ("0", "1"):
                return None
            out.append("~" if following == "0" else "/")
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def resolve(value: JsonValue, pointer: str) -> tuple[bool, JsonValue]:
    """``(found, value)`` for an RFC 6901 pointer. Array indices are decimal without leading zeros."""
    if pointer == "":
        return True, value
    if not pointer.startswith("/"):
        return False, None
    current = value
    for raw in pointer[1:].split("/"):
        token = _unescape(raw)
        if token is None:
            return False, None
        if isinstance(current, JsonObject):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, tuple):
            if not token.isdigit() or not token.isascii() or (len(token) > 1 and token[0] == "0"):
                return False, None
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def strings(value: JsonValue, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Every string in ``value`` with its JSON pointer, in document order (keys sorted)."""
    if isinstance(value, str):
        yield pointer, value
    elif isinstance(value, JsonObject):
        for key, item in value.pairs:
            yield from strings(item, f"{pointer}/{escape(key)}")
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            yield from strings(item, f"{pointer}/{index}")


def check_citation(citation: Citation, reads: Mapping[tuple[str, str], RecordRead]) -> tuple[str, str] | None:
    """``None`` when the citation is evidence this job read, else ``(reason, detail)``.

    Reasons: ``unread_evidence`` (never read in this run), ``revision_mismatch`` (read at another revision or
    with other bytes), ``unknown_field`` (no such field in that revision), ``quote_not_text`` (the field is not
    a string) and ``quote_not_found`` (the words are not in it)."""
    read = reads.get(citation.record)
    where = f"{citation.kind}/{citation.entity_id}"
    if read is None:
        return "unread_evidence", f"{where} was not read in this job"
    if read.revision != citation.revision or read.etag != citation.etag:
        return "revision_mismatch", f"{where} was read at {read.revision}, not as cited"
    if citation.field is None:
        return None
    found, value = resolve(envelope_view(read), citation.field)
    if not found:
        return "unknown_field", f"{where} has no field {citation.field}"
    if citation.quote is None:
        return None
    if not isinstance(value, str):
        return "quote_not_text", f"{where} {citation.field} is not text"
    if citation.quote not in value:
        return "quote_not_found", f"{where} {citation.field} does not contain the quoted words"
    return None


def check_finding(finding: Finding, reads: Mapping[tuple[str, str], RecordRead]) -> tuple[str, str] | None:
    """``None`` when every citation checks, else the first failure; a finding with no citation is ``uncited``."""
    if not finding.citations:
        return "uncited", "a finding must cite at least one record"
    for citation in finding.citations:
        problem = check_citation(citation, reads)
        if problem is not None:
            return problem
    return None


def detach(finding: Finding) -> Finding:
    """A fresh, re-validated copy of a worker's finding, sharing nothing it can still change.

    The values are frozen dataclasses, but in-process code can still re-run one's ``__init__``. So the job
    checks and keeps this copy, built from exact ``str`` fields (which nothing can change) through the
    constructors' own validation, and never the object the worker holds. Raises when the finding is not
    well formed."""
    if type(finding) is not Finding:
        raise TypeError("not a Finding")
    citations = finding.citations
    if type(citations) is not tuple or not all(type(item) is Citation for item in citations):
        raise TypeError("citations must be a tuple of Citation values")
    return Finding(finding.category, finding.summary,
                   tuple(Citation(item.kind, item.entity_id, item.revision, item.etag, item.field, item.quote)
                         for item in citations))


def evidence_key(finding: Finding) -> tuple[str, frozenset[tuple[str, str, str | None]]]:
    """Two findings of one category citing the same fields of the same records are the same finding."""
    return finding.category, frozenset((*citation.record, citation.field) for citation in finding.citations)


__all__ = ["check_citation", "check_finding", "detach", "envelope_view", "escape", "evidence_key", "resolve", "strings"]
