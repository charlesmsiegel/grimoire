"""Resumable sampling of a finite rotating-image source.

The temporary journal owns the source and discovery counters; acceptance only
publishes a source-free collection. Relinking may fail after publication, so
the caller retires the journal only after verifying every intended replacement.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from . import atomic, fetch, image_collections, locks, paths

_JOB = re.compile(r"[0-9a-f]{64}\Z")
_COUNTERS = ("requests", "valid", "added", "duplicates", "failed")


def job_path(wid: str, job_id: str) -> Path:
    image_collections.directory(wid)  # prove world existence and id safety
    if not _JOB.fullmatch(job_id):
        raise ValueError("invalid image collection job id")
    return paths.home() / ".cache" / "image-collection-imports" / wid / f"{job_id}.json"


def _save(path: Path, job: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, json.dumps(job, indent=2) + "\n")


def _read(path: Path) -> dict:
    job = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(job, dict) or job.get("format") != 1
            or not isinstance(job.get("members"), list)
            or not isinstance(job.get("source_url"), str)):
        raise image_collections.CollectionInvalidError("invalid harvest journal")
    return job


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
    # Job -> image lock, never the reverse. The journal's lock spans requests,
    # but the short world image lock is released before another download.
    with locks.image_collection_job_lock(wid, job_id):
        if target.exists():
            job = _read(target)
            if job["source_url"] != source_url or job["job_id"] != job_id:
                raise image_collections.CollectionInvalidError("harvest source has changed")
        else:
            job = {"format": 1, "job_id": job_id, "source_url": source_url,
                   "collection_id": uuid.uuid4().hex, "members": [], "accepted": False,
                   "totals": dict.fromkeys(_COUNTERS, 0), "stop": "sampling"}
        if job["accepted"]:
            image_collections.read(wid, job["collection_id"])
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
            try:
                result = download(source_url)
                if result is None:
                    raise ValueError("download failed or did not return an image")
                name = image_collections.put_member(wid, result[0])
            except Exception as exc:  # noqa: BLE001 -- a failed sample is journaled and bounded
                counts["failed"] += 1
                failures += 1
                duplicates = 0
                job["last_error"] = str(exc)
            else:
                counts["valid"] += 1
                failures = 0
                if name in seen:
                    counts["duplicates"] += 1
                    duplicates += 1
                else:
                    counts["added"] += 1
                    job["members"].append(name)
                    seen.add(name)
                    duplicates = 0
            for key in _COUNTERS:
                # Totals are persisted after every response, not at invocation
                # end: killing a long sample must not lose completed requests.
                job["totals"][key] += counts[key] - before[key]
            if failures >= failure_limit:
                job["stop"] = "failures"
            elif duplicates >= duplicate_limit:
                job["stop"] = "duplicates"
            elif counts["requests"] >= max_requests:
                job["stop"] = "limit"
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
        job = _read(target)
        image_collections.publish(wid, job["collection_id"], job["members"])
        job["accepted"] = True
        job["stop"] = "accepted"
        _save(target, job)
        return job
