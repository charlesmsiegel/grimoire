import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { PCEditor } from "./PCEditor";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    listPCs: vi.fn(), createPC: vi.fn(), createCampaignPC: vi.fn(),
    deletePC: vi.fn(), actorImageUrl: vi.fn(() => "/portrait"),
  } };
});
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";

function Destination() {
  const location = useLocation();
  return <div>{location.pathname} {String((location.state as { newPC?: boolean } | null)?.newPC)}</div>;
}
function renderIndex(campaign = false, strict = false) {
  const scope = campaign ? { kind: "campaign" as const, id: "saltmarch" }
    : { kind: "world" as const, id: "realm" };
  const base = campaign ? "/campaigns/saltmarch/world/pcs" : "/worlds/realm/pcs";
  const page = <MemoryRouter initialEntries={[base]}><Link to="/elsewhere">Leave PCs</Link><Routes>
    <Route path={base} element={<PCEditor scope={scope} wid="realm"
      recordHref={(id) => `${base}/${encodeURIComponent(id)}`} />} />
    <Route path={`${base}/:pid`} element={<Destination />} />
    <Route path="/elsewhere" element={<div>Elsewhere</div>} />
  </Routes></MemoryRouter>;
  return render(strict ? <StrictMode>{page}</StrictMode> : page);
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listPCs).mockResolvedValue(fixture([{ id: "mara", name: "Mara",
    default_version: "default", has_avatar: false }]));
  vi.mocked(api.createPC).mockResolvedValue(fixture({ pc: "winifred" }));
  vi.mocked(api.createCampaignPC).mockResolvedValue(fixture({ pc: "winifred" }));
});

test("the PC section lists records at their own URLs", async () => {
  renderIndex();
  expect(await screen.findByRole("link", { name: "Mara" })).toHaveAttribute("href", "/worlds/realm/pcs/mara");
  expect(api.listPCs).toHaveBeenCalledWith({ kind: "world", id: "realm" });
});

test("creating a world PC opens its record in edit mode", async () => {
  vi.mocked(api.createPC).mockImplementation(async (world, input) => {
    if (world !== "realm" || input.name !== "Winifred") throw new Error("wrong create scope");
    return fixture({ pc: "winifred" });
  });
  renderIndex();
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  await waitFor(() => expect(screen.getByText("/worlds/realm/pcs/winifred true")).toBeInTheDocument());
});

test("creating a campaign PC keeps campaign scope", async () => {
  vi.mocked(api.createCampaignPC).mockImplementation(async (campaign, input) => {
    if (campaign !== "saltmarch" || input.name !== "Winifred") throw new Error("wrong create scope");
    return fixture({ pc: "winifred" });
  });
  renderIndex(true);
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  await waitFor(() => expect(screen.getByText("/campaigns/saltmarch/world/pcs/winifred true")).toBeInTheDocument());
});

test("a list read failure is visible", async () => {
  vi.mocked(api.listPCs).mockRejectedValueOnce(new Error("unavailable"));
  renderIndex();
  expect(await screen.findByText(/unavailable/i)).toBeInTheDocument();
});


test("a new PC can be cancelled without creating it", async () => {
  renderIndex();
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  expect(await screen.findByText("Select or create a PC.")).toBeInTheDocument();
  expect(api.createPC).not.toHaveBeenCalled();
});

test.each(["success", "refusal"])("a cancelled PC creation cannot reclaim the page: %s", async (outcome) => {
  let finish: (() => void) | undefined;
  vi.mocked(api.createPC).mockImplementation(() => new Promise((resolve, reject) => {
    finish = () => outcome === "success" ? resolve({ pc: "winifred", version: "default" })
      : reject(new Error("creation refused"));
  }));
  renderIndex();
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  if (!finish) throw new Error("creation never started");
  await act(async () => { finish?.(); });
  expect(screen.getByText("Select or create a PC.")).toBeInTheDocument();
  expect(screen.queryByText(/creation refused/)).not.toBeInTheDocument();
});

test("a PC create in flight cannot be submitted twice", async () => {
  vi.mocked(api.createPC).mockReturnValue(new Promise(() => {}));
  renderIndex();
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  const create = screen.getByRole("button", { name: "Create PC" });
  fireEvent.click(create);
  fireEvent.click(create);
  expect(api.createPC).toHaveBeenCalledTimes(1);
  expect(create).toBeDisabled();
});

test("an old creation cannot navigate or release a newer form's busy state", async () => {
  const finish: Array<() => void> = [];
  vi.mocked(api.createPC).mockImplementation((_wid, body) => new Promise((resolve) => {
    finish.push(() => resolve({ pc: body.name.toLowerCase(), version: "default" }));
  }));
  renderIndex();
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  expect(api.createPC).toHaveBeenCalledTimes(2);
  await act(async () => { finish[0]?.(); });
  expect(screen.getByLabelText("PC name")).toHaveValue("Winifred");
  expect(screen.getByRole("button", { name: "Create PC" })).toBeDisabled();
  await act(async () => { finish[1]?.(); });
  expect(screen.getByText("/worlds/realm/pcs/winifred true")).toBeInTheDocument();
});

test("a creation that finishes after leaving the route cannot navigate back", async () => {
  let finish: (() => void) | undefined;
  vi.mocked(api.createPC).mockImplementation(() => new Promise((resolve) => {
    finish = () => resolve({ pc: "winifred", version: "default" });
  }));
  renderIndex();
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  fireEvent.click(screen.getByRole("link", { name: "Leave PCs" }));
  await screen.findByText("Elsewhere");
  if (!finish) throw new Error("creation never started");
  await act(async () => { finish?.(); });
  expect(screen.getByText("Elsewhere")).toBeInTheDocument();
});

test("the current creation still navigates under StrictMode", async () => {
  renderIndex(false, true);
  fireEvent.click(screen.getByRole("link", { name: "+ New PC" }));
  fireEvent.change(screen.getByLabelText("PC name"), { target: { value: "Winifred" } });
  fireEvent.click(screen.getByRole("button", { name: "Create PC" }));
  expect(await screen.findByText("/worlds/realm/pcs/winifred true")).toBeInTheDocument();
});
