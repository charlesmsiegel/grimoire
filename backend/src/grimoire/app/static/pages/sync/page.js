// Devices and sync (114): this device, the devices it pairs with and sync, over the media adapter's ops
// (docs/MEDIA_SYNC_UI.md).
//
// The top of the page is a card for this device, one for each other device (how it is reached, its last sync,
// "Sync now") and a dashed "Pair a device" that opens the pairing form. A sync says "Syncing…" for as long as
// the request takes, then a plain notice: what was exchanged, or what went wrong with "Try again".
//
// What a sync leaves waiting (a keep-both conflict, a fork) is not decided here: one card says how many there
// are and links to the inbox ("Needs you"), where each is decided beside everything else that waits, with the
// same choices and confirmations (pages/inbox, and versions.js for how a conflict's versions are shown). Sync is
// loopback only, unencrypted and never in the background; the page says so, and lists what the app does not
// offer yet instead of showing buttons that fail. Everything from the library or from another device is set as
// text only.

import { feedNotes, fromSync, itemHref, loadFeed, served as inboxServed } from "../inbox/feed.js";

const UNAVAILABLE = { unpair: "Unpairing", background_sync: "Background sync",
  resolve_source_conflict: "Resolving a source's conflict" };

const plural = (count, one, many) => count + " " + (count === 1 ? one : many || one + "s");

export async function mount(root, ctx) {
  ctx.styles("page.css");
  ctx.setTitle("Devices and sync");
  ctx.layout("page");
  const { ui } = ctx;
  const { el } = ui;
  let report = null;  // what the last action did, shown above everything until the next one
  let shownKey = null;  // a key just drawn for a pairing: shown once, to type on the other device
  let pairing = false;  // whether the pairing form is open
  let syncButtons = new Map();  // each device's "Sync now" (and "Try again"), to say "Syncing…" while it runs

  // what the last action did lives in its own box, drawn the moment the action answers: a refresh that fails
  // after a resolve still leaves every version it set aside listed
  const reportBox = el("div", { class: "g-stack sync-report" });
  const showReport = () => reportBox.replaceChildren(...[reportView()].filter(Boolean));
  const page = el("div", { class: "g-stack g-loose" }, reportBox);
  root.replaceChildren(page);

  const load = async () => {
    // the devices are this page's own; a feed that cannot be read is said in its card, and the page still works
    const waiting = inboxServed(ctx) ? loadFeed(ctx).catch((error) => { if (error && error.reason === "left") throw error; return { error }; }) : null;
    const [devices, feed] = await Promise.all([ctx.api("media.devices", {}), waiting]);
    syncButtons = new Map();
    showReport();
    page.replaceChildren(...[
      reportBox,
      devicesSection(devices),
      feed ? waitingCard(feed) : null,
      devices.unavailable.length ? el("section", { class: "g-stack", "aria-label": "Not in this app yet" },
        el("h2", { class: "g-heading" }, "Not in this app yet"),
        el("div", { class: "sync-grid" }, devices.unavailable.map((item) =>
          ui.unavailable(UNAVAILABLE[item.action] || item.action.replace(/_/g, " "), item.reason)))) : null].filter(Boolean));
  };

  const after = async (next) => {
    report = next;
    showReport();
    await load();
  };

  // -- this device, its peers and pairing ---------------------------------------------------------------------
  const devicesSection = (devices) => {
    const cards = [deviceCard(devices), ...devices.peers.map(peerCard)];
    const canPair = devices.device && ctx.has("media.pair");
    let form = null;
    if (canPair) {
      form = pairForm(devices);
      form.hidden = !pairing;
      const toggle = el("button", { type: "button", class: "g-button g-dashed sync-pair", "aria-expanded": String(pairing),
        "data-focus-key": "sync:pair", onclick: () => {
          pairing = !pairing;
          form.hidden = !pairing;
          toggle.setAttribute("aria-expanded", String(pairing));
          toggle.textContent = pairing ? "Close pairing" : "Pair a device";
          if (pairing) { const first = form.querySelector("input"); if (first) first.focus(); }
        } }, pairing ? "Close pairing" : "Pair a device");
      cards.push(toggle);
    }
    return el("section", { class: "g-stack", "aria-label": "Devices" },
      el("div", { class: "sync-devices" }, cards),
      !devices.peers.length ? ui.empty("No other device yet.") : null, form);
  };

  const deviceCard = (devices) => {
    const card = el("section", { class: "g-card sync-device", "aria-label": "This device" }, ui.eyebrow("This device"));
    if (devices.device) {
      card.append(el("div", { class: "sync-name" }, devices.device));
    } else if (devices.device_problem === "unnamed" && ctx.has("media.identify")) {
      const name = ui.field("Name this device", { placeholder: "laptop",
        hint: "A device is named once, before it pairs. Use letters, digits and dashes." });
      card.append(el("div", { class: "sync-name" }, "Not named yet"), name.node,
        ui.actions(ui.button("Name it", () => ctx.act(async () => {
          await ctx.api("media.identify", { device: name.value.trim() });
          await after({ kind: "note", text: "This device is named." });
        }), { kind: "primary" })));
    } else {
      card.append(ui.notice("This device's name cannot be read (" + devices.device_problem + "), so it cannot pair or sync.", { kind: "bad" }));
    }
    card.append(el("div", { class: "g-muted sync-small" }, devices.transport.note));
    return card;
  };

  const peerCard = (peer) => {
    const last = peer.last;
    const canSync = peer.address && ctx.has("media.sync");
    return el("section", { class: "g-card sync-device", "aria-label": peer.peer },
      el("div", { class: "g-row g-between" }, ui.eyebrow(peer.paired ? "Paired" : "Not paired"),
        canSync ? syncButton(peer.peer, "Sync now", { kind: "primary", size: "small" }) : null),
      el("div", { class: "sync-name" }, peer.peer),
      el("div", { class: "g-muted sync-small" }, peer.address ? "This device dials it at " + peer.address + "." : "It dials this device."),
      last ? el("div", { class: "sync-small " + (last.status === "incomplete" || last.status === "refused" ? "sync-bad" : "g-muted") },
        "Last sync since the app started: " + lastText(last)) : el("div", { class: "g-muted sync-small" }, "Not synced since the app started."));
  };

  const lastText = (last) => last.status === "conflicts" ? "synced, " + plural(last.conflicts.length, "file") + " in conflict"
    : last.status + (last.code ? " (" + last.code + ")" : "");

  // A button that syncs with one device. While the request runs every such button for that device reads
  // "Syncing…"; the shell keeps the page's buttons disabled meanwhile, so a second tap is not a second sync.
  const syncButton = (peer, label, options) => {
    const button = ui.button(label, () => sync(peer), options);
    button.setAttribute("data-focus-key", "sync:run:" + peer);
    if (!syncButtons.has(peer)) syncButtons.set(peer, []);
    syncButtons.get(peer).push([button, label]);
    return button;
  };

  const sync = (peer) => ctx.act(async () => {
    const shown = syncButtons.get(peer) || [];
    for (const [button] of shown) button.textContent = "Syncing…";
    let result;
    try {
      result = await ctx.api("media.sync", { peer });
    } catch (error) {
      if (error && error.reason === "left") throw error;
      // refused before anything was exchanged (no address, not paired, a key that does not match)
      for (const [button, label] of shown) button.textContent = label;
      await after({ kind: "sync", peer, error });
      return;
    }
    try { await ctx.refreshLibrary(); } finally { await after({ kind: "sync", peer, result }); }
  });

  const pairForm = (devices) => {
    const peer = ui.field("Other device's name", { placeholder: "phone" });
    const key = ui.field("Pairing key (optional)", { hint: "Leave it empty to make a new key here, then type that key on the other device." });
    const address = ui.field("Its address, if this device dials it (optional)", { placeholder: "127.0.0.1:8765",
      hint: "A loopback address forwarded to the other device (adb reverse or an SSH tunnel). Kept while the app runs." });
    for (const field of [key, address]) field.node.append(field.node.children[1]);  // each hint reads under its input
    const pair = async (replace) => {
      const name = peer.value.trim();
      if (replace && !(await ui.confirm("Replace " + name + "'s pairing key? Its old key stops working at once, so it cannot sync here until it has the new one. Its conflicts stay.",
        { confirmLabel: "Replace the key", danger: true }))) return;
      const result = await ctx.api("media.pair", { peer: name, key: key.value.trim() || null,
        address: address.value.trim() || null, replace });
      key.clear();
      shownKey = result.key ? { peer: result.peer, key: result.key } : null;
      pairing = false;
      await after({ kind: "note", text: "Paired with " + result.peer + "." });
    };
    const known = new Set(devices.peers.filter((item) => item.paired).map((item) => item.peer));
    return el("section", { class: "g-card sync-pairing", "aria-label": "Pair a device" },
      el("h2", { class: "g-heading" }, "Pair a device"),
      el("div", { class: "sync-fields" }, peer.node, key.node, address.node),
      ui.actions(ui.button("Pair", () => ctx.act(() => pair(false)), { kind: "primary" }),
        known.size ? ui.button("Pair again, replacing its key", () => ctx.act(() => pair(true)), { kind: "danger" }) : null));
  };

  // -- what sync left waiting ----------------------------------------------------------------------------------
  // One card: how many conflicts and forks wait, and the way to the inbox, where they are decided.
  const waitingCard = (feed) => {
    if (feed.error) {
      return el("section", { class: "g-card sync-waiting", "aria-label": "Waiting from sync" },
        el("div", { class: "g-grow sync-waiting-text" }, el("h2", { class: "g-heading" }, "What sync left waiting could not be read"),
          el("div", { class: "sync-small sync-bad" }, ui.errorText(feed.error))),
        el("a", { class: "g-button", href: itemHref(ctx) }, "Open Needs you"));
    }
    const waiting = fromSync(feed).length;
    const said = waiting ? plural(waiting, "thing") + " from sync " + (waiting === 1 ? "is" : "are") + " waiting" : "Nothing from sync is waiting";
    return el("section", { class: "g-card sync-waiting", "aria-label": "Waiting from sync" },
      el("div", { class: "g-grow sync-waiting-text" }, el("h2", { class: "g-heading" }, said),
        el("div", { class: "g-muted sync-waiting-sub" }, "Conflicts and forks are decided in one place, alongside everything else that needs you."),
        feedNotes(feed).map((note) => el("div", { class: "sync-small " + (note.bad ? "sync-bad" : "g-muted") }, note.text))),
      waiting ? el("a", { class: "g-button g-primary", href: itemHref(ctx), "data-focus-key": "sync:decide" }, "Decide in Needs you") : null);
  };

  // -- what the last action did -------------------------------------------------------------------------------
  const syncNotice = ({ peer, result, error }) => {
    const failed = (title, detail) => el("div", { class: "g-notice g-bad sync-notice", role: "alert" },
      el("div", { class: "g-grow" }, el("div", { class: "sync-notice-title" }, title), el("div", { class: "sync-notice-text" }, detail)),
      ctx.has("media.sync") ? syncButton(peer, "Try again", { kind: "danger" }) : null);
    if (error) {
      // the app itself did not answer: whether the transfer ran is not known, so nothing is claimed about it
      if (error.reason === "network" || error.reason === "bad_response") {
        return failed("The app did not answer", ui.errorText(error).replace(/[.\s]*$/, "") + ". Whether the sync with " + peer + " ran is not known: sync again to be sure.");
      }
      return failed("Sync with " + peer + " did not start", ui.errorText(error).replace(/[.\s]*$/, "") + ". Nothing was changed on either device.");
    }
    if (result.status === "synced" || result.status === "conflicts") {
      const waiting = result.conflicts.length;
      const exchanged = result.copied || result.deleted.length
        ? plural(result.copied, "file") + (result.copied === 1 ? " was" : " were") + " copied"
          + (result.deleted.length ? " and " + result.deleted.length + " deleted" : "")
        : "Both devices already held the same files";
      return el("div", { class: "g-notice g-soft sync-notice", role: "status" },
        el("span", { class: "g-dot " + (waiting ? "" : "g-good") + " sync-notice-dot", "aria-hidden": "true" }),
        el("span", { class: "g-grow" }, "Synced with " + result.peer + " just now. " + exchanged + ", with "
          + (waiting ? plural(waiting, "file") + " in conflict: both versions are kept, to decide in Needs you." : "nothing new to resolve.")));
    }
    const code = result.code ? " (" + result.code + ")" : "";
    return result.code === "unreachable"
      ? failed("Couldn't reach " + result.peer, "Nothing answered at its address. Nothing was changed on either device. "
        + "Check that Grimoire is open on the other device, then try again.")
      : failed("Sync with " + result.peer + " did not finish", "It ended as " + result.status + code
        + ". Nothing here was deleted; play on and sync later.");
  };

  const reportView = () => {
    const parts = [];
    if (shownKey) {
      parts.push(el("section", { class: "g-card g-accent sync-keycard", "aria-label": "Pairing key" },
        el("h2", { class: "g-heading" }, "Type this key on " + shownKey.peer),
        el("div", { class: "sync-key" }, shownKey.key),
        el("div", { class: "g-muted" }, "It is shown once. Anyone with it can sync with this device until it is replaced."),
        el("div", { class: "g-row" }, ui.button("I have typed it", () => { shownKey = null; return ctx.act(load); }))));
    }
    if (!report) return parts.length ? el("div", { class: "g-stack" }, parts) : null;
    if (report.kind === "note") parts.push(ui.notice(report.text, { kind: "soft" }));
    if (report.kind === "sync") parts.push(syncNotice(report));
    return el("div", { class: "g-stack" }, parts);
  };

  await load();
}
