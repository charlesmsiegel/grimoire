import { screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../api/client";

vi.mock("../api/client", async () => (await import("../testkit/configViewMocks")).configApiMock());
vi.mock("../components/ResponseTargetsPicker", async () => (await import("../testkit/configViewMocks")).responseTargetsMock());
vi.mock("../components/ImageStoreCard", async () => (await import("../testkit/configViewMocks")).imageStoreMock());
vi.mock("../components/RegexRulesEditor", async () => (await import("../testkit/configViewMocks")).regexRulesMock());
import { cfg, installConfigMocks, renderView, open, save } from "../testkit/configViewHarness";

beforeEach(installConfigMocks);

test("the Prompt layout section carries the toggle and the editor", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue({
    enabled: false,
    sections: [{ id: "world_info", label: "", default_label: "World info",
                 tier: "spotlight", enabled: true }],
  });
  renderView();
  await open(/^Prompt layout/);
  expect(await screen.findByRole("checkbox", { name: /use my section order/i }))
    .not.toBeChecked();
  expect(await screen.findByLabelText("Label for World info")).toBeInTheDocument();
});

test("the section order toggle saves as on/off", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue({ enabled: false, sections: [] });
  vi.mocked(api.putConfig).mockResolvedValue({ ...cfg, prompt_layout_enabled: "on" });
  renderView();
  await open(/^Prompt layout/);
  fireEvent.click(await screen.findByRole("checkbox", { name: /use my section order/i }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ prompt_layout_enabled: "on" })));
});

const twoRows = () => ({
  enabled: false,
  sections: [
    { id: "world_info", label: "", default_label: "World info",
      tier: "spotlight", enabled: true },
    { id: "weather", label: "", default_label: "Weather",
      tier: "spotlight", enabled: true },
  ],
});

test("reordering a section counts as an unsaved change on the page", async () => {
  /** The trap this replaced: the panel held its own draft, so a reordered
   *  section left the footer reading "No unsaved changes" while the page's
   *  Save wrote everything except the reordering. */
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument();

  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(screen.getByText(/1 unsaved change$/i)).toBeInTheDocument();
});

test("the page's Save writes the layout, and writes no config when only it moved", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  vi.mocked(api.putPromptLayout).mockImplementation((sections) =>
    Promise.resolve({ enabled: false, sections: sections.map((s) => ({
      ...s, default_label: s.id === "weather" ? "Weather" : "World info",
      tier: "spotlight" })) }));
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  save();

  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalled());
  expect(vi.mocked(api.putPromptLayout).mock.calls[0][0].map((s) => s.id))
    .toEqual(["weather", "world_info"]);
  // config.md is untouched: an empty patch is a read-modify-write storing nothing
  expect(api.putConfig).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument());
});

test("Revert puts a reordered layout back", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id")))
    .toEqual(["weather", "world_info"]);

  fireEvent.click(screen.getByRole("button", { name: /^revert$/i }));
  expect(screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id")))
    .toEqual(["world_info", "weather"]);
  expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument();
});

test("Reset writes immediately and clears the pending reorder", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  vi.mocked(api.putPromptLayout).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);

  fireEvent.click(screen.getByRole("button", { name: /reset to default order/i }));
  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalledWith([]));
  await waitFor(() => expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument());
});

test("the layout is fetched only when its section is opened", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  renderView();
  await screen.findByRole("button", { name: /^Storage/ });
  expect(api.getPromptLayout).not.toHaveBeenCalled();
  await open(/^Prompt layout/);
  await waitFor(() => expect(api.getPromptLayout).toHaveBeenCalledTimes(1));
});

test("a failed layout write reports itself and leaves the settings unwritten", async () => {
  /** One Save, one outcome: the settings must not land while the layout the
   *  same click was meant to store did not. */
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  vi.mocked(api.putPromptLayout).mockRejectedValue({ detail: "disk full" });
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  fireEvent.click(await screen.findByRole("checkbox", { name: /use my section order/i }));
  save();

  expect(await screen.findByText(/disk full/i)).toBeInTheDocument();
  expect(api.putConfig).not.toHaveBeenCalled();
  // ...and the reorder is still on screen to retry, not silently discarded
  expect(screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id")))
    .toEqual(["weather", "world_info"]);
});


test("a failed Reset clears busy and preserves pending settings and layout order", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  vi.mocked(api.putPromptLayout).mockRejectedValueOnce({ detail: "disk full" })
    .mockResolvedValueOnce(twoRows());
  renderView();
  await open(/^Timeouts/);
  fireEvent.change(screen.getByLabelText(/^retries$/i), { target: { value: "4" } });
  await open(/^Prompt layout/);
  fireEvent.click(await screen.findByRole("button", { name: /move world info down/i }));
  fireEvent.click(screen.getByRole("button", { name: /reset to default order/i }));
  expect(await screen.findByText("Could not load the prompt layout.")).toBeInTheDocument();
  expect(api.putConfig).not.toHaveBeenCalled();
  expect(api.putPromptLayout).toHaveBeenCalledWith([]);
  expect(screen.getByRole("button", { name: /^save$/i })).toBeEnabled();
  await open(/^Timeouts/);
  expect(screen.getByLabelText(/^retries$/i)).toHaveValue("4");
  expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ llm_retries: "4" }));
  expect(vi.mocked(api.putPromptLayout).mock.calls[1][0].map((row) => row.id))
    .toEqual(["weather", "world_info"]);
});

test("switching sections retains a pending layout edit without another read", async () => {
  vi.mocked(api.getPromptLayout).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  fireEvent.change(await screen.findByLabelText("Label for World info"), { target: { value: "Realm" } });
  await open(/^Context/);
  await open(/^Prompt layout/);
  expect(screen.getByLabelText("Label for World info")).toHaveValue("Realm");
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
  expect(api.getPromptLayout).toHaveBeenCalledTimes(1);
});
