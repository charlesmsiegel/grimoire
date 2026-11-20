// A structural accessibility audit of a rendered screen in fakedom: the subset of WCAG 2.2 / axe rules that the
// document tree alone can decide. It has no layout, styles or colour, so it cannot judge contrast, focus
// visibility, reflow or target size (the browser acceptance suite and a person with a real screen reader do),
// and no accessibility tree: names are computed from the attributes and text the shell sets, as below.
//
// audit(root) returns every violation under `root` as {rule, node, detail}; describe(violation) says it in a line.
// Hidden subtrees (the `hidden` attribute, `aria-hidden="true"`, a closed <dialog>, <template>) are skipped, as a
// browser leaves them out of the accessibility tree, except that a focusable element inside aria-hidden is itself
// a violation.

const CONTROLS = new Set(["INPUT", "SELECT", "TEXTAREA"]);
const INTERACTIVE = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "DETAILS"]);
// roles a person can operate: an element with a click listener and no native semantics needs one of these
const WIDGET_ROLES = new Set(["button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox",
  "menuitemradio", "option", "treeitem", "gridcell", "slider", "spinbutton", "combobox", "textbox", "searchbox", "row"]);
const ROLES = new Set([...WIDGET_ROLES, "alert", "alertdialog", "application", "article", "banner", "cell", "columnheader",
  "complementary", "contentinfo", "definition", "dialog", "directory", "document", "feed", "figure", "form", "grid",
  "group", "heading", "img", "list", "listbox", "listitem", "log", "main", "marquee", "math", "menu", "menubar",
  "navigation", "none", "note", "presentation", "progressbar", "radiogroup", "region", "rowgroup", "rowheader",
  "scrollbar", "search", "separator", "status", "table", "tablist", "tabpanel", "term", "timer", "toolbar", "tooltip",
  "tree", "treegrid", "generic", "meter", "paragraph", "caption", "code", "emphasis", "strong", "time"]);
const BOOLEAN_STATES = ["aria-pressed", "aria-expanded", "aria-selected", "aria-checked", "aria-disabled",
  "aria-busy", "aria-invalid", "aria-hidden", "aria-modal", "aria-required", "aria-current"];
const STATE_VALUES = {
  "aria-pressed": ["true", "false", "mixed"], "aria-checked": ["true", "false", "mixed"],
  "aria-invalid": ["true", "false", "grammar", "spelling"],
  "aria-current": ["true", "false", "page", "step", "location", "date", "time"],
};
const ID_REFS = ["aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-errormessage", "aria-details",
  "aria-activedescendant"];
// containers whose name comes only from their author (aria-label, aria-labelledby, title), never from their text
const AUTHOR_NAMED = new Set(["DIALOG", "NAV", "MAIN", "SECTION", "ASIDE", "HEADER", "FOOTER", "FORM", "UL", "OL", "TABLE"]);
const AUTHOR_NAMED_ROLES = new Set(["dialog", "alertdialog", "navigation", "main", "region", "complementary", "banner",
  "contentinfo", "form", "search", "list", "listbox", "grid", "table", "tablist", "toolbar", "menu", "menubar", "tree",
  "radiogroup", "group", "img"]);
const LANDMARKS = { MAIN: "main", NAV: "navigation", HEADER: "banner", FOOTER: "contentinfo", ASIDE: "complementary" };

function elements(root) {
  const found = [];
  const walk = (node, hidden) => {
    for (const child of node.childNodes || []) {
      if (!child.tagName) continue;
      const tag = child.tagName;
      const closed = tag === "TEMPLATE" || child.hasAttribute("hidden") || (tag === "DIALOG" && !child.open);
      const ariaHidden = hidden || child.getAttribute("aria-hidden") === "true";
      if (closed) continue;
      found.push({ node: child, ariaHidden });
      walk(child, ariaHidden);
    }
  };
  walk(root, false);
  return found;
}

// text a person hears: aria-hidden and hidden descendants are left out
function heard(node) {
  if (!node.tagName) return node.textContent || "";
  if (node.getAttribute("aria-hidden") === "true" || node.hasAttribute("hidden") || node.tagName === "TEMPLATE") return "";
  return (node.childNodes || []).map(heard).join("");
}
const text = (node) => heard(node).replace(/\s+/g, " ").trim();

// every id under root, hidden subtrees included: an aria reference to a hidden element is valid ARIA
function allIds(root) {
  const ids = new Set();
  const walk = (node) => {
    for (const child of node.childNodes || []) {
      if (!child.tagName) continue;
      if (child.getAttribute("id") !== null) ids.add(child.getAttribute("id"));
      walk(child);
    }
  };
  walk(root);
  return ids;
}

function byId(all, id) { return all.find(({ node }) => node.getAttribute("id") === id); }

function focusable(node) {
  const tabindex = node.getAttribute("tabindex");
  if (tabindex !== null) return Number(tabindex) >= 0;
  if (node.disabled) return false;
  if (node.tagName === "A") return node.hasAttribute("href");
  if (node.tagName === "INPUT") return node.getAttribute("type") !== "hidden";
  return ["BUTTON", "SELECT", "TEXTAREA", "SUMMARY"].includes(node.tagName);
}

// the accessible name, by the parts of the name computation the shell relies on
export function nameOf(node, all) {
  const labelledby = node.getAttribute("aria-labelledby");
  if (labelledby) {
    const parts = labelledby.split(/\s+/).filter(Boolean).map((id) => byId(all, id)).filter(Boolean).map(({ node: n }) => text(n));
    if (parts.join("").trim()) return parts.join(" ").trim();
  }
  const label = node.getAttribute("aria-label");
  if (label && label.trim()) return label.trim();
  if (CONTROLS.has(node.tagName)) {
    const id = node.getAttribute("id");
    if (id) {
      const labels = all.filter(({ node: n, ariaHidden }) => !ariaHidden && n.tagName === "LABEL" && n.getAttribute("for") === id);
      const named = labels.map(({ node: n }) => text(n)).filter(Boolean);
      if (named.length) return named.join(" ");
    }
    for (let at = node.parentNode; at && at.tagName; at = at.parentNode) {
      if (at.getAttribute("aria-hidden") === "true") break;
      if (at.tagName === "LABEL") { const own = text(at); if (own) return own; }
    }
    if (node.tagName === "INPUT" && ["button", "submit", "reset"].includes(node.getAttribute("type"))) {
      return (node.getAttribute("value") || "").trim();
    }
  } else if (AUTHOR_NAMED.has(node.tagName) || AUTHOR_NAMED_ROLES.has(node.getAttribute("role"))) {
    // no name from content
  } else if (node.tagName !== "IMG") {
    const own = text(node);
    if (own) return own;
    const images = node.querySelectorAll ? node.querySelectorAll("img") : [];
    const alts = images.map((img) => (img.getAttribute("alt") || "").trim()).filter(Boolean);
    if (alts.length) return alts.join(" ");
  } else {
    const alt = node.getAttribute("alt");
    if (alt !== null) return alt.trim();
  }
  const title = node.getAttribute("title");
  if (title && title.trim()) return title.trim();
  if (node.getAttribute("placeholder") && CONTROLS.has(node.tagName)) return "";  // a placeholder is not a label
  return "";
}

export function audit(root, { page = true } = {}) {
  const all = elements(root);
  const known = allIds(root);
  const out = [];
  const flag = (rule, node, detail = "") => out.push({ rule, node, detail });

  const ids = new Map();
  for (const { node } of all) {
    const id = node.getAttribute("id");
    if (id === null) continue;
    if (!id.trim()) flag("empty-id", node);
    ids.set(id, (ids.get(id) || 0) + 1);
  }
  for (const [id, count] of ids) if (count > 1) flag("duplicate-id", all.find(({ node }) => node.getAttribute("id") === id).node, id);

  for (const { node, ariaHidden } of all) {
    const tag = node.tagName;
    const role = node.getAttribute("role");
    if (ariaHidden) {
      if (focusable(node)) flag("aria-hidden-focus", node);
      continue;
    }
    if (role !== null) for (const one of role.split(/\s+/)) if (!ROLES.has(one)) flag("aria-roles", node, role);
    for (const state of BOOLEAN_STATES) {
      const value = node.getAttribute(state);
      if (value === null) continue;
      if (!(STATE_VALUES[state] || ["true", "false"]).includes(value)) flag("aria-valid-attr-value", node, state + "=" + value);
    }
    for (const attribute of ID_REFS) {
      const value = node.getAttribute(attribute);
      if (value === null) continue;
      for (const id of value.split(/\s+/).filter(Boolean)) if (!known.has(id)) flag("aria-valid-ref", node, attribute + " → " + id);
    }
    const tabindex = node.getAttribute("tabindex");
    if (tabindex !== null && Number(tabindex) > 0) flag("tabindex", node, tabindex);

    if (CONTROLS.has(tag) && node.getAttribute("type") !== "hidden" && !nameOf(node, all)) flag("label", node);
    if (tag === "LABEL" && node.getAttribute("for") !== null) {
      const target = byId(all, node.getAttribute("for"));
      if (!target || !CONTROLS.has(target.node.tagName)) flag("label-for", node, node.getAttribute("for"));
    }
    if (tag === "BUTTON" && !nameOf(node, all)) flag("button-name", node);
    if (tag === "A" && node.hasAttribute("href") && !nameOf(node, all)) flag("link-name", node);
    if (tag === "IMG" && node.getAttribute("alt") === null && role !== "presentation" && role !== "none") flag("image-alt", node);
    if (role === "img" && !nameOf(node, all)) flag("role-img-alt", node);
    if ((tag === "DIALOG" || role === "dialog" || role === "alertdialog") && !nameOf(node, all) && !node.getAttribute("aria-labelledby")) {
      flag("aria-dialog-name", node);
    }
    if (/^H[1-6]$/.test(tag) && !text(node)) flag("empty-heading", node);
    if (tag === "LI") {
      const parent = node.parentNode;
      if (!parent || !["UL", "OL", "MENU"].includes(parent.tagName) && parent.getAttribute("role") !== "list") flag("listitem", node);
    }
    if ((tag === "SECTION" || role === "region") && node.hasAttribute("aria-labelledby") && !nameOf(node, all)) flag("region-name", node);
    if (role && WIDGET_ROLES.has(role) && !INTERACTIVE.has(tag) && !nameOf(node, all)) flag("aria-command-name", node, role);
    // something clickable must be reachable and announced as what it is
    // (a listener on a landmark, a dialog, or the frame around the page's main is delegation or a backdrop's
    // light dismiss, not a control itself; any other clickable container, like a row holding a Delete button, is;
    // an option of a listbox is reached by aria-activedescendant from its focused combobox, not by Tab)
    const clicks = node.listeners && (node.listeners.get("click") || []).length;
    const delegates = () => ["DIALOG", "NAV", "MAIN"].includes(tag) || ["dialog", "alertdialog", "navigation", "main"].includes(role)
      || node.querySelectorAll("main,nav,dialog").length > 0;
    if (clicks && !INTERACTIVE.has(tag) && !(role && WIDGET_ROLES.has(role)) && !delegates()) {
      flag("click-events-have-key-events", node, tag.toLowerCase() + (node.className ? "." + node.className.split(/\s+/).join(".") : ""));
    } else if (clicks && role && WIDGET_ROLES.has(role) && role !== "option" && !INTERACTIVE.has(tag) && !focusable(node)) {
      flag("focusable-widget", node, role);
    }
    if (INTERACTIVE.has(tag) && tag !== "DETAILS") {
      for (let at = node.parentNode; at && at.tagName; at = at.parentNode) {
        if (at === root) break;
        if (["A", "BUTTON"].includes(at.tagName) || (at.tagName === "SUMMARY" && tag !== "SUMMARY")) { flag("nested-interactive", node, at.tagName.toLowerCase()); break; }
      }
    }
  }

  if (page) {
    const landmarks = (name) => all.filter(({ node, ariaHidden }) => !ariaHidden
      && (LANDMARKS[node.tagName] === name || node.getAttribute("role") === name));
    const mains = landmarks("main");
    if (mains.length !== 1) flag("landmark-one-main", root, mains.length + " main landmarks");
    const navs = landmarks("navigation");
    if (navs.length > 1) for (const { node } of navs) if (!nameOf(node, all)) flag("landmark-unique", node, "an unnamed nav among several");
    const headings = all.filter(({ node, ariaHidden }) => !ariaHidden && /^H[1-6]$/.test(node.tagName));
    if (!headings.some(({ node }) => node.tagName === "H1")) flag("page-has-heading-one", root);
  }
  return out;
}

export function describe({ rule, node, detail }) {
  const name = node.tagName ? node.tagName.toLowerCase() : "document";
  const cls = node.className ? "." + String(node.className).split(/\s+/).filter(Boolean).join(".") : "";
  const words = text(node).slice(0, 60);
  return rule + ": <" + name + cls + ">" + (words ? " \"" + words + "\"" : "") + (detail ? " (" + detail + ")" : "");
}
