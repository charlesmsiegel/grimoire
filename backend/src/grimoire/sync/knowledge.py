"""Merging two devices' 020 knowledge logs (028, docs/SYNC.md "Knowledge logs").

020 names each batch for the device that appended it (``batch/<seq>-<tag>.json``) and every batch
names all the heads it extends, so two devices that appended to one campaign offline wrote
different files and their log is a DAG that 020's reads assemble whole. Sync therefore merges two
such logs by copying, create-only, exactly as it copies one device's appends: nothing existing is
rewritten, so ``sync(a, b)`` and ``sync(b, a)`` still write the same bytes, and a crash anywhere is
a crash of an ordinary copy.

What sync must still refuse is a union 020 would not read whole. When both devices hold knowledge
files of a campaign that the other lacks (the logs diverged), the union of both devices' files must
assemble with nothing quarantined, and no proposition may have gained a ruling on both devices:
two devices deciding canon on one proposition offline is a conflicting edit, and "the later
sequence governs" would silently overrule one of them. Belief-chain forks, a contribution used on
both sides and a record listed by both devices' batches are refused by the assembly itself. A
refused merge holds the campaign's log as one unit, as a diverged log always was: each diverged
batch is an ``unapplicable`` conflict (keep-both) and the rest of its knowledge copies are skipped.

Pure over the two scans: no clock, no randomness, no write.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from grimoire.core.identity import KnowledgeRef
from grimoire.core.knowledge import Ruling
from grimoire.evidence import ScannedFile, assemble
from grimoire.store import InvalidEnvelope, etag
from grimoire.store.layout import classify
from grimoire.store.replica import CanonicalScan, parse_exact


def knowledge_ref(path: str) -> KnowledgeRef | None:
    """The knowledge record (or batch) a synchronized path holds, or ``None``."""
    found = classify(tuple(path.split("/")))
    return found[1] if found is not None and found[0] == "knowledge" else None  # type: ignore[return-value]


def _campaign_files(scan: CanonicalScan, campaign: str) -> dict[str, bytes]:
    return {path: data for path, data in scan.files.items()
            if (ref := knowledge_ref(path)) is not None and ref.campaign_id == campaign}


def _scanned(path: str, data: bytes) -> ScannedFile:
    """What 020's own read makes of these bytes: the envelope's schema and payload, or a damage code."""
    try:
        record = parse_exact(tuple(path.split("/")), data)
    except InvalidEnvelope as error:
        return ScannedFile(None, damage=getattr(error, "code", None) or "malformed")
    return ScannedFile(etag(data), record.schema, record.payload)  # type: ignore[union-attr]


def merge_refusal(campaign: str, scans: Iterable[CanonicalScan]) -> str | None:
    """Why the two devices' knowledge logs of ``campaign`` cannot become one, or ``None``.

    The union of both devices' canonical knowledge files of the campaign must assemble clean, and no
    proposition may hold a ruling from each device that the other lacks. A path both hold with
    different bytes is never merged (a record or batch id both devices used: a conflict of its own).
    The reason names records only, never their text.
    """
    sides = [_campaign_files(scan, campaign) for scan in scans]
    union: dict[str, bytes] = {}
    for files in sides:
        for path, data in files.items():
            if union.setdefault(path, data) != data:
                return f"{path} differs between the devices"
    files = {knowledge_ref(path): _scanned(path, data) for path, data in union.items()}
    assembly = assemble(campaign, files)  # type: ignore[arg-type]
    if not assembly.clean:
        found = ", ".join([*(f"{q.name} ({q.reason})" for q in assembly.quarantined[:3]), *assembly.problems[:3]])
        return f"the merged log would not read whole: {found}"
    ruled = []
    for files in sides:
        own = {knowledge_ref(path).name for path in files}  # type: ignore[union-attr]
        others = set().union(*({knowledge_ref(path).name for path in other}  # type: ignore[union-attr]
                               for other in sides if other is not files))
        ruled.append({record.proposition_id for name, record in assembly.records.items()
                      if isinstance(record, Ruling) and name in own - others})
    both = sorted(ruled[0].intersection(*ruled[1:]))
    if both:
        return ("both devices ruled offline on " + ", ".join(f"proposition:{p}" for p in both[:3])
                + "; roll one device's offline appends back first")
    return None


def diverged(copies: Mapping[str, Iterable[str]]) -> set[str]:
    """Campaigns whose knowledge copies go both ways: each device holds files of the log the other lacks."""
    by_device = [{ref.campaign_id for path in paths if (ref := knowledge_ref(path)) is not None}
                 for paths in copies.values()]
    return set.intersection(*by_device) if by_device else set()


def refused_merges(copies: Mapping[str, Iterable[str]], scans: Iterable[CanonicalScan],
                   stuck: set[str]) -> dict[str, str]:
    """Every batch path copied either way for a diverged campaign whose merge is refused, with why
    (every knowledge path copied for it, when no batch would cross: records no batch lists are damage,
    and the refusal must still hold the whole unit).

    ``copies`` lists, per receiving device, the paths it is to receive; ``stuck`` holds the campaigns
    already held back as a unit (``"*"``: every one), which need no check.
    """
    if "*" in stuck:
        return {}
    scans = list(scans)
    refused: dict[str, str] = {}
    paths = [path for listed in copies.values() for path in listed]
    for campaign in sorted(diverged(copies) - stuck):
        why = merge_refusal(campaign, scans)
        if why is None:
            continue
        log = [path for path in paths if (ref := knowledge_ref(path)) is not None and ref.campaign_id == campaign]
        batches = [path for path in log if knowledge_ref(path).family == "batch"]  # type: ignore[union-attr]
        for path in batches or log:
            refused[path] = f"the two devices' knowledge logs of campaign {campaign} cannot be merged: {why}"
    return refused


__all__ = ["diverged", "knowledge_ref", "merge_refusal", "refused_merges"]
