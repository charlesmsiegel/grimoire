# 06-S1 Store-editing skill and drift guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one maintained outside-app store-editing procedure, discoverable through a thin Codex adapter, and detect broken executable references.

**Architecture:** The procedure stays in `.claude/skills/`; `.agents/skills/` duplicates only its discovery metadata and links back. One test module checks repository artifacts and exercises its own diagnostics on isolated broken fixtures, reusing the existing docs guard and the real CLI parser instead of introducing runtime dependencies.

**Tech Stack:** Markdown, Python 3.11+, pytest, standard-library AST/import/signature/argument parsing, existing string-scalar frontmatter parser.

**Spec:** [06](../specs/2026-10-09-roadmap-06-store-editing-skill-design.md), sections 3, 4, 6, 8 and 9; delivers 06-C1 partially.

## Global Constraints

- Canonical: `.claude/skills/grimoire-store-editing/SKILL.md`; adapter: `.agents/skills/grimoire-store-editing/SKILL.md`; no symlinks or generated procedure copies.
- Copy the spec's section 4.1 `name` and `description` exactly; keep the canonical near its 170-line outline and the adapter body at most 12 lines.
- Privacy examples use Seraphine and Realm; no actual library access or contents in repository artifacts.
- Existing skill conventions stay in place; existing six skills are not retrofitted to the new duplication rule.
- Both interpreter forms stay within four lines; every checkout command sets `PYTHONPATH`. Worktree interpreter selection routes to CONTRIBUTING.md.
- Campaign edits require quiescence throughout the batch, even when a store writer is used. A closed browser does not stop detached runs.
- No-spend examples use `collecting(embed=False, deleted=...)`; a large batch previews embeddings after local edits and before enabling them.
- Preserve record IDs on display renames and preserve all cache deletion controls. This skill provides no cache repair operation.
- Add no product dependencies, runtime API or parser behavior. Use the current exported report vocabulary, including `EMBEDDING_FIELDS` and `STOP_REASONS`.

## Review Focus

- A CLI invocation has a valid module and flags but an invalid verb: reject it without invoking the command.
- A deleted adapter, a mis-cased link or a nonexistent cross-document anchor: fail with the file and offending reference.
- A function keyword appears in a Python fence or inline snippet: reject an unknown keyword against the real signature.
- A status table lists successful paths while a batch is stopped or its token writes failed: the procedure reports partial work and does not claim completion.
- A collector unwinds after an edit under no-spend intent: its example and application scenario keep embedding disabled on normal and exceptional exit.

## Task 1: Repository drift guard and negative fixtures

**Files:** Create `backend/tests/test_skills_guard.py`; modify `backend/tests/test_install_scripts.py` and the guard table in `CONTRIBUTING.md`.

**Interfaces:** Consume `test_docs_guard._refs`, `_anchors`, `_spelled_exactly`, `_longest_shared_run`, `MAX_SHARED_RUN`, `PROSE`; `frontmatter.parse_frontmatter(text)`; `cache.build_parser()`; `main.create_app().routes`; `cache_sync`'s exported tuples.

Produce local diagnostic helpers with these responsibilities:

- `_pair_problems(canonical_dir: Path, adapter_dirs: tuple[Path, ...], routing_skills: tuple[str, ...]) -> list[str]`: pair both ways, metadata identity, directory identity, thin body, canonical pointer and no copied resources.
- `_link_problems(path: Path, text: str) -> list[str]`: relative references, exact case and anchors, reusing the docs helpers.
- `_code_problems(text: str) -> list[str]`: command, option, module, store symbol, signature and route diagnostics; no product operations are executed.
- `_vocabulary_problems(text: str) -> list[str]`: typed result-table rows map to real report tuples, and code-formatted names in the result section prose are validated against those vocabularies; every path status must be documented.
- `_routing_problems(text: str, others: tuple[tuple[str, str], ...]) -> list[str]`: fenced-code-excluding 16-word duplication ceiling, description included.

- [x] Write repository assertions for the canonical and mandatory adapter and run them before either exists. Record their expected missing-artifact failure.
- [x] Add fixtures before implementing each diagnostic check. For each fixture, assert the named failure, using literal expectations rather than the helper to build its expected result.
- [x] Implement pairing and frontmatter checks with `ADAPTER_DIRS = (ROOT / ".agents" / "skills",)`, `CANONICAL_DIR = ROOT / ".claude" / "skills"`, `ROUTING_SKILLS = ("grimoire-store-editing",)`, `ADAPTER_MAX_BODY_LINES = 12`.
- [x] Implement relative-link and anchor checks. Isolated fixture roots patch the docs helper's `ROOT` so case checking remains meaningful on Windows.
- [x] Join fenced shell continuation lines; validate complete `grimoire.cache` invocations by its real parser, catching `SystemExit` as a diagnostic. `grimoire.where` is an importable, argument-free command; reject stray options on it. Check prose sync flags separately. Git's `-C`, `-z`, `--porcelain=v1` and `--untracked-files=all` are explicit exceptions belonging to Git, not cache options.
- [x] Resolve backticked store dotted symbols and literal calls in Python fences/inline code through import and `getattr`. Use AST import bindings for aliases and `inspect.signature(...).bind_partial` for literal keyword validation; report missing symbols and parameters. Do not execute snippets. State that dynamic arguments and arbitrary shell syntax are outside this guard.
- [x] Check `/api/...` references against a newly built app's route paths inside the existing isolated pytest harness.
- [x] Read report names from typed table rows (`Path status`, `Refusal reason`, `Kind outcome`, `Batch field`, `Embedding field`, `Stop reason`) and all inline code in the result section prose. Reject unknown names in each typed row, unknown prose report names such as `tokens_falied`, and missing members of `PATH_STATUSES`. Valid CLI flags are checked by the command diagnostic; cache states and qualified example forms are explicit bounded exceptions rather than reasons to ignore prose.
- [x] Enforce routing against the existing `PROSE` and other canonical skill bodies; frontmatter description participates, fences do not. Adapter duplication is checked against the canonical body separately.
- [x] Plant every section 9 case: single-character description drift, missing adapter, 13 body lines, extra resource, broken/mis-cased link, missing anchor, bad prose flag, bad continued flag, nonexistent command, option on wrong command, missing module/function/keyword, unknown or omitted path status, unknown typed refusal/kind/batch/embedding/stop word, a prose-only `tokens_falied` typo with all valid table rows retained, absent API path and a 17-word copied run. Include a valid fixture to prove the checks accept supported forms.
- [x] Add the canonical to installer `DOCS` with a matching test ID; add a malformed Unix-only command fixture to the existing interpreter check. Register the guard in CONTRIBUTING.md. Explain the guard's syntactic reach and behavioral limits in its docstring.

## Task 2: Canonical reference procedure and adapter

**Files:** Create the canonical and adapter `SKILL.md` files; update the execution record and this plan's checkboxes.

**Interfaces:** Consume Task 1's diagnostics and the current cache API; produce a maintained reference with typed result rows and complete parseable examples.

- [x] Write the canonical after observing the missing-artifact failure. Follow the corrected ten-item outline: privacy links; resolver commands; preferred writers and image metadata doors; quiescence; manual-edit boundaries; safe lifecycle changes; one sync per batch; no-spend and preview timing; report interpretation; readback/verify; phone access and first-paint behavior; report the verified result.
- [x] Give one Python example using the actual `paths.home()`, `voice_anchors.write`, `entities.delete_entity` and `cache_sync.collecting(embed=False, deleted=[...])` signatures. Print `collected.report.as_dict()` only after block exit. Use the confirmed Realm/Seraphine paths and a named lore deletion.
- [x] Include complete macOS/Linux, Git Bash and PowerShell `where` and `cache sync` examples, plus prose flags for scopes, dry run, no embedding, verification and quiet counts. Keep interpreter variants nearby and route to the worktree rule.
- [x] Document all nine path statuses plus current refusal/kind and partial-batch names in typed rows; explain that root/path names are private although the report contains no record prose.
- [x] Write the adapter with byte-identical name/description and the relative canonical link `../../../.claude/skills/grimoire-store-editing/SKILL.md`. Its folder contains only that file.
- [x] Run the focused suite and all negative fixtures. Run skill-creator's `quick_validate.py` on both skill folders. Reword any prohibited prose duplication rather than widening the limit.
- [x] Record the theory: runtime interfaces own executable truth, documents own guidance, and the guard checks the boundary. It does not prove agent decisions, arbitrary dynamic code or discovery behavior; independent application scenarios cover those operational gaps.
- [x] Run an independent fresh-context application scenario with the new skill, including no-spend normal/exception exit, active detached play, referenced display rename, preserved deletion controls and partial reports. Reuse existing runtime regressions; do not add text-matching safety assertions.
- [x] Complete the required Codex diff review and spec-to-diff gate for S1, resolve findings, record evidence, then commit this slice. Leave its Landed box unticked until it actually reaches main.

## Verification

From this worktree, set `PYTHONPATH=backend/src` and use the primary checkout's interpreter as CONTRIBUTING.md requires. On Windows: `$env:PYTHONPATH='backend/src'; & 'C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe' -m pytest backend/tests/test_skills_guard.py backend/tests/test_install_scripts.py backend/tests/test_docs_guard.py -q`. On macOS/Linux the shared interpreter is the checkout's `backend/.venv/bin/python`.

After both slices: `make check PY=C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe`. Do not treat a focused run as the gate; ratchet reductions, if any, require `make baseline` and the smaller baseline file.
