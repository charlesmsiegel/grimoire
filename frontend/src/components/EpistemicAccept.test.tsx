import { fireEvent, render, screen } from "@testing-library/react";
import { api, type SceneContext } from "../api/client";
import { type ContextSection, type EpistemicDetail } from "../api/types";
import { ContextBreakdown } from "./ContextBreakdown";

vi.mock("../api/client", () => ({ api: { ledgerCreateKnowledge: vi.fn() } }));

const create = vi.mocked(api.ledgerCreateKnowledge);

const KEY = { key: "p-aaaa", part: "", variant: "", digest: "d1", shown: "s1" };

function detail(keys = [KEY], perspective = "actor:characters:winifred",
                access: "witnessed" | "unknown" = "unknown"): EpistemicDetail {
  return {
    perspective, decided: 0,
    names: { "characters:winifred": "Winifred", "characters:mara": "Mara", "characters:seraphine": "Seraphine" },
    items: [{
      key: "a".repeat(32), scene: "001--pier", admitted: true, state: "unattributed", header: {},
      known_to: [], suspected_by: [],
      slices: [{ part: "posts", posts: [0], access, basis: ["not_recorded_present"], keys },
               { part: "summary", posts: [], access: "unknown", basis: ["summary_needs_grant"], keys: [] }],
    }],
  };
}

/** The section as a capture holds it: `carried` puts the post in its fitted
 *  items (the prompt carried it); `shed` marks that item shed by the packer. */
function ctx(epistemic: EpistemicDetail, carried = true, shed = false): SceneContext {
  const items = carried ? [{
    scene: "001--pier", identity: null, key: "a".repeat(32), title: "", tier: 1,
    admitted_by: ["structural"], rounds: [], ranks: [], excerpts: [[0]],
    posts: [{ index: 0, key: "p-aaaa", part: null, speaker: "Mara", text: "x" }],
    summary: "included", tokens: 10, shed,
  }] : [];
  const section: ContextSection = {
    id: "history_known", label: "Recalled history · what the speaker knows", text: "", tokens: 0,
    tier: "history-recall", dropped: false, trimmed: 0,
    history: { items, coverage: { tiers: [1], semantic: "off", admitted: 1, agreement: 0, verdict: "thin" },
               ceiling: 1200, budget_from: ["config"], epistemic },
  };
  return { sections: [section], history: [], total_tokens: 0, history_tokens: 0 } as unknown as SceneContext;
}


test("a captured turn's part posts the user's word with the fingerprints it had then", async () => {
  create.mockClear();
  create.mockResolvedValue({} as never);
  render(<ContextBreakdown models={[]} ctx={ctx(detail([KEY], undefined, "witnessed"))} acceptCid="saltmarch" />);
  fireEvent.click(screen.getAllByRole("button", { name: "Mark as known" })[0]);
  expect(create).toHaveBeenCalledWith("saltmarch", {
    audience: "characters:winifred", status: "knows", scene: "001--pier",
    posts: [{ key: "p-aaaa", part: "" }], whole_scene: false,
    expect: [KEY], expect_digest: "", expect_shown: "",
    audience_name: "Winifred", source: "accepted",
  });
  expect(await screen.findByText(/Saved: Winifred knows this/)).toBeInTheDocument();
  // The summary is never offered: one accept row, for the post.
  expect(screen.getAllByRole("button", { name: "Mark as known" })).toHaveLength(1);
});

test("a part that moved since the turn says so instead of granting", async () => {
  const moved = Object.assign(new Error("moved"),
    { kind: "moved", detail: "that post changed since it was shown" });
  create.mockRejectedValue(moved);
  render(<ContextBreakdown models={[]} ctx={ctx(detail([KEY], undefined, "witnessed"))} acceptCid="saltmarch" />);
  fireEvent.click(screen.getByRole("button", { name: "Mark as suspected" }));
  expect(await screen.findByText(/changed since this turn/)).toBeInTheDocument();
});

test("a keyless part shows unkeyed and offers only unaware, with no expectation", () => {
  create.mockClear();
  create.mockResolvedValue({} as never);
  render(<ContextBreakdown models={[]} ctx={ctx(detail([{ ...KEY, key: "" }]))} acceptCid="saltmarch" />);
  expect(screen.getByText("unkeyed")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Mark as known/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Mark as unaware (whole scene)" }));
  expect(create.mock.calls[0][1]).toMatchObject({ status: "unaware", expect: null });
});

test("the narrator's turn lets the reader pick which character", () => {
  create.mockClear();
  create.mockResolvedValue({} as never);
  render(<ContextBreakdown models={[]} ctx={ctx(detail([KEY], "narrator"))} acceptCid="saltmarch" />);
  fireEvent.change(screen.getByLabelText("Who"), { target: { value: "characters:mara" } });
  fireEvent.click(screen.getByRole("button", { name: "Mark as unaware" }));
  expect(create.mock.calls[0][1]).toMatchObject({ audience: "characters:mara", audience_name: "Mara" });
});

test("the live panel offers no accept", () => {
  render(<ContextBreakdown models={[]} ctx={ctx(detail())} />);
  expect(screen.queryByRole("button", { name: /Mark as/ })).toBeNull();
  expect(screen.getByText(/Access, for Winifred/)).toBeInTheDocument();
});

test("a part an NPC was not given can only be withheld, never granted", () => {
  // Its text was not in this turn's prompt, so the capture never showed it.
  render(<ContextBreakdown models={[]} ctx={ctx(detail())} acceptCid="saltmarch" />);
  expect(screen.queryByRole("button", { name: "Mark as known" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Mark as suspected" })).toBeNull();
  expect(screen.getByRole("button", { name: "Mark as unaware" })).toBeInTheDocument();
});

test("a part the packer shed was never sent, so it can only be withheld", () => {
  render(<ContextBreakdown models={[]} ctx={ctx(detail([KEY], undefined, "witnessed"), true, true)}
                           acceptCid="saltmarch" />);
  expect(screen.queryByRole("button", { name: "Mark as known" })).toBeNull();
  expect(screen.getByRole("button", { name: "Mark as unaware" })).toBeInTheDocument();
});
