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

/** A pre-notice was acknowledged, or an acknowledgement taken back (#106).
 *
 *  A fourth channel because two surfaces show the same ledger at once and
 *  neither owns it: `SceneInspector`'s When section reads notices off the scene
 *  datetime payload, and `NewSceneChooser` reads them from the campaign clock.
 *  CampaignView mounts them as independent siblings, so the chooser can be
 *  overlaid on a live inspector -- and a dismissal in one left the other holding
 *  a payload from before it, showing the reader the warning they just closed.
 *
 *  Emitted from the api client's mutators for the reason the three above are:
 *  the mutators are the one place every path goes through, so a surface cannot
 *  forget -- including the next one somebody adds. */
export const noticesChanged = notices.emit;
export const onNoticesChanged = notices.subscribe;
