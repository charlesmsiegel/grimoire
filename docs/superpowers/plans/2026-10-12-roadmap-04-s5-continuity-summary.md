# Roadmap 04 S5 — `continuity_summary`, in-process, with the text seam

> Executed inline; plan gate by substitute review, the PR's `@codex review`
> standing in for the CLI gates.

**Goal:** 04-C1b's `overview.continuity_summary` (in-process) and 04-C1c's
`owed`, `continuity-overlaps`, `continuity-closures` chores and the shell's
`ledger_open`. Spec 04 sections 3.3, 3.5, 3.6, 12.1–12.4.

## Design choice: one bundle, threaded by context

The spec allows `*_from_text` twins "or one `Texts` bundle threaded through
`pending.Current.load` and `effective.records` / `live_canon` / `links`".
The continuity call graph reaches its five files from a dozen path-taking
functions, so threading an argument through all of them would touch every
signature. Instead:

- `store/ledger_texts.py` (stdlib-only leaf): `text(cid, name, path)` is the
  one place each reader gets its file's text; `feeding(cid, files)` makes it
  answer from handed bytes for that campaign (strict UTF-8, universal
  newlines, raising where `read_text` raises) and open nothing.
- Each reader keeps its path form as "ask `ledger_texts.text`, then parse
  with the twin": `plot.read_from_text`, `commitments.read_from_text`,
  `events.read_from_text` (with its tolerance; `_mutable` shares the seam),
  `continuity.doc.load_from_text`, `continuity.candidates.load_from_text`.
  One parser per file; the live and fed paths are the same code.

## Tasks

1. The seam and twins above. `test_continuity_candidates`' leaf test allows
   `ledger_texts`.
2. `store/overview/kinds.py`: `continuity_paths(cid)` (each file from its own
   module's `_path`), `continuity_live(cid)` (today's code: one
   `effective.commitments` call gives `ledger_open` and `owed`;
   `pending.findings` filtered `live` gives the chore counts; any exception is
   unknown — the deliberate widening of section 3.5), and `continuity(cid,
   files)` = `continuity_live` under `feeding`.
3. `read.continuity_summary(cid)`: `memo_stamped` keyed on the campaign root
   in `_OVERVIEW_POOL`; each file stamped before its bytes; an absent file
   vouched for by the campaign root's stamp; a failed read (not absence) takes
   the live path and remembers nothing.
4. Consumers through `_Ctx.continuity()` (one per request, shared by the
   shell block and the badge's chores): `owed`, overlaps, closures (and the
   threads/resolutions link), and the shell's `ledger_open`. The shell's own
   `_ledger_open` goes. Items expansions stay live.
5. Frozen sweep gains `overview.continuity_summary[<cid>]`; snapshot reviewed.

## Tests (`tests/test_overview_continuity.py`)

Oracle equality; a campaign with no ledgers; `commitments.json` holding `[]`
(200 with `owed` absent and `ledger_open: null`, where the old `_owed`
raises); the byte-fed compute opens nothing and reads no clock (audit hook,
`paths.now_iso` and `time.time` trapped); feeding is per campaign and
complete; CRLF and a bad byte decode as `read_text` would; each of the five
files created/edited/deleted is seen; a same-size rewrite with mtime restored
is seen; the shell and Todo share one computation; a failed read takes the
live path and is not remembered.
