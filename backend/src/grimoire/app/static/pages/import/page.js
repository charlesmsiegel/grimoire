// Sprint 110: import, migration and change review, over the `imports.*` adapter ops (docs/REVIEW_UI.md).
//
// Every write is a proposal: a preview op plans it and answers every file it would write (exact size, sha256,
// text and diff where readable) and a digest over those bytes. Approve sends that digest back with
// `imports.proposal_apply`; the server recomputes it from the plan before the domain service commits, so the bytes
// approved are the bytes written. Reject ends the proposal and writes nothing. Nothing on this page writes any
// other way, and a V1 library is only ever read.
//
// The page is three parts: the kinds across the top (the address keeps the chosen one, `?tab=`), that kind's
// form (`stage`), and what the form found or proposes (`out`), which ends in the shared review. A kind is added
// by a row in KINDS and a form in FORMS. The card form lives in card.js.
//
// Every value shown comes from a library, card or export and is only ever set as text, never as markup.

import { cardForm } from "./card.js";

const MAX_FILE = 34 * 1024 * 1024;  // the app takes a request of 48 MiB, base64 included
const HISTORY_ACCEPT = ".jsonl,application/jsonl,text/plain";

// each kind: its key (the `tab` of the address), its name, a line about it, and every op its form calls. A kind
// is shown only when the app serves all of them
const KINDS = [
  ["card", "Character card", "A PNG card, a card JSON, a scenario card or a lorebook", ["imports.card_inspect", "imports.card_preview"]],
  ["history", "Chat history", "A SillyTavern chat export", ["imports.history_scan", "imports.history_preview"]],
  ["v1", "V1 library", "Migrate a Grimoire 0.2.1 library", ["imports.v1_scan", "imports.v1_preview", "imports.v1_receipts"]],
  ["changeset", "Change set", "Review a proposed set of edits", ["imports.changeset_review"]],
  ["rebase", "Source update", "Move a chat to a newer revision", ["imports.rebase_candidates", "imports.rebase_review", "imports.rebase_preview"]],
];

const ACTIONS = { create: "New file", replace: "Replaces a file", unchanged: "Unchanged", write: "Written" };
// what a review is called at each state, and what its footer says once it is decided
const STATES = { open: "Review before anything is written", applied: "Written", rejected: "Rejected", ended: "Not written",
  unknown: "Check whether this was written" };
const OUTCOMES = { applied: "Written to your library", rejected: "Rejected: nothing was written",
  ended: "Not written: preview it again", unknown: "This may already have been written: check the library before you preview it again" };

async function bytesOf(file) {
  if (file.size > MAX_FILE) {
    throw Object.assign(new Error((file.name || "The file") + " is larger than this app takes in one request"), { reason: "too_large" });
  }
  return new Uint8Array(await file.arrayBuffer());
}

function base64(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

function shown(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function sizeOf(bytes) {
  return bytes < 1024 ? bytes + " bytes" : bytes < 1024 * 1024 ? (bytes / 1024).toFixed(1) + " KB" : (bytes / 1024 / 1024).toFixed(1) + " MB";
}

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle("Import");
  const capabilities = await ctx.api("imports.capabilities", {});
  const offered = Array.isArray(capabilities.ops) ? new Set(capabilities.ops) : null;
  const served = (ops) => ops.every((op) => ctx.has(op) && (!offered || offered.has(op)));
  const usable = KINDS.filter(([, , , ops]) => served(ops));
  const tab = usable.some(([key]) => key === ctx.query.get("tab")) ? ctx.query.get("tab") : usable.length ? usable[0][0] : null;

  const facts = (pairs) => el("dl", { class: "import-facts" },
    pairs.filter(([, value]) => value !== undefined && value !== null && value !== "")
      .flatMap(([key, value]) => [el("dt", {}, key), el("dd", {}, shown(value))]));
  const rows = (title, found, describe = shown) => {
    if (!found) return null;
    const items = Array.isArray(found) ? found : found.rows || [];
    const total = Array.isArray(found) ? found.length : found.total;
    if (!total) return null;
    return el("details", { class: "import-rows" }, el("summary", {}, title + " (" + total + ")"),
      el("ul", {}, items.map((row) => el("li", {}, describe(row)))),
      found.truncated ? el("p", { class: "g-muted" }, "Showing " + items.length + " of " + total + ".") : null);
  };
  const present = (...nodes) => nodes.flat().filter(Boolean);
  const stage = el("div", {});
  const out = el("div", { class: "g-stack", "aria-live": "polite" });
  const show = (...nodes) => out.replaceChildren(...present(nodes));
  // a step of a kind's own flow (who is speaking, what a V1 library holds): a titled card
  const step = (title, ...children) => ui.card({ tag: "section", label: title }, el("h2", { class: "g-heading import-step-title" }, title), ...present(children));
  // the foot of a form or a step: what happens next, said once, and the one button that does it
  const next = (note, label, work) => el("div", { class: "import-next" }, el("span", { class: "g-muted" }, note),
    ui.button(label, () => ctx.act(work), { kind: "primary" }));
  // pictures shown from a dropped file itself: blob: URLs (the one image source the page's policy allows), made
  // from the file's bytes and let go when the form starts over or the page is left
  const held = new Set();
  const pictures = {
    of(bytes, type) { const url = URL.createObjectURL(new Blob([bytes], { type })); held.add(url); return url; },
    clear() { for (const url of held) URL.revokeObjectURL(url); held.clear(); },
  };
  const startOver = () => {
    pictures.clear();
    show();
    stage.replaceChildren(...present(FORMS[tab]()));
    ctx.scrollTo(0);
    const first = stage.querySelector("button") || stage.querySelector("input") || stage.querySelector("textarea");
    if (first) first.focus();
  };

  // -- the shared review: every file, its exact bytes' hash, and digest-bound Approve / Reject -----------------
  function review(proposal, { title, summary, notes }, after) {
    const panel = el("section", { class: "import-review", "aria-label": "Proposed write" });
    const count = proposal.writes + (proposal.writes === 1 ? " file" : " files");
    let selected = proposal.files.length ? proposal.files[0] : null;

    const fileButtons = proposal.files.map((file) => {
      const action = ACTIONS[file.action] || (String(file.action).startsWith("unreadable") ? "Current file unreadable" : file.action);
      return [file, el("button", { type: "button", class: "import-file", "aria-pressed": String(file === selected),
        onclick: () => { selected = file; fill(); } },
      el("span", { class: "import-path" }, file.path),
      el("span", { class: "import-file-meta" }, el("span", {}, action), el("span", {}, file.size + " bytes")))];
    });
    const pane = el("div", { class: "import-diff" });
    function fill() {
      for (const [file, node] of fileButtons) node.setAttribute("aria-pressed", String(file === selected));
      if (!selected) { pane.replaceChildren(ui.empty("Nothing to write: the library already holds exactly this.")); return; }
      const body = selected.diff !== null && selected.diff !== undefined ? el("pre", { class: "import-pre" }, selected.diff)
        : selected.text !== null && selected.text !== undefined ? el("pre", { class: "import-pre" }, selected.text)
          : el("p", { class: "g-muted" }, selected.binary ? "Binary file: shown by its size and hash only."
            : "Too large to show: shown by its size and hash only.");
      pane.replaceChildren(el("p", { class: "import-hash" }, selected.path + " · sha256 " + selected.sha256), body);
    }

    function render(state, result) {
      const open = state === "open";
      panel.replaceChildren(...present(
        el("div", { class: "import-review-head" },
          el("div", { class: "import-review-what" }, ui.eyebrow(STATES[state] || state, { accent: true }),
            el("h2", { class: "import-review-title" }, title)),
          summary),
        notes && notes.length ? el("div", { class: "import-review-notes" }, notes) : null,
        el("div", { class: "import-review-body" },
          el("div", { class: "import-files" }, el("div", { class: "g-muted import-count" }, count), fileButtons.map(([, node]) => node)),
          pane),
        result ? el("div", { class: "import-result" }, ui.eyebrow("What was written"), facts(Object.entries(result))) : null,
        open ? el("div", { class: "import-review-foot" },
          el("span", { class: "import-digest" }, "Digest " + proposal.digest + " · the bytes approved are the bytes written"),
          ui.button("Reject", () => ctx.act(reject)),
          ui.button(proposal.writes ? "Approve and write " + count : "Approve", () => ctx.act(approve), { kind: "primary" }))
          : el("div", { class: "import-review-foot import-done" },
            el("strong", { class: "import-outcome" }, OUTCOMES[state] || state),
            ui.button("Import another", startOver),
            ctx.registered("sources") ? ui.buttonLink("Open the library", "sources") : null)));
      fill();
    }
    async function approve() {
      if (proposal.writes && !await ui.confirm("Write " + count + " into this library, exactly as shown?",
        { confirmLabel: "Write " + count })) return;
      let done;
      try {
        done = await ctx.api("imports.proposal_apply", { proposal: proposal.proposal, digest: proposal.digest });
      } catch (error) {
        // a stale or conflicting approval ended this proposal before anything was written: preview again
        if (["stale", "stale_approval", "conflict"].includes(error.reason)) {
          render("ended");
          ctx.notify(ui.errorText(error) + " Nothing was written; preview it again.", { bad: true });
          return;
        }
        // the server no longer holds this proposal: an approval whose answer was lost may have written it already
        if (error.reason === "no_proposal") {
          render("unknown");
          ctx.notify("This proposal is no longer held here, so it may already have been written (or the app restarted). "
            + "Check the library before you preview it again.", { bad: true });
          return;
        }
        // any other refusal from the server also ends the proposal, without saying what reached the disk; a denied
        // write, or a request that got no answer, leaves it open to try again
        if (error.status && error.reason !== "denied") render("unknown");
        throw error;
      }
      render("applied", done.result);
      ctx.notify("Written.");
      if (after) await after(done);
    }
    async function reject() {
      await ctx.api("imports.proposal_reject", { proposal: proposal.proposal });
      render("rejected");
      ctx.notify("Rejected: nothing was written.");
    }
    render(proposal.state);
    return panel;
  }

  // -- shared form pieces ------------------------------------------------------------------------------------
  // A large drop zone for one file: drag a file onto it, or choose one. `onfile` gets the file.
  function dropZone({ title, hint, accept, label, onfile }) {
    const input = el("input", { type: "file", accept, hidden: true, "aria-label": label });
    const zone = el("div", { class: "import-drop" },
      el("span", { class: "import-drop-title" }, title), el("span", { class: "g-muted import-drop-hint" }, hint),
      el("div", { class: "import-drop-actions" }, ui.button("Choose a file", () => input.click())), input);
    const take = (file) => { if (file) ctx.act(() => onfile(file)); };
    input.addEventListener("change", () => { const file = input.files && input.files[0]; input.value = ""; take(file); });
    for (const type of ["dragenter", "dragover"]) zone.addEventListener(type, (event) => { event.preventDefault(); zone.classList.add("import-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("import-over"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("import-over");
      if (event.dataTransfer) take(event.dataTransfer.files[0]);
    });
    return zone;
  }
  const check = (label) => {
    const input = el("input", { type: "checkbox" });
    return { input, node: el("label", { class: "import-check" }, input, label) };
  };

  // -- one form per kind -------------------------------------------------------------------------------------
  // the card form is its own module (card.js): it shares this page's pieces
  const cardFormOf = () => cardForm({ ctx, ui, el, capabilities, stage, show, review, facts, rows, present, dropZone, startOver,
    bytesOf, base64, sizeOf, pictures });

  function historyForm() {
    async function scanIt(file) {
      const exported = base64(await bytesOf(file));
      const found = await ctx.api("imports.history_scan", { export: exported });
      const speakers = Array.isArray(found.speakers) ? found.speakers : [];
      const inputs = speakers.map((speaker) => [speaker.id, ui.field(
        (speaker.label || speaker.id) + (speaker.role === "user" ? " (you)" : "") + ", " + speaker.posts + " posts",
        { placeholder: "card:mara-vell", hint: "The character source this speaker is, or leave it empty." })]);
      const label = ui.field("Chat label", { hint: "Optional." });
      async function preview() {
        const map = {};
        for (const [sid, input] of inputs) if (input.value.trim()) map[sid] = input.value.trim();
        const args = { export: exported, speakers: map, name: file.name };
        if (label.value.trim()) args.label = label.value.trim();
        const proposal = await ctx.api("imports.history_preview", args);
        const r = proposal.report;
        show(review(proposal, {
          title: "Chat history from " + (file.name || "an export"),
          summary: facts([["Status", r.status], ["Chat", r.chat], ["Pages", r.pages], ["Counts", r.counts], ["Staged", r.staged],
            ["Accepted events", r.accepted_events.length], ["Character memories", r.memories.length]]),
          notes: present(el("p", { class: "g-muted" }, "Imported lines are kept as transcript only: they become no accepted events and no character memories."),
            rows("Not carried over", r.losses)),
        }, async (done) => { if (ctx.registered("chat") && done.result.chat) ctx.notify("Imported as " + done.result.chat + "."); }));
      }
      show(step("Who is speaking",
        el("div", { class: "import-path" }, file.name || ""),
        facts([["Bytes", found.bytes], ["sha256", found.sha256], ["Counts", found.counts]]),
        inputs.map(([, input]) => input.node), label.node, rows("Not carried over", found.losses),
        next("Nothing is written until you approve the preview.", "Preview import", preview)));
    }
    return [dropZone({ title: "Drop a chat export here", label: "SillyTavern chat export", accept: HISTORY_ACCEPT, onfile: scanIt,
      hint: "A SillyTavern chat export (.jsonl). It is read first, so you can say who each speaker is." })];
  }

  function v1Form() {
    const source = ui.field("Grimoire 0.2.1 library folder", { placeholder: "/path/to/the/v1/library",
      hint: "Only read, never changed. Stop V1 first, or scan a copy." });
    const archive = ui.field("Archive name", { placeholder: "my-v1-library",
      hint: "A short name for this V1 library; the same name reruns an import and writes only what changed." });
    async function scanIt() {
      const found = await ctx.api("imports.v1_scan", { source: source.value.trim(), archive_id: archive.value.trim() });
      const selection = found.proposed_selection;
      const picks = [];
      const pick = (kind, legacy, target) => {
        const box = check(legacy + " becomes " + target);
        box.input.checked = true;
        picks.push([kind, legacy, target, box.input]);
        return box.node;
      };
      async function preview() {
        const chosen = { worlds: {}, campaigns: {}, skip: selection.skip };
        for (const [kind, legacy, target, box] of picks) if (box.checked) chosen[kind][legacy] = target;
        const proposal = await ctx.api("imports.v1_preview", { scan: found.scan, selection: chosen });
        const r = proposal.report;
        show(review(proposal, {
          title: "V1 library " + r.archive_id,
          summary: facts([["Archive", r.archive_id], ["Outcomes", r.outcome_counts], ["Model calls", r.provider_calls]]),
          notes: present(rows("Unresolved", r.unresolved), rows("Not imported", r.not_imported),
            rows("Flagged", r.flags), rows("Not carried over", r.losses)),
        }, async () => {
          const receipts = await ctx.api("imports.v1_receipts", { archive_id: found.archive_id });
          ctx.notify("Import receipt: run " + receipts.runs + ", " + receipts.written + " files written so far.");
        }));
      }
      show(step("What the V1 library holds",
        facts([["Archive", found.archive_id], ["Status", found.status], ["Counts", found.counts],
          ["Pictures", found.images], ["Model calls", found.provider_calls]]),
        el("h3", { class: "import-sub" }, "Import"),
        Object.entries(selection.worlds).map(([legacy, target]) => pick("worlds", legacy, target)),
        Object.entries(selection.campaigns).map(([legacy, target]) => pick("campaigns", legacy, target)),
        rows("Not proposed", selection.unproposed),
        rows("Identities", found.identities, (row) => [row.legacy_id, row.status, row.candidate].filter(Boolean).join(" · ")),
        rows("Findings", found.findings), rows("Unresolved", found.unresolved), rows("Not carried over", found.losses),
        next("Nothing is written until you approve the preview.", "Preview import", preview)));
    }
    return [ui.card({ tag: "section", label: "V1 library" }, source.node, archive.node,
      next("Every write is previewed first, file by file.", "Scan library", scanIt))];
  }

  function changesetForm() {
    const documentField = ui.field("Change set (JSON)", { multiline: true,
      hint: "Proposed change sets are not stored yet: paste the change set document to review it." });
    documentField.input.classList.add("import-document");
    async function reviewIt() {
      let changeset;
      try { changeset = JSON.parse(documentField.value); }
      catch (error) { documentField.setError("This is not JSON."); return; }
      documentField.setError("");
      const proposal = await ctx.api("imports.changeset_review", { changeset });
      const r = proposal.report;
      show(review(proposal, {
        title: r.title || r.changeset,
        summary: facts([["Change set", r.changeset], ["Title", r.title], ["Author", r.author], ["Why", r.rationale],
          ["Can be applied", r.applicable ? "yes" : "no"]]),
        notes: present(rows("Changes", r.changes, (row) => row.action + " " + row.path + (row.summary ? ": " + row.summary : "")),
          rows("Conflicts", r.conflicts), rows("Blocked", r.blocked)),
      }));
    }
    return [ui.card({ tag: "section", label: "Change set" }, documentField.node,
      next("Every write is previewed first, file by file.", "Review change set", reviewIt))];
  }

  function rebaseForm() {
    const chat = ui.field("Chat", { value: ctx.query.get("chat") || "", placeholder: "chat:…",
      hint: "The chat whose characters may follow a newer revision of their source." });
    async function reviewActor(actor) {
      const found = await ctx.api("imports.rebase_review", { actor });
      const choices = [];
      async function preview() {
        const decisions = {};
        for (const [name, select] of choices) decisions[name] = select.value;
        const proposal = await ctx.api("imports.rebase_preview", { review: found.review, decisions });
        show(review(proposal, { title: "Update " + found.actor,
          summary: facts([["From", found.ancestor], ["To", found.upstream], ["Decisions", decisions]]) }));
      }
      show(step("Update " + found.actor,
        facts([["From", found.ancestor], ["To", found.upstream], ["History kept", found.history]]),
        found.rows.map((row) => {
          let control = null;
          if (row.needs_decision) {
            const options = [{ value: "take", label: "Take the new version" }];
            if (row.keepable) options.push({ value: "keep", label: "Keep this chat's version" });
            control = ui.choice(row.field, options);
            choices.push([row.field, control.select]);
          }
          return el("details", { class: "import-rows", open: row.needs_decision },
            el("summary", {}, row.field + ": " + row.status),
            facts([["Before", row.ancestor], ["New", row.upstream], ["This chat", row.local]]), control ? control.node : null);
        }),
        rows("Notices", found.notices),
        next("Nothing is written until you approve the preview.", "Preview update", preview)));
    }
    async function candidates() {
      const found = await ctx.api("imports.rebase_candidates", { chat: chat.value.trim() });
      show(step("Characters in this chat", ui.list(found.actors.map((row) => row.behind
        ? ui.button(row.binding + " has a newer revision: review the update", () => ctx.act(() => reviewActor(row.actor)))
        : el("span", {}, ui.rowText(row.binding, row.problem ? "Its source cannot be read: " + row.problem : "Up to date"))),
      { empty: "No character in this chat follows a source." })));
    }
    return [ui.card({ tag: "section", label: "Source update" }, chat.node,
      next("Every write is previewed first, file by file.", "Find updates", candidates))];
  }

  const FORMS = { card: cardFormOf, history: historyForm, v1: v1Form, changeset: changesetForm, rebase: rebaseForm };

  const kinds = el("nav", { class: "import-kinds", "aria-label": "What to import or review" }, usable.map(([key, label, about]) =>
    el("a", { class: "import-kind", href: ctx.href("import", [], { tab: key }), "aria-current": key === tab ? "page" : null },
      el("span", { class: "import-kind-name" }, label), el("span", { class: "g-muted import-kind-about" }, about))));
  if (tab) stage.replaceChildren(...present(FORMS[tab]()));
  else stage.replaceChildren(ui.unavailable("Import", "This app serves no import or review yet."));
  root.append(el("div", { class: "import-page" }, usable.length ? kinds : null, stage, out,
    capabilities.unavailable && capabilities.unavailable.length ? el("details", { class: "import-rows import-later" },
      el("summary", {}, "What this app cannot import yet"),
      el("ul", {}, capabilities.unavailable.map((row) => el("li", {}, row.detail, el("span", { class: "g-muted" }, " · waits on " + row.blocked_by))))) : null));
  return () => pictures.clear();
}
