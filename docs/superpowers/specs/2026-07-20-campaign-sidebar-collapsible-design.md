# Collapsible campaign layout + editable active cast

**Date:** 2026-07-20
**Status:** Approved

## Problem

The campaign page (`CampaignView.tsx`) is a fixed three-column layout — scene
rail, chat, inspector sidebar — under a fixed two-bar chrome (global topbar +
campaign subheader). None of it collapses, so on smaller screens or when the
user just wants to focus on the transcript, there's no way to reclaim the
space. Within the right-hand inspector (`SceneInspector.tsx`), every section
(Story so far, Active characters, Location, Prose style, When, Context) is
always fully expanded, with no way to tuck away sections that aren't relevant
to the current moment.

Separately: the "Active characters" section is read-only. There is no way to
remove a character from a scene's cast at all — `store.appearances` only has
`appear()` (add). `CastPanel.tsx` (the pre-scene setup panel, shown only while
`messages.length === 0`) can add cast members, but that flow disappears the
moment a scene has its first message, and even then it can only add, never
remove.

## Decision (user-approved)

Five independent but related pieces, all scoped to the campaign page:

1. Every `.side-section` in `SceneInspector` becomes collapsible — click its
   `<h4>` header to toggle.
2. The scene rail (left) and inspector (right) each become collapsible as
   whole panels — collapsing fully hides the panel and leaves a thin edge tab
   to bring it back. Rail tucks left, inspector tucks right.
3. Both header bars — the global topbar and the campaign subheader — become
   collapsible, but only while viewing a campaign; every other route always
   shows the topbar fully, regardless of the stored preference.
4. The "Active characters" section gains real add/remove capability, usable
   at any point in a scene (not just pre-scene setup). Removing/adding a
   character mid-scene (once the scene already has messages) narrates an
   italic transition line in the transcript, mirroring how location and date
   changes narrate; before the first message, add/remove stays silent
   (matching `CastPanel`'s existing behavior).
5. All collapse state (section-level, panel-level, header-level) persists via
   `localStorage` — it's UI chrome, not campaign data, so it does not belong
   in the `~/.grimoire` store.

## Design

### 1. Collapsible sidebar sections

`SceneInspector` currently renders each section as:

```tsx
<div className="side-section">
  <h4>Active characters</h4>
  ...content...
</div>
```

Replace with a small local helper inside `SceneInspector.tsx` (not a new
shared component — the pattern is only used here):

```tsx
function SideSection({ id, title, collapsed, onToggle, children }: {
  id: string; title: string; collapsed: boolean; onToggle: (id: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="side-section">
      <button className="side-section-head" aria-expanded={!collapsed}
              onClick={() => onToggle(id)}>
        <h4>{title}</h4>
        <span className="side-section-chev">{collapsed ? "▸" : "▾"}</span>
      </button>
      {!collapsed && <div className="side-section-body">{children}</div>}
    </div>
  );
}
```

`SceneInspector` holds `const [collapsed, setCollapsed] = useState<Record<string, boolean>>(...)`,
initialized from `localStorage` (key `grimoire.inspector.sections`, a JSON
object of section id → bool) and written back on every toggle. Every existing
section (`offscreen`, `story`, `cast`, `location`, `style`, `when`, `context`)
wraps its current content in `<SideSection>`. No behavior change to what's
*inside* each section — this only wraps the header/visibility.

The Context section's inner per-entry `<details>` (token breakdown rows) is
unrelated and untouched — collapsing the outer "Context" section just hides
the whole block, entries and all.

### 2. Collapsible rail + inspector panels

`CampaignView.tsx`'s `.layout` is a CSS grid:
`grid-template-columns: 236px 1fr 286px`.

Add two booleans, `railCollapsed` / `inspectorCollapsed`, initialized from
`localStorage` (`grimoire.rail.collapsed`, `grimoire.inspector.collapsed`) and
persisted on toggle, applied as classes: `<div className={"layout" + (railCollapsed ? " rail-collapsed" : "") + (inspectorCollapsed ? " inspector-collapsed" : "")}>`.

**Not inline styles.** An inline `style={{ gridTemplateColumns: ... }}` would
always beat the existing `@media (max-width: 1100px)` stylesheet rule
(inline style has higher specificity than any selector in an external
sheet), so a persisted "expanded" inspector would keep its 286px grid track
reserved — invisible but still taking the space — on narrow viewports. Using
classes keeps the cascade in one place (the stylesheet) so the narrow
breakpoint can still win. In `index.css`:

```css
.layout { grid-template-columns: 236px 1fr 286px; }
.layout.rail-collapsed { grid-template-columns: 28px 1fr 286px; }
.layout.inspector-collapsed { grid-template-columns: 236px 1fr 28px; }
.layout.rail-collapsed.inspector-collapsed { grid-template-columns: 28px 1fr 28px; }

@media (max-width: 1100px) {
  .inspector, .inspector-tab { display: none; }
  .layout, .layout.inspector-collapsed { grid-template-columns: 236px 1fr; }
  .layout.rail-collapsed, .layout.rail-collapsed.inspector-collapsed { grid-template-columns: 28px 1fr; }
}
```

The media-query rules must stay positioned *after* the four base
`.layout...` rules in the file (same specificity per matched selector pair,
so normal cascade source-order decides it) — this mirrors how the existing
narrow-viewport rule already overrides the base `.layout` today, just
extended to the two new collapsed-state selectors so neither can leave a
stale, invisible grid track reserved. (`inspector-collapsed` alone collapses
to the same 236px/1fr as the plain narrow case, since the inspector is force-hidden
either way below 1100px; only `rail-collapsed` still matters there.)

Each panel gets a small toggle button pinned to its inner edge (`‹` on the
rail's right edge, `›` on the inspector's left edge, when expanded). When
collapsed, the panel's content is not rendered — instead a thin
`.rail-tab`/`.inspector-tab` button fills the 28px column, showing the
opposite chevron, full height, click to re-expand. This matches the existing
`@media (max-width: 1100px) { .inspector { display: none; } }` breakpoint in
spirit (the inspector already knows how to disappear); the new collapse is
just a manual, persisted version of that, plus the edge tab and plus the same
treatment for the rail.

`SceneInspector` is only rendered when `!inspectorCollapsed`; same for the
rail's content vs. its collapsed-tab rendering inside `CampaignView`.

### 3. Collapsible headers

**Scope rule:** the topbar's collapsed state is stored in `localStorage`
(`grimoire.topbar.collapsed`) but is only *applied* while the current route is
a campaign detail page (`/campaigns/:cid`, matched as
`/^\/campaigns\/[^/]+$/` against `location.pathname`, which excludes
`/campaigns/new` and campaign-scoped `/world`). On every other route the
topbar always renders fully — the stored preference is inert there, not
cleared, so it resumes next time a campaign is opened.

`App.tsx` owns the state:

```tsx
const [topbarCollapsed, setTopbarCollapsed] = useState(
  () => localStorage.getItem("grimoire.topbar.collapsed") === "1");
const isCampaignRoute = /^\/campaigns\/[^/]+$/.test(location.pathname);
function toggleTopbar() {
  setTopbarCollapsed((v) => {
    localStorage.setItem("grimoire.topbar.collapsed", v ? "0" : "1");
    return !v;
  });
}
...
<header className={"topbar" + (isCampaignRoute && topbarCollapsed ? " collapsed" : "")}>
```

`.topbar.collapsed` hides `<nav>` and `.topbar-right`, keeping just the brand
row at a reduced height (fully hiding the brand too would leave no visual
anchor that you're still in Grimoire — keeping a slim brand strip is cheap
and avoids that). `toggleTopbar` and `topbarCollapsed` are passed to
`CampaignView` via its route element, same pattern as the existing `ready`
prop.

The campaign subheader's collapse is entirely local to `CampaignView`
(`subheaderCollapsed` state + `grimoire.subheader.collapsed` in
localStorage) — no cross-component plumbing needed.

**Always-reachable toggle strip.** Since both bars can be hidden, add one new
persistent element at the very top of `CampaignView`'s render, above
`.subheader`, that never itself collapses:

```tsx
<div className="chrome-bar">
  <button onClick={onToggleTopbar} aria-pressed={!topbarCollapsed}>
    {topbarCollapsed ? "▾ Nav" : "▴ Nav"}
  </button>
  <button onClick={() => setSubheaderCollapsed((v) => !v)} aria-pressed={!subheaderCollapsed}>
    {subheaderCollapsed ? "▾ Bar" : "▴ Bar"}
  </button>
</div>
```

~24px tall, minimal styling (reuses `.rail-date`-style text-button
conventions). This is the horizontal equivalent of the rail/inspector edge
tab: a small always-visible affordance so collapsing something is never a
dead end.

### 4. Active characters: add + remove

**Backend** (`backend/src/grimoire/store/appearances.py`):

```python
def leave(cid: str, scene_id: str, kind: str, actor_id: str) -> None:
    """Drop `scene_id` from the actor's appearance record. The actor stays
    appeared campaign-wide (other scenes, roster) — only this scene's cast
    loses them. Narrates a transition line once the scene already has
    messages; silent while the scene is still in pre-first-message setup,
    matching appear()'s existing silent-first-add via CastPanel.

    Idempotent: an actor already absent from this scene's cast (never cast,
    or a repeat call after a lost response / retry) is a silent no-op, not
    an error — a retried DELETE must not fail just because the first attempt
    already landed."""
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is None or scene_id not in rec.get("scenes", []):
        return
    from . import scenes  # lazy: scenes <-> appearances is already a lazy pair
    name = _actor_name(campaigns.campaign_root(cid), kind, actor_id, rec["version"]) or actor_id
    rec["scenes"].remove(scene_id)
    _write(cid, data)
    try:
        has_messages = bool(scenes.read_scene(cid, scene_id)["messages"])
    except scenes.SceneNotFound:
        return  # synthetic/test scene id with no backing file: nothing to narrate into
    if has_messages:
        scenes.append_message(cid, scene_id, "assistant", f"*{name} leaves the scene.*")
```

The `try/except scenes.SceneNotFound` is required, not defensive filler:
many existing `appearances` store tests call `appear()`/`leave()` with a
bare string scene id ("s1", "the-docks", …) that was never created via
`scenes.create_scene` — there is no backing scene file. `scenes.read_scene`
raises `SceneNotFound` for those, so the narration check must tolerate a
missing scene rather than assume every appear/leave call names a real one.

`appear()` gets the matching narration line added for symmetry (currently
narrates nothing regardless of scene state). Both of its branches — an
already-locked actor rejoining *this* scene, and a fresh first-time lock —
need the same "scene already has messages → narrate" check, so it's cleanest
as one check at the end covering either path:

```python
def appear(cid: str, scene_id: str, kind: str, actor_id: str, version_id: str, role: str,
           narrate: bool = True) -> None:
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is not None:
        if rec["version"] != version_id:
            raise AppearError(f"{ref} is locked to version {rec['version']}, not {version_id}")
        if rec["role"] != role:
            raise AppearError(f"{ref} is locked to role {rec['role']}, not {role}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            _write(cid, data)
        else:
            return  # already in this scene: no-op, no narration
    else:
        base = _lock(cid, kind, actor_id, version_id)
        data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
        _write(cid, data)
        campaigns.touch(cid)

    if not narrate:
        return
    from . import scenes  # lazy: scenes <-> appearances is already a lazy pair
    try:
        has_messages = bool(scenes.read_scene(cid, scene_id)["messages"])
    except scenes.SceneNotFound:
        return
    if has_messages:
        name = _actor_name(campaigns.campaign_root(cid), kind, actor_id, version_id) or actor_id
        scenes.append_message(cid, scene_id, "assistant", f"*{name} joins the scene.*")
```

`_actor_name` already exists and is reused, not duplicated.

**Why `narrate` is a parameter, not inferred.** `appear()` has a second
caller: `backend/scripts/ingest_scene.py`'s `build_scene()` writes a whole
historical scene's dialogue via `scenes.append_message` *first*, then calls
`appear()` for every cast member afterward — including characters appearing
for the very first time in the campaign. Without an explicit opt-out, the
rule above ("scene already has messages → narrate the join") would inject a
synthetic "*Marisol joins the scene.*" line into what's supposed to be a
faithful transcript of an already-complete, real historical RP log — a
different kind of appear() call than an interactive mid-scene add through
this feature's new sidebar UI, which genuinely should narrate a first-time
join. The two calls are structurally identical (fresh lock, target scene has
messages) and cannot be told apart from inside `appear()`, so the caller
states its intent explicitly. Fix `build_scene`'s call site
(`backend/scripts/ingest_scene.py:91-94`) to opt out:

```python
    for actor in scene["characters"]:
        kind, aid = actor["kind"], actor["id"]
        vid = resolve_version(cid, kind, aid)
        appearances.appear(cid, sid, kind, aid, vid, "player" if kind == "pcs" else "npc",
                            narrate=False)
```

**Full caller audit** (every `appearances.appear(` call site in the
codebase, confirmed by grep — not just the one this section originally
named):

- `backend/src/grimoire/routes.py:2880` (`_seat_cast_member`, the existing
  `POST .../cast` route) and this feature's new sidebar add (Task 4) — both
  interactive, keep the default `narrate=True`.
- `backend/src/grimoire/store/playing.py:124` (`start_from_greeting`) — safe
  as-is with the default: the function raises `PlayError` earlier if the
  scene already has messages (a greeting can only start a scene, never
  resume one mid-way), so `appear()`'s has-messages check never finds a
  non-empty scene here in practice. No change needed.
- `backend/src/grimoire/store/absorb.py:530` (`apply_edits`'s
  `new_character` handling — the emergent-new-character-during-absorb
  feature) — **also needs `narrate=False`**, for the same reason as
  `ingest_scene.py`: a character absorb discovers mid-transcript and
  retroactively casts is not "live-joining" the scene, it's recording
  something the already-written transcript already implies. Reachable from
  two paths, both post-transcript: the live `PUT .../chronicle` route
  (normal end-of-scene absorb) and `ingest_scene.py`'s own `apply_scene`
  step — meaning this call site left unfixed would have reintroduced the
  exact transcript-corruption bug in the ingest pipeline through a second
  door, even with `build_scene`'s own call site correctly fixed.
- `backend/scripts/ingest_scene.py:94` (`build_scene`) — fixed above.

`leave()` has no batch/non-interactive caller today, so it does not need
the same parameter — YAGNI; add it if one shows up.

New route in `backend/src/grimoire/routes.py`, next to `post_scene_cast`:

```python
@router.delete("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def delete_scene_cast(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    store.appearances.leave(cid, sid, kind, id)
    return {"ok": True}
```

`kind` is still validated (an unrecognized actor kind is a genuine client
error, not a retry case), but "not currently cast" is not — see the
idempotency note on `leave()` above.

**Frontend:**

- `api/client.ts`: `removeFromCast: (cid, sid, kind, id) => request<{ ok: boolean }>("DELETE", ...)`.
- `SceneInspector.tsx`'s "Active characters" `SideSection`:
  - each `inspector-row` gets a small trailing `✕` remove button
    (`aria-label="Remove {name} from scene"`) that calls `removeFromCast` then
    reloads cast (and calls a new `onSceneChanged`-style callback so
    `CampaignView` re-fetches messages and shows the narrated line — plumbed
    the same way `onSceneChanged` already flows from `SceneInspector` for
    location/datetime changes).
  - below the existing rows, a compact "+ Add" affordance: a kind
    select (Character/PC) + actor select + (for characters) role select +
    "Add" button — the same shape as `CastPanel`'s "Add to scene" block,
    calling the existing `api.addToCast`, then reloading cast and calling the
    same scene-changed callback so a narrated join line shows up.
  - the actor/PC option lists (`chars`, `pcs`) that `SceneInspector` needs for
    the picker aren't currently fetched there — add the same
    `api.listCharacters({ kind: "campaign", id: cid })` /
    `api.listCampaignPCs(cid)` effect `CastPanel` already has.

`CastPanel`'s own add flow is untouched — it remains the pre-scene setup
experience; the sidebar's add/remove is the general, always-available path.

### 5. Persistence

All new state uses `localStorage` directly (no wrapper hook needed — five
call sites, not worth abstracting): `grimoire.inspector.sections`,
`grimoire.rail.collapsed`, `grimoire.inspector.collapsed`,
`grimoire.topbar.collapsed`, `grimoire.subheader.collapsed`. These are global
(not per-campaign) — collapsing the inspector is a screen-real-estate
preference, not something that should differ campaign to campaign.

## Non-impacts

- `CastPanel.tsx` is unchanged — it keeps handling pre-scene setup exactly as
  today (location, datetime, cast, opener generation).
- The Context section's inner per-entry token breakdown `<details>` rows are
  unaffected — only the outer section gains a collapse toggle.
- No change to how `scene_cast` / `roster` / `getCast` responses are shaped —
  `leave()` only mutates the `scenes` array inside an existing record.
- `backend/scripts/ingest_scene.py`'s `build_scene()` and
  `backend/src/grimoire/store/absorb.py`'s `apply_edits` (`new_character`
  handling) each need their `appear()` call site updated to pass
  `narrate=False` (see section 4's full caller audit) — the reason the
  `narrate` parameter exists at all. `playing.py`'s `start_from_greeting`
  call needs no change (see section 4 for why it's already safe).
- The `@media (max-width: 1100px)` narrow-viewport rule (`.inspector { display: none; }`)
  is untouched; it now composes with the new manual collapse (both hide the
  same element for different reasons — narrow viewport wins regardless of the
  stored preference, since it's a separate CSS rule, not JS-driven).

## Known limitations (accepted)

- Removing the *last* player-role actor mid-scene is allowed — there is no
  guard requiring at least one player present. This matches how the backend
  already tolerates `pcless` (offscreen) scenes with zero players; no new
  invariant is introduced.
- `leave()`'s narration check (`scenes.read_scene(...)["messages"]`) reads the
  scene once more than strictly necessary (the route already implicitly
  confirms the scene exists via `_require_scene`). Accepted — consistent with
  the existing `set_location`/`set_scene_datetime` pattern, which also
  re-reads before appending.
- No confirmation prompt before removing a character (unlike `deleteScene`'s
  `window.confirm`). Removing from a scene's active cast is low-stakes and
  reversible (re-add), so this intentionally does not follow the delete-scene
  precedent.
- **No locking around `leave()`/`appear()`'s read-modify-write + narration
  append.** A simple retry (lost response, client retries the same request)
  is now safe — both are idempotent for their "already in the requested
  state" case, so a repeat lands as a no-op rather than an error or a
  duplicate transition line. What remains unguarded is genuine *concurrent,
  distinct* mutation: two tabs adding/removing different actors in the same
  scene at nearly the same instant could still race on the
  read-modify-write of `appearances.json` and lose one side's change, or
  independently decide "the scene already has messages" and each append
  their own (differently-worded but not literally duplicate) transition
  line. This is not a new risk class: `set_location` and
  `set_scene_datetime` already do the identical unguarded
  read-modify-write-then-narrate sequence for the same reason, and the store
  has no locking anywhere — accepted as inherent to this single-user,
  local-only app (see the identical acceptance in
  `docs/superpowers/specs/2026-07-17-played-greeting-exclusion-design.md`).
  Not introducing new transactional machinery here keeps this change
  consistent with the rest of the store rather than a one-off exception;
  the idempotency fix above covers the actually-likely case (retries), and
  true concurrent-distinct-mutation is exactly as rare here as it already is
  everywhere else in the store.

## Tests

Backend (`backend/tests/test_appearances_store.py`, plus a route-level check
alongside the existing `post_scene_cast` coverage):

- `leave()` removes the scene id from the actor's `scenes` list but leaves the
  appearance record (and other scenes) intact.
- `leave()` on an actor not cast in that scene (never cast, or a repeat call
  after an earlier successful `leave()`) is a no-op: no exception, no
  narration, `scenes.json` unchanged.
- `leave()` on a scene with existing messages appends the
  `*{name} leaves the scene.*` transition line; on a scene with zero messages
  it stays silent.
- `appear()`'s new narration: joining a scene that already has messages
  appends `*{name} joins the scene.*`; joining an empty scene stays silent
  (covers both the fresh-lock branch and the already-locked-elsewhere,
  rejoin-this-scene branch).
- `appear(..., narrate=False)` never narrates regardless of scene state —
  covers `ingest_scene.build_scene`'s use.
- `appear()`/`leave()` on a scene id with no backing scene file (the
  `SceneNotFound` case most pre-existing `appearances` tests already use)
  does not raise — it's treated the same as "nothing to narrate into."
- `DELETE .../cast/{kind}/{id}` route: 200 + narration on a real member, 404
  for an unknown kind, 200 no-op (no narration, no error) for an actor not
  currently in that scene's cast — including calling it twice in a row.

Also (`backend/tests/test_ingest_scene.py`):

- `build_scene()` on a scene whose cast includes a first-time character
  still produces the exact transcript from the source `turns` — no
  synthetic "joins the scene" line — confirming `narrate=False` actually
  suppresses it (this is the regression the `narrate` parameter exists to
  prevent; `test_build_scene_writes_transcript_cast_location_date` already
  covers the transcript-content assertion and must keep passing unchanged).

Frontend:

- `SceneInspector.test.tsx`: clicking a section header hides its content and
  toggles `aria-expanded`; clicking again restores it; collapse state
  survives a remount (seed `localStorage` before render, assert initial
  collapsed state matches). Active characters: clicking ✕ on a row calls
  `removeFromCast` and the row disappears after reload; the "+ Add" picker
  calls `addToCast` and the new row appears after reload.
- `CampaignView.test.tsx`: rail/inspector collapse buttons hide the panel and
  render the edge tab; clicking the tab restores the panel; grid column
  widths reflect collapsed state. The `chrome-bar`'s two toggle buttons flip
  `.topbar`/subheader visibility; a route change away from `/campaigns/:cid`
  with `topbarCollapsed` stored still renders the topbar in full (mount at
  `/worlds` with the localStorage flag pre-set, assert `.topbar` lacks
  `.collapsed`).
- `App.test.tsx` (or wherever topbar rendering is currently covered, if
  anywhere): the `isCampaignRoute` gating specifically.

Placeholder names in any fixtures follow the repo convention (Seraphine,
Mara, Winifred, Realm, Saltmarch) — never real content.
