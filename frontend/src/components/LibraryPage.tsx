import type { ReactNode } from "react";
import LibraryColumn from "./LibraryColumn";
import { PageShell } from "./PageShell";

/** Every library section — Worlds, Modules, Styles, Response presets,
 *  Climates, Connections — is the same page with different contents: the six
 *  sections in the column, one of them lit, and that section's records in
 *  main. This is that page. */
export default function LibraryPage({ children }: { children: ReactNode }) {
  return (
    // `library` is what the phone rules key off: at 375px these six sections
    // become a scrolling strip at the foot rather than a full-screen index,
    // because section-switching is the dominant action here and a strip keeps
    // it one tap from the records.
    <PageShell className="library" column={<LibraryColumn />} columnLabel="Library sections">
      {children}
    </PageShell>
  );
}
