"""Spec 07-S8's cost gate: is a persisted membership index cheaper than a derive?

Spec 03 section 8 wires a kind only when "compute plus read costs much more
than a stat, one indexed lookup and a decode", shown on a synthetic library and
never on the user's. This builds throwaway synthetic Worlds of a few sizes in
a temporary store and times, per size:

* ``fresh``: S2's cold path -- the strict capture (every group file read and
  hashed, both identity ledgers) plus parse and inversion.
* ``capture``: the capture alone, which spec 07 section 7.4 keeps mandatory on
  every persistent hit.
* ``spec_hit``: the section 7.4 artifact as written -- 03 collection digests of
  the World's and the campaign's ``groups/*.md`` (safe_id stems) plus the two
  ledgers' content hashes, through ``Batch.derive``, after the capture.
* ``lean_hit``: the cheapest key 7.4 permits -- the capture's own fresh-byte
  digests, one ``lookup`` and a decode back into the memo value.

A probe kind is patched into ``compiled.KINDS`` for the run only, so nothing is
registered. Run from backend/:

    python -m scripts.benchmark_membership --output report.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

SIZES = (5, 20, 60, 200, 1000)
MEMBERS_PER_GROUP = 8


def _median_ms(call: Callable[[], object], repeats: int) -> float:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


@contextlib.contextmanager
def _store() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="grimoire-membership-bench-") as tmp:
        home = Path(tmp) / "home"
        with patch.dict(os.environ, {"GRIMOIRE_HOME": str(home)}):
            yield home


def _world(size: int):
    from grimoire.store import campaigns, entities, overlay, worlds
    wid = worlds.create_world(f"Realm {size}")
    wroot = worlds.world_root(wid)
    for g in range(size):
        refs = ", ".join(f"characters:actor-{(g * 7 + k) % 500}" for k in range(MEMBERS_PER_GROUP))
        entities.create_entity(wroot, "groups", f"Group {g}", "A quiet cabal. " * 20,
                               fields={"members": refs, "leader": f"characters:actor-{g % 500}"})
    cid = campaigns.create_campaign(f"Saltmarch {size}", wid)
    return cid, wroot, overlay.croot_of(cid)


def _spec_read(cid: str, wroot: Path, croot: Path, kind, computes: list) -> object:
    """The section 7.4 artifact: capture first, then 03's collections."""
    from grimoire.store import compiled, membership, overlay
    capture = overlay.group_capture(cid)
    inputs = [compiled.Input("tombstones", croot / "deleted.json"),
              compiled.Input("detached", croot / "detached.json")]
    collections = [compiled.Collection("world_groups", wroot / "groups")]
    if (croot / "groups").is_dir():
        collections.append(compiled.Collection("campaign_groups", croot / "groups"))

    def compute(view, params):
        computes.append(1)
        return {"groups": len(membership._derive(capture).index.groups)}
    with compiled.batch() as request:
        return request.derive(kind, inputs, params={"scope": "campaign"},
                              compute=compute, collections=collections)


def _lean_key(kind, capture):
    from grimoire.store import compiled
    return compiled.make_key(kind, [("ledgers", capture.ledger_digest or "none"),
                                    ("groups", capture.groups_digest)],
                             params={"scope": "campaign", "roots": [str(r) for r in capture.roots]})


def _lean_store(cid: str, kind) -> bool:
    from grimoire.routes.common import _dump
    from grimoire.store import compiled, membership, overlay
    capture = overlay.group_capture(cid)
    value = json.loads(json.dumps(_dump(membership._derive(capture)), default=str))
    with compiled.batch() as request:
        request.put(_lean_key(kind, capture), value)
    compiled.close()
    with compiled.batch() as request:
        return request.lookup(_lean_key(kind, overlay.group_capture(cid))).hit


def _lean_read(cid: str, kind) -> object:
    """The cheapest permitted key: the capture's digests, a lookup, a decode."""
    from grimoire.store import compiled, membership, overlay
    capture = overlay.group_capture(cid)
    with compiled.batch() as request:
        found = request.lookup(_lean_key(kind, capture))
    return membership._MemoValue(**found.value) if found.hit else membership._derive(capture)


def _row(size: int, repeats: int, spec_kind, lean_kind) -> dict:
    from grimoire.store import compiled, membership, overlay
    cid, wroot, croot = _world(size)
    fresh = _median_ms(lambda: membership._derive(overlay.group_capture(cid)), repeats)
    capture = _median_ms(lambda: overlay.group_capture(cid), repeats)
    computes: list[int] = []
    _spec_read(cid, wroot, croot, spec_kind, computes)
    compiled.close()
    computes.clear()
    spec = _median_ms(lambda: _spec_read(cid, wroot, croot, spec_kind, computes), repeats)
    lean_stored = _lean_store(cid, lean_kind)
    lean = _median_ms(lambda: _lean_read(cid, lean_kind), repeats) if lean_stored else None
    compiled.close()
    return {"groups": size, "available": compiled._available(),
            "fresh_ms": fresh, "capture_ms": capture,
            "spec_hit_ms": spec, "spec_hit_recomputed": len(computes),
            "lean_hit_ms": lean, "lean_stored": lean_stored}


def measure(sizes: tuple[int, ...] = SIZES, repeats: int = 15) -> dict:
    # Imported here so GRIMOIRE_HOME is set before anything resolves a root.
    from grimoire.store import compiled, entity_schema

    def filter_groups(relative: str) -> bool:
        path = Path(relative)
        return path.suffix == ".md" and path.parent.name == "groups" and entity_schema.referenceable(path.stem)

    spec_kind = compiled.Kind("benchmark:membership_index", 1, (), ("scope",), False,
                              absent_ok_roles=("tombstones", "detached"), member_filter=filter_groups)
    lean_kind = compiled.Kind("benchmark:membership_lean", 1, (), ("scope", "roots"), False)
    with _store(), patch.dict(compiled.KINDS, {spec_kind.name: spec_kind, lean_kind.name: lean_kind}):
        rows = [_row(size, repeats, spec_kind, lean_kind) for size in sizes]
    return {"members_per_group": MEMBERS_PER_GROUP, "repeats": repeats, "rows": rows,
            # The gate: a persisted read must beat the derive it replaces.
            "selected": any(row["spec_hit_recomputed"] == 0 and row["spec_hit_ms"] < row["fresh_ms"]
                            for row in rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=15)
    args = parser.parse_args()
    report = measure(repeats=args.repeats)
    text = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
