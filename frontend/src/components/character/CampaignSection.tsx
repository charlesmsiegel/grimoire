import { type Casefile } from "../../api/client";
import { ColumnSection } from "../PageShell";
import { sceneOrdinal } from "./shared";

function CampaignRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="local-row">
      <span className="data-label">{label}</span>
      <p className="local-value">{value}</p>
    </div>
  );
}

/** What one campaign has made of this character.
 *
 *  Nothing here is part of the card and nothing here is editable: it is a
 *  readout of files the absorb pass writes, and looking like a readout rather
 *  than a form is the point. Main is a document someone authors and every
 *  campaign then shares; this is one campaign's record of what happened, and it
 *  belongs to that campaign alone.
 *
 *  **World scope renders this not at all**, which is the whole reason it moved
 *  here from a 300px pane of its own. That pane's world-scope state was a
 *  paragraph explaining that there was no campaign to report on — a fifth
 *  column of chrome, permanently, to say nothing. A section that is absent says
 *  the same thing and costs nothing to say it.
 */
export function CampaignSection(
  { label, name, state }: {
    /** The campaign's name, or its slug if the name could not be read. */
    label: string;
    name: string;
    /** `null` while the campaign's record is still being read. */
    state: { scenes: string[]; casefile: Casefile | null } | null;
  },
) {
  return (
    <ColumnSection label={`In ${label}`}>
      <p className="field-hint">Campaign-local · not part of the card</p>
      {state === null ? (
        <p className="local-empty">Reading…</p>
      ) : state.scenes.length === 0 ? (
        <p className="local-empty">
          {name} has not been in a scene in {label} yet. Play one and the absorb
          pass writes their state and dossier here — the card does not change.
        </p>
      ) : <>
        {state.casefile === null ? (
          <p className="local-empty">
            Could not read {name}'s state in {label}. The scenes below are what
            their appearance record still says.
          </p>
        ) : <>
          {state.casefile.standing && <CampaignRow label="Current state" value={state.casefile.standing} />}
          {state.casefile.knows && <CampaignRow label="Knows" value={state.casefile.knows} />}
          {state.casefile.suspects && <CampaignRow label="Suspects" value={state.casefile.suspects} />}
          {/* The tagline is the guess a dossier replaces, so it only stands in
              while there is no dossier — showing both would present a first
              impression and the record that outgrew it as equals. */}
          {(state.casefile.dossier || state.casefile.tagline) && (
            <div className="local-row">
              <span className="data-label">Dossier</span>
              <p className="local-value">{state.casefile.dossier || state.casefile.tagline}</p>
              <span className="dossier-source">
                {state.casefile.dossier ? "dossier.md" : "tagline.md"}
              </span>
            </div>
          )}
          {!state.casefile.standing && !state.casefile.knows && !state.casefile.suspects
            && !state.casefile.dossier && !state.casefile.tagline && (
            <p className="local-empty">
              Nothing recorded yet. {label} has had them on stage but no absorb
              pass has written their state or dossier.
            </p>
          )}
        </>}

        <div className="local-row">
          <span className="data-label">Appears in</span>
          {/* Plain attributes, not links: a scene is somewhere this page cannot
              navigate to, and a chip that looks clickable and is not is worse
              than one that never claimed to be. */}
          <div className="chip-row">
            {state.scenes.map((sid) => (
              <span className="chip on" key={sid}>Scene {sceneOrdinal(sid)}</span>
            ))}
          </div>
        </div>
      </>}
    </ColumnSection>
  );
}

export default CampaignSection;
