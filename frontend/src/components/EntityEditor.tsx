import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, api, ENTITY_FIELDS, ENTITY_KINDS, SECRECY_LABELS, SECRECY_LEVELS, type EntityFieldSpec, type EntityKind, type EntityScope, type EntitySummary, type ModuleContentEntry, type ModuleDetail, type RefKind, type Secrecy } from "../api/client";
import { loreOwnerOptions, refOptions, type RecordRef } from "../api/loreOwners";
import CreationWizard from "./CreationWizard";
import { DemotePanel } from "./DemotePanel";
import { Field } from "./Field";
import { ImageDescriptionField } from "./ImageDescriptionField";
import { GroupStatePanel } from "./GroupStatePanel";
import { LibraryPanel } from "./LibraryPanel";
import { OwnedLorePanel } from "./OwnedLorePanel";
import { Portrait } from "./Portrait";
import SheetPanel from "./SheetPanel";
import { StaleRecordBanner } from "./StaleRecordBanner";

export const KIND_LABELS: Record<EntityKind, string> = {
  locations: "location", lore: "lore entry", items: "item", groups: "group", creatures: "creature",
};

// What each referenceable kind is called, for saying what an empty picker is
// missing (`many`) and for telling two same-named candidates apart (`one`).
// Wider than KIND_LABELS because a ref field can name an actor, which is not an
// entity kind and so has no entry there.
const KIND_NOUNS: Record<RefKind, { one: string; many: string }> = {
  characters: { one: "character", many: "characters" },
  pcs: { one: "PC", many: "PCs" },
  locations: { one: "location", many: "locations" },
  lore: { one: "lore entry", many: "lore entries" },
  items: { one: "item", many: "items" },
  groups: { one: "group", many: "groups" },
  creatures: { one: "creature", many: "creatures" },
};

/** Display text per ref, unique within `options`.
 *
 *  Names are not unique — only ids are — and a field like `holder` offers four
 *  kinds at once, so a PC and a group can both be called Mara. Two radios
 *  reading "Mara" is a control where the reader cannot tell which relationship
 *  they are about to store, and the accessible name is just as ambiguous as
 *  the visible one.
 *
 *  Qualified only where it is needed, and by the least that settles it: the
 *  kind for a cross-kind collision, the ref itself when even that still
 *  collides (two characters really can share a name). An unambiguous candidate
 *  keeps its plain name, so the common picker is not decorated for a case it
 *  does not have. */
function displayLabels(options: RecordRef[]): Map<string, string> {
  const byLabel = new Map<string, RecordRef[]>();
  for (const o of options) byLabel.set(o.label, [...(byLabel.get(o.label) ?? []), o]);
  const out = new Map<string, string>();
  for (const [label, group] of byLabel) {
    if (group.length === 1) { out.set(group[0].ref, label); continue; }
    const kinds = new Map<string, number>();
    for (const o of group) kinds.set(o.kind, (kinds.get(o.kind) ?? 0) + 1);
    for (const o of group) {
      out.set(o.ref, kinds.get(o.kind) === 1
        ? `${label} (${KIND_NOUNS[o.kind].one})`
        : `${label} (${o.ref})`);
    }
  }
  return out;
}

// What a record's body costs when it reaches a prompt (#51). Counted on the
// server with the same tokenizer the context inspector uses; the frontend only
// formats it.
//
// The same TOKENIZER, not the same total: the World info section is the
// activated bodies joined with blank lines, and BPE is not additive, so these
// badges summed come out a token or so per join UNDER the inspector's row for
// that section. Near enough to reason with, and not a figure to reconcile
// arithmetically — the inspector remains the authority on what a prompt cost.
//
// The cost is per ACTIVATION, not per turn, and the tooltip says so: a keyed
// entry pays it on the turns its keys hit and nothing on the rest, so a big
// number here is only a big prompt if the entry is always-on. Reading it as a
// standing cost is the obvious wrong conclusion to draw from a bare number.
//
// It is also a CEILING for a body that uses macros, which the tooltip says
// because the rail has no room for a second line: the prompt gets the expanded
// text, so `{{random:a,b,c}}` collapses to one option and the stored form can
// measure double what is sent. Exact for the ordinary body that uses none.
const TOKEN_HINT =
  "Tokens this body costs each time it enters the prompt — keyed entries pay it only when they "
  + "activate, and macros are counted unexpanded";

// A gm-only body is dropped before every activation rule (#49), so it costs
// nothing, ever. The count is still shown -- it is the size of the text, and it
// is what the entry would cost the moment someone marks it public again -- but
// it has to LOOK spent-out rather than sit there reading like a live charge.
const TOKEN_HINT_NEVER =
  "This body never reaches the prompt while it is GM-only — what it would cost if published";

function tokenLabel(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? "token" : "tokens"}`;
}

const isNeverCharged = (level: Secrecy) => level === "gm-only";
const tokenClass = (level: Secrecy, base = "token-badge") =>
  base + (isNeverCharged(level) ? " never-charged" : "");
const tokenTitle = (level: Secrecy) => (isNeverCharged(level) ? TOKEN_HINT_NEVER : TOKEN_HINT);
// Struck-through text does not announce as struck through, so the exemption has
// to be in the name rather than only in the styling.
const tokenAria = (n: number, level: Secrecy) =>
  isNeverCharged(level) ? `${tokenLabel(n)}, never charged` : tokenLabel(n);

// What each secrecy level does to the prompt, in the words the picker shows.
// "this text", not "this entry": secrecy gates the BODY, and the record still
// has a name the app uses where it must refer to the place at all — the
// scene-suggestion picker, a mechanics sheet label. Claiming the whole entry
// disappears would be a promise the app does not keep.
const SECRECY_HINTS: Record<Secrecy, string> = {
  public: "activates normally; any character may know it",
  secret: "activates normally, but uninvolved characters must not reveal it",
  "gm-only": "this text never reaches the model; the name may still be used",
};

// Anything unrecognised (a hand-edited file) reads as public, exactly as
// store.entities.normalize_secrecy does on the other side -- INCLUDING the trim
// and the lowercase. Matching only the canonical spelling looked harmless and
// was not: the backend reads `secrecy: Secret` as secret and keeps the entry
// out of ignorant characters' mouths, while this returned "public", badged the
// entry Public, and made the next save send `secrecy: "public"` -- a valid
// level, so the route accepts it and the key is deleted. Editing the body of a
// hand-marked secret silently published it.
const asSecrecy = (v: unknown): Secrecy => {
  const level = String(v ?? "").trim().toLowerCase();
  return (SECRECY_LEVELS as readonly string[]).includes(level) ? (level as Secrecy) : "public";
};

/** The union of the record kinds this kind's `ref` fields can name, in first
 *  appearance order and without repeats — what to fetch once for the whole
 *  form. Empty for a kind that declares no ref field, which is what the effect
 *  below checks before making any request at all. */
function refKindsFor(specs: EntityFieldSpec[]): RefKind[] {
  const out: RefKind[] = [];
  for (const spec of specs) {
    for (const k of spec.kinds ?? []) if (!out.includes(k)) out.push(k);
  }
  return out;
}

/** The declared fields whose value differs from what was loaded.
 *
 *  A create passes `{}` as `loaded`, so everything the user typed is "changed"
 *  and an untouched field is simply absent — which is what `create_entity`
 *  wants anyway, since it drops empties. */
function changedFields(
  current: Record<string, string>, loaded: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(current)) {
    if (value !== (loaded[key] ?? "")) out[key] = value;
  }
  return out;
}

/** Refs stored in one field, in the `owners:` spelling: comma-separated
 *  `<kind>:<id>`. Same parse as `entity_schema.parse_refs` on the other side.
 *
 *  Takes the undefined a `fields[key]` lookup can hand back rather than
 *  `unknown`: everything that reaches this has already been read out of the
 *  editor's own string map, and widening it would only invite a caller to
 *  stringify something that has no spelling. */
const parseRefs = (v: string | undefined): string[] =>
  (v ?? "").split(",").map((r) => r.trim()).filter(Boolean);

// What a ref that resolves to nothing says when hovered. Not an error and not
// hidden: a delete deliberately leaves refs dangling (#222), so the chip's job
// is to say the record is gone rather than to look like a field nobody filled in.
const DANGLING_HINT = "This record no longer exists here — it may have been deleted, "
  + "or it may live outside this campaign";

// ...and what it says when we do not actually know. A candidate list that
// failed to load leaves every ref unresolved, and reporting that as "deleted"
// turns a dropped request into a claim about the store — one the reader may
// act on by clearing a relationship that was perfectly fine.
const UNLOADED_HINT = "Could not load the list of records, so this reference could not be "
  + "resolved — it has not necessarily gone anywhere";

/** The picker for one `ref` field (#222).
 *
 *  Real radios and real checkboxes, for the reason the secrecy picker gives:
 *  a native group gets arrow-key navigation and roving focus for free, which a
 *  row of buttons wearing `role="radio"` does not. Which of the two a field
 *  gets is `multi` — a group has one leader and a creature ranges over several
 *  places, and a control that let you tick two leaders would be offering a save
 *  the backend refuses.
 *
 *  Not wrapped in `Field`: `Field` labels ONE control by id, and this is a set
 *  of them. The heading is carried by the group's `aria-label` instead, which
 *  is what makes each option's own label unambiguous — "Mara" under Leader and
 *  "Mara" under Held by are two different controls, and only the group says so.
 */
function RefField({ spec, options, value, onChange, unresolvedHint, optionsComplete }: {
  spec: EntityFieldSpec;
  options: RecordRef[];
  value: string;
  onChange: (v: string) => void;
  /** What a ref no candidate answers to means right now — "the record is gone"
   *  only when the candidate lists actually loaded. See `unresolvedHint` in the
   *  editor below. */
  unresolvedHint: string;
  /** Whether `options` is the whole truth. False while the listings are in
   *  flight or after one failed, and it is what stops an empty picker from
   *  claiming the store is empty. */
  optionsComplete: boolean;
}) {
  const selected = parseRefs(value);
  const shown = displayLabels(options);
  const labelOf = (o: RecordRef) => shown.get(o.ref) ?? o.label;
  // A stored ref that no candidate answers to — the record was deleted, or it
  // lives outside this scope. It gets a row of its own, checked, so the value
  // is VISIBLE and clearable. Leaving it out was the tempting shortcut and the
  // wrong one: the field would look unset while still saving the old ref, and
  // the one thing the user cannot then do is remove it.
  const dangling = selected.filter((r) => !options.some((o) => o.ref === r));
  // Only what this field is allowed to name, in the words the field uses. A
  // picker that offered nothing and said nothing reads as a broken control.
  //
  // ...but "there are none" is a claim about the store, and only a listing
  // that arrived can make it. A single-kind field whose one listing failed
  // would otherwise sit there reading "No locations yet." — a request outage
  // presented as an empty library, with no error and nothing to retry.
  const empty = optionsComplete
    ? `No ${spec.kinds?.map((k) => KIND_NOUNS[k].many).join(" or ")} yet.`
    : "Could not load the list of records to choose from.";
  const danglingRow = (ref: string, type: "radio" | "checkbox", onClear: () => void) => (
    <label key={ref} className="owner-option dangling" title={unresolvedHint}>
      <input type={type} name={spec.key} aria-label={ref} checked onChange={onClear} />
      {ref}
    </label>
  );
  if (spec.multi) {
    return (
      <div className="field">
        <label>{spec.label}</label>
        <div className="chips owner-picker" role="group" aria-label={spec.label}>
          {options.map((o) => (
            <label key={o.ref} className="owner-option">
              <input type="checkbox" aria-label={labelOf(o)} checked={selected.includes(o.ref)}
                     onChange={(e) => onChange(
                       (e.target.checked
                         ? [...selected, o.ref]
                         : selected.filter((r) => r !== o.ref)).join(", "))} />
              {labelOf(o)}
            </label>
          ))}
          {dangling.map((ref) => danglingRow(ref, "checkbox",
            () => onChange(selected.filter((r) => r !== ref).join(", "))))}
          {options.length === 0 && dangling.length === 0
            && <span className="field-hint">{empty}</span>}
        </div>
      </div>
    );
  }
  return (
    <div className="field">
      <label>{spec.label}</label>
      <div className="chips owner-picker" role="radiogroup" aria-label={spec.label}>
        {/* An explicit None, because a radio group has no way back to unset:
            without it, picking a leader by accident could never be undone. */}
        <label className="owner-option">
          <input type="radio" name={spec.key} aria-label="None" checked={selected.length === 0}
                 onChange={() => onChange("")} />
          None
        </label>
        {options.map((o) => (
          <label key={o.ref} className="owner-option">
            <input type="radio" name={spec.key} aria-label={labelOf(o)}
                   checked={selected[0] === o.ref} onChange={() => onChange(o.ref)} />
            {labelOf(o)}
          </label>
        ))}
        {/* Clearing it means picking None or another candidate; the row itself
            is checked and stays checked until one of those happens, which is
            how a radio group says "this is the current value". */}
        {dangling.map((ref) => danglingRow(ref, "radio", () => undefined))}
        {options.length === 0 && dangling.length === 0
          && <span className="field-hint">{empty}</span>}
      </div>
    </div>
  );
}

export function EntityEditor({ wid, kind, scope: scopeProp, nav, onNavConsumed, onOpenOwner, onOpenLore, onReclassified, module = null }: {
  wid: string;
  kind: EntityKind;
  scope?: EntityScope;
  nav?: { focusEntry?: string; newOwner?: string } | null;
  onNavConsumed?: () => void;
  onOpenOwner?: (ref: string) => void;
  onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void;
  // A reclassified record leaves this editor's list entirely, so the parent is
  // told where it went rather than being left showing a section it is no longer
  // in. Without it the move looks exactly like a delete.
  onReclassified?: (kind: EntityKind, id: string) => void;
  module?: ModuleDetail | null;
}) {
  const scope: EntityScope = scopeProp ?? { kind: "world", id: wid };
  const [items, setItems] = useState<EntitySummary[]>([]);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null); // entity id, or null = new
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [keys, setKeys] = useState("");
  const fieldSpecs = ENTITY_FIELDS[kind];
  const [fields, setFields] = useState<Record<string, string>>({});
  // The declared fields as LOADED. A save sends the difference against this,
  // never the whole set — see `changedFields`.
  const [loadedFields, setLoadedFields] = useState<Record<string, string>>({});
  // The candidates every `ref` field on this kind can offer, fetched once for
  // the union of their kinds and filtered per field below — `holder` and
  // `headquarters` both want locations, and asking twice would be two requests
  // for one answer.
  const [refOpts, setRefOpts] = useState<RecordRef[]>([]);
  // Whether `refOpts` is the whole truth. False while the listings are in
  // flight and after any of them failed, and that is the only thing that
  // entitles the UI to call an unresolved ref *deleted*.
  const [refOptsComplete, setRefOptsComplete] = useState(false);
  // Token for the in-flight candidate fetch, so a slow earlier response cannot
  // land on top of a newer one — the same guard `PCEditor` uses for its lock
  // lookup, and for the same reason.
  const refReq = useRef(0);
  // The same guard for the record READS. `select` and the image shelf both
  // await and then write view state, and this editor stays mounted across a
  // world-to-world navigation — so without a token the previous scope's record
  // lands under the new one, where Save and Delete act on the new `scope`.
  // Bumped by each read and by the scope change below.
  const readReq = useRef(0);
  const [owners, setOwners] = useState<string[]>([]);          // selected owner refs (lore only)
  const [secrecy, setSecrecy] = useState<Secrecy>("public");    // audience gate (#49)
  const [sdPrompt, setSdPrompt] = useState("");                 // suggested SD prompt, absorb-set only
  const [ownerOpts, setOwnerOpts] = useState<RecordRef[]>([]); // candidates for the picker
  const [tokenCost, setTokenCost] = useState<number | null>(null); // selected record's prompt cost
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing entries open read-only
  const [error, setError] = useState<string | null>(null);
  // The rev of the record as loaded, echoed back on save so a write cannot
  // land on top of an edit made outside the app (#35); `stale` holds the
  // refusal, with the on-disk rev an overwrite would have to be based on.
  const [rev, setRev] = useState<string | null>(null);
  // `to` is set only when the refusal came from a reclassify, and it is what
  // makes "do it anyway" repeat the right write rather than turning a move into
  // a save.
  const [stale, setStale] = useState<{ rev: string | null; to?: EntityKind } | null>(null);
  // `description` is undefined until the listing says otherwise, and that is
  // load-bearing: undefined means never reviewed, "" means reviewed and
  // deliberately undescribed. `described` from the server is what separates them.
  const [images, setImages] = useState<{ name: string; v: string; description?: string }[]>([]);
  const [contentPreview, setContentPreview] = useState<ModuleContentEntry | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const shelfFileRef = useRef<HTMLInputElement>(null);
  const label = KIND_LABELS[kind];

  const reload = useCallback(() => api.listEntities(scope, kind).then(setItems),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [wid, kind, scope.kind, scope.id]);
  useEffect(() => {
    readReq.current += 1;   // discard a record read still out for the old scope
    reload();
    resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, kind, scope.kind, scope.id]);

  useEffect(() => {
    if (kind === "lore") loreOwnerOptions(scope).then(setOwnerOpts);
  }, [wid, kind]);

  useEffect(() => {
    // Cleared FIRST, and every response checked against the request that is
    // current when it lands. Navigating between two worlds can keep this
    // editor mounted, and without both halves the picker offers the previous
    // scope's records until the new listing settles — or permanently, if the
    // older request resolves second. Picking one of those saves a ref into a
    // scope where it names nothing, and since existence is deliberately not
    // validated the backend takes it.
    setRefOpts([]);
    setRefOptsComplete(false);
    const kinds = refKindsFor(fieldSpecs);
    // Guarded rather than unconditional: a kind with no ref fields (lore, and
    // locations, which has three text fields and none of these) would
    // otherwise pay a listing request per record kind on every mount for a
    // picker it never renders.
    if (!kinds.length) { setRefOptsComplete(true); return; }
    const req = ++refReq.current;
    refOptions(scope, kinds)
      .then(({ options, failed }) => {
        if (req !== refReq.current) return;
        setRefOpts(options);
        // A partial answer still populates the picker with the kinds that DID
        // load — losing three of a `holder`'s four kinds to one bad request is
        // a worse picker for no reason — but it is not complete, so nothing
        // built on it may call a ref dead.
        setRefOptsComplete(failed.length === 0);
      })
      .catch(() => { if (req === refReq.current) setRefOpts([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, kind, scope.kind, scope.id]);

  /** The candidates one field may name — the same filter the form's picker
   *  applies. The sidebar has to use it too: resolving a value against the
   *  WHOLE union let a `leader` holding `locations:realm` resolve off the
   *  locations fetched for `headquarters` and render as an ordinary clickable
   *  chip, so the read view called a malformed relationship valid while the
   *  form, one click away, correctly showed it unresolved. */
  const optionsFor = useCallback(
    (spec: EntityFieldSpec) => refOpts.filter((o) => spec.kinds?.includes(o.kind)),
    [refOpts],
  );

  /** The record one of `spec`'s stored refs names, or null when nothing this
   *  field may name answers to it — deleted, out of scope, its listing failed,
   *  or (as above) a kind this field does not accept. Null is the interesting
   *  answer: it is what the sidebar renders as a dangling chip rather than
   *  dropping (#222). */
  const resolveRef = useCallback(
    (spec: EntityFieldSpec, ref: string) => {
      const opts = optionsFor(spec);
      const hit = opts.find((o) => o.ref === ref);
      return hit ? { ...hit, label: displayLabels(opts).get(hit.ref) ?? hit.label } : null;
    },
    [optionsFor],
  );
  // Which of the two stories an unresolved ref gets. Reading `refOptsComplete`
  // rather than assuming the worst is the whole point: only a listing that
  // actually arrived can say a record is gone.
  const unresolvedHint = refOptsComplete ? DANGLING_HINT : UNLOADED_HINT;

  const ownerLabel = useCallback(
    (ref: string) => ownerOpts.find((o) => o.ref === ref)?.label ?? ref,
    [ownerOpts],
  );

  // inbound navigation from an owner editor: open an entry, or start a new pre-owned entry.
  // Clear it via onNavConsumed so it doesn't leak into later manual "+ New" / re-entry.
  useEffect(() => {
    if (!nav) return;
    if (nav.focusEntry) {
      select(nav.focusEntry);
    } else {
      setEditing(null);
      setName("");
      setBody("");
      setKeys("");
      setFields({});
      setLoadedFields({});
      setOwners(nav.newOwner ? [nav.newOwner] : []);
      setSecrecy("public");
      setMode("edit");
      setContentPreview(null);
    }
    onNavConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav]);

  const reloadImages = useCallback((id: string) => {
    const req = readReq.current;   // whichever select or refresh asked for these
    api.listEntityImages(scope, kind, id)
      .then((imgs) => { if (req === readReq.current) setImages(imgs.map((i) => ({
        name: i.name, v: i.v,
        description: i.described ? (i.description ?? "") : undefined,
      }))); })
      .catch(() => { if (req === readReq.current) setImages([]); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, scope.kind, scope.id]);

  function resetForm() {
    setEditing(null);
    setRev(null);
    setStale(null);
    setName("");
    setBody("");
    setKeys("");
    setFields({});
    setLoadedFields({});
    setOwners([]); // manual "+ New" / post-save: always world-level, never a stale nav owner
    setSecrecy("public");
    setSdPrompt("");
    setTokenCost(null);
    setImages([]);
    setContentPreview(null);
    setWizardOpen(false);
    setMode("edit"); // a brand-new entry goes straight to the form
  }

  async function select(id: string) {
    setError(null);
    setStale(null);
    setContentPreview(null);
    setWizardOpen(false);
    const req = ++readReq.current;
    const e = await api.readEntity(scope, kind, id);
    if (req !== readReq.current) return;   // the scope moved on, or a later select won
    setEditing(id);
    setRev(e.rev);
    setName(e.meta.name);
    setBody(e.body);
    setKeys(e.meta.keys ?? "");
    const loaded = Object.fromEntries(
      fieldSpecs.map((f) => [f.key, String((e.meta as any)[f.key] ?? "")]));
    setFields(loaded);
    setLoadedFields(loaded);
    setOwners((e.meta.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean));
    setSecrecy(asSecrecy(e.meta.secrecy));
    setSdPrompt(e.meta.sd_prompt ?? "");
    setTokenCost(typeof e.tokens === "number" ? e.tokens : null);
    setMode("view");
    reloadImages(id);
  }

  async function selectContent(id: string) {
    if (!module) return;
    setError(null);
    const entry = await api.readModuleContent(module.id, kind, id);
    setContentPreview(entry);
    setEditing(null);
    setMode("view");
  }

  async function instantiate() {
    if (!module || !contentPreview) return;
    setError(null);
    try {
      const { id } = await api.instantiateContent(scope, kind, module.id, contentPreview.id);
      setContentPreview(null);
      await reload();
      await select(id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  /** `base` is the rev the write claims to be replacing. Normally the one this
   *  editor loaded; on an explicit overwrite, the one the 409 reported, which
   *  is how "keep mine anyway" stays a deliberate second click rather than a
   *  silent retry without the precondition. */
  async function save(base: string | null = rev) {
    if (!name.trim()) return;
    setError(null);
    setStale(null);
    const ownerStr = owners.join(", ");
    // Only what the user actually touched. Sending the whole set re-submitted
    // every stored value on every save, which turned any value the save
    // boundary refuses into a record that could not be edited at all — and a
    // key this table has newly CLAIMED can hold one: `holder` was unrecognised
    // frontmatter before ref fields existed, so a world where somebody hand-
    // wrote `holder: Mara` would have had an unrelated body edit rejected until
    // they noticed and cleared it. A field nobody edited is left exactly as it
    // was found, which is what it did before this table named the key.
    const patch = changedFields(fields, loadedFields);
    try {
      if (editing) {
        await api.updateEntity(scope, kind, editing,
          { name, body, keys, owners: ownerStr,
            secrecy, ...(Object.keys(patch).length ? { fields: patch } : {}),
            ...(base ? { rev: base } : {}) });
        await reload();
        await select(editing); // back to the read-only view
      } else {
        await api.createEntity(scope, kind,
          { name, body, keys, owners: ownerStr,
            secrecy, ...(Object.keys(patch).length ? { fields: patch } : {}) });
        await reload();
        resetForm();
      }
    } catch (err: any) {
      if (err instanceof ApiError && err.kind === "stale_record") {
        // The form keeps the user's text; nothing is discarded without a click.
        setStale({ rev: (err.body?.rev as string | null) ?? null });
        return;
      }
      setError(err.detail ?? String(err));
    }
  }

  async function discardAndReload() {
    setStale(null);
    if (!editing) return;
    await reload();
    try {
      await select(editing);
    } catch {
      resetForm(); // the record is gone from disk entirely
    }
  }

  /** Move the selected record to another generic kind (#119).
   *
   *  `askReclassify` is the confirmed entry point, and its wording says the two
   *  parts the user cannot see: how far the move reaches, and (leaving
   *  `locations`) that scenes set here stop showing a setting. The confirm is
   *  deliberately not in HERE, so the stale banner's "Reclassify anyway" --
   *  already a deliberate second click -- does not ask the same question twice.
   *
   *  It carries the same `rev` a save does: a reclassify moves the very text
   *  the editor is showing, so a record rewritten elsewhere in the meantime is
   *  exactly what the precondition exists to refuse. The refusal reuses the
   *  shared banner, relabelled, because "Overwrite with mine" would name a
   *  write this is not about to make.
   */
  function askReclassify(to: EntityKind) {
    if (!editing || !to) return;
    const where = scope.kind === "world"
      ? " Every campaign of this world follows it."
      : " The world keeps its own copy under the old kind.";
    // The one loss worth naming before the click, and it only happens in this
    // direction: a scene's location history stores bare ids with no kind beside
    // them, so nothing can follow the record out of `locations`. The history
    // stays as the play left it; what goes is the setting block the prompt
    // builds from the record.
    const leaving = kind === "locations"
      ? " Scenes set here keep their history, but will no longer show a setting."
      : "";
    if (window.confirm(`Reclassify '${name}' as a ${KIND_LABELS[to]}?${where}${leaving}`)) {
      void reclassify(to);
    }
  }

  async function reclassify(to: EntityKind, base: string | null = rev) {
    if (!editing || !to) return;
    setError(null);
    setStale(null);
    try {
      const { id } = await api.reclassifyEntity(scope, kind, editing, to, base);
      resetForm();
      await reload();
      onReclassified?.(to, id);
    } catch (err) {
      if (err instanceof ApiError && err.kind === "stale_record") {
        setStale({ rev: (err.body?.rev as string | null) ?? null, to });
        return;
      }
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  }

  async function remove(e: EntitySummary) {
    if (!window.confirm(`Delete '${e.name}'?`)) return;
    await api.deleteEntity(scope, kind, e.id);
    if (editing === e.id) resetForm();
    await reload();
  }

  // ---- location images shelf (the primary image is the asset named "avatar") ----
  // ?v= names the exact content state, so the browser caches these immutable;
  // uploads/promotes refresh the tokens via reloadImages/reload.
  const hasPrimary = images.some((i) => i.name === "avatar");
  const galleryNames = images
    .map((i) => i.name)
    .filter((n) => n.startsWith("gallery_"))
    .sort((a, b) => Number(a.slice("gallery_".length)) - Number(b.slice("gallery_".length)));
  const imgSrc = (n: string) => {
    const base = api.entityImageUrl(scope, kind, editing ?? "", n);
    const v = images.find((i) => i.name === n)?.v;
    return v ? `${base}?v=${v}` : base;
  };

  async function describeImage(name: string, description: string) {
    if (!editing) return;
    await api.setEntityImageDescription(scope, kind, editing, name, description);
    reloadImages(editing);
  }

  async function draftDescription(name: string): Promise<string> {
    if (!editing) return "";
    return (await api.draftEntityImageDescription(wid, kind, editing, name)).description;
  }

  async function promoteImage(name: string) {
    if (!editing) return;
    setError(null);
    try {
      await api.promoteEntityImage(scope, kind, editing, name);
      reloadImages(editing);
      await reload();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onShelfAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !editing) return;
    setError(null);
    const next = hasPrimary
      ? `gallery_${galleryNames.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0) + 1}`
      : "avatar";
    try {
      await api.putEntityImage(scope, kind, editing, next, file);
      reloadImages(editing);
      await reload();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  const keyList = keys.split(",").map((k) => k.trim()).filter(Boolean);
  // How often that cost is actually paid, which is the whole reason a raw
  // number needs a sentence next to it. The branches mirror the activation
  // rules in `context.world_state`: gm-only drops the body before any other
  // rule, then owners gate the entry, keys decide the turns, and a keyless
  // LOCATION is the exception to the rest — it never joins world info at all
  // and is charged only as the scene's current setting.
  const costHint =
    secrecy === "gm-only"
      ? "Never charged: a GM-only body does not reach the prompt at all."
      : kind === "locations"
        ? keyList.length > 0
          ? "Charged when these keys activate it, or when it is the scene's setting."
          : "Charged only when it is the scene's current setting."
        : owners.length > 0
          ? keyList.length > 0
            ? "Charged when an owner is in the scene and these keys hit."
            : "Charged on every turn an owner is in the scene."
          : keyList.length > 0
            ? "Charged on the turns these keys activate the entry."
            : "Always-on: charged on every turn of a scene it reaches.";
  // Beside the title in both header layouts, so it is written once rather than
  // in the plain-<h3> branch and the image branch separately.
  const heading = (
    <h3>
      {name}
      {tokenCost !== null && (
        <span className={tokenClass(secrecy)} title={tokenTitle(secrecy)}
              aria-label={tokenAria(tokenCost, secrecy)}>
          {tokenLabel(tokenCost)}
        </span>
      )}
    </h3>
  );

  // Group lore rows: "Unowned (world)" first, then one group per distinct owner ref.
  const ownersOf = (e: EntitySummary) => (e.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean);

  // What the rail is currently showing. Name and keys, because those are what
  // a reader knows a record BY -- the id is a slug they never typed and the
  // body is not in the summary.
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? items.filter((e) => (e.name ?? "").toLowerCase().includes(needle)
                       || (e.keys ?? "").toLowerCase().includes(needle))
    : items;

  const groups: { key: string; label: string; rows: EntitySummary[] }[] = [];
  if (kind === "lore") {
    const unowned = shown.filter((e) => ownersOf(e).length === 0);
    if (unowned.length) groups.push({ key: "", label: "Unowned (world)", rows: unowned });
    const seen = new Set<string>();
    for (const e of shown) {
      for (const ref of ownersOf(e)) {
        if (seen.has(ref)) continue;
        seen.add(ref);
        groups.push({ key: ref, label: ownerLabel(ref), rows: shown.filter((x) => ownersOf(x).includes(ref)) });
      }
    }
  }

  const row = (e: EntitySummary) => (
    <button key={e.id}
            className={"row" + (e.has_image ? " loc-row" : "") + (editing === e.id ? " active" : "")}
            onClick={() => select(e.id)}>
      {e.has_image && (
        <img className="loc-row-img" alt=""
             src={`${api.entityImageUrl(scope, kind, e.id, "avatar")}${e.image_v ? `?v=${e.image_v}` : ""}`}
             onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.display = "none"; }} />
      )}
      <span className="row-name">{e.name}</span>
      {asSecrecy(e.secrecy) !== "public" && (
        <span className={`chip secrecy-tag ${asSecrecy(e.secrecy)}`}>
          {SECRECY_LABELS[asSecrecy(e.secrecy)]}
        </span>
      )}
      {kind === "lore" && (
        <span className="owner-stack">
          {ownersOf(e).map((ref) => {
            const o = ownerOpts.find((x) => x.ref === ref);
            return o?.avatar ? (
              <img key={ref} className="owner-stack-img" alt="" title={o.label} src={o.avatar}
                   onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.display = "none"; }} />
            ) : null;
          })}
        </span>
      )}
      {/* The unit is dropped for width -- the rail is a fixed 220px -- so the
          accessible name carries it instead. The row is a button named from
          its contents, so without this a screen reader reads "Salt 1,240" and
          the number could be anything; `title` alone is not announced. */}
      {typeof e.tokens === "number" && (
        <span className={tokenClass(asSecrecy(e.secrecy), "row-tokens")}
              title={tokenTitle(asSecrecy(e.secrecy))}
              aria-label={tokenAria(e.tokens, asSecrecy(e.secrecy))}>
          {e.tokens.toLocaleString()}
        </span>
      )}
    </button>
  );

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New {label}</button>
        {/* Shown once there is enough to lose something in. Below that the
            filter is a control that costs a row and saves nothing. */}
        {items.length > 8 && (
          <>
            <input className="rail-search" type="search" value={query}
                   aria-label={`Search ${kind}`} placeholder={`Search ${kind}…`}
                   onChange={(e) => setQuery(e.target.value)} />
            <div className="rail-count">
              {shown.length === items.length
                ? `${items.length} ${label}${items.length === 1 ? "" : "s"}`
                : `${shown.length} of ${items.length}`}
            </div>
          </>
        )}
        {module && Object.values(module.sheets.sheet_types).some((st) => st.kind === kind) && (
          <button className="subtle" onClick={() => { resetForm(); setWizardOpen(true); }}>
            + New {label} with sheet…
          </button>
        )}
        {kind === "lore"
          ? groups.map((g) => (
              <div key={g.key} className="rail-group">
                <div className="rail-group-head">{g.label}</div>
                {g.rows.map(row)}
              </div>
            ))
          : shown.map(row)}
        {/* Three different answers, and they must not share one line: nothing
            here yet, nothing MATCHING here, and a filter narrowing a real set. */}
        {items.length === 0 && <div className="editor-empty">No {kind} yet.</div>}
        {items.length > 0 && shown.length === 0 && (
          <div className="editor-empty">Nothing matches “{query}”.</div>
        )}
        {module?.content.filter((c) => c.kind === kind).map((c) => (
          <button key={`content-${c.id}`} className="row content-row"
                  onClick={() => selectContent(c.id)}>
            <span className="row-name">{c.name}</span>
            <span className="chip">template</span>
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {stale && (
          <StaleRecordBanner label={label} rev={stale.rev} onReload={discardAndReload}
                             overwriteLabel={stale.to ? "Reclassify anyway" : undefined}
                             onOverwrite={() => (stale.to
                               ? reclassify(stale.to, stale.rev)
                               : save(stale.rev))} />
        )}
        {wizardOpen && module ? (
          <CreationWizard scope={scope} kind={kind} module={module}
                          createRecord={(n) => api.createEntity(scope, kind, { name: n }).then((r) => r.id)}
                          deleteRecord={(id) => api.deleteEntity(scope, kind, id).then(() => {})}
                          onDone={async (id) => { setWizardOpen(false); await reload(); await select(id); }}
                          onCancel={() => setWizardOpen(false)} />
        ) : contentPreview ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{contentPreview.name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{contentPreview.body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="primary" onClick={instantiate}>Instantiate</button>
              </div>
              <div className="side-section">
                <h4>Module</h4>
                <span className="chip on">{module?.manifest.name}</span>
              </div>
            </aside>
          </div>
        ) : mode === "view" && editing ? (
          <div className="detail-view">
            <div className="detail-main">
              {editing && hasPrimary ? (
                <div className="loc-head">
                  <img className="loc-head-img" alt={`${name} primary`} src={imgSrc("avatar")}
                       onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                  {heading}
                </div>
              ) : (
                heading
              )}
              {editing && (
                <>
                  <div className="section-label">Images</div>
                  <div className="images-shelf wide">
                    {hasPrimary ? (
                      <figure className="shelf-tile avatar-tile">
                        <a href={imgSrc("avatar")} target="_blank" rel="noreferrer">
                          <img alt="primary" src={imgSrc("avatar")} />
                        </a>
                        <figcaption>primary</figcaption>
                        <ImageDescriptionField
                          key={`${editing}:avatar`}
                          name="avatar"
                          value={images.find((i) => i.name === "avatar")?.description}
                          onSave={(d) => describeImage("avatar", d)}
                          onDraft={scope.kind === "world" ? () => draftDescription("avatar") : undefined} />
                      </figure>
                    ) : (
                      <div className="shelf-tile shelf-empty">no image</div>
                    )}
                    {galleryNames.map((n) => (
                      <div className="shelf-tile" key={n}>
                        <a href={imgSrc(n)} target="_blank" rel="noreferrer"><img alt={n} src={imgSrc(n)} /></a>
                        <button className="shelf-promote" onClick={() => promoteImage(n)}>Set as primary</button>
                        <ImageDescriptionField
                          key={`${editing}:${n}`}
                          name={n} value={images.find((i) => i.name === n)?.description}
                          onSave={(d) => describeImage(n, d)}
                          onDraft={scope.kind === "world" ? () => draftDescription(n) : undefined} />
                      </div>
                    ))}
                    <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                    <input ref={shelfFileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" hidden
                           aria-label="Add image" onChange={onShelfAdd} />
                  </div>
                </>
              )}
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              {/* Keyed on the record, so switching rows remounts rather than
                  showing the previous record's library state while the new one
                  loads. The two are opposites and never both apply: a campaign
                  puts a record INTO the library, a world takes one out. */}
              {editing && (scope.kind === "campaign" ? (
                <LibraryPanel key={`${scope.id}:${kind}:${editing}`}
                              cid={scope.id} kind={kind} id={editing}
                              onMoved={() => { void reload(); }} />
              ) : (
                <DemotePanel key={`${scope.id}:${kind}:${editing}`}
                             wid={scope.id} kind={kind} id={editing}
                             onDemoted={() => { void reload(); resetForm(); }} />
              ))}
              <div className="side-section">
                <h4>Keys</h4>
                {keyList.length > 0
                  ? <div className="chips">{keyList.map((k) => <span key={k} className="chip on">{k}</span>)}</div>
                  : <div className="field-hint">always-on</div>}
              </div>
              <div className="side-section">
                <h4>Secrecy</h4>
                <div className="chips">
                  <span className={`chip on secrecy-tag ${secrecy}`}>{SECRECY_LABELS[secrecy]}</span>
                </div>
                <div className="field-hint">{SECRECY_HINTS[secrecy]}</div>
              </div>
              {tokenCost !== null && (
                <div className="side-section">
                  <h4>Context cost</h4>
                  <div className="chips"><span className="chip on">{tokenLabel(tokenCost)}</span></div>
                  <div className="field-hint">{costHint}</div>
                </div>
              )}
              {fieldSpecs.some((f) => f.widget !== "ref" && fields[f.key]) && (
                <div className="side-section">
                  <h4>Details</h4>
                  <div className="chips">
                    {fieldSpecs.filter((f) => f.widget !== "ref" && fields[f.key]).map((f) => (
                      <span key={f.key} className="chip on">{f.label}: {fields[f.key]}</span>
                    ))}
                  </div>
                </div>
              )}
              {/* One section per ref field rather than a row inside Details:
                  these are records, and the list/detail contract says metadata
                  referencing another record renders as a clickable chip under
                  its own heading — the same shape lore's Owners has. */}
              {fieldSpecs.filter((f) => f.widget === "ref" && fields[f.key]).map((f) => (
                <div className="side-section" key={f.key}>
                  <h4>{f.label}</h4>
                  <div className="chips">
                    {parseRefs(fields[f.key]).map((ref) => {
                      const hit = resolveRef(f, ref);
                      return hit === null ? (
                        // Not a button: there is nothing to navigate to. It is
                        // shown at all because a delete does not scrub the refs
                        // that name the record (#222), and a field that quietly
                        // rendered nothing would read as one nobody filled in.
                        <span key={ref} className="chip dangling" title={unresolvedHint}>{ref}</span>
                      ) : (
                        <button key={ref} className="chip owner-chip"
                                onClick={() => onOpenOwner?.(ref)}>
                          <Portrait src={hit.avatar ?? null} name={hit.label} />
                          {hit.label}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
              {sdPrompt && (
                <div className="side-section">
                  <h4>Image prompt</h4>
                  <div className="field-hint">{sdPrompt}</div>
                </div>
              )}
              {kind === "lore" && (
                <div className="side-section">
                  <h4>Owners</h4>
                  {owners.length > 0 ? (
                    <div className="chips">
                      {owners.map((ref) => (
                        <button key={ref} className="chip owner-chip" onClick={() => onOpenOwner?.(ref)}>
                          <Portrait src={ownerOpts.find((x) => x.ref === ref)?.avatar ?? null}
                                    name={ownerLabel(ref)} />
                          {ownerLabel(ref)}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="field-hint">world-level</div>
                  )}
                </div>
              )}
              {kind === "locations" && editing && onOpenLore && (
                <OwnedLorePanel
                  scope={scope}
                  ownerRef={`locations:${editing}`}
                  onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                  onNewEntry={() => onOpenLore({ newOwner: `locations:${editing}` })}
                />
              )}
              {kind === "groups" && scope.kind === "campaign" && editing && (
                <GroupStatePanel cid={scope.id} gid={editing} />
              )}
              {/* The record keeps its id across the move (#119), so everything
                  filed against it -- its images, its sheet, its pins, its undo
                  history -- comes with it. A select rather than a row of
                  buttons: there are four destinations and this is a rare,
                  deliberate correction, not a control to make prominent. */}
              <div className="side-section">
                <h4>Reclassify</h4>
                <select aria-label="Reclassify as" value=""
                        onChange={(e) => askReclassify(e.target.value as EntityKind)}>
                  <option value="">Reclassify as…</option>
                  {ENTITY_KINDS.filter((k) => k !== kind).map((k) => (
                    <option key={k} value={k}>{KIND_LABELS[k]}</option>
                  ))}
                </select>
                <div className="field-hint">
                  {scope.kind === "world"
                    ? "Moves this record for every campaign of this world."
                    : "Moves this campaign's copy only; the world keeps its own."}
                </div>
              </div>
              {module && editing && (
                <SheetPanel scope={scope} module={module} kind={kind} eid={editing}
                            onOpenRef={(kind, id) => onOpenOwner?.(`${kind}:${id}`)} />
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{editing ? `Edit ${label}` : `New ${label}`}</h3>
            <Field label="Name">
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Body">
              <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={10} />
            </Field>
            <Field label="Keys" hint="comma-separated activation triggers; blank = always-on">
              <input type="text" value={keys} onChange={(e) => setKeys(e.target.value)} />
            </Field>
            {/* Real radio inputs, like the Owners checkboxes above: a native
                group gets arrow-key navigation and roving focus for free,
                which a row of buttons wearing role="radio" does not. */}
            <Field label="Secrecy" hint={SECRECY_HINTS[secrecy]}>
              <div className="secrecy-picker" role="radiogroup" aria-label="Secrecy">
                {SECRECY_LEVELS.map((level) => (
                  <label key={level} className="secrecy-option">
                    <input type="radio" name="secrecy" value={level}
                           checked={secrecy === level}
                           onChange={() => setSecrecy(level)} />
                    {SECRECY_LABELS[level]}
                  </label>
                ))}
              </div>
            </Field>
            {fieldSpecs.map((f) => (f.widget === "ref" ? (
              <RefField key={f.key} spec={f} options={optionsFor(f)}
                        value={fields[f.key] ?? ""} unresolvedHint={unresolvedHint}
                        optionsComplete={refOptsComplete}
                        onChange={(v) => setFields({ ...fields, [f.key]: v })} />
            ) : (
              <Field key={f.key} label={f.label}>
                <input type="text" value={fields[f.key] ?? ""}
                       onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />
              </Field>
            )))}
            {kind === "lore" && (
              <Field label="Owners" hint="lore activates only when an owner is in the scene; none = world-level">
                <div className="chips owner-picker">
                  {ownerOpts.map((o) => (
                    <label key={o.ref} className="owner-option">
                      <input
                        type="checkbox"
                        aria-label={o.label}
                        checked={owners.includes(o.ref)}
                        onChange={(e) =>
                          setOwners(e.target.checked ? [...owners, o.ref] : owners.filter((r) => r !== o.ref))
                        }
                      />
                      {o.label}
                    </label>
                  ))}
                  {ownerOpts.length === 0 && <span className="field-hint">No characters, PCs, or locations yet.</span>}
                </div>
              </Field>
            )}
            <div className="form-actions">
              {editing && <button className="subtle" onClick={() => remove(items.find((x) => x.id === editing)!)}>Delete</button>}
              {editing && <button className="subtle" onClick={() => select(editing)}>Cancel</button>}
              <button className="primary" onClick={() => save()} disabled={!name.trim()}>
                {editing ? "Save" : `Create ${label}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
