import { useEffect, useMemo, useState } from "react";

import { canonicalizeCharacterRef, type ApiScene, type PCEntry } from "../../../api/campaign";
import { ApiError } from "../../../api/client";
import type { WidgetSnapshot } from "../../../api/hud";
import type { ResolvedCharacter } from "../../../api/types";
import { viewsApi } from "../../../api/views";
import { InspectorPanel } from "../Inspector/InspectorPanel";
import { WhatChangedPanel } from "../WhatChangedPanel";
import { AuxInflightBadge } from "./AuxInflightBadge";
import { PresentCastChip } from "./PresentCastChip";
import { parsePresentCast } from "./presentCastShape";
import { useHud, type HudWidgetState } from "./useHud";
import { BannerWidget } from "./widgets/BannerWidget";
import { BlockWidget } from "./widgets/BlockWidget";
import { ChipListWidget } from "./widgets/ChipListWidget";
import { CompositeWidget } from "./widgets/CompositeWidget";
import { InventoryFlagsPanel } from "./widgets/InventoryFlagsPanel";
import { RowWidget } from "./widgets/RowWidget";
import { asArray, asNumber, asRecord, asString, primaryScalar } from "./widgets/widget-common";

export interface QuickActions {
  onUndo: () => void;
  onEndScene: () => void;
  onAnalyzeScene: () => void;
  onDeleteScene: () => void;
  onNewScene: () => void;
  onOpenLedger: () => void;
  onSkipTime: () => void;
  onManualFact: () => void;
  onIllustrate: () => void;
  busy: boolean;
}

interface Props {
  campaignId: string;
  sceneId: string | null;
  scene: ApiScene | null;
  pcs: PCEntry[];
  actions: QuickActions;
  playerInput: string;
  pcRef?: string | null;
  latestNarratorTurnId: string | null;
}

const SCENE_SETTING_IDS = new Set([
  "core.in-game-date",
  "core.in-game-time",
  "core.weather",
  "core.temperature",
  "core.location",
]);

const CORE_WIDGET_IDS = new Set([
  ...SCENE_SETTING_IDS,
  "core.present-cast",
  "core.recent-events",
  "core.active-commitments",
  "core.scene-summary",
  "core.review-queue",
  "core.drift-alerts",
  "core.active-threads",
  "core.inventory",
]);

const RENDER_FALLBACK = "block";

function renderWidget(
  snapshot: WidgetSnapshot,
  onRefresh: () => void,
  campaignId: string,
): React.ReactNode {
  const hint = (snapshot.render_hint ?? RENDER_FALLBACK).toLowerCase();
  const key = snapshot.id;
  switch (hint) {
    case "row":
      return <RowWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "chip-list":
      return (
        <ChipListWidget
          key={key}
          snapshot={snapshot}
          campaignId={campaignId}
          onRefresh={onRefresh}
        />
      );
    case "banner":
      return <BannerWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "composite":
      return <CompositeWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "block":
    default:
      return <BlockWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
  }
}

/* ---- Info-block components (shared style) ---- */

function SceneSettingBlock({ widgets }: { widgets: HudWidgetState[] }) {
  const entries = widgets
    .filter((w) => w.snapshot.status === "ok")
    .map((w) => ({
      id: w.snapshot.id,
      label: w.snapshot.title ?? w.snapshot.id,
      value: primaryScalar(w.snapshot.data),
    }))
    .filter((e) => e.value !== null);

  if (entries.length === 0) return null;

  return (
    <div className="scene-setting-block" aria-label="Scene setting">
      {entries.map((e) => (
        <div key={e.id} className="scene-setting-entry">
          <span className="scene-setting-label">{e.label}</span>
          <span className="scene-setting-value">{e.value}</span>
        </div>
      ))}
    </div>
  );
}

function CastBlock({ widget, campaignId }: { widget: HudWidgetState | null; campaignId: string }) {
  if (!widget || widget.snapshot.status !== "ok") return null;
  const chips = parsePresentCast(widget.snapshot.data);
  if (chips.length === 0) return null;

  return (
    <div className="scene-setting-block" aria-label="Cast">
      <div className="scene-setting-entry scene-setting-entry-full">
        <span className="scene-setting-label">Cast</span>
        <span className="scene-setting-badges">
          {chips.map((chip) => (
            <PresentCastChip key={chip.character_id} chip={chip} campaignId={campaignId} />
          ))}
        </span>
      </div>
    </div>
  );
}

function extractTextItems(data: unknown): string[] {
  const rec = asRecord(data);
  const arr = asArray(rec?.items ?? data);
  if (!arr) return [];
  return arr
    .map((item) => {
      if (typeof item === "string") return item;
      const r = asRecord(item);
      if (!r) return null;
      return (
        asString(r.text) ??
        asString(r.statement) ??
        asString(r.label) ??
        asString(r.summary) ??
        asString(r.message) ??
        null
      );
    })
    .filter((s): s is string => s !== null);
}

function InfoListBlock({ widget, onRefresh }: { widget: HudWidgetState; onRefresh?: () => void }) {
  const { snapshot } = widget;
  if (snapshot.status === "hidden") return null;

  const title = snapshot.title ?? snapshot.id;

  if (snapshot.status !== "ok") {
    return (
      <div className="scene-setting-block" aria-label={title}>
        <div className="scene-setting-entry">
          <span className="scene-setting-label">{title}</span>
          <span className="scene-setting-value scene-setting-error">
            {snapshot.status === "timeout" ? "Timed out" : (snapshot.error ?? "Error")}
            {onRefresh && (
              <button
                type="button"
                className="scene-setting-retry"
                onClick={onRefresh}
                title="Retry"
              >
                ↻
              </button>
            )}
          </span>
        </div>
      </div>
    );
  }
  const rec = asRecord(snapshot.data);
  const text = rec ? asString(rec.text) : null;
  const count = rec ? asNumber(rec.count) : null;
  const items = extractTextItems(snapshot.data);

  const hasContent = text || items.length > 0 || (count !== null && count > 0);
  if (!hasContent) return null;

  return (
    <div className="scene-setting-block" aria-label={title}>
      {text ? (
        <div className="scene-setting-entry scene-setting-entry-full">
          <span className="scene-setting-label">{title}</span>
          <span className="scene-setting-text">{text}</span>
        </div>
      ) : count !== null && items.length === 0 ? (
        <div className="scene-setting-entry">
          <span className="scene-setting-label">{title}</span>
          <span className="scene-setting-value">{count}</span>
        </div>
      ) : (
        <div className="scene-setting-entry scene-setting-entry-full">
          <span className="scene-setting-label">{title}</span>
          <ul className="scene-setting-list">
            {items.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ---- Mechanics ---- */

const MECHANIC_KEYS = ["rolls", "slots", "pools", "resources", "tracks"] as const;

function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "string") return value.length > 60 ? `${value.slice(0, 60)}…` : value;
  if (Array.isArray(value)) return `[${value.length}]`;
  return "{…}";
}

function useActivePcCharacters(campaignId: string, pcs: PCEntry[]): { rows: ResolvedCharacter[] } {
  const [rows, setRows] = useState<ResolvedCharacter[]>([]);
  useEffect(() => {
    let cancelled = false;
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
        // Normalize both sides: PCs are stored under whatever ref spelling was
        // registered (often a wizard shorthand), while the ref built here is
        // canonical (#517).
        const activeRefs = new Set(pcs.map((p) => canonicalizeCharacterRef(p.character_ref)));
        setRows(
          all.filter((c) => {
            const ref = canonicalizeCharacterRef(
              c.character.world_id !== null
                ? `library:worlds/${c.character.world_id}/characters/${c.character.id}`
                : `campaign:emergent/character/${c.character.id}`,
            );
            return activeRefs.has(ref);
          }),
        );
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, pcs]);
  return { rows };
}

function MechanicsBlock({ campaignId, pcs }: { campaignId: string; pcs: PCEntry[] }) {
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
      return { id: row.character.id, name: row.character.name, entries };
    })
    .filter(
      (s): s is { id: string; name: string; entries: { key: string; value: string }[] } =>
        s !== null,
    );

  if (sections.length === 0) return null;

  return (
    <div className="scene-setting-block" aria-label="Mechanics">
      {sections.map((s) => (
        <div key={s.id} className="scene-setting-entry scene-setting-entry-full">
          <span className="scene-setting-label">{s.name}</span>
          <div className="scene-setting-mechanics">
            {s.entries.map((e, i) => (
              <span key={i} className="scene-setting-mechanic-pair">
                <span className="scene-setting-mechanic-key">{e.key}</span>{" "}
                <span className="scene-setting-mechanic-val">{e.value}</span>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ---- Quick Actions ---- */

function QuickActionsBlock({ actions, scene }: { actions: QuickActions; scene: ApiScene | null }) {
  return (
    <div className="scene-setting-block" aria-label="Quick actions">
      <div className="scene-setting-entry scene-setting-entry-full">
        <span className="scene-setting-label">Actions</span>
        <div className="hud-quick-actions">
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
          <button type="button" onClick={actions.onAnalyzeScene} disabled={actions.busy || !scene}>
            Analyze scene
          </button>
          <button
            type="button"
            onClick={actions.onDeleteScene}
            disabled={actions.busy || !scene}
            className="danger-btn"
          >
            Delete scene
          </button>
          <button
            type="button"
            onClick={actions.onNewScene}
            disabled={actions.busy || (scene != null && !scene.closed)}
          >
            New scene
          </button>
          <button type="button" onClick={actions.onOpenLedger} disabled={actions.busy}>
            Scene ledger
          </button>
          <button type="button" onClick={actions.onSkipTime} disabled={actions.busy || !scene}>
            Skip time
          </button>
          <button type="button" onClick={actions.onManualFact} disabled={actions.busy}>
            Manual fact
          </button>
          <button type="button" onClick={actions.onIllustrate} disabled={actions.busy || !scene}>
            Illustrate
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---- Main component ---- */

export function SideHud({
  campaignId,
  sceneId,
  scene,
  pcs,
  actions,
  playerInput,
  pcRef,
  latestNarratorTurnId,
}: Props) {
  const hud = useHud(campaignId, sceneId);

  const { sceneSetting, castWidget, orderedWidgets } = useMemo(() => {
    const setting: HudWidgetState[] = [];
    const rest: HudWidgetState[] = [];
    let cast: HudWidgetState | null = null;
    for (const w of hud.widgets) {
      if (SCENE_SETTING_IDS.has(w.snapshot.id)) setting.push(w);
      else if (w.snapshot.id === "core.present-cast") cast = w;
      else rest.push(w);
    }
    return { sceneSetting: setting, castWidget: cast, orderedWidgets: rest };
  }, [hud.widgets]);

  if (hud.loading && hud.widgets.length === 0) {
    return (
      <aside className="side-hud side-hud-loading" aria-busy="true" aria-label="Scene HUD">
        <p className="empty-state">Loading HUD…</p>
      </aside>
    );
  }

  if (hud.error && hud.widgets.length === 0) {
    return (
      <aside className="side-hud side-hud-error" role="alert" aria-label="Scene HUD">
        <p>Failed to load HUD: {hud.error}</p>
        <button type="button" onClick={hud.refresh}>
          Retry
        </button>
      </aside>
    );
  }

  return (
    <aside className="side-hud" aria-label="Scene HUD">
      <AuxInflightBadge campaignId={campaignId} />
      <SceneSettingBlock widgets={sceneSetting} />
      <CastBlock widget={castWidget} campaignId={campaignId} />
      {orderedWidgets.map((w) =>
        CORE_WIDGET_IDS.has(w.snapshot.id) ? (
          <InfoListBlock
            key={w.snapshot.id}
            widget={w}
            onRefresh={() => hud.refreshWidget(w.snapshot.id)}
          />
        ) : (
          <div key={w.snapshot.id} className="side-hud-widget-slot">
            {renderWidget(
              w.stale ? { ...w.snapshot, stale: true } : w.snapshot,
              () => hud.refreshWidget(w.snapshot.id),
              campaignId,
            )}
          </div>
        ),
      )}
      <MechanicsBlock campaignId={campaignId} pcs={pcs} />
      <QuickActionsBlock actions={actions} scene={scene} />
      <InspectorPanel
        campaignId={campaignId}
        playerInput={playerInput}
        sessionId={campaignId}
        pcRef={pcRef}
      />
      <WhatChangedPanel turnId={latestNarratorTurnId} />
      <InventoryFlagsPanel campaignId={campaignId} />
    </aside>
  );
}
