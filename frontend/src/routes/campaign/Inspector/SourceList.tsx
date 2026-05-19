/**
 * Source list — one row per chunk in the assembled preview, expandable
 * to show the per-source inclusion reasons and pin/exclude controls.
 */

import { useState } from "react";

import type {
  ContextSourceExplanation,
  ContextTier,
  InclusionReason,
} from "../../../api/inspector";
import { PinControls } from "./PinControls";

const REASON_LABELS: Record<InclusionReason, string> = {
  present_in_scene: "Present in scene",
  mentioned_in_recent_posts: "Mentioned recently",
  commitment_open_to_pc: "Open commitment to PC",
  keyword_triggered: "Keyword triggered",
  relationship_to_present: "Relationship to present",
  pinned_by_user: "Pinned by user",
  scene_anchor: "Scene anchor",
  mechanics_relevant: "Mechanics relevant",
  style_guide_active: "Style guide active",
  pc_card: "PC card",
  composition_default: "Composition default",
  extras_pinned_to_hud: "Extras pinned",
  extras_default_visible: "Extras default",
  lore_before_cast: "Lore before cast",
  lore_after_cast: "Lore after cast",
  lore_at_depth: "Lore at depth",
  lore_archive: "Lore archive",
  transient_state_active: "Transient state active",
};

interface Props {
  campaignId: string;
  sources: ContextSourceExplanation[];
  onChanged?: () => void;
}

const TIER_ORDER: Record<ContextTier, number> = {
  "lock-in": 0,
  spotlight: 1,
  background: 2,
  archive: 3,
};

export function SourceList({ campaignId, sources, onChanged }: Props) {
  const sorted = [...sources].sort(
    (a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || b.tokens - a.tokens,
  );
  return (
    <ul className="inspector-source-list" aria-label="Sources in preview">
      {sorted.map((s) => (
        <SourceRow
          key={s.source_id || `${s.kind}:${s.owner_id}`}
          campaignId={campaignId}
          source={s}
          onChanged={onChanged}
        />
      ))}
      {sorted.length === 0 && <li className="inspector-empty">No sources in this preview.</li>}
    </ul>
  );
}

function SourceRow({
  campaignId,
  source,
  onChanged,
}: {
  campaignId: string;
  source: ContextSourceExplanation;
  onChanged?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const headline = source.summary || source.owner_id || source.kind;
  return (
    <li className={`inspector-source-row inspector-tier-${source.tier}`}>
      <button
        type="button"
        className="inspector-source-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="inspector-source-tier">{source.tier}</span>
        <span className="inspector-source-kind">{source.kind}</span>
        <span className="inspector-source-headline">{headline}</span>
        <span className="inspector-source-tokens">{source.tokens.toLocaleString()} tok</span>
      </button>
      {open && (
        <div className="inspector-source-details">
          <p className="inspector-source-id">
            <code>{source.source_id || "(no id)"}</code>
          </p>
          <ul className="inspector-reason-list">
            {source.inclusion_reasons.length === 0 ? (
              <li className="inspector-empty">No declared reason.</li>
            ) : (
              source.inclusion_reasons.map((r) => (
                <li key={r} className={`inspector-reason inspector-reason-${r}`}>
                  {REASON_LABELS[r] ?? r}
                </li>
              ))
            )}
          </ul>
          <PinControls campaignId={campaignId} source={source} onChanged={onChanged} />
        </div>
      )}
    </li>
  );
}
