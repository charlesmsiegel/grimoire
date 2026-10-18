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

/** The head of each of the three, in the shape every Settings section and
 *  every page that owns the screen wears: the group as the eyebrow over a
 *  screen title, as a `.shelf-head` so the rule under it sits clear of what
 *  follows (the provider detail's Edit used to sit on it). They had the
 *  library's 64px display `.page-h1` instead, which made a Settings page read
 *  as a different app from the section it is listed beside. */
export function InferenceHead({ title }: { title: string }) {
  return (
    <div className="shelf-head">
      <div>
        <div className="eyebrow">Inference</div>
        <h1 className="screen-title">{title}</h1>
      </div>
    </div>
  );
}
