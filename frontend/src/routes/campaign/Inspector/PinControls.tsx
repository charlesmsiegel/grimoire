/**
 * Pin / Exclude buttons with TTL picker for a single source.
 */

import { useState } from "react";

import { inspectorApi, type ContextSourceExplanation } from "../../../api/inspector";

interface Props {
  campaignId: string;
  source: ContextSourceExplanation;
  onChanged?: () => void;
}

export function PinControls({ campaignId, source, onChanged }: Props) {
  const [ttl, setTtl] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const callPinOrExclude = async (kind: "pin" | "exclude") => {
    setBusy(true);
    try {
      const ttlTurns = ttl === "" ? null : Math.max(1, Number(ttl) || 1);
      await inspectorApi.pin(campaignId, {
        kind,
        target: source.source_id
          ? { source_id: source.source_id }
          : {
              entity_kind: source.kind,
              entity_id: source.owner_id ?? "",
            },
        ttlTurns,
      });
      onChanged?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="inspector-pin-controls"
      role="group"
      aria-label={`Pin controls for ${source.source_id}`}
    >
      <label className="inspector-ttl">
        TTL:&nbsp;
        <select
          value={ttl}
          onChange={(e) => setTtl(e.target.value)}
          disabled={busy}
          aria-label="Pin/exclude TTL in turns"
        >
          <option value="">forever</option>
          <option value="1">1 turn</option>
          <option value="3">3 turns</option>
          <option value="5">5 turns</option>
          <option value="10">10 turns</option>
        </select>
      </label>
      <button
        type="button"
        className="inspector-pin-btn"
        onClick={() => void callPinOrExclude("pin")}
        disabled={busy}
      >
        Pin
      </button>
      <button
        type="button"
        className="inspector-exclude-btn"
        onClick={() => void callPinOrExclude("exclude")}
        disabled={busy}
      >
        Exclude
      </button>
    </div>
  );
}
