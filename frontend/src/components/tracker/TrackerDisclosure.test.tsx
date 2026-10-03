import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TrackerDisclosure, summaryText } from "./TrackerDisclosure";
import { shareSummary } from "./share";
import { api } from "../../api/client";
import type { TrackerEntry, TrackerRecord, TrackerSummary } from "../../api/client";

vi.mock("../../api/client", () => ({ api: { getTrackerRecord: vi.fn(), editTrackerRecord: vi.fn(),
  retryTracker: vi.fn(), rerunTrackerFrom: vi.fn() } }));

const F = { upstream_changed: false, text_changed: false };
const NAMES = { "characters:mara": "Mara", "characters:winifred": "Winifred" };
const LABELS = { visible_mood: "Visible mood", clothing: "Clothing", companions: "Companions" };

const RECORD: TrackerRecord = {
  key: "p-1", status: "ok", flags: F, names: NAMES,
  fields: [
    { key: "visible_mood", label: "Visible mood", type: "enum", aware: "present", hint: "", options: ["calm", "fear"] },
    { key: "clothing", label: "Clothing", type: "text", aware: "present", hint: "" },
    { key: "companions", label: "Companions", type: "list", aware: "present", hint: "" },
  ],
  snapshot: {
    "characters:winifred": { present: false, fields: { clothing: { value: "apron", aware: "present" } } },
    "characters:mara": { present: true, fields: {
      visible_mood: { value: "fear", aware: "present" },
      clothing: { value: "cloak", aware: [] },
      companions: { value: ["Winifred"], aware: ["characters:winifred"] },
    } },
  },
};
const OK: TrackerEntry = { status: "ok", changed: [["characters:mara", "visible_mood", "fear"]], flags: F };

function mount(entry: TrackerEntry | undefined, over: Partial<{ enabled: boolean }> = {}) {
  const onChanged = vi.fn();
  render(<TrackerDisclosure cid="run" sid="s1" trackerKey="p-1" entry={entry}
    names={NAMES} labels={LABELS} enabled={over.enabled ?? true} onChanged={onChanged} />);
  return onChanged;
}

beforeEach(() => {
  vi.mocked(api.getTrackerRecord).mockReset().mockResolvedValue(RECORD);
  vi.mocked(api.editTrackerRecord).mockReset().mockResolvedValue(RECORD);
  vi.mocked(api.retryTracker).mockReset().mockResolvedValue({} as TrackerSummary);
  vi.mocked(api.rerunTrackerFrom).mockReset().mockResolvedValue({} as TrackerSummary);
});

test("summary states", () => {
  expect(summaryText(undefined, {}, {})).toBe("Tracker · untracked");
  expect(summaryText({ status: "pending", changed: [], flags: F }, {}, {})).toBe("Tracker · updating…");
  expect(summaryText({ status: "ok", changed: [], flags: F }, {}, {})).toBe("Tracker · no change");
  expect(summaryText({ status: "ok", changed: [["characters:mara", "visible_mood", "fear"]], flags: F },
    { "characters:mara": "Mara" }, { visible_mood: "Visible mood" })).toBe("Tracker · Mara: Visible mood → fear");
  expect(summaryText({ status: "ok", changed: [["characters:mara", "companions", ["A", "B"]],
    ["characters:x", "mood", "calm"]], flags: F }, { "characters:mara": "Mara" }, {}))
    .toBe("Tracker · Mara: companions → A, B; characters:x: mood → calm");
  expect(summaryText({ status: "failed", changed: [], flags: F }, {}, {})).toBe("Tracker · untracked");
  expect(summaryText({ status: "ok", changed: [], flags: { ...F, upstream_changed: true } }, {}, {}))
    .toBe("Tracker · no change · earlier state changed");
  expect(summaryText({ status: "ok", changed: [], flags: { ...F, text_changed: true } }, {}, {}))
    .toBe("Tracker · no change · text changed since tracked");
});

test("fetches only on open and renders read-only values with awareness", async () => {
  const { container } = render(<TrackerDisclosure cid="run" sid="s1" trackerKey="p-1" entry={OK}
    names={NAMES} labels={LABELS} enabled onChanged={vi.fn()} />);
  expect(api.getTrackerRecord).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Tracker · Mara: Visible mood → fear"));
  expect(await screen.findAllByText("Clothing")).toHaveLength(2);
  expect(api.getTrackerRecord).toHaveBeenCalledWith("run", "s1", "p-1");
  expect(screen.getByText("private")).toBeInTheDocument();
  expect(screen.getByText("known to: Winifred")).toBeInTheDocument();
  expect(screen.getByText(/Mara/, { selector: "h5" })).toBeInTheDocument();
  expect(screen.getByText(/Winifred \(not present\)/)).toBeInTheDocument();
  // Present actors come first, and this post's own change is marked.
  const names = [...container.querySelectorAll("h5")].map((h) => h.textContent);
  expect(names).toEqual(["Mara", "Winifred (not present)"]);
  expect(container.querySelectorAll("li.changed")).toHaveLength(1);
  expect(container.querySelector("textarea")).toBeNull();
  expect(container.querySelector("input")).toBeNull();
});

test("Edit reveals the form and Save sends only changed values", async () => {
  const onChanged = mount(OK);
  fireEvent.click(screen.getByText(/Tracker · /));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Mara Clothing"), { target: { value: "wet cloak" } });
  fireEvent.change(screen.getByLabelText("Mara Clothing awareness"), { target: { value: "present" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.editTrackerRecord).toHaveBeenCalledTimes(1));
  expect(api.editTrackerRecord).toHaveBeenCalledWith("run", "s1", "p-1", {
    "characters:mara": { clothing: { value: "wet cloak", aware: "present" } },
  });
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
  // Back to the read-only view.
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("list values split on commas, and a person can be added to who knows", async () => {
  mount(OK);
  fireEvent.click(screen.getByText(/Tracker · /));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Mara Companions"), { target: { value: "Winifred, Seraphine" } });
  fireEvent.change(screen.getByLabelText("Mara Visible mood"), { target: { value: "calm" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.editTrackerRecord).toHaveBeenCalled());
  expect(vi.mocked(api.editTrackerRecord).mock.calls[0][3]).toEqual({
    "characters:mara": { companions: { value: ["Winifred", "Seraphine"] }, visible_mood: { value: "calm" } },
  });
});

test("Save with nothing changed sends nothing", async () => {
  mount(OK);
  fireEvent.click(screen.getByText(/Tracker · /));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.editTrackerRecord).not.toHaveBeenCalled();
});

test("an edit the server refuses is shown, not swallowed", async () => {
  vi.mocked(api.editTrackerRecord).mockRejectedValue(new Error("scene is busy"));
  mount(OK);
  fireEvent.click(screen.getByText(/Tracker · /));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Mara Clothing"), { target: { value: "x" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/scene is busy/);
});

test("Retry on a failed entry calls retryTracker and asks the view to refresh", async () => {
  const onChanged = mount({ status: "failed", changed: [], flags: F, error: "interrupted" });
  expect(screen.getByText("Tracker · untracked")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(api.retryTracker).toHaveBeenCalledWith("run", "s1", "p-1"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

test("a key with no entry reads untracked and offers Retry", () => {
  mount(undefined);
  expect(screen.getByText("Tracker · untracked")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
});

test("a retry the server refuses is shown inline", async () => {
  vi.mocked(api.retryTracker).mockRejectedValue(new Error("tracker_off"));
  mount(undefined);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/tracker_off/);
});

test("Re-run tracker from here calls rerunTrackerFrom and refreshes", async () => {
  const onChanged = mount(OK);
  fireEvent.click(screen.getByText(/Tracker · /));
  fireEvent.click(await screen.findByRole("button", { name: "Re-run tracker from here" }));
  await waitFor(() => expect(api.rerunTrackerFrom).toHaveBeenCalledWith("run", "s1", "p-1"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

test("a pending entry reads updating and offers no Retry", () => {
  mount({ status: "pending", changed: [], flags: F });
  expect(screen.getByText("Tracker · updating…")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});

test("with the tracker off, a key with no entry renders nothing", () => {
  const { container } = render(<TrackerDisclosure cid="run" sid="s1" trackerKey="p-1" entry={undefined}
    names={NAMES} labels={LABELS} enabled={false} onChanged={vi.fn()} />);
  expect(container).toBeEmptyDOMElement();
});

test("with the tracker off, an existing entry is read-only", async () => {
  mount(OK, { enabled: false });
  fireEvent.click(screen.getByText(/Tracker · /));
  expect(await screen.findAllByText("Clothing")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Re-run tracker from here" })).toBeNull();
});

test("shareSummary keeps the identity of everything a re-read did not change", () => {
  const a: TrackerSummary = { enabled: true, names: NAMES, labels: LABELS, moods: {},
    keys: [{ index: 0, key: "p-1" }, { index: 2, key: "p-2" }], entries: { "p-1": OK, "p-2": OK } };
  const same = JSON.parse(JSON.stringify(a)) as TrackerSummary;
  expect(shareSummary(a, same)).toBe(a);
  const moved = JSON.parse(JSON.stringify(a)) as TrackerSummary;
  moved.entries["p-2"] = { status: "failed", changed: [], flags: F };
  const out = shareSummary(a, moved);
  expect(out).not.toBe(a);
  expect(out.entries["p-1"]).toBe(a.entries["p-1"]);
  expect(out.entries["p-2"].status).toBe("failed");
  expect(out.names).toBe(a.names);
  expect(out.keys).toBe(a.keys);
});
