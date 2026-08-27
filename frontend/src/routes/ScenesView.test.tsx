import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { getCampaign: vi.fn(), listScenes: vi.fn(), getShell: vi.fn() },
}));

// The chooser is driven end-to-end by its own suite; here it only has to be
// reachable, and to report back the two ways it can end.
vi.mock("../components/NewSceneChooser", () => ({
  NewSceneChooser: ({ onClose, onCreated }: any) => (
    <div data-testid="scene-chooser">
      <button onClick={() => onCreated("s9")}>stub-pick</button>
      <button onClick={() => onClose()}>stub-close</button>
    </div>
  ),
}));

import { api } from "../api/client";
import ScenesView from "./ScenesView";

const META = { id: "run", name: "Run One", world: "w", created: "", updated: "",
               scenes: 3, last_scene: "", absorbed: 1 };

const scene = (over: Record<string, unknown>) => ({
  id: "001--a", title: "A", model: "", created: "", updated: "", date: "",
  place: "", done: false, ...over,
});

function shell(pending: { sid: string; proposals: number }[] = []) {
  return {
    campaigns: 1, todo: null,
    campaign: {
      id: "run", name: "Run One", world_name: "Saltmarch", scenes: 3, open: [],
      ledger_open: 0, sheets: null, unreviewed: 0, pending,
      images_undescribed: null,
    },
  };
}

function renderScenes() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes"]}>
      <Routes>
        <Route path="/campaigns/:cid/scenes" element={<ScenesView />} />
        <Route path="/campaigns/:cid/scenes/:sid"
               element={<div data-testid="play" />} />
      </Routes>
    </MemoryRouter>);
}

beforeEach(() => {
  (api.getCampaign as any).mockResolvedValue({ meta: META, body: "" });
  (api.getShell as any).mockResolvedValue(shell());
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "Day 41", place: "The weir" }),
    scene({ id: "002--second", title: "The second", done: true }),
    scene({ id: "001--first", title: "The first", done: true }),
  ]);
});

test("every scene is listed with its number from its own id", async () => {
  renderScenes();
  await screen.findByText("The third");
  // Story order comes off the id, never the list position: `list_scenes` sorts
  // by `updated`, so editing an early scene would renumber the whole campaign.
  const rows = screen.getAllByRole("listitem");
  expect(within(rows[0]).getByText("3")).toBeInTheDocument();
  expect(within(rows[2]).getByText("1")).toBeInTheDocument();
});

test("the eyebrow names the campaign, what this list is, and how much is open", async () => {
  // The design's eyebrow is richer than the bare campaign name: it also says
  // what kind of list this is and how much of it still needs playing.
  renderScenes();
  await screen.findByText("The third");
  expect(screen.getByText("Run One · every scene, newest first · 1 open"))
    .toBeInTheDocument();
});

test("the eyebrow drops the open count rather than claiming zero", async () => {
  // Joining with a filter means a part with nothing to say disappears
  // instead of printing "0 open" -- matching how the hub's eyebrow behaves.
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--first", title: "The first", done: true }),
  ]);
  renderScenes();
  await screen.findByText("The first");
  expect(screen.getByText("Run One · every scene, newest first")).toBeInTheDocument();
});

test("the action names what to do, and it is not the same verb for every state", async () => {
  (api.getShell as any).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
  renderScenes();
  await screen.findByText("The third");
  const rows = screen.getAllByRole("listitem");
  // Open, unreviewed and finished are three different things to do next.
  expect(within(rows[0]).getByText("Open →")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Wrap up →")).toBeInTheDocument();
  expect(within(rows[2]).getByText("Read →")).toBeInTheDocument();
});

test("an absorbed scene holding a review is not filed away as finished", async () => {
  // "Absorbed" and "absorbed but nobody decided its proposals" are different
  // states, and calling both of them done loses the one that needs doing.
  (api.getShell as any).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
  renderScenes();
  await screen.findByText("The second");
  const row = screen.getAllByRole("listitem")[1];
  expect(within(row).getByText("4 unreviewed")).toBeInTheDocument();
});

test("the chip carries the proposal count, not just that some are waiting", async () => {
  // The shell payload already has the count per pending scene; a bare
  // "unreviewed" would make the reader open the scene to learn whether it's
  // one proposal or thirty.
  (api.getShell as any).mockResolvedValue(shell([{ sid: "002--second", proposals: 12 }]));
  renderScenes();
  await screen.findByText("The second");
  const row = screen.getAllByRole("listitem")[1];
  expect(within(row).getByText("12 unreviewed")).toBeInTheDocument();
});

test("when and where come off the row without a second read", async () => {
  renderScenes();
  await screen.findByText("The third");
  expect(screen.getByText(/Day 41 · The weir/)).toBeInTheDocument();
});

test("filtering narrows the list and says how much of it is showing", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.change(screen.getByLabelText(/filter scenes by title/i),
                   { target: { value: "third" } });
  expect(screen.getByText("1 of 3")).toBeInTheDocument();
  expect(screen.queryByText("The second")).not.toBeInTheDocument();
});

test("a filter matching nothing is not an empty campaign", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.change(screen.getByLabelText(/filter scenes by title/i),
                   { target: { value: "zzz" } });
  expect(screen.getByText(/no scene here matches that/i)).toBeInTheDocument();
  expect(screen.queryByText(/no scenes yet/i)).not.toBeInTheDocument();
});

test("an empty campaign says so and offers the first scene", async () => {
  // This page is where a campaign starts now: the play view mounts on a scene,
  // so there is no composer to type the first one into.
  (api.listScenes as any).mockResolvedValue([]);
  renderScenes();
  expect(await screen.findByText(/no scenes yet/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /\+ new scene/i })).toBeInTheDocument();
});

test("+ New scene opens the chooser without creating anything", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
});

test("a chooser pick opens the scene it made", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(screen.getByTestId("play")).toBeInTheDocument());
});

test("closing the chooser leaves the list alone", async () => {
  renderScenes();
  await screen.findByText("The third");
  const readsBefore = (api.listScenes as any).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(screen.queryByTestId("play")).toBeNull();
  expect((api.listScenes as any).mock.calls.length).toBe(readsBefore);
});

test("a failed read is not an empty campaign", async () => {
  (api.listScenes as any).mockRejectedValue(new Error("offline"));
  renderScenes();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText(/no scenes yet/i)).not.toBeInTheDocument();
});
