# 06. Store-editing skill

**Status:** Draft — spec gate (`/codex:adversarial-review`) pending.
**Date:** 2026-10-09
**Roadmap:** 06 in `ROADMAP-CHECKLIST.md`. Lane: cache (03 -> 04 -> 05 -> 06).
**Baseline:** `main` at `35c1fb7`.
**Reconciles:** the bundle draft `06-store-editing-cache-skill.md`
(2026-10-06), against the repository's existing skills and its documentation
guards, and against 05 (`2026-10-09-roadmap-05-direct-edit-cache-sync-design.md`).

> Implementation rule: re-read the code and every PR landed on this subsystem
> since the baseline before planning. In particular, read 05's landed CLI
> (`grimoire.cache.build_parser()`) before writing the skill: the skill names
> its flags, and the drift test holds it to them.

## Depends on

| Contract | Provided by | What this spec uses it for | Hard or soft |
|---|---|---|---|
| 05-C2 | 05 | The `python -m grimoire.cache sync` command and its flags, exit status and report, which the skill's procedure ends with. The drift test checks every flag the skill names against the real parser. | Hard |
| 05-C1 / 05-C4 | 05 | `cache_sync.collecting()`, the in-process form for an agent that writes through `grimoire.store` from Python. | Soft (the skill can fall back to the CLI alone) |
| 05-C2 (API) | 05 | `POST /api/cache/sync`, for a store the agent cannot run Python against (a phone). A tab that observes the API run end also forgets its remembered overview reads (05 section 3.2, 04-C2b). | Soft |

## Required by

| Contract (provided here) | Consumer | What the consumer uses it for |
|---|---|---|
| 06-C1 | none | Agents doing maintenance on a store. No later spec depends on it. |

## 1. Current state (reconciled against main)

### 1.1 Skills today

- Six skills live in `.claude/skills/<name>/SKILL.md`: `create-mechanics-module`,
  `create-world`, `ingest-campaign-log`, `populate-world-content`, `verify`
  and `world-card-integration`. Two carry `scripts/`, and one a `references/`
  file. Each `SKILL.md` opens with YAML frontmatter holding only `name` and
  `description`, and the description is the trigger text.
- There is **no** top-level `skills/` directory and **no** `.agents/`
  directory.
- `AGENTS.md` is a router that "routes; it does not restate". Its "Skills"
  section tells every agent, whatever its vendor, that `.claude/skills/` holds
  the task procedures and to use one when it covers the task. So a non-Claude
  agent that reads `AGENTS.md` already reaches the skills by routing. What it
  does not get is automatic discovery by trigger text.
- Nothing tests a skill's content. The one reference from a test is a comment:
  `backend/tests/llm_fakes.py` notes that the `verify` skill points at the
  cassette fixtures.

### 1.2 What the existing skills already say about writing the store

They write through `grimoire.store`, and say so firmly.
`world-card-integration` has a section titled "Work through the store, not the
filesystem", which lists the entity, greeting, tag, voice-anchor and card
functions and explains why hand-written Markdown goes wrong: slug collisions,
frontmatter quoting, `{{char}}` baking and newline translation. `create-world`
writes a JSON plan and applies it through `backend/scripts/create_world.py`.
`ingest-campaign-log` drives `backend/scripts/ingest_scene.py`.

So the draft's premise, "agents directly edit authoritative Markdown/JSON",
describes only part of what happens. Usually an agent is a Python process
calling store functions with no server involved. Sometimes it is an editor
writing bytes. The skill has to cover both. It should steer toward the first,
since that is what the other skills already teach and what the store's
guarantees are written for.

### 1.3 The guards a skill must live with

- `backend/tests/test_docs_guard.py` holds `README.md`, `CONTRIBUTING.md`,
  `AGENTS.md`, `docs/store-guarantees.md` and the screenshots README to the
  code. It checks link targets, enumerations, orphans, and **verbatim
  duplication**: no two of those documents plus `CLAUDE.md` (`PROSE`,
  `test_docs_guard.py:71`) may share a run of more than `MAX_SHARED_RUN = 16`
  words (`:90`; `test_no_document_restates_another`, `:318`). It also requires
  that `AGENTS.md` point at `CLAUDE.md` (`test_agents_points_at_claude_md`,
  `:346`). Skills are not in `PROSE`, so nothing stops a skill from restating
  `CLAUDE.md` today.
- `backend/tests/test_install_scripts.py` requires every document it lists
  (`DOCS`, `:359`) to give a venv interpreter in both forms,
  `.venv/bin/python` and `.venv/Scripts/python.exe`, within four lines of each
  other. The rule reaches exactly the files it names, and no skill is in its
  list. The existing skills are inconsistent on this: some give both forms,
  some only the Windows one.
- `CONTRIBUTING.md`'s guard table must name every `test_*guard*.py`
  (`test_contributing_names_every_guard_test`).

### 1.4 What the draft proposed, and what changes

| Draft | Here | Why |
|---|---|---|
| Canonical file in a new top-level `skills/grimoire-store-editing/`, with adapters in both `.claude/skills/` and `.agents/skills/`. | Canonical file in `.claude/skills/grimoire-store-editing/`; one thin adapter in `.agents/skills/`. | Every other skill's canonical file is in `.claude/skills/`, and `AGENTS.md` already routes every agent there. One new top-level directory used by one skill out of seven is a second convention. Moving all seven to a neutral directory is a separate decision (Open question 1). |
| "Edit those files only", then sync. | Write through `grimoire.store` where a function exists, and edit bytes only where none does. Then sync. | Section 1.2. The store's functions take the cross-process campaign lock and keep its formats. |
| Optional `cache status`, `doctor`, `gc` and `benchmark`. | Not mentioned. | None of them exists, and naming commands that do not exist is the drift the test below exists to prevent. |
| "Sync refreshes previously materialized expensive artifacts." | The same, through 05-C3, plus `--dry-run` before a large batch. | 05 section 6.7: re-embedding needs no confirmation, so the dry run is how an agent sees the spend first. |

## 2. Goal (and what is explicitly not the goal)

**Goal.** One short procedure that an agent follows whenever it changes
records in a Grimoire store outside the running app. It should leave the
records correct and the derived state current, and leave nothing private in
this repository. The procedure:

- lives in exactly one file;
- is discoverable by Claude Code and by at least one other agent family
  through a thin adapter;
- is held to the code by a test, so that a renamed flag or a moved file fails
  the build rather than misleading an agent.

**Not the goal.**

- Teaching record formats. The store's functions and the existing skills own
  those, and this skill routes to them.
- Teaching writing, campaign design or content policy. The draft says so too.
- Cache administration.
- Replacing `world-card-integration`, `create-world` or
  `populate-world-content`. Those skills gain one closing step that points
  here (section 5).

## 3. Layout

```
.claude/skills/grimoire-store-editing/
  SKILL.md                    canonical: frontmatter + the whole procedure
.agents/skills/grimoire-store-editing/
  SKILL.md                    adapter: the same frontmatter + a pointer, nothing else
backend/tests/test_skills_guard.py
```

- **No symlinks.** The repository is used on Windows, where a symlink in a
  checkout needs a developer setting and is otherwise checked out as a text
  file. That is the draft's reasoning, and `store.atomic` already declines
  linked records for related reasons.
- **No generated copies.** A copy kept in step by a generator is still two
  files, and the second is the one an agent reads stale. The adapter carries
  the frontmatter, which must be duplicated for discovery to work, and a link.
  The drift test holds the duplicated part equal.
- **Which adapter directories.** `.agents/skills/` is the draft's choice and
  the one this spec names. The plan's first task confirms, from each agent's
  current documentation, which directories that agent discovers skills in. The
  test reads the adapter directories from one tuple, so adding or dropping one
  is a one-line change. If no agent the user runs discovers `.agents/skills/`,
  the adapter is dropped and routing through `AGENTS.md` (section 1.1) is the
  whole story.

### 3.1 The adapter, in full

```markdown
---
name: grimoire-store-editing
description: <byte-identical to the canonical description>
---

# Editing a Grimoire store outside the app

The procedure lives in
[`.claude/skills/grimoire-store-editing/SKILL.md`](../../../.claude/skills/grimoire-store-editing/SKILL.md).
Read that file in full and follow it. Nothing here adds to it or overrides it.
```

## 4. The canonical `SKILL.md`

This section is the content outline. The frontmatter is final. The body is
outlined section by section, with the sentences that carry a rule given in
full. The plan writes the prose. It must stay under about 170 lines, because
a long procedure is one an agent skims, and it must link rather than restate
(section 6, check 6).

### 4.1 Frontmatter

```yaml
---
name: grimoire-store-editing
description: Use when creating, editing, renaming, moving or deleting records in a grimoire store (the library under GRIMOIRE_HOME) by any route other than the running app -- a script calling grimoire.store, a Python session, or an editor -- including voice anchors, image descriptions, lore and entity cleanup, tags, and bulk passes after another skill. Covers finding the store, what to write through and what never to touch, and the one `cache sync` call that brings search, recall and overview pages current afterwards. Not needed for edits made in the app's own UI or API.
---
```

The description names the triggers an agent sees in a request ("clean up the
lore", "describe these images", "fix the voice anchors", "bulk retag"), says
when the skill does **not** apply, and names the command, so that an agent
deciding whether it applies does not have to open the file.

### 4.2 Body outline

1. **What this is for** (about 6 lines). Records are files, and the app reads
   them fresh. Derived state (parsed records, search documents, embeddings,
   overview cards) is rebuilt lazily on the next read, so a forgotten sync is
   never wrong, only slow. The procedure makes it current at once, and keeps
   the edit itself safe. Then the scope line: edits made in the app need none
   of this, since the app syncs its own writes (05-C1).

2. **Privacy** (about 4 lines, a link and one sentence). "The store is the
   user's private library. Talking about its contents in the conversation is
   fine; putting any of it, including a world, campaign or character name,
   into this repository is not." Then a link to the privacy section of
   `CLAUDE.md` for the rest. Examples in the skill use the placeholder names
   only.

3. **Find the store** (a command block, both platforms):

   ```
   backend/.venv/bin/python -m grimoire.where          # macOS/Linux
   backend/.venv/Scripts/python.exe -m grimoire.where  # Windows
   ```

   It prints where the library lives and why. "Never assume `~/.grimoire`."
   If the store is a git repository, run `git -C <store> status` before
   starting, so that the edit is a clean diff of its own.

4. **Write through the store where you can** (about 15 lines).
   - Prefer `grimoire.store` functions. For where they are, link to
     `world-card-integration`'s "Work through the store, not the filesystem"
     section rather than listing them again.
   - From Python, wrap the work in `cache_sync.collecting()`, passing what you
     delete as `deleted=`, so that the sync runs when the block exits. A short
     example: Seraphine's voice anchor in world `realm` through
     `voice_anchors.write`, inside `collecting()`, then print the report.
   - "Campaign records are edited through the store functions, or not while
     the app is playing that campaign." The store's functions take the
     cross-process campaign lock. A hand edit of a transcript that a turn is
     rewriting at the same moment can be lost. Link
     `docs/store-guarantees.md`, section "A second process on the same
     store".
   - "Image descriptions and subjects are written through `image_descriptions`
     / `image_subjects`, never by editing files under `assets/image-store/`."
     The object sidecars are written under image locks, and GC reads them.
     Link the store-guarantees section "Shared metadata: descriptions and
     subjects".
   - Editing bytes directly is fine for a record with no store function, or a
     one-line fix in an editor. Keep the file's existing line endings.

5. **Never touch** (a short list).
   - `.cache/`: anything in it, including `.cache/compiled/` and
     `.cache/embeddings/`. "If derived state looks wrong, the files are right":
     never change a record to match the cache. The repair is deleting
     `.cache/compiled/`; link the store-guarantees section on the compiled
     cache once 03 adds it.
   - `backups/`, `logs/`, `usage/` and `llm_connections/` (which holds keys).
   - A campaign's `revision.txt` (its write token, `store/revision.py`).
     `cache sync` bumps it (05 section 10).

6. **Sync once per coherent batch** (the core, about 25 lines).
   - Collect the paths you created or changed, the ones you deleted and any
     you renamed. With a git store, `git -C <store> status --porcelain` lists
     all three (a rename shows as `R old -> new`).
   - Before an `--all` sync, or any batch of more than a handful of hot records,
     run it with `--dry-run` first and read how many texts it would embed.
   - Then run, from the repository root, in both forms:

     ```
     PYTHONPATH=backend/src backend/.venv/bin/python -m grimoire.cache sync \
         worlds/realm/lore/pact.md worlds/realm/characters/seraphine/voice_anchor.md \
         --deleted worlds/realm/lore/tidewatch.md \
         --renamed worlds/realm/items/lantern.md=worlds/realm/items/tide-lantern.md
     PYTHONPATH=backend/src backend/.venv/Scripts/python.exe -m grimoire.cache sync ...   # Windows
     ```

   - "One sync after the batch, not one per file." Scoped forms
     (`--campaign`, `--world`) are for a pass too large to list.
   - `--no-embed` when the user has asked not to spend; the vectors are then
     rebuilt lazily.

7. **Read the result** (a status table, about 12 lines). Exit status `0` is
   done. `1` means some path is `failed` or `refused`.
   - `failed: <ExceptionClass>`: a reader rejects the file. Fix the file, not
     the cache.
   - `refused: <reason>`: the path is outside the store, goes through a link,
     or is not a record directory. Check the path.
   - `cold`: nothing was built from this file yet. That is fine.
   - `stale_space` and `embedding_off`: nothing to embed in the current space.
     That is fine.
   - `cache: off`: the compiled cache is disabled here. That is fine.

   The report never contains record text, so it can be pasted into the
   conversation as it is.

8. **Check one reader** (about 6 lines). After a significant pass, read one
   edited record back through its store function, or open the page in the app.
   For a bulk pass, run the sync again with `--verify`.

9. **A store you cannot run Python against** (about 5 lines). On a phone,
   call `POST /api/cache/sync` on the running app, and poll the run it
   returns. Otherwise do nothing: the app rebuilds lazily. An open app page
   may show the old answer for one frame of the next visit and then replace
   it, with or without a sync (04-C2b). That is expected, not a failure.

10. **Report back** (about 4 lines). Tell the user which files changed and the
    sync counts. Anything committed **to this repository** about the work
    (a commit message here, a doc, a test) uses no store names. Commits
    **in the store's own repository** follow that store's history, as
    `world-card-integration` describes.

### 4.3 What the outline deliberately leaves out

- Record formats, frontmatter rules and slugging, which belong to the store
  functions and the other skills.
- The reasons behind 03 and 05, which belong to their specs and to
  `docs/store-guarantees.md`.
- Any constant from 05 (`WRITE_QUIET_S`, `MAX_PATHS`). An agent does not need
  them, and they will be tuned.

## 5. Changes to the other skills and to `AGENTS.md`

- **`world-card-integration`, `create-world`, `populate-world-content` and
  `ingest-campaign-log`** each gain one closing line: "When the records are
  written, finish with `grimoire-store-editing`'s sync step." It is a link,
  not a restatement. For `create-world` the sync is a no-op (nothing in a new
  world is hot yet), and the line says so, so that nobody reads a cold report
  as a failure. `backend/scripts/create_world.py` and `ingest_scene.py` already
  sync themselves under 05 section 5.5, so the two skills that drive them need
  the line only for edits made outside those scripts.
- **`AGENTS.md`'s "Skills" section** gains one sentence: the store-editing
  procedure exists, and `.agents/skills/` holds discovery adapters that point
  back into `.claude/skills/`. The sentence names the skill and links the
  directory. It is worded fresh, so that it shares no long run with the skill's
  own description (`test_no_document_restates_another` does not compare
  skills, but the skills guard below does).
- **`CONTRIBUTING.md`'s guard table** gains a row for `test_skills_guard.py`
  (no marker family).
- **`CLAUDE.md`**: no change. It routes to `AGENTS.md`, which routes here.

## 6. The drift test: `backend/tests/test_skills_guard.py` (06-C1)

It is named as a guard because it is one: it holds documents to the code the
way `test_docs_guard.py` does. It imports that module's helpers
(`_longest_shared_run`, `MAX_SHARED_RUN`, `_refs`) rather than copying them;
if the plan prefers, it first moves them into a small shared test module.

```python
ADAPTER_DIRS = (ROOT / ".agents" / "skills",)
CANONICAL_DIR = ROOT / ".claude" / "skills"
#: Skills held to "route, do not restate". New skills join it; the six that
#: predate it are not retro-fitted here.
ROUTING_SKILLS = ("grimoire-store-editing",)
ADAPTER_MAX_BODY_LINES = 12
```

Checks:

1. **Every adapter has a canonical.** For each `<adapter dir>/<name>/SKILL.md`,
   `.claude/skills/<name>/SKILL.md` exists.
2. **The frontmatter is identical.** The adapter's `name` and `description`
   equal the canonical's byte for byte, and `name` equals the directory name in
   both. A drifted description is a trigger that fires for one agent and not
   another.
3. **The adapter is thin.** Its body (after the frontmatter) is at most
   `ADAPTER_MAX_BODY_LINES` lines, contains a relative link whose target is the
   canonical file and resolves, and shares no run longer than
   `MAX_SHARED_RUN` words with the canonical body. Its directory holds nothing
   but `SKILL.md`, so no script can be copied beside it.
4. **The canonical's links resolve.** Every relative link in a
   `ROUTING_SKILLS` file resolves, checked with `test_docs_guard`'s `_refs`
   and case-exact spelling.
5. **The commands exist.** Every `--flag` that appears after
   `-m grimoire.cache sync` in the canonical file (fenced or inline) is an
   option string of `grimoire.cache.build_parser()`'s `sync` subparser, and
   every `python -m grimoire.<module>` the file names is an importable module.
   This is the check that keeps the skill and 05-C2 in step: rename a flag and
   the skill fails until it is updated.
6. **It routes rather than restates.** A `ROUTING_SKILLS` file shares no run
   longer than `MAX_SHARED_RUN` words with any document in `test_docs_guard`'s
   `PROSE` (`CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`,
   `docs/store-guarantees.md` and the rest), nor with another skill's
   `SKILL.md`, `world-card-integration` included. Fenced code is excluded, as it
   is there.
7. **Both venv forms.** `test_install_scripts.py`'s `DOCS` gains the canonical
   file, so every venv interpreter it names appears in both forms nearby. That
   test already enforces the rule. It only needs to be pointed at the file.

What it cannot see, stated in its docstring in the house style: whether the
procedure is *right*, whether an example uses a real name (a reviewer's job,
and the privacy rule's), and whether an agent other than Claude Code actually
discovers the adapter.

## 7. Contract

**06-C1. A canonical `grimoire-store-editing` skill, with thin adapters and a
drift test.**

- *Canonical:* `.claude/skills/grimoire-store-editing/SKILL.md`, with the
  frontmatter in section 4.1 and a body that follows section 4.2, under about
  170 lines.
- *Adapters:* one per directory in `ADAPTER_DIRS` (initially
  `.agents/skills/`), each holding only `SKILL.md`: identical frontmatter and a
  link to the canonical file.
- *Drift test:* `backend/tests/test_skills_guard.py`, checks 1 to 6, plus the
  canonical file added to `test_install_scripts.py`'s `DOCS` (check 7).
- *Routing:* `AGENTS.md` names the skill. The four skills that write the store
  end by pointing to it.
- *Guarantee:* every command and flag the skill names exists in the code that
  ships with it. The skill restates no maintained document. No store content
  appears in it.
- *Failure:* a drift fails `make check-py` with a message naming the file and
  the missing flag, link or duplicated run.

This is the checklist's 06-C1, unchanged in substance. The one refinement is
that the canonical file lives in `.claude/skills/`, not in a new `skills/`.

## 8. Interaction with repo rules

- **Docs guards.** `AGENTS.md` keeps pointing at `CLAUDE.md`, and every link
  it gains resolves (`test_relative_links_resolve`). Its new sentence is held
  by `test_no_document_restates_another`. The skill is held by check 6.
  `CONTRIBUTING.md` names the new guard.
- **Venv commands.** Both forms, enforced through `test_install_scripts.py`
  (check 7).
- **Privacy.** Placeholder names only (Seraphine, Mara, Winifred, Realm,
  Saltmarch, and slugs made from them). No example describes a real library's
  shape or size. The skill tells agents where the commit boundary is
  (section 4.2, item 10).
- **Android.** The skill names the API for a phone's store and assumes no
  shell there (05 section 7.9).
- **Review gates.** A skill is a document, but it changes how agents write the
  store. So it goes through the same spec, plan and review gates as code. This
  spec is its spec gate's subject.

## 9. Tests and acceptance

- `test_skills_guard.py` passes on the new files, and each check fails on a
  fixture that breaks it: a description differing by one character, an adapter
  body of thirteen lines, a broken canonical link, a flag the parser does not
  have, a seventeen-word run copied from `CLAUDE.md`, and a file beside an
  adapter's `SKILL.md`.
- `test_install_scripts.py` fails on a Unix-only venv command added to the
  skill.
- `test_docs_guard.py` still passes with the `AGENTS.md` and `CONTRIBUTING.md`
  changes.
- **A dry run of the skill.** In a session with an isolated store (the
  `verify` skill's isolation rules, `GRIMOIRE_HOME` set to a scratch
  directory), an agent asked to "rewrite the voice anchor for Seraphine in
  Realm and delete the old tidewatch lore entry" follows the skill: it
  writes through `voice_anchors.write`, deletes through `entities.delete_entity`
  inside `collecting(deleted=...)` or runs one CLI sync, gets exit status 0,
  and reports counts without record text. This is checked by a person, not CI.

## 10. Non-goals

- A `grimoire-cache-admin` skill. There is nothing for it to drive yet.
- Retro-fitting the six existing skills to "route, do not restate", or to
  both venv forms. That is worth doing, but it is a separate change with its
  own review.
- Generating adapters for agent families nobody here uses.
- Teaching record formats or writing craft.

## 11. Open questions

1. **Where do canonical skills live?** This spec keeps `.claude/skills/` as
   the one home and adds discovery adapters elsewhere. The draft wanted a
   neutral `skills/`. *Recommendation:* keep `.claude/skills/`. If a second
   vendor's agent becomes a regular here, move all seven skills in one change,
   with adapters for each, rather than splitting the convention now.
2. **Which adapter directories?** `.agents/skills/` is presumed.
   *Recommendation:* confirm at plan time from each tool's current
   documentation, and ship no adapter for a directory no tool in use reads.
   `AGENTS.md` routing covers every agent regardless.
3. **A `--git` option on `cache sync`?** Collecting changed, deleted and
   renamed paths from `git status --porcelain` is mechanical, and the skill
   currently asks the agent to do it. *Recommendation:* not yet. It would make
   05's CLI depend on git and on the store being a repository. Revisit if
   agents get the path list wrong in practice.
4. **Should `ROUTING_SKILLS` include the existing six?** *Recommendation:* no,
   not in this change (Non-goals). Each would need rewording to pass check 6,
   which is a review of its own.
