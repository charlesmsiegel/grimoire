import { useMemo } from "react";

import type { WidgetSnapshot } from "../../../api/hud";
import { AuxInflightBadge } from "./AuxInflightBadge";
import { useHud, type HudWidgetState } from "./useHud";
import { BannerWidget } from "./widgets/BannerWidget";
import { BlockWidget } from "./widgets/BlockWidget";
import { ChipListWidget } from "./widgets/ChipListWidget";
import { CompositeWidget } from "./widgets/CompositeWidget";
import { RowWidget } from "./widgets/RowWidget";
import { primaryScalar } from "./widgets/widget-common";

interface Props {
  campaignId: string;
  sceneId: string | null;
}

const SCENE_SETTING_IDS = new Set([
  "core.in-game-date",
  "core.in-game-time",
  "core.weather",
  "core.temperature",
  "core.location",
]);

const RENDER_FALLBACK = "block";

function renderWidget(
  snapshot: WidgetSnapshot,
  onRefresh: () => void,
): React.ReactNode {
  const hint = (snapshot.render_hint ?? RENDER_FALLBACK).toLowerCase();
  const key = snapshot.id;
  switch (hint) {
    case "row":
      return <RowWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "chip-list":
      return <ChipListWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "banner":
      return <BannerWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "composite":
      return <CompositeWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
    case "block":
    default:
      return <BlockWidget key={key} snapshot={snapshot} onRefresh={onRefresh} />;
  }
}

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

export function SideHud({ campaignId, sceneId }: Props) {
  const hud = useHud(campaignId, sceneId);

  const { sceneSetting, rest } = useMemo(() => {
    const s: HudWidgetState[] = [];
    const r: HudWidgetState[] = [];
    for (const w of hud.widgets) {
      if (SCENE_SETTING_IDS.has(w.snapshot.id)) s.push(w);
      else r.push(w);
    }
    return { sceneSetting: s, rest: r };
  }, [hud.widgets]);

  if (hud.loading && hud.widgets.length === 0) {
    return (
      <aside className="side-hud side-hud-loading" aria-busy="true" aria-label="Scene HUD">
        <p className="side-hud-empty">Loading HUD…</p>
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
      <div className="side-hud-toolbar">
        <AuxInflightBadge campaignId={campaignId} />
        <button
          type="button"
          className="side-hud-refresh icon-btn"
          onClick={hud.refresh}
          aria-label="Refresh HUD"
          title="Refresh HUD"
        >
          ↻
        </button>
      </div>
      <SceneSettingBlock widgets={sceneSetting} />
      {rest.length === 0 && sceneSetting.length === 0 ? (
        <p className="side-hud-empty">No widgets configured.</p>
      ) : (
        rest.length > 0 && (
          <ul className="side-hud-widgets">
            {rest.map((w) => (
              <li key={w.snapshot.id} className="side-hud-widget-slot">
                {renderWidget(
                  w.stale ? { ...w.snapshot, stale: true } : w.snapshot,
                  () => hud.refreshWidget(w.snapshot.id),
                )}
              </li>
            ))}
          </ul>
        )
      )}
    </aside>
  );
}
