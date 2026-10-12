import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CampaignPlayDecisions } from "./CampaignPlayDecisions";

vi.mock("../api/client", () => ({
  api: { getCampaignPlayDecisions: vi.fn(), setCampaignPlayDecisions: vi.fn() },
}));
import { api } from "../api/client";

const inherited = {
  speaker_sampling: "", turn_plan: "", play_tool: "",
  effective: { speaker_sampling: "on", turn_plan: "off", play_tool: "off" },
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });

test("each switch says what Inherit comes to", async () => {
  (api.getCampaignPlayDecisions as any).mockResolvedValue(inherited);
  render(<CampaignPlayDecisions cid="saltmarch" />);
  expect(await screen.findByText(/^Currently on \(following the global setting\)\. No extra call/))
    .toBeInTheDocument();
  expect(screen.getAllByText(/^Currently off \(following the global setting\)\./)).toHaveLength(2);
  // The switch's text is the disclosure (§3.6, §3.7): what it costs, and where its input goes.
  expect(screen.getByText(/One extra call before every character's reply/)).toBeInTheDocument();
  expect(screen.getByText(/card summary, which goes to that model's provider/)).toBeInTheDocument();
  expect(screen.getByLabelText("Turn decision")).toHaveValue("");
});

test("choosing a value writes only that switch and shows the answer", async () => {
  (api.getCampaignPlayDecisions as any).mockResolvedValue(inherited);
  (api.setCampaignPlayDecisions as any).mockResolvedValue({
    ...inherited, turn_plan: "plan", effective: { ...inherited.effective, turn_plan: "plan" } });
  render(<CampaignPlayDecisions cid="saltmarch" />);
  const select = await screen.findByLabelText("Turn decision");
  await waitFor(() => expect(select).not.toBeDisabled());
  fireEvent.change(select, { target: { value: "plan" } });
  await waitFor(() => expect(api.setCampaignPlayDecisions)
    .toHaveBeenCalledWith("saltmarch", { turn_plan: "plan" }));
  expect(await screen.findByText(/^Currently plan\. One extra call/)).toBeInTheDocument();
});

test("a failed read says why", async () => {
  (api.getCampaignPlayDecisions as any).mockRejectedValue(new Error("campaign not found"));
  render(<CampaignPlayDecisions cid="gone" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("campaign not found");
});
