import { render, screen } from "@testing-library/react";
import { api } from "../../api/client";
import { ControlsReadout } from "./ControlsReadout";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { previewControls: vi.fn() } };
});

beforeEach(() => {
  vi.clearAllMocks();
});

test("ControlsReadout renders the server's states", async () => {
  vi.mocked(api.previewControls).mockResolvedValue({
    requested: {}, effective: {},
    controls: {
      temperature: { state: "supported", wire: "temperature", why: "", source: "catalog" },
      reasoning_effort: { state: "translated", wire: "thinking",
                          why: "sent as an adaptive thinking budget", source: "catalog" },
      min_p: { state: "unsupported", wire: null,
               why: "this provider takes no min-p", source: "adapter" },
      top_k: { state: "unknown", wire: "top_k",
               why: "no catalog is cached", source: "unknown" },
    },
  });

  render(<ControlsReadout presetId="warm" provider="or" model="vendor/m" />);

  const list = await screen.findByRole("list", { name: "Controls" });
  // It asks; it does not work anything out for itself.
  expect(api.previewControls).toHaveBeenCalledWith(
    { preset_id: "warm", provider: "or", model: "vendor/m" });
  // Only what is not sent as written is listed.
  expect(list.querySelectorAll("li")).toHaveLength(3);
  expect(screen.queryByText(/temperature/i)).toBeNull();
  const reasoning = screen.getByText(/Reasoning effort/).closest("li")!;
  expect(reasoning).toHaveTextContent("translated");
  expect(reasoning).toHaveTextContent("thinking");
  expect(reasoning).toHaveTextContent("sent as an adaptive thinking budget");
  const minP = screen.getByText(/Min p/).closest("li")!;
  expect(minP).toHaveTextContent("unsupported");
  expect(minP).toHaveTextContent("not sent");
  expect(minP).toHaveTextContent("this provider takes no min-p");
  const topK = screen.getByText(/Top k/).closest("li")!;
  expect(topK).toHaveTextContent("unknown");
  expect(topK).toHaveTextContent("top_k");
});

test("says so when every control is sent as written", async () => {
  vi.mocked(api.previewControls).mockResolvedValue({
    requested: {}, effective: {},
    controls: { temperature: { state: "supported", wire: "temperature", why: "",
                               source: "catalog" } },
  });
  render(<ControlsReadout presetId="warm" provider="or" model="vendor/m" />);
  expect(await screen.findByText(/every control is sent as written/i)).toBeInTheDocument();
});

test("asks nothing without a provider", () => {
  const { container } = render(<ControlsReadout presetId="warm" provider="" model="" />);
  expect(api.previewControls).not.toHaveBeenCalled();
  expect(container).toBeEmptyDOMElement();
});

test("a native decision reads as one line: nothing is sent", async () => {
  const na = { state: "n/a" as const, wire: "", why: "a native decision takes no sampling",
               source: "adapter" as const };
  vi.mocked(api.previewControls).mockResolvedValue({
    requested: { temperature: 0.8 }, effective: {},
    controls: { temperature: na, top_k: na, reasoning_effort: na },
  });
  render(<ControlsReadout presetId="warm" provider="or" model="vendor/decider"
                          operation="decide" />);
  expect(await screen.findByText("Not sent: a native decision takes no sampling."))
    .toBeInTheDocument();
  // It asks about a decision, and lists no control one by one.
  expect(api.previewControls).toHaveBeenCalledWith(
    { preset_id: "warm", provider: "or", model: "vendor/decider", operation: "decide" });
  expect(screen.queryByRole("list", { name: "Controls" })).toBeNull();
});
