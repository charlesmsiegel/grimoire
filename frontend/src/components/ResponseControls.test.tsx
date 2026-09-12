import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResponseControls } from "./ResponseControls";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: { getResponse: vi.fn() } }));

function show(extra = {}) {
  const actions = { onDelete: vi.fn(), onReroll: vi.fn(), onActivate: vi.fn(), onReplay: vi.fn() };
  render(<ResponseControls cid="realm" sid="scene" responseId="response-a" canReroll={true}
    {...actions} {...extra} />);
  return actions;
}

describe("individual response controls", () => {
  it("deletes only the selected response and rerolls with its own steer", () => {
    const actions = show();
    fireEvent.click(screen.getByRole("button", { name: "Delete response" }));
    expect(actions.onDelete).toHaveBeenCalledWith("response-a");
    fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "More restrained" } });
    fireEvent.click(screen.getByRole("button", { name: "Reroll response" }));
    expect(actions.onReroll).toHaveBeenCalledWith("response-a", "More restrained", { connection_id: "", model: "" });
  });
  it("shows retained incomplete and changed-context states, disables all writes while busy", () => {
    show({ disabled: true, status: "incomplete", contextChanged: true });
    expect(screen.getByText("Incomplete response")).toBeInTheDocument();
    expect(screen.getByText("Earlier context changed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay from here" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete response" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reroll response" })).toBeDisabled();
  });
  it("offers explicit replay when a historical snapshot is unavailable", () => {
    const actions = show({ canReroll: false });
    expect(screen.queryByRole("button", { name: "Reroll response" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Replay from here" }));
    expect(actions.onReplay).toHaveBeenCalledOnce();
  });
  it("activates the stable variant id from the selected response", async () => {
    vi.mocked(api.getResponse).mockResolvedValue({ id: "response-a", active_variant: "v1",
      variants: [{ id: "v1", content: "First", status: "complete" },
        { id: "v2", content: "Second", status: "complete" }, { id: "v3", content: "Partial", status: "incomplete" }] } as Awaited<ReturnType<typeof api.getResponse>>);
    const actions = show();
    fireEvent.click(screen.getByRole("button", { name: "Response variants" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Use variant 2" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Use variant 3 (incomplete)" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Use variant 2" }));
    expect(actions.onActivate).toHaveBeenCalledWith("response-a", "v2");
  });
});
