import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { api, type ExpressionImportPreview, type ExpressionTarget } from "../../api/client";
import { ExpressionImportDialog } from "./ExpressionImportDialog";

vi.mock("../../api/client", () => ({ api: { previewExpressionPack: vi.fn(), previewExpressionFolder: vi.fn(), publishExpressionPack: vi.fn() } }));
const target: ExpressionTarget = { scope: "world", owner_id: "realm", kind: "characters", actor_id: "mara", version_id: "default" };
const preview: ExpressionImportPreview = { token: "token", expires_at: Date.now() / 1000 + 1800, effective_revision: "revision",
  images: [{ file: "joy.png", outfit: "everyday", expression: "joy", ext: "png", ambiguous: true },
    { file: "joy_1.png", outfit: "everyday", expression: "joy", ext: "png", ambiguous: true }],
  issues: ["Unsupported file: readme.txt"], metadata: { format: 1, outfits: {} } };

it("requires a deliberate selection for alternatives and preserves the preview on conflict", async () => {
  vi.mocked(api.previewExpressionPack).mockResolvedValue(preview);
  vi.mocked(api.publishExpressionPack).mockRejectedValue(new Error("Library changed; preview again"));
  render(<ExpressionImportDialog target={target} outfit="everyday" onImported={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Expression ZIP"), { target: { files: [new File(["zip"], "pack.zip")] } });
  expect(await screen.findByText("Unsupported file: readme.txt")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Import selected sprites" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Select joy_1.png"));
  fireEvent.click(screen.getByRole("button", { name: "Import selected sprites" }));
  await waitFor(() => expect(api.publishExpressionPack).toHaveBeenCalledWith(target, preview, [
    { file: "joy_1.png", outfit: "everyday", expression: "joy", overwrite: false },
  ]));
  expect(await screen.findByRole("alert")).toHaveTextContent("Library changed");
  expect(screen.getByText("joy_1.png")).toBeInTheDocument();
});

it("imports an outfit-only pack after showing its metadata", async () => {
  const empty = { ...preview, images: [], issues: [], metadata: { format: 1 as const,
    default_outfit: "formal", outfits: { formal: { label: "Formal" } } } };
  vi.mocked(api.previewExpressionPack).mockResolvedValue(empty);
  vi.mocked(api.publishExpressionPack).mockResolvedValue({} as never);
  const imported = vi.fn();
  render(<ExpressionImportDialog target={target} outfit="" onImported={imported} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Expression ZIP"), { target: { files: [new File(["zip"], "outfits.zip")] } });
  expect(await screen.findByText(/Formal \(formal\)/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Import outfits" }));
  await waitFor(() => expect(imported).toHaveBeenCalled());
  expect(api.publishExpressionPack).toHaveBeenCalledWith(target, empty, []);
});

it("passes the selected format and outfit when importing a folder", async () => {
  vi.mocked(api.previewExpressionFolder).mockResolvedValue(preview);
  render(<ExpressionImportDialog target={target} outfit="armor" onImported={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Pack format"), { target: { value: "sillytavern" } });
  const file = new File(["sprite"], "neutral.png");
  fireEvent.change(screen.getByLabelText("Expression folder"), { target: { files: [file] } });
  await waitFor(() => expect(api.previewExpressionFolder).toHaveBeenCalledWith(target, [file], "sillytavern", "armor"));
});
