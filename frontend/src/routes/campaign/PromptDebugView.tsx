/**
 * "What did the model see?" — per-turn prompt debug view.
 *
 * Spec: `docs/superpowers/specs/2026-05-18-observability-COMPLETED.md` §5.
 * Renders the verbatim assembled prompt captured on a TurnAudit, with per-
 * message tier annotations + token estimates, per-source attribution
 * (scope / owner / override flag), the composition snapshot, and a diff
 * against the immediately preceding turn.
 *
 * URL: /campaigns/:campaignId/debug/prompt/:turnId?
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { observabilityApi, type PromptDiff, type PromptResponse } from "../../api/observability";
import { useResource } from "../../api/useResource";

export function PromptDebugView() {
  const { campaignId = "", turnId } = useParams<{ campaignId: string; turnId?: string }>();
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(turnId ?? null);
  const [diff, setDiff] = useState<PromptDiff | null>(null);
  const [error, setError] = useState<string | null>(null);

  const turnsResource = useResource(
    useCallback(() => observabilityApi.listTurns(campaignId, 50), [campaignId]),
  );
  const turns = useMemo(() => turnsResource.data ?? [], [turnsResource.data]);

  // Use a ref so the auto-select effect can read the current selection without
  // re-running when the user clicks a turn. Reading selectedTurnId directly
  // would either re-fire or trip react-hooks/exhaustive-deps.
  const selectedTurnIdRef = useRef(selectedTurnId);
  selectedTurnIdRef.current = selectedTurnId;

  // Auto-select the most recent turn once the list resolves.
  const loadedTurns = turnsResource.data;
  useEffect(() => {
    if (!loadedTurns) return;
    if (!selectedTurnIdRef.current && loadedTurns.length > 0) {
      setSelectedTurnId(loadedTurns[0]!.turn_id);
    }
  }, [loadedTurns]);

  const promptResource = useResource(
    useCallback(
      () =>
        selectedTurnId
          ? observabilityApi.getPrompt(selectedTurnId)
          : Promise.resolve<PromptResponse | null>(null),
      [selectedTurnId],
    ),
  );
  const prompt = promptResource.data;
  const loading = selectedTurnId ? promptResource.loading : false;

  // Single error region: surface a list/prompt load failure or a diff failure.
  const displayError =
    error ?? turnsResource.error?.message ?? promptResource.error?.message ?? null;

  const previousTurnId = useMemo(() => {
    if (!selectedTurnId) return null;
    const idx = turns.findIndex((t) => t.turn_id === selectedTurnId);
    if (idx < 0 || idx + 1 >= turns.length) return null;
    return turns[idx + 1]!.turn_id;
  }, [turns, selectedTurnId]);

  const loadDiff = async () => {
    if (!selectedTurnId || !previousTurnId) return;
    try {
      const d = await observabilityApi.diffPrompts(selectedTurnId, previousTurnId);
      setDiff(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section className="route campaign-prompt-debug" aria-labelledby="prompt-debug-heading">
      <header className="route-header">
        <h2 id="prompt-debug-heading">What did the model see?</h2>
        <p className="muted">
          The verbatim prompt captured for a turn, with per-message tier, token estimate, and source
          attribution.
        </p>
      </header>

      <div className="prompt-debug-layout">
        <aside className="prompt-debug-sidebar" aria-label="Turns">
          <h3>Turns</h3>
          {turns.length === 0 ? (
            <p className="muted">No turns recorded yet.</p>
          ) : (
            <ul className="turn-list">
              {turns.map((t) => (
                <li
                  key={t.turn_id}
                  className={t.turn_id === selectedTurnId ? "turn-row active" : "turn-row"}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedTurnId(t.turn_id);
                      setDiff(null);
                      setError(null);
                    }}
                  >
                    <strong>{t.turn_id}</strong>
                    <span className="muted">
                      {t.started_at ? new Date(t.started_at).toLocaleString() : ""}
                    </span>
                    {t.player_input ? (
                      <span className="turn-player-input">{truncate(t.player_input, 80)}</span>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="prompt-debug-main">
          {displayError && (
            <p className="error" role="alert">
              {displayError}
            </p>
          )}
          {loading && <p className="muted">Loading prompt…</p>}
          {!loading && prompt && (
            <PromptDetails
              prompt={prompt}
              onDiff={loadDiff}
              hasPrevious={previousTurnId != null}
              previousTurnId={previousTurnId}
              diff={diff}
              onClearDiff={() => setDiff(null)}
            />
          )}
        </div>
      </div>
    </section>
  );
}

interface DetailsProps {
  prompt: PromptResponse;
  onDiff: () => void;
  hasPrevious: boolean;
  previousTurnId: string | null;
  diff: PromptDiff | null;
  onClearDiff: () => void;
}

function PromptDetails({
  prompt,
  onDiff,
  hasPrevious,
  previousTurnId,
  diff,
  onClearDiff,
}: DetailsProps) {
  const totalTokens = prompt.messages.reduce((acc, m) => acc + (m.tokens || 0), 0);
  return (
    <>
      <section className="prompt-meta">
        <dl>
          <div>
            <dt>Messages hash</dt>
            <dd>
              <code>{prompt.messages_hash || "—"}</code>
            </dd>
          </div>
          <div>
            <dt>Total est. tokens</dt>
            <dd>{totalTokens}</dd>
          </div>
          <div>
            <dt>Source count</dt>
            <dd>{prompt.sources.length}</dd>
          </div>
        </dl>
        {prompt.composition_snapshot && (
          <details className="prompt-composition">
            <summary>Composition snapshot</summary>
            <pre>{JSON.stringify(prompt.composition_snapshot, null, 2)}</pre>
          </details>
        )}
      </section>

      <section className="prompt-budget">
        <h3>Per-tier budget</h3>
        <ul className="tier-budget-list">
          {Object.entries(prompt.budget_used).map(([tier, used]) => (
            <li key={tier}>
              <span className="tier-label">{tier}</span>
              <span className="tier-tokens">{used} tok</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="prompt-messages">
        <h3>Messages ({prompt.messages.length})</h3>
        <ol className="message-list">
          {prompt.messages.map((m, idx) => (
            <li key={idx} className={`message-row tier-${m.tier ?? "unknown"}`}>
              <header className="message-header">
                <span className="message-role">{m.role}</span>
                {m.tier && <span className="message-tier">{m.tier}</span>}
                <span className="message-tokens">{m.tokens} tok</span>
              </header>
              <pre className="message-body">{m.content}</pre>
            </li>
          ))}
        </ol>
      </section>

      <section className="prompt-sources">
        <h3>Sources ({prompt.sources.length})</h3>
        {prompt.sources.length === 0 ? (
          <p className="muted">No structured sources recorded.</p>
        ) : (
          <table className="source-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Owner</th>
                <th>Scope</th>
                <th>Tier</th>
                <th>Tokens</th>
                <th>Override</th>
              </tr>
            </thead>
            <tbody>
              {prompt.sources.map((s, idx) => (
                <tr key={`${s.source_id}-${idx}`}>
                  <td>{s.kind}</td>
                  <td>{s.owner_id ?? <span className="muted">—</span>}</td>
                  <td>{s.scope}</td>
                  <td>{s.tier}</td>
                  <td>{s.tokens}</td>
                  <td>{s.override_applied ? "yes" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="prompt-diff">
        <h3>Diff vs previous turn</h3>
        {hasPrevious ? (
          <>
            <p>
              Compare against turn <code>{previousTurnId}</code>.{" "}
              <button type="button" onClick={onDiff}>
                Compute diff
              </button>
              {diff && (
                <button type="button" onClick={onClearDiff}>
                  Hide
                </button>
              )}
            </p>
            {diff && <DiffPanel diff={diff} />}
          </>
        ) : (
          <p className="muted">No previous turn to diff against.</p>
        )}
      </section>
    </>
  );
}

function DiffPanel({ diff }: { diff: PromptDiff }) {
  const noChanges =
    diff.added_messages.length === 0 &&
    diff.removed_messages.length === 0 &&
    diff.changed_messages.length === 0 &&
    diff.added_sources.length === 0 &&
    diff.removed_sources.length === 0;
  return (
    <div className="diff-panel">
      <p>
        Messages hash changed: <strong>{diff.messages_hash_changed ? "yes" : "no"}</strong>
      </p>
      {noChanges && <p className="muted">Prompts are structurally identical.</p>}
      {diff.changed_messages.length > 0 && (
        <section>
          <h4>Changed messages ({diff.changed_messages.length})</h4>
          <ul>
            {diff.changed_messages.map((c, idx) => (
              <li key={idx}>
                <header>
                  <span className="message-role">{c.role}</span>
                  {c.tier && <span className="message-tier">{c.tier}</span>}
                </header>
                <div className="diff-sides">
                  <div className="diff-side diff-before">
                    <h5>Before</h5>
                    <pre>{c.before.content}</pre>
                  </div>
                  <div className="diff-side diff-after">
                    <h5>After</h5>
                    <pre>{c.after.content}</pre>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
      {diff.added_messages.length > 0 && (
        <section>
          <h4>Added messages ({diff.added_messages.length})</h4>
          <ul>
            {diff.added_messages.map((m, idx) => (
              <li key={idx}>
                <header>
                  <span className="message-role">{m.role}</span>
                  {m.tier && <span className="message-tier">{m.tier}</span>}
                  <span className="message-tokens">{m.tokens} tok</span>
                </header>
                <pre>{m.content}</pre>
              </li>
            ))}
          </ul>
        </section>
      )}
      {diff.removed_messages.length > 0 && (
        <section>
          <h4>Removed messages ({diff.removed_messages.length})</h4>
          <ul>
            {diff.removed_messages.map((m, idx) => (
              <li key={idx}>
                <header>
                  <span className="message-role">{m.role}</span>
                  {m.tier && <span className="message-tier">{m.tier}</span>}
                  <span className="message-tokens">{m.tokens} tok</span>
                </header>
                <pre>{m.content}</pre>
              </li>
            ))}
          </ul>
        </section>
      )}
      {(diff.added_sources.length > 0 || diff.removed_sources.length > 0) && (
        <section>
          <h4>Source attribution</h4>
          {diff.added_sources.length > 0 && (
            <div>
              <h5>Added sources ({diff.added_sources.length})</h5>
              <ul>
                {diff.added_sources.map((s, idx) => (
                  <li key={`add-${idx}`}>
                    {s.kind} · {s.owner_id ?? "—"} · {s.tier ?? "—"}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {diff.removed_sources.length > 0 && (
            <div>
              <h5>Removed sources ({diff.removed_sources.length})</h5>
              <ul>
                {diff.removed_sources.map((s, idx) => (
                  <li key={`rem-${idx}`}>
                    {s.kind} · {s.owner_id ?? "—"} · {s.tier ?? "—"}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      {Object.keys(diff.tier_budget_shifts).length > 0 && (
        <section>
          <h4>Per-tier budget shift</h4>
          <ul className="tier-shifts">
            {Object.entries(diff.tier_budget_shifts).map(([tier, delta]) => (
              <li key={tier}>
                <span className="tier-label">{tier}</span>
                <span className={delta === 0 ? "muted" : delta > 0 ? "delta-pos" : "delta-neg"}>
                  {delta > 0 ? `+${delta}` : delta}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return `${s.slice(0, n)}…`;
}
