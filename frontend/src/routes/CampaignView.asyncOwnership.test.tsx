import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import { api, type Actor, type SceneMeta } from "../api/client";
import { Here, here, installCampaignMocks, playRoutes, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";

// Real CastPanel, SceneDateField and dossier controls: the delayed API replies
// must exercise the public callback handoffs, including callbacks held at await.
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));

beforeEach(installCampaignMocks);

function gate<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function row(id: string, title: string): SceneMeta {
  return { id, title, model: "", created: "", updated: "", date: "" };
}

function renderDestination(cid: string, sid: string) {
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Here />
      <Link to={`/campaigns/${cid}/scenes/${sid}`}>open destination</Link>
      {playRoutes()}
    </>)}
  </MemoryRouter>);
}

function keepDraft() {
  fireEvent.change(screen.getByPlaceholderText("Speak your intent…"),
    { target: { value: "Destination draft" } });
  fireEvent.change(screen.getByLabelText("Next reply words"), { target: { value: "120" } });
}

function expectDestination(cid: string, sid: string) {
  expect(here()).toBe(`/campaigns/${cid}/scenes/${sid}`);
  expect(screen.getByText("Destination scene")).toBeInTheDocument();
  expect(screen.getByText(`Destination transcript ${cid}/${sid}`)).toBeInTheDocument();
  expect(screen.getByPlaceholderText("Speak your intent…")).toHaveValue("Destination draft");
  expect(screen.getByLabelText("Next reply words")).toHaveValue(120);
  // An unknown/mismatched loaded owner disables post actions even if old prose
  // remains visible. Keep a real post action enabled on the destination too.
  expect(screen.getAllByTitle("Edit message")[0]).toBeEnabled();
}

test.each([
  { cid: "run", sid: "s2" },
  { cid: "other", sid: "s1" },
])("a delayed setup date rename keeps destination $cid/$sid", async ({ cid, sid }) => {
  const date = gate<Awaited<ReturnType<typeof api.setSceneDatetime>>>();
  let renamed = false;
  vi.mocked(api.setSceneDatetime).mockReturnValue(date.promise);
  vi.mocked(api.getCalendarMonths).mockResolvedValue(fixture({
    months: [{ key: "10", name: "October", days: 31 }],
  }));
  vi.mocked(api.listScenes).mockImplementation(async (campaign) => {
    const source = row(renamed ? "s1-dated" : "s1", "Source scene");
    return campaign === "run"
      ? cid === "run" ? [source, row(sid, "Destination scene")] : [source]
      : [row(sid, "Destination scene")];
  });
  vi.mocked(api.getScene).mockImplementation(async (campaign, scene) => fixture({
    meta: { id: scene },
    messages: campaign === "run" && scene === "s1" ? [] : [
      { role: "assistant", content: `Destination transcript ${campaign}/${scene}` },
    ],
  }));
  renderDestination(cid, sid);
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  await screen.findByRole("option", { name: "October" });
  fireEvent.change(screen.getByLabelText("Scene date month"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Scene date day"), { target: { value: "27" } });
  fireEvent.click(screen.getByRole("button", { name: "Set date" }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("run", "s1", "2026-10-27"));

  fireEvent.click(screen.getByText("open destination"));
  await screen.findByText(`Destination transcript ${cid}/${sid}`);
  keepDraft();
  vi.mocked(api.listScenes).mockClear();
  vi.mocked(api.getScene).mockClear();
  renamed = true;
  await act(async () => { date.resolve(fixture({ id: "s1-dated" })); });

  expectDestination(cid, sid);
  expect(vi.mocked(api.listScenes).mock.calls.map(([campaign]) => campaign)).toEqual(["run"]);
  expect(api.getScene).not.toHaveBeenCalled();
});

const SOURCE_ACTOR: Actor = { kind: "characters", id: "source", name: "Source actor", role: "npc" };
const DESTINATION_ACTOR: Actor = { kind: "characters", id: "destination", name: "Destination actor", role: "npc" };

function dossierScenes(sid: string) {
  vi.mocked(api.listScenes).mockImplementation(async (cid) =>
    [row(cid === "run" ? "s1" : sid, cid === "run" ? "Source scene" : "Destination scene")]);
  vi.mocked(api.getScene).mockImplementation(async (cid, scene) => fixture({
    meta: { id: scene }, messages: [{ role: "assistant", content:
      cid === "run" ? "Source transcript" : `Destination transcript ${cid}/${scene}` }],
  }));
  vi.mocked(api.getCast).mockImplementation(async (cid) => [cid === "run" ? SOURCE_ACTOR : DESTINATION_ACTOR]);
  vi.mocked(api.getCasefile).mockImplementation(async (cid, scene) => fixture({
    ...(cid === "run" ? SOURCE_ACTOR : DESTINATION_ACTOR), version: "v1", avatar_v: null,
    scenes: [{ id: scene, title: cid === "run" ? "Source scene" : "Destination scene" }],
    last_seen: "", standing: "", knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  }));
}

async function openDossier(name: string) {
  fireEvent.click(await within(screen.getByRole("complementary")).findByText(name));
  await screen.findByRole("heading", { name });
}

function clearReadCalls() {
  vi.mocked(api.getScene).mockClear();
  vi.mocked(api.getCast).mockClear();
  vi.mocked(api.getSceneDatetime).mockClear();
  vi.mocked(api.sceneBriefing).mockClear();
  vi.mocked(api.getSceneGroup).mockClear();
}

function expectNoCompletionReads() {
  expect(api.getScene).not.toHaveBeenCalled();
  expect(api.getCast).not.toHaveBeenCalled();
  expect(api.getSceneDatetime).not.toHaveBeenCalled();
  expect(api.sceneBriefing).not.toHaveBeenCalled();
  expect(api.getSceneGroup).not.toHaveBeenCalled();
}

test.each([
  { sid: "s2", outcome: "success" }, { sid: "s1", outcome: "success" },
  { sid: "s2", outcome: "failure" }, { sid: "s1", outcome: "failure" },
])("delayed dossier removal $outcome leaves other/$sid intact", async ({ sid, outcome }) => {
  dossierScenes(sid);
  const removal = gate<Awaited<ReturnType<typeof api.removeFromCast>>>();
  vi.mocked(api.removeFromCast).mockReturnValue(removal.promise);
  renderDestination("other", sid);
  await screen.findByText("Source transcript");
  await openDossier("Source actor");
  fireEvent.click(screen.getByRole("button", { name: "Remove from scene" }));
  expect(api.removeFromCast).toHaveBeenCalledWith("run", "s1", "characters", "source");

  fireEvent.click(screen.getByText("open destination"));
  await screen.findByText(`Destination transcript other/${sid}`);
  await openDossier("Destination actor");
  keepDraft();
  clearReadCalls();
  await act(async () => {
    if (outcome === "success") removal.resolve({ ok: true });
    else removal.reject(new Error("source removal refused"));
  });

  expectDestination("other", sid);
  expect(screen.getByRole("heading", { name: "Destination actor" })).toBeInTheDocument();
  expect(screen.queryByText(/source removal refused/)).not.toBeInTheDocument();
  expectNoCompletionReads();
  fireEvent.click(screen.getByRole("button", { name: "‹ All cast" }));
  const column = within(screen.getByRole("complementary"));
  expect(column.getByText("Destination actor")).toBeInTheDocument();
  expect(column.queryByText("Source actor")).not.toBeInTheDocument();
});

test("a removal's already-started cast read cannot overwrite the destination cast", async () => {
  dossierScenes("s1");
  renderDestination("other", "s1");
  await screen.findByText("Source transcript");
  await openDossier("Source actor");
  const cast = gate<Actor[]>();
  // Only the removal's explicit first read is held; the transcript refresh's
  // independent cast read is allowed to settle while its scene is still open.
  vi.mocked(api.getCast).mockReturnValueOnce(cast.promise);
  fireEvent.click(screen.getByRole("button", { name: "Remove from scene" }));
  await waitFor(() => expect(api.removeFromCast).toHaveBeenCalled());
  await waitFor(() => expect(screen.queryByRole("heading", { name: "Source actor" })).not.toBeInTheDocument());
  fireEvent.click(screen.getByText("open destination"));
  await screen.findByText("Destination transcript other/s1");
  await within(screen.getByRole("complementary")).findByText("Destination actor");
  keepDraft();
  await act(async () => { cast.resolve([SOURCE_ACTOR]); });
  expectDestination("other", "s1");
  const column = within(screen.getByRole("complementary"));
  expect(column.getByText("Destination actor")).toBeInTheDocument();
  expect(column.queryByText("Source actor")).not.toBeInTheDocument();
});
