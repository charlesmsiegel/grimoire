// What the play view re-renders, and what it asks for, as a scene opens and a
// turn is played. A suite of its own for the reason `CampaignView.shortcuts`
// is: it swaps in counting stand-ins (Portrait, ResponseControls) and the REAL
// CastPanel, and neither belongs under the four hundred tests that assert on
// the page rather than on its cost.
//
// Synthetic data only (placeholder names Seraphine / Mara / Winifred).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api/client", async () =>
  (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponsePresetPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponsePresetPicker());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());

// Counted stand-ins that render the real thing. A plate's portrait renders
// whenever its run does, and a response's controls whenever its post does,
// so between them they say which existing rows a render reached.
const renders = vi.hoisted(() => ({ portraits: [] as string[], controls: 0 }));
vi.mock("../components/Portrait", async () => {
  const actual = await vi.importActual<typeof import("../components/Portrait")>(
    "../components/Portrait");
  return {
    ...actual,
    Portrait: (props: Parameters<typeof actual.Portrait>[0]) => {
      renders.portraits.push(props.name);
      return actual.Portrait(props);
    },
  };
});
vi.mock("../components/ResponseControls", async () => {
  const actual = await vi.importActual<typeof import("../components/ResponseControls")>(
    "../components/ResponseControls");
  return {
    ...actual,
    ResponseControls: (props: Parameters<typeof actual.ResponseControls>[0]) => {
      renders.controls += 1;
      return <actual.ResponseControls {...props} />;
    },
  };
});

import { api, type Message } from "../api/client";
import { RunRegistryProvider } from "../runs/RunRegistryProvider";
import { Here, installCampaignMocks, playRoutes, withPalette } from "../testkit/campaignHarness";

const CAST = [
  { kind: "characters", id: "seraphine", name: "Seraphine", role: "npc" },
  { kind: "characters", id: "mara", name: "Mara", role: "npc" },
  { kind: "pcs", id: "winifred", name: "Winifred", role: "player" },
];

/** A scene of `rounds` exchanges: the player, then Seraphine and Mara answering
 *  as one two-part response. */
function transcript(rounds: number): Message[] {
  const out: Message[] = [];
  for (let r = 0; r < rounds; r++) {
    out.push({ role: "user", speaker: "Winifred", content: `I look toward the harbour. (${r})` });
    for (const speaker of ["Seraphine", "Mara"]) {
      out.push({ role: "assistant", speaker, response_id: `r${r}`, response_part: speaker,
                 response_status: "complete", response_can_reroll: true,
                 content: `"The tide is late," ${speaker} says on the Saltmarch quay. (${r})` });
    }
  }
  return out;
}

function renderPlay() {
  return render(
    <RunRegistryProvider>
      <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
        {withPalette(<><Here />{playRoutes()}</>)}
      </MemoryRouter>
    </RunRegistryProvider>,
  );
}

/** A promise and the function that settles it. */
function gate() {
  let open!: () => void;
  const opened = new Promise<void>((r) => { open = r; });
  return { opened, open };
}

beforeEach(() => {
  installCampaignMocks();
  (api.getCast as any).mockResolvedValue(CAST);
});

test("composer keystrokes and streamed deltas re-render none of the posts on screen", async () => {
  const posts = transcript(10);
  (api.getScene as any).mockResolvedValue(
    { meta: { id: "s1", title: "Old" }, messages: posts, total: posts.length,
      has_user_message: true });
  const started = gate();
  const counted = gate();
  const delivered = gate();
  const streamed = gate();
  (api.chat as any).mockImplementation(async (
    _c: string, _s: string, _t: string, onEvent: (e: object) => void) => {
    onEvent({ delta: "The lamps " });
    started.open();
    await counted.opened;                   // the test zeroes its counters
    for (let k = 0; k < 20; k++) {
      onEvent({ delta: k % 6 === 5 ? "\n\n" : `word${k} ` });
      // One network chunk per task, as a real body delivers them.
      await new Promise((r) => setTimeout(r, 0));
    }
    delivered.open();
    await streamed.opened;                  // and reads them before the turn ends
    onEvent({ done: true });
  });
  renderPlay();
  const composer = await screen.findByRole("textbox");
  await waitFor(() => expect(renders.controls).toBeGreaterThan(0));
  const rowsOnScreen = document.querySelectorAll(".msg").length;
  expect(rowsOnScreen).toBe(posts.length);

  renders.portraits.length = 0;
  renders.controls = 0;
  for (let k = 0; k < 20; k++) {
    fireEvent.change(composer, { target: { value: "I look".slice(0, 1 + (k % 6)) + k } });
  }
  expect(renders.controls).toBe(0);
  expect(renders.portraits).toEqual([]);

  fireEvent.change(composer, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await started.opened;
  // The turn starting does re-render the rows once -- their gutters hide while
  // it runs -- so counting starts after that, at the first delta.
  await screen.findByText(/The lamps/);
  renders.portraits.length = 0;
  renders.controls = 0;
  counted.open();
  // Waited on directly rather than through a text query's own ceiling: twenty
  // chunks a task apart can take longer than that on a loaded runner.
  await delivered.opened;
  await screen.findByText(/word19/);
  expect(renders.controls).toBe(0);
  // The only portrait a delta may draw is the streaming reply's own plate.
  expect(renders.portraits.filter((n) => n !== "Grimoire")).toEqual([]);
  streamed.open();
});

test("typing in a post's edit form re-renders that post's run and no other", async () => {
  const posts = transcript(10);
  (api.getScene as any).mockResolvedValue(
    { meta: { id: "s1", title: "Old" }, messages: posts, total: posts.length,
      has_user_message: true });
  renderPlay();
  fireEvent.click(await screen.findByRole("button", { name: "Edit message 4" }));  // round 1's player post
  const box = await screen.findByLabelText("Edit message");

  renders.portraits.length = 0;
  renders.controls = 0;
  for (let k = 0; k < 10; k++) {
    fireEvent.change(box, { target: { value: `I look toward the quay. ${k}` } });
  }
  expect(box).toHaveValue("I look toward the quay. 9");
  expect(renders.controls).toBe(0);
  expect(new Set(renders.portraits)).toEqual(new Set(["Winifred"]));
  expect(renders.portraits.length).toBeLessThanOrEqual(10);
});

test("a scene with posts never mounts the empty-scene setup while it opens", async () => {
  // Between the scene being chosen and its transcript arriving, `messages` is
  // empty too. Mounting the setup panel in that gap asked for the campaign's
  // whole character list and a suggestion scan, for a panel that unmounted as
  // soon as the posts came in.
  const posts = transcript(2);
  const landed = gate();
  (api.getScene as any).mockImplementation(async () => {
    await landed.opened;
    return { meta: { id: "s1", title: "Old" }, messages: posts, total: posts.length,
             has_user_message: true };
  });
  renderPlay();
  await screen.findByRole("textbox");
  expect(screen.queryByText(/Cast & scene setup/)).toBeNull();

  landed.open();
  await screen.findByText("I look toward the harbour. (1)");
  expect(screen.queryByText(/Cast & scene setup/)).toBeNull();
  expect(api.getSuggestions).not.toHaveBeenCalled();
  expect(api.listCharacters).not.toHaveBeenCalledWith({ kind: "campaign", id: "run" });
});

test("an empty scene shows its setup once its transcript has landed", async () => {
  const landed = gate();
  (api.getScene as any).mockImplementation(async () => {
    await landed.opened;
    return { meta: { id: "s1", title: "Old" }, messages: [], total: 0 };
  });
  renderPlay();
  await screen.findByRole("textbox");
  expect(screen.queryByText(/Cast & scene setup/)).toBeNull();

  landed.open();
  expect(await screen.findByText(/Cast & scene setup/)).toBeInTheDocument();
  expect(api.listCharacters).toHaveBeenCalledWith({ kind: "campaign", id: "run" });
});
