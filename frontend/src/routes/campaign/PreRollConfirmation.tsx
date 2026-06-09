/**
 * Pre-roll confirmation (spec 06 §Pre-roll evaluation).
 *
 * The orchestrator emits a `pre_roll_pending` event for turns where the
 * configured policy requires player review (always or high-stakes-only). This
 * component subscribes to the campaign's WebSocket stream, opens a modal
 * listing the proposed rolls, and POSTs the player's per-proposal decisions
 * (accept / modify / decline) to `/turns/{turn_id}/resolve-proposals`.
 */

import { useCallback, useState } from "react";

import {
  campaignApi,
  type PreRollPendingEvent,
  type ProposedRoll,
  type RollResolution,
} from "../../api/campaign";
import { errorMessage } from "../../api/client";
import { useCampaignEvent } from "../../state/useCampaignEvent";

type Decision = "accept" | "modify" | "decline";

interface RowState {
  decision: Decision;
  pool: number;
  difficulty: number | null;
}

function initialRow(p: ProposedRoll): RowState {
  return {
    decision: "accept",
    pool: p.pool,
    difficulty: p.difficulty ?? null,
  };
}

interface Props {
  campaignId: string;
}

export function PreRollConfirmation({ campaignId }: Props) {
  const [pending, setPending] = useState<PreRollPendingEvent | null>(null);
  const [rows, setRows] = useState<RowState[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleEvent = useCallback((m: { type: string } & Record<string, unknown>) => {
    if (m.type !== "pre_roll_pending") return;
    // The WS stream is scoped to /ws/campaigns/{id}/stream, so every
    // event delivered here is already for this campaign — no need to
    // filter on a campaign_id field (the backend doesn't include one
    // on the WS payload).
    const event = m as unknown as PreRollPendingEvent;
    setPending(event);
    setRows(event.proposals.map(initialRow));
    setError(null);
  }, []);

  useCampaignEvent("pre_roll_pending", handleEvent);

  if (!pending) return null;

  function update(idx: number, patch: Partial<RowState>) {
    setRows((prev) => prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }

  function buildResolutions(): RollResolution[] {
    if (!pending) return [];
    return pending.proposals.map((proposal, idx) => {
      const row = rows[idx] ?? initialRow(proposal);
      if (row.decision === "decline") {
        return { label: proposal.label, accepted: false };
      }
      if (row.decision === "modify") {
        const mods: Partial<ProposedRoll> = {};
        if (row.pool !== proposal.pool) mods.pool = row.pool;
        if ((row.difficulty ?? null) !== (proposal.difficulty ?? null)) {
          mods.difficulty = row.difficulty;
        }
        return {
          label: proposal.label,
          accepted: true,
          modifications: Object.keys(mods).length ? mods : null,
        };
      }
      return { label: proposal.label, accepted: true };
    });
  }

  async function submit() {
    if (!pending) return;
    setSubmitting(true);
    setError(null);
    try {
      await campaignApi.resolveProposals(campaignId, pending.turn_id, buildResolutions());
      setPending(null);
      setRows([]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  function declineAll() {
    setRows((prev) => prev.map((r) => ({ ...r, decision: "decline" })));
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="preroll-title">
      <div className="modal pre-roll-modal">
        <header>
          <h3 id="preroll-title">Confirm proposed rolls</h3>
          <p className="wizard-step-help">
            The mechanics module proposed {pending.proposals.length} roll
            {pending.proposals.length === 1 ? "" : "s"} for this turn. Accept, modify, or decline
            each, then submit to continue.
          </p>
        </header>
        <ul className="pre-roll-list">
          {pending.proposals.map((proposal, idx) => {
            const row = rows[idx] ?? initialRow(proposal);
            return (
              <li key={`${proposal.label}-${idx}`} className="pre-roll-item">
                <div className="pre-roll-head">
                  <strong>{proposal.label}</strong>
                  {proposal.high_stakes && (
                    <span className="badge badge-warn" title="High-stakes roll">
                      high stakes
                    </span>
                  )}
                  <small className="muted"> · {proposal.kind}</small>
                </div>
                {proposal.rationale && (
                  <p className="pre-roll-rationale muted">{proposal.rationale}</p>
                )}
                <div className="pre-roll-grid">
                  <label className="field">
                    <span>Pool</span>
                    <input
                      type="number"
                      value={row.pool}
                      disabled={row.decision !== "modify"}
                      onChange={(e) =>
                        update(idx, { pool: Number.parseInt(e.target.value, 10) || 0 })
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Difficulty</span>
                    <input
                      type="number"
                      value={row.difficulty ?? ""}
                      disabled={row.decision !== "modify"}
                      placeholder="—"
                      onChange={(e) => {
                        const raw = e.target.value;
                        update(idx, {
                          difficulty: raw === "" ? null : Number.parseInt(raw, 10) || 0,
                        });
                      }}
                    />
                  </label>
                </div>
                {proposal.modifiers.length > 0 && (
                  <ul className="pre-roll-mods muted">
                    {proposal.modifiers.map((m, i) => (
                      <li key={i}>
                        {m.label}: {m.delta > 0 ? `+${m.delta}` : m.delta}
                        {m.multiplier !== 1 ? ` ×${m.multiplier}` : ""}
                      </li>
                    ))}
                  </ul>
                )}
                <div className="pre-roll-decision" role="radiogroup" aria-label="Decision">
                  {(["accept", "modify", "decline"] as Decision[]).map((d) => (
                    <label key={d} className="field-inline">
                      <input
                        type="radio"
                        name={`decision-${idx}`}
                        value={d}
                        checked={row.decision === d}
                        onChange={() => update(idx, { decision: d })}
                      />
                      <span>{d}</span>
                    </label>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
        {error && (
          <p className="wizard-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button type="button" onClick={declineAll} disabled={submitting}>
            Decline all
          </button>
          <button
            type="button"
            className="primary"
            disabled={submitting}
            onClick={() => void submit()}
          >
            {submitting ? "Submitting…" : "Submit resolutions"}
          </button>
        </div>
      </div>
    </div>
  );
}
