# Prose Style Guides — Design

**Date:** 2026-07-12
**Status:** Approved, ready for implementation plan

## Problem

Scene generation has no way to steer prose tone/genre. There's a single global
free-text "System prompt" (`config.md`), but nothing genre-shaped, nothing
reusable across campaigns, and no way to override tone for just one scene or
one campaign. Add a **Style Guide** concept: a named, reusable prose-style
prompt fragment, selectable at three nested levels (global default → campaign
default → scene override), with a handful of genre presets shipped in code and
support for user-authored custom guides.

## Data model & storage

A Style Guide is `{id, name, description, tags, body}` — YAML frontmatter
(`name`, `description`, `tags`) + markdown body, parsed with the existing
hand-rolled `store/frontmatter.py` (same format already used by greetings and
scenes; list fields like `tags` stored as a comma-joined string).

Two sources, merged at read time into one list:

- **Built-in** — seven genre presets (gothic horror, high fantasy, modern
  thriller, noir detective, pulp adventure, shoujo romance, superheroes)
  shipped as `.md` files under `templates/styles/`, resolved via the existing
  `prompts.templates_dir()` (already `GRIMOIRE_TEMPLATES`/Android-safe — no new
  path-resolution code). Committed to the repo: generic genre guidance, no
  private content.
- **Custom** — `.md` files in `<GRIMOIRE_HOME>/styles/`, managed by a new
  `store/styles.py` module mirroring `store/greetings.py`: id is
  `uniquify(slugify(name), exists)` checked against **both** built-in and
  custom ids so they share one namespace; listing globs the directory,
  reads frontmatter, sorts by `natural_key(name)`; a malformed custom file is
  skipped (best-effort, same as `greetings.py`/calendar plugins) rather than
  breaking the list.

Dropping a compatible `.md` file directly into `<GRIMOIRE_HOME>/styles/` makes
it available immediately — no import step, same discovery model as custom
calendar providers (`store/calendars/plugins.py`).

Each listed record carries `built_in: bool` so the frontend can distinguish
sources.

## Selection & precedence

Three tiers, each nullable ("inherit"), resolved scene → campaign → global:

- **Global default** — new `default_style_id` field in `~/.grimoire/config.md`
  (same flat frontmatter file as the existing global system-prompt text).
- **Campaign default** — new `style_id` field in `<campaign-root>/campaign.md`
  (alongside `name`/`world`/`world_copy`). Unset ⇒ inherits the global default.
- **Scene override** — new `style_id` field in the scene's own frontmatter
  (alongside `title`/`pcless`). Unset ⇒ inherits the campaign default. This is
  the sticky per-scene tier: set once, every subsequent turn/retry/reroll in
  that scene uses it until changed.

Resolution: `scene.style_id or campaign.style_id or config.default_style_id or
None`, then look up the resolved id in the merged style list. If the id
doesn't resolve (style deleted, or a custom style missing after switching
`GRIMOIRE_HOME`), resolution silently falls back up the chain — same
best-effort spirit as the calendar plugin loader. This never breaks
generation; there is no user-facing warning for a stale reference.

## Prompt integration

New template section `templates/scene/sections/prose_style.j2` — a small
labeled block (style name as heading, body as-is) — added to the `sections`
list in `templates/scene/system.j2` immediately after
`global_system_prompt.j2` and before `card_system_prompts.j2`. Rationale:
prose style is a broad authorial-voice instruction, grouped with the other
global-voice section, while still letting a character card's own system
prompt layer on top for that character specifically. Follows the existing
"render, strip, drop if empty" pattern every other section already uses.

`store/context.py`'s data-building step resolves the style id per the
precedence chain and adds `prose_style_name` / `prose_style_body` to the
`data` dict passed into `system.j2`. When nothing resolves, the section
renders empty and is dropped — scenes with no style configured are
byte-for-byte unaffected.

## Backend API surface

New global (non-world/campaign-scoped) routes — a style guide is reusable
content, not tied to one world:

- `GET /api/styles` — list, merged built-in + custom:
  `{id, name, description, tags, built_in}` each (no `body`, keeps the list
  light).
- `GET /api/styles/{id}` — full record including `body`.
- `POST /api/styles` — create custom (`name, description, tags, body`).
- `PUT /api/styles/{id}` — update; 400 if `id` is built-in.
- `DELETE /api/styles/{id}` — delete; 400 if `id` is built-in.
- `POST /api/styles/{id}/duplicate` — clone any style (built-in or custom)
  into a new custom record, name suffixed (e.g. "Gothic Horror (copy)"),
  id uniquified.

Existing endpoints gain one optional field each:

- `ConfigUpdate` / config response: `default_style_id`.
- Campaign read/update: `style_id`.
- Scene meta update (whichever route already patches scene frontmatter fields
  like `pcless`; extended, or added if no generic scene-meta-patch route
  exists yet): `style_id`.

## Frontend

- **New top-level "Style Guides" page** (`StyleGuideEditor.tsx`), following
  the CLAUDE.md list/detail pattern: rail of all styles (built-in ones show a
  "built-in" badge); `view` mode renders the body as markdown with
  description/tags in the sidebar. For a built-in style, the sidebar action is
  **"Duplicate to customize"** instead of **Edit** — creates an editable
  custom copy and switches into `edit` mode on it. Custom styles get normal
  Edit/Delete.
- **Configuration page** — new "Default prose style" `<select>` next to the
  existing system-prompt textarea: options are style names plus "(None)",
  saved via the existing config PUT.
- **Campaign rail** — new picker mirroring `CalendarConfig.tsx` (fetch styles
  + current campaign `style_id`, `<select>` with a "(Use global default)"
  option, explicit Save button), mounted next to the existing
  `CalendarConfig` in `CampaignView.tsx`.
- **Scene view** — a similar picker in the scene sidebar/header (near
  title/`pcless`) with a "(Use campaign default)" option. Saves immediately on
  change (no separate Save button) since it's the sticky per-scene override.

This UI is scaffolding to make the feature usable end-to-end, not a final
design pass — layout/placement may be revisited later.

## Testing

### Backend (pytest)

- `store/styles.py`: built-in+custom merge; CRUD only mutates custom files;
  id uniqueness enforced across both sets; a malformed custom file is skipped,
  not fatal.
- Routes: CRUD happy paths; 400 on editing/deleting a built-in id; duplicate
  endpoint clones a built-in into an editable custom record.
- `context.py`: resolved style body appears in the rendered system prompt with
  correct scene → campaign → global precedence; an unset or deleted-and-thus-
  unresolvable style renders no section at all.

### Frontend (vitest)

- `StyleGuideEditor.test.tsx`, shaped like `GreetingEditor.test.tsx`: row click
  → read-only view; Edit → form (custom only); built-in shows "Duplicate to
  customize" instead of Edit; `+ New` opens the form directly.
- Picker components (Configuration / campaign / scene): loads options from
  `/api/styles`, saves the selected id.

## Out of scope

- Any UI/CLI "import" flow for pulling external files into
  `<GRIMOIRE_HOME>/styles/` — copying a compatible `.md` file into that folder
  by hand is sufficient; the app only needs to discover and read it.
- Per-message or per-generation transient style override (e.g. layered on top
  of the existing `reroll(guidance)` ad-hoc text) — out of scope for this pass;
  the scene-level override is persistent only.
- Visible warnings when a stored style id fails to resolve — silent fallback
  only, per the calendar-plugin precedent.
- World-level style default (the calendar system's world→campaign copy-on-create
  pattern) — not requested; only global/campaign/scene tiers exist.
