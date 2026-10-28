import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type ChubUnlinkedVersion, type EntityScope, type ModuleDetail } from "../api/client";
import CreationWizard from "./CreationWizard";
import { DescribeQueue } from "./DescribeQueue";
import { ErrorNote } from "./ErrorNote";
import { TextCharacterImport } from "./TextCharacterImport";
import { characterHref } from "./character/shared";
import { CharacterCard } from "./characterGrid/CharacterCard";
import { CharacterGridToolbar, ChubUnlinkedList } from "./characterGrid/CharacterGridToolbar";
import { CharacterImportDialogs } from "./characterGrid/CharacterImportDialogs";
import { NewCharacterForm } from "./characterGrid/NewCharacterForm";
import { useCharacterImports } from "./characterGrid/useCharacterImports";
import { useCharacterRoster, useResetOnKey } from "./characterGrid/useCharacterRoster";
import { useDescribeBacklog } from "./characterGrid/useDescribeBacklog";
import { useRosterView } from "./characterGrid/useRosterView";
import { useTaglineBatch } from "./characterGrid/useTaglineBatch";

/** The world's (or campaign's) roster, as cards.
 *
 *  Half of what used to be `CharacterEditor`: the grid and every bulk operation
 *  that acts on a roster rather than on one record. The other half is
 *  `routes/CharacterPage`, which owns a route now — so opening a character here
 *  is a navigation, and this component no longer has modes.
 *
 *  What went with the modes is the machinery that existed only because one
 *  long-lived component was showing a record from a scope that could change
 *  underneath it: `adopt`, `scopeRef`, `keepVisible`'s pending reveal. A close
 *  that has to un-filter the character it is handing back is still needed and
 *  is `reveal` below — the page passes the id back through `location.state`,
 *  which is the same fact travelling by a route rather than by a ref.
 *
 *  A scope seen before paints on the first render, from the rows the api
 *  client remembers (see "remembered reads" in `api/client.ts`), and is
 *  revalidated by the same read a first visit makes; so what a revisit shows
 *  before that read lands is at most one revalidation stale, and never
 *  another scope's.
 *
 *  The pieces live in `characterGrid/`: the roster reads and their visit fence
 *  (`useCharacterRoster`), the describe backlog, the imports, the tagline run,
 *  the filter and first-paint slice (`useRosterView`), and the toolbar, dialogs
 *  and cards that draw them.
 */
export function CharacterGrid(
  { scope: scopeProp, wid, reveal, module = null, onListed }: {
    scope: EntityScope;
    wid: string;
    /** A character to keep visible even if the appeared filter would hide it —
     *  handed back by their page on close. */
    reveal?: string | null;
    module?: ModuleDetail | null;
    /** Called once this scope's list read has settled (landed or failed): the
     *  moment the grid has what it needs to paint, and so the moment the page
     *  can start the reads the grid can do without for its first paint. */
    onListed?: () => void;
  },
) {
  // By value, so a caller that builds its scope object per render hands the
  // callbacks below the same identity every time -- the memoized cards take
  // them as props.
  const scope = useMemo<EntityScope>(() => ({ kind: scopeProp.kind, id: scopeProp.id }),
                                     [scopeProp.kind, scopeProp.id]);
  const scopeKey = `${scope.kind}:${scope.id}`;
  const worldScope = scope.kind === "world";
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const creating = params.get("new") === "1";
  const roster = useCharacterRoster(scope, onListed);
  const { chars, error, setError, reload, fence } = roster;
  const { visit, liveVisit } = fence;
  const [wizardOpen, setWizardOpen] = useState(false);
  const [unlinkedVersions, setUnlinkedVersions] = useState<ChubUnlinkedVersion[] | null>(null);
  const [urlPromptOpen, setUrlPromptOpen] = useState(false);
  const [textImportOpen, setTextImportOpen] = useState(false);
  // Closed with the scope it was opened on, in the same render (see `useResetOnKey`).
  useResetOnKey(scopeKey, () => setTextImportOpen(false));
  // A wizard is a record being made in one library; the next opens on its grid.
  useEffect(() => { setWizardOpen(false); }, [visit]);

  const describe = useDescribeBacklog(scope, fence, setError);
  const imports = useCharacterImports({ wid, scope, reload, setError, navigate, fence });
  const untagged = useMemo(() => (worldScope ? chars.filter((c) => !c.tagline) : []),
                           [worldScope, chars]);
  const taglines = useTaglineBatch({ wid, untaggedCount: untagged.length, reload, setError, fence });
  const view = useRosterView({ chars, appeared: roster.appeared, worldScope, reveal, visit, scopeKey });

  async function checkChubLinks() {
    setError(null);
    try {
      const { versions } = await api.findChubUnlinked(wid);
      setUnlinkedVersions(versions);
    } catch (err: unknown) { setError(err); }
  }

  // Campaign scope has no verdict yet while the roster is in flight. Painting
  // the grid anyway shows every inherited character for as long as that read
  // takes and then yanks most of them away. A FAILED read is not this state: it
  // has its answer, which is "do not filter".
  const rosterPending = !worldScope && roster.appeared === null && !roster.rosterFailed;

  // Every hook above this line: the wizard below returns early.
  if (wizardOpen && module && worldScope) {
    return (
      <div className="character-editor">
        <CreationWizard scope={scope} kind="characters" module={module}
                        createRecord={(n) => api.createCharacter(scope, { name: n }).then((r) => r.character)}
                        deleteRecord={(id) => api.deleteCharacter(scope, id).then(() => {})}
                        onDone={(id) => void (async () => {
                          setWizardOpen(false);
                          await reload();
                          void navigate(characterHref(scope, id));
                        })()}
                        onCancel={() => setWizardOpen(false)} />
      </div>
    );
  }

  if (creating) {
    return <NewCharacterForm scope={scope} worldScope={worldScope} error={error} setError={setError} />;
  }

  return (
    <div className="character-editor">
      <CharacterImportDialogs wid={wid} chars={chars} imports={imports}
                              urlPromptOpen={urlPromptOpen}
                              onCloseUrlPrompt={() => setUrlPromptOpen(false)}
                              onTaglineSaved={() => {
                                if (liveVisit.current !== visit) return;
                                void reload().catch((err: unknown) => {
                                  if (liveVisit.current === visit) setError(err);
                                });
                              }} />
      {textImportOpen && worldScope && (
        <TextCharacterImport key={scopeKey} wid={scope.id}
          onCancel={() => setTextImportOpen(false)}
          onSaved={(result) => {
            setTextImportOpen(false);
            void reload().then(() => {
              if (liveVisit.current === visit) {
                void navigate(characterHref(scope, result.character));
              }
            }).catch((err: unknown) => {
              if (liveVisit.current === visit) setError(err);
            });
          }} />
      )}
      {describe.describeQueue !== null && (
        <DescribeQueue key={scopeKey} scope={scope} wid={wid} queue={describe.describeQueue}
                       onSaved={describe.recountUndescribed}
                       onClose={describe.closeDescribe} />
      )}

      <CharacterGridToolbar worldScope={worldScope} module={module} imports={imports}
                            taglines={taglines} view={view} total={chars.length}
                            untaggedCount={untagged.length}
                            describe={{ count: describe.undescribedCount,
                                        loading: describe.describeLoading,
                                        open: describe.openDescribe }}
                            onOpenWizard={() => setWizardOpen(true)}
                            onOpenText={() => setTextImportOpen(true)}
                            onOpenUrl={() => setUrlPromptOpen(true)}
                            onCheckChub={() => void checkChubLinks()} />

      {unlinkedVersions !== null && <ChubUnlinkedList scope={scope} versions={unlinkedVersions} />}

      {error != null && <div className="banner"><ErrorNote err={error} /></div>}

      {rosterPending || !roster.charsKnown ? null : view.shown.length === 0 ? (
        <div className="editor-empty">
          {chars.length === 0
            ? "No characters yet. Create one or import a card."
            : "No one has appeared in this campaign yet — show All to see the world's roster."}
        </div>
      ) : (
        <div className="char-grid">
          {view.drawn.map((c) => (
            <CharacterCard key={c.id} c={c} scopeKind={scope.kind} scopeId={scope.id} />
          ))}
        </div>
      )}
    </div>
  );
}

export default CharacterGrid;
