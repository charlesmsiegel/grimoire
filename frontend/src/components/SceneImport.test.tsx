import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneImport } from "./SceneImport";
import type { SceneImportDraft } from "../api/client";

// Partial mock, the shape SceneConfirmForm's suite uses: the module's pure
// helpers stay real, so a test can never pass against a reimplementation.
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    sceneImportParse: vi.fn(), sceneImport: vi.fn(), listEntities: vi.fn(),
  } };
});
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel, disabled }: any) =>
    <input aria-label={ariaLabel} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />,
}));
import { api } from "../api/client";

const DRAFT: SceneImportDraft = {
  title: "The Long Quay",
  date: "2026-01-02",
  location: "the-quay",
  pcless: false,
  messages: [
    { role: "user", content: "I walk the quay looking for Mara." },
    { role: "assistant", speaker: "Mara", content: '"You found me."' },
  ],
  turn_sizes: [1],
  cast: [{ label: "Mara", kind: "characters", id: "mara", name: "Mara", role: "npc" }],
  unmatched: [],
  warnings: [],
};

function draft(over: Partial<SceneImportDraft> = {}): SceneImportDraft {
  return { ...DRAFT, ...over };
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listEntities as any).mockResolvedValue([{ id: "the-quay", name: "The Quay" }]);
  (api.sceneImportParse as any).mockResolvedValue(DRAFT);
  (api.sceneImport as any).mockResolvedValue({ id: "001--the-long-quay", messages: 2, cast: 1 });
});

function readFile(text = "**You:** hi\n") {
  fireEvent.change(screen.getByLabelText(/transcript file/i),
                   { target: { files: [new File([text], "scene.md")] } });
  fireEvent.click(screen.getByRole("button", { name: /read file/i }));
}

test("reading a file shows the review form and writes nothing", async () => {
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();

  await screen.findByDisplayValue("The Long Quay");
  expect(screen.getByLabelText("Scene date")).toHaveValue("2026-01-02");
  expect(screen.getByLabelText("Location")).toHaveValue("the-quay");
  expect(screen.getByLabelText("Seat Mara")).toBeChecked();
  expect(screen.getByText(/2 posts will be imported/i)).toBeInTheDocument();
  // The opening post is shown, so "unchanged" is something the reviewer can
  // check rather than take on trust.
  expect(screen.getByText(/I walk the quay looking for Mara\./)).toBeInTheDocument();
  expect(api.sceneImport).not.toHaveBeenCalled();
});

test("import sends the reviewed draft, not the parsed one", async () => {
  const onImported = vi.fn();
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={onImported} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");

  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "A Better Title" } });
  fireEvent.click(screen.getByLabelText("Seat Mara"));          // decline the seat
  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));

  await waitFor(() => expect(api.sceneImport).toHaveBeenCalled());
  const [cid, body] = (api.sceneImport as any).mock.calls[0];
  expect(cid).toBe("c");
  expect(body.title).toBe("A Better Title");
  expect(body.cast).toEqual([]);
  expect(body.messages).toEqual(DRAFT.messages);                 // the transcript is untouched
  expect(body.turn_sizes).toEqual([1]);                          // and its reply boundaries ride along
  await waitFor(() => expect(onImported).toHaveBeenCalledWith("001--the-long-quay"));
});

test("every warning the parse raised is shown before anything is imported", async () => {
  (api.sceneImportParse as any).mockResolvedValue(draft({
    date: "", warnings: ["“2 January 2026” is not a date this campaign's calendar can read."],
  }));
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByText(/not a date this campaign's calendar can read/i);
  expect(api.sceneImport).not.toHaveBeenCalled();
});

test("a speaker who names nobody here is named, and does not block the import", async () => {
  (api.sceneImportParse as any).mockResolvedValue(draft({ cast: [], unmatched: ["Mara"] }));
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByText(/no one in this campaign is called/i);
  expect(screen.getByText(/“Mara”/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));
  await waitFor(() => expect(api.sceneImport).toHaveBeenCalled());
});

test("a transcript with player posts cannot be imported as offscreen", async () => {
  // `post_chat` never stores a user turn for a pcless scene, so importing one
  // would create a scene the play loop could not have produced -- with the
  // player's own posts attributed to nobody, since the seat is stripped too.
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");     // DRAFT has a role:"user" post
  expect(screen.getByLabelText("Offscreen scene")).toBeDisabled();
  expect(screen.getByText(/this transcript has player posts/i)).toBeInTheDocument();
});

test("a player cannot be seated once the scene is marked offscreen", async () => {
  (api.sceneImportParse as any).mockResolvedValue(draft({
    // No player posts, so the offscreen box is available to tick at all.
    messages: [{ role: "assistant", speaker: "Seraphine", content: "She waits." }],
    cast: [{ label: "Seraphine", kind: "pcs", id: "seraphine", name: "Seraphine", role: "player" }],
  }));
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByLabelText("Seat Seraphine");
  expect(screen.getByLabelText("Seat Seraphine")).toBeChecked();

  fireEvent.click(screen.getByLabelText("Offscreen scene"));
  expect(screen.getByLabelText("Seat Seraphine")).toBeDisabled();
  expect(screen.getByLabelText("Seat Seraphine")).not.toBeChecked();

  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));
  await waitFor(() => expect(api.sceneImport).toHaveBeenCalled());
  expect((api.sceneImport as any).mock.calls[0][1].cast).toEqual([]);
});

test("a location the campaign no longer has never reaches the commit", async () => {
  // Asserting the <select>'s value would prove nothing: a select whose value
  // matches no option reports "" whatever the state behind it says, so that
  // assertion passes just as happily while the bad id is still on its way to
  // the server. The committed body is the thing under test.
  (api.listEntities as any).mockResolvedValue([]);
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");
  await screen.findByText(/no location .the-quay./i);   // and the reviewer is told

  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));
  await waitFor(() => expect(api.sceneImport).toHaveBeenCalled());
  expect((api.sceneImport as any).mock.calls[0][1].location).toBe("");
});

test("the import pane is usable again after a successful import", async () => {
  // `busy` and the orchestrator's gate move together. Left apart, the success
  // path cleared one and not the other, and the pane stayed fully disabled --
  // masked today only because the chooser unmounts it in the same batch.
  const onWriting = vi.fn();
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}}
                      onImported={() => {}} onWriting={onWriting} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");
  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));

  await waitFor(() => expect(onWriting).toHaveBeenLastCalledWith(false));
  expect(screen.getByRole("button", { name: /import scene/i })).toBeEnabled();
  expect(screen.getByLabelText("Title")).toBeEnabled();
});

test("a parse failure keeps the file picker and says why", async () => {
  (api.sceneImportParse as any).mockRejectedValue({ detail: "could not parse: no blocks" });
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByText(/could not parse: no blocks/i);
  expect(screen.getByLabelText(/transcript file/i)).toBeInTheDocument();
});

test("a connection that failed mid-import does not offer a blind retry", async () => {
  // The scene may be written and only the reply lost. `commit` is
  // all-or-nothing against failures it can see; it cannot undo a reply that
  // never arrived, and retrying a long transcript would make a second copy.
  (api.sceneImport as any).mockRejectedValue(new TypeError("Failed to fetch"));
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");
  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));

  await screen.findByText(/may have landed/i);
  expect(screen.getByRole("button", { name: /import scene/i })).toBeDisabled();
});

test("a server refusal keeps the retry available", async () => {
  // The other half: an answered request means nothing landed, so the reviewer
  // can fix the draft and press Import again.
  (api.sceneImport as any).mockRejectedValue(
    Object.assign(new Error("actor not found"), { status: 404, detail: "actor not found" }));
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={() => {}} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");
  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));

  await screen.findByText(/actor not found/i);
  expect(screen.getByRole("button", { name: /import scene/i })).toBeEnabled();
});

test("a failed import says why and does not report a scene", async () => {
  const onImported = vi.fn();
  (api.sceneImport as any).mockRejectedValue(
    Object.assign(new Error("actor not found"), { status: 404, detail: "actor not found" }));
  render(<SceneImport cid="c" onBack={() => {}} onCancel={() => {}} onImported={onImported} />);
  readFile();
  await screen.findByDisplayValue("The Long Quay");
  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));
  await screen.findByText(/actor not found/i);
  expect(onImported).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /import scene/i })).toBeEnabled();
});
