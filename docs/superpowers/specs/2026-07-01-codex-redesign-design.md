# Brutalist Codex redesign — design

**Date:** 2026-07-01
**Branch:** `codex-redesign` (worktree `.worktrees/codex-redesign`)
**Source of truth:** `design_handoff_codex_redesign/` (README.md + `Grimoire.dc.html`
prototype + screenshots). The handoff is high-fidelity: colors, typography, spacing,
borders, and shadows are final and recreated exactly. Where this spec and the handoff
disagree on a visual value, the handoff (and its live prototype) wins.

## Goal

Replace the current dark-parchment UI with the **Brutalist Codex** direction across
every screen — Campaigns dashboard, Worlds dashboard, World view (all tabs),
Character detail, the 4-step Campaign wizard, Config, and the Scene workspace — plus
a 3-theme system (Codex default / Manuscript / Astral) where the same layouts render
under swapped CSS variable sets.

## Decisions made in brainstorming (user was AFK — flagged for review)

1. **Old themes are replaced.** `occult` / `terminal` / `ink` are deleted; the app
   ships exactly `codex` / `manuscript` / `astral`. Saved configs holding an old
   theme name fall back to Codex via the existing `resolveTheme` fallback. Backend
   `DEFAULT_THEME` changes `"occult"` → `"codex"` (config store + its tests).
2. **Fonts are self-hosted** via `@fontsource` npm packages, not the Google Fonts
   CDN. Grimoire is a local-first app (markdown store in `~/.grimoire`); it must not
   change appearance when offline. Weights per the handoff: Big Shoulders Display
   500–900, Newsreader 400–600 + italics, JetBrains Mono 400/500/700, plus
   Cormorant Garamond, EB Garamond, Cinzel, Spectral, Space Mono.
3. **Full handoff scope**, including the functional deltas (message `speaker`,
   configurable You/Grimoire labels, wizard step-1 calendar/holidays, rail
   date/holiday, campaign world-copy views). Rationale: the backend already has
   almost all supporting endpoints, so the deltas are small.
4. **The CLAUDE.md list/detail editor pattern stays** for editing flows (greetings,
   lore, locations, characters, PCs). The handoff specifies browse/read layouts and
   never shows edit forms; editors keep their structure and get restyled through the
   new variables. CLAUDE.md conventions take precedence where the handoff is silent.

## Approach

**Theme-system-first, then screen-by-screen restyle** (the handoff itself mandates
"implement theme system first — everything depends on it"). The app keeps working
and tests keep passing after each slice.

Alternatives considered and rejected:
- *Parallel rebuild* (new screens alongside old, switch at the end) — doubles the
  surface, orphans the existing test suite until the switch, big-bang risk.
- *CSS-only reskin, layout changes second* — the handoff's layouts differ enough
  (chrome topbar, vertical speaker spines, wizard step-1 fields, rail footer) that
  TSX changes are unavoidable; two passes over every screen wastes the first pass.

## 1. Theme system

- `frontend/src/theme/types.ts` — `Theme.tokens` stays `Record<string, string>`;
  no type change needed.
- Replace `themes/occult.ts`, `terminal.ts`, `ink.ts` with `codex.ts`,
  `manuscript.ts`, `astral.ts`. Each carries the full variable table from the
  handoff README (§ "Variables and their values per theme"): `--bg --surface
  --panel --panel2 --ink --subtle --muted --rule --rule-soft --track --chrome
  --chrome-text --chrome-muted --chrome-rule --accent --on-accent --quote --page
  --page-ink --page-muted --banner-bg --banner-ink --disabled --rw --rw2 --rw3
  --sh2 --sh3 --sh4 --sh5 --fd --fb --fm --fx --glow`. Exact `--fx` overlay values
  (Manuscript gold radial wash, Astral starfield) are read from `Grimoire.dc.html`
  `this.themeDefs` during implementation.
- `themes/index.ts`: `DEFAULT_THEME = "codex"`; registration list swapped.
  `resolveTheme` already falls back to the default for unknown names — this is the
  old-config migration.
- The old token names (`--fg`, `--font-display`, `--font-body`, `--radius`)
  disappear; `index.css` is rewritten against the new vocabulary. Components never
  reference raw hex.
- `ThemeProvider` is unchanged (applies tokens to `:root`, persists via config).
  Config's theme picker becomes the 3 equal CODEX / MANUSCRIPT / ASTRAL cards.
- Fonts imported once in `main.tsx` via `@fontsource/*` subpath imports.
- **Codex design law:** zero border radius anywhere; hard 2px ink borders; hard
  offset block shadows (never blurred). Manuscript/Astral soften via the variables
  only. Buttons always set `color` explicitly.

## 2. Global chrome (App.tsx)

Top bar on `--chrome`: logo `grimoire-128.png` at 30×30 + "✦ GRIMOIRE" brand block
(`--fd` 900 24px, right border, `text-shadow: var(--glow)`), CAMPAIGNS / WORLDS nav
buttons (`--fm` 700 12px; active = `--accent` bg + `--on-accent` text, driven by
route matching). Right side: `● OPENROUTER · CONNECTED` status (`--fm` 10px,
dot in `--accent`; text reflects `key_set` — `CONNECTED` vs `NO KEY`), divider,
CONFIG button (active = underline + `--chrome-text`).

View transitions: 0.3s ease slide-up (`translateY(6px)→0`), **never animating
opacity from 0** (handoff: frozen-frame captures go blank).

## 3. Screens

Layouts identical across themes; sizes are the Codex desktop reference. Pixel
values below are from the handoff README; the prototype HTML is authoritative.

### Campaigns dashboard (`/`)
Centered 940px column, padding 44px 56px. H1 "CAMPAIGNS" (`--fd` 900 64px,
line-height .86) over a `--rw3` rule; "+ NEW CAMPAIGN" accent button (`--sh4`)
top-right; count label under the rule. Campaign list = one bordered `--surface`
block; each row a full-width button: name `--fd` 700 26px, metadata line `--fm`
10.5px `WORLD ▸ {world} · {n} SCENES · LAST: {scene}`, right-aligned `→` in
`--accent`. Row → its scene workspace (first scene).

### Worlds dashboard (`/worlds`)
Same header pattern; joined inline create (input + CREATE, shared `--sh3`).
2-col card grid: `--sh5` cards, name `--fd` 800 30px, italic blurb, footer
`{n} ENTITIES · {n} CHARACTERS · {n} LORE` over a `--rule-soft` top border.

### World view (`/worlds/:wid`)
Back link `‹ ALL WORLDS`; H1 56px; italic blurb. Tab strip CHARACTERS / PCS /
LOCATIONS / LORE / GREETINGS (active = 3px `--accent` bottom border on the strip's
2px rule). Characters tab: 3-col card grid, 96px initials avatar block colored by
role (Host `--accent`, PC `--chrome`, Extra `--muted`, Cast `--subtle`), name +
role chip. Other tabs: bordered list rows (name + italic sub + right tag chip).
Editing flows behind these views keep the list/detail editor pattern, restyled.

**World-copy mode:** a campaign-scoped variant of the same view (new route,
`/campaigns/:cid/world/*`), rendering `WorldView` with `EntityScope
{kind:"campaign"}` data. Back link reads `‹ {CAMPAIGN NAME} / WORLD COPY` and
returns to the scene; a banner (`--rw` solid `--accent`, `--banner-bg`/`--banner-ink`)
explains the fork. All world/cast/location links inside a scene navigate here,
never to the shared world.

### Character detail
Back link `‹ {WORLD} / CHARACTERS`. Header: 120×120 avatar (role bg, `--rw`
border, `--sh5`), name `--fd` 900 48px, `BY {creator} · {ROLE}`, outlined tag
chips. Version switcher: joined segmented buttons top-right. Sections
(Description / Personality / Scenario / First message): `--fm` 10px `--accent`
label over a `--rw2` rule, body 17px/1.6. Alternate greetings: italic blocks with
4px `--accent` left border.

### Campaign wizard (`/campaigns/new`)
Max-width 680px, H1 52px, single-row bordered stepper (current = accent cell +
label; done = `--panel` + ✓). Step 1 gains **Calendar** (select, "Gregorian",
caption "MORE PROVIDERS TO COME") and **Holidays** (US/UK/Canada/Australia/
Israel/None, caption "REGIONAL HOLIDAY SET") in a 2-col row — wired to the
existing `createCampaign(name, world, region)` API. NEXT ▸ disabled until name
non-empty (`--disabled` bg). Steps 2–4 keep current logic, restyled per handoff
(chip tag editor, repeatable location cards with dashed add button, greeting
chips / opener streaming panel with blinking block cursor, FINISH ▸ on `--chrome`).

### Config (`/config`)
Max-width 680px. Existing controls restyled: storage location (joined path input +
MOVE + confirmation line), API key password input, **ModelCombobox** (joined
dropdown, `--sh5`, name+price rows, filter on id+name, select on mousedown),
system prompt textarea, quote-color checkbox (`accent-color: var(--accent)`),
**theme cards** (3 equal cells, selected = accent + `--sh3`, applies instantly),
SAVE + "SAVED ✓" flash. New: **speaker label inputs** — the "You" / "Grimoire"
display-name defaults (see § 4).

### Scene workspace (`/campaigns/:cid`)
Keeps the existing 3-column structure (`.sidebar | .main | SceneInspector`),
re-gridded to `236px | 1fr | 286px` under a campaign **sub-header** on `--chrome`:
`‹ CAMPAIGNS`, divider, campaign name `--fd` 700 20px, `WORLD ▸ {name} ↗`
(accent underline → world copy). Right: CHANGES, END SCENE (accent).

- **Scene rail** (`--panel`): `SCENES / NN` counter, `+ NEW SCENE` (`--chrome`
  solid), rows `NN · Title` (active = `--accent` bg). Rename/delete affordances
  from `EditableRow` are preserved within the new row styling. Pinned bottom:
  `CAMPAIGN WORLD ↗` outlined button + campaign date line (`FRI 3 JUL 2026`,
  `--fm` 10px, from the existing scene datetime endpoint) + `✦ {HOLIDAY}` accent
  line when `holidays_today` is non-empty. The inline `<details>` CalendarConfig
  leaves the rail body: clicking the pinned date line toggles the existing
  CalendarConfig editor (restyled) in a panel above the transcript, the same
  slot the absorb panel uses. Initial calendar setup now happens in wizard
  step 1.
- **Transcript** (center, `--page`/`--page-ink`): scene title `--fd` 900 34px
  uppercase, `--rw` bottom rule, `--glow`. Messages lose their cards/bubbles
  entirely: flex row with a **vertical speaker spine** (`writing-mode:
  vertical-rl` + `rotate(180deg)`, `--fm` 10px, `align-self: flex-start` —
  critical), user label in `--quote`, others `--page-muted`. Label = message
  `speaker` if present, else the configured You/Grimoire defaults. Body
  16.5px/1.62; quoted spans colored via the existing `quotePlugin` + `.color-quotes`
  toggle. Message editing keeps its affordance (an edit control in/near the spine
  row) since the handoff doesn't forbid it. Streaming keeps typing-in with a 9×16
  `--accent` block cursor (`steps(1)` 1s blink) + auto-scroll.
- **Composer:** borderless textarea on `--page` under a `--rw` top rule,
  SEND ▸ (`--chrome` bg, left `--rw` border). Enter sends, Shift+Enter newline
  (already implemented).
- **Inspector** (`--panel`, 4 sections with `--rw` rules): STORY SO FAR, CAST
  (name ↗ → character in world copy + role chip), LOCATION (↗ → locations tab of
  world copy), CONTEXT — header `CONTEXT … {pct}%`, 10px bordered accent bar,
  token caption, then one `<details>` per section (already implemented) restyled:
  7px square dot (Transcript = `--accent`), label, `{tokens} · {pct}%`; expanded =
  4px proportional bar on `--track` + the section's `text` in a `--fm` 10.5px pre
  block (`--panel2`, max-height 150px). The `ContextSection` API already carries
  `text` and `tokens` — no backend change.
- **END SCENE / absorb panel:** existing absorb flow restyled on `--panel2`
  ("REVIEW SCENE SUMMARY" `--fd` 800 22px, CANCEL outlined / SAVE SUMMARY solid
  chrome). The existing timeline/edit-approval rows keep working inside it.
- **CHANGES banner:** existing `ChangesPanel` restyled (`--fm` 11px/1.7, accent
  caption, bold `--ink` values).

## 4. Functional deltas (backend + frontend)

1. **`speaker` on messages.** `Message` gains optional `speaker: str | None`
   (backend scene store + routes; frontend `Message` type). Transcript label
   precedence: `speaker` → configured default for the role. Anticipates
   per-character posts; nothing writes it yet except the API passthrough.
2. **Configurable role labels.** Config gains `user_label` (default "You") and
   `assistant_label` (default "Grimoire") — backend config store + `/api/config`,
   frontend Config inputs + transcript fallback labels.
3. **Wizard step-1 calendar/holidays** — UI only; `createCampaign` already accepts
   `region`. Calendar select is a single "Gregorian" option (disabled-in-practice,
   caption "MORE PROVIDERS TO COME").
4. **Campaign world-copy route** — `/campaigns/:cid/world/*` rendering the world
   view against campaign scope, with banner + back-to-scene link. Uses the existing
   `EntityScope {kind:"campaign"}` plumbing; gaps found during implementation
   (e.g. a campaign-scoped characters listing) are closed with matching endpoints.
5. **Rail date/holiday** — render from the existing `getSceneDatetime` response.
6. **Backend default theme** — `DEFAULT_THEME = "codex"` in
   `backend/src/grimoire/store/config.py` + test updates.

## 5. CSS architecture

`index.css` stays the single stylesheet (project convention) and is rewritten
top-to-bottom against the new variables: base/reset + fonts, chrome, page scaffold
(H1/rules/back-links), buttons/chips/inputs (joined-control pattern), list blocks,
card grids, tab strip, wizard, config, scene workspace (rail/transcript/inspector),
absorb/changes, editor (list/detail) restyle, transitions. Expect roughly 3–4× the
current 342 lines; if it grows unwieldy the plan may split it into a few imported
sheets (`theme.css`, `chrome.css`, `scene.css`) — a mechanical, reviewable split.

## 6. Testing

- Every existing vitest suite keeps passing, updated where selectors/copy change
  (e.g. `msg-card` → spine rows, topbar link classes, theme names in
  `ThemeProvider.test.tsx`, `ConfigView.test.tsx` theme cards).
- New/updated coverage: theme files expose the full variable set and `codex` is
  default; transcript renders `speaker` label with configured fallbacks; wizard
  step 1 sends `region`; rail shows date + holiday line when `holidays_today`
  non-empty; world-copy view shows banner + campaign back link; config saves
  `user_label`/`assistant_label`.
- Backend: pytest for `speaker` round-trip, new config fields, `DEFAULT_THEME`.
- Visual sanity: run the app and compare against `screenshots/` per theme
  (manual; the prototype is authoritative).

## Out of scope

- New behaviors not in the handoff (per-character posting UI, calendar providers
  beyond Gregorian).
- Mobile/responsive work beyond what exists (`@media` inspector hide stays).
- Restructuring editor flows (list/detail pattern is kept as-is visually restyled).
