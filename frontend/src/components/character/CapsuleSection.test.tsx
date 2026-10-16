import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, test, expect, beforeEach } from "vitest";
import { CapsuleSection } from "./CapsuleSection";
import { api } from "../../api/client";
import type { Card } from "../../api/types";

// The real module with its api object swapped for mocks: every call this
// component makes is one the tests can count.
vi.mock("../../api/client", async () => ({
  ...(await vi.importActual<typeof import("../../api/client")>("../../api/client")),
  api: {
    getCampaignCharacterTagline: vi.fn(),
    setCampaignCharacterTagline: vi.fn(),
    generateCampaignCapsule: vi.fn(),
    updateVersion: vi.fn(),
  },
}));

beforeEach(() => vi.clearAllMocks());

function card(tags?: string[]): Card {
  return { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara", tags } };
}

function mount(opts: { tags?: string[]; onSaved?: () => void; onError?: (e: unknown) => void } = {}) {
  return render(
    <CapsuleSection campaignId="run" cid="mara" vid="default" card={card(opts.tags)}
                    onSaved={opts.onSaved ?? vi.fn()} onError={opts.onError ?? vi.fn()} />,
  );
}

test("shows the stored capsule and the card's tags once read", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "A gate-warden." });
  mount({ tags: ["loyal", "wary"] });
  expect(await screen.findByText("A gate-warden.")).toBeTruthy();
  expect(screen.getByText("Tags: loyal, wary")).toBeTruthy();
  expect(api.getCampaignCharacterTagline).toHaveBeenCalledWith("run", "mara");
});

test("Generate fills the draft and writes nothing until Save", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.generateCampaignCapsule as any).mockResolvedValue({
    capsule: "A gate-warden who keeps every promise.", tags: ["loyal", "wary"],
  });
  mount();
  await screen.findByText("No capsule yet.");

  fireEvent.click(screen.getByRole("button", { name: "Generate" }));

  expect(await screen.findByDisplayValue("A gate-warden who keeps every promise.")).toBeTruthy();
  expect(screen.getByDisplayValue("loyal, wary")).toBeTruthy();
  expect(api.generateCampaignCapsule).toHaveBeenCalledWith("run", "mara");
  expect(api.setCampaignCharacterTagline).not.toHaveBeenCalled();
  expect(api.updateVersion).not.toHaveBeenCalled();
});

test("Save writes the capsule and, when they changed, the tags onto the card", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Old." });
  (api.setCampaignCharacterTagline as any).mockResolvedValue({ ok: true });
  (api.updateVersion as any).mockResolvedValue({ ok: true });
  const onSaved = vi.fn();
  mount({ tags: ["loyal"], onSaved });
  await screen.findByText("Old.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Capsule"), { target: { value: "New." } });
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "loyal, Iron Gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  expect(api.setCampaignCharacterTagline).toHaveBeenCalledWith("run", "mara", "New.");
  expect(api.updateVersion).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "default",
    expect.objectContaining({ data: expect.objectContaining({ tags: ["loyal", "Iron Gate"] }) }),
  );
});

test("Save leaves the card alone when only the capsule changed", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Old." });
  (api.setCampaignCharacterTagline as any).mockResolvedValue({ ok: true });
  mount({ tags: ["loyal"] });
  await screen.findByText("Old.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Capsule"), { target: { value: "New." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(api.setCampaignCharacterTagline).toHaveBeenCalled());
  expect(api.updateVersion).not.toHaveBeenCalled();
});

test("Cancel discards the draft and restores what was stored", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Kept." });
  mount({ tags: ["loyal"] });
  await screen.findByText("Kept.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Capsule"), { target: { value: "Discarded." } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  expect(await screen.findByText("Kept.")).toBeTruthy();
  expect(api.setCampaignCharacterTagline).not.toHaveBeenCalled();
});

test("a failed read disables Generate and Edit, so nothing can overwrite the unseen capsule", async () => {
  (api.getCampaignCharacterTagline as any).mockRejectedValue(new Error("offline"));
  mount();
  expect(await screen.findByText(/Could not read the capsule/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /Edit|Write one/ })).toBeDisabled();
});

test("an empty generated capsule is reported and installs no draft", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.generateCampaignCapsule as any).mockResolvedValue({ capsule: "  ", tags: [] });
  const onError = vi.fn();
  mount({ onError });
  await screen.findByText("No capsule yet.");

  fireEvent.click(screen.getByRole("button", { name: "Generate" }));

  await waitFor(() => expect(onError).toHaveBeenCalled());
  expect(screen.queryByLabelText("Capsule")).toBeNull();
});
