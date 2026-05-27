/**
 * "What changed?" debug surface for a single turn (issue #351 / spec
 * §6 of the observability remaining-design).
 *
 * Pulls ``/api/observability/turns/{turn_id}/deltas`` and renders the
 * applied + queued deltas grouped by kind. Two filters live on top:
 * a confidence floor and a free-text source/strategy match. The panel
 * stays usable when there's no turn yet (no posts) and when the audit
 * endpoint 404s (turn audit not produced — common in tests).
 */

import { useEffect, useMemo, useState } from "react";

import { observabilityApi, type TurnDeltaDiff, type TurnDeltaEntry } from "../../api/observability";
import { ApiError } from "../../api/client";

interface Props {
  turnId: string | null;
}

interface State {
  status: "idle" | "loading" | "ok" | "missing" | "error";
  diff: TurnDeltaDiff | null;
  error: string | null;
}

const INITIAL_STATE: State = { status: "idle", diff: null, error: null };

export function WhatChangedPanel({ turnId }: Props) {
  const [state, setState] = useState<State>(INITIAL_STATE);
  const [minConfidence, setMinConfidence] = useState(0);
  const [sourceFilter, setSourceFilter] = useState("");
  const [showQueued, setShowQueued] = useState(true);

  useEffect(() => {
    if (!turnId) {
      setState(INITIAL_STATE);
      return;
    }
    const controller = new AbortController();
    setState({ status: "loading", diff: null, error: null });
    observabilityApi
      .turnDeltas(turnId, controller.signal)
      .then((diff) => {
        setState({ status: "ok", diff, error: null });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) {
          setState({ status: "missing", diff: null, error: null });
          return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        setState({ status: "error", diff: null, error: msg });
      });
    return () => controller.abort();
  }, [turnId]);

  const filtered = useMemo(() => {
    if (!state.diff) return null;
    const wanted = sourceFilter.trim().toLowerCase();
    const matches = (e: TurnDeltaEntry) =>
      (e.confidence ?? 1) >= minConfidence &&
      (wanted === "" || (e.source ?? "").toLowerCase().includes(wanted));
    return {
      applied: state.diff.applied.filter(matches),
      queued: showQueued ? state.diff.queued.filter(matches) : [],
    };
  }, [state.diff, minConfidence, sourceFilter, showQueued]);

  return (
    <div className="scene-setting-block what-changed-panel" aria-label="What changed this turn">
      <div className="scene-setting-entry scene-setting-entry-full">
        <span className="scene-setting-label">What changed?</span>
        <div className="what-changed-filters">
          <label>
            Conf
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={minConfidence}
              onChange={(e) => setMinConfidence(Number(e.target.value))}
              aria-label="Minimum confidence"
            />
            <span className="what-changed-filter-value">{minConfidence.toFixed(2)}</span>
          </label>
          <label className="what-changed-toggle">
            <input
              type="checkbox"
              checked={showQueued}
              onChange={(e) => setShowQueued(e.target.checked)}
            />
            Queued
          </label>
        </div>
        <div className="what-changed-filters">
          <label className="what-changed-source-filter">
            Source
            <input
              type="search"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
              placeholder="e.g. extractor"
              aria-label="Filter by source"
            />
          </label>
        </div>
      </div>
      <div className="scene-setting-entry scene-setting-entry-full">
        <Body state={state} filtered={filtered} />
      </div>
    </div>
  );
}

function Body({
  state,
  filtered,
}: {
  state: State;
  filtered: TurnDeltaDiff | null;
}) {
  if (state.status === "idle") {
    return <p className="side-empty">Waiting for the first turn of the scene.</p>;
  }
  if (state.status === "loading") {
    return (
      <p className="side-empty" aria-busy="true">
        Loading deltas…
      </p>
    );
  }
  if (state.status === "missing") {
    return <p className="side-empty">No audit record yet for this turn.</p>;
  }
  if (state.status === "error") {
    return (
      <p className="side-empty" role="alert">
        Couldn’t load deltas: {state.error}
      </p>
    );
  }
  if (!filtered) return null;
  const total = filtered.applied.length + filtered.queued.length;
  if (total === 0) {
    return <p className="side-empty">No deltas matched the current filters.</p>;
  }
  return (
    <>
      <DeltaSection title="Auto-applied" entries={filtered.applied} variant="applied" />
      {filtered.queued.length > 0 && (
        <DeltaSection title="Queued for review" entries={filtered.queued} variant="queued" />
      )}
    </>
  );
}

function DeltaSection({
  title,
  entries,
  variant,
}: {
  title: string;
  entries: TurnDeltaEntry[];
  variant: "applied" | "queued";
}) {
  const grouped = useMemo(() => groupByKind(entries), [entries]);
  return (
    <section className="side-section what-changed-section" data-variant={variant}>
      <h3>
        {title} <span className="what-changed-count">({entries.length})</span>
      </h3>
      {Object.entries(grouped).map(([kind, items]) => (
        <div key={kind} className="what-changed-group">
          <h4 className="what-changed-kind">{kind}</h4>
          <ul className="side-list">
            {items.map((e) => (
              <DeltaItem key={e.id} entry={e} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

function DeltaItem({ entry }: { entry: TurnDeltaEntry }) {
  const confidencePct =
    entry.confidence == null ? null : Math.round(entry.confidence * 100);
  return (
    <li className="what-changed-item">
      <div className="what-changed-target">
        <code>{entry.target_id ?? "—"}</code>
        {entry.target_scope && (
          <span className="what-changed-scope">{entry.target_scope}</span>
        )}
      </div>
      {entry.evidence && <p className="what-changed-evidence">“{entry.evidence}”</p>}
      <div className="what-changed-meta">
        {entry.strategy && (
          <span className="what-changed-chip" title="Producing strategy">
            {entry.strategy}
          </span>
        )}
        {confidencePct !== null && (
          <span className="what-changed-chip" title="Confidence">
            {confidencePct}%
          </span>
        )}
        {entry.review_status && (
          <span
            className="what-changed-chip what-changed-chip-review"
            title="Review queue status"
          >
            {entry.review_status}
          </span>
        )}
      </div>
    </li>
  );
}

function groupByKind(entries: TurnDeltaEntry[]): Record<string, TurnDeltaEntry[]> {
  const out: Record<string, TurnDeltaEntry[]> = {};
  for (const e of entries) {
    const key = e.kind || "other";
    if (!out[key]) out[key] = [];
    out[key].push(e);
  }
  return out;
}
