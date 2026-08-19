import { useEffect, useState } from "react";
import { render, screen } from "@testing-library/react";

/** A page that settles in steps, one macrotask apart — the shape every page in
 *  this app has (campaign read → scene list → resolver redirect → the scene's
 *  own dozen reads), reduced to what the harness has to survive.
 *
 *  Every other step is DELIBERATELY invisible: it lands a value and starts the
 *  next step without moving anything a test can query. Those are why `settle`
 *  wants two consecutive unchanged ticks rather than one — a page is not
 *  finished just because a tick went by without the screen changing.
 *
 *  Each step is scheduled by the effect that runs on the PREVIOUS step's
 *  commit, never by a chain of timers started up front. That is what makes it
 *  exactly one step per macrotask: a timer chain queues its next 0 ms timeout
 *  while the current one is still running, so several come due in the same
 *  timer phase and land together — which silently hid the one-tick case when
 *  this test was written the obvious way. */
const LAST = 5;

function Staged() {
  const [step, setStep] = useState(0);
  useEffect(() => {
    if (step >= LAST) return;
    const t = setTimeout(() => setStep(step + 1), 0);
    return () => clearTimeout(t);
  }, [step]);
  // Steps 2 and 4 render exactly what the step before them rendered.
  return (
    <ul>
      {step >= 1 && <li>first</li>}
      {step >= 3 && <li>middle</li>}
      {step >= LAST && <li>last</li>}
    </ul>
  );
}

// The guarantee `src/test-setup.ts` installs, and the one #351 was about: an
// `await` returns with the page SETTLED, not merely with the queried condition
// true.
//
// Verified to fail two ways, which is the only reason it is worth having:
// without the wrapper at all (React Testing Library drains exactly one
// macrotask after `findBy*` succeeds, so `last` is three steps away), and with
// the wrapper settling for a single unchanged tick instead of two (it stops on
// step 4, which renders what step 3 rendered).
test("an await returns with the page settled, not at the step it asked about", async () => {
  render(<Staged />);
  await screen.findByText("first");
  expect(screen.getByText("last")).toBeInTheDocument();
});
