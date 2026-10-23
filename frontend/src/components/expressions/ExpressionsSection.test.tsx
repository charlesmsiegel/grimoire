import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api, type ExpressionCollection, type ExpressionTarget } from "../../api/client";
import { ExpressionsSection } from "./ExpressionsSection";

vi.mock("../../api/client", () => ({ api: {
  getExpressions: vi.fn(), mutateExpressions: vi.fn(), uploadExpression: vi.fn(),
  expressionImageUrl: vi.fn(() => "/sprite.png"), expressionExportUrl: vi.fn(() => "/export"),
  nativeActorExportUrl: vi.fn(() => "/bundle"),
} }));

const target: ExpressionTarget = { scope: "world", owner_id: "realm", kind: "characters", actor_id: "mara", version_id: "default" };
const collection: ExpressionCollection = { format: 1, revision: null, effective_revision: "one", default_outfit: "armor",
  standard_labels: ["neutral", "anger"], hidden_outfits: [], hidden_expressions: { armor: ["joy"] }, warnings: [],
  outfits: { armor: { label: "Armor", clothing_aliases: [], fallback_expression: "neutral", source: "inherited",
    presentation: { scale: 1, offset_x: 0, offset_y: 0, anchor: "bottom-center" },
    expressions: { neutral: { image_id: "image", available: true, token: "token", source: "inherited", revision: "r" } } } } };

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.getExpressions).mockResolvedValue(structuredClone(collection)); });

it("shows sparse slots and reveals editing only when requested", async () => {
  render(<ExpressionsSection target={target} />);
  expect(await screen.findByRole("button", { name: "Preview neutral" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Upload anger")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit expressions" }));
  expect(screen.getByLabelText("Upload anger")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Restore joy" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Hide neutral" })).toBeInTheDocument();
});

it("shows configuration warnings alongside usable sprites", async () => {
  vi.mocked(api.getExpressions).mockResolvedValue({ ...collection, warnings: ["Default outfit formal is unavailable."] });
  render(<ExpressionsSection target={target} />);
  expect(await screen.findByText("Default outfit formal is unavailable.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Preview neutral" })).toBeEnabled();
});

it("renames the display label while preserving the outfit ID", async () => {
  vi.mocked(api.mutateExpressions).mockResolvedValue(collection);
  render(<ExpressionsSection target={target} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit expressions" }));
  fireEvent.change(screen.getByLabelText("Outfit name"), { target: { value: "Battle armor" } });
  fireEvent.click(screen.getByRole("button", { name: "Save outfit" }));
  await waitFor(() => expect(api.mutateExpressions).toHaveBeenCalledWith(target, "one", [expect.objectContaining({
    op: "patch-outfit", outfit: "armor", metadata: expect.objectContaining({ label: "Battle armor" }),
  })]));
});

it("does not install a late character upload into the newly selected PC", async () => {
  let finish!: (result: ExpressionCollection) => void;
  vi.mocked(api.uploadExpression).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const { rerender } = render(<ExpressionsSection target={target} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit expressions" }));
  fireEvent.change(screen.getByLabelText("Upload anger"), { target: { files: [new File(["png"], "anger.png", { type: "image/png" })] } });
  const pc: ExpressionTarget = { ...target, kind: "pcs", actor_id: "winifred" };
  vi.mocked(api.getExpressions).mockResolvedValue({ ...collection, outfits: {}, default_outfit: null });
  rerender(<ExpressionsSection target={pc} />);
  expect(await screen.findByText("No outfits yet.")).toBeInTheDocument();
  finish(collection);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Preview neutral" })).not.toBeInTheDocument());
  expect(api.uploadExpression).toHaveBeenCalledWith(target, "one", "armor", "anger", expect.any(File));
});
