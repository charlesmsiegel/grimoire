import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ScenarioImport } from "./ScenarioImport";

vi.mock("../api/client", () => ({
  api: { scenarioParse: vi.fn(), scenarioParseUrl: vi.fn(), scenarioImport: vi.fn() },
}));
import { api } from "../api/client";

const PROPOSAL = {
  characters: [
    { name: "Mara", description: "Tends the tide-gate.", personality: "Watchful." },
    { name: "Winifred", description: "Keeps the books.", personality: "Dry." },
  ],
  entries: [
    { name: "The Tide-Gate", keys: ["gate"], body: "Iron and barnacle.", category: "locations" },
  ],
  greetings: [
    { name: "Saltmarch", body: "Mara is waiting.", character: "Mara", present: ["Mara"] },
    { name: "Saltmarch (alt 1)", body: "The square is empty.", character: "", present: [] },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.scenarioParse as any).mockResolvedValue(structuredClone(PROPOSAL));
  (api.scenarioParseUrl as any).mockResolvedValue(structuredClone(PROPOSAL));
  (api.scenarioImport as any).mockResolvedValue({
    characters: [{ name: "Mara", id: "mara", version: "default", created: true }],
    entries: [{ kind: "locations", id: "the-tide-gate" }],
    greetings: [{ name: "Saltmarch", id: "saltmarch" }],
    art: { total: 1, localized: 1, skipped: 0, failed: 0, capped: false },
  });
});

function pickFile() {
  fireEvent.change(screen.getByLabelText(/scenario card file/i),
                   { target: { files: [new File(["{}"], "card.json")] } });
}

async function readCard() {
  render(<ScenarioImport wid="w" />);
  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /read card/i }));
  await screen.findByLabelText("character name 0");
}

test("reading a card shows the cast, the entries and the openers it proposes", async () => {
  await readCard();
  expect(screen.getByDisplayValue("Winifred")).toBeTruthy();
  expect(screen.getByDisplayValue("The Tide-Gate")).toBeTruthy();
  expect(screen.getByDisplayValue("Saltmarch (alt 1)")).toBeTruthy();
  expect(screen.getByRole("button", { name: /import 5 records/i })).toBeTruthy();
});

test("importing sends the edited proposal, not the one that was proposed", async () => {
  await readCard();
  fireEvent.change(screen.getByLabelText("character name 0"), { target: { value: "Mara Vel" } });
  fireEvent.change(screen.getByLabelText("entry category 0"), { target: { value: "lore" } });
  fireEvent.click(screen.getByRole("button", { name: /import 5 records/i }));
  await waitFor(() => expect((api.scenarioImport as any).mock.calls.length).toBe(1));
  const [, sent, art] = (api.scenarioImport as any).mock.calls[0];
  expect(sent.characters[0].name).toBe("Mara Vel");
  expect(sent.entries[0].category).toBe("lore");
  expect(art).toBe(true);
  await screen.findByText(/imported 1 character/i);
});

test("a row the reviewer unchecks is left out of the import", async () => {
  await readCard();
  fireEvent.click(screen.getByLabelText("keep character 1"));
  fireEvent.click(screen.getByLabelText("keep greeting 1"));
  fireEvent.click(screen.getByRole("button", { name: /import 3 records/i }));
  await waitFor(() => expect((api.scenarioImport as any).mock.calls.length).toBe(1));
  const sent = (api.scenarioImport as any).mock.calls[0][1];
  expect(sent.characters.map((c: any) => c.name)).toEqual(["Mara"]);
  expect(sent.greetings.map((g: any) => g.name)).toEqual(["Saltmarch"]);
});

test("renaming a cast member carries every opener that named them", async () => {
  // The openers reference the cast by NAME, so a rename that does not travel
  // leaves them pointing at somebody who will never be created — and the
  // backend drops an unresolvable name silently, so the opener would arrive
  // with no cast at all.
  await readCard();
  fireEvent.change(screen.getByLabelText("character name 0"), { target: { value: "Mara Vel" } });
  fireEvent.click(screen.getByRole("button", { name: /import 5 records/i }));
  await waitFor(() => expect((api.scenarioImport as any).mock.calls.length).toBe(1));
  const sent = (api.scenarioImport as any).mock.calls[0][1];
  expect(sent.greetings[0].character).toBe("Mara Vel");
  expect(sent.greetings[0].present).toEqual(["Mara Vel"]);
});

test("repointing an opener keeps the rest of its cast present", async () => {
  (api.scenarioParse as any).mockResolvedValue({
    ...structuredClone(PROPOSAL),
    greetings: [{ name: "Saltmarch", body: "Mara and Winifred.",
                  character: "Mara", present: ["Mara", "Winifred"] }],
  });
  await readCard();
  fireEvent.change(screen.getByLabelText("greeting character 0"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: /import 4 records/i }));
  await waitFor(() => expect((api.scenarioImport as any).mock.calls.length).toBe(1));
  const sent = (api.scenarioImport as any).mock.calls[0][1];
  // Winifred leads now; Mara is still in the scene rather than written out of it.
  expect(sent.greetings[0].present).toEqual(["Winifred", "Mara"]);
});

test("an opener pointing at a cast member that is not being imported says so", async () => {
  await readCard();
  fireEvent.click(screen.getByLabelText("keep character 0"));   // drop Mara
  expect(screen.getByText(/mara is not being imported/i)).toBeTruthy();
});

test("an opener can be pointed at any proposed cast member", async () => {
  await readCard();
  fireEvent.change(screen.getByLabelText("greeting character 1"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: /import 5 records/i }));
  await waitFor(() => expect((api.scenarioImport as any).mock.calls.length).toBe(1));
  const sent = (api.scenarioImport as any).mock.calls[0][1];
  expect(sent.greetings[1].character).toBe("Winifred");
  expect(sent.greetings[1].present).toEqual(["Winifred"]);
});

test("art can be turned off before importing", async () => {
  await readCard();
  fireEvent.click(screen.getByLabelText(/download the openers' images/i));
  fireEvent.click(screen.getByRole("button", { name: /import 5 records/i }));
  await waitFor(() => expect((api.scenarioImport as any).mock.calls.length).toBe(1));
  expect((api.scenarioImport as any).mock.calls[0][2]).toBe(false);
});

test("a URL is read through the URL route", async () => {
  render(<ScenarioImport wid="w" />);
  fireEvent.change(screen.getByLabelText(/card url/i),
                   { target: { value: "https://chub.ai/characters/creator/saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /read url/i }));
  await screen.findByLabelText("character name 0");
  expect((api.scenarioParseUrl as any).mock.calls[0][1])
    .toBe("https://chub.ai/characters/creator/saltmarch");
  expect((api.scenarioParse as any).mock.calls.length).toBe(0);
});

test("a failed read shows the error and proposes nothing", async () => {
  (api.scenarioParse as any).mockRejectedValue({ detail: "No LLM connection selected" });
  render(<ScenarioImport wid="w" />);
  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /read card/i }));
  await screen.findByText(/no llm connection selected/i);
  expect(screen.queryByRole("button", { name: /import/i })).toBeNull();
});

test("a card with no cast still offers its entries and openers", async () => {
  (api.scenarioParse as any).mockResolvedValue({ ...structuredClone(PROPOSAL), characters: [] });
  render(<ScenarioImport wid="w" />);
  pickFile();
  fireEvent.click(screen.getByRole("button", { name: /read card/i }));
  await screen.findByText(/no cast proposed/i);
  expect(screen.getByRole("button", { name: /import 3 records/i })).toBeTruthy();
});
