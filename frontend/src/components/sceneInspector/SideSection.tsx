import { useCallback, useState, type ReactNode } from "react";

const SECTIONS_KEY = "grimoire.inspector.sections";

function loadSectionCollapse(): Record<string, boolean> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}");
    if (stored === null || typeof stored !== "object" || Array.isArray(stored)) return {};
    const preferences: Record<string, boolean> = {};
    for (const [key, value] of Object.entries(stored)) {
      if (typeof value === "boolean") preferences[key] = value;
    }
    return preferences;
  } catch {
    return {};
  }
}

export function SideSection({ id, title, collapsed, onToggle, extra, children }: {
  id: string; title: string; collapsed: boolean;
  /** Handed the state the reader is actually looking at, not just the id. Every
   *  section but the briefing derives `collapsed` from the stored map, so for
   *  those the two agree — but the briefing's default comes from the post count
   *  (#118), and flipping the *stored* `undefined` there would write `true` on a
   *  click meant to expand, leaving the section shut and the click inert. */
  onToggle: (id: string, collapsed: boolean) => void;
  extra?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="side-section">
      <button type="button" className="side-section-head" aria-expanded={!collapsed}
              onClick={() => onToggle(id, collapsed)}>
        <h4>{title}</h4>
        <span className="side-section-head-right">
          {extra}
          <span className="side-section-chev" aria-hidden>{collapsed ? "▸" : "▾"}</span>
        </span>
      </button>
      {!collapsed && <div className="side-section-body">{children}</div>}
    </div>
  );
}

export type SectionControls = {
  collapsed: Record<string, boolean>;
  toggleSection: (id: string, current: boolean) => void;
};
export function useSectionCollapse(): SectionControls {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadSectionCollapse);
  const toggleSection = useCallback((id: string, current: boolean) => {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !current };
      localStorage.setItem(SECTIONS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);
  return { collapsed, toggleSection };
}
