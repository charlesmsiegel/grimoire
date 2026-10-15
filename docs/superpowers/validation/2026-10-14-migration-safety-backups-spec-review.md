# Issue 496: reproduction and spec review

Runtime baseline: `92a55a50d` on `codex/issue-496-migration-safety-backups`.
Proposed spec: `docs/superpowers/specs/2026-10-14-migration-safety-backups-design.md`.
Spec SHA-256: `2ed94dc5916985e93f327529d483af19796adf7d84cb69d697eb8e53448973ee`.

## Reproduction

A temporary script set `GRIMOIRE_HOME` to disposable fixture directories
before importing the store, disabled automatic inference migration, and ran
this worktree's `migrate.ensure()` with observation at `backups._store_file`.
It confirmed:

- An image and an unrelated scene transcript were copied into the safety ZIP.
- Status during copying reported `running` with no progress field.
- An injected archive I/O failure left migration failed; the next run
  succeeded and copied the first member again.

No real library was inspected or modified. This is the baseline reproduction;
product regression tests and the post-fix reproduction await implementation.

## Existing focused tests

With this worktree's `backend/src` first on `PYTHONPATH`, the main checkout's
venv interpreter ran:

```text
python -m pytest backend/tests/test_backups_store.py backend/tests/test_inference_migrate.py backend/tests/test_inference_retire.py -q
240 passed, 1 skipped, 1 warning in 176.09s
```

The warning was FastAPI/Starlette's existing TestClient deprecation. This is
a focused baseline, not the full `make check` gate.

## Codex spec gate

The repository's `/codex:adversarial-review` runner reviewed the working-tree
spec in thread `01a13da6-4847-7c10-8a12-3968199e5cbf`. Verdict: needs-attention.
It identified two contract gaps:

1. Backup-directory pruning could omit protected records when output was a
   settings or campaign directory.
2. Polling based only on progress could stop before retirement's archive
   started, on an earlier format-2/done response during planning.

The spec now requires overlap to fail closed and a separate activity signal
covering the whole live run, including planning and inter-phase gaps. It
also explicitly fails closed on nested selected directory symlinks.

The first reviewer could not launch local readers because the Windows
sandbox helper was unavailable; it inspected unchanged GitHub main sources
at `92a55a5`, matching this worktree's runtime baseline. Its plugin thread
could not be resumed through the CLI because no rollout was available.

A fresh read-only `codex exec` follow-up received the complete revised spec,
the actual local backups, migration, settings-poller and presentation sources,
and the existing process-lock implementation as direct input. It was asked
to challenge the full contract and verify both corrections, without tools or
edits. Its final assessment was:

> **Approve.** No concrete material blockers found in the revised contract against the supplied source.

The final assessment was saved successfully. The enclosing PowerShell command
reported exit 1; the captured log contains the completed assessment and a
warning about an ignored existing Codex configuration option, with no review
failure reported. The gate evidence here is the reviewer's explicit final
assessment, not a claim that the CLI command exited successfully.

## Remaining handoffs

The spec remains proposed pending the human design review required by the
brainstorming workflow. No product code has changed. Implementation planning,
the plan gate, failing regressions, the fix, `make check`, implementation and
final spec-conformance reviews, and the PR remain outstanding.
