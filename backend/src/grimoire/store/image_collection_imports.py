"""Resumable sampling of a finite rotating-image source.

The temporary journal owns the source and discovery counters; acceptance only
publishes a source-free collection. Relinking may fail after publication, so
the caller retires the journal only after verifying every intended replacement.

A journal is written as format 2: its `members` are image ids, and acceptance
publishes a format-2 manifest. A format-1 journal (library names, left by an
older grimoire) is converted the first time it is read, under the job lock and
then the collection lock: each name becomes the id it stands for
(`image_collections.format1_member_id`), the names are kept as
`legacy_members`, and the journal is saved as format 2 at once. A name that
maps to nothing refuses the journal. The one exception is an accepted journal
whose format-1 manifest is already published: that manifest is authoritative,
so the journal is left as it is and reconciles against its names, even when a
member's file has since gone.

Two edges are known and left for stage 4's journal retirement: an unaccepted
format-1 journal whose manifest an older build published, and which has since
lost a member, does not reconcile; and an accepted format-1 journal whose
manifest is gone is converted before it is refused.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from . import atomic, fetch, image_collections, image_hash, locks

_JOB = re.compile(r"[0-9a-f]{64}\Z")
_COUNTERS = ("requests", "valid", "added", "duplicates", "failed")


def job_path(wid: str, job_id: str) -> Path:
    if not isinstance(job_id, str) or not _JOB.fullmatch(job_id):
        raise ValueError("invalid image collection job id")
    return image_collections.journal_directory(wid) / f"{job_id}.json"


def _save(path: Path, job: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(job, indent=2) + "\n")


def _invalid() -> image_collections.CollectionInvalidError:
    return image_collections.CollectionInvalidError("invalid harvest journal")


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _published_format1(wid: str, job: dict) -> bool:
    """Is this journal's collection published as a format-1 manifest? A
    manifest that does not read raises, refusing the journal."""
    try:
        return image_collections.read(wid, job["collection_id"])["format"] == 1
    except FileNotFoundError:
        return False


def _converted(wid: str, target: Path, job: dict) -> dict:
    """A format-1 journal as format 2, saved before it is returned. Two names
    for one picture (the same pixels under other metadata) become one member,
    as a format-2 harvest would have counted them."""
    ids: list[str] = []
    for name in job["members"]:
        image_id = image_collections.format1_member_id(wid, name)
        if image_id not in ids:
            ids.append(image_id)
    converted = {**job, "format": 2, "members": ids, "legacy_members": list(job["members"])}
    _save(target, converted)
    return converted


def _read(wid: str, path: Path) -> dict:
    """The journal at `path`, converting a format-1 one (module docstring).
    Called under its job lock; refuses anything that is not a journal."""
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _invalid() from exc
    if (not isinstance(job, dict) or type(job.get("format")) is not int
            or not _strings(job.get("members"))
            or not isinstance(job.get("source_url"), str)
            or not isinstance(job.get("collection_id"), str)
            or type(job.get("accepted")) is not bool):
        raise _invalid()
    if job["format"] == 2:
        if (not all(image_hash.ID_RE.fullmatch(m) for m in job["members"])
                or ("legacy_members" in job and not _strings(job["legacy_members"]))):
            raise _invalid()
        return job
    if job["format"] != 1:
        raise _invalid()
    # Job lock, then collection lock: the published check and the conversion
    # see one state of the manifests and the library.
    with locks.image_collection_lock(wid):
        if job["accepted"] and _published_format1(wid, job):
            return job
        return _converted(wid, path, job)


def _load_job(wid: str, target: Path, source_url: str, job_id: str) -> dict:
    if target.exists():
        job = _read(wid, target)
        if job["source_url"] != source_url or job.get("job_id") != job_id:
            raise image_collections.CollectionInvalidError("harvest source has changed")
        return job
    return {"format": 2, "job_id": job_id, "source_url": source_url,
            "collection_id": uuid.uuid4().hex, "members": [], "accepted": False,
            "totals": dict.fromkeys(_COUNTERS, 0), "stop": "sampling"}


def _published_members(job: dict, manifest_format: int) -> list[str] | None:
    """What the journal says a manifest of this format should list: names
    against a format-1 manifest, ids against a format-2 one."""
    if manifest_format == 1:
        return job["members"] if job["format"] == 1 else job.get("legacy_members")
    return job["members"] if job["format"] == 2 else None


def _reconcile_publication(wid: str, target: Path, job: dict) -> bool:
    """The manifest is authoritative even if the confirmation write crashed.
    It is compared in its own format, so a journal converted after an older
    grimoire published it still recognises its own publication."""
    try:
        manifest = image_collections.read(wid, job["collection_id"])
    except FileNotFoundError:
        if job["accepted"]:
            raise
        return False
    if manifest["members"] != _published_members(job, manifest["format"]):
        raise image_collections.CollectionInvalidError("journal differs from published collection")
    if not job["accepted"]:
        job["accepted"] = True
        job["stop"] = "accepted"
        _save(target, job)
    return True


def _observe(wid: str, source_url: str, download: Callable, job: dict, seen: set) -> str:
    try:
        result = download(source_url)
        if result is None:
            raise ValueError("download failed or did not return an image")
        image_id = image_collections.put_member(wid, result[0])
    except Exception as exc:  # noqa: BLE001 -- a failed sample is journaled and bounded
        job["last_error"] = str(exc)
        return "failed"
    if image_id in seen:
        return "duplicates"
    job["members"].append(image_id)
    seen.add(image_id)
    return "added"


def _stop_reason(failures: int, duplicates: int, requests: int,
                 failure_limit: int, duplicate_limit: int, max_requests: int) -> str:
    if failures >= failure_limit:
        return "failures"
    if duplicates >= duplicate_limit:
        return "duplicates"
    if requests >= max_requests:
        return "limit"
    return "sampling"


def sample(wid: str, source_url: str, *, duplicate_limit: int = 30,
           max_requests: int = 200, failure_limit: int = 3, delay: float = 0.25,
           fetch_image: Callable[[str], tuple[bytes, str] | None] | None = None) -> dict:
    """Sample one bounded invocation; a request cap is never completeness."""
    if min(duplicate_limit, max_requests, failure_limit) < 1 or delay < 0:
        raise ValueError("sampling limits must be positive and delay nonnegative")
    if not isinstance(source_url, str) or not source_url.startswith(("https://", "http://")):
        raise ValueError("source must be an HTTP image address")
    job_id = hashlib.sha256(source_url.encode()).hexdigest()
    target = job_path(wid, job_id)
    download = fetch_image or fetch.download_url
    # Job -> collection -> image lock, never the reverse. The journal's lock
    # spans requests, but the shorter locks are released before a download.
    with locks.image_collection_job_lock(wid, job_id):
        job = _load_job(wid, target, source_url, job_id)
        if _reconcile_publication(wid, target, job):
            return job
        job["last"] = dict.fromkeys(_COUNTERS, 0)
        job["stop"] = "sampling"
        _save(target, job)
        seen = set(job["members"])
        duplicates = failures = 0
        while job["last"]["requests"] < max_requests:
            counts = job["last"]
            before = dict(counts)
            counts["requests"] += 1
            outcome = _observe(wid, source_url, download, job, seen)
            counts[outcome] += 1
            if outcome != "failed":
                counts["valid"] += 1
            failures = failures + 1 if outcome == "failed" else 0
            duplicates = duplicates + 1 if outcome == "duplicates" else 0
            for key in _COUNTERS:
                # Totals are persisted after every response, not at invocation
                # end: killing a long sample must not lose completed requests.
                job["totals"][key] += counts[key] - before[key]
            job["stop"] = _stop_reason(failures, duplicates, counts["requests"],
                                       failure_limit, duplicate_limit, max_requests)
            _save(target, job)
            if job["stop"] != "sampling":
                return job
            if delay:
                time.sleep(delay)
    return job


def accept(wid: str, job_id: str) -> dict:
    """Explicitly accept a sampled pool; preserve its source for relink recovery."""
    target = job_path(wid, job_id)
    with locks.image_collection_job_lock(wid, job_id):
        job = _read(wid, target)
        if _reconcile_publication(wid, target, job):
            return job
        image_collections.publish(wid, job["collection_id"], job["members"])
        job["accepted"] = True
        job["stop"] = "accepted"
        _save(target, job)
        return job
