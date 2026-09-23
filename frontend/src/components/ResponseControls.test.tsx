import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResponseControls } from "./ResponseControls";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  api: { getResponse: vi.fn(), listConnections: vi.fn(), getConfig: vi.fn() },
}));

function show(extra = {}, { open = true } = {}) {
  const actions = { onDelete: vi.fn(), onReroll: vi.fn(), onActivate: vi.fn(), onReplay: vi.fn() };
  render(<ResponseControls cid="realm" sid="scene" responseId="response-a" canReroll={true}
    {...actions} {...extra} />);
  // The actions live behind the disclosure, and are built only once it opens.
  if (open) fireEvent.click(screen.getByText("Response actions"));
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
  it("builds the actions only once the disclosure is opened, and keeps a steer across closing it", () => {
    // One of these sits under every response in a scene, nearly all of them
    // shut for good, so a shut one carries no body at all.
    show({ status: "incomplete" }, { open: false });
    expect(screen.getByText("Incomplete response")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete response" })).toBeNull();
    const summary = screen.getByText("Response actions");
    fireEvent.click(summary);
    fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "Quieter" } });
    fireEvent.click(summary);
    expect(screen.queryByLabelText("Response steer")).toBeNull();
    fireEvent.click(summary);
    expect(screen.getByLabelText("Response steer")).toHaveValue("Quieter");
  });
  it("shuts the route disclosure with the body, so a reopen reads nothing it does not show", async () => {
    // The route picker reads the connections and the config as it mounts. The
    // body is rebuilt on every reopen, and its route disclosure with it, shut:
    // a picker still mounted inside that shut disclosure would read both again
    // for a control nobody can see.
    vi.mocked(api.listConnections).mockResolvedValue([]);
    vi.mocked(api.getConfig).mockResolvedValue({ active_connection: null } as never);
    show();
    fireEvent.click(screen.getByText("Model for this reroll"));
    await waitFor(() => expect(api.listConnections).toHaveBeenCalledTimes(1));
    const summary = screen.getByText("Response actions");
    fireEvent.click(summary);
    fireEvent.click(summary);
    await screen.findByText("Model for this reroll");
    expect(screen.getByText("Model for this reroll").closest("details")).not.toHaveAttribute("open");
    expect(api.listConnections).toHaveBeenCalledTimes(1);
    expect(api.getConfig).toHaveBeenCalledTimes(1);
  });
  it("activates the stable variant id from the selected response", async () => {
    vi.mocked(api.getResponse).mockResolvedValue({ id: "response-a", active_variant: "v1",
      variants: [{ id: "v1", content: "First", status: "complete" },
        { id: "v2", content: "Second", status: "complete" }, { id: "v3", content: "Partial", status: "incomplete", issue: "invalid_handoff" }] } as Awaited<ReturnType<typeof api.getResponse>>);
    const actions = show();
    fireEvent.click(screen.getByRole("button", { name: "Response variants" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Use variant 2" })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Use variant 3 (incomplete)" })).toBeDisabled();
    expect(screen.getByText("Response issue: invalid_handoff")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use variant 2" }));
    expect(actions.onActivate).toHaveBeenCalledWith("response-a", "v2");
  });
});
