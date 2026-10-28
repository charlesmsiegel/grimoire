import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type EntityFieldSpec, type EntityKind, type EntityScope, type OptionSource } from "../../api/client";
import { loreOwnerOptions, refOptions, type RecordRef } from "../../api/loreOwners";
import { choiceOptions, displayLabels, KNOWN_BY_SPEC, refKindsFor,
  DANGLING_HINT, UNLOADED_HINT, type ChoiceList, type ChoiceOption } from "./fields";

/** How each named `source` on a `choice` spec is resolved. One entry per
 *  `OptionSource`; the type is what keeps a source the schema names and the
 *  editor cannot load from compiling. */
const OPTION_SOURCES: Record<OptionSource, () => Promise<ChoiceOption[]>> = {
  climates: () => api.listClimates().then((r) =>
    r.climates.map((c) => ({ value: c.id, label: c.name }))),
};

/** Scoped display choices, never authority for record identity or binding consent. */
export function useEntityOptions({ scopeKind, scopeId, kind, fieldSpecs, wantActors }: {
  scopeKind: EntityScope["kind"]; scopeId: string; kind: EntityKind;
  fieldSpecs: EntityFieldSpec[]; wantActors: boolean;
}) {
  // The candidates every `ref` field on this kind can offer, fetched once for
  // the union of their kinds and filtered per field below — `holder` and
  // `headquarters` both want locations, and asking twice would be two requests
  // for one answer.
  const [refOpts, setRefOpts] = useState<RecordRef[]>([]);
  // Whether `refOpts` is the whole truth. False while the listings are in
  // flight and after any of them failed, and that is the only thing that
  // entitles the UI to call an unresolved ref *deleted*.
  const [refOptsComplete, setRefOptsComplete] = useState(false);
  // What each sourced `choice` field on this kind may offer, by field key.
  // Loaded per kind; a literal `options` list never lands here.
  const [choiceOpts, setChoiceOpts] = useState<Record<string, ChoiceList>>({});
  const [ownerOpts, setOwnerOpts] = useState<RecordRef[]>([]);

  useEffect(() => {
    let live = true;
    setOwnerOpts([]);
    if (kind === "lore") {
      // Primitive scope dependencies also catch world→campaign with the same
      // id. Retire both success and failure when this listing loses ownership.
      loreOwnerOptions({ kind: scopeKind, id: scopeId })
        .then((options) => { if (live) setOwnerOpts(options); })
        .catch(() => { if (live) setOwnerOpts([]); });
    }
    return () => { live = false; };
  }, [scopeKind, scopeId, kind]);

  useEffect(() => {
    // Fetched on mount and on every kind change rather than cached across
    // them: the climates are the user's list, and coming back from the
    // Climates page (which remounts this editor) must offer what was just
    // saved. `live` discards a reply that lands after the kind moved on.
    let live = true;
    setChoiceOpts({});
    for (const spec of fieldSpecs) {
      if (spec.widget !== "choice" || !spec.source) continue;
      const key = spec.key;
      OPTION_SOURCES[spec.source]()
        .then((opts) => { if (live) setChoiceOpts((cur) => ({ ...cur, [key]: opts })); })
        .catch(() => { if (live) setChoiceOpts((cur) => ({ ...cur, [key]: "failed" })); });
    }
    return () => { live = false; };
  }, [kind, fieldSpecs]);

  useEffect(() => {
    // Cleared FIRST, and every response checked against the request that is
    // current when it lands. Navigating between two worlds can keep this
    // editor mounted, and without both halves the picker offers the previous
    // scope's records until the new listing settles — or permanently, if the
    // older request resolves second. Picking one of those saves a ref into a
    // scope where it names nothing, and since existence is deliberately not
    // validated the backend takes it.
    let live = true;
    setRefOpts([]);
    setRefOptsComplete(false);
    const kinds = refKindsFor(fieldSpecs);
    // Guarded rather than unconditional: a kind with no ref fields (lore, and
    // locations, which has three text fields and none of these) would
    // otherwise pay a listing request per record kind on every mount for a
    // picker it never renders.
    if (!kinds.length) { setRefOptsComplete(true); return; }
    refOptions({ kind: scopeKind, id: scopeId }, kinds)
      .then(({ options, failed }) => {
        if (!live) return;
        setRefOpts(options);
        // A partial answer still populates the picker with the kinds that DID
        // load — losing three of a `holder`'s four kinds to one bad request is
        // a worse picker for no reason — but it is not complete, so nothing
        // built on it may call a ref dead.
        setRefOptsComplete(failed.length === 0);
      })
      .catch(() => { if (live) setRefOpts([]); });
    return () => { live = false; };
  }, [scopeKind, scopeId, kind, fieldSpecs]);

  // The actors `known_by` may name, fetched on their own and only when something
  // draws them -- the Activation disclosure is open, or the record being read
  // already has a `known_by`. Opening a section is counted by what it lists
  // (WorldView.test pins it), and a picker nobody opened is not worth two
  // listings per mount on every kind. Cleanup retires replies even when the
  // new scope or need requires no fetch at all.
  const [actorOpts, setActorOpts] = useState<RecordRef[]>([]);
  const [actorsComplete, setActorsComplete] = useState(false);
  useEffect(() => {
    let live = true;
    setActorOpts((cur) => (cur.length ? [] : cur));   // same array: no re-render
    setActorsComplete(false);
    if (!wantActors) return;
    refOptions({ kind: scopeKind, id: scopeId }, KNOWN_BY_SPEC.kinds ?? [])
      .then(({ options, failed }) => {
        if (!live) return;
        setActorOpts(options);
        setActorsComplete(failed.length === 0);
      })
      .catch(() => { if (live) setActorOpts([]); });
    return () => { live = false; };
  }, [scopeKind, scopeId, kind, wantActors]);

  /** A field value as the sidebar shows it: a choice by its option's label
   *  when the option is known, anything else as stored. */
  const displayValue = (spec: EntityFieldSpec, value: string) => {
    const list = spec.widget === "choice" ? choiceOptions(spec, choiceOpts) : undefined;
    return (Array.isArray(list) && list.find((o) => o.value === value)?.label) || value;
  };
  /** The candidates one field may name — the same filter the form's picker
   *  applies. The sidebar has to use it too: resolving a value against the
   *  WHOLE union let a `leader` holding `locations:realm` resolve off the
   *  locations fetched for `headquarters` and render as an ordinary clickable
   *  chip, so the read view called a malformed relationship valid while the
   *  form, one click away, correctly showed it unresolved. */
  const optionsFor = useCallback(
    (spec: EntityFieldSpec) =>
      (spec === KNOWN_BY_SPEC ? actorOpts : refOpts).filter((o) => spec.kinds?.includes(o.kind)),
    [refOpts, actorOpts],
  );
  const completeFor = (spec: EntityFieldSpec) =>
    spec === KNOWN_BY_SPEC ? actorsComplete : refOptsComplete;

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
  // Which of the two stories an unresolved ref gets. Only a listing that
  // actually arrived can say a record is gone.
  const unresolvedHint = refOptsComplete ? DANGLING_HINT : UNLOADED_HINT;

  // The owner candidates by ref. The rail asks for an owner's label and avatar
  // once per owner per row, and a scan of every candidate for each of those
  // made a large lore rail cost owners × entries on every render.
  const ownerById = useMemo(() => new Map(ownerOpts.map((o) => [o.ref, o])), [ownerOpts]);
  const ownerLabel = useCallback((ref: string) => ownerById.get(ref)?.label ?? ref, [ownerById]);
  return { choiceOpts, ownerOpts, ownerById, ownerLabel, displayValue, optionsFor, completeFor,
    resolveRef, unresolvedHint, actorsComplete };
}
export type EntityOptions = ReturnType<typeof useEntityOptions>;
