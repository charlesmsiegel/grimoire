import { render, screen, fireEvent } from "@testing-library/react";
import { ChangesPanel } from "./ChangesPanel";

vi.mock("../api/client", () => ({ api: { campaignChanges: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => vi.clearAllMocks());

const HARBOR = {
  ref: { kind: "locations", id: "harbor" }, name: "Harbor",
  scene: { id: "s1", title: "The blockade", date: "12 Harvestmoon" },
  fields: [{ field: "body", label: "Harbor — locations",
    diff: [{ op: "equal", text: "A busy port town." },
           { op: "insert", text: "Now blockaded." }] }],
};

test("lists changed records and shows a field diff on select", async () => {
  (api.campaignChanges as any).mockResolvedValue([HARBOR]);
  render(<ChangesPanel cid="c1" />);
  fireEvent.click(await screen.findByRole("button", { name: /Harbor/ }));
  expect(screen.getByText("Now blockaded.")).toBeInTheDocument();
  expect(screen.getByText("Now blockaded.").className).toContain("diff-insert");
  expect(screen.getByText("A busy port town.").className).toContain("diff-equal");
});

test("shows an empty state when nothing has changed", async () => {
  (api.campaignChanges as any).mockResolvedValue([]);
  render(<ChangesPanel cid="c1" />);
  expect(await screen.findByText(/No record changes yet/)).toBeInTheDocument();
});
