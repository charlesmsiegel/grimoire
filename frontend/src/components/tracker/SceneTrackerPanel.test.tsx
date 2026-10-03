import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SceneTrackerPanel } from "./SceneTrackerPanel";
import { api } from "../../api/client";
import type { TrackerField, TrackerLayer, TrackerLayerBundle } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getTrackerFields: vi.fn(), setTrackerFields: vi.fn() },
}));

const ATTENTION: TrackerField = { key: "attention", label: "Attention", type: "text", aware: "present", hint: "" };
const CLOTHING: TrackerField = { key: "clothing", label: "Clothing", type: "text", aware: "present", hint: "" };
const SCENE = { kind: "scene", cid: "saltmarch", sid: "s1" };

function serve(layer: TrackerLayer, inherited: TrackerField[] = [ATTENTION, CLOTHING]) {
  const off = new Set(layer.off ?? []);
  const eff = inherited.map((f) => (off.has(f.key) ? { ...f, off: true } : f));
  const have = new Set(inherited.map((f) => f.key));
  const added = (layer.fields ?? []).filter((f) => !have.has(f.key));
  const b: TrackerLayerBundle = { layer, effective: [...eff, ...added], inherited };
  vi.mocked(api.getTrackerFields).mockResolvedValue(b);
  // The server answers a PUT with the bundle for what it stored.
  vi.mocked(api.setTrackerFields).mockImplementation(async (_s, sent) => {
    const off2 = new Set(sent.off ?? []);
    return { layer: sent, inherited,
      effective: [...inherited.map((f) => (off2.has(f.key) ? { ...f, off: true } : f)),
                  ...(sent.fields ?? []).filter((f) => !have.has(f.key))] };
  });
}

beforeEach(() => {
  vi.mocked(api.getTrackerFields).mockReset();
  vi.mocked(api.setTrackerFields).mockReset().mockResolvedValue({} as TrackerLayerBundle);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

test("scene panel toggles a field off for this scene only", async () => {
  serve({});
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  const box = await screen.findByRole("checkbox", { name: "Attention" });
  expect(box).toBeChecked();
  fireEvent.click(box);
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(SCENE, { fields: [], off: ["attention"] }));
  expect(api.getTrackerFields).toHaveBeenCalledWith(SCENE);
});

test("a field already off for the scene is unticked, and ticking it switches it back on", async () => {
  serve({ off: ["attention", "clothing"] });
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  const box = await screen.findByRole("checkbox", { name: "Attention" });
  expect(box).not.toBeChecked();
  fireEvent.click(box);
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(SCENE, { fields: [], off: ["clothing"] }));
});

test("a field a layer below switched off is shown unticked and cannot be ticked here", async () => {
  serve({}, [{ ...ATTENTION, off: true }, CLOTHING]);
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  const box = await screen.findByRole("checkbox", { name: "Attention" });
  expect(box).not.toBeChecked();
  expect(box).toBeDisabled();
});

test("a scene never sends a change", async () => {
  serve({ fields: [{ key: "gait", label: "Gait", type: "text", aware: "present", hint: "" }] });
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Clothing" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalled());
  const sent = vi.mocked(api.setTrackerFields).mock.calls[0][1];
  expect(sent).not.toHaveProperty("change");
  expect(sent.fields).toHaveLength(1);
});

test("scene-only fields are listed with Remove, apart from the inherited checkboxes", async () => {
  const gait: TrackerField = { key: "gait", label: "Gait", type: "text", aware: "present", hint: "" };
  serve({ fields: [gait] });
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  await screen.findByRole("checkbox", { name: "Attention" });
  expect(screen.queryByRole("checkbox", { name: "Gait" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove Gait" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(SCENE, { fields: [], off: [] }));
});

test("+ Scene-only field adds a field to the scene's layer", async () => {
  serve({});
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Scene-only field" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Torch Light" } });
  fireEvent.change(screen.getByLabelText("Hint"), { target: { value: "How bright it burns." } });
  fireEvent.click(screen.getByRole("button", { name: "Add field" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(SCENE, {
    fields: [{ key: "torch_light", label: "Torch Light", type: "text", aware: "present",
               hint: "How bright it burns." }],
    off: [],
  }));
});

test("a key an inherited field already has is refused before any request", async () => {
  serve({});
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Scene-only field" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Clothing" } });
  expect(screen.getByText(/already exists/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add field" })).toBeDisabled();
});

test("the server's refusal, a scene held by a turn, is shown", async () => {
  serve({});
  vi.mocked(api.setTrackerFields).mockRejectedValue({ detail: "scene_busy" });
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Attention" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("scene_busy");
});

test("an addition the campaign has since taken over is an inherited checkbox, and is not written back", async () => {
  const stale: TrackerField = { key: "attention", label: "Old", type: "text", aware: "self", hint: "" };
  serve({ fields: [stale] });
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  const box = await screen.findByRole("checkbox", { name: "Attention" });
  expect(screen.queryByText("Scene-only fields")).toBeNull();
  fireEvent.click(box);
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(SCENE, { fields: [], off: ["attention"] }));
});

test("the panel shows the bundle a write answers with, without a second read", async () => {
  serve({});
  render(<SceneTrackerPanel cid="saltmarch" sid="s1" />);
  fireEvent.click(await screen.findByRole("checkbox", { name: "Attention" }));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "Attention" })).not.toBeChecked());
  expect(api.getTrackerFields).toHaveBeenCalledTimes(1);
});
