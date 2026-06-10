import type { ApiScene } from "../../api/campaign";

interface Props {
  scene: ApiScene | null;
}

function formatTime(scene: ApiScene): string | null {
  const moment = scene.in_game_start;
  if (!moment) return null;
  const dt = new Date(moment);
  if (Number.isNaN(dt.getTime())) return moment;
  return dt.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function SceneHeader({ scene }: Props) {
  if (!scene) {
    return (
      <header className="scene-header">
        <p className="empty-state">
          No active scene. Submit a post or open one from the Timeline view.
        </p>
      </header>
    );
  }

  const time = formatTime(scene);

  return (
    <header className="scene-header" aria-label="Scene context">
      <div className="scene-header-row">
        <h2 className="scene-header-title">
          {scene.title || scene.slug || `Scene ${scene.ordinal}`}
        </h2>
        {scene.closed && <span className="scene-header-flag">closed</span>}
        {scene.mood && <span className="scene-header-mood">mood: {scene.mood}</span>}
      </div>
      <dl className="scene-header-meta">
        {time && (
          <div className="scene-header-meta-item">
            <dt>Time</dt>
            <dd>{time}</dd>
          </div>
        )}
      </dl>
    </header>
  );
}
