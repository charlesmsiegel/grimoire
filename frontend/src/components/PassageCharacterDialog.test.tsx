import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PassageCharacterDialog } from "./PassageCharacterDialog";
import { api } from "../api/client";

vi.mock("../api/client", async () => ({ ...await vi.importActual("../api/client"),
  api: { listCharacters: vi.fn(), draftPassageCharacter: vi.fn(), savePassageCharacter: vi.fn() } }));
const source = 'Mara said, "Wait."';
beforeEach(() => {
  vi.mocked(api.listCharacters).mockResolvedValue([{ id: "mara", name: "Mara" }] as Awaited<ReturnType<typeof api.listCharacters>>);
  vi.mocked(api.savePassageCharacter).mockResolvedValue({ character: "mara", name: "Mara", version: "default" });
  vi.mocked(api.draftPassageCharacter).mockResolvedValue({ name: "Mara", description: "Watches the door.",
    mes_example: "<START>\n{{char}}: Wait.", quotes: ["Wait."] });
});
function show() {
  const onSaved = vi.fn();
  render(<PassageCharacterDialog cid="realm" sid="scene" responseId="response-a" sourceText={source}
    onClose={vi.fn()} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("Character name"), { target: { value: "Mara" } });
  return onSaved;
}
describe("reviewed passage character", () => {
  it("can save a sparse card without calling a model", async () => {
    const saved = show();
    fireEvent.click(screen.getByRole("button", { name: "Create reviewed character" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(api.draftPassageCharacter).not.toHaveBeenCalled();
    expect(api.savePassageCharacter).toHaveBeenCalledWith("realm", "scene", "response-a", {
      name: "Mara", passage: source, source_text: source, description: "", mes_example: "" });
  });
  it("reviews a draft, attaches to an existing identity, and retains exact source", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Draft description and examples" }));
    await waitFor(() => expect(screen.getByLabelText("Character description")).toHaveValue("Watches the door."));
    fireEvent.change(screen.getByLabelText("Save character to"), { target: { value: "mara" } });
    fireEvent.click(screen.getByRole("button", { name: "Attach reviewed evidence" }));
    await waitFor(() => expect(api.savePassageCharacter).toHaveBeenCalledWith("realm", "scene", "response-a",
      expect.objectContaining({ source_text: source, existing_ref: "characters:mara", mes_example: "<START>\n{{char}}: Wait." })));
  });
  it("changing the reviewed identity discards the other person's generated draft", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Draft description and examples" }));
    await waitFor(() => expect(screen.getByLabelText("Character description")).toHaveValue("Watches the door."));
    fireEvent.change(screen.getByLabelText("Character name"), { target: { value: "Winifred" } });
    expect(screen.getByLabelText("Character description")).toHaveValue("");
    expect(screen.queryByLabelText("Include evidenced dialogue examples")).not.toBeInTheDocument();
  });
});
