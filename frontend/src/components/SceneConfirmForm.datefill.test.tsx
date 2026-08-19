/** The date-fill button against the REAL `CalendarDatePicker`.
 *
 *  Separate from `SceneConfirmForm.test.tsx`, which stubs the picker out with a
 *  plain input: the one thing worth proving here is that the fill survives the
 *  picker's own mid-edit guard, and a stub has no such guard to survive. */
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SceneConfirmForm } from "./SceneConfirmForm";
import type { SceneDraft } from "./sceneDraft";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getCampaignClock: vi.fn(), getCalendarMonths: vi.fn(), listEntities: vi.fn(),
    listCharacters: vi.fn(), listCampaignPCs: vi.fn(), listAppearances: vi.fn(),
    createScene: vi.fn(), setSceneDatetime: vi.fn(),
  } };
});

const DRAFT: SceneDraft = {
  source: "custom", title: "A quiet night", defaultTitle: "A quiet night",
  date: "", location: "", pcless: false, premise: "", cast: [],
};

const MONTHS_5786 = [
  { key: "Tishrei", name: "Tishrei", days: 30 },
  { key: "Kislev", name: "Kislev", days: 30 },
  { key: "Tevet", name: "Tevet", days: 29 },
];

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaignClock as any).mockResolvedValue(
    { now: "5786-Kislev-25", friendly: "25 Kislev 5786", log: [] });
  (api.getCalendarMonths as any).mockResolvedValue({ months: MONTHS_5786 });
  (api.listEntities as any).mockResolvedValue([]);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
});

function renderForm() {
  render(<SceneConfirmForm cid="c" draft={DRAFT} ready onBack={() => {}} onCreated={vi.fn()} />);
}

test("the fill lands in the year, month and day controls", async () => {
  renderForm();
  fireEvent.click(await screen.findByRole("button", { name: /last scene's date/i }));

  expect(await screen.findByLabelText("Scene date year")).toHaveValue(5786);
  expect(await screen.findByLabelText("Scene date month")).toHaveValue("Kislev");
  expect(await screen.findByLabelText("Scene date day")).toHaveValue("25");
});

test("the fill wins over a half-typed date the picker would otherwise protect", async () => {
  renderForm();
  const button = await screen.findByRole("button", { name: /last scene's date/i });
  // A year alone leaves the picker "dirty": it drops externally-arriving values
  // in that state so an async prefill cannot stomp an edit in progress. A click
  // on this button is not that — it is the edit.
  await userEvent.type(await screen.findByLabelText("Scene date year"), "1999");

  fireEvent.click(button);

  expect(await screen.findByLabelText("Scene date year")).toHaveValue(5786);
  expect(await screen.findByLabelText("Scene date month")).toHaveValue("Kislev");
  expect(await screen.findByLabelText("Scene date day")).toHaveValue("25");
});
