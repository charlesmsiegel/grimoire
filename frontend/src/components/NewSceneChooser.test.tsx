import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";

vi.mock("../api/client", () => ({
  api: {
    availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), createScene: vi.fn(),
    startFromGreeting: vi.fn(), addToCast: vi.fn(), setSceneLocation: vi.fn(),
    deleteScene: vi.fn(),
  },
}));
import { api } from "../api/client";

const GREETINGS = [
  { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true },
  { id: "open", name: "Open", available: true, reasons: [], unlocked: false },
  { id: "gala", name: "Gala", available: false, reasons: ["missing required tags"], unlocked: false },
  { id: "dawn", name: "Dawn", available: true, reasons: [], unlocked: false },
];
const SUGGESTION = {
  title: "The creditor", premise: "A debt-collector arrives.",
  cast: [{ kind: "characters", id: "doran", name: "Doran" }],
  location: { id: "keep", name: "The Keep" },
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.availableGreetings as any).mockResolvedValue(GREETINGS);
  (api.sceneSuggestions as any).mockResolvedValue({ suggestions: [SUGGESTION,
    { title: "Storm watch", premise: "Thunder over the marsh.", cast: [], location: null }] });
  (api.createScene as any).mockResolvedValue({ id: "s9" });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
});

function renderChooser(props: Partial<{ afterSid: string | null; keySet: boolean;
                                        onClose: () => void; onCreated: (sid: string, p?: string) => void }> = {}) {
  render(<NewSceneChooser cid="c" afterSid={props.afterSid !== undefined ? props.afterSid : "s1"}
                          keySet={props.keySet ?? true}
                          onClose={props.onClose ?? (() => {})}
                          onCreated={props.onCreated ?? (() => {})} />);
}

test("renders 2 greeting cards (unlocked first) and generated cards once loaded", async () => {
  renderChooser();
  await screen.findByText("Reckoning");
  expect(screen.getByText("unlocked")).toBeInTheDocument();
  expect(screen.getByText("Open")).toBeInTheDocument();
  expect(screen.queryByText("Dawn")).toBeNull();        // capped at 2 when generation is on
  expect(screen.queryByText("Gala")).toBeNull();        // unavailable greetings never show
  await screen.findByText("The creditor");              // async generated card
  expect(screen.getByText("Storm watch")).toBeInTheDocument();
  expect(api.availableGreetings).toHaveBeenCalledWith("c", "s1");
});

test("picking a greeting creates a scene, starts it, and reports the sid", async () => {
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("Reckoning"));
  await waitFor(() => expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9", "reck"));
  expect(api.createScene).toHaveBeenCalledWith("c");
  expect(onCreated).toHaveBeenCalledWith("s9");
});

test("picking a generated card seeds cast + location and passes the premise", async () => {
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", "A debt-collector arrives."));
  expect(api.addToCast).toHaveBeenCalledWith("c", "s9", { kind: "characters", id: "doran" });
  expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s9", "keep");
});

test("Create manually only creates the scene", async () => {
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9"));
  expect(api.startFromGreeting).not.toHaveBeenCalled();
  expect(api.addToCast).not.toHaveBeenCalled();
});

test("Cancel closes without creating anything", async () => {
  const onClose = vi.fn();
  renderChooser({ onClose });
  fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
  expect(onClose).toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("without a key: no suggestions fetch, hint shown, up to 4 greetings", async () => {
  renderChooser({ keySet: false });
  await screen.findByText("Reckoning");
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(screen.getByText(/set an openrouter key/i)).toBeInTheDocument();
  expect(screen.getByText("Dawn")).toBeInTheDocument(); // slot cap grows to 4
});

test("a failed seed deletes the orphan scene and keeps the chooser open", async () => {
  (api.startFromGreeting as any).mockRejectedValue({ detail: "boom" });
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("Reckoning"));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("c", "s9"));
  expect(onCreated).not.toHaveBeenCalled();
  expect(await screen.findByText("boom")).toBeInTheDocument();
});

test("no afterSid fetches availability without the param", async () => {
  renderChooser({ afterSid: null });
  await screen.findByText("Reckoning");
  expect(api.availableGreetings).toHaveBeenCalledWith("c", undefined);
});
