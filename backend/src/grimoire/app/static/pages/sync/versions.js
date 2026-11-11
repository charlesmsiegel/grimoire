// What a keep-both conflict and a fork choice look like (114): the versions of a file side by side, and, after a
// decision, everything it set aside. The inbox (pages/inbox) shows these where a conflict or a fork is decided;
// nothing here decides anything. A turn is shown only as the table may read it, exactly as media.conflicts
// answered it; a version this build cannot read is still a version. Everything from the library or from another
// device is set as text only.
//
// Its classes are in versions.css (all "sync-"), which it links itself, so a page outside this folder needs
// nothing else.

const STYLES = "/static/pages/sync/versions.css";

function styles() {
  if (!document.head.querySelector('link[data-page-styles="' + CSS.escape(STYLES) + '"]')) {
    const link = document.createElement("link");
    link.setAttribute("rel", "stylesheet");
    link.setAttribute("href", STYLES);
    link.setAttribute("data-page-styles", STYLES);
    document.head.append(link);
  }
}

// One version: who holds it, and what it holds.
function version(ctx, holder, candidate) {
  const { el } = ctx.ui;
  return el("div", { class: "sync-version" }, el("div", { class: "sync-holder" }, holder), preview(ctx, candidate));
}

function preview(ctx, candidate) {
  const { el } = ctx.ui;
  if (candidate.deleted) return el("div", { class: "g-muted" }, "Deleted");
  const seen = candidate.preview;
  if (!seen) return el("div", { class: "g-muted" }, candidate.unreadable ? "Its bytes cannot be read here" : "Not shown");
  if (seen.unreadable) return el("div", { class: "g-muted" }, "This build cannot read it (" + seen.unreadable + ")");
  if (seen.placement) {
    const placement = seen.placement;
    const node = el("div", {}, placement.mode + (placement.crop ? ", cropped" : ""));
    if (placement.image) node.append(picture(ctx, placement.image, "The picture in this version"));
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
}

function picture(ctx, ref, label) {
  const { el } = ctx.ui;
  const frame = el("div", { class: "g-muted", role: "img", "aria-label": label }, "Loading…");
  ctx.picture(ref).then((url) => {
    if (url) frame.replaceWith(el("img", { class: "sync-thumb", src: url, alt: label }));
    else frame.textContent = "This picture cannot be shown here.";
  }, () => { /* the page was left */ });
  return frame;
}

// A conflict as `media.conflicts` lists it: this device's version and every other device's side of it (its kept
// version, or that it deleted the file), side by side, with the file's path.
export function conflictVersions(ctx, conflict) {
  styles();
  const { el } = ctx.ui;
  const versions = [version(ctx, "This device", conflict.mine)];
  for (const theirs of conflict.theirs) versions.push(version(ctx, (theirs.peers || []).join(", ") || "Another device", theirs));
  for (const side of conflict.peers) if (side.deleted) versions.push(version(ctx, side.peer, { deleted: true }));
  return el("div", { class: "g-stack sync-conflict", "aria-label": "The versions kept" },
    el("div", { class: "sync-versions" }, versions), el("div", { class: "g-muted sync-path" }, conflict.path));
}

// What `media.resolve` answered: every version that was not kept, and what other devices still hold in conflict
// on that file. A resolve that did not go through says so, and that nothing changed.
export function resolvedReport(ctx, path, result) {
  styles();
  const { ui } = ctx;
  const { el } = ui;
  if (result.status !== "resolved") {
    return ui.notice("Not resolved: " + result.status + (result.reason ? " (" + result.reason + ")" : "") + ". Nothing changed.", { kind: "bad" });
  }
  const lost = result.not_kept.map((item) => version(ctx, item.holder === "this device" ? "This device" : item.holder, item));
  const left = result.still_open
    ? [...result.still_open.theirs.map((item) => version(ctx, (item.peers || []).join(", "), item)),
      ...result.still_open.peers.filter((side) => side.deleted).map((side) => version(ctx, side.peer, { deleted: true }))] : [];
  return el("section", { class: "g-card g-stack sync-resolved", "aria-label": "Resolved" },
    el("h3", { class: "g-heading" }, "Resolved ", el("span", { class: "sync-path" }, path)),
    lost.length ? [el("div", {}, "Not kept:"), el("div", { class: "sync-versions" }, lost)] : el("div", { class: "g-muted" }, "Nothing was set aside."),
    left.length ? [el("div", {}, "Still in conflict with other devices:"), el("div", { class: "sync-versions" }, left)] : null);
}

// What `media.choose` answered: the turns the choice set aside (heard by no one), and whether the chat still waits.
export function chosenReport(ctx, result) {
  styles();
  const { ui } = ctx;
  const { el } = ui;
  return el("section", { class: "g-card g-stack sync-resolved", "aria-label": "Branch chosen" },
    el("h3", { class: "g-heading" }, "Branch chosen"),
    result.set_aside.length
      ? [el("div", {}, "Set aside, heard by no one:"), ui.card({ kind: "flush" }, el("div", { class: "g-rows" }, result.set_aside.map((turn) =>
        el("div", {}, el("span", { class: "sync-holder" }, "Turn " + turn.number),
          el("span", { class: "g-muted sync-path" }, " " + (turn.device ? "played on " + turn.device + " · " : "") + turn.turn)))))]
      : el("div", { class: "g-muted" }, "No turn was set aside."),
    result.waiting.length ? el("div", { class: "g-muted" }, "The chat still waits: " + result.waiting.join(", ") + ".") : null);
}
