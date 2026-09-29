import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResponseTargetsPicker } from "./ResponseTargetsPicker";
import { api } from "../api/client";

vi.mock("../api/client");

const bundle = {
  response_opening_words: "", response_opening_paragraphs: "",
  response_continuation_words: "120", response_continuation_paragraphs: "",
  style_id: "", effective: {
    opening: { words: 400, paragraphs: 3 }, continuation: { words: 120, paragraphs: 2 },
    style_id: "", reply_words: 120, blocks: 1, paragraphs: 2, speakers: 1, blocks_per_speaker: 1,
  },
  provenance: {
    "opening.words": { scope: "global" }, "opening.paragraphs": { scope: "default" },
    "continuation.words": { scope: "scene" }, "continuation.paragraphs": { scope: "default" },
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSceneResponse).mockResolvedValue(bundle as any);
  vi.mocked(api.getGlobalResponse).mockResolvedValue(bundle as any);
  vi.mocked(api.listStyles).mockResolvedValue([]);
  vi.mocked(api.setSceneResponse).mockResolvedValue({ ok: true });
  vi.mocked(api.setGlobalResponse).mockResolvedValue({ ok: true });
});

it("shows phase targets with their sources and inherited placeholders", async () => {
  render(<ResponseTargetsPicker scope="scene" cid="run" sid="s1" />);
  expect(await screen.findByLabelText("Opening words")).toHaveAttribute("placeholder", "400");
  expect(screen.getByText("400 from global")).toBeInTheDocument();
  expect(screen.getByLabelText("Continuation words")).toHaveValue(120);
  expect(screen.getByText("120 from scene")).toBeInTheDocument();
  expect(screen.queryByLabelText("Response preset")).not.toBeInTheDocument();
});

it("saves only target fields and style", async () => {
  render(<ResponseTargetsPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.clear(await screen.findByLabelText("Opening words"));
  await userEvent.type(screen.getByLabelText("Opening words"), "500");
  await userEvent.click(screen.getByRole("button", { name: "Save response targets" }));
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ response_opening_words: "500", response_continuation_words: "120" })));
  expect(vi.mocked(api.setSceneResponse).mock.calls[0][2]).not.toHaveProperty("response_preset");
});

it("uses global response endpoints at global scope", async () => {
  render(<ResponseTargetsPicker scope="global" />);
  await screen.findByLabelText("Opening words");
  await userEvent.click(screen.getByRole("button", { name: "Save response targets" }));
  await waitFor(() => expect(api.setGlobalResponse).toHaveBeenCalled());
  expect(api.getSceneResponse).not.toHaveBeenCalled();
});
