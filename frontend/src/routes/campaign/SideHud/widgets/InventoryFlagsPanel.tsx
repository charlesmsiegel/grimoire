import { useCallback, useEffect, useState } from "react";

import { inventoryApi, type InventoryFlag } from "../../../../api/inventory";
import { useCampaignEvent } from "../../../../state/useCampaignEvent";

export function InventoryFlagsList({
  flags,
  onResolve,
}: {
  flags: InventoryFlag[];
  onResolve: (id: string) => void;
}) {
  if (flags.length === 0) return null;
  return (
    <ul className="inventory-flags">
      {flags.map((f) => (
        <li key={f.id}>
          <span className="flag-reason">{f.flag_reason}</span>
          <code className="flag-op">{f.op_json}</code>
          <button type="button" onClick={() => onResolve(f.id)}>
            Resolve
          </button>
        </li>
      ))}
    </ul>
  );
}

export function InventoryFlagsPanel({ campaignId }: { campaignId: string }) {
  const [flags, setFlags] = useState<InventoryFlag[]>([]);

  const refresh = useCallback(() => {
    void inventoryApi
      .flags(campaignId, false)
      .then((r) => setFlags(r.flags))
      .catch(() => setFlags([]));
  }, [campaignId]);

  useEffect(refresh, [refresh]);
  useCampaignEvent("inventory_flagged", refresh);

  const onResolve = useCallback(
    (id: string) => {
      void inventoryApi.resolveFlag(campaignId, id).then(refresh);
    },
    [campaignId, refresh],
  );

  if (flags.length === 0) return null;
  return (
    <section aria-label="Inventory review" className="inventory-flags-panel">
      <h3>Inventory review ({flags.length})</h3>
      <InventoryFlagsList flags={flags} onResolve={onResolve} />
    </section>
  );
}
