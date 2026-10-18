# Frozen current-format campaign

This is a second synthetic store vintage. Its production writers came from
[6e68ef2837d83e6dd527e22b43cb396e350627f6](https://github.com/charlesmsiegel/grimoire/commit/6e68ef2837d83e6dd527e22b43cb396e350627f6);
the input was frozen on **2026-10-18
UTC (2026-10-17 America/Los_Angeles)**. It is current on that date and gains
historical value as later writers change. It does not pretend to be a second
old-format store today.

`home/` starts with a byte-for-byte copy of the shipped public
`../frozen_campaign/home/`. The original fixture, its snapshot and its tests were
left unchanged. The one-time builder then used the store APIs to:

- migrate the dated scene filename and backfill stable scene identities;
- persist format-2 global/campaign inference settings and retire the legacy
  connection model fields into `inference-retired.json`;
- ingest the existing `../images/png_onepixel.png` bytes through `assets.put_image`
  as Seraphine's default avatar, producing an image-store blob, object and placement;
- persist matching whole-scene knowledge statements: everyone knows the first
  scene, with a more specific `unaware` override for Mara.

There were no real-store reads, bootstrap, API startup, provider calls or generated
images. Both provider keys are empty. Clock values and opaque UUID/revision entropy
were fixed only inside the builder's scoped patches. Store writers still produced
every authoritative record on Windows. Git attributes preserve these exact input
bytes, including the Windows writer's CRLF records. Cache sync ran with embeddings
disabled and verified the record scope. The new input excludes `.cache/`, safety `backups/` and `*.lock`
runtime files; the actual rename usage event and campaign revision are preserved.
Two separate scratch builds produced identical bytes and directory entries before
one was copied here.

## Three independent contracts

`home/` is **never regenerated or written by a test**. Tests copy it through
`tests.frozen_copy.copy_home(source=...)`; the builder is not test setup. A future
format calls for another frozen vintage.

`digest.json` pins every input file's SHA-256 and every directory entry. It is input
provenance, not expected reader output. Replacing input and updating its digest is
a change to the historical evidence and defeats this fixture's purpose.

`snapshot.json` records 93 reader sections: the original broad read-only sweep plus
stored inference/retirement, image object/placement and knowledge readers. It can
be refreshed deliberately when reviewed reader behavior changes, using only a
disposable copy (run from `backend/`, with the worktree interpreter described in
`CONTRIBUTING.md`):

```sh
PYTHONPATH=src /path/to/backend/.venv/bin/python \
    -m tests.fixtures.frozen_campaign_current.sweep
```

Unrelated config defaults are excluded from the inference projection: an absent
tracker setting, for example, uses a runtime default the test suite overrides.
As in the original sweep, token counts and generated data stay out of snapshots.

`test_frozen_current.py` also asserts real inference choices and stripped fields,
retired values, pinned image bytes and pixel identity, valid grant fingerprints,
audience precedence, stale-grant refusal, byte-for-byte read-only behavior and
already-current migration idempotence. Deliberately corrupted **copies** prove
these semantic assertions fail without consulting the snapshot. Legacy-specific
rename/migration assertions remain in the original fixture's tests.

## One-time builder provenance

`build.py` describes how this input was minted. It requires a nonexistent explicit
output outside either checked-in frozen fixture, and refuses an existing output
before any copy or write:

```sh
PYTHONPATH=src /path/to/backend/.venv/bin/python \
    -m tests.fixtures.frozen_campaign_current.build --out /tmp/new-vintage/home
```

That command is for investigating provenance or minting a **new sibling**. It is
not a command to refresh this `home/`. Determinism at the documented writer revision
does not promise identical output from future writers; frozen bytes, independent
semantics and the digest are what those writers must continue to read.
