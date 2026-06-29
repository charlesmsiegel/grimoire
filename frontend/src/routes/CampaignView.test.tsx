import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import CampaignView from "./CampaignView";

// CastPanel has its own test + makes its own API calls; stub it here to keep this test focused.
vi.mock("../components/CastPanel", () => ({ CastPanel: () => null }));

vi.mock("../api/client", () => ({
  api: {
    getCampaign: vi.fn(),
    listScenes: vi.fn(),
    getScene: vi.fn(),
    createScene: vi.fn(),
    renameScene: vi.fn(),
    deleteScene: vi.fn(),
    chat: vi.fn(),
    retry: vi.fn(),
    getConfig: vi.fn(),
    editMessage: vi.fn(),
    // consumed by the embedded SceneInspector
    getCast: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
    getCastDetail: vi.fn(), readEntity: vi.fn(),
    listCharacters: vi.fn(), listPCs: vi.fn(), listCampaignPCs: vi.fn(),
    campaignImageUrl: () => "/img",
  },
}));
vi.mock("../api/models", () => ({ fetchModels: vi.fn() }));
import { api } from "../api/client";
import { fetchModels } from "../api/models";

const ONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "" }];

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Run One", world: "w" }, body: "" });
  (api.listScenes as any).mockResolvedValue([]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.renameScene as any).mockResolvedValue({ id: "s1", title: "New" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.chat as any).mockResolvedValue(undefined);
  (api.retry as any).mockResolvedValue(undefined);
  (api.getConfig as any).mockResolvedValue({ model: "m", theme: "occult", key_set: true, system_prompt: "", quote_color: "off" });
  (api.editMessage as any).mockResolvedValue({ ok: true });
  (api.getCast as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({ model: "m", total_tokens: 0, sections: [] });
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (fetchModels as any).mockResolvedValue([]);
});

function renderCampaign() {
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <Routes>
        <Route path="/campaigns/:cid" element={<CampaignView keySet={true} />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("shows the campaign name and loads its scenes", async () => {
  renderCampaign();
  await screen.findByText("Run One");
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("run"));
});

test("renders the inspector for an active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText(/Active characters/i);
  await screen.findByText(/^Context/);
});

test("editing a message saves and reloads", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getAllByRole("button", { name: /edit/i })[0]);
  const ta = await screen.findByLabelText(/edit message/i);
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 0, "hello"));
});

test("Enter sends a message in the active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() =>
    expect(api.chat).toHaveBeenCalledWith("run", "s1", "hello", expect.any(Function)),
  );
});

test("Shift+Enter does not send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});

test("sending with no scene creates one first", async () => {
  (api.listScenes as any).mockResolvedValue([]);
  renderCampaign();
  await waitFor(() => expect(api.listScenes).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hi" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("run"));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "hi", expect.any(Function)));
});

test("the edit button renames a scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "New"));
});

test("the delete button deletes a scene after confirm", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("run", "s1"));
});

test("declining the delete confirm does nothing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(api.deleteScene).not.toHaveBeenCalled();
});

test("an error shows a Retry button that retries the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "boom" } });
  });
  renderCampaign();
  await screen.findByText("Old");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const retryBtn = await screen.findByRole("button", { name: /retry/i });
  fireEvent.click(retryBtn);
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith("run", "s1", expect.any(Function)));
  expect(screen.getAllByText("hello")).toHaveLength(1);
});
