/** The state that has to survive a component unmounting, and the resume
 *  cursor that has to be right.
 *
 *  These are unit tests on the registry rather than renders of the campaign
 *  view: what the provider owes is a small, exact contract, and driving it
 *  through a 3,500-line component would test the component's plumbing instead.
 *  `CampaignView.test.tsx` covers the wiring; this covers the rules.
 */
import { render, screen, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

// FILE-WIDE, not per-describe. The registry now mirrors its pending map to
// `localStorage` so a send survives the renderer being restarted -- which also
// means every `capture()` rehydrates whatever the previous test left behind,
// and two suites below already caught each other that way.
beforeEach(() => window.localStorage.clear());

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

describe("the key", () => {
  it("does not confuse two scenes whose ids differ only in where a space falls", () => {
    // `store.safe_id` permits interior spaces, so a separator-joined key makes
    // ("a", "b c") and ("a b", "c") the same entry. This provider survives
    // navigation, so both pairs can hold a pending send at once -- and the
    // second `begin` would overwrite the first's saved prompt, leaving recovery
    // to resolve it into the wrong scene.
    const { registry } = capture();
    act(() => registry.begin({ ...SEND, cid: "a", sid: "b c", text: "first" }));
    act(() => registry.begin({ ...SEND, cid: "a b", sid: "c", text: "second" }));

    expect(registry.pending("a", "b c")?.text).toBe("first");
    expect(registry.pending("a b", "c")?.text).toBe("second");
  });

  it("does not let settling one of them resolve the other", () => {
    const { registry } = capture();
    act(() => registry.begin({ ...SEND, cid: "a", sid: "b c", text: "first" }));
    act(() => registry.begin({ ...SEND, cid: "a b", sid: "c", text: "second" }));
    act(() => registry.settle("a b", "c"));

    expect(registry.pending("a", "b c")?.text).toBe("first");
  });
});

describe("surviving the whole renderer", () => {
  it("hands a pending send back to a provider built from scratch", () => {
    // React state does not survive a reload, and on Android the WebView's
    // renderer can be restarted out from under a healthy backend turn -- which
    // is the exact scenario this feature exists for. The provider then came
    // back empty, reattached to the live run, and held no copy of what the
    // player typed; a rollback by that run left the words nowhere at all.
    const first = capture();
    act(() => first.registry.begin(SEND));
    first.unmount();

    const { registry } = capture();      // a fresh provider, as after a reload
    expect(registry.pending("c1", "s1")?.text).toBe("Mara waits.");
    expect(registry.pending("c1", "s1")?.attempt).toBe("a-1");
  });

  it("does not hand back a send that was settled before the restart", () => {
    const first = capture();
    act(() => first.registry.begin(SEND));
    act(() => first.registry.settle("c1", "s1"));
    first.unmount();

    expect(capture().registry.pending("c1", "s1")).toBeUndefined();
  });

  it("follows a rename across the restart too", () => {
    const first = capture();
    act(() => first.registry.begin(SEND));
    act(() => first.registry.rekey("c1", "s1", "s1-renamed"));
    first.unmount();

    expect(capture().registry.pending("c1", "s1-renamed")?.text).toBe("Mara waits.");
  });

  it("still works when storage is unavailable", () => {
    // Private windows and blocked site data make the ACCESSOR throw, not just
    // the write. A registry that could not save is still a registry -- it
    // degrades to what it was before, rather than taking the app down.
    // `Storage.prototype`, not the instance: spying on `window.localStorage`
    // directly does not intercept in jsdom, so the first version of this test
    // passed with the guard deleted -- it never reached a throw at all.
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      const { registry } = capture();
      act(() => registry.begin(SEND));

      expect(spy).toHaveBeenCalled();          // the premise
      expect(registry.pending("c1", "s1")?.text).toBe("Mara waits.");
    } finally {
      spy.mockRestore();
    }
  });

  it("mounts even when reading storage throws", () => {
    // The accessor itself throws in a private window, which is the read side
    // of the same hazard -- and this one happens during construction, so an
    // unguarded read takes the whole app down rather than one send.
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      const { registry } = capture();
      expect(spy).toHaveBeenCalled();
      expect(registry.pending("c1", "s1")).toBeUndefined();
    } finally {
      spy.mockRestore();
    }
  });

  it("ignores stored junk rather than failing to mount", () => {
    window.localStorage.setItem("grimoire.runs.pending", "{not json");
    expect(capture().registry.pending("c1", "s1")).toBeUndefined();
  });
});

describe("what the stored copy is trusted to be", () => {
  it("drops an entry that does not have the shape a send has", () => {
    // Written by a PREVIOUS build of the app, or half-written, or simply
    // something else on the origin. A malformed record here lands exactly
    // where recovery reads the player's text from.
    window.localStorage.setItem("grimoire.runs.pending", JSON.stringify([
      ['["c1","s1"]', { cid: "c1", sid: "s1", attempt: "a-1", text: "kept", runId: null }],
      ['["c1","s2"]', { cid: "c1", sid: "s2" }],            // no attempt, no text
      ["not-an-entry"],
    ]));
    const { registry } = capture();

    expect(registry.pending("c1", "s1")?.text).toBe("kept");
    expect(registry.pending("c1", "s2")).toBeUndefined();
  });
});
