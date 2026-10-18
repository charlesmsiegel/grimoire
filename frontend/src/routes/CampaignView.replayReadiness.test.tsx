// Readiness belongs to scene generation, not to accepting/stopping an existing
// replay. Use the real panel here: a wiring-only stub cannot prove this contract.
import { screen, fireEvent, waitFor } from "@testing-library/react";
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api, type ReplaySession } from "../api/client";
import { installCampaignMocks, renderCampaign } from "../testkit/campaignHarness";

const SESSION: ReplaySession = {
  scene: "s1", cut: 0, done: 0, steps: 2, turns_left: 1,
  next: "generation", staged: false, created: "", gone: false, pending: false,
};

beforeEach(async () => {
  installCampaignMocks();
  const view = await api.getCampaignInference("run");
  vi.mocked(api.getCampaignInference).mockResolvedValue({ ...view,
    routes: view.routes.map((route) => ({ ...route, problem: "The standing provider has no key" })),
  });
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", content: "The tide turns." },
  ] });
});

test("scene readiness blocks replay start before the transcript is cut", async () => {
  vi.mocked(api.replayPreview).mockResolvedValue({ posts: 1, turns: 1, threshold: 10,
    fork: false, blocked: "" });
  renderCampaign();
  await screen.findByText(/No model is ready to generate/);
  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  fireEvent.click(await screen.findByRole("checkbox", { name: /Replay in a branch/ }));
  const start = screen.getByRole("button", { name: "Replay in place" });
  expect(start).toBeDisabled();
  fireEvent.click(start);
  expect(api.startReplay).not.toHaveBeenCalled();
  expect(screen.getByText("The tide turns.")).toBeInTheDocument();
});

test.each([false, true])("scene readiness blocks replay generation with pending=%s", async (pending) => {
  vi.mocked(api.getReplay).mockResolvedValue({ ...SESSION, pending });
  renderCampaign();
  await screen.findByText(/No model is ready to generate/);
  const generate = await screen.findByRole("button", { name: pending ? "Try again" : "Replay next turn" });
  expect(generate).toBeDisabled();
  fireEvent.click(generate);
  expect(api.replayTurn).not.toHaveBeenCalled();
  expect(api.regenerate).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
  if (pending) {
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(api.acceptReplay).toHaveBeenCalledWith("run", "s1"));
  } else {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(api.cancelReplay).toHaveBeenCalledWith("run", "s1", true));
  }
});
