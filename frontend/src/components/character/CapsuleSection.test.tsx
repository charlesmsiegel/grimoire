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
  },
}));

beforeEach(() => vi.clearAllMocks());

function card(tags?: string[]): Card {
  return { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara", tags } };
}

function mount(opts: {
  tags?: string[]; onSaveTags?: (tags: string[]) => Promise<boolean>; onError?: (e: unknown) => void;
} = {}) {
  return render(
    <CapsuleSection campaignId="run" cid="mara" vid="default" card={card(opts.tags)}
                    onSaveTags={opts.onSaveTags ?? vi.fn().mockResolvedValue(true)}
                    onError={opts.onError ?? vi.fn()} />,
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
  const onSaveTags = vi.fn().mockResolvedValue(true);
  mount({ onSaveTags });
  await screen.findByText("No capsule yet.");

  fireEvent.click(screen.getByRole("button", { name: "Generate" }));

  expect(await screen.findByDisplayValue("A gate-warden who keeps every promise.")).toBeTruthy();
  expect(screen.getByDisplayValue("loyal, wary")).toBeTruthy();
  expect(api.generateCampaignCapsule).toHaveBeenCalledWith("run", "mara", "default");
  expect(api.setCampaignCharacterTagline).not.toHaveBeenCalled();
  expect(onSaveTags).not.toHaveBeenCalled();
});

test("Save writes the capsule and, when they changed, the tags onto the card", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Old." });
  (api.setCampaignCharacterTagline as any).mockImplementation(async (_c: string, _h: string, t: string) => ({ ok: true, tagline: t }));
  const onSaveTags = vi.fn().mockResolvedValue(true);
  mount({ tags: ["loyal"], onSaveTags });
  await screen.findByText("Old.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Capsule"), { target: { value: "New." } });
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "loyal, Iron Gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(onSaveTags).toHaveBeenCalledWith(["loyal", "Iron Gate"]));
  expect(api.setCampaignCharacterTagline).toHaveBeenCalledWith("run", "mara", "New.");
});

test("Save leaves the card alone when only the capsule changed", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Old." });
  (api.setCampaignCharacterTagline as any).mockImplementation(async (_c: string, _h: string, t: string) => ({ ok: true, tagline: t }));
  const onSaveTags = vi.fn().mockResolvedValue(true);
  mount({ tags: ["loyal"], onSaveTags });
  await screen.findByText("Old.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Capsule"), { target: { value: "New." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(api.setCampaignCharacterTagline).toHaveBeenCalled());
  expect(onSaveTags).not.toHaveBeenCalled();
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

test("Edit is disabled while a draft is being generated, so a late result cannot reopen a discarded form", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Kept." });
  (api.generateCampaignCapsule as any).mockReturnValue(new Promise(() => {}));
  mount();
  await screen.findByText("Kept.");

  fireEvent.click(screen.getByRole("button", { name: "Generate" }));

  expect(await screen.findByRole("button", { name: "Generating…" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
});

test("a draft with no tags line keeps the card's tags rather than clearing them", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.generateCampaignCapsule as any).mockResolvedValue({ capsule: "A gate-warden.", tags: [] });
  const onSaveTags = vi.fn().mockResolvedValue(true);
  mount({ tags: ["loyal", "wary"], onSaveTags });
  await screen.findByText("No capsule yet.");

  fireEvent.click(screen.getByRole("button", { name: "Generate" }));

  expect(await screen.findByDisplayValue("loyal, wary")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setCampaignCharacterTagline).toHaveBeenCalled());
  expect(onSaveTags).not.toHaveBeenCalled();
});

test("a refused tags save leaves the draft open rather than reporting it written", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Old." });
  (api.setCampaignCharacterTagline as any).mockImplementation(async (_c: string, _h: string, t: string) => ({ ok: true, tagline: t }));
  const onSaveTags = vi.fn().mockResolvedValue(false);
  mount({ tags: ["loyal"], onSaveTags });
  await screen.findByText("Old.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "loyal, Iron Gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(onSaveTags).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  expect(screen.getByLabelText("Tags")).toBeTruthy();
});

test("a blank save shows the sentence the campaign now answers, not the blank", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Campaign's own." });
  (api.setCampaignCharacterTagline as any).mockResolvedValue({ ok: true, tagline: "The library's sentence." });
  mount({ tags: [] });
  await screen.findByText("Campaign's own.");

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Capsule"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

  expect(await screen.findByText("The library's sentence.")).toBeTruthy();
  expect(screen.queryByText("No capsule yet.")).toBeNull();
});

test("an error from a draft discarded by a version switch is not reported", async () => {
  (api.getCampaignCharacterTagline as any).mockResolvedValue({ tagline: "Kept." });
  let fail: (e: unknown) => void = () => {};
  (api.generateCampaignCapsule as any).mockReturnValue(new Promise((_, reject) => { fail = reject; }));
  const onError = vi.fn();
  const { unmount } = mount({ onError });
  await screen.findByText("Kept.");

  fireEvent.click(screen.getByRole("button", { name: "Generate" }));
  unmount();
  fail(new Error("provider failed"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(onError).not.toHaveBeenCalled();
});
