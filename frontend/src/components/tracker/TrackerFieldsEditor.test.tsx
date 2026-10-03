import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { TrackerFieldsEditor } from "./TrackerFieldsEditor";
import { api } from "../../api/client";
import type { TrackerField, TrackerLayer, TrackerLayerBundle } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getTrackerFields: vi.fn(), setTrackerFields: vi.fn() },
}));

const ATTENTION: TrackerField = {
  key: "attention", label: "Attention", type: "text", aware: "present", hint: "Who they watch.",
};
const MOOD: TrackerField = {
  key: "visible_mood", label: "Visible mood", type: "enum", aware: "present", hint: "Readable demeanour.",
  options: ["calm", "fear"],
};
const INHERITED = [ATTENTION, MOOD];

/** A bundle the way the server builds it: `effective` is `inherited` with the layer laid over. */
function bundle(layer: TrackerLayer, inherited: TrackerField[] = INHERITED): TrackerLayerBundle {
  const eff = inherited.map((f) => {
    const merged = { ...f, ...(layer.change?.[f.key] ?? {}) };
    return (layer.off ?? []).includes(f.key) ? { ...merged, off: true } : merged;
  });
  return { layer, effective: [...eff, ...(layer.fields ?? [])], inherited };
}

const CAMPAIGN = { kind: "campaign" as const, cid: "saltmarch" };
const WORLD = { kind: "world" as const, wid: "realm" };

function serve(layer: TrackerLayer, inherited?: TrackerField[]) {
  vi.mocked(api.getTrackerFields).mockResolvedValue(bundle(layer, inherited));
}

beforeEach(() => {
  vi.mocked(api.getTrackerFields).mockReset();
  vi.mocked(api.setTrackerFields).mockReset().mockResolvedValue(bundle({}));
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => vi.restoreAllMocks());

async function open(label: string, scope: typeof CAMPAIGN | typeof WORLD = CAMPAIGN) {
  render(<TrackerFieldsEditor scope={scope} />);
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(label) }));
  return await screen.findByRole("heading", { name: label });
}

test("clicking a row shows the read-only view with its sidebar", async () => {
  serve({});
  await open("Attention");
  expect(document.querySelector("input, textarea")).toBeNull();
  expect(screen.getByText("Who they watch.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(screen.getByText("type: text")).toBeInTheDocument();
  expect(screen.getByText("known to: everyone present")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Switch off" })).toBeInTheDocument();
});

test("an enum shows its options as chips", async () => {
  serve({});
  await open("Visible mood");
  expect(screen.getByText("calm")).toBeInTheDocument();
  expect(screen.getByText("fear")).toBeInTheDocument();
});

test("Edit reveals the form", async () => {
  serve({});
  await open("Attention");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Label")).toHaveValue("Attention");
  expect(screen.getByLabelText("Key")).toHaveAttribute("readonly");
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
});

test("+ New field opens the form directly", async () => {
  serve({});
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New field" }));
  expect(screen.getByRole("heading", { name: "New field" })).toBeInTheDocument();
  expect(screen.getByLabelText("Label")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("inherited fields are labelled by the layer they sit on", async () => {
  serve({});
  await open("Attention", WORLD);
  expect(screen.getByText("built-in")).toBeInTheDocument();
});

test("a campaign calls them inherited", async () => {
  serve({});
  await open("Attention");
  expect(screen.getByText("inherited")).toBeInTheDocument();
});

test("switching off an inherited field writes it to off", async () => {
  serve({});
  await open("Attention");
  fireEvent.click(screen.getByRole("button", { name: "Switch off" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    CAMPAIGN, { fields: [], change: {}, off: ["attention"] }));
});

test("a switched-off field stays in the rail, dimmed, and can be switched back on", async () => {
  serve({ off: ["attention"], change: { visible_mood: { label: "Mood" } } });
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  const row = await screen.findByRole("button", { name: /Attention/ });
  expect(row.className).toMatch(/\boff\b/);
  fireEvent.click(row);
  fireEvent.click(await screen.findByRole("button", { name: "Switch on" }));
  // The other change in the layer is carried, not dropped.
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    CAMPAIGN, { fields: [], change: { visible_mood: { label: "Mood" } }, off: [] }));
});

test("a field switched off by a layer below cannot be switched on here", async () => {
  serve({}, [{ ...ATTENTION, off: true }, MOOD]);
  await open("Attention");
  expect(screen.queryByRole("button", { name: "Switch on" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Switch off" })).toBeNull();
  expect(screen.getByText(/Switched off by a layer below/)).toBeInTheDocument();
});

test("editing an inherited field writes only what differs as a change", async () => {
  serve({});
  await open("Attention");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Focus" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    CAMPAIGN, { fields: [], change: { attention: { label: "Focus" } }, off: [] }));
  // Back to the read-only view, and the list was re-read.
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.getTrackerFields).toHaveBeenCalledTimes(2);
});

test("changing an enum's options is a change of options alone", async () => {
  serve({});
  await open("Visible mood");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Options"), { target: { value: "calm, fear, joy" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    CAMPAIGN, { fields: [], change: { visible_mood: { options: ["calm", "fear", "joy"] } }, off: [] }));
});

test("an edit that restores the inherited field drops the change entry", async () => {
  serve({ change: { attention: { label: "Focus" } } });
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: /Focus/ }));
  await screen.findByText("changed here");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Attention" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    CAMPAIGN, { fields: [], change: {}, off: [] }));
});

test("Revert drops this layer's change for that key only", async () => {
  serve({ change: { attention: { label: "Focus" }, visible_mood: { label: "Mood" } } });
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: /Focus/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Revert" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    CAMPAIGN, { fields: [], change: { visible_mood: { label: "Mood" } }, off: [] }));
});

test("a new field is written to fields, with the key following the label", async () => {
  serve({});
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New field" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Held Breath" } });
  expect(screen.getByLabelText("Key")).toHaveValue("held_breath");
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "enum" } });
  fireEvent.change(screen.getByLabelText("Options"), { target: { value: "shallow, deep" } });
  fireEvent.change(screen.getByLabelText("Default awareness"), { target: { value: "self" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(CAMPAIGN, {
    fields: [{ key: "held_breath", label: "Held Breath", type: "enum", aware: "self", hint: "",
               options: ["shallow", "deep"] }],
    change: {}, off: [],
  }));
});

test("a bad key and a taken key are refused before any request", async () => {
  serve({});
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New field" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Thing" } });
  fireEvent.change(screen.getByLabelText("Key"), { target: { value: "Bad Key" } });
  expect(screen.getByText(/lowercase letters, digits and underscores/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Key"), { target: { value: "attention" } });
  expect(screen.getByText(/already exists/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(api.setTrackerFields).not.toHaveBeenCalled();
});

test("an enum with no options is refused client-side", async () => {
  serve({});
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: "+ New field" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Gait" } });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "enum" } });
  expect(screen.getByText("An enum needs at least one option.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("a field this layer added is removed, not switched off, and editing rewrites its entry", async () => {
  const mine: TrackerField = { key: "gait", label: "Gait", type: "text", aware: "present", hint: "" };
  serve({ fields: [mine] });
  await open("Gait");
  expect(screen.getByText("this layer")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Switch off" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Stride" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(CAMPAIGN, {
    fields: [{ ...mine, label: "Stride" }], change: {}, off: [] }));
  vi.mocked(api.setTrackerFields).mockClear();
  fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(CAMPAIGN, {
    fields: [], change: {}, off: [] }));
});

test("an inherited field is never written back as this layer's own", async () => {
  serve({ change: { attention: { label: "Focus" } } });
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  fireEvent.click(await screen.findByRole("button", { name: /Visible mood/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Switch off" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalled());
  const sent = vi.mocked(api.setTrackerFields).mock.calls[0][1];
  expect(sent.fields).toEqual([]);
});

test("the server's refusal is shown and the form stays open", async () => {
  serve({});
  vi.mocked(api.setTrackerFields).mockRejectedValue({ detail: "field 'x': an enum needs a non-empty options list" });
  await open("Attention");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "Focus" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("an enum needs a non-empty options list");
  expect(screen.getByLabelText("Label")).toHaveValue("Focus");
});

test("Cancel returns to the read-only view", async () => {
  serve({});
  await open("Attention");
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.setTrackerFields).not.toHaveBeenCalled();
});

test("a world layer is read and written at the world scope", async () => {
  serve({});
  await open("Attention", WORLD);
  fireEvent.click(screen.getByRole("button", { name: "Switch off" }));
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    WORLD, { fields: [], change: {}, off: ["attention"] }));
  expect(within(screen.getByRole("heading", { name: "Attention" }).closest(".detail-view") as HTMLElement)
    .getByText("built-in")).toBeInTheDocument();
});

test("a failed read says so, with a way to try again", async () => {
  vi.mocked(api.getTrackerFields).mockRejectedValueOnce(new Error("down"));
  render(<TrackerFieldsEditor scope={CAMPAIGN} />);
  expect(await screen.findByText(/could not be read/)).toBeInTheDocument();
  serve({});
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByRole("button", { name: /Attention/ })).toBeInTheDocument();
});
