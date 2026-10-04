# Local image collections: review and verification

The feature is implemented on `feature/local-image-collections`. Its local service runs from the preserved worktree. Existing main-checkout edits were left in place. Private imports, source inventories, backups, and receipts remain outside this repository.

## Theory

A collection is a permanent ordered set of local image identities. Discovery belongs to a temporary job, and selection belongs to each rendered occurrence. Separating those lifetimes lets the source disappear after relinking while images continue to work in scenes, greetings, forks, and world bundles.

Existing asset storage, Pillow validation, atomic writers, process locks, image serving, export packing, and Markdown renderers are reused. The added concepts are the immutable manifest, resumable discovery journal, and occurrence-local selection. Copying just the latest remote response would lose the pool. Keeping the remote endpoint in the permanent manifest would make offline rendering depend on it again.

A collection of already-local images can use `publish` directly. A later authoring interface can call that function without using the sampler. Discovery assumes a fixed finite source: thirty consecutive valid duplicates are acceptance evidence, not a mathematical proof of exhaustion. Byte hashes intentionally distinguish different encodings of the same pixels. App-managed world writes protect members; arbitrary external filesystem edits remain outside that guarantee.

## Review rulings

A fresh local reviewer examined the complete change against its spec. Four material findings were reproduced, pinned with failing tests, and fixed:

| Finding | Resolution |
| --- | --- |
| Windows case aliases could overwrite or delete a protected member | Membership protection compares case-folded names; uppercase PUT/DELETE aliases are covered. |
| A crash between manifest publication and journal confirmation allowed resampling | Resume and acceptance reconcile the published manifest before sampling. A failed confirmation write now recovers without fetching. |
| World case aliases acquired separate collection/job locks | Lock identity uses the resolved, normalized world path, including its store root. |
| A missing world aborted a collection-bearing campaign export | Export catches `WorldNotFound` and returns the image's alt text. |

Two smaller verification gaps were addressed: the widget test now renders two independent occurrences, and the greeting editor test proves collection members cannot expose greeting-owned subject controls. Fork and bundle tests verify all members and links under a different world id. No further material finding remained in that review; no second reviewer pass was used.

Additional implementation rulings:

- Per-job locks cover journal updates; short world collection locks cover member and manifest publication. Network requests stay outside the world lock.
- Campaign cleanup follows release of the world collection lock, avoiding cross-domain lock inversion.
- Greeting extras receive the selected member URL. Subject controls remain scoped to images owned by that greeting.
- Streaming uses stable Markdown components, preserving a chosen image when a paragraph closes or its parent rerenders.
- Private relinking backs up each file and compares bytes immediately before atomic replacement. It refuses observed edits; an external editor racing between comparison and replacement exceeds existing filesystem guarantees.
- Both new store modules are deliberate public facade additions. Only those entries were added to the frozen API snapshot. The route-safety sweep was extended with the new collection parameter.
- Static exports pack a deterministic first available representative. World bundles and forks retain every member.

## Verification

Passing checks:

- Collection behavior, review regressions, and lock guards: 185 backend tests.
- Store facade, route safety, and import guards: 523 passed, two skipped.
- Pydantic 1 collection behavior, facade, and route safety: 542 passed, two skipped, in a separate Python 3.12 environment.
- Complete frontend suite: 161 files, 3,363 tests, with coverage enabled.
- Final widget and greeting ownership tests: 67 passed.
- TypeScript checks and production frontend build.
- Ruff and ESLint ratchets: no findings above their existing baselines.
- Templates: all 128 checks passed.
- Live import verification compared every served image's bytes with its content hash, checked all replacement references, and confirmed source-free manifests.

Repository-wide verification is not entirely green in this Windows environment. On unchanged main, the following six failing tests and ten fixture errors were reproduced: asset deletion during listing, concurrent atomic appends, backup newline round-trips, a wildcard image filename, a Windows-normalized directory name, an oversized review filename, and long parametrized test ids exceeding Windows' environment-variable limit. A transient configuration-file sharing failure also appeared in one broad run and passed when checked on main.

The Pydantic 1 broad run also encountered absorb-budget timing failures. Four of the five failed tests reproduced on unchanged main; the fifth passed in isolation. The collection-specific Pydantic 1 checks above pass after the facade/sweep corrections.

The mypy ratchet has the same two failing file/rule pairs on unchanged main: platform-specific missing attributes in `atomic.py` and `proclock.py`. No typing baseline was raised. One broad backend coverage run reached 92.71%, below the existing 93% floor; it overlapped the final integration-test corrections. The later full backend run recorded 9,686 passed, 30 skipped, eight failed, and ten fixture errors. Six failures and all fixture errors reproduced on unchanged main. The other two were route-sweep cases collected before the parameter correction; the corrected sweep passes.

The final supplemental coverage run used the whole-suite data and passed 549 tests, with two skipped. It reran collection behavior, the corrected facade and route sweep, and added adversarial cases for non-HTTP sources, retargeted/unreadable journals, missing accepted manifests, and journal/member disagreement. Combined backend coverage reached 93.02%, passing the unchanged 93% floor. The added sampler cases also passed on Pydantic 1 (12 sampler tests total). No baseline was loosened. The entire Windows suite remains red for the reproduced unrelated failures described above.

UI behavior was verified with component tests and the production build. Interactive browser verification was unavailable in this session.
