import { useRef } from "react";
import { Link } from "react-router-dom";
import type { ChubUnlinkedVersion, EntityScope, ModuleDetail } from "../../api/client";
import { characterHref } from "../character/shared";
import type { CharacterImports } from "./useCharacterImports";
import type { RosterView } from "./useRosterView";
import type { TaglineBatch } from "./useTaglineBatch";

/** The world-only half of the toolbar: the sheet wizard and every import. */
function WorldImportActions(
  { module, imports, onOpenWizard, onOpenText, onOpenUrl, onCheckChub }: {
    module: ModuleDetail | null;
    imports: CharacterImports;
    onOpenWizard: () => void;
    onOpenText: () => void;
    onOpenUrl: () => void;
    onCheckChub: () => void;
  },
) {
  const fileRef = useRef<HTMLInputElement>(null);
  const hasCharacterSheet = module
    && Object.values(module.sheets.sheet_types).some((st) => st.kind === "characters");
  return <>
    {hasCharacterSheet && (
      <button className="subtle" onClick={onOpenWizard}>+ New character with sheet…</button>
    )}
    <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
    <button className="subtle" onClick={onOpenText}>Import text</button>
    <input ref={fileRef} type="file" accept=".json,.png,.charx" multiple hidden
           aria-label="Import character card" onChange={imports.onPickFiles} />
    <button className="subtle" onClick={onOpenUrl}>Download from URL</button>
    <button className="subtle" onClick={onCheckChub}>Check chub.ai links</button>
  </>;
}

/** The Derive button, the run's progress line and Stop, and its report. */
function TaglineControls(
  { worldScope, untaggedCount, taglines }: {
    worldScope: boolean;
    untaggedCount: number;
    taglines: TaglineBatch;
  },
) {
  const { taglineBatch, taglineBatchMsg } = taglines;
  return <>
    {worldScope && untaggedCount > 0 && (
      <button className="subtle" disabled={taglineBatch !== null}
              onClick={() => void taglines.deriveTaglines()}>
        ▶ Derive taglines ({untaggedCount})
      </button>
    )}
    {taglineBatch && (<>
      <span className="field-hint">
        Deriving taglines {taglineBatch.done}/{taglineBatch.total}
        {taglineBatch.name ? ` — ${taglineBatch.name}` : ""}…
      </span>
      <button className="subtle" onClick={taglines.stopTaglines}>Stop</button>
    </>)}
    {!taglineBatch && taglineBatchMsg && <span className="field-hint">{taglineBatchMsg}</span>}
  </>;
}

/** The progress lines of a batch import in flight. */
function ImportProgress({ imports: { bulkLocalize, bulkUrl } }: { imports: CharacterImports }) {
  return <>
    {bulkLocalize && (
      <span className="field-hint">Localizing card {bulkLocalize.current}/{bulkLocalize.cards}…</span>
    )}
    {bulkUrl && (
      <span className="field-hint">
        Adding {bulkUrl.current}/{bulkUrl.total} — {bulkUrl.name}: {bulkUrl.step}…
      </span>
    )}
  </>;
}

/** A campaign's Appeared / All switch. */
function AppearedFilter({ view, total }: { view: RosterView; total: number }) {
  const { showAll, setShowAll } = view;
  return (
    <div className="chips" role="group" aria-label="Show">
      <button className={"chip" + (showAll ? "" : " on")} aria-pressed={!showAll}
              onClick={() => setShowAll(false)}>
        Appeared ({view.appearedCount})
      </button>
      <button className={"chip" + (showAll ? " on" : "")} aria-pressed={showAll}
              onClick={() => setShowAll(true)}>
        All ({total})
      </button>
    </div>
  );
}

/** The roster's toolbar: New, the imports, the bulk passes over the roster
 *  (describe, derive taglines) with their progress, and the campaign filter. */
export function CharacterGridToolbar(
  {
    worldScope, module, imports, taglines, view, total, untaggedCount, describe,
    onOpenWizard, onOpenText, onOpenUrl, onCheckChub,
  }: {
    worldScope: boolean;
    module: ModuleDetail | null;
    imports: CharacterImports;
    taglines: TaglineBatch;
    view: RosterView;
    /** The whole roster's size, for the filter's All. */
    total: number;
    untaggedCount: number;
    describe: { count: number | null; loading: boolean; open: () => void };
    onOpenWizard: () => void;
    onOpenText: () => void;
    onOpenUrl: () => void;
    onCheckChub: () => void;
  },
) {
  return (
    <div className="grid-toolbar">
      {/* Both scopes since #60. In campaign scope the label says whose
          character it will be: the world's roster is the library, and this
          one deliberately is not in it until somebody publishes it. */}
      <Link className="primary" to="?new=1">
        {worldScope ? "+ New character" : "+ New NPC (this campaign)"}
      </Link>
      {worldScope && (
        <WorldImportActions module={module} imports={imports} onOpenWizard={onOpenWizard}
                            onOpenText={onOpenText} onOpenUrl={onOpenUrl} onCheckChub={onCheckChub} />
      )}
      {describe.count !== null && describe.count > 0 && (
        <button className="subtle" disabled={describe.loading} onClick={describe.open}>
          ▶ Describe images ({describe.count})
        </button>
      )}
      <TaglineControls worldScope={worldScope} untaggedCount={untaggedCount} taglines={taglines} />
      <ImportProgress imports={imports} />
      {view.filterable && <AppearedFilter view={view} total={total} />}
      {!imports.bulkLocalize && imports.importMsg && (
        <span className="field-hint">{imports.importMsg}</span>
      )}
    </div>
  );
}

/** The answer to "Check chub.ai links": every version not linked, as links to
 *  that version's page. */
export function ChubUnlinkedList(
  { scope, versions }: { scope: EntityScope; versions: ChubUnlinkedVersion[] },
) {
  return (
    <div className="chub-unlinked-list">
      {versions.length === 0 ? (
        <div className="field-hint">All versions are linked to chub.ai</div>
      ) : <>
        <div className="field-hint">
          {versions.length} version{versions.length === 1 ? "" : "s"} not linked to chub.ai:
        </div>
        <div className="chips">
          {versions.map((u) => (
            <Link key={`${u.character}:${u.version}`} className="chip"
                  to={characterHref(scope, u.character, u.version)}>
              {u.character_name} ({u.version_name})
            </Link>
          ))}
        </div>
      </>}
    </div>
  );
}
