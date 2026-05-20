/**
 * SideHud — campaign dashboard pane that lives to the right of the scene.
 *
 * Renders widgets in the order returned by ``GET /hud`` (the server has
 * already applied the user's ordering + visibility filters via
 * ``hud.yaml``). Each widget gets a render-hint component; the WS bridge
 * in ``useHud`` triggers per-widget refresh in the background.
 *
 * Layout is pure CSS — the parent already provides the flex column slot.
 * On narrow viewports the parent stacks the HUD under the scene pane.
 */

import type { WidgetSnapshot } from "../../../api/hud";
import { AuxInflightBadge } from "./AuxInflightBadge";
import { useHud } from "./useHud";
import { BannerWidget } from "./widgets/BannerWidget";
import { BlockWidget } from "./widgets/BlockWidget";
import { ChipListWidget } from "./widgets/ChipListWidget";
import { CompositeWidget } from "./widgets/CompositeWidget";
import { RowWidget } from "./widgets/RowWidget";

interface Props {
  campaignId: string;
}

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

export function SideHud({ campaignId }: Props) {
  const hud = useHud(campaignId);

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
      <header className="side-hud-header">
        <h2>Dashboard</h2>
        <AuxInflightBadge campaignId={campaignId} />
        <button
          type="button"
          className="side-hud-refresh"
          onClick={hud.refresh}
          aria-label="Refresh HUD"
        >
          Refresh
        </button>
      </header>
      {hud.widgets.length === 0 ? (
        <p className="side-hud-empty">No widgets configured.</p>
      ) : (
        <ul className="side-hud-widgets">
          {hud.widgets.map((w) => (
            <li key={w.snapshot.id} className="side-hud-widget-slot">
              {renderWidget(
                // Surface the stale flag we track client-side via the snapshot,
                // so widget components only need to read snapshot.stale.
                w.stale ? { ...w.snapshot, stale: true } : w.snapshot,
                () => hud.refreshWidget(w.snapshot.id),
              )}
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
