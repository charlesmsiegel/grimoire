import { Link } from "react-router-dom";
import { ColumnSection } from "../PageShell";

const PAGES = [
  { key: "providers", label: "Providers", to: "/providers" },
  { key: "models", label: "Models", to: "/models" },
  { key: "presets", label: "Presets", to: "/presets" },
] as const;

/** Settings → Inference's three pages, at the top of each one's column
 *  (spec 1): the column indexing the page's neighbourhood. The rail still
 *  answers which page of the app this is (it lights Settings on all three). */
export function InferenceNav({ current }: { current: (typeof PAGES)[number]["key"] }) {
  return (
    <ColumnSection label="Inference">
      {PAGES.map((p) => (
        <Link key={p.key} to={p.to} aria-current={p.key === current ? "page" : undefined}
              className={"column-row" + (p.key === current ? " active" : "")}>
          <span className="column-row-label">{p.label}</span>
        </Link>
      ))}
      <Link to="/config" className="column-row">
        <span className="column-row-label">← All settings</span>
      </Link>
    </ColumnSection>
  );
}
