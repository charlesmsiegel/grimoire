/** The state that has to survive a component unmounting, and the resume
 *  cursor that has to be right.
 *
 *  These are unit tests on the registry rather than renders of the campaign
 *  view: what the provider owes is a small, exact contract, and driving it
 *  through a 3,500-line component would test the component's plumbing instead.
 *  `CampaignView.test.tsx` covers the wiring; this covers the rules.
 */
import { render, screen, act } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RunRegistryProvider, useRunRegistry, type RunRegistry } from "./RunRegistryProvider";
import { parseSSEChunk } from "../api/stream";

function capture(): { registry: RunRegistry; unmount: () => void } {
  let registry!: RunRegistry;
  function Probe() {
    registry = useRunRegistry();
    return <span>probe</span>;
  }
  const { unmount } = render(
    <RunRegistryProvider><Probe /></RunRegistryProvider>,
  );
  return { registry, unmount };
}

const SEND = { cid: "c1", sid: "s1", attempt: "a-1", text: "Mara waits.", runId: null };

describe("what survives the component", () => {
  it("remembers a send whose outcome nobody learned", () => {
    const { registry } = capture();
    act(() => registry.begin(SEND));
    expect(registry.pending("c1", "s1")?.attempt).toBe("a-1");
  });

  it("keeps the submitted text, because the id alone cannot give it back", () => {
    // If the response dies before the run frame and the turn then fails, the
    // backend takes the player's post back off the transcript -- so the words
    // exist in neither the scene nor the unmounted component. This is the only
    // copy left.
    const { registry } = capture();
    act(() => registry.begin(SEND));
    expect(registry.pending("c1", "s1")?.text).toBe("Mara waits.");
  });

  it("does not answer for a different scene", () => {
    // Attempt ids are only unique within a scene, and a send held for one
    // scene recovered into another would put the player's words in the wrong
    // transcript.
    const { registry } = capture();
    act(() => registry.begin(SEND));
    expect(registry.pending("c1", "s2")).toBeUndefined();
    expect(registry.pending("c2", "s1")).toBeUndefined();
  });

  it("forgets a send once its outcome is established", () => {
    const { registry } = capture();
    act(() => registry.begin(SEND));
    act(() => registry.settle("c1", "s1"));
    expect(registry.pending("c1", "s1")).toBeUndefined();
  });
});

describe("the resume cursor", () => {
  it("is one past the last frame actually read", () => {
    const { registry } = capture();
    act(() => registry.consume("r1", 4));
    expect(registry.resumeFrom("r1")).toBe(5);
  });

  it("starts at zero for a run nothing has been read from", () => {
    // Not `next_index`: that is the live tail, so resuming from it drops
    // everything generated while this client was away -- which, in the case
    // this whole feature is for, is the entire reply.
    const { registry } = capture();
    expect(registry.resumeFrom("never-read")).toBe(0);
  });

  it("never moves backwards when a replay re-delivers an earlier frame", () => {
    const { registry } = capture();
    act(() => registry.consume("r1", 7));
    act(() => registry.consume("r1", 3));
    expect(registry.resumeFrom("r1")).toBe(8);
  });

  it("counts heartbeats, because the wire index does", () => {
    // The defect this exists to catch: `parseSSEChunk` surfaces no event for a
    // comment frame, so a cursor derived from the events it emitted undercounts
    // by one per heartbeat and resumes early -- duplicating text mid-reply.
    // Driven through the REAL parser: a helper that recomputed the index its
    // own way would agree with a broken implementation and prove nothing.
    const { registry } = capture();
    // The heartbeat is LAST, and that is what makes this discriminate. With a
    // data frame after it the absolute index already accounts for it, so an
    // implementation that skipped comment frames would agree. Detaching right
    // after one -- a phone locking during a slow time-to-first-token, which is
    // exactly when heartbeats appear -- is where the two answers diverge.
    const wire = [
      'id: 0\ndata: {"delta":"The lamps "}\n\n',
      'id: 1\ndata: {"delta":"are already lit."}\n\n',
      "id: 2\n: heartbeat\n\n",
    ].join("");
    const seen: unknown[] = [];
    parseSSEChunk("", wire, (e) => seen.push(e),
                  (i) => act(() => registry.consume("r1", i)));

    expect(seen).toHaveLength(2);               // the heartbeat surfaced nothing
    expect(registry.resumeFrom("r1")).toBe(3);  // but it still moved the cursor
  });
});

describe("without a provider", () => {
  it("degrades to a no-op rather than throwing", () => {
    // Several suites render the campaign view on its own. A hard requirement
    // here would turn every one of them into a provider test; the cost is that
    // recovery does nothing there, which is what they expect.
    function Bare() {
      const registry = useRunRegistry();
      registry.begin(SEND);
      return <span>{String(registry.pending("c1", "s1") === undefined)}</span>;
    }
    render(<Bare />);
    expect(screen.getByText("true")).toBeInTheDocument();
  });
});

describe("following a rename", () => {
  it("keeps a pending send reachable under the scene's new id", () => {
    // A `sid` carries the slug, so renaming mints a new one. Left under the
    // old key the entry is unreachable: opening the renamed scene looks under
    // the new id and finds nothing, so a failed run whose post was rolled back
    // strands the player's words under an id no route will select again.
    const { registry } = capture();
    act(() => registry.begin(SEND));
    act(() => registry.rekey("c1", "s1", "s1-renamed"));

    expect(registry.pending("c1", "s1-renamed")?.text).toBe("Mara waits.");
    expect(registry.pending("c1", "s1")).toBeUndefined();
  });

  it("carries the new id on the record too, not just in the key", () => {
    // `settle` and `attach` are called with the sid the caller is holding, and
    // recovery re-selects `held.sid`. A record whose own `sid` still said the
    // old one would resolve into a scene that no longer exists.
    const { registry } = capture();
    act(() => registry.begin(SEND));
    act(() => registry.rekey("c1", "s1", "s1-renamed"));

    expect(registry.pending("c1", "s1-renamed")?.sid).toBe("s1-renamed");
  });

  it("does not invent an entry for a scene that had no pending send", () => {
    const { registry } = capture();
    act(() => registry.rekey("c1", "s1", "s1-renamed"));
    expect(registry.pending("c1", "s1-renamed")).toBeUndefined();
  });
});
