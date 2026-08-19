import { useEffect, useState } from "react";
import { render, screen } from "@testing-library/react";

/** A component that settles in stages, one macrotask apart — the shape every
 *  page in this app has (campaign read → scene list → resolver redirect → the
 *  scene's own dozen reads), reduced to the part the harness has to survive.
 *  Each stage appends rather than replaces, so a stage that has landed can be
 *  queried after a later one has. */
function Staged({ stages = 4 }: { stages?: number }) {
  const [landed, setLanded] = useState<number[]>([]);
  useEffect(() => {
    let alive = true;
    (async () => {
      for (let i = 1; i <= stages; i++) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        if (!alive) return;
        setLanded((l) => [...l, i]);
      }
    })();
    return () => { alive = false; };
  }, [stages]);
  return <ul>{landed.map((i) => <li key={i}>stage {i}</li>)}</ul>;
}

// The guarantee `src/test-setup.ts` installs, and the one #351 was about: an
// `await` returns with the page SETTLED, not merely with the queried condition
// true. Without it this fails on the sync query below — React Testing Library
// drains exactly one macrotask after `findBy*` succeeds, which is stage 2, and
// the suite's tests read stage 4 (a control that is enabled, a gutter that is
// rendered, a select that has its value) off the very next line.
test("an await returns with the page settled, not at the stage it asked about", async () => {
  render(<Staged />);
  await screen.findByText("stage 1");
  expect(screen.getByText("stage 4")).toBeInTheDocument();
});
