import { useEffect, useState } from "react";

import type { ApiScene, OpenCommitment, PCEntry } from "../../api/campaign";
import { ApiError } from "../../api/client";
import type { ResolvedCharacter } from "../../api/types";
import { viewsApi } from "../../api/views";
import { AuxBrainstormPanel } from "./Auxiliary/AuxBrainstormPanel";
import { SourceBadge } from "./SourceBadge";

interface QuickActions {
  onRegenerate: () => void;
  onUndo: () => void;
  onEndScene: () => void;
  onSkipTime: () => void;
  onManualFact: () => void;
  busy: boolean;
}

interface Props {
  campaignId: string;
  scene: ApiScene | null;
  pcs: PCEntry[];
  commitments: OpenCommitment[];
  actions: QuickActions;
}

interface CapabilityChip {
  kind: string;
  label: string;
}

const MECHANIC_KEYS = ["rolls", "slots", "pools", "resources", "tracks"] as const;

function capabilityChips(cap: Record<string, unknown>): CapabilityChip[] {
  const kind = typeof cap.kind === "string" ? cap.kind : "capability";
  const name = typeof cap.name === "string" ? cap.name : undefined;
  const id = typeof cap.id === "string" ? cap.id : undefined;
  const label = name || id || kind;
  return [{ kind, label }];
}

export function SidePanel({ campaignId, scene, pcs, commitments, actions }: Props) {
  const present = scene?.present_character_refs ?? [];
  const threads = scene?.threads_introduced ?? [];

  return (
    <aside className="side-panel" aria-label="Scene side panel">
      <section className="side-section">
        <h3>Present cast</h3>
        {present.length === 0 ? (
          <p className="side-empty">No cast tracked yet.</p>
        ) : (
          <ul className="side-list">
            {present.map((ref) => {
              const pc = pcs.find((p) => p.character_ref === ref);
              return (
                <li key={ref}>
                  {pc ? <strong>{pc.name}</strong> : <span>{ref}</span>}
                  {/* A ref matched in the PC roster resolves from the
                      library; one only mentioned in present_character_refs
                      without a roster entry is an emergent NPC the model
                      brought into the scene. */}
                  <SourceBadge source={pc ? "library" : "emergent"} />
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="side-section">
        <h3>Active threads</h3>
        {threads.length === 0 ? (
          <p className="side-empty">No open threads.</p>
        ) : (
          <ul className="side-list">
            {threads.map((t, idx) => (
              <li key={`${idx}-${t.text}`}>{t.text}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="side-section">
        <h3>Open commitments</h3>
        {commitments.length === 0 ? (
          <p className="side-empty">No open commitments.</p>
        ) : (
          <ul className="side-list">
            {commitments.slice(0, 5).map((c) => (
              <li key={c.id}>{c.text}</li>
            ))}
          </ul>
        )}
      </section>

      <CapabilitiesSection campaignId={campaignId} pcs={pcs} />
      <MechanicsSummarySection campaignId={campaignId} pcs={pcs} />

      <section className="side-section">
        <AuxBrainstormPanel campaignId={campaignId} />
      </section>

      <section className="side-section">
        <h3>Quick actions</h3>
        <div className="side-actions">
          <button type="button" onClick={actions.onRegenerate} disabled={actions.busy}>
            Regenerate
          </button>
          <button type="button" onClick={actions.onUndo} disabled={actions.busy}>
            Undo turn
          </button>
          <button
            type="button"
            onClick={actions.onEndScene}
            disabled={actions.busy || !scene || scene.closed}
          >
            End scene
          </button>
          <button type="button" onClick={actions.onSkipTime} disabled={actions.busy || !scene}>
            Skip time
          </button>
          <button type="button" onClick={actions.onManualFact} disabled={actions.busy}>
            Manual fact
          </button>
        </div>
      </section>
    </aside>
  );
}

function useActivePcCharacters(
  campaignId: string,
  pcs: PCEntry[],
): { state: "loading" | "ready"; rows: ResolvedCharacter[] } {
  const [rows, setRows] = useState<ResolvedCharacter[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    setRows(null);
    if (pcs.length === 0) {
      setRows([]);
      return () => {
        cancelled = true;
      };
    }
    viewsApi
      .listCharacters(campaignId)
      .then((all) => {
        if (cancelled) return;
        const activeRefs = new Set(pcs.map((p) => p.character_ref));
        const matched = all.filter((c) => {
          const ref =
            c.character.world_id !== null
              ? `library:worlds/${c.character.world_id}/characters/${c.character.id}`
              : `campaign:emergent/character/${c.character.id}`;
          return activeRefs.has(ref);
        });
        setRows(matched);
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, pcs]);
  return rows === null ? { state: "loading", rows: [] } : { state: "ready", rows };
}

function CapabilitiesSection({ campaignId, pcs }: { campaignId: string; pcs: PCEntry[] }) {
  const { state, rows } = useActivePcCharacters(campaignId, pcs);
  return (
    <section className="side-section">
      <h3>Capabilities</h3>
      {state === "loading" ? (
        <p className="side-empty">Loading capabilities…</p>
      ) : rows.length === 0 ? (
        <p className="side-empty">No active PC capabilities surfaced.</p>
      ) : (
        <ul className="side-list">
          {rows.map((row) => {
            const chips = row.capabilities.flatMap(capabilityChips);
            return (
              <li key={row.character.id}>
                <strong>{row.character.name}</strong>
                {chips.length === 0 ? (
                  <span className="side-empty"> · none</span>
                ) : (
                  <ul className="capability-chip-list">
                    {chips.map((c, i) => (
                      <li key={i} className={`capability-chip capability-${c.kind}`}>
                        {c.label}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function MechanicsSummarySection({ campaignId, pcs }: { campaignId: string; pcs: PCEntry[] }) {
  const { rows } = useActivePcCharacters(campaignId, pcs);
  const [sheets, setSheets] = useState<Record<string, Record<string, unknown> | null>>({});

  useEffect(() => {
    let cancelled = false;
    if (rows.length === 0) {
      setSheets({});
      return () => {
        cancelled = true;
      };
    }
    Promise.all(
      rows.map(async (row) => {
        try {
          const sheet = await viewsApi.getSheet(campaignId, "character", row.character.id);
          return [row.character.id, sheet] as const;
        } catch (err) {
          if (err instanceof ApiError && err.status === 404) {
            return [row.character.id, null] as const;
          }
          return [row.character.id, null] as const;
        }
      }),
    ).then((pairs) => {
      if (cancelled) return;
      const next: Record<string, Record<string, unknown> | null> = {};
      for (const [id, sheet] of pairs) next[id] = sheet;
      setSheets(next);
    });
    return () => {
      cancelled = true;
    };
  }, [campaignId, rows]);

  // Build a compact mechanics summary per PC: any of the canonical top-level
  // keys (rolls/slots/pools/...) gets rendered as a "key: scalar/short"
  // list. Schemas vary across mechanics; this is best-effort.
  const sections = rows
    .map((row) => {
      const sheet = sheets[row.character.id];
      if (!sheet) return null;
      const entries: { key: string; value: string }[] = [];
      for (const key of MECHANIC_KEYS) {
        const raw = sheet[key];
        if (raw === undefined || raw === null) continue;
        if (typeof raw === "object" && !Array.isArray(raw)) {
          for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
            entries.push({ key: `${key}.${k}`, value: formatScalar(v) });
          }
        } else {
          entries.push({ key, value: formatScalar(raw) });
        }
      }
      if (entries.length === 0) return null;
      return { name: row.character.name, entries };
    })
    .filter((s): s is { name: string; entries: { key: string; value: string }[] } => s !== null);

  if (sections.length === 0) return null;
  return (
    <section className="side-section">
      <h3>Mechanics</h3>
      <ul className="side-list">
        {sections.map((s) => (
          <li key={s.name}>
            <strong>{s.name}</strong>
            <ul className="mechanics-summary">
              {s.entries.map((e, i) => (
                <li key={i}>
                  <span className="mechanics-key">{e.key}</span>
                  <span className="mechanics-value">{e.value}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </section>
  );
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "string") return value.length > 60 ? `${value.slice(0, 60)}…` : value;
  if (Array.isArray(value)) return `[${value.length}]`;
  return "{…}";
}
