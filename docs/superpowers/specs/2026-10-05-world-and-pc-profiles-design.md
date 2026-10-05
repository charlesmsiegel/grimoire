# World and PC profiles: richer metadata and PC revision history

Issues: #38 (world genre / description / tags / tone at creation), #65
(structured PC profile fields), #67 (PC profile revision history).

All three enrich what a record *says about itself* without changing what any
record *is*: nothing here adds a record kind, a store root, or a sync rule.
Each issue offered options; this spec takes the one each issue recommended,
and says where it narrows it.

## #65 — PC goals and narrator notes

### Storage

Two new persona frontmatter scalars, `goals` and `player_notes`, appended to
`pcs.PERSONA_FIELDS`. `blank_persona` gains both as `""`. A file written before
this change has neither key and `_load_persona` already defaults absent keys to
`""`, so there is no migration, and `version_hash`/`dir_hash` (which hash bytes)
carry the new fields through world→campaign sync and materialization for free.

**Single line, enforced.** `store/frontmatter.py` writes `key: value` lines, so
a value holding a newline would split into a second, bogus key on the next
read. That is already true of `summary`, which nothing guards today. `_dump_persona`
therefore folds every run of whitespace in each frontmatter scalar into one
space before writing — for all `PERSONA_FIELDS`, not only the new two, since the
corruption it prevents is the same one. The description (the body) is
untouched. `actor_names.persona_name` folds the same way, so the name the
uniqueness check compares is the name that will be stored. The UI uses single-line inputs for both new fields, like Summary.

### Prompt

`pc_block` in `templates/scene/_persona_blocks.j2` appends, after the
description, `Goals: <goals>` and `Narrator guidance: <player_notes>` — each
line only when its field is non-empty. So a PC with neither field renders
byte-identically to today (the frozen campaign's snapshot does not move).

`player_notes` *does* reach the model: the field exists to tell the narrator
how to handle this PC (what to never decide for them, what to lean into),
which is only useful if the narrator reads it. Both persona sections
(`player_personas`, `absent_players`) share `pc_block`, so both carry it; an
absent PC's notes are still guidance about that PC.

### Other readers

- `components/cardFields.ts`'s `PERSONA_FIELDS` (read by `IncomingReview.tsx`) gains both, so a world-side change to
  them is visible in the incoming-change diff rather than an invisible change.
- `store/export.py`'s book export keeps summary + description only: goals and
  table notes are not story text.

### UI

`routes/PCPage.tsx` (the PC record page): two `<input>`s, **Goals** and **Notes
for the narrator**, after Summary in the form; read-only view shows each as a
`ColumnSection` in the context column when non-empty, beside Pronouns/Summary.
`Persona` in `api/types.ts` gains both as optional strings.

## #67 — PC revision history

### Storage: a copy-on-save sidecar

`pcs.update_version` copies the version file's current text to
`pcs/<pid>/history/<vid>/<rid>.md` before overwriting it — but only when the
new text differs, so a Save with no edits records nothing. `<rid>` is a UTC
timestamp with microseconds (`20261005T120000123456Z`), uniquified against an
existing file, so it is a `safe_id` and sorts chronologically by name.

Retention: the newest **20** revisions per version are kept; older ones are
deleted after each snapshot. A structural bound, to keep a long-edited PC from
accumulating without limit; tune later if it proves short.

Why this is sync-safe: `_version_ids` globs `*.md` non-recursively and
`snapshot`/`dir_hash` cover only `pc.md` + version files, so a `history/`
subdirectory is invisible to sync, to version listing and to materialization
(`overlay.materialize_actor` copies `snapshot`'s files only). A test pins
`dir_hash` unchanged by a history write.

Scope follows the root that was written: a world edit snapshots into the
world's PC folder, a campaign edit into the campaign's copy (materialized
first, as every campaign PC write already is). Campaign history therefore
starts empty and records edits made in that campaign — the first entry is the
inherited text the first campaign edit replaced. A campaign route never writes
into the world.

History never outlives its text. `delete_version` removes `history/<vid>/`;
so do the paths that remove version files without it — a lock purging sibling
versions (`appearances._purge_other_versions`) and an import replacing the
locked version. `overlay.dematerialize_actor` (a copy reverting to inherited)
drops the copy's whole history, and `materialize_actor` clears history residue
with the version residue — otherwise a restore from a stale entry would
re-materialize and write old campaign text over an accepted world change.

Library writes that overwrite a campaign version file *in place* — a sync
accept copying the world's text over a locked, diverged copy, and an import
over the same id — go through `pcs.keep_before_overwrite`, the one door into
history, so the campaign edit they clobber is the newest entry.

World PC saves and restores run under `locks.world_actor_lock(wid)` (they did
not take it before), so two concurrent saves cannot each snapshot the same old
text and drop one from the trail. Campaign ones already hold the campaign lock. `delete_pc` already
removes the folder. World fork and bundle export copy the whole world
directory and so carry history along; that is correct (it is the PC's history).

What history does not record: world-side library writes (a promote or push
landing in the world) — those arrive through their own review surfaces.

### Store API (`store/pcs.py`)

- `list_revisions(root, pid, vid) -> [{"id", "saved", "name"}]`, newest first.
  `saved` is the ISO time the revision was replaced; `name` is the persona name
  it held, so a list row can say whose text it was.
- `read_revision(root, pid, vid, rid) -> persona dict`. Raises
  `PCRevisionNotFoundError` for an unknown or unsafe id.
- `restore_revision(root, pid, vid, rid)` = `update_version` with that persona,
  so the text being replaced is itself snapshotted and a restore is undoable.

### Routes

World (`routes/worlds.py`) and campaign (`routes/campaigns.py`) twins:

- `GET  .../pcs/{pid}/versions/{vid}/revisions`
- `GET  .../pcs/{pid}/versions/{vid}/revisions/{rid}`
- `POST .../pcs/{pid}/versions/{vid}/revisions/{rid}/restore`

Campaign reads go through `overlay.pc_revisions` / `overlay.pc_revision`
(`test_overlay_guard.py` forbids resolving a PC off a raw campaign root
anywhere else); they read the **campaign** copy's history, so an inherited PC
lists none rather than the world's. The campaign restore, under
`campaign_lock(cid)`, finds the revision *before* `ensure_actor_writable`, so a
404 has materialized nothing; then the name check and the write. The world
restore runs the same uniqueness check `put_pc_version` does. 404s:
`pc not found`, `version not found`, `revision not found`.

### UI

`PCPage.tsx`: a **History** `ColumnSection` in the context column listing the
current version's revisions (time, newest first; "No earlier revisions"
otherwise). Clicking one shows it read-only in the Persona tab (rendered
description + its fields) with **Restore** and **Back to current** buttons.
Restore calls the route and re-reads. The list refreshes after every save,
restore, and version switch, and is disabled while the form is open (opening a
revision over an unsaved edit would discard it). Ids are validated against the
timestamp shape, so a sync client's conflict copy in a history folder is not
listed.

## #38 — World genre, tone, tags and description

### Storage: `world.md` carries it

Frontmatter gains `genre`, `tone` and `themes` (comma-joined, the
`keys`/`owners` convention, since frontmatter is string scalars only); the markdown **body is
the description**. All single-line fields are whitespace-folded as for
personas, and themes are stripped of commas and empties. Old worlds have none of
it and read as empty strings — no migration. Fork copies `world.md`'s
frontmatter verbatim except identity, so the profile travels with a fork.

The word "tags" is already taken in a world for the greeting-gating vocabulary
(`tags.md`, `/worlds/{wid}/tags`). The new field describes the *world* and gates
nothing, so the UI labels it **Themes** and the API names it `themes` to keep
the two apart; on disk it is `themes:` in `world.md`.

### Store API (`store/worlds`)

- `create_world(name, *, genre="", tone="", themes=(), description="")`.
- `update_world(wid, *, name=None, genre=None, tone=None, themes=None,
  description=None)` — a partial read-modify-write. Worlds have no lock and
  `world.md` has other unlocked writers (`touch`, the module binding), so it
  re-reads before writing and retries on a changed file, raising
  `WorldChanged` (409) if it keeps losing; `rename_world` becomes a call to it.
- `profile_of(root) -> {"genre", "tone", "themes": [...], "description"}` — one
  parser, used by the world read and by the prompt assembler (which holds a
  root from `campaigns.read.world_root_of`).
- `read_world` already returns all frontmatter + body; it additionally returns
  `meta.themes` as a list. `list_worlds`/`list_world_rows` rows gain `genre`
  (stat-memoized like the rest of the row) so shelf cards can show it. Both
  change the frozen campaign's read-only sweep output (`saltmarch` gains an
  empty `genre` and `themes`), so `snapshot.json` is regenerated deliberately
  with this change; its prompt renders do not move.

### Routes

- `POST /worlds` takes `WorldCreate {name, genre?, tone?, themes?, description?}`
  — a body of only `{name}` behaves exactly as before.
- `PUT /worlds/{wid}` takes `WorldUpdate` with every field optional; a `name`
  that is present must be non-blank (400), absent fields are left alone.
  Response stays `{id, name}`, the name read back from `world.md`.

### Prompt: a `world_overview` section

A new section, **World overview**, between `card_system_prompts` and
`character_descriptions` in `context.assemble.SECTIONS`, tier `BACKGROUND`
(framing the model already gets piecemeal from cards and world info; it gives
way before scene-specific content). Data comes from the campaign's world via
`campaigns.read.world_root_of` → `world.md`, read at assembly time — the same
live-through-the-world relationship every inherited record already has, so no
copy into the campaign is needed (the issue's "copy world.md at
create_campaign" predates the overlay). A campaign with no resolvable world
gets an empty section.

The template renders nothing when genre, tone, themes and description are all
empty — so every existing prompt is unchanged. Otherwise `# World overview`, then `Genre:`, `Tone:`, `Themes:` lines
for the fields that are set, then the description. The world's name is not
included (it is a library label, often not an in-fiction one).

It is setting-level public framing, so it is not on the actor-scoped blanking
list: an NPC's prompt may carry it. `scripts/verify_templates.py`'s gather
mirror and section order gain it.

### UI

- `WorldsView.tsx`: the inline create input stays as the quick path; a
  **More details** disclosure beside it opens genre / tone / themes /
  description fields that go in the same `createWorld` call. Shelf cards show
  the genre under the name when set.
- `WorldOverview.tsx`: an **About** section at the top — read-only by default
  (description rendered with `<Markdown remarkPlugins={[remarkGfm]}>`, genre /
  tone as `.field-hint`, themes as `chip on` spans) with an **Edit** button that
  swaps in the form; Save returns to view, Cancel discards.

## Testing

- Backend: persona round-trip of the new fields and newline folding
  (`test_pcs_store.py`); `pc_block` rendering with and without them
  (`test_context.py`); history snapshot-on-change, no-snapshot-on-noop,
  retention cap, `dir_hash` unchanged by history, delete_version clearing
  history, restore round-trip and undoability; route tests for both scopes
  including "campaign route never writes the world"; world create/update/read
  of the profile, fork carrying it, and the `world_overview` section empty vs
  populated.
- `make check` (incl. `verify_templates.py`, evals offline, frozen campaign,
  ratchets).
- Frontend: PCPage new fields in form + column; history list → preview →
  restore; WorldsView create-with-details; WorldOverview About view/edit.

## Out of scope

Guided world hub (#39), post-creation world meta editors beyond About (#40),
the create-world skill (#56), and recording non-editor writes into PC history.

## Review log

Spec gate: `/codex:adversarial-review` could not run in this environment (no
Codex CLI), so an independent review subagent ran the same adversarial pass.
Its findings and their resolution, all folded into the text above:

1. History outliving its text through dematerialize / purge / import → those
   paths drop it (`pcs.forget_history`).
2. Campaign history read off a raw campaign root fails `test_overlay_guard.py`
   → reads moved into `store/overlay.py`.
3. A 404 restore materializing the PC → revision found before materializing.
4. Concurrent world saves and in-place library overwrites losing history →
   world actor lock; `keep_before_overwrite` on sync accept and import.
5. Folded names slipping past uniqueness → `persona_name` folds too.
6. Frozen sweep output moves → regenerated deliberately.
7. `world.md` lost updates → re-read-and-retry in `update_world`.
8–9. Spec drift and UI states → corrected above.
