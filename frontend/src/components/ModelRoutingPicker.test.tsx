import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ModelRoutingPicker } from "./ModelRoutingPicker";
import { ApiError, api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getGlobalRouting: vi.fn(),
    setGlobalRouting: vi.fn(),
    getCampaignRouting: vi.fn(),
    setCampaignRouting: vi.fn(),
  } };
});

const CONNECTIONS = [
  { id: "big", name: "Big", kind: "openrouter", model: "vendor/opus" },
  { id: "cheap", name: "Cheap", kind: "openrouter", model: "vendor/haiku" },
];

const CATALOG = [
  { key: "scene", label: "Scene turns", hint: "Every streamed turn in play.",
    tasks: ["chat", "retry"] },
  { key: "dossier", label: "Dossier refresh", hint: "One call per present character.",
    tasks: ["dossier"] },
];

function bundle(over: Partial<any> = {}) {
  return {
    scope: "global", catalog: CATALOG, connections: CONNECTIONS,
    active_connection_id: "big",
    routes: { scene: "", dossier: "" },
    effective: { scene: "", dossier: "" },
    provenance: { scene: { scope: "active" }, dossier: { scope: "active" } },
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getGlobalRouting).mockResolvedValue(bundle() as never);
  vi.mocked(api.getCampaignRouting).mockResolvedValue(
    bundle({ scope: "campaign" }) as never);
  vi.mocked(api.setGlobalRouting).mockResolvedValue(bundle() as never);
  vi.mocked(api.setCampaignRouting).mockResolvedValue(bundle({ scope: "campaign" }) as never);
});

test("lists a row per route, each inheriting the active connection", async () => {
  render(<ModelRoutingPicker scope="global" />);

  const scene = await screen.findByLabelText<HTMLSelectElement>("Scene turns");
  expect(scene.value).toBe("");
  // One per row, and both rows inherit -- naming the connection that inheriting
  // actually reaches, which is the question the row is asking.
  expect(screen.getAllByText("— inherit (Big, from the active connection) —"))
    .toHaveLength(2);
  expect(screen.getByLabelText("Dossier refresh")).toBeInTheDocument();
  expect(screen.getByText("One call per present character.")).toBeInTheDocument();
});

test("names the connection an inherited route currently resolves to", async () => {
  vi.mocked(api.getCampaignRouting).mockResolvedValue(bundle({
    scope: "campaign",
    effective: { scene: "cheap", dossier: "" },
    provenance: { scene: { scope: "global" }, dossier: { scope: "active" } },
  }) as never);

  render(<ModelRoutingPicker scope="campaign" cid="c" />);

  // The point of the bundle's `effective`: "inherit" is unanswerable as a
  // choice unless the row says what inheriting gets you.
  expect(await screen.findByText("— inherit (Cheap, from the global default) —"))
    .toBeInTheDocument();
});

test("choosing a connection writes only that route", async () => {
  render(<ModelRoutingPicker scope="global" />);
  const scene = await screen.findByLabelText("Scene turns");

  fireEvent.change(scene, { target: { value: "big" } });

  await waitFor(() => expect(api.setGlobalRouting).toHaveBeenCalledWith({ scene: "big" }));
  expect(api.setGlobalRouting).toHaveBeenCalledTimes(1);
});

test("the campaign scope writes to the campaign", async () => {
  render(<ModelRoutingPicker scope="campaign" cid="run" />);
  const scene = await screen.findByLabelText("Scene turns");

  fireEvent.change(scene, { target: { value: "cheap" } });

  await waitFor(() =>
    expect(api.setCampaignRouting).toHaveBeenCalledWith("run", { scene: "cheap" }));
  expect(api.setGlobalRouting).not.toHaveBeenCalled();
});

test("renders from the bundle the write returned, not from the click", async () => {
  vi.mocked(api.setGlobalRouting).mockResolvedValue(bundle({
    routes: { scene: "cheap", dossier: "" },
    effective: { scene: "cheap", dossier: "" },
    provenance: { scene: { scope: "global" }, dossier: { scope: "active" } },
  }) as never);

  render(<ModelRoutingPicker scope="global" />);
  fireEvent.change(await screen.findByLabelText("Scene turns"), { target: { value: "cheap" } });

  await waitFor(() =>
    expect(screen.getByLabelText<HTMLSelectElement>("Scene turns").value).toBe("cheap"));
});

test("a slow first write cannot overwrite what a later one already showed", async () => {
  // Nothing orders two responses. Without the ticket, the first write's bundle
  // landing second renders a state the store has already moved past -- the
  // second row snapping back to inherit while the store holds the connection
  // the reader picked.
  const slow = bundle({
    routes: { scene: "big", dossier: "" },
    effective: { scene: "big", dossier: "" },
    provenance: { scene: { scope: "global" }, dossier: { scope: "active" } },
  });
  const fast = bundle({
    routes: { scene: "big", dossier: "cheap" },
    effective: { scene: "big", dossier: "cheap" },
    provenance: { scene: { scope: "global" }, dossier: { scope: "global" } },
  });
  let releaseSlow: (b: unknown) => void = () => {};
  vi.mocked(api.setGlobalRouting)
    .mockImplementationOnce(() => new Promise((res) => { releaseSlow = res; }) as never)
    .mockResolvedValueOnce(fast as never);

  render(<ModelRoutingPicker scope="global" />);
  fireEvent.change(await screen.findByLabelText("Scene turns"), { target: { value: "big" } });
  fireEvent.change(screen.getByLabelText("Dossier refresh"), { target: { value: "cheap" } });

  await waitFor(() =>
    expect(screen.getByLabelText<HTMLSelectElement>("Dossier refresh").value).toBe("cheap"));
  releaseSlow(slow);

  await waitFor(() => expect(api.setGlobalRouting).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText<HTMLSelectElement>("Dossier refresh").value).toBe("cheap");
});

test("a refused write shows the reason and restores what the store actually has", async () => {
  // A real refusal, not a bare object: `request` throws `ApiError`, and a
  // component that only reads `.detail` off whatever it caught is how a
  // network failure renders as "[object Object]".
  vi.mocked(api.setGlobalRouting).mockRejectedValue(
    new ApiError(400, "not routable at this scope"));

  render(<ModelRoutingPicker scope="global" />);
  fireEvent.change(await screen.findByLabelText("Scene turns"), { target: { value: "big" } });

  expect(await screen.findByText("not routable at this scope")).toBeInTheDocument();
  // Re-read rather than left showing a choice nothing stored.
  await waitFor(() => expect(api.getGlobalRouting).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText<HTMLSelectElement>("Scene turns").value).toBe("");
});

test("a route naming a connection the list does not have still shows as set", async () => {
  vi.mocked(api.getGlobalRouting).mockResolvedValue(bundle({
    routes: { scene: "since-deleted", dossier: "" },
    effective: { scene: "", dossier: "" },
    provenance: { scene: { scope: "active" }, dossier: { scope: "active" } },
  }) as never);

  render(<ModelRoutingPicker scope="global" />);

  const scene = await screen.findByLabelText<HTMLSelectElement>("Scene turns");
  // Falling back to the blank option would report the route as inherited, which
  // is a different setting from one pointing at something that is gone.
  expect(scene.value).toBe("since-deleted");
});

test("a failed load says so instead of rendering an empty picker", async () => {
  vi.mocked(api.getGlobalRouting).mockRejectedValue(new ApiError(500, "no store"));

  render(<ModelRoutingPicker scope="global" />);

  expect(await screen.findByText("no store")).toBeInTheDocument();
  expect(screen.queryByLabelText("Scene turns")).not.toBeInTheDocument();
});
