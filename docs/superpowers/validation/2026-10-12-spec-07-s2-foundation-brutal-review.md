# S2 membership foundation independent brutal review

Reviewed commit: `79f82a7c0afa830a78b8e23223648ce2cbe11f8b` against approved S1 source `62270523a`. All source inspection and runtime checks used the frozen git archive at `C:/Users/charl/AppData/Local/Temp/grimoire-review-s2-79f82a7c0`. No production/test edits or commits were made. Synthetic Temp stores only.

**Verdict: non-blocking for the scoped production foundation, with one should-fix compatibility-test defect below.** No concrete production correctness blocker survived the attacks. This verdict does not declare the frozen compatibility gate green or certify S2 API/UI completion.

## Ranked findings

### Should fix: directory-denial test injects no failure on Python 3.11

Where: `backend/tests/test_membership_cache.py:462-476`, specifically the `os.scandir` patch at line 475.

Reproduction: execute `test_unreadable_group_directory_never_returns_partial_index` on frozen source under the existing Python 3.11.14/Pydantic 1 environment. Both World and campaign cases fail with `DID NOT RAISE PermissionError`; independent result: **2 failed, 41 deselected**. Python 3.11 `Path.iterdir` uses `os.listdir`, so patching only `os.scandir` leaves the collection readable. The test therefore fails a supported runtime without exercising the intended error path.

Consequence: the frozen checkpoint cannot claim a clean supported-runtime gate; the intended read-denial regression is not injected there. This is not a stale-index production defect. Independent scratch injection covering both enumeration primitives makes the actual warm World read raise `PermissionError` on both Python 3.11 and 3.14.

Specific fix: inject the targeted directory failure through both `os.listdir` and `os.scandir`, delegating all other paths, then rerun the focused cases on both runtimes. The author reported a successor correction in progress; that uncommitted correction was not included in this review.

## Attacks and evidence

- Frozen foundation/guard run: **232 passed**, no skips, one existing Starlette warning, 128.56 seconds on Python 3.14.5. Files: `test_membership.py`, `test_membership_cache.py`, `test_overlay_view.py`, `test_entities_store.py`, `test_statcache.py`, `test_overlay_guard.py`, `test_import_guard.py`, `test_paths_guard.py`, `test_pydantic_guard.py`.
- This run exercised complete-stat-preserving Members/secrecy edits, malformed and inaccessible strict ledgers, denied collections/files, cache insertion by a competing reader, root changes, ledger/content mismatch, bounded private memo ownership and deep copies, native actor-alias changes, source-file identity and the native World-sensitive/campaign-insensitive selection case. The actual thread insertion tests passed; they check the returned wrapper's provenance and exactly one fresh fallback.
- Independent script `C:/Users/charl/AppData/Local/Temp/grimoire-s2-independent-probes.py` passed all three probes on **both Python 3.14 and Python 3.11/Pydantic 1**: mutate the source immediately after bytes are captured and prove rows/digest retain those captured bytes; deny the actual enumeration primitive after a warm read and require failure; use physical Windows case policy with two distinct owned group files over an insensitive World and preserve both owned rosters. These supplement the repository's opposite-direction native control without mocking filesystem identity.
- Configured frozen ratchets: **ruff 1146**, **mypy 161**, all at baseline. An earlier live-worktree lint invocation also passed, but is not the evidence for frozen-source validation.
- Optional `python-code-doctor/scripts/analyze_diff.py` ran successfully (exit 0) with advisory warnings, not a clean/no-findings result. Report: `C:/Users/charl/AppData/Local/Temp/grimoire-s2-diff-analysis.json`. It used frozen working-tree bytes with the existing repository git directory and explicit base; its untracked-file discovery also included S1 files absent from that index. Only scoped source warnings were assessed. Generic mutable-global, optional-return, View-private-field and docstring/complexity warnings supplied no failing production case: the private bounded pool and owner-local View capture are explicit contracts.

## Contract assessment

The distinction between a self-computed mismatch and another reader's mismatching wrapper is sound. Spec 7.3 explicitly requires returning the fresh computation uncached when preflight and compute differ, without looping. `_read_index` has already performed that computation when `value is computed`; doing another read would add a retry. When `memo_stamped` instead returns another reader's contrary wrapper, it evicts and derives exactly one fresh uncached answer. Both paths deep-copy the resulting Index. No later fingerprint is attached to earlier rows.

Group bytes, source layer/ref and strict ledger sets are captured before parsing; the same captured bytes supply content fingerprints and rows. Current availability/content preflight precedes warm hits, including reads whose stats are preserved. Native inherited-actor detachment is rechecked using retained raw refs and shared S1 identity/filter helpers. Owned rows remain authored. The inverse reads no actor bodies, performs no token encoding, takes no campaign write locks and writes no store state. All changed foundation entry-point callers were traced; API consumers do not yet exist in this frozen checkpoint.

## Not reviewed

S2 routes/UI and their eventual integration, persistent-tier S8, the independent S1 full repository gate, other slices, and successor commits were not certified. The complete Python 3.11 foundation suite was not rerun here: only the two failing fixture cases and three independent probes were run on that environment. No permission ACLs were changed; access errors were injected at the actual filesystem-call boundary. External-editor atomic snapshot isolation beyond the approved pre-read-stamp/coupled-content contract is not claimed. The optional analyzer's out-of-scope S1 findings were not re-reviewed as S2 defects.

## Author resolution

The portable OS-denial injection was committed separately at `94c2e9ad6`. Actual Python3.11.14/Pydantic1 core83passed8.50s, and the two targeted desktop denial cases pass. Production foundation source is unchanged from the reviewed79f82 checkpoint. Full S2 API/UI reviews remain required.

## Targeted capture correction recheck — 2026-10-12

Frozen commit: `c9b742273e1934cb58c63452eca6270dc44a0ece`; source archive: `C:/Users/charl/AppData/Local/Temp/grimoire-review-s2-c9b742273`. **Scoped verdict: non-blocking.** The two subsequently found capture defects are resolved in the exercised cases; this supersedes neither the historical review evidence nor the full S2 gates.

Reviewed the correction for mixing store A's owned groups/ledgers with store B's World after a pointer switch, and the correction for an accepted owned `salt-circle.MD` losing to inherited `salt-circle.md` on a native case-sensitive directory. `group_capture` now captures the logical store container before resolving the campaign directory, and reads the campaign-to-World binding from that captured physical campaign into that logical store. `world_root_from` and `world_root_at` do not reread the global store pointer. Selection now checks captured owned IDs before probing native aliases, preserving accepted uppercase-extension sources while retaining native alias handling.

Independent frozen run: **3 passed, 42 deselected**, one existing Starlette warning, 0.68 seconds: `test_data_directory_switch_during_capture_cannot_mix_stores`, `test_captured_owned_uppercase_extension_still_overrides_world`, and `test_capture_root_switch_returns_uncached`. No broad suite repeat.

Additional independent probe: `C:/Users/charl/AppData/Local/Temp/grimoire-s2-junction-probe.py`, **passed**. It creates two synthetic stores with matching campaign/World IDs but different World rosters, relocates store A's campaign outside its logical campaigns directory, and creates a real Windows directory junction at the logical campaign path. Capture roots correctly name the physical campaign and store A's World. Switching `GRIMOIRE_HOME` to B at the second capture's physical-campaign stamp still returns A's owned Mara and A's inherited Winifred; the next request correctly returns B's Seraphine. This directly tests why the logical store must be retained before resolving the campaign link. The native mechanism exercised was a junction, not a symbolic-link privilege simulation.

No remaining concrete blocker found in these corrections. Not reviewed: dirty S3 source, unrelated successor work, full S2/API/UI completion, general junction retargeting concurrency, or compatibility across every supported runtime. Lint/mypy and broad tests were deliberately not repeated for this lightweight gate; earlier results above remain attributed to their original SHA. No production/test files changed; only this artifact and isolated Temp fixtures were written.
