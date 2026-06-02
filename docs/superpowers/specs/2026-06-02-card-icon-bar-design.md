# Card Icon Bar — Design

**Date:** 2026-06-02
**Status:** Approved (pending spec review)
**Scope:** Frontend (React/TS) + docs + ESLint config + one GitHub issue. No backend changes.

## Goal

Establish a single, enforced UI convention: **every card renders a `CardIconBar` at
its bottom edge**, and that bar is the one home for per-card actions. As the first
action, every card backing a deletable artifact under `~/.grimoire/` (plus chat posts)
gets a **Delete (🗑) icon**, replacing today's scattered bespoke "Delete" text buttons.

This is phase one: only the delete action moves into the bar. Other per-card actions
(edit, fork, configure) stay where they are for now and migrate into the bar later as
the icon set grows.

## The Rule (documented in CLAUDE.md and AGENTS.md)

> **Every card renders a `CardIconBar` at its bottom edge.** Cards are the block-level
> `*-card` components — `campaign-card`, `library-card`, `entity-card`,
> `entity-card-static`, `timeline-card`, `provider-card`, `suggestion-card`,
> `character-card`, `why-character-card` — plus chat posts (`PostItem`). The bar is the
> single home for per-card actions. Every card that backs a deletable artifact under
> `~/.grimoire/` starts with a **Delete (🗑)** icon in its bar; cards with no delete
> render an empty bar reserved for future actions. **Never render a bespoke
> delete/remove button** — route deletes through `CardIconBar`'s `deleteAction`.
>
> Not cards: sub-element classes (`*-card-head`, `*-card-actions`, `*-card-icon`,
> `*-card-title`, `*-card-meta`, …), the `card-filters` toolbar, and grid wrappers
> (`library-card-grid`, `why-character-cards`).

## Component

`frontend/src/components/CardIconBar.tsx`

```tsx
export interface CardIconAction {
  key: string;
  icon: string;                 // emoji for now; see icon-library issue
  label: string;                // becomes aria-label AND title
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;               // shows a busy glyph, disables the button
  variant?: "default" | "danger";
}

export function CardIconBar({ actions }: { actions: CardIconAction[] }): JSX.Element;
// role="toolbar", aria-label="Card actions". Always renders the container element,
// even when actions is empty (rule: every card has a bar).

export const DELETE_ICON = "🗑"; // TODO: replace with icon library — issue #<N>

export function deleteAction(opts: {
  onClick: () => void;
  label?: string;               // default "Delete"
  busy?: boolean;
  disabled?: boolean;
}): CardIconAction;             // key "delete", icon DELETE_ICON, variant "danger"
```

Each action renders as a `<button type="button">` with `aria-label`/`title` set to
`label`, an `aria-hidden` icon span, `disabled = disabled || busy`, and the busy glyph
(`…`) swapped in for the icon while `busy`.

### Empty bars are invisible until populated

When `actions` is empty the container still renders (so the rule holds literally and the
slot exists for future actions) but has **no min-height and no visible chrome** — it
occupies no visible space. The top-border / padding only appear once the bar holds at
least one button. This keeps config and read-only cards from sprouting empty strips.

## Placement & CSS

New rules in `frontend/src/index.css`:

- `.card-icon-bar` — `display:flex; justify-content:flex-end; gap:…; margin-top:auto`
  so it pins to the card's bottom edge. Top border + padding apply only via
  `.card-icon-bar:not(:empty)`.
- `.card-icon-button` — square icon button, transparent background, hover background,
  focus-visible ring. `.card-icon-button.danger:hover` tints the trash red.

Cards that aren't already flex-columns get `display:flex; flex-direction:column` (and,
where needed, the existing content wrapped) so `margin-top:auto` pushes the bar down.
Per-card CSS tweaks are enumerated in the implementation plan.

## Per-card migration

| Card | Location | Action in bar |
|---|---|---|
| `campaign-card` | CampaignsView | 🗑 → `deleteCampaign` (keep confirm + busy) |
| `library-card` (world) | WorldsListView | 🗑 → `deleteWorld` |
| `library-card` (entity) | EntityListView | 🗑 → `deleteEntity` |
| `library-card` (style guide) | StyleGuidesView | 🗑 → delete style guide |
| `library-card` (image preset) | ImagePresetsView | 🗑 → `deleteImagePreset` |
| `library-card` (calendar) | CalendarsView | 🗑 → `deleteCalendar` |
| `library-card` (holiday set) | HolidaySetsView | 🗑 → `deleteHolidaySet` |
| `timeline-card` (scene) | TimelineView | 🗑 → `deleteScene` |
| `entity-card` (PC) | CastView | 🗑 → `DELETE /pcs/{ref}` |
| `character-card` (PC fieldset) | CharacterCreation | 🗑 → remove this PC |
| `PostItem` (chat post) | ScenePane / PostItem | 🗑 → `deletePost` |
| `library-card` (plugin) | PluginsView | empty bar (code scope; uninstall ≠ delete) |
| `library-card` (mechanics) | MechanicsView (library) | empty bar |
| `entity-card` (display) | LedgerView, ContentBrowser, MechanicsView (campaign) | empty bar |
| `entity-card-static` | WorldView | empty bar (read-only) |
| `provider-card` | ProvidersTab, StartupWizard | empty bar (config) |
| `suggestion-card` | SceneSuggestionView | empty bar |
| `why-character-card` | WhyCharacterPanel | empty bar (read-only diagnostic) |

All existing bespoke delete controls (`campaign-card-delete`, `library-card-action`
delete links, etc.) are removed; their confirm dialogs, busy state, and error handling
are preserved by wiring into the `deleteAction`.

The `character-card` fieldset in CharacterCreation gets a trash that removes the PC from
the current draft/campaign (reusing the existing `removePC` path / PC-delete endpoint;
exact wiring decided in the plan).

All delete endpoints already exist — this is a frontend-only change.

## Enforcement — ESLint rule `no-bespoke-delete`

A custom local rule (under `frontend/eslint-rules/`, wired into the flat config) flags a
JSX `<button>` / `<Link>` / `<a>` when **any** of:

- `className` (static string or template literal) matches `/delete/i`, or
- its accessible text / `aria-label` / `title` starts with `Delete` or `Remove`, or
- it contains the 🗑 glyph,

**except** in:

- `frontend/src/components/CardIconBar.tsx` (the sanctioned implementation), and
- files matching `*Dialog*` / `*Confirm*` (legitimate confirm-action buttons such as
  `ConfirmDestructiveDialog`).

Escape hatch: `// eslint-disable-next-line grimoire/no-bespoke-delete` with a reason.
The rule ships with a unit test covering a flagged case, a CardIconBar-exempt case, and
a dialog-exempt case.

## Icon library — GitHub issue

Open an issue, **"Create a shared icon library,"** that:

- Catalogs every emoji/glyph icon currently used across the app — at minimum
  🗑 (delete), ✦ / ⊕ / ◎ (provider category icons), ✕ (discard), `+` (new),
  `…` (busy) — by scanning the frontend.
- Proposes replacing emoji strings with inline SVG icon components (themeable via
  `currentColor`), starting with the trash icon used by `CardIconBar`.

`DELETE_ICON` and the provider icons get `// TODO(#<N>)` comments referencing the issue.

## Testing

- `CardIconBar` unit test: renders actions, fires `onClick`, respects `disabled`/`busy`,
  renders an empty (invisible) bar when `actions` is empty.
- One regression test per migrated delete: confirm dialog still fires, busy state shows,
  delete API called. (Extend existing `CampaignsView` / `EntityListView` tests.)
- ESLint rule test (flag + two exemptions) via `RuleTester`.
- `pnpm lint`, `pnpm typecheck`, `pnpm test` all green.

## Out of scope (phase two / follow-ups)

- Migrating non-delete actions (edit, fork, configure) into the bar.
- Adding delete capability to cards that intentionally have none (plugins, mechanics,
  providers, read-only displays).
- The SVG icon library itself (tracked by the new issue).
