/**
 * Mid-campaign switch follow-up (spec 06 §Switching modules mid-campaign).
 *
 * When the user changes a campaign's mechanics module via
 * `POST /campaigns/{id}/mechanics/switch`, the backend returns the list of
 * entities that have an old-module sheet but no new-module one. This
 * component walks the new module's `CharacterCreation` wizard once per
 * missing sheet, in sequence, with per-sheet progress and skip support.
 */

import { useState } from "react";

import type { MissingSheet } from "../../api/campaign";
import { CampaignCharacterCreation } from "./CharacterCreation";

interface Props {
  campaignId: string;
  moduleId: string;
  themeCss?: string | null;
  missing: MissingSheet[];
  onClose: () => void;
}

export function BulkSheetCreation({ campaignId, moduleId, themeCss, missing, onClose }: Props) {
  const [idx, setIdx] = useState(0);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [completed, setCompleted] = useState<string[]>([]);

  const current = missing[idx];
  const total = missing.length;

  if (!current) {
    return (
      <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="bulk-done">
        <div className="modal bulk-creation-modal">
          <header>
            <h3 id="bulk-done">Bulk creation complete</h3>
          </header>
          <p>
            Created {completed.length} of {total} sheet{total === 1 ? "" : "s"}.
            {skipped.length > 0 && ` Skipped ${skipped.length}.`}
          </p>
          <p className="wizard-meta">
            Skipped sheets can be created later by opening each character's mechanics view.
          </p>
          <div className="modal-actions">
            <button type="button" className="primary" onClick={onClose}>
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  const advance = () => setIdx((i) => i + 1);

  const handleSkip = () => {
    setSkipped((prev) => [...prev, current.entity_id]);
    advance();
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="bulk-heading">
      <div className="modal bulk-creation-modal">
        <header>
          <h3 id="bulk-heading">
            Create sheets for new mechanics ({idx + 1} of {total})
          </h3>
          <p className="wizard-step-help">
            Sheet for <strong>{current.character_name ?? current.entity_id}</strong> (
            <code>{current.kind}</code>) under the new module.
          </p>
        </header>
        <CampaignCharacterCreation
          key={current.entity_id}
          campaignId={campaignId}
          characterId={current.entity_id}
          moduleId={moduleId}
          themeCss={themeCss}
          heading={current.character_name ?? current.entity_id}
          onCancel={handleSkip}
          onComplete={() => {
            setCompleted((prev) => [...prev, current.entity_id]);
            advance();
          }}
        />
        <div className="modal-actions bulk-creation-footer">
          <button type="button" onClick={handleSkip}>
            Skip this sheet
          </button>
        </div>
      </div>
    </div>
  );
}
