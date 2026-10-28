import { MarkdownArtProvider } from "../markdown/MarkdownArtProvider";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Markdown from "react-markdown";
import { markdownImageComponents } from "../markdown/MarkdownImage";
import remarkGfm from "remark-gfm";
import { ApiError, api, ENTITY_FIELDS, type EntityFieldSpec, type EntityKind, type EntityScope, type EntitySummary, type GroupMembershipContext, type GroupRefBinding, type ModuleContentEntry, type ModuleDetail, type Secrecy } from "../api/client";
import { errorText } from "../api/errors";
import { onArtChanged } from "../appEvents";
import { RecordArtViewer } from "./art/RecordArtEntry";
import { useRecordArt } from "./art/useRecordArt";
import CreationWizard from "./CreationWizard";
import { StaleRecordBanner } from "./StaleRecordBanner";
import { activationKeys, changedFields, parseRefs } from "./entityEditor/fields";
import { KIND_LABELS, asSecrecy } from "./entityEditor/presentation";
import { EntityFieldControl } from "./entityEditor/EntityFields";
import { useEntityOptions } from "./entityEditor/useEntityOptions";
import { EntityBrowse } from "./entityEditor/EntityBrowse";
import { EntityDetails } from "./entityEditor/EntityDetails";
import { EntityForm } from "./entityEditor/EntityForm";

export { KIND_LABELS } from "./entityEditor/presentation";

export function EntityEditor({ wid, kind, scope: scopeProp, selected, isNew = false, newOwner, sectionPath, newHref, recordHref, onOpenOwner, onReclassified, module = null }: {
  wid: string;
  kind: EntityKind;
  scope?: EntityScope;
  /** The record the URL names, or null for the section's own screen. That
   *  screen is the browse grid, unless `isNew` says it is the blank form. */
  selected?: string | null | undefined;
  /** The blank new-record form, asked for by `?new=1` (or `?owner=` on Lore).
   *  With no record named, this is what separates the form from the grid. */
  isNew?: boolean;
  /** An owner to pre-fill a blank form with (`characters:seraphine`), from
   *  `?owner=`. Only ever set when `selected` is null: it belongs to the form,
   *  not to a record that has owners of its own. */
  newOwner?: string;
  /** This section's own address: the browse grid, and where Back goes. */
  sectionPath: string;
  /** The blank form's address (`?new=1`), for `+ New`. */
  newHref: string;
  /** ...and one record's, for a rail row. A CALLBACK rather than a string this
   *  file concatenates onto `sectionPath`: `sectionHref` is the only thing
   *  allowed to build these paths, and an id containing a slash or a space is
   *  exactly what hand-joining gets wrong. */
  recordHref: (rid: string) => string;
  onOpenOwner?: (ref: string) => void;
  // A reclassified record leaves this editor's list entirely, so the parent is
  // told where it went rather than being left showing a section it is no longer
  // in. Without it the move looks exactly like a delete.
  onReclassified?: (kind: EntityKind, id: string) => void;
  module?: ModuleDetail | null;
}) {
  const scope: EntityScope = scopeProp ?? { kind: "world", id: wid };
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const recordTab = params.get("tab") === "art" ? "art" : "details";
  const [loadedArtOwner, setLoadedArtOwner] = useState<string | null>(null);
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
  // The guard for the record READS. `select` and the image shelf both
  // await and then write view state, and this editor stays mounted across a
  // world-to-world navigation — so without a token the previous scope's record
  // lands under the new one, where Save and Delete act on the new `scope`.
  // Bumped by each read and by the scope change below.
  const readReq = useRef(0);
  // Bumped when the scope/kind changes or its owner unmounts. An adopt request outlives the
  // click that sent it, and what it does on completion -- reload the rail, open
  // a record, set the note or the error -- is about the scope it was sent from.
  const scopeGen = useRef(0);
  // Initial loads, writes and shared-art invalidation all feed the same rail.
  // Order them together so a pre-write response cannot undo fresh thumbnails.
  const summaryReads = useRef(0);
  // Every completion that can reload, reset, or navigate must still belong to
  // the view that started it. A record change matters within one scope too.
  const moduleIdentity = useRef(module?.id);
  moduleIdentity.current = module?.id;
  const captureViewGuard = () => {
    const reading = readReq.current;
    const generation = scopeGen.current;
    const moduleId = moduleIdentity.current;
    return () => reading === readReq.current && generation === scopeGen.current
      && moduleId === moduleIdentity.current;
  };
  const [owners, setOwners] = useState<string[]>([]);          // selected owner refs (lore only)
  const [secrecy, setSecrecy] = useState<Secrecy>("public");    // audience gate (#49)
  const [sdPrompt, setSdPrompt] = useState("");                 // suggested SD prompt, absorb-set only
  const [tokenCost, setTokenCost] = useState<number | null>(null); // selected record's prompt cost
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing entries open read-only
  const [error, setError] = useState<string | null>(null);
  // A committed create/remove owns one destination, including its summary
  // readback. Only that route transition may inherit the notice; a later
  // record, New form, scope or module must retire it like any other request.
  const summaryNavigation = useRef<{
    destination: string | null; read: number; generation: number;
    moduleId: string | undefined; arrived: boolean; message: string | null;
  } | null>(null);
  // The rev of the record as loaded, echoed back on save so a write cannot
  // land on top of an edit made outside the app (#35); `stale` holds the
  // refusal, with the on-disk rev an overwrite would have to be based on.
  const [rev, setRev] = useState<string | null>(null);
  // Consent belongs to this loaded record/rev. Scope keys invalidate it on
  // the first render of navigation, before an effect or late reply can run.
  // Never infer foreign identity from actor options (spec 07).
  const [membership, setMembership] = useState<{
    key: string; rev: string; context: GroupMembershipContext;
  } | null>(null);
  const [bindings, setBindings] = useState<GroupRefBinding[]>([]);
  const [identityConflict, setIdentityConflict] = useState(false);
  const recordKey = JSON.stringify([scope.kind, scope.id, kind, selected ?? editing]);
  const groupContext = membership?.key === recordKey && membership.rev === rev ? membership.context : null;
  const requiresGroupContext = kind === "groups" && scope.kind === "campaign" && editing !== null;
  const groupNeedsReload = requiresGroupContext && (!groupContext || identityConflict);
  const unresolvedFor = (spec: EntityFieldSpec): string[] => {
    if (kind !== "groups" || scope.kind !== "campaign" || groupContext?.layer !== "world") return [];
    if (spec.key === "members") return groupContext.unresolved_members;
    // Every guarded Leader slot, not only the one display names: a legacy
    // line holding two Leader tokens is refused on either (spec 07 7.7).
    if (spec.key === "leader") return groupContext.unresolved_leaders
      ?? (groupContext.unresolved_leader ? [groupContext.unresolved_leader] : []);
    return [];
  };
  // `to` is set only when the refusal came from a reclassify, and it is what
  // makes "do it anyway" repeat the right write rather than turning a move into
  // a save.
  const [stale, setStale] = useState<{ rev: string | null; to?: EntityKind } | null>(null);
  const [contentPreview, setContentPreview] = useState<ModuleContentEntry | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  // Whether the form's Activation disclosure is open. Its contents are drawn
  // only while it is: they include a second picker over every character and PC,
  // and a closed one that still rendered them would put two checkboxes named
  // for the same actor on every lore form -- one under Owners, one here.
  const [activationOpen, setActivationOpen] = useState(false);
  // What the record's stashed SillyTavern settings would still add (spec 4.2):
  // null when there is no stash, nothing pending, or the preview did not load.
  const [adopt, setAdopt] = useState<
    { fields: Record<string, string>; unmapped: string[]; inherited?: boolean } | null>(null);
  // The bulk button's one-line outcome.
  const [adoptAllNote, setAdoptAllNote] = useState<string | null>(null);
  const [pendingAdoptions, setPendingAdoptions] = useState<number | null>(null);
  // An adopt request is out (this record's or the whole library's). Both
  // buttons wait on it: a second click would send the same write twice and, for
  // the bulk one, report the second run's "0 entries" over the first's outcome.
  const [adoptBusy, setAdoptBusy] = useState(false);
  const label = KIND_LABELS[kind];

  const reload = useCallback(async (fresh = true) => {
    const gen = scopeGen.current, sequence = ++summaryReads.current;
    try {
      const rows = await (fresh ? api.listEntities(scope, kind, true) : api.listEntities(scope, kind));
      if (gen === scopeGen.current && sequence === summaryReads.current) {
        setItems(rows);
        // The bulk action spans every entity kind and only campaign-owned
        // records. The server owns both that scope and the import mapping.
        if (kind === "lore") await api.previewAdoptStAll(scope)
          .then((r) => {
            if (gen === scopeGen.current && sequence === summaryReads.current) setPendingAdoptions(r.pending);
          }).catch(() => {
            // An unknown offer cannot license repeating the last sweep. Keep
            // browsing and its successful outcome, but retire the stale count.
            if (gen === scopeGen.current && sequence === summaryReads.current) setPendingAdoptions(null);
          });
      }
    } catch (err) {
      if (gen === scopeGen.current && sequence === summaryReads.current) throw err;
    }
  },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [wid, kind, scope.kind, scope.id]);
  useEffect(() => {
    readReq.current += 1;   // discard a record read still out for the old scope
    scopeGen.current += 1;  // ...and an adopt still out for it
    setAdoptBusy(false);    // that request no longer holds this scope's buttons
    setPendingAdoptions(null);
    const gen = scopeGen.current;
    void reload(false).catch((err: unknown) => { if (gen === scopeGen.current) setError(errorText(err)); });
    resetForm();
    return () => {
      // This route can unmount entirely, rather than merely receive new props.
      // Retire writes too: a late create must not navigate back into this page.
      readReq.current += 1;
      scopeGen.current += 1;
      summaryReads.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, kind, scope.kind, scope.id]);

  const wantActors = activationOpen || (loadedFields.known_by ?? "").trim() !== "";
  const candidates = useEntityOptions({ scopeKind: scope.kind, scopeId: scope.id, kind, fieldSpecs, wantActors });
  const { ownerOpts, ownerById, ownerLabel, optionsFor, completeFor, choiceOpts } = candidates;

  // Whatever the route names.
  //
  // The token is bumped on EVERY change, the null one included. Without that,
  // navigating /items/a -> /items resets the form and then A's slower read
  // lands and reopens A at an address that names no record.
  useEffect(() => {
    const req = ++readReq.current;
    const summary = summaryNavigation.current;
    if (summary) {
      if (!summary.arrived && summary.read === req - 1 && summary.generation === scopeGen.current
          && summary.moduleId === moduleIdentity.current && summary.destination === (selected ?? null)
          && !isNew && !newOwner) {
        summary.read = req;
        summary.arrived = true;
      } else {
        summaryNavigation.current = null;
        if (summary.message !== null) setError(null);
      }
    }
    if (selected) {
      // A bookmarked/reload address is exactly what this branch made
      // possible -- so a read that 404s here has to say so rather than
      // leaving a blank "new" form up with nothing to explain it.
      void select(selected, req)
        .catch((err: unknown) => { if (req === readReq.current) setError(errorText(err)); });
    } else resetForm(newOwner);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, isNew, newOwner, kind, scope.kind, scope.id]);

  function resetForm(owner = "") {
    setEditing(null);
    setRev(null);
    setMembership(null);
    setBindings([]);
    setIdentityConflict(false);
    setStale(null);
    setName("");
    setBody("");
    setKeys("");
    setFields({});
    setLoadedFields({});
    setOwners(owner ? [owner] : []);
    setSecrecy("public");
    setSdPrompt("");
    setTokenCost(null);
    setLoadedArtOwner(null);
    setContentPreview(null);
    setWizardOpen(false);
    setAdopt(null);
    setAdoptAllNote(null);   // a status about the last scope or record is not about this one
    setActivationOpen(false);
    setMode("edit"); // a brand-new entry goes straight to the form
  }

  async function select(id: string, req = ++readReq.current) {
    setMembership(null);
    setBindings([]);
    setIdentityConflict(false);
    const summary = summaryNavigation.current;
    setError(summary?.destination === id && summary.read === req ? summary.message : null);
    setStale(null);
    setContentPreview(null);
    setWizardOpen(false);
    const e = await api.readEntity(scope, kind, id);
    if (req !== readReq.current) return;   // the scope moved on, or a later select won
    if (id !== editing) setAdoptAllNote(null);   // a different record: the note was about the last
    setEditing(id);
    setRev(e.rev);
    setMembership(e.membership_context ? {
      key: JSON.stringify([scope.kind, scope.id, kind, id]), rev: e.rev, context: e.membership_context,
    } : null);
    setName(e.meta.name);
    setBody(e.body);
    setKeys(e.meta.keys ?? "");
    const loaded = Object.fromEntries(
      [...fieldSpecs.map((f) => f.key), ...activationKeys()]
        .map((k) => {
          const value = e.meta[k];
          if (typeof value === "string") return [k, value];
          if (typeof value === "number" || typeof value === "boolean") return [k, String(value)];
          // Preserve structured frontmatter as JSON instead of an object label.
          return [k, typeof value === "object" && value !== null ? JSON.stringify(value) : ""];
        }));
    setFields(loaded);
    setLoadedFields(loaded);
    // Open when the record already sets something in it: that is what the
    // reader came to see, and what they would otherwise have to go looking for.
    setActivationOpen(activationKeys().some((k) => loaded[k].trim() !== ""));
    setOwners((e.meta.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean));
    setSecrecy(asSecrecy(e.meta.secrecy));
    setSdPrompt(e.meta.sd_prompt ?? "");
    setTokenCost(typeof e.tokens === "number" ? e.tokens : null);
    setMode("view");
    setLoadedArtOwner(JSON.stringify([scope.kind, scope.id, kind, id]));
    // Asked only of a record that carries a stash: nothing else can have
    // anything to adopt, and most records are not imports. Its failure is
    // silence -- the banner is an offer, not something the record needs.
    setAdopt(null);
    const stash = e.meta.st_extensions;
    if (typeof stash === "string" && stash.trim()) {
      api.previewAdoptSt(scope, kind, id)
        .then((p) => {
          if (req === readReq.current) {
            setAdopt(Object.keys(p.fields).length ? p : null);
          }
        })
        .catch(() => undefined);
    }
  }

  /** `reload`, unless the scope has moved on since `gen` was taken: a listing
   *  of the old scope must not repopulate the new scope's rail. */
  async function reloadFor(gen: number) {
    if (gen === scopeGen.current) await reload(true);
  }

  async function applyAdopt() {
    if (!editing || adoptBusy) return;
    const gen = scopeGen.current;
    const viewing = readReq.current;   // unchanged = no other record opened since
    setError(null);
    setAdoptBusy(true);
    try {
      await api.adoptSt(scope, kind, editing);
      await reloadFor(gen);
      // Nothing below is about the screen any more once the scope has changed
      // or another record has been opened: `select` would put this one back.
      if (gen === scopeGen.current && viewing === readReq.current) {
        await select(editing);   // the new values, and a banner with nothing left
      }
    } catch (err) {
      // The same guard as success: an error about this record is noise on
      // another one the reader has opened since.
      if (gen === scopeGen.current && viewing === readReq.current) setError(errorText(err));
    } finally {
      if (gen === scopeGen.current) setAdoptBusy(false);
    }
  }

  async function applyAdoptAll() {
    if (adoptBusy) return;
    const gen = scopeGen.current;
    const viewing = readReq.current;
    setError(null);
    setAdoptAllNote(null);
    setAdoptBusy(true);
    try {
      const r = await api.adoptStAll(scope);
      if (gen !== scopeGen.current) return;   // the outcome is about another scope
      // The committed sweep consumed this offer. A failed list read must not
      // revive it before a fresh preview has confirmed anything still pending.
      setPendingAdoptions(null);
      setAdoptAllNote(`Applied to ${r.applied.length} ${r.applied.length === 1 ? "entry" : "entries"}; `
        + `${r.skipped.length} skipped`);
      await reloadFor(gen);
      // Re-read only a record being VIEWED. Re-selecting one open in the form
      // would reset its fields, body and name to what is on disk and drop the
      // reader into view mode -- discarding an edit nobody saved. (The button is
      // disabled for that case; this is the guard if it ever is not.)
      if (gen === scopeGen.current && viewing === readReq.current
          && editing && mode === "view") await select(editing);
    } catch (err) {
      if (gen === scopeGen.current) setError(errorText(err));
    } finally {
      if (gen === scopeGen.current) setAdoptBusy(false);
    }
  }

  async function selectContent(id: string) {
    if (!module) return;
    // A new preview supersedes any older preview/read even within one scope.
    readReq.current += 1;
    const stillHere = captureViewGuard();
    setError(null);
    try {
      const entry = await api.readModuleContent(module.id, kind, id);
      if (!stillHere()) return;
      setContentPreview(entry);
      setEditing(null);
      setMode("view");
    } catch (err) {
      if (stillHere()) setError(errorText(err));
    }
  }

  async function instantiate() {
    if (!module || !contentPreview) return;
    const stillHere = captureViewGuard();
    setError(null);
    try {
      const { id } = await api.instantiateContent(scope, kind, module.id, contentPreview.id);
      if (!stillHere()) return;
      setContentPreview(null);
      await finishCreation(id, stillHere);
    } catch (err: unknown) {
      if (stillHere()) setError(errorText(err));
    }
  }

  // Record/sheet creation has committed. A failed readback cannot roll it back
  // or strand its URL on the section root. The caller captures ownership before
  // its write starts, including when the wizard eventually invokes onDone.
  async function finishCreation(id: string, stillHere: () => boolean) {
    if (!stillHere()) return;
    let refreshError: string | null = null;
    try { await reload(); }
    catch (err) { refreshError = errorText(err); }
    if (!stillHere()) return;
    if (refreshError !== null) {
      beginSummaryNavigation(id, refreshError);
      setError(refreshError);
    }
    setWizardOpen(false);
    // Navigate rather than `select` directly: the address still names the
    // section root, so `+ New …` (a `Link` to `sectionPath`) would point at
    // the screen already showing and silently do nothing (#follow-up to
    // GreetingEditor's own fix for the same defect). The URL-driven
    // `selected` effect is what actually opens the record read-only.
    void navigate(recordHref(id));
  }

  /** `base` is the rev the write claims to be replacing. Normally the one this
   *  editor loaded; on an explicit overwrite, the one the 409 reported, which
   *  is how "keep mine anyway" stays a deliberate second click rather than a
   *  silent retry without the precondition. */
  async function save(base: string | null = rev) {
    if (!name.trim() || groupNeedsReload) return;
    const stillHere = captureViewGuard();
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
    const reviewed = requiresGroupContext ? bindings.filter((binding) =>
      parseRefs(fields[binding.field]).includes(binding.actor)) : [];
    // An opt-in reviews the unchanged raw field too. Omitting it would make the
    // backend reject the binding as consent for a slot not submitted.
    for (const binding of reviewed) patch[binding.field] = fields[binding.field] ?? "";
    try {
      if (editing) {
        await api.updateEntity(scope, kind, editing,
          { name, body, keys, owners: ownerStr,
            secrecy, ...(Object.keys(patch).length ? { fields: patch } : {}),
            ...(base ? { rev: base } : {}),
            ...(reviewed.length ? { group_ref_bindings: reviewed } : {}) });
        if (!stillHere()) return;
        setBindings([]);
        await reload();
        if (!stillHere()) return;
        await select(editing); // back to the read-only view
      } else {
        const { id } = await api.createEntity(scope, kind,
          { name, body, keys, owners: ownerStr,
            secrecy, ...(Object.keys(patch).length ? { fields: patch } : {}) });
        if (!stillHere()) return;
        await finishCreation(id, stillHere);
      }
    } catch (err: unknown) {
      if (!stillHere()) return;
      if (err instanceof ApiError && err.kind === "group_identity_conflict") {
        setBindings([]);
        setIdentityConflict(true);
        setError(err.detail);
        return;
      }
      if (err instanceof ApiError && err.kind === "stale_record") {
        // The form keeps the user's text; nothing is discarded without a click.
        setStale({ rev: (err.body?.rev as string | null) ?? null });
        return;
      }
      setError(errorText(err));
    }
  }

  async function discardAndReload() {
    const stillHere = captureViewGuard();
    const generation = scopeGen.current;
    setStale(null);
    setBindings([]);
    setMembership(null);
    if (!editing) return;
    try {
      await reload();
    } catch (err) {
      if (stillHere()) setError(errorText(err));
      return;
    }
    if (!stillHere()) return;
    const req = ++readReq.current;
    try {
      await select(editing, req);
    } catch {
      if (req === readReq.current && generation === scopeGen.current) {
        resetForm(); // the record is gone from disk entirely
      }
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
    const stillHere = captureViewGuard();
    setError(null);
    setStale(null);
    try {
      const { id } = await api.reclassifyEntity(scope, kind, editing, to, base);
      if (!stillHere()) return;
      resetForm();
      await reload();
      if (!stillHere()) return;
      onReclassified?.(to, id);
    } catch (err) {
      if (!stillHere()) return;
      if (err instanceof ApiError && err.kind === "stale_record") {
        setStale({ rev: (err.body?.rev as string | null) ?? null, to });
        return;
      }
      setError(err instanceof ApiError ? err.detail : String(err));
    }
  }

  async function cancelEdit() {
    if (!editing) return;
    // select increments readReq itself by default; capture the exact token
    // used by this read so its rejection can still report on this draft.
    const req = ++readReq.current;
    const stillHere = captureViewGuard();
    try { await select(editing, req); }
    catch (err) { if (stillHere()) setError(errorText(err)); }
  }

  async function remove(e: Pick<EntitySummary, "id" | "name">) {
    if (!window.confirm(`Delete '${e.name}'?`)) return;
    const stillHere = captureViewGuard();
    setError(null);
    try { await api.deleteEntity(scope, kind, e.id); }
    catch (err) { if (stillHere()) setError(errorText(err)); return; }
    if (!stillHere()) return;
    if (editing === e.id) {
      // The address still names the record just deleted; leave it for the grid
      // rather than a blank form under a dead id.
      await finishRemoval();
      return;
    }
    try { await reload(); }
    catch (err) { if (stillHere()) setError(errorText(err)); }
  }

  function beginSummaryNavigation(destination: string | null, message: string | null = null) {
    const owner = {
      destination, message, read: readReq.current, generation: scopeGen.current,
      moduleId: moduleIdentity.current, arrived: false,
    };
    summaryNavigation.current = owner;
    return owner;
  }

  async function finishRemoval() {
    const owner = beginSummaryNavigation(null);
    resetForm();
    void navigate(sectionPath);
    try { await reload(); }
    catch (err) {
      // The grid's own route effect advances this owner's read exactly once.
      // Another route change (including grid→New with no selected id) retires
      // the object rather than allowing its old failure onto the new screen.
      if (summaryNavigation.current === owner && owner.read === readReq.current
          && owner.generation === scopeGen.current && owner.moduleId === moduleIdentity.current) {
        owner.message = errorText(err);
        setError(owner.message);
      }
    }
  }

  function refreshSidebar(stillHere: () => boolean) {
    if (!stillHere()) return;
    void reload().catch((err: unknown) => { if (stillHere()) setError(errorText(err)); });
  }

  // A selected record is not yet an Art owner while another record/scope is
  // resolving. Tag the successful prose read so old ids cannot be combined
  // with the new scope, and never issue image requests for a New form.
  const selectedRead = readReq.current;
  const artOwner = editing ? JSON.stringify([scope.kind, scope.id, kind, editing]) : null;
  const art = useRecordArt(editing && loadedArtOwner === artOwner && (selected === undefined || selected === editing)
    ? { scope, kind, record_id: editing } : null, { recordName: name, onSaved: async () => {
      const gen = scopeGen.current, viewing = readReq.current;
      await reloadFor(gen);
      // Reloading the rail can outlive a same-scope selection change. Refresh
      // prose only while the original selection still owns this callback.
      if (gen === scopeGen.current && viewing === readReq.current && editing) await select(editing);
    } });
  function selectRecordTab(tab: "details" | "art") {
    art.transition(() => {
      const next = new URLSearchParams(params);
      if (tab === "art") next.set("tab", "art"); else next.delete("tab");
      setParams(next, { replace: true });
    });
  }
  useEffect(() => {
    const gen = scopeGen.current;
    return onArtChanged(() => {
      // Shared captions/bytes may affect another placement's rail preview.
      void reloadFor(gen).catch((err: unknown) => { if (gen === scopeGen.current) setError(errorText(err)); });
    });
  }, [scope.kind, scope.id, kind]); // eslint-disable-line react-hooks/exhaustive-deps

  function setField(key: string, value: string) {
    setFields((current) => ({ ...current, [key]: value }));
    // Once removed, a slot loses its opt-in. Re-adding it cannot revive a
    // consent captured against the earlier form value.
    setBindings((current) => current.filter((binding) =>
      binding.field !== key || parseRefs(value).includes(binding.actor)));
  }
  function setBinding(binding: GroupRefBinding, checked: boolean) {
    setBindings((current) => checked ? [...current, binding]
      : current.filter((b) => b.field !== binding.field || b.actor !== binding.actor));
  }
  const renderSpec = (spec: EntityFieldSpec) => (
    <EntityFieldControl key={spec.key} spec={spec} value={fields[spec.key] ?? ""}
      options={optionsFor(spec)} optionsComplete={completeFor(spec)} choiceOptionsByKey={choiceOpts}
      foreignRefs={unresolvedFor(spec)} bindings={bindings}
      onChange={(value) => setField(spec.key, value)} onBindingChange={setBinding} />
  );
  const draft = { name, body, keys, secrecy, fields, owners };

  // Open in the form with a record under it: the bulk apply re-reads that
  // record, and the form's unsaved text would go with it. A blank NEW form has
  // no record to re-read, so it is not blocked.
  const adoptAllBlocked = mode === "edit" && editing !== null;
  // Apply-to-all is a sweep over the whole library, so it is reachable from a
  // record too: the rail that used to hold it is gone from a record's screen.
  const adoptAllControls = kind === "lore" ? (
    <>
      {pendingAdoptions !== null && pendingAdoptions > 0 && <button className="subtle adopt-all" onClick={() => { void applyAdoptAll(); }}
              disabled={adoptBusy || adoptAllBlocked}
              title={adoptAllBlocked
                ? "Save or cancel the edit in progress first -- applying re-reads the record"
                : undefined}>
        Apply imported settings to all
      </button>}
      {adoptAllNote && <div className="field-hint" role="status">{adoptAllNote}</div>}
    </>
  ) : null;

  // The browse grid is the section root with nothing open: no record named, no
  // blank form asked for, and neither the wizard nor a template preview up.
  // The wizard only draws with a module; a wizard flag left behind without one
  // must not take the grid away for a blank form nobody asked for.
  const browsing = selected == null && !isNew && !(wizardOpen && module) && !contentPreview;

  // This rendered callback belongs to the operation that starts with it.
  const wizardStillHere = captureViewGuard();
  const sidebarStillHere = captureViewGuard();
  return (
    <div className="editor">
      {/* Two states, not one layout (#437): the section root is the browse grid
          of cards, and a record or the blank form takes the whole width with a
          Back link. The grid is the roster; `GreetingEditor`'s rail stays for a
          handful. */}
      {browsing ? (
        <EntityBrowse scope={scope} kind={kind} items={items} editing={editing} query={query}
          setQuery={setQuery} ownerById={ownerById} ownerLabel={ownerLabel} newHref={newHref}
          recordHref={recordHref} module={module} error={error} adoptAllControls={adoptAllControls}
          onOpenWizard={() => art.transition(() => { readReq.current += 1; resetForm(); setWizardOpen(true); })}
          onOpenTemplate={(id) => art.transition(() => { void selectContent(id); })} />
      ) : (
        <div className="editor-body">
          <div className="entity-record-bar">
          <Link className="subtle entity-back" to={sectionPath}
                onClick={() => { readReq.current += 1; setWizardOpen(false); setContentPreview(null); }}>← Back to the list</Link>
            {adoptAllControls}
          </div>
          <RecordArtViewer controller={art} />
        {error && <div className="banner">{error}</div>}
        {groupNeedsReload && (
          <div className="banner">
            Reload the group to review its current World references before saving.
            <button className="subtle" onClick={() => { void cancelEdit(); }}>
              Reload group (discard edits)
            </button>
          </div>
        )}
        {stale && (
          <StaleRecordBanner label={label} rev={stale.rev} onReload={() => { void discardAndReload(); }}
                             overwriteLabel={stale.to ? "Reclassify anyway" : undefined}
                             onOverwrite={() => { void (stale.to
                               ? reclassify(stale.to, stale.rev)
                               : save(stale.rev)); }} />
        )}
        {wizardOpen && module ? (
          <CreationWizard scope={scope} kind={kind} module={module}
                          createRecord={(n) => api.createEntity(scope, kind, { name: n }).then((r) => r.id)}
                          deleteRecord={(id) => api.deleteEntity(scope, kind, id).then(() => {})}
                          onDone={(id) => { void finishCreation(id, wizardStillHere); }}
                          onCancel={() => { readReq.current += 1; setWizardOpen(false); }} />
        ) : contentPreview ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{contentPreview.name}</h3>
              <div className="detail-rendered">
                <MarkdownArtProvider enabled contextKey={JSON.stringify([scope.kind, scope.id, kind, "template", contentPreview.name])}><Markdown components={markdownImageComponents} remarkPlugins={[remarkGfm]}>{contentPreview.body}</Markdown></MarkdownArtProvider>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="primary" onClick={() => { void instantiate(); }}>Instantiate</button>
              </div>
              <div className="side-section">
                <h4>Module</h4>
                <span className="chip on">{module?.manifest.name}</span>
              </div>
            </aside>
          </div>
        ) : mode === "view" && editing ? (
          <EntityDetails scope={scope} kind={kind} editing={editing} draft={draft} tokenCost={tokenCost}
            sdPrompt={sdPrompt} fieldSpecs={fieldSpecs} candidates={candidates}
            group={{ required: requiresGroupContext, loaded: groupContext !== null, unresolvedFor }}
            art={art} recordTab={recordTab} selectRecordTab={selectRecordTab} adopt={adopt} adoptBusy={adoptBusy}
            onApplyAdopt={() => { void applyAdopt(); }}
            onEdit={() => art.transition(() => { selectRecordTab("details"); setMode("edit"); })}
            onMoved={() => refreshSidebar(sidebarStillHere)}
            onDemoted={() => {
              // A completed sidebar write belongs to the record that opened it,
              // not a later selection. Leave the removed record's address for the grid.
              if (selectedRead === readReq.current && sidebarStillHere()) void finishRemoval();
            }}
            onReclassify={askReclassify} onOpenOwner={onOpenOwner} module={module} />
        ) : (
          <EntityForm kind={kind} editing={editing} draft={draft} setName={setName} setBody={setBody}
            setKeys={setKeys} setSecrecy={setSecrecy} setOwners={setOwners} setField={setField}
            activationOpen={activationOpen} setActivationOpen={setActivationOpen} ownerOpts={ownerOpts}
            fieldControls={fieldSpecs.map(renderSpec)} renderActivation={renderSpec} groupNeedsReload={groupNeedsReload}
            onDelete={() => { if (editing) void remove({ id: editing, name }); }}
            onCancel={() => { void cancelEdit(); }} onSave={() => { void save(); }} />
        )}
        </div>
      )}
    </div>
  );
}
