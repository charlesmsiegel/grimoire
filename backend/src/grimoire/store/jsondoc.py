"""One small JSON document per file: the read and write shapes the per-campaign
ledgers share.

Several campaign sidecars (`facts.json`, `pins.json`, `appearances.json`,
`changes.json`, ...) are a single JSON object written whole. They each kept a
verbatim copy of the same three blocks; this module is that copy, once. Each
caller keeps its own `_path`/`read`/`_write` names, because those are the
seams tests patch and the docstrings that say why a given file reads strictly
or tolerantly live with the file they describe.

Only `atomic` and the standard library are imported, so any store module can
use this without a cycle.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic


def read(path: Path) -> dict:
    """The document at `path`, or `{}` when there is no file.

    Strict: an unreadable or unparseable file raises, and valid JSON of the
    wrong shape comes back as whatever it is. Callers that mutate check the
    shape themselves (`facts._read_ledger` says why `{}` is the wrong answer).
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_object(path: Path) -> dict:
    """The document at `path`, or `{}` when it is missing, unreadable,
    unparseable or not a JSON object.

    For display-side files where one bad byte must cost the contents rather
    than the page (`changes.read`).
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def write(path: Path, data: dict) -> None:
    """Publish `data` at `path` atomically: sorted keys, two-space indent and a
    trailing newline, so a hand-edit diffs cleanly against the next write."""
    atomic.write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
