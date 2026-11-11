// Devices and sync (114): this device, the devices it pairs with, sync, keep-both conflicts and forks, over the
// media adapter's ops (docs/MEDIA_SYNC_UI.md).
//
// The top of the page is a card for this device, one for each other device (how it is reached, its last sync,
// "Sync now") and a dashed "Pair a device" that opens the pairing form. A sync says "Syncing…" for as long as
// the request takes, then a plain notice: what was exchanged, or what went wrong with "Try again".
//
// A conflict shows this device's version and every other device's side of it (its kept version, or that it
// deleted the file) side by side; keeping one is confirmed first, and the report after it lists every version
// that was not kept and what other devices still hold. A fork waits for the owner's choice, and the report lists
// the turns it set aside. Sync is loopback only, unencrypted and never in the background; the page says so, and
// lists what the app does not offer yet instead of showing buttons that fail. Everything from the library or
// from another device is set as text only.

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
    const [devices, conflicts, forks] = await Promise.all([
      ctx.api("media.devices", {}), ctx.api("media.conflicts", {}), ctx.api("media.forks", {})]);
    syncButtons = new Map();
    showReport();
    page.replaceChildren(...[
      reportBox,
      devicesSection(devices),
      conflictsSection(devices, conflicts),
      forksSection(forks),
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

  // -- keep-both conflicts ------------------------------------------------------------------------------------
  const conflictsSection = (devices, listed) => {
    const children = [el("h2", { class: "g-heading" }, "Conflicts")];
    if (listed.unknown.length) {
      children.push(ui.notice("This device's sync records for " + listed.unknown.join(", ")
        + " cannot be read in full, so any of their files may be in conflict and not all can be listed.", { kind: "bad" }));
    }
    children.push(listed.conflicts.length ? listed.conflicts.map((item) => conflictCard(devices, item))
      : ui.empty("Nothing is in conflict."));
    return el("section", { class: "g-stack", "aria-label": "Conflicts" }, ...children);
  };

  const conflictCard = (devices, item) => {
    const what = item.kind === "placement" ? "the " + item.slot + " picture of " + item.subject
      : item.kind === "turn" ? "a turn of a chat" : "a " + item.kind + " record";
    const versions = [version("This device", item.mine)];
    for (const theirs of item.theirs) versions.push(version((theirs.peers || []).join(", ") || "Another device", theirs));
    for (const side of item.peers) if (side.deleted) versions.push(version(side.peer, { deleted: true }));
    const body = el("div", { class: "g-stack sync-card-body" }, el("div", { class: "sync-versions" }, versions));
    const card = el("article", { class: "g-card g-flush sync-conflict", "aria-label": item.path },
      el("div", { class: "g-card-head" }, el("div", { class: "g-grow" }, el("div", { class: "g-title" }, capital(what)),
        el("div", { class: "g-muted sync-path" }, item.path)), ui.pill("Both kept", { kind: "bad" })), body);
    if (!ctx.has("media.resolve")) {
      body.append(ui.unavailable("Resolving here", "This app does not serve media.resolve."));
      return card;
    }
    if (!item.peers.length) {
      body.append(el("div", { class: "g-muted" }, "Which device this is with cannot be read here, so it cannot be resolved yet."));
      return card;
    }
    const dialed = new Set(devices.peers.filter((peer) => peer.paired && peer.address).map((peer) => peer.peer));
    for (const side of item.peers) {
      const row = el("div", { class: "sync-side", "data-peer": side.peer }, el("strong", {}, "With " + side.peer));
      if (!dialed.has(side.peer)) {
        row.append(el("div", { class: "g-muted" }, "Both devices must be reachable: give " + side.peer + "'s address under Pair a device to resolve it here."));
      } else {
        // with several other versions standing, the text API keeps this device's only on the other device; and a
        // deleting device's side waits until the others' versions are resolved (it is refused as stale till then)
        const waits = side.deleted && item.theirs.length > 0;
        const buttons = item.theirs.length > 1 || waits ? [] : [ui.button(item.mine.deleted ? "Keep this device's deletion" : "Keep this device's", () =>
          resolve(item, side, "mine", "this device's " + (item.mine.deleted ? "deletion" : "version")), { size: "small" })];
        for (const tag of side.versions) {
          buttons.push(ui.button("Keep " + side.peer + "'s", () => resolve(item, side, tag, side.peer + "'s version"), { size: "small" }));
        }
        if (side.deleted && !item.theirs.length) {
          buttons.push(ui.button("Accept " + side.peer + "'s deletion", () => resolve(item, side, "theirs", side.peer + "'s deletion"), { kind: "danger", size: "small" }));
        }
        if (buttons.length) row.append(el("div", { class: "g-row" }, buttons));
        if (item.theirs.length > 1 && !waits) {
          row.append(el("div", { class: "g-muted" }, "Several other versions stand, so this device's can be kept only on "
            + side.peer + ". Keeping one of theirs here lists this device's after, with the rest."));
        }
        if (side.deleted && item.theirs.length) {
          row.append(el("div", { class: "g-muted" }, side.peer + " deleted it. Resolve the other devices' versions first; then its deletion can be kept or set aside."));
        }
      }
      body.append(row);
    }
    body.append(el("div", { class: "g-muted sync-small" }, "Whatever is not kept is listed after resolving."));
    return card;
  };

  const resolve = (item, side, keep, label) => ctx.act(async () => {
    if (!(await ui.confirm("Keep " + label + " of " + item.path + "? The rest of this conflict with " + side.peer
      + " is set aside here and on " + side.peer + ", and listed after.", { confirmLabel: "Keep it" }))) return;
    const result = await ctx.api("media.resolve", { peer: side.peer, path: item.path, keep,
      mine: item.mine.etag, theirs: item.theirs.map((theirs) => theirs.etag) });
    await after({ kind: "resolve", path: item.path, result });
  });

  const version = (holder, candidate) => el("div", { class: "sync-version" },
    el("div", { class: "sync-holder" }, holder), preview(candidate));

  const preview = (candidate) => {
    if (candidate.deleted) return el("div", { class: "g-muted" }, "Deleted");
    const seen = candidate.preview;
    if (!seen) return el("div", { class: "g-muted" }, candidate.unreadable ? "Its bytes cannot be read here" : "Not shown");
    if (seen.unreadable) return el("div", { class: "g-muted" }, "This build cannot read it (" + seen.unreadable + ")");
    if (seen.placement) {
      const placement = seen.placement;
      const node = el("div", {}, placement.mode + (placement.crop ? ", cropped" : ""));
      if (placement.image) node.append(picture(placement.image, "The picture in this version"));
      return node;
    }
    if (seen.turn) {
      const turn = seen.turn;
      const head = el("div", { class: "g-muted" }, "Turn " + turn.number + (turn.device ? ", played on " + turn.device : ""));
      if (seen.set_aside !== undefined || !("post" in turn)) {
        return el("div", {}, head, el("div", { class: "g-muted" }, seen.set_aside === null
          ? "Its chat cannot be opened to tell whether anyone heard it, so only its number is shown."
          : "Set aside by a fork choice: heard by no one."));
      }
      const take = turn.kept || (turn.offered || [])[0];
      return el("div", {}, head,
        turn.post ? el("div", { class: "sync-line" }, el("span", { class: "sync-speaker" }, turn.post.speaker + ": "), turn.post.text || "") : null,
        take ? take.lines.map((line) => el("div", { class: "sync-line" },
          el("span", { class: "sync-speaker" }, (line.speaker || "Narrator") + ": "), line.text || ""))
          : el("div", { class: "g-muted" }, turn.passed ? "Passed" : "No take kept"));
    }
    return el("div", { class: "g-muted" }, "A " + (seen.kind || "record") + " (" + (candidate.size || 0) + " bytes)");
  };

  const picture = (ref, label) => {
    const frame = el("div", { class: "g-muted", role: "img", "aria-label": label }, "Loading…");
    ctx.picture(ref).then((url) => {
      if (url) frame.replaceWith(el("img", { class: "sync-thumb", src: url, alt: label }));
      else frame.textContent = "This picture cannot be shown here.";
    }, () => { /* the page was left */ });
    return frame;
  };

  // -- forks --------------------------------------------------------------------------------------------------
  const forksSection = (listed) => {
    const cards = listed.forks.map((item) => {
      const body = el("div", { class: "g-stack sync-card-body" },
        el("div", { class: "g-muted" }, "Both devices played on from turn " + item.fork.after + ". Choose the branch that goes on; the other's turns are set aside and heard by no one."));
      const card = el("article", { class: "g-card g-flush sync-fork", "aria-label": "A chat went two ways" },
        el("div", { class: "g-card-head" }, el("div", { class: "g-grow" }, el("div", { class: "g-title" }, "A chat went two ways"),
          ctx.registered("chat") ? null : el("div", { class: "g-muted sync-path" }, item.chat)),
          ctx.registered("chat") ? ui.link("Open the chat", "chat", [item.chat]) : null), body);
      const branches = el("div", { class: "sync-versions" });
      for (const branch of item.fork.branches) {
        const from = branch.device || "this device";
        branches.append(el("div", { class: "sync-version" }, el("div", { class: "sync-holder" }, "From " + from + ", " + plural(branch.turns.length, "turn")),
          branch.set_aside ? el("div", { class: "g-muted" }, "Set aside") :
            ctx.has("media.choose") ? el("div", { class: "g-row" }, ui.button("Go on with this branch", () => ctx.act(async () => {
              if (!(await ui.confirm("Go on with the branch from " + from + "? The other branch's turns are set aside: no one hears them.",
                { confirmLabel: "Go on with it" }))) return;
              const result = await ctx.api("media.choose", { chat: item.chat, turn: branch.head });
              await after({ kind: "choose", result });
            }), { size: "small" })) : null));
      }
      body.append(branches);
      if (item.set_aside.length) body.append(el("div", { class: "g-muted" }, plural(item.set_aside.length, "turn")
        + (item.set_aside.length === 1 ? " was" : " were") + " set aside by an earlier choice."));
      return card;
    });
    const children = [el("h2", { class: "g-heading" }, "Forks"), cards.length ? cards : ui.empty("No chat is waiting on a choice.")];
    if (listed.more) children.push(el("div", { class: "g-muted" }, "Only the most recent chats are checked."));
    return el("section", { class: "g-stack", "aria-label": "Forks" }, ...children.flat());
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
          + (waiting ? plural(waiting, "file") + " in conflict: both versions are kept, to decide under Conflicts below." : "nothing new to resolve.")));
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
    if (report.kind === "resolve") {
      const result = report.result;
      if (result.status !== "resolved") {
        parts.push(ui.notice("Not resolved: " + result.status + (result.reason ? " (" + result.reason + ")" : "") + ". Nothing changed.", { kind: "bad" }));
      } else {
        const lost = result.not_kept.map((item) => version(item.holder === "this device" ? "This device" : item.holder, item));
        const left = result.still_open
          ? [...result.still_open.theirs.map((item) => version((item.peers || []).join(", "), item)),
            ...result.still_open.peers.filter((side) => side.deleted).map((side) => version(side.peer, { deleted: true }))] : [];
        parts.push(el("section", { class: "g-card g-stack sync-resolved", "aria-label": "Resolved" },
          el("h2", { class: "g-heading" }, "Resolved ", el("span", { class: "sync-path" }, report.path)),
          lost.length ? [el("div", {}, "Not kept:"), el("div", { class: "sync-versions" }, lost)] : el("div", { class: "g-muted" }, "Nothing was set aside."),
          left.length ? [el("div", {}, "Still in conflict with other devices:"), el("div", { class: "sync-versions" }, left)] : null));
      }
    }
    if (report.kind === "choose") {
      const result = report.result;
      parts.push(el("section", { class: "g-card g-stack sync-resolved", "aria-label": "Branch chosen" },
        el("h2", { class: "g-heading" }, "Branch chosen"),
        result.set_aside.length
          ? [el("div", {}, "Set aside, heard by no one:"), ui.card({ kind: "flush" }, el("div", { class: "g-rows" }, result.set_aside.map((turn) =>
            el("div", {}, el("span", { class: "sync-holder" }, "Turn " + turn.number),
              el("span", { class: "g-muted sync-path" }, (turn.device ? "played on " + turn.device + " · " : "") + turn.turn)))))]
          : el("div", { class: "g-muted" }, "No turn was set aside."),
        result.waiting.length ? el("div", { class: "g-muted" }, "The chat still waits: " + result.waiting.join(", ") + ".") : null));
    }
    return el("div", { class: "g-stack" }, parts);
  };

  await load();
}

function capital(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
