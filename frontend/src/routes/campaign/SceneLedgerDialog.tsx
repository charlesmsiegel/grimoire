import { useCallback, useEffect, useState } from "react";

import { newSceneApi } from "../../api/campaign/newScene";
import type { LedgerEntry } from "../../api/campaign/types";

interface Props {
  campaignId: string;
  open: boolean;
  onClose: () => void;
}

export function SceneLedgerDialog({ campaignId, open, onClose }: Props) {
  const [items, setItems] = useState<LedgerEntry[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await newSceneApi.listLedger(campaignId);
      setItems(resp);
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const toggleStatus = useCallback(
    async (item: LedgerEntry) => {
      const newStatus = item.status === "dismissed" ? "active" : "dismissed";
      await newSceneApi.updateLedger(campaignId, item.id, newStatus);
      await load();
    },
    [campaignId, load],
  );

  if (!open) return null;

  const active = items.filter((i) => i.status === "active");
  const used = items.filter((i) => i.status === "used");
  const dismissed = items.filter((i) => i.status === "dismissed");

  return (
    <div className="ledger-dialog-backdrop" onClick={onClose}>
      <div className="ledger-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="ledger-header">
          <h2>Scene Ledger</h2>
          <button onClick={onClose} className="close-btn">
            &times;
          </button>
        </div>

        {loading ? (
          <p className="ledger-loading">Loading...</p>
        ) : (
          <div className="ledger-content">
            {active.length > 0 && (
              <section>
                <h3>Active</h3>
                {active.map((item) => (
                  <LedgerRow
                    key={item.id}
                    item={item}
                    onToggle={() => toggleStatus(item)}
                    actionLabel="Dismiss"
                  />
                ))}
              </section>
            )}
            {used.length > 0 && (
              <section>
                <h3>Used</h3>
                {used.map((item) => (
                  <LedgerRow key={item.id} item={item} />
                ))}
              </section>
            )}
            {dismissed.length > 0 && (
              <section>
                <h3>Dismissed</h3>
                {dismissed.map((item) => (
                  <LedgerRow
                    key={item.id}
                    item={item}
                    onToggle={() => toggleStatus(item)}
                    actionLabel="Restore"
                  />
                ))}
              </section>
            )}
            {items.length === 0 && (
              <p className="ledger-empty">No scene ideas yet.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LedgerRow({
  item,
  onToggle,
  actionLabel,
}: {
  item: LedgerEntry;
  onToggle?: () => void;
  actionLabel?: string;
}) {
  return (
    <div className="ledger-row">
      <span className={`source-badge ${item.source}`}>{item.source}</span>
      <span className="ledger-summary">{item.summary}</span>
      {onToggle && actionLabel && (
        <button onClick={onToggle} className="ledger-action">
          {actionLabel}
        </button>
      )}
    </div>
  );
}
