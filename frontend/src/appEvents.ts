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

/** The set of campaigns, or one of their names, changed. */
export const campaignsChanged = campaigns.emit;
export const onCampaignsChanged = campaigns.subscribe;

/** The active connection, or its model, changed — the status bar is now
 *  naming something other than what the next generation will actually use. */
export const configChanged = config.emit;
export const onConfigChanged = config.subscribe;
