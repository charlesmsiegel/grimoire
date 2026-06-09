/**
 * Shared in-page tab strip (WAI-ARIA tabs pattern): roving tabindex,
 * Left/Right/Home/End keyboard navigation, selection follows focus.
 * Container class defaults to `tab-row`; settings screens pass `tab-bar`.
 * Route-level navigation (library kinds, campaign subnav) stays NavLink-based.
 */

import { useRef } from "react";

interface TabsProps<K extends string> {
  tabs: { key: K; label: string }[];
  active: K;
  onSelect: (key: K) => void;
  ariaLabel: string;
  className?: string;
}

export function Tabs<K extends string>({
  tabs,
  active,
  onSelect,
  ariaLabel,
  className = "tab-row",
}: TabsProps<K>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (e: React.KeyboardEvent, idx: number) => {
    const last = tabs.length - 1;
    let next: number | null = null;
    if (e.key === "ArrowRight") next = idx === last ? 0 : idx + 1;
    else if (e.key === "ArrowLeft") next = idx === 0 ? last : idx - 1;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = last;
    if (next === null) return;
    e.preventDefault();
    const tab = tabs[next];
    if (!tab) return;
    onSelect(tab.key);
    refs.current[next]?.focus();
  };

  return (
    <div className={className} role="tablist" aria-label={ariaLabel}>
      {tabs.map((t, i) => (
        <button
          key={t.key}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="tab"
          aria-selected={active === t.key}
          tabIndex={active === t.key ? 0 : -1}
          className={active === t.key ? "tab active" : "tab"}
          onClick={() => onSelect(t.key)}
          onKeyDown={(e) => onKeyDown(e, i)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
