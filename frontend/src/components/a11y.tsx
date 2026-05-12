/**
 * Minimal accessibility primitives. Heavier components (popovers, dialogs)
 * will move to Radix when those views are implemented; this file keeps the
 * shell self-contained.
 */

import type { CSSProperties, ReactNode } from "react";

const visuallyHiddenStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export function VisuallyHidden({ children }: { children: ReactNode }) {
  return <span style={visuallyHiddenStyle}>{children}</span>;
}

export function SkipLink({ targetId }: { targetId: string }) {
  return (
    <a className="skip-link" href={`#${targetId}`}>
      Skip to main content
    </a>
  );
}

export function LiveRegion({
  children,
  politeness = "polite",
}: {
  children: ReactNode;
  politeness?: "polite" | "assertive";
}) {
  return (
    <div role="status" aria-live={politeness} style={visuallyHiddenStyle}>
      {children}
    </div>
  );
}
