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
persisted on toggle. Compute the grid template inline from these:

```tsx
const railW = railCollapsed ? "28px" : "236px";
const inspectorW = inspectorCollapsed ? "28px" : "286px";
<div className="layout" style={{ gridTemplateColumns: `${railW} 1fr ${inspectorW}` }}>
```

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
class NotCastError(AppearError):
    pass

def leave(cid: str, scene_id: str, kind: str, actor_id: str) -> None:
    """Drop `scene_id` from the actor's appearance record. The actor stays
    appeared campaign-wide (other scenes, roster) — only this scene's cast
    loses them. Narrates a transition line once the scene already has
    messages; silent while the scene is still in pre-first-message setup,
    matching appear()'s existing silent-first-add via CastPanel."""
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is None or scene_id not in rec.get("scenes", []):
        raise NotCastError(f"{ref} is not in scene {scene_id}")
    from . import scenes  # lazy: scenes <-> appearances is already a lazy pair
    name = _actor_name(campaigns.campaign_root(cid), kind, actor_id, rec["version"]) or actor_id
    rec["scenes"].remove(scene_id)
    _write(cid, data)
    if scenes.read_scene(cid, scene_id)["messages"]:
        scenes.append_message(cid, scene_id, "assistant", f"*{name} leaves the scene.*")
```

`appear()` gets the matching narration line added for symmetry (currently
narrates nothing regardless of scene state). Both of its branches — an
already-locked actor rejoining *this* scene, and a fresh first-time lock —
need the same "scene already has messages → narrate" check, so it's cleanest
as one check at the end covering either path:

```python
def appear(cid: str, scene_id: str, kind: str, actor_id: str, version_id: str, role: str) -> None:
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

    from . import scenes  # lazy: scenes <-> appearances is already a lazy pair
    if scenes.read_scene(cid, scene_id)["messages"]:
        name = _actor_name(campaigns.campaign_root(cid), kind, actor_id, version_id) or actor_id
        scenes.append_message(cid, scene_id, "assistant", f"*{name} joins the scene.*")
```

`_actor_name` already exists and is reused, not duplicated.

New route in `backend/src/grimoire/routes.py`, next to `post_scene_cast`:

```python
@router.delete("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def delete_scene_cast(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    try:
        store.appearances.leave(cid, sid, kind, id)
    except store.appearances.NotCastError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True}
```

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

## Tests

Backend (`backend/tests/test_appearances_store.py`, plus a route-level check
alongside the existing `post_scene_cast` coverage):

- `leave()` removes the scene id from the actor's `scenes` list but leaves the
  appearance record (and other scenes) intact.
- `leave()` on an actor not cast in that scene raises `NotCastError`.
- `leave()` on a scene with existing messages appends the
  `*{name} leaves the scene.*` transition line; on a scene with zero messages
  it stays silent.
- `appear()`'s new narration: joining a scene that already has messages
  appends `*{name} joins the scene.*`; joining an empty scene stays silent
  (covers both the fresh-lock branch and the already-locked-elsewhere,
  rejoin-this-scene branch).
- `DELETE .../cast/{kind}/{id}` route: 200 + narration on a real member, 404
  for an unknown kind, 404 for an actor not currently in that scene's cast.

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
