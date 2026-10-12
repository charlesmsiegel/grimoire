# Spec 03 synthetic cost experiment

Only public placeholder libraries created by `scripts.synth_library` were measured. The page corpus contains 2 worlds, 2 campaigns per world, 3 scenes per campaign, 6 record units per world, and 20,000 placeholder words per body. The larger token corpus contains one world/campaign/scene/record and 525,000 words, yielding 8,400,025 supplied scene-body characters. No private library or phone was used.

[Raw samples, call/read counts, worker PIDs, medians and dependency versions](2026-10-11-spec-03-compiled-cache.json) retain the four page cases (cold, same-process warm, fresh-process restart, one-file edited), kind probes and the separate large-token experiment. Every cold repetition uses a disposable copy without `.cache`; restart repetitions use separate Python processes. Setup reads occur outside the measured interval. Fresh worker timings include BUILD, dependency, encoder and database initialization; process wall times also include imports. The controlled public source clock advances beyond both metadata timestamps and the five-second window; elapsed timings use the real performance clock. OS page caches remain warm. Seven repeats per case were run on a shared Windows/Python 3.14 host.

## Selection under the required cost inequality

| Candidate | Live parse/count median ms | Stat + lookup + decode median ms | Production ruling |
|---|---:|---:|---|
| Markdown search extraction | 14.202 | 85.556 | retain live extraction |
| Card search extraction | 3.125 | 67.890 | retain live extraction |
| World row | 0.710 | 68.198 | retain live row |
| Campaign row | 4.714 | 66.795 | retain live row |
| Medium supplied-text count | 55.730 | 25.455 | no persistence: fresh-process initialization removes the apparent win |

The lookup column clears owned pools and closes SQLite between samples; it is **not** the fresh-process result. Fresh medium counts were 367.646 ms direct and 365.415 ms through `record_tokens`, with all seven wrapper samples actually encoding the text. They are below the production admission cutoff and do not open SQLite.

For the selected 8,400,025-character body, actual fresh-process `record_tokens` measured 1,195.560 ms versus 1,764.881 ms direct encoding. All seven cached workers performed zero `count_tokens` calls; all seven direct workers performed one. Persistent admission therefore begins at 8,388,608 supplied characters. This replaced the earlier 262,144-character cutoff, whose apparent pool-clear win failed when fresh processes paid BUILD initialization.

Same-process warm supplied-text counts at source pin `26bf8a0b4` were compared against the original public wrapper in 15 alternating pairs of 100 calls. For the same 8,400,025-character body, current median was 0.000805 ms versus 0.021271 ms, paired median ratio 0.03579, with equal counts. A two-entry exact-string digest memo retains at most 32 MiB of text plus overhead; larger strings hash live. This checkpoint exposed a missing actual-caller case: lists parse new equal strings and a three-record sweep thrashes a two-entry digest memo. The final count memo therefore selects a path/encoder slot and compares exact supplied text before hashing. It retains at most 4,096 entries and 32 MiB of body text; the separate digest cache adds at most 32 MiB. The path selects storage only and proves no file currency.

Independent actual `entities.list_entities` measurements at `23228d8dc` used three distinct records and the existing shared `_Enc` fixture, with zero encoding after seeding both pools. Fifteen alternating pairs of 10 full listings returned equal outputs. At 320,000 characters per record, medians were 3.684 ms original/3.624 ms current, paired median ratio 0.98382. At 4,200,000 characters, medians were 50.454/52.913 ms and paired median ratio 0.98961; alternating order/noise means ratios and marginal medians differ. The earlier SHA-per-body regression (paired 1.788 and 1.501) is closed by exact-body comparison, not by measuring only a reused string object.

Original spec sections 8 and 14 require a demonstrated restart win before a kind is wired. Search/card/shelf persistence failed that gate and is withheld. The private benchmark kinds prove the registry/byte-fed computation contract, not production wiring. The tentative S6 consumer list is reduced explicitly under that stronger requirement. The four page cases include the unchanged search walk, not a claim that an unselected kind gained persistent acceleration.

Independent actual `search.search('Mara')` comparisons exposed a 5–9% warm-page tax from changing the unselected search wrapper. Its original signature memo and version eviction were restored. Search consequently retains its preexisting same-size in-place rewrite with restored-mtime limitation. Source validation keeps change-time checks. The world and campaign shelf rows were not wired to SQLite, but their in-memory memo did change: they moved from the `(path, mtime, size)` signature memo to the full-stamp `statcache.memo_stamped`, which adds change time (native ChangeTime on Windows). Their same-process warm listing was compared with main's afterwards; see the validation note's PR follow-up.

## Input identity and limits

Token counts are pure measures of exact stripped supplied text and the actual encoder, with zero file inputs. Memory stores that exact body and encoder; persistence selects its SHA-256 plus encoding. The supplied path is never reopened or used to claim file currency or materialization history. A caller can deliberately supply old text beside a newer file. Heuristic, failed, unattributed and empty counts never persist; encoder recovery takes effect on the next call. Optional hashing allocation/Unicode failures use the existing count fallback.

Tests cover deterministic valid synthetic API shapes, generator refusal of every nonempty target including planted markers, unmarked benchmark refusal, unchanged original bytes/environment, CRLF, malformed/nonUTF8 search tolerance, stem/dirname fallback keys, supported source change-time validation, supplied-body mismatch, encoder recovery/failure, thread-local attribution, campaign-held SQL opt-out and frozen-copy cold/warm/off/restart equality. Frozen fixture originals were not regenerated.

Persisted file-source trust requires known local metadata and filesystem provenance: native Windows ChangeTime/creation guards on supported local NTFS/ReFS, or POSIX ctime under the platform contract. Unsupported or unavailable metadata forces byte validation. Ordinary live native memos do not claim that persisted-source provenance. Legacy migration scene-identity marks still persist best-effort stamps under their existing policy; they are not compiled SourceRows. Real POSIX and on-device performance remain unverified.

## Updated-main confirmation (2026-10-12)

At source `61a5c7c069` on main `246a0e73a8`, the same synthetic 8,400,025-character body was measured in seven actual fresh Python workers for each path. Median direct encoding was 1,104.696 ms; cached `record_tokens` was 799.168 ms, including encoder, BUILD and database initialization. The seven cached workers made zero `count_tokens` calls, while the seven direct workers each made one. This confirms the selected large-body restart benefit after the rebase. Full process-wall samples and distinct worker PIDs are in the JSON report.

The host also ran backend tests and an APK build during this confirmation; OS page cache was not cleared. The earlier page, kind and warm-list measurements remain explicitly tied to their original source checkpoints. The confirmation does not broaden persistence to the unselected consumers.

## Lean-cache re-measurement (PR follow-up, 2026-10-12)

The selection above measured a cache whose hit re-read the control files on
every call, and each fresh worker paid BUILD on its first token count. After
the control-snapshot memos and the startup BUILD prime landed, the same
synthetic shapes were measured again. These measurements ran under Python 3.11
on a shared Linux container, with OS page caches warm and medians of seven.

Token counts compared `count_tokens` with a persisted `record_tokens` hit on
an open connection, using a placeholder body:

| Supplied characters | Direct encoding (ms) | Persisted hit (ms) |
|---:|---:|---:|
| 2,000 | 0.22 | 0.73 |
| 8,000 | 0.84 | 0.67 |
| 32,000 | 3.36 | 0.79 |
| 128,000 | 13.72 | 1.29 |
| 512,000 | 53.53 | 1.13 |

The two cross between 8,000 and 32,000 characters, so admission now begins at
16,384 (`tokens.PERSIST_MIN_CHARS`), replacing 8,388,608.

Search-shaped extraction used 200 placeholder records of about 24 KB each.
Batched `validate` plus `lookup_many` took 1.1 to 1.3 ms per record, against
about 0.45 ms to extract each record directly. The payload is about twice the
input, so the loss is structural, and search stays live.

The world and campaign rows were measured on `scripts.synth_library` with 40
worlds and 5 campaigns each. A live row costs about 29 µs for a world and
90 µs for a campaign, against at least 0.7 ms for a hit, so they stay live
too.

