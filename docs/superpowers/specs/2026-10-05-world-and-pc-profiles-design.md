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
untouched. The UI uses single-line inputs for both new fields, like Summary.

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

- `IncomingReview.tsx`'s `PERSONA_FIELDS` gains both, so a world-side change to
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

`delete_version` removes `history/<vid>/` with the version, so a later version
reusing the id does not inherit a dead version's trail. `delete_pc` already
removes the folder. World fork and bundle export copy the whole world
directory and so carry history along; that is correct (it is the PC's history).

What history does **not** record: writes that do not go through
`update_version` — a sync accept/push replacing a version file, a campaign
`import-version`. Those are library operations with their own review surfaces;
history is the edit trail of the PC editor.

### Store API (`store/pcs.py`)

- `list_revisions(root, pid, vid) -> [{"id", "saved", "name"}]`, newest first.
  `saved` is the ISO time the revision was replaced; `name` is the persona name
  it held, so a list row can say whose text it was.
- `read_revision(root, pid, vid, rid) -> persona dict`. Raises
  `PCRevisionNotFound` for an unknown or unsafe id.
- `restore_revision(root, pid, vid, rid)` = `update_version` with that persona,
  so the text being replaced is itself snapshotted and a restore is undoable.

### Routes

World (`routes/worlds.py`) and campaign (`routes/campaigns.py`) twins:

- `GET  .../pcs/{pid}/versions/{vid}/revisions`
- `GET  .../pcs/{pid}/versions/{vid}/revisions/{rid}`
- `POST .../pcs/{pid}/versions/{vid}/revisions/{rid}/restore`

Campaign reads resolve the **campaign** root (not `overlay.pc_root`), so an
inherited PC lists no history rather than the world's. The campaign restore
mirrors `put_campaign_pc_version`: under `campaign_lock(cid)`,
`ensure_actor_writable`, and the persona-name uniqueness check. The world
restore runs the same uniqueness check `put_pc_version` does. 404s:
`pc not found`, `version not found`, `revision not found`.

### UI

`PCPage.tsx`: a **History** `ColumnSection` in the context column listing the
current version's revisions (time, newest first; "No earlier revisions"
otherwise). Clicking one shows it read-only in the Persona tab (rendered
description + its fields) with **Restore** and **Back to current** buttons.
Restore calls the route and re-reads. The list refreshes after every save,
restore, and version switch.

## #38 — World genre, tone, tags and description

### Storage: `world.md` carries it

Frontmatter gains `genre`, `tone` and `tags` (comma-joined, the `keys`/`owners`
convention, since frontmatter is string scalars only); the markdown **body is
the description**. All single-line fields are whitespace-folded as for
personas, and tags are stripped of commas and empties. Old worlds have none of
it and read as empty strings — no migration. Fork copies `world.md`'s
frontmatter verbatim except identity, so the profile travels with a fork.

The word "tags" is already taken in a world for the greeting-gating vocabulary
(`tags.md`, `/worlds/{wid}/tags`). The new field describes the *world* and gates
nothing, so the UI labels it **Themes** and the API names it `themes` to keep
the two apart; on disk it is `themes:` in `world.md`.

### Store API (`store/worlds`)

- `create_world(name, *, genre="", tone="", themes=(), description="")`.
- `update_world(wid, *, name=None, genre=None, tone=None, themes=None,
  description=None)` — a partial update through the same read-modify-write
  `_restamp` does (worlds have no lock); `rename_world` becomes a call to it.
- `world_profile(wid) -> {"genre", "tone", "themes": [...], "description"}`.
- `read_world` already returns all frontmatter + body; it additionally returns
  `meta.themes` as a list. `list_worlds`/`list_world_rows` rows gain `genre`
  (stat-memoized like the rest of the row) so shelf cards can show it.

### Routes

- `POST /worlds` takes `WorldCreate {name, genre?, tone?, themes?, description?}`
  — a body of only `{name}` behaves exactly as before.
- `PUT /worlds/{wid}` takes `WorldUpdate` with every field optional; a `name`
  that is present must be non-blank (400), absent fields are left alone.
  Response stays `{id, name}`.

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
empty — so every existing prompt, and the frozen campaign snapshot, is
unchanged. Otherwise `# World overview`, then `Genre:`, `Tone:`, `Themes:` lines
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
