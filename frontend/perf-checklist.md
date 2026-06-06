# Performance budgets — manual checklist

Spec 14 §Performance budgets sets four numbers we ship against. There is no
Lighthouse-in-CI yet (deliberate; see spec §28 — the budget is verified by
hand and by the lightweight in-app instrumentation in
`frontend/src/state/perf.ts`).

Run the checklist before any release that touches the play loop, the library
list, or the campaign switcher.

## Setup

1. Build a release-mode bundle:
   ```bash
   pnpm --filter frontend build
   pnpm --filter frontend preview
   ```
   Then open the preview URL. Running against `pnpm dev` will overstate every
   number because of dev-mode HMR + unminified bundles.
2. Use a clean Chrome profile (no extensions) with devtools open.
3. In the devtools console, enable the in-app perf log for this session:
   ```js
   localStorage.debug = "perf"; // optional, not used today
   ```
   The recommended path is to launch the preview with `VITE_PERF_LOG=true`
   in the environment so `markEnd` calls `console.debug("[perf] …")`.

## Budgets

| Budget                           | Number     | Span name          |
| -------------------------------- | ---------- | ------------------ |
| Initial load                     | < 2 000 ms | `app:initial-load` |
| Library list (100 assets) render | < 500 ms   | `library:render`   |
| Campaign switch (library cached) | < 300 ms   | `campaign:switch`  |
| Scene jump within a campaign     | < 500 ms   | `scene:jump`       |

The spans show up in the Performance tab under **User Timing** as well as in
the console when `VITE_PERF_LOG=true`.

## Steps

### 1. Initial load

1. Quit the browser. Cold-open the preview URL.
2. Watch the console for `[perf] app:initial-load: <N>ms`.
3. Pass if `N < 2000`. Repeat 3 times; median wins.

Manual fallback (paste in devtools when launching from a fresh tab):

```js
performance.now(); // record before navigation
// after the app renders:
performance.now();
```

### 2. Library 100 assets

1. Seed a library with at least 100 worlds (or characters in one world). See
   `backend/scripts/seed_demo.py` or hand-edit `data/library/worlds/`.
2. Reload, then click **Library** in the nav.
3. Watch the console for `[perf] library:render: <N>ms`.
4. Pass if `N < 500`.

Manual fallback:

```js
const t0 = performance.now();
// click Library, wait for the list to appear
performance.now() - t0;
```

### 3. Campaign switch with library cached

1. Open campaign A. Wait for `[perf] campaign:switch` to log once.
2. Navigate to **Library** and back. The library API responses should be
   served from `frontend/src/api/library.ts`'s 30s cache (no network calls
   in the Network tab — filter by `/api/library`).
3. Open campaign B. Watch the console for the next
   `[perf] campaign:switch: <N>ms`.
4. Pass if `N < 300`.

To verify the cache: open the Network tab, filter `library`, switch back to
campaign A within 30 seconds. Cached calls show no new request rows.

Manual fallback:

```js
const t0 = performance.now();
// click campaign B in the sidebar
// when the campaign header text changes:
performance.now() - t0;
```

### 4. Scene jump

1. In a campaign with multiple scenes, trigger a refresh / scene change
   (advance a scene, or — once §9 lands — click "Jump to scene" from the
   timeline).
2. Watch the console for `[perf] scene:jump: <N>ms`.
3. Pass if `N < 500`.

Manual fallback:

```js
const t0 = performance.now();
// trigger the scene change
// when the new scene id renders:
performance.now() - t0;
```

## When a budget fails

- Check whether the matching span actually closed (`markEnd` requires a prior
  `markStart` for the same name).
- Re-run with `VITE_PERF_LOG=true` to confirm the number is consistent.
- File a budget-regression ticket linking to a Performance-tab profile of
  the failing path before changing the budget number.
