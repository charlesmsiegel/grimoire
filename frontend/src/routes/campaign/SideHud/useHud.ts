/**
 * useHud — manages the SideHud's aggregate state.
 *
 * On mount we fetch the full aggregate. Each WS event we subscribe to maps
 * back to the widgets whose ``refresh_on`` includes that event; those
 * widgets are individually re-fetched via ``GET /hud/widgets/{id}`` so a
 * burst of unrelated events doesn't trigger a full aggregate refresh.
 *
 * Per-widget ``lastRefreshAt`` lets the layout flag stale widgets (no
 * refresh in N seconds despite expecting one). The widget descriptors
 * themselves are returned by ``/hud/widgets/available`` so we know the
 * ``refresh_on`` set client-side without re-deriving it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { hudApi, type HudWidget, type WidgetSnapshot } from "../../../api/hud";
import { useCampaignEvent } from "../../../state/useCampaignEvent";

const DEFAULT_STALE_THRESHOLD_S = 60;

export interface HudWidgetState {
  snapshot: WidgetSnapshot;
  descriptor: HudWidget | null;
  lastRefreshAt: number; // epoch ms
  stale: boolean;
}

export interface HudState {
  loading: boolean;
  error: string | null;
  sceneId: string | null;
  generatedAt: string | null;
  /** ordered the same way the server returned them */
  widgets: HudWidgetState[];
}

/**
 * Union of refresh_on events across all known widgets. We subscribe to
 * the full set so newly-loaded widgets are covered without re-subscribing.
 */
function collectRefreshEvents(descriptors: HudWidget[]): string[] {
  const seen = new Set<string>();
  for (const d of descriptors) {
    for (const evt of d.refresh_on) seen.add(evt);
  }
  return [...seen].sort();
}

export function useHud(campaignId: string, activeSceneId?: string | null): HudState & {
  refresh: () => void;
  refreshWidget: (widgetId: string) => void;
} {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sceneId, setSceneId] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [widgets, setWidgets] = useState<HudWidgetState[]>([]);
  const [descriptors, setDescriptors] = useState<HudWidget[]>([]);
  const descriptorsRef = useRef<HudWidget[]>([]);
  descriptorsRef.current = descriptors;

  const fetchAggregate = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const [agg, available] = await Promise.all([
          hudApi.aggregate(campaignId, signal, activeSceneId),
          // Tolerate available() failing — we still render with degraded
          // refresh wiring (no per-event mapping).
          hudApi.available(campaignId, signal).catch(() => [] as HudWidget[]),
        ]);
        if (signal?.aborted) return;
        const now = Date.now();
        const byId = new Map(available.map((d) => [d.id, d] as const));
        setDescriptors(available);
        setSceneId(agg.scene_id);
        setGeneratedAt(agg.generated_at);
        setWidgets(
          agg.widgets.map((snap) => ({
            snapshot: snap,
            descriptor: byId.get(snap.id) ?? null,
            lastRefreshAt: now,
            stale: snap.stale,
          })),
        );
        setError(null);
      } catch (e) {
        if (signal?.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [campaignId, activeSceneId],
  );

  const refreshWidget = useCallback(
    async (widgetId: string) => {
      try {
        const snap = await hudApi.widget(campaignId, widgetId);
        setWidgets((prev) => {
          const idx = prev.findIndex((w) => w.snapshot.id === widgetId);
          const existing = idx === -1 ? undefined : prev[idx];
          if (!existing) return prev;
          const next = prev.slice();
          next[idx] = {
            descriptor: existing.descriptor,
            snapshot: snap,
            lastRefreshAt: Date.now(),
            stale: snap.stale,
          };
          return next;
        });
      } catch {
        // Leave the existing snapshot; staleness will surface on its own.
      }
    },
    [campaignId],
  );

  useEffect(() => {
    setLoading(true);
    const controller = new AbortController();
    void fetchAggregate(controller.signal);
    return () => controller.abort();
  }, [fetchAggregate]);

  // Build a stable string of the union of refresh_on events so the WS
  // subscription only re-binds when the descriptor set changes.
  const eventsKey = useMemo(() => collectRefreshEvents(descriptors).join(","), [descriptors]);
  const eventTypes = useMemo(
    () => (eventsKey ? eventsKey.split(",") : []),
    [eventsKey],
  );

  useCampaignEvent(
    eventTypes,
    useCallback(
      (msg) => {
        const evt = msg.type;
        const affected = descriptorsRef.current
          .filter((d) => d.refresh_on.includes(evt))
          .map((d) => d.id);
        for (const wid of affected) void refreshWidget(wid);
      },
      [refreshWidget],
    ),
  );

  // Periodically re-flag widgets as stale if they haven't been refreshed
  // within their configured threshold. We only mark stale, never hide —
  // the widget keeps its last good data with a soft "Stale" badge.
  useEffect(() => {
    const tick = () => {
      setWidgets((prev) => {
        const now = Date.now();
        let mutated = false;
        const next = prev.map((w) => {
          const thresholdS = w.descriptor?.stale_threshold_s ?? DEFAULT_STALE_THRESHOLD_S;
          if (thresholdS <= 0) return w;
          // No refresh_on declared → never auto-stale, the data is supposed to be static.
          if (!w.descriptor || w.descriptor.refresh_on.length === 0) return w;
          const isStale = now - w.lastRefreshAt > thresholdS * 1000;
          if (isStale === w.stale) return w;
          mutated = true;
          return { ...w, stale: isStale };
        });
        return mutated ? next : prev;
      });
    };
    const id = window.setInterval(tick, 5_000);
    return () => window.clearInterval(id);
  }, []);

  return {
    loading,
    error,
    sceneId,
    generatedAt,
    widgets,
    refresh: () => void fetchAggregate(),
    refreshWidget: (id: string) => void refreshWidget(id),
  };
}
