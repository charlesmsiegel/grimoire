import type { ComponentProps } from "react";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import { installCampaignMocks, ONE_SCENE, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { configChanged } from "../appEvents";

// The mocks and the per-test defaults live in `src/testkit`, which the review
// suite shares (#378). A `vi.mock` factory is hoisted above every import
// and can close over nothing, so it reaches the harness through a dynamic
// import rather than a module-scope binding.
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponseTargetsPicker", () => ({
  ResponseTargetsPicker: ({ onChanged }: ComponentProps<typeof import("../components/ResponseTargetsPicker").ResponseTargetsPicker>) =>
    <button onClick={() => onChanged?.()}>stub-target-saved</button>,
}));
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));

beforeEach(installCampaignMocks);

function scene() {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hello" }, { role: "assistant", content: "a reply" },
  ] });
}

test("a campaign metadata read failure is an owned non-generating notice", async () => {
  scene();
  vi.mocked(api.getCampaign).mockRejectedValue(new Error("metadata offline"));
  renderCampaign();
  await screen.findByText(/campaign.*could not be read.*metadata offline/i);
  expect(screen.queryByRole("button", { name: /^Retry/ })).not.toBeInTheDocument();
  expect(api.chat).not.toHaveBeenCalled();
});

test.each(["success", "failure"] as const)("retired campaign metadata %s cannot replace the current campaign", async (outcome) => {
  scene();
  let resolve!: (v: Awaited<ReturnType<typeof api.getCampaign>>) => void;
  let reject!: (e: Error) => void;
  vi.mocked(api.getCampaign).mockReturnValueOnce(new Promise((yes, no) => { resolve = yes; reject = no; }))
    .mockResolvedValue(fixture({ meta: { name: "Current campaign", world_name: "Current world" } }));
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Link to="/campaigns/other/scenes/s1">switch campaign</Link>{playRoutes()}</>)}
  </MemoryRouter>);
  await screen.findByText("a reply");
  fireEvent.click(screen.getByText("switch campaign"));
  await screen.findByRole("link", { name: "Current campaign — back to the campaign" });
  await act(async () => {
    if (outcome === "success") resolve(fixture({ meta: { name: "Retired campaign", world_name: "Retired world" } }));
    else reject(new Error("retired metadata failure"));
  });
  expect(screen.getByRole("link", { name: "Current campaign — back to the campaign" })).toBeInTheDocument();
  expect(screen.queryByText(/retired metadata failure/i)).not.toBeInTheDocument();
});

test.each(["transcript", "list", "both"] as const)("replay completion owns a failed %s readback without generating again", async (which) => {
  scene();
  renderCampaign();
  await screen.findByText("a reply");
  if (which !== "list") vi.mocked(api.getScene).mockRejectedValue(new Error("transcript offline"));
  if (which !== "transcript") vi.mocked(api.listScenes).mockRejectedValue(new Error("list offline"));
  fireEvent.click(screen.getByText("stub-replay-changed-asked"));
  await screen.findByText(/replay.*(scene|list).*could not.*offline/i);
  expect(screen.queryByRole("button", { name: /^Retry/ })).not.toBeInTheDocument();
  expect(api.regenerate).not.toHaveBeenCalled();
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();
});

test("rename focuses on opening and keeps chosen focus across background updates", async () => {
  scene();
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Rename scene" }));
  const box = screen.getByRole("textbox", { name: "Rename scene" });
  expect(box).toHaveFocus();
  fireEvent.change(box, { target: { value: "New title" } });
  expect(box).toHaveFocus();
  const composer = screen.getByPlaceholderText("Speak your intent…");
  await act(async () => { composer.focus(); configChanged(); });
  expect(composer).toHaveFocus();
  fireEvent.keyDown(box, { key: "Escape" });
  expect(screen.queryByRole("textbox", { name: "Rename scene" })).not.toBeInTheDocument();
  expect(api.renameScene).not.toHaveBeenCalled();
});

test("dice focuses only on opening or returning from check mode", async () => {
  scene();
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  const notation = screen.getByRole("textbox", { name: "Dice notation" });
  expect(notation).toHaveFocus();
  fireEvent.change(notation, { target: { value: "2d6" } });
  const label = screen.getByRole("textbox", { name: "Roll label" });
  act(() => { label.focus(); });
  fireEvent.change(label, { target: { value: "a roll" } });
  expect(label).toHaveFocus();
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  fireEvent.click(screen.getByRole("button", { name: "Dice" }));
  expect(screen.getByRole("textbox", { name: "Dice notation" })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Dice notation" }), { key: "Escape" });
  expect(screen.queryByRole("textbox", { name: "Dice notation" })).not.toBeInTheDocument();
  expect(api.chat).not.toHaveBeenCalled();
});

// The target picker has already committed its PUT when it invokes onChanged.
// A failed refresh must describe that read and never offer another generation.
test("saved response targets own a rejected transcript refresh", async () => {
  scene();
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByText("Scene ⋯"));
  fireEvent.click(screen.getByRole("button", { name: "Response" }));
  await screen.findByText("stub-target-saved");
  vi.mocked(api.getScene).mockRejectedValue(new Error("target read offline"));
  fireEvent.click(screen.getByText("stub-target-saved"));
  await screen.findByText(/response targets saved.*scene could not be re-read.*target read offline/i);
  expect(screen.queryByRole("button", { name: /^Retry/ })).not.toBeInTheDocument();
  expect(api.chat).not.toHaveBeenCalled();
});

// The edit is committed even when the confirming GET fails; Retry generates
// new prose and cannot recover a saved edit.
test("a saved post reports its failed readback without retrying generation", async () => {
  scene();
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  fireEvent.change(screen.getByRole("textbox", { name: "Edit message" }), { target: { value: "changed words" } });
  vi.mocked(api.getScene).mockRejectedValue(new Error("post read offline"));
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await screen.findByText(/post saved.*scene could not be re-read.*post read offline/i);
  expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 0, "changed words");
  expect(screen.queryByRole("button", { name: /^Retry/ })).not.toBeInTheDocument();
  expect(api.chat).not.toHaveBeenCalled();
});
