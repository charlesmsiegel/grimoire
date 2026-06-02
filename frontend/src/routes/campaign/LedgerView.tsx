import { useCallback, useState } from "react";
import { useParams } from "react-router-dom";

import { campaignApi } from "../../api/campaign";
import type { Commitment, ContinuityLedger, ContradictionReport, Fact } from "../../api/campaign";
import { useApi } from "../../api/useApi";
import { CardIconBar } from "../../components/CardIconBar";
import { Loading } from "./common";

interface Props {
  campaignId: string;
}

export function LedgerView({ campaignId }: Props) {
  const state = useApi(useCallback(() => campaignApi.getLedger(campaignId), [campaignId]));

  return (
    <section className="route campaign-ledger" aria-labelledby="ledger-heading">
      <header className="route-header">
        <h2 id="ledger-heading">Continuity Ledger</h2>
      </header>
      <Loading state={state}>{(ledger) => <LedgerSections ledger={ledger} />}</Loading>
    </section>
  );
}

export function LedgerRoute() {
  const { campaignId } = useParams();
  if (!campaignId) return null;
  return <LedgerView campaignId={campaignId} />;
}

function LedgerSections({ ledger }: { ledger: ContinuityLedger }) {
  return (
    <div className="ledger-layout">
      <CommitmentSection
        title="Open Commitments"
        items={ledger.open_commitments}
        emptyMessage="No open commitments."
      />
      <CommitmentSection
        title="Overdue"
        items={ledger.overdue_commitments}
        emptyMessage="No overdue commitments."
        warning
      />
      <CommitmentSection
        title="Stale Threads"
        items={ledger.stale_commitments}
        emptyMessage="No stale threads."
      />
      <FactsSection facts={ledger.recent_facts} />
      <ContradictionsSection reports={ledger.unresolved_contradictions} />
    </div>
  );
}

function CommitmentSection({
  title,
  items,
  emptyMessage,
  warning,
}: {
  title: string;
  items: Commitment[];
  emptyMessage: string;
  warning?: boolean;
}) {
  return (
    <section
      className={warning ? "ledger-section ledger-warning" : "ledger-section"}
      aria-label={title}
    >
      <h3>{title}</h3>
      {items.length === 0 ? (
        <p className="muted">{emptyMessage}</p>
      ) : (
        <ul className="entity-list">
          {items.map((c) => (
            <li key={c.id} className="entity-card">
              <div className="entity-card-head">
                <span className="entity-name">{c.text}</span>
                <small className="entity-meta">{c.status}</small>
              </div>
              {(c.owed_by || c.owed_to) && (
                <small className="entity-meta">
                  {c.owed_by && <>by {c.owed_by} </>}
                  {c.owed_to && <>to {c.owed_to}</>}
                </small>
              )}
              <CardIconBar actions={[]} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FactsSection({ facts }: { facts: Fact[] }) {
  return (
    <section className="ledger-section" aria-label="Recent Facts">
      <h3>Recent Facts</h3>
      {facts.length === 0 ? (
        <p className="muted">No recent facts.</p>
      ) : (
        <ul className="side-list">
          {facts.map((f) => (
            <li key={f.id}>{f.text}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ContradictionsSection({ reports }: { reports: ContradictionReport[] }) {
  return (
    <section className="ledger-section" aria-label="Unresolved Contradictions">
      <h3>Unresolved Contradictions</h3>
      {reports.length === 0 ? (
        <p className="muted">No unresolved contradictions.</p>
      ) : (
        <ul className="entity-list">
          {reports.map((r) => (
            <ContradictionItem key={r.id} report={r} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ContradictionItem({ report }: { report: ContradictionReport }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="entity-card">
      <button
        type="button"
        className="ledger-disclosure"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="entity-name">{report.candidate_fact.text}</span>
        <small className="entity-meta">
          {report.conflicts.length} conflict{report.conflicts.length === 1 ? "" : "s"}
        </small>
      </button>
      {open && (
        <ul className="side-list">
          {report.conflicts.map((c, i) => (
            <li key={i}>
              <strong>{c.verdict}</strong>
              <span className="muted">
                {" "}
                · {c.existing_fact.text} ({Math.round(c.similarity * 100)}%)
              </span>
            </li>
          ))}
        </ul>
      )}
      <CardIconBar actions={[]} />
    </li>
  );
}
