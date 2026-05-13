import type { ApiScene } from "../../api/campaign";
import { SourceBadge } from "./SourceBadge";

interface Props {
  scene: ApiScene | null;
}

function formatTime(scene: ApiScene): string | null {
  const moment = scene.in_game_start?.moment;
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
      <header className="scene-header scene-header-empty">
        <p>No active scene. Submit a post or open one from the Timeline view.</p>
      </header>
    );
  }

  const time = formatTime(scene);
  const cast = scene.present_character_refs;

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
        {scene.location_ref && (
          <div className="scene-header-meta-item">
            <dt>Location</dt>
            <dd>
              {scene.location_ref}
              <SourceBadge source="library" />
            </dd>
          </div>
        )}
        {time && (
          <div className="scene-header-meta-item">
            <dt>Time</dt>
            <dd>{time}</dd>
          </div>
        )}
        {cast.length > 0 && (
          <div className="scene-header-meta-item">
            <dt>Present</dt>
            <dd>
              {cast.map((ref, idx) => (
                <span key={ref} className="scene-header-cast">
                  {idx > 0 && ", "}
                  {ref}
                  <SourceBadge source="library" />
                </span>
              ))}
            </dd>
          </div>
        )}
      </dl>
    </header>
  );
}
