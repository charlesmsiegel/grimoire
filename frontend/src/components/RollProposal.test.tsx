import { render, screen, fireEvent } from "@testing-library/react";
import { vi, test, expect } from "vitest";
import { RollProposal } from "./RollProposal";
import type { ProposalRecord } from "../api/client";

const REC: ProposalRecord = {
  id: "pr-000001", status: "pending",
  payload: { id: "pr-000001", check: "brawl", check_label: "Vigor + Brawl",
             actor: "characters:mara", actor_label: "Mara", difficulty: 6,
             available: { "characters:mara": [["brawl", "Vigor + Brawl"], ["perception", "Wits + Occult"]] },
             problems: [] },
  resolution: null,
};

test("accept sends ids and numbers", () => {
  const onResolve = vi.fn();
  render(<RollProposal record={REC} busy={false} onResolve={onResolve} />);
  fireEvent.click(screen.getByText("Roll it"));
  expect(onResolve).toHaveBeenCalledWith(
    { proposal: "pr-000001", action: "accept", check: "brawl",
      actor: "characters:mara", difficulty: 6, modifier: 0 });
});

test("modify swaps check and difficulty", () => {
  const onResolve = vi.fn();
  render(<RollProposal record={REC} busy={false} onResolve={onResolve} />);
  fireEvent.click(screen.getByText("Modify"));
  fireEvent.change(screen.getByLabelText("Check"), { target: { value: "perception" } });
  fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "8" } });
  fireEvent.click(screen.getByText("Roll it"));
  expect(onResolve).toHaveBeenCalledWith(
    { proposal: "pr-000001", action: "accept", check: "perception",
      actor: "characters:mara", difficulty: 8, modifier: 0 });
});

const REC_PROBLEMS: ProposalRecord = {
  id: "pr-000002", status: "pending",
  payload: { id: "pr-000002", check: "brawl", check_label: "Vigor + Brawl",
             difficulty: 6,
             available: {
               "characters:mara": [["brawl", "Vigor + Brawl"], ["perception", "Wits + Occult"]],
               "characters:seraphine": [["stealth", "Wits + Stealth"]],
             },
             problems: ['Multiple actors named "Mara" — pick one.'] },
  resolution: null,
};

test("problems auto-open modify with actor select", () => {
  const onResolve = vi.fn();
  render(<RollProposal record={REC_PROBLEMS} busy={false} onResolve={onResolve} />);
  expect(screen.getByLabelText("Actor")).toBeTruthy();
  expect(screen.getByLabelText("Check")).toBeTruthy();
  expect(screen.getByText('Multiple actors named "Mara" — pick one.')).toBeTruthy();
});

test("decline and resolved-continue", () => {
  const onResolve = vi.fn();
  const { unmount } = render(<RollProposal record={REC} busy={false} onResolve={onResolve} />);
  fireEvent.click(screen.getByText("Decline"));
  expect(onResolve).toHaveBeenCalledWith({ proposal: "pr-000001", action: "decline" });
  unmount();

  const RESOLVED: ProposalRecord = { ...REC, status: "resolved" };
  const onResolve2 = vi.fn();
  render(<RollProposal record={RESOLVED} busy={false} onResolve={onResolve2} />);
  expect(screen.queryByText("Roll it")).toBeNull();
  expect(screen.queryByText("Modify")).toBeNull();
  expect(screen.queryByText("Decline")).toBeNull();
  fireEvent.click(screen.getByText("Continue narration"));
  expect(onResolve2).toHaveBeenCalledWith({ proposal: "pr-000001", action: "accept" });
});

// backend sends difficulty: null (not omitted) when the LLM's roll fence
// leaves difficulty out — resolve_check falls back to the module default,
// so the frontend must not coerce that to 0 and must not render a diff chip.
const REC_NULL_DIFFICULTY: ProposalRecord = {
  id: "pr-000003", status: "pending",
  payload: { id: "pr-000003", check: "brawl", check_label: "Vigor + Brawl",
             actor: "characters:mara", actor_label: "Mara", difficulty: null,
             available: { "characters:mara": [["brawl", "Vigor + Brawl"], ["perception", "Wits + Occult"]] },
             problems: [] },
  resolution: null,
};

test("null difficulty: no diff chip, and accept omits difficulty", () => {
  const onResolve = vi.fn();
  render(<RollProposal record={REC_NULL_DIFFICULTY} busy={false} onResolve={onResolve} />);
  expect(screen.queryByText(/diff/)).toBeNull();
  fireEvent.click(screen.getByText("Roll it"));
  expect(onResolve).toHaveBeenCalledWith(
    { proposal: "pr-000003", action: "accept", check: "brawl",
      actor: "characters:mara", modifier: 0 });
  const body = onResolve.mock.calls[0][0];
  expect(body).not.toHaveProperty("difficulty");
});
