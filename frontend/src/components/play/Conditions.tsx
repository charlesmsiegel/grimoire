import { Link } from "react-router-dom";
import type { SceneDatetime, SceneLocation, SceneWeather } from "../../api/client";

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="conditions-row">
      <span className="conditions-label">{label}</span>
      <span className="conditions-value">{value}</span>
    </div>
  );
}

/** WHERE / WHEN / SKY, pinned to the foot of the context column.
 *
 *  Pinned rather than scrolled with the rest: it is true of the scene whether
 *  you are looking at the cast or at one person's dossier, and a fact you have
 *  to scroll a column to re-read is a fact you stop re-reading. It lives
 *  outside the column's scroll port for that reason — see `PageShell`, whose
 *  structure exists to make this survive a short viewport. */
export default function Conditions(
  { cid, worldName, location, datetime, weather }: {
    cid: string;
    worldName: string;
    location: SceneLocation | null;
    datetime: SceneDatetime | null;
    weather: SceneWeather | null;
  },
) {
  const where = location?.current?.name ?? "";
  const when = datetime?.current
    ? [datetime.current.weekday, datetime.current.friendly].filter(Boolean).join(" ")
    : "";
  const sky = weather?.weather
    ? [weather.weather.condition, weather.weather.temperature, weather.weather.wind]
        .filter(Boolean).join(" · ")
    : "";

  return (
    <div className="conditions">
      {/* An unset condition is dropped rather than dashed. A scene with no
          location recorded has not been placed yet; "WHERE —" claims it has
          been placed nowhere. */}
      {where && <Line label="Where" value={where} />}
      {when && <Line label="When" value={when} />}
      {sky && <Line label="Sky" value={sky} />}
      {!where && !when && !sky && (
        <p className="column-empty">No place or time set for this scene yet.</p>
      )}
      {/* The campaign's own copy of the world, not the world itself — editing
          here reaches this campaign only, and the label is where that gets
          said before the click rather than after. */}
      <Link className="world-copy" to={`/campaigns/${cid}/world`}>
        <span className="section-label">World copy</span>
        <span className="world-copy-name">
          {worldName || "this campaign"} · this campaign's
        </span>
        <span className="world-copy-arrow" aria-hidden>→</span>
      </Link>
    </div>
  );
}
