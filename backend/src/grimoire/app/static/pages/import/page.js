// Sprint 110: import, migration and change review, over the `imports.*` adapter ops (docs/REVIEW_UI.md).
//
// Every write is a proposal: a preview op plans it and answers every file it would write (exact size, sha256,
// text and diff where readable) and a digest over those bytes. Approve sends that digest back with
// `imports.proposal_apply`; the server recomputes it from the plan before the domain service commits, so the bytes
// approved are the bytes written. Reject ends the proposal and writes nothing. Nothing on this page writes any
// other way, and a V1 library is only ever read.
//
// Every value shown comes from a library, card or export and is only ever set as text, never as markup.

const MAX_FILE = 34 * 1024 * 1024;  // the app takes a request of 48 MiB, base64 included

// each tab, and every op its form calls: a tab is shown only when the app serves all of them
const TABS = [
  ["card", "Card", ["imports.card_preview"]],
  ["history", "Chat history", ["imports.history_scan", "imports.history_preview"]],
  ["v1", "V1 library", ["imports.v1_scan", "imports.v1_preview", "imports.v1_receipts"]],
  ["changeset", "Change set", ["imports.changeset_review"]],
  ["rebase", "Source update", ["imports.rebase_candidates", "imports.rebase_review", "imports.rebase_preview"]],
];

const ACTIONS = { create: "New file", replace: "Replaces a file", unchanged: "Unchanged", write: "Written" };

function base64Of(file) {
  return new Promise((resolve, reject) => {
    if (file.size > MAX_FILE) {
      reject(Object.assign(new Error(file.name + " is larger than this app takes in one request"), { reason: "too_large" }));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const bytes = new Uint8Array(reader.result);
      let text = "";
      for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      resolve(btoa(text));
    };
    reader.onerror = () => reject(reader.error || new Error("the file could not be read"));
    reader.readAsArrayBuffer(file);
  });
}

function shown(value) {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export async function mount(root, ctx) {
  const { ui } = ctx;
  const { el } = ui;
  ctx.styles("page.css");
  ctx.setTitle("Import and review");
  const served = (ops) => ops.every((op) => ctx.has(op));
  const usable = TABS.filter(([, , ops]) => served(ops));
  const tab = usable.some(([key]) => key === ctx.query.get("tab")) ? ctx.query.get("tab") : usable.length ? usable[0][0] : null;

  const facts = (pairs) => el("dl", { class: "import-facts" },
    pairs.filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(([key, value]) => [el("dt", {}, key), el("dd", {}, shown(value))]));
  const rows = (title, found, describe = shown) => {
    if (!found) return null;
    const items = Array.isArray(found) ? found : found.rows || [];
    const total = Array.isArray(found) ? found.length : found.total;
    if (!total) return null;
    return ui.section(title + " (" + total + ")",
      el("ul", { class: "g-list" }, items.map((row) => el("li", {}, describe(row)))),
      found.truncated ? el("p", { class: "g-muted" }, "Showing " + items.length + " of " + total + ".") : null);
  };
  const out = el("div", { "aria-live": "polite" });
  const show = (...nodes) => out.replaceChildren(...nodes.flat().filter(Boolean));

  // -- the shared proposal panel: every file, its exact bytes' hash, and digest-bound Approve / Reject ----------
  function fileRow(file) {
    const action = ACTIONS[file.action] || (String(file.action).startsWith("unreadable") ? "Current file unreadable" : file.action);
    const body = file.diff !== null && file.diff !== undefined ? el("pre", { class: "import-pre" }, file.diff)
      : file.text !== null && file.text !== undefined ? el("pre", { class: "import-pre" }, file.text)
        : el("p", { class: "g-muted" }, file.binary ? "Binary file: shown by its size and hash only."
          : "Too large to show: shown by its size and hash only.");
    return el("details", { class: "import-file" },
      el("summary", {}, el("strong", {}, file.path), " ", el("span", { class: "import-badge" }, action), " ",
        el("span", { class: "g-muted" }, file.size + " bytes")),
      el("p", { class: "g-muted import-mono" }, "sha256 " + file.sha256), body);
  }

  function proposalPanel(proposal, report, after) {
    const panel = el("section", { class: "import-proposal", "aria-label": "Proposed write" });
    const count = proposal.writes + (proposal.writes === 1 ? " file" : " files");
    function render(state, result) {
      const open = state === "open";
      panel.replaceChildren(...[
        el("h2", {}, open ? "Review before anything is written" : state === "applied" ? "Written" : "Not written"),
        report,
        el("h3", {}, count),
        proposal.files.length ? proposal.files.map(fileRow)
          : ui.empty("Nothing to write: the library already holds exactly this."),
        el("p", { class: "g-muted import-mono" }, "Digest " + proposal.digest),
        result ? facts(Object.entries(result)) : null,
        open ? ui.actions(
          ui.button(proposal.writes ? "Approve and write" : "Approve", () => ctx.act(approve), { kind: "primary" }),
          ui.button("Reject", () => ctx.act(reject))) : null,
      ].flat().filter(Boolean));
    }
    async function approve() {
      if (proposal.writes && !await ui.confirm("Write " + count + " into this library, exactly as shown?",
        { confirmLabel: "Write " + count })) return;
      try {
        const done = await ctx.api("imports.proposal_apply", { proposal: proposal.proposal, digest: proposal.digest });
        render("applied", done.result);
        ctx.notify("Written.");
        if (after) await after(done);
      } catch (error) {
        // a stale or conflicting write ended this proposal: preview again. A denied one stays open.
        if (["stale", "stale_approval", "conflict", "no_proposal"].includes(error.reason)) {
          render("ended");
          ctx.notify(ui.errorText(error) + " Nothing was written; preview it again.", { bad: true });
          return;
        }
        throw error;
      }
    }
    async function reject() {
      await ctx.api("imports.proposal_reject", { proposal: proposal.proposal });
      render("rejected");
      ctx.notify("Rejected: nothing was written.");
    }
    render(proposal.state);
    return panel;
  }

  // -- one form per kind -------------------------------------------------------------------------------------
  const fileInput = (label, accept, hint) => {
    const input = el("input", { type: "file", accept, class: "g-input", id: ui.id("file") });
    return { input, node: el("div", { class: "g-field" }, el("label", { for: input.id }, label),
      hint ? el("div", { class: "g-muted" }, hint) : null, input) };
  };
  const check = (label) => {
    const input = el("input", { type: "checkbox" });
    return { input, node: el("label", { class: "import-check" }, input, label) };
  };

  function cardForm() {
    const cardFile = fileInput("Card", ".json,application/json",
      "Character Card V3 JSON, a scenario card or a V3 lorebook.");
    const portrait = fileInput("Portrait", "image/png,image/jpeg,image/webp", "Optional. Without it the card's pictures are reported missing.");
    const lorebook = fileInput("Separate lorebook", ".json,application/json", "Optional.");
    const scenario = check("Import as a scenario");
    async function preview() {
      const file = cardFile.input.files[0];
      if (!file) { ctx.notify("Choose a card file first.", { bad: true }); return; }
      const args = { card: await base64Of(file), origin: file.name, as_scenario: scenario.input.checked };
      if (portrait.input.files[0]) args.portrait = await base64Of(portrait.input.files[0]);
      if (lorebook.input.files[0]) args.lorebook = await base64Of(lorebook.input.files[0]);
      const proposal = await ctx.api("imports.card_preview", args);
      const r = proposal.report;
      show(proposalPanel(proposal, el("div", {},
        facts([["Status", r.status], ["Destination", r.source], ["Format", r.format], ["Becomes", r.kind]]),
        rows("Not carried over", r.losses), rows("Collisions", r.collisions),
        r.suggestions ? ui.section("Name suggestions (shown, never linked)", facts(Object.entries(r.suggestions))) : null)));
    }
    return [cardFile.node, portrait.node, lorebook.node, scenario.node,
      ui.actions(ui.button("Preview import", () => ctx.act(preview), { kind: "primary" }))];
  }

  function historyForm() {
    const exportFile = fileInput("SillyTavern chat export", ".jsonl,application/jsonl,text/plain");
    async function scanIt() {
      const file = exportFile.input.files[0];
      if (!file) { ctx.notify("Choose a SillyTavern chat export first.", { bad: true }); return; }
      const exported = await base64Of(file);
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
        show(proposalPanel(proposal, el("div", {},
          facts([["Status", r.status], ["Chat", r.chat], ["Pages", r.pages], ["Counts", r.counts], ["Staged", r.staged],
            ["Accepted events", r.accepted_events.length], ["Character memories", r.memories.length]]),
          el("p", { class: "g-muted" }, "Imported lines are kept as transcript only: they become no accepted events and no character memories."),
          rows("Not carried over", r.losses)),
        async (done) => { if (ctx.registered("chat") && done.result.chat) ctx.notify("Imported as " + done.result.chat + "."); }));
      }
      show(ui.section("Who is speaking",
        facts([["Bytes", found.bytes], ["sha256", found.sha256], ["Counts", found.counts]]),
        inputs.map(([, input]) => input.node), label.node, rows("Not carried over", found.losses),
        ui.actions(ui.button("Preview import", () => ctx.act(preview), { kind: "primary" }))));
    }
    return [exportFile.node, ui.actions(ui.button("Read export", () => ctx.act(scanIt), { kind: "primary" }))];
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
        show(proposalPanel(proposal, el("div", {},
          facts([["Archive", r.archive_id], ["Outcomes", r.outcome_counts], ["Model calls", r.provider_calls]]),
          rows("Unresolved", r.unresolved), rows("Not imported", r.not_imported),
          rows("Flagged", r.flags), rows("Not carried over", r.losses)),
        async () => {
          const receipts = await ctx.api("imports.v1_receipts", { archive_id: found.archive_id });
          ctx.notify("Import receipt: run " + receipts.runs + ", " + receipts.written + " files written so far.");
        }));
      }
      show(ui.section("What the V1 library holds",
        facts([["Archive", found.archive_id], ["Status", found.status], ["Counts", found.counts],
          ["Pictures", found.images], ["Model calls", found.provider_calls]]),
        el("h3", {}, "Import"),
        Object.entries(selection.worlds).map(([legacy, target]) => pick("worlds", legacy, target)),
        Object.entries(selection.campaigns).map(([legacy, target]) => pick("campaigns", legacy, target)),
        rows("Not proposed", selection.unproposed),
        rows("Identities", found.identities, (row) => [row.legacy_id, row.status, row.candidate].filter(Boolean).join(" · ")),
        rows("Findings", found.findings), rows("Unresolved", found.unresolved), rows("Not carried over", found.losses),
        ui.actions(ui.button("Preview import", () => ctx.act(preview), { kind: "primary" }))));
    }
    return [source.node, archive.node, ui.actions(ui.button("Scan library", () => ctx.act(scanIt), { kind: "primary" }))];
  }

  function changesetForm() {
    const documentField = ui.field("Change set (JSON)", { multiline: true,
      hint: "Proposed change sets are not stored yet: paste the change set document to review it." });
    async function review() {
      let changeset;
      try { changeset = JSON.parse(documentField.value); }
      catch (error) { documentField.setError("This is not JSON."); return; }
      documentField.setError("");
      const proposal = await ctx.api("imports.changeset_review", { changeset });
      const r = proposal.report;
      show(proposalPanel(proposal, el("div", {},
        facts([["Change set", r.changeset], ["Title", r.title], ["Author", r.author], ["Why", r.rationale],
          ["Can be applied", r.applicable ? "yes" : "no"]]),
        rows("Changes", r.changes, (row) => row.action + " " + row.path + (row.summary ? ": " + row.summary : "")),
        rows("Conflicts", r.conflicts), rows("Blocked", r.blocked))));
    }
    return [documentField.node, ui.actions(ui.button("Review change set", () => ctx.act(review), { kind: "primary" }))];
  }

  function rebaseForm() {
    const chat = ui.field("Chat", { value: ctx.query.get("chat") || "", placeholder: "chat:…",
      hint: "The chat whose characters may follow a newer revision of their source." });
    async function review(actor) {
      const found = await ctx.api("imports.rebase_review", { actor });
      const choices = [];
      async function preview() {
        const decisions = {};
        for (const [name, select] of choices) decisions[name] = select.value;
        const proposal = await ctx.api("imports.rebase_preview", { review: found.review, decisions });
        show(proposalPanel(proposal, facts([["From", found.ancestor], ["To", found.upstream], ["Decisions", decisions]])));
      }
      show(ui.section("Update " + found.actor,
        facts([["From", found.ancestor], ["To", found.upstream], ["History kept", found.history]]),
        found.rows.map((row) => {
          let control = null;
          if (row.needs_decision) {
            const options = [{ value: "take", label: "Take the new version" }];
            if (row.keepable) options.push({ value: "keep", label: "Keep this chat's version" });
            control = ui.choice(row.field, options);
            choices.push([row.field, control.select]);
          }
          return el("details", { class: "import-file", open: row.needs_decision },
            el("summary", {}, row.field + ": " + row.status),
            facts([["Before", row.ancestor], ["New", row.upstream], ["This chat", row.local]]), control ? control.node : null);
        }),
        rows("Notices", found.notices),
        ui.actions(ui.button("Preview update", () => ctx.act(preview), { kind: "primary" }))));
    }
    async function candidates() {
      const found = await ctx.api("imports.rebase_candidates", { chat: chat.value.trim() });
      show(ui.section("Characters in this chat", ui.list(found.actors.map((row) => row.behind
        ? ui.button(row.binding + " has a newer revision: review the update", () => ctx.act(() => review(row.actor)))
        : el("span", {}, ui.rowText(row.binding, row.problem ? "Its source cannot be read: " + row.problem : "Up to date"))),
      { empty: "No character in this chat follows a source." })));
    }
    return [chat.node, ui.actions(ui.button("Find updates", () => ctx.act(candidates), { kind: "primary" }))];
  }

  const FORMS = { card: cardForm, history: historyForm, v1: v1Form, changeset: changesetForm, rebase: rebaseForm };

  const capabilities = await ctx.api("imports.capabilities", {});
  const tabs = el("div", { class: "g-actions", role: "group", "aria-label": "What to import or review" });
  for (const [key, label] of usable) {
    const link = el("a", { class: "g-button", href: ctx.href("import", [], { tab: key }) }, label);
    if (key === tab) link.setAttribute("aria-current", "page");
    tabs.append(link);
  }
  root.append(tabs, tab ? ui.section(TABS.find(([key]) => key === tab)[1], ...FORMS[tab]())
    : ui.unavailable("Import", "This app serves no import or review yet."), out,
    el("details", {}, el("summary", {}, "Not available yet"),
      el("ul", { class: "g-list" }, capabilities.unavailable.map((row) =>
        el("li", {}, ui.rowText(row.detail, "Waits on " + row.blocked_by))))));
}
