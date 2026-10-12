# Spec 07-S8 membership index: spec 03 cost gate — 2026-10-12

Spec 03 section 8 wires a compiled-cache kind only when "compute plus read
costs much more than a stat, one indexed lookup and a decode", shown on a
synthetic library. Spec 07-S8 (the persistent tier of the inverse index) was
measured against that gate once 03 landed (#506). **It does not pass, so the
kind is not wired**, the same decision 03 recorded for search extraction and
the world/campaign rows. Every consumer keeps reading S2's in-process memo,
which already answers warm reads inside a process.

## Method

`backend/scripts/benchmark_membership.py` builds throwaway Worlds in a
temporary store (never a user's library), each with N groups of eight member
refs and a Leader, plus one campaign, and takes medians over repeated calls:

- **fresh** is S2's cold path. It runs the strict capture, which reads and
  hashes every group file and both identity ledgers, then parses and inverts.
- **capture** is the capture alone. Section 7.4 keeps it mandatory on every
  persistent hit.
- **spec hit** is the 7.4 artifact as written. It is keyed on 03 collection
  digests over the World's and the campaign's `groups/*.md` (safe_id stems,
  member filter) plus both ledgers' content hashes. It is read through
  `Batch.derive` after the capture, and collection bytes are re-read freshly,
  as 7.4 requires.
- **lean hit** is the cheapest key 7.4 allows. It reuses the capture's own
  fresh-byte digests, then makes one `lookup` and decodes the result back into
  the memo value.

A probe kind is patched into `compiled.KINDS` for the run only.

## Result (synthetic; one Linux container, figures illustrative only)

| groups | fresh | capture | spec hit | lean hit |
|---:|---:|---:|---:|---:|
| 5 | 1.4 ms | 0.5 ms | 6.9 ms (hit) | 3.3 ms |
| 20 | 4.2 ms | 1.1 ms | 10.1 ms (hit) | 5.4 ms |
| 60 | 12.8 ms | 2.7 ms | 19.7 ms (hit) | 11.5 ms |
| 200 | 29.5 ms | 9.1 ms | 69.4 ms (recomputed every call) | 30.6 ms |
| 1000 | 193.8 ms | 53.1 ms | 399.8 ms (recomputed every call) | not stored |

- **The spec hit loses at every size.** Even when it hits, it pays the
  mandatory capture, a second listing and a fresh read and hash of every
  member, and a materialized-history query. All of that is already more than
  the parse it saves.
- **The spec hit stops being stored above ~125 groups.** Past roughly that
  size a derivation's family (one artifact plus one materialized row per
  member) exceeds 03's `MAX_STAGED_ROWS` (128), so it is never admitted.
- **The lean hit is at best marginal.** It is roughly level with fresh at 60
  and 200 groups, slower below that, and at 1000 groups its payload passes 03's
  1 MiB `MAX_PAYLOAD_BYTES`. What dominates its cost is rebuilding the plain
  Pydantic models from JSON, which spec 07's v1/v2 rule keeps on full
  validation.

No configuration consistent with spec 07 section 7.4 makes a persisted read
cheaper than the derive it would replace. A persistent tier would also write
`<home>/.cache/compiled` on read paths such as turn composition and the Story
Graph. Spec 03 tolerates that only for kinds that win their measurement.

## Decision

07-S8 is closed as **measured and withheld**. Nothing is registered in
`compiled.KINDS`. If 03 later gains a cheaper hit path (a stamp-vouched
collection that 7.4 could accept, or a smaller decode), rerun the harness;
the kind is wired only if it then passes the gate.
