type Listener = () => void;

function channel() {
  const listeners = new Set<Listener>();
  return {
    // Iterate a copy: a listener that unsubscribes itself (an effect cleanup
    // firing during the notification) would otherwise mutate the set mid-loop.
    emit: () => { for (const listener of [...listeners]) listener(); },
    subscribe: (listener: Listener) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

/** Shell-wide "something you are displaying went stale" signals.
 *
 *  The shell's chrome — the sidebar and the status bar — outlives every route,
 *  so it cannot refresh itself off navigation alone: the views that change what
 *  the chrome shows do it without moving the pathname. `CampaignsView` renames
 *  and deletes from `/`; `ConfigView` and `ConnectionsView` change which
 *  connection is active, and its model, from their own routes.
 *
 *  Emitted from the api client rather than from each view, so a caller cannot
 *  forget: the mutators are the one place every path goes through. */
const campaigns = channel();
const config = channel();
const shell = channel();
const notices = channel();
const storeMoves = channel();

/** The set of campaigns, or one of their names, changed. */
export const campaignsChanged = campaigns.emit;
export const onCampaignsChanged = campaigns.subscribe;

/** The active connection, or its model, changed — the status bar is now
 *  naming something other than what the next generation will actually use. */
export const configChanged = config.emit;
export const onConfigChanged = config.subscribe;

/** A count the nav rail badges changed.
 *
 *  A third channel rather than a reuse of either above, because neither one
 *  means this. `configChanged` fires for the active connection and its model;
 *  `campaignsChanged` fires for the set of campaigns and their names. Ending a
 *  scene, writing the ledger or creating a sheet changes what the rail says and
 *  fires neither — and does it without moving the pathname, which is the one
 *  thing that would otherwise have refetched.
 *
 *  Emitted from the api client's mutators for the reason the other two are: the
 *  mutators are the one place every path goes through, so a view cannot
 *  forget. */
export const shellChanged = shell.emit;
export const onShellChanged = shell.subscribe;

/** What is imminent, or what the reader has been told about, changed (#106).
 *
 *  A fourth channel because two surfaces show the same ledger at once and
 *  neither owns it: `SceneInspector`'s When section reads notices off the scene
 *  datetime payload, and `NewSceneChooser` reads them from the campaign clock.
 *  CampaignView mounts them as independent siblings, so the chooser can be
 *  overlaid on a live inspector -- and a dismissal in one left the other holding
 *  a payload from before it, showing the reader the warning they just closed.
 *
 *  Fired by more than dismissal, because a pre-notice is DERIVED rather than
 *  stored: filing or re-dating a scheduled event changes what is upcoming,
 *  `warn_days` changes the width of the window, and advancing the clock moves
 *  the moment the whole thing is judged from. Those writes come from panels
 *  sitting in the same rail as the banner -- `EventsPanel`, the calendar
 *  settings -- with no way to refresh it.
 *
 *  Emitted from the api client's mutators for the reason the three above are:
 *  the mutators are the one place every path goes through, so a surface cannot
 *  forget -- including the next one somebody adds. */
export const noticesChanged = notices.emit;
export const onNoticesChanged = notices.subscribe;

/** Another tab of this origin moved the store root to a different library.
 *
 *  A fifth channel because the other four refresh the CHROME, and this is the
 *  one change the page under it cannot survive: whatever route is open was
 *  built from the old library's records -- its ids, its drafts, its selection
 *  -- and every write it can still make goes to the new one, where the same id
 *  may name a different record entirely. `App` answers it by remounting the
 *  routed page, so nothing on screen outlives the store it was read from.
 *
 *  Only a move HEARD from another tab: a move made in this one is made from the
 *  page that is open, which owns its own state -- remounting it would, among
 *  other things, restart the setup wizard at step one halfway through. */
export const storeMovedElsewhere = storeMoves.emit;
export const onStoreMovedElsewhere = storeMoves.subscribe;
