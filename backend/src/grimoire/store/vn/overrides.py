"""Optimistic presentation records live and die with their scene incarnation.

Reset is a new revision, not deletion: a delayed save cannot resurrect a reset.
No image placements are stored here; values only select existing scoped assets.
"""

import json
import re
import uuid

from .. import atomic, filesystem_links, locks, paths
from ..scene_history import records

MAX_BYTES = 4 * 1024 * 1024


class ConflictError(ValueError):
    """The scene or presentation changed since it was read."""


class UnavailableError(ValueError):
    """Unknown data must never be overwritten as an empty presentation."""


class InvalidError(ValueError):
    """The requested presentation is not valid for this historical post."""


def _path(cid, identity):
    path = records.directory(cid, identity) / "presentation.json"
    if filesystem_links.linked(paths.home(), path):
        raise UnavailableError("Linked presentation data is unavailable")
    return path


def _load(cid, identity):
    path = _path(cid, identity)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise UnavailableError("Presentation data exceeds its size limit")
        value = json.loads(atomic.read_text(path))
        if value.get("format") != 1 or not isinstance(value.get("posts"), dict):
            raise ValueError("Unsupported presentation data")
        for post, row in value["posts"].items():
            if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", post)
                    or not re.fullmatch(r"[0-9a-f]{32}", row["revision"])
                    or not isinstance(row["actors"], dict)
                    or not (row["background"] is None or isinstance(row["background"], str))):
                raise ValueError("Invalid presentation data")
            for actor in row["actors"].values():
                if (not isinstance(actor, dict) or set(actor) - {"outfit", "expression", "hidden"}
                        or any(not isinstance(v, str) for k, v in actor.items() if k != "hidden")
                        or ("hidden" in actor and type(actor["hidden"]) is not bool)):
                    raise ValueError("Invalid actor presentation")
        return value
    except FileNotFoundError:
        return {"format": 1, "posts": {}}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise UnavailableError("Presentation data cannot be read; restore it before saving") from exc


def read(cid, identity, post):
    return _load(cid, identity)["posts"].get(post, {"revision": "none", "actors": {}, "background": None})


def write(cid, identity, post, expected, actors, background):
    with locks.campaign_lock(cid):
        value = _load(cid, identity)
        if value["posts"].get(post, {}).get("revision", "none") != expected:
            raise ConflictError("Presentation changed; reload before saving")
        value["posts"][post] = {"revision": uuid.uuid4().hex, "actors": actors, "background": background}
        payload = json.dumps(value, ensure_ascii=True, sort_keys=True)
        if len(payload.encode()) > MAX_BYTES:
            raise InvalidError("Presentation data exceeds its size limit")
        path = _path(cid, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic.write_text(path, payload)
