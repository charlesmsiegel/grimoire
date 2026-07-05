import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";

vi.mock("../api/client", () => ({
  api: {
    availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), createScene: vi.fn(),
    startFromGreeting: vi.fn(), addToCast: vi.fn(), addCastBatch: vi.fn(), setSceneLocation: vi.fn(),
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
  (api.addCastBatch as any).mockResolvedValue({ ok: true, added: 1, skipped: [] });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
});

async function renderChooser(props: Partial<{ afterSid: string | null; keySet: boolean;
                                              onClose: () => void; onCreated: (sid: string, p?: string) => void }> = {}) {
  render(<NewSceneChooser cid="c" afterSid={props.afterSid !== undefined ? props.afterSid : "s1"}
                          keySet={props.keySet ?? true}
                          onClose={props.onClose ?? (() => {})}
                          onCreated={props.onCreated ?? (() => {})} />);
  fireEvent.click(await screen.findByText("With your PC"));
}

test("renders 2 greeting cards (unlocked first) and generated cards once loaded", async () => {
  await renderChooser();
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
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("Reckoning"));
  await waitFor(() => expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9", "reck"));
  expect(api.createScene).toHaveBeenCalledWith("c", undefined, undefined, false);
  expect(onCreated).toHaveBeenCalledWith("s9");
});

test("picking a generated card seeds cast + location and passes the premise", async () => {
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", "A debt-collector arrives."));
  expect(api.addCastBatch).toHaveBeenCalledWith("c", "s9", [{ kind: "characters", id: "doran" }]);
  expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s9", "keep");
});

test("Create manually only creates the scene", async () => {
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9"));
  expect(api.startFromGreeting).not.toHaveBeenCalled();
  expect(api.addCastBatch).not.toHaveBeenCalled();
});

test("Cancel closes without creating anything", async () => {
  const onClose = vi.fn();
  await renderChooser({ onClose });
  fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
  expect(onClose).toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("without a key: no suggestions fetch, hint shown, up to 4 greetings", async () => {
  await renderChooser({ keySet: false });
  await screen.findByText("Reckoning");
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(screen.getByText(/set an openrouter key/i)).toBeInTheDocument();
  expect(screen.getByText("Dawn")).toBeInTheDocument(); // slot cap grows to 4
});

test("a failed seed deletes the orphan scene and keeps the chooser open", async () => {
  (api.startFromGreeting as any).mockRejectedValue({ detail: "boom" });
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("Reckoning"));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("c", "s9"));
  expect(onCreated).not.toHaveBeenCalled();
  expect(await screen.findByText("boom")).toBeInTheDocument();
});

test("already-cast members (skipped server-side) don't block the pick", async () => {
  (api.addCastBatch as any).mockResolvedValue({ ok: true, added: 0, skipped: ["characters/doran"] });
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", "A debt-collector arrives."));
  expect(api.deleteScene).not.toHaveBeenCalled();
});

test("a cast seeding failure aborts the pick and cleans up the scene", async () => {
  (api.addCastBatch as any).mockRejectedValue({ status: 500, detail: "boom" });
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("c", "s9"));
  expect(onCreated).not.toHaveBeenCalled();
  expect(await screen.findByText("boom")).toBeInTheDocument();
});

test("a failed greetings fetch surfaces in the banner", async () => {
  (api.availableGreetings as any).mockRejectedValue({ status: 500, detail: "greetings down" });
  await renderChooser();
  expect(await screen.findByText("greetings down")).toBeInTheDocument();
  expect(screen.getByText("No available greetings.")).toBeInTheDocument();
});

test("no afterSid fetches availability without the param", async () => {
  await renderChooser({ afterSid: null });
  await screen.findByText("Reckoning");
  expect(api.availableGreetings).toHaveBeenCalledWith("c", undefined);
});

test("renders exactly the server-filtered greeting list (skipped absent, marks tolerated)", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", available: true, reasons: [], unlocked: false, mark: "completed" },
  ]);
  await renderChooser();
  await screen.findByText("Gala");                     // a marked-complete greeting still renders
  expect(screen.queryByText("Reckoning")).toBeNull();  // nothing beyond the server's list
});

test("with >2 greetings the section shows Choosing… until the LLM call lands", async () => {
  let release: (v: unknown) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValue(new Promise((r) => { release = r; }));
  await renderChooser();
  await screen.findByText(/choosing…/i);
  expect(screen.queryByText("Reckoning")).toBeNull();
  release({ suggestions: [SUGGESTION], greeting_picks: [] });
  await screen.findByText("Reckoning"); // empty picks: falls back to today's order
  expect(screen.queryByText(/choosing…/i)).toBeNull();
});

test("greeting picks choose and order the greeting cards", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], greeting_picks: ["dawn", "reck"] });
  await renderChooser();
  await screen.findByText("Dawn");
  expect(screen.getByText("Reckoning")).toBeInTheDocument();
  expect(screen.queryByText("Open")).toBeNull(); // present but not picked
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false);
});

test("without a key greetings render immediately, no Choosing…", async () => {
  await renderChooser({ keySet: false });
  await screen.findByText("Reckoning");
  expect(screen.queryByText(/choosing…/i)).toBeNull();
});

test("picking a generated card passes its suggested date to createScene", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [{ ...SUGGESTION, date: "2026-07-10" }], next_date: "2026-07-08" });
  await renderChooser();
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-10", false));
});

test("manual creation passes the general next_date once suggestions land", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], next_date: "2026-07-08", greeting_picks: [] });
  await renderChooser();
  await screen.findByText("The creditor"); // suggestions resolved → nextDate is set
  fireEvent.click(screen.getByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-08", false));
});

test("a greeting pick passes the general next_date when available", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], next_date: "2026-07-08", greeting_picks: [] });
  await renderChooser();
  await screen.findByText("The creditor");
  fireEvent.click(screen.getByText("Reckoning"));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-08", false));
});

test("a card without a date falls back to next_date", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], next_date: "2026-07-08", greeting_picks: [] });
  await renderChooser();
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-08", false));
});

test("mode step gates all fetches and offscreen filters to pcless greetings", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: false },
    { id: "cabal", name: "The Cabal", available: true, reasons: [], unlocked: false, pcless: true },
  ]);
  render(<NewSceneChooser cid="c" afterSid="s1" keySet={true}
                          onClose={() => {}} onCreated={() => {}} />);
  expect(api.availableGreetings).not.toHaveBeenCalled();
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByText(/offscreen \(npcs only\)/i));
  await screen.findByText("The Cabal");
  expect(screen.queryByText("Reckoning")).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", true);
});

test("offscreen manual create flags the scene pcless", async () => {
  const onCreated = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" keySet={true}
                          onClose={() => {}} onCreated={onCreated} />);
  fireEvent.click(await screen.findByText(/offscreen \(npcs only\)/i));
  fireEvent.click(await screen.findByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9"));
  expect(api.createScene).toHaveBeenCalledWith("c", undefined, undefined, true);
});

test("pc mode hides pcless greetings", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: false },
    { id: "cabal", name: "The Cabal", available: true, reasons: [], unlocked: false, pcless: true },
  ]);
  await renderChooser();
  await screen.findByText("Reckoning");
  expect(screen.queryByText("The Cabal")).toBeNull();
});
