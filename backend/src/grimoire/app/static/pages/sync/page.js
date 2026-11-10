// Sync (Sprint 114): this device, the devices it pairs with, sync, keep-both conflicts and forks, over the media
// adapter's ops (docs/MEDIA_SYNC_UI.md).
//
// A conflict shows this device's version and every other device's side of it (its kept version, or that it
// deleted the file) side by side; keeping one is confirmed first, and the report after it lists every version
// that was not kept and what other devices still hold. A fork waits for the owner's choice, and the report lists
// the turns it set aside. Sync is loopback only, unencrypted and never in the background; the page says so, and
// lists what the app does not offer yet instead of showing buttons that fail. Everything from the library or
// from another device is set as text only.

const UNAVAILABLE = { unpair: "Unpairing", background_sync: "Background sync",
  resolve_source_conflict: "Resolving a source's conflict" };

export async function mount(root, ctx) {
  ctx.styles("page.css");
  ctx.setTitle("Sync");
  const { ui } = ctx;
  const { el } = ui;
  let report = null;  // what the last action did, shown above everything until the next one
  let shownKey = null;  // a key just drawn for a pairing: shown once, to type on the other device

  const load = async () => {
    const [devices, conflicts, forks] = await Promise.all([
      ctx.api("media.devices", {}), ctx.api("media.conflicts", {}), ctx.api("media.forks", {})]);
    root.replaceChildren(...[
      reportView(),
      deviceSection(devices),
      peersSection(devices),
      conflictsSection(devices, conflicts),
      forksSection(forks),
      ui.section("Not in this app yet", ...devices.unavailable.map((item) =>
        ui.unavailable(UNAVAILABLE[item.action] || item.action.replace(/_/g, " "), item.reason)))].filter(Boolean));
  };

  const after = async (next) => {
    report = next;
    await load();
  };

  // -- this device and its peers ------------------------------------------------------------------------------
  const deviceSection = (devices) => {
    if (devices.device) {
      return ui.section("This device", el("p", {}, "Named ", el("strong", {}, devices.device), "."),
        el("p", { class: "g-muted" }, devices.transport.note));
    }
    const children = [el("p", { class: "g-muted" }, devices.transport.note)];
    if (devices.device_problem === "unnamed" && ctx.has("media.identify")) {
      const name = ui.field("Name this device", { placeholder: "laptop",
        hint: "A device is named once, before it pairs. Use letters, digits and dashes." });
      children.unshift(name.node, ui.actions(ui.button("Name it", () => ctx.act(async () => {
        await ctx.api("media.identify", { device: name.value.trim() });
        await after({ kind: "note", text: "This device is named." });
      }), { kind: "primary" })));
    } else {
      children.unshift(ui.notice("This device's name cannot be read (" + devices.device_problem + "), so it cannot pair or sync.", { kind: "bad" }));
    }
    return ui.section("This device", ...children);
  };

  const peersSection = (devices) => {
    const rows = devices.peers.map((peer) => {
      const last = peer.last;
      const row = el("div", { class: "g-section" },
        el("strong", {}, peer.peer), " ", el("span", { class: "g-muted" }, peer.paired ? "paired" : "not paired"),
        el("div", { class: "g-muted" }, peer.address ? "This device dials it at " + peer.address + "." : "It dials this device."),
        last ? el("div", { class: last.status === "synced" ? "g-muted" : "sync-bad" },
          "Last sync since the app started: " + last.status + (last.code ? " (" + last.code + ")" : "")) : null);
      if (peer.address && ctx.has("media.sync")) {
        row.append(ui.actions(ui.button("Sync now", () => ctx.act(async () => {
          const result = await ctx.api("media.sync", { peer: peer.peer });
          await ctx.refreshLibrary();
          await after({ kind: "sync", result });
        }))));
      }
      return row;
    });
    const children = [rows.length ? el("div", {}, rows) : ui.empty("No other device yet.")];
    if (devices.device && ctx.has("media.pair")) children.push(pairForm(devices));
    return ui.section("Devices", ...children);
  };

  const pairForm = (devices) => {
    const peer = ui.field("Other device's name", { placeholder: "phone" });
    const key = ui.field("Pairing key (optional)", { hint: "Leave it empty to make a new key here, then type that key on the other device." });
    const address = ui.field("Its address, if this device dials it (optional)", { placeholder: "127.0.0.1:8765",
      hint: "A loopback address forwarded to the other device (adb reverse or an SSH tunnel). Kept while the app runs." });
    const pair = async (replace) => {
      const name = peer.value.trim();
      if (replace && !(await ui.confirm("Replace " + name + "'s pairing key? Its old key stops working at once, so it cannot sync here until it has the new one. Its conflicts stay.",
        { confirmLabel: "Replace the key", danger: true }))) return;
      const result = await ctx.api("media.pair", { peer: name, key: key.value.trim() || null,
        address: address.value.trim() || null, replace });
      key.clear();
      shownKey = result.key ? { peer: result.peer, key: result.key } : null;
      await after({ kind: "note", text: "Paired with " + result.peer + "." });
    };
    const known = new Set(devices.peers.filter((item) => item.paired).map((item) => item.peer));
    return el("details", {}, el("summary", {}, "Pair a device"), peer.node, key.node, address.node,
      ui.actions(ui.button("Pair", () => ctx.act(() => pair(false)), { kind: "primary" }),
        known.size ? ui.button("Pair again, replacing its key", () => ctx.act(() => pair(true)), { kind: "danger" }) : null));
  };

  // -- keep-both conflicts ------------------------------------------------------------------------------------
  const conflictsSection = (devices, listed) => {
    const children = [];
    if (listed.unknown.length) {
      children.push(ui.notice("This device's sync records for " + listed.unknown.join(", ")
        + " cannot be read in full, so any of their files may be in conflict and not all can be listed.", { kind: "bad" }));
    }
    children.push(listed.conflicts.length ? el("div", {}, listed.conflicts.map((item) => conflictCard(devices, item)))
      : ui.empty("Nothing is in conflict."));
    return ui.section("Conflicts", ...children);
  };

  const conflictCard = (devices, item) => {
    const what = item.kind === "placement" ? "the " + item.slot + " picture of " + item.subject
      : item.kind === "turn" ? "a turn of a chat" : "a " + item.kind + " record";
    const versions = [version("This device", item.mine)];
    for (const theirs of item.theirs) versions.push(version((theirs.peers || []).join(", ") || "Another device", theirs));
    for (const side of item.peers) if (side.deleted) versions.push(version(side.peer, { deleted: true }));
    const card = ui.section(capital(what), el("div", { class: "g-muted sync-path" }, item.path),
      el("div", { class: "sync-versions" }, versions));
    if (!ctx.has("media.resolve")) {
      card.append(ui.unavailable("Resolving here", "This app does not serve media.resolve."));
      return card;
    }
    if (!item.peers.length) {
      card.append(el("p", { class: "g-muted" }, "Which device this is with cannot be read here, so it cannot be resolved yet."));
      return card;
    }
    const dialed = new Set(devices.peers.filter((peer) => peer.paired && peer.address).map((peer) => peer.peer));
    for (const side of item.peers) {
      const row = el("div", { class: "g-section" }, el("strong", {}, "With " + side.peer));
      if (!dialed.has(side.peer)) {
        row.append(el("p", { class: "g-muted" }, "Both devices must be reachable: give " + side.peer + "'s address under Pair a device to resolve it here."));
      } else {
        // with several other versions standing, the text API keeps this device's only on the other device
        const buttons = item.theirs.length > 1 ? [] : [ui.button(item.mine.deleted ? "Keep this device's deletion" : "Keep this device's", () =>
          resolve(item, side, "mine", "this device's " + (item.mine.deleted ? "deletion" : "version")))];
        for (const tag of side.versions) {
          buttons.push(ui.button("Keep " + side.peer + "'s", () => resolve(item, side, tag, side.peer + "'s version")));
        }
        if (side.deleted && !item.theirs.length) {
          buttons.push(ui.button("Accept " + side.peer + "'s deletion", () => resolve(item, side, "theirs", side.peer + "'s deletion"), { kind: "danger" }));
        }
        if (buttons.length) row.append(ui.actions(...buttons));
        if (item.theirs.length > 1) {
          row.append(el("p", { class: "g-muted" }, "Several other versions stand, so this device's can be kept only on "
            + side.peer + ". Keeping one of theirs here lists this device's after, with the rest."));
        }
        if (side.deleted && item.theirs.length) {
          row.append(el("p", { class: "g-muted" }, side.peer + " deleted it. Resolve the other devices' versions first; then its deletion can be kept or set aside."));
        }
      }
      card.append(row);
    }
    card.append(el("p", { class: "g-muted" }, "Whatever is not kept is listed after resolving."));
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
      const card = ui.section("A chat went two ways",
        ctx.registered("chat") ? el("p", {}, ui.link("Open the chat", "chat", [item.chat])) : el("p", { class: "g-muted sync-path" }, item.chat),
        el("p", { class: "g-muted" }, "Both devices played on from turn " + item.fork.after + ". Choose the branch that goes on; the other's turns are set aside and heard by no one."));
      for (const branch of item.fork.branches) {
        const turns = branch.turns.length + " turn" + (branch.turns.length === 1 ? "" : "s");
        const from = branch.device || "this device";
        card.append(el("div", { class: "g-section" }, el("strong", {}, "From " + from + ", " + turns),
          branch.set_aside ? el("div", { class: "g-muted" }, "Set aside") :
            ctx.has("media.choose") ? ui.actions(ui.button("Go on with this branch", () => ctx.act(async () => {
              if (!(await ui.confirm("Go on with the branch from " + from + "? The other branch's turns are set aside: no one hears them.",
                { confirmLabel: "Go on with it" }))) return;
              const result = await ctx.api("media.choose", { chat: item.chat, turn: branch.head });
              await after({ kind: "choose", result });
            }))) : null));
      }
      if (item.set_aside.length) card.append(el("p", { class: "g-muted" }, item.set_aside.length + " turn"
        + (item.set_aside.length === 1 ? " was" : "s were") + " set aside by an earlier choice."));
      return card;
    });
    const children = [cards.length ? el("div", {}, cards) : ui.empty("No chat is waiting on a choice.")];
    if (listed.more) children.push(el("p", { class: "g-muted" }, "Only the most recent chats are checked."));
    return ui.section("Forks", ...children);
  };

  // -- what the last action did -------------------------------------------------------------------------------
  const reportView = () => {
    const parts = [];
    if (shownKey) {
      parts.push(ui.section("Type this key on " + shownKey.peer,
        el("p", { class: "sync-key" }, shownKey.key),
        el("p", { class: "g-muted" }, "It is shown once. Anyone with it can sync with this device until it is replaced."),
        ui.actions(ui.button("I have typed it", () => { shownKey = null; return ctx.act(load); }))));
    }
    if (!report) return parts.length ? el("div", {}, parts) : null;
    if (report.kind === "note") parts.push(ui.notice(report.text));
    if (report.kind === "sync") {
      const result = report.result;
      const done = result.status === "synced";
      parts.push(ui.notice("Sync with " + result.peer + ": " + result.status + (result.code ? " (" + result.code + ")" : "")
        + (result.conflicts.length ? ". " + result.conflicts.length + " file" + (result.conflicts.length === 1 ? " is" : "s are") + " in conflict: see below." : ".")
        + (done ? "" : " Nothing here was deleted; play on and sync later."), { kind: done ? "" : "bad" }));
    }
    if (report.kind === "resolve") {
      const result = report.result;
      if (result.status !== "resolved") {
        parts.push(ui.notice("Not resolved: " + result.status + (result.reason ? " (" + result.reason + ")" : "") + ". Nothing changed.", { kind: "bad" }));
      } else {
        const lost = result.not_kept.map((item) => version(item.holder === "this device" ? "This device" : item.holder, item));
        const left = result.still_open
          ? [...result.still_open.theirs.map((item) => version((item.peers || []).join(", "), item)),
            ...result.still_open.peers.filter((side) => side.deleted).map((side) => version(side.peer, { deleted: true }))] : [];
        parts.push(ui.section("Resolved " + report.path,
          lost.length ? [el("p", {}, "Not kept:"), el("div", { class: "sync-versions" }, lost)] : el("p", { class: "g-muted" }, "Nothing was set aside."),
          left.length ? [el("p", {}, "Still in conflict with other devices:"), el("div", { class: "sync-versions" }, left)] : null));
      }
    }
    if (report.kind === "choose") {
      const result = report.result;
      parts.push(ui.section("Branch chosen", result.set_aside.length
        ? [el("p", {}, "Set aside, heard by no one:"), ui.list(result.set_aside.map((turn) =>
          ui.rowText("Turn " + turn.number, (turn.device ? "played on " + turn.device + " · " : "") + turn.turn)))]
        : el("p", { class: "g-muted" }, "No turn was set aside."),
      result.waiting.length ? el("p", { class: "g-muted" }, "The chat still waits: " + result.waiting.join(", ") + ".") : null));
    }
    return el("div", { role: "status" }, parts);
  };

  await load();
}

function capital(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
