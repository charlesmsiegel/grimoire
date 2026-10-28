import { MarkdownArtProvider } from "../../markdown/MarkdownArtProvider";
import Markdown from "react-markdown";
import { markdownImageComponents } from "../../markdown/MarkdownImage";
import remarkGfm from "remark-gfm";
import { ACTIVATION_FIELDS, ENTITY_KINDS, SECRECY_LABELS, type EntityKind, type EntityScope, type EntityFieldSpec, type ModuleDetail } from "../../api/client";
import { RecordArtEntry } from "../art/RecordArtEntry";
import { RecordArtPanel } from "../art/RecordArtPanel";
import type { useRecordArt } from "../art/useRecordArt";
import { DemotePanel } from "../DemotePanel";
import { GroupStatePanel } from "../GroupStatePanel";
import { LibraryPanel } from "../LibraryPanel";
import { MobileArtEntry } from "../MobileArtEntry";
import { OwnedLorePanel } from "../OwnedLorePanel";
import { Portrait } from "../Portrait";
import SheetPanel from "../SheetPanel";
import { sectionHref } from "../../worldPaths";
import { ACTIVATION_POSTS, KNOWN_BY_SPEC, isKeepOn, parseRefs, worldRefLabel, WORLD_BINDING_HINT, DANGLING_HINT, UNLOADED_HINT } from "./fields";
import { KIND_LABELS, SECRECY_HINTS, tokenClass, tokenTitle, tokenAria, tokenLabel } from "./presentation";
import type { EntityDraft } from "./EntityForm";
import type { EntityOptions } from "./useEntityOptions";

export function EntityDetails({ scope, kind, editing, draft, tokenCost, sdPrompt, fieldSpecs, candidates,
  group, art, recordTab, selectRecordTab, adopt, adoptBusy, onApplyAdopt, onEdit, onMoved, onDemoted,
  onReclassify, onOpenOwner, module }: {
  scope: EntityScope; kind: EntityKind; editing: string; draft: EntityDraft; tokenCost: number | null;
  sdPrompt: string; fieldSpecs: EntityFieldSpec[]; candidates: EntityOptions;
  group: { readonly required: boolean; readonly loaded: boolean; unresolvedFor: (spec: EntityFieldSpec) => string[] };
  art: ReturnType<typeof useRecordArt>; recordTab: "details" | "art";
  selectRecordTab: (tab: "details" | "art") => void;
  adopt: { fields: Record<string, string>; unmapped: string[]; inherited?: boolean } | null;
  adoptBusy: boolean; onApplyAdopt: () => void; onEdit: () => void; onMoved: () => void;
  onDemoted: () => void; onReclassify: (to: EntityKind) => void;
  onOpenOwner?: ((ref: string) => void) | undefined; module: ModuleDetail | null;
}) {
  const { name, body, keys, secrecy, fields, owners } = draft;
  const { displayValue, resolveRef, unresolvedHint, actorsComplete, ownerById, ownerLabel } = candidates;
  const { unresolvedFor } = group;
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
    <h3 ref={art.focusRef} tabIndex={-1}>
      {name}
      {tokenCost !== null && (
        <span className={tokenClass(secrecy)} title={tokenTitle(secrecy)}
              aria-label={tokenAria(tokenCost, secrecy)}>
          {tokenLabel(tokenCost)}
        </span>
      )}
    </h3>
  );

  // The set activation controls as sidebar chips, in catalog order. `known_by`
  // is not among them: it names records, so it gets chips of its own.
  // Each is [key, heading, chip]: the heading is the label the form's
  // Activation disclosure uses, so the sidebar and the form say the same word.
  const activationChips: [string, string, string][] = ACTIVATION_FIELDS.flatMap((a) => {
    const v = (fields[a.key] ?? "").trim();
    if (!v || a.widget === "refs") return [];
    if (a.widget === "bool") return isKeepOn(v) ? [[a.key, a.label, "yes"] as [string, string, string]] : [];
    const unit = ACTIVATION_POSTS.has(a.key) ? (v === "1" ? " post" : " posts") : "";
    return [[a.key, a.label, `${v}${unit}`] as [string, string, string]];
  });

  return (
    <div className="detail-view">
      <div className="detail-main">
        <div className="entity-record-head">
          {heading}
          <RecordArtEntry controller={art} name={name} onOpenArt={() => selectRecordTab("art")} />
        </div>
        <MobileArtEntry name={name} image={art.preview ? () => art.preview.thumb : null} focus={art.preview?.focus}
          onOpenArt={() => selectRecordTab("art")}
          onOpenPreview={opener => { if (art.preview) art.openImage(art.preview, opener); }} />
        <div className="card-tabs" role="tablist" aria-label="Record">
          <button role="tab" aria-selected={recordTab === "details"} className={"tab" + (recordTab === "details" ? " active" : "")}
            onClick={() => selectRecordTab("details")}>Details</button>
          <button role="tab" aria-selected={recordTab === "art"} className={"tab" + (recordTab === "art" ? " active" : "")}
            onClick={() => selectRecordTab("art")}>{art.count === null ? "Art" : "Art " + art.count}</button>
        </div>
        <div role="tabpanel">
          {recordTab === "art" ? <RecordArtPanel controller={art} /> : <>
        {adopt?.inherited && (
          // A record the campaign only reads from its world: the world's
          // copy is the one that takes the import, so there is nothing to
          // apply from here.
          <div className="banner adopt-banner" role="status" aria-label="Imported settings">
            <p>Imported SillyTavern settings are available on the world's copy of this entry.</p>
          </div>
        )}
        {adopt && !adopt.inherited && (
          <div className="banner adopt-banner" role="status" aria-label="Imported settings">
            <p>
              <strong>This entry carries imported SillyTavern settings that are not
              applied yet:</strong>{" "}
              {Object.entries(adopt.fields).map(([k, v], i) => (
                <span key={k}>{i > 0 && ", "}<code title={`${k}: ${v}`}>{k}</code></span>
              ))}
              .
              {adopt.unmapped.length > 0 && (
                <> Not mapped to anything here: {adopt.unmapped.join(", ")}.</>
              )}
            </p>
            <button className="subtle" onClick={onApplyAdopt}
                    disabled={adoptBusy}>Apply</button>
          </div>
        )}
            <div className="detail-rendered">
              <MarkdownArtProvider enabled contextKey={JSON.stringify([scope.kind, scope.id, kind, editing])}><Markdown components={markdownImageComponents} remarkPlugins={[remarkGfm]}>{body}</Markdown></MarkdownArtProvider>
            </div>
          </>}
        </div>
      </div>
      <aside className="detail-sidebar">
        <div className="form-actions">
          <button className="subtle" onClick={onEdit}>Edit</button>
        </div>
        {/* Keyed on the record, so switching rows remounts rather than
            showing the previous record's library state while the new one
            loads. The two are opposites and never both apply: a campaign
            puts a record INTO the library, a world takes one out. */}
        {editing && (scope.kind === "campaign" ? (
          <LibraryPanel key={`${scope.id}:${kind}:${editing}`}
                        cid={scope.id} kind={kind} id={editing}
                        onMoved={onMoved} />
        ) : (
          <DemotePanel key={`${scope.id}:${kind}:${editing}`}
                       wid={scope.id} kind={kind} id={editing}
                       beforeDemote={() => {
                         let allowed = false; art.transition(() => { allowed = true; }); return allowed;
                       }}
                       onDemoted={onDemoted} />
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
                <span key={f.key} className="chip on">
                  {f.label}: {displayValue(f, fields[f.key])}
                </span>
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
                if (unresolvedFor(f).includes(ref)) return (
                  <span key={ref} className="chip dangling" title={WORLD_BINDING_HINT}>{worldRefLabel(ref)}</span>
                );
                const hit = group.required && !group.loaded && (f.key === "members" || f.key === "leader")
                  ? null : resolveRef(f, ref);
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
        {/* One section per set activation field (spec 3), as the
            other metadata above is drawn. */}
        {activationChips.map(([key, heading, text]) => (
          <div key={key} className="side-section">
            <h4>{heading}</h4>
            <div className="chips">
              <span className="chip on">{text}</span>
            </div>
          </div>
        ))}
        {parseRefs(fields.known_by).length > 0 && (
          <div className="side-section">
            <h4>{KNOWN_BY_SPEC.label}</h4>
            <div className="chips">
              {parseRefs(fields.known_by).map((ref) => {
                const hit = resolveRef(KNOWN_BY_SPEC, ref);
                if (hit) {
                  // A host that cannot open an actor gets a label, not
                  // a button that does nothing when pressed.
                  return onOpenOwner ? (
                    <button key={ref} className="chip owner-chip"
                            onClick={() => onOpenOwner(ref)}>
                      <Portrait src={hit.avatar ?? null} name={hit.label} />
                      {hit.label}
                    </button>
                  ) : (
                    <span key={ref} className="chip on">
                      <Portrait src={hit.avatar ?? null} name={hit.label} />
                      {hit.label}
                    </span>
                  );
                }
                // Gone only if the listing actually arrived; a failed
                // or pending one has no standing to say so.
                return actorsComplete ? (
                  <span key={ref} className="chip missing" title={DANGLING_HINT}>
                    missing: {ref}
                  </span>
                ) : (
                  <span key={ref} className="chip dangling" title={UNLOADED_HINT}>{ref}</span>
                );
              })}
            </div>
          </div>
        )}
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
                    <Portrait src={ownerById.get(ref)?.avatar ?? null}
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
        {kind === "locations" && editing && (
          <OwnedLorePanel
            scope={scope}
            ownerRef={`locations:${editing}`}
            hrefFor={(id) => sectionHref(scope, { kind: "record", at: "lore", rid: id })}
            newHref={sectionHref(scope,
                                 { kind: "section", at: "lore", newOwner: `locations:${editing}` })}
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
                  onChange={(e) => art.transition(() => onReclassify(e.target.value as EntityKind))}>
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
  );
}
