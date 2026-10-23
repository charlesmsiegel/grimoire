import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { api, type NativeActorTarget } from "../../api/client";
import { NativeActorPack } from "./NativeActorPack";

vi.mock("../../api/client", () => ({ api: {
  nativeActorExportUrl: () => "/bundle", previewNativeActor: vi.fn(), publishNativeActor: vi.fn(),
} }));
const target: NativeActorTarget = { scope: "campaign", owner_id: "saltmarch", kind: "pcs", actor_id: "winifred" };
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.previewNativeActor).mockResolvedValue({ token: "token", expires_at: 9999999999, kind: "pcs",
    versions: ["default", "formal"], has_dependency: true, can_bind: false });
});

it("imports the selected PC destination with an explicit detached choice", async () => {
  vi.mocked(api.publishNativeActor).mockResolvedValue({ ...target, actor_id: "winifred-copy" });
  render(<NativeActorPack target={target} />);
  fireEvent.click(screen.getByText("Full character / PC pack"));
  fireEvent.change(screen.getByLabelText("Native character ZIP"), { target: { files: [new File(["zip"], "actor.zip")] } });
  expect(await screen.findByText("Versions: default, formal")).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "Keep inheritance from the matching world character" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Import full actor pack" }));
  await waitFor(() => expect(api.publishNativeActor).toHaveBeenCalledWith({ ...target, actor_id: "winifred-copy" }, "token", "detached"));
  expect(await screen.findByRole("status")).toHaveTextContent("Imported winifred-copy");
});

it("invalidates the preview when the destination changes", async () => {
  render(<NativeActorPack target={target} />);
  fireEvent.click(screen.getByText("Full character / PC pack"));
  fireEvent.change(screen.getByLabelText("Native character ZIP"), { target: { files: [new File(["zip"], "actor.zip")] } });
  expect(await screen.findByRole("button", { name: "Import full actor pack" })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Destination actor ID"), { target: { value: "other" } });
  expect(screen.queryByRole("button", { name: "Import full actor pack" })).not.toBeInTheDocument();
  expect(api.publishNativeActor).not.toHaveBeenCalled();
});
