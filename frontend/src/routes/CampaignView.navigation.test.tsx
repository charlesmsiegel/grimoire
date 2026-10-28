import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, Link, RouterProvider, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { SceneMeta } from "../api/client";
import { here, Here, installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { RouterRequest } from "../testkit/routerRequest";
import { RunRegistryProvider } from "../runs/RunRegistryProvider";
import { readCounter, TWO_SCENES, transcriptsPerScene } from "../testkit/campaignFixtures";

// The mocks and the per-test defaults live in `src/testkit`, which the review
// suite shares (#378). A `vi.mock` factory is hoisted above every import
// and can close over nothing, so it reaches the harness through a dynamic
// import rather than a module-scope binding.
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));

beforeEach(installCampaignMocks);



function Back() {
  const navigate = useNavigate();
  return <button onClick={() => void navigate(-1)}>go back</button>;
}

test("a scene URL opens that scene, not the most recently updated one", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign("/campaigns/run/scenes/s2");

  await screen.findByText("transcript of s2");
  expect(screen.queryByText("transcript of s1")).toBeNull();
  // and the rest of the scene's context is hydrated, not just its posts
  expect(api.getCast).toHaveBeenCalledWith("run", "s2");
  expect(api.getSceneDatetime).toHaveBeenCalledWith("run", "s2");
});

test("opening a campaign with no scene in the URL redirects to one", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign();

  await screen.findByText("transcript of s1");
  expect(here()).toBe("/campaigns/run/scenes/s1");
});

test("clicking a scene in the rail puts it in the URL", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");

  await openScene(/The Saltmarch Gate/);

  await screen.findByText("transcript of s2");
  expect(here()).toBe("/campaigns/run/scenes/s2");
});

test("a scene id that no longer exists falls back to the first scene", async () => {
  // Scene ids are filenames and change under renames, restamps and repads, so
  // a bookmarked or reloaded id can name nothing. Never render a dead scene —
  // and never leave the dead URL in the history for Back to return to.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/worlds", "/campaigns/run/scenes/003--gone"]}>
      {withPalette(<>
        <Here />
        <Back />
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );

  await screen.findByText("transcript of s1");
  expect(here()).toBe("/campaigns/run/scenes/s1");
  // the fallback never fetched the scene that isn't there
  expect(vi.mocked(api.getScene).mock.calls.every((c) => c[1] !== "003--gone")).toBe(true);

  fireEvent.click(screen.getByText("go back"));
  await waitFor(() => expect(here()).toBe("/worlds"));
});

// A campaign with no scenes resolves to the scenes list, which is the page
// that offers to create one. `ScenesView.test.tsx` owns that case.

test("renaming the active scene carries the URL to its new id, replacing the old", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  render(
    <MemoryRouter initialEntries={["/worlds", "/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Back />
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));

  vi.mocked(api.listScenes).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" }]);
  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const nameInput = screen.getByDisplayValue("Old");
  fireEvent.change(nameInput, { target: { value: "Renamed" } });
  fireEvent.keyDown(nameInput, { key: "Enter" });

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-renamed"));
  // A rename is not a place the reader went, so it must not stack an entry —
  // and the entry it replaces names a scene that no longer exists.
  fireEvent.click(screen.getByText("go back"));
  await waitFor(() => expect(here()).toBe("/worlds"));
});

test.each(["memory", "data"] as const)("renaming a scene that is not first in the rail keeps the reader on it (%s router)", async (host) => {
  // The rename has to move the URL itself. Left to the stale-id fallback, the
  // reader lands on whatever the list sorts FIRST — which is the renamed scene
  // only when there is one scene, and some other scene the moment there are two.
  vi.mocked(api.listScenes).mockResolvedValueOnce(TWO_SCENES).mockResolvedValue([
    TWO_SCENES[0],
    { id: "s2-renamed", title: "Renamed", model: "", created: "", updated: "1", date: "" },
  ]);
  transcriptsPerScene();
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s2-renamed" }));
  // Production uses a data-router host; keep the ordinary MemoryRouter case
  // too, so the same identity contract holds in both harnesses.
  if (host === "data") vi.stubGlobal("Request", RouterRequest);
  const router = host === "data" ? createMemoryRouter([
    { path: "*", element: withPalette(<><Here />{playRoutes()}</>) },
  ], { initialEntries: ["/campaigns/run/scenes/s2"] }) : null;
  const page = router
    ? render(<RunRegistryProvider><RouterProvider router={router} useTransitions={false} /></RunRegistryProvider>)
    : renderCampaign("/campaigns/run/scenes/s2");
  try {
    await screen.findByText("transcript of s2");

    fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
    const box = screen.getByDisplayValue("The Saltmarch Gate");
    fireEvent.change(box, { target: { value: "Renamed" } });
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2-renamed"));
    expect(screen.queryByText("transcript of s1")).toBeNull();
  } finally {
    page.unmount();
    router?.dispose();
    if (host === "data") vi.unstubAllGlobals();
  }
});

test("a scene renamed by the inspector's first date stamp carries the URL too", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));

  vi.mocked(api.listScenes).mockResolvedValue(
    [{ id: "s10", title: "Old", model: "", created: "", updated: "", date: "" }]);
  // the stubbed CastPanel reports a date-stamp rename to s10
  fireEvent.click(screen.getByText("stub-datestamp"));

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s10"));
});

test("deleting the scene on screen moves the URL to what is left", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign("/campaigns/run/scenes/s2");
  await screen.findByText("transcript of s2");

  vi.mocked(api.listScenes).mockResolvedValue([TWO_SCENES[0]]);
  fireEvent.click(screen.getByRole("button", { name: /delete scene/i }));   // s2 is on screen

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));
  await screen.findByText("transcript of s1");
});

test("deleting the last scene drops it from the URL and clears the transcript", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  transcriptsPerScene();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("transcript of s1");

  vi.mocked(api.listScenes).mockResolvedValue([]);
  fireEvent.click(screen.getByRole("button", { name: /delete scene/i }));

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));
  expect(screen.queryByText("transcript of s1")).toBeNull();
});

test("a newly created scene becomes the URL", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");

  vi.mocked(api.listScenes).mockResolvedValue(
    [...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));   // the stubbed chooser creates s9

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s9"));
  await screen.findByText("transcript of s9");
});

test("a scene the URL names but the server cannot read says so", async () => {
  // The list has the row, so this is not the stale-id fallback — the read
  // itself failed. It used to be an unhandled rejection: no banner, and an
  // unreadable transcript rendered as a scene with nothing in it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockRejectedValue(
    Object.assign(new Error("boom"), { detail: "transcript is not valid UTF-8" }));
  renderCampaign();

  expect(await screen.findByText(/transcript is not valid UTF-8/)).toBeInTheDocument();
});

test("a scene list that cannot be read says so", async () => {
  // The resolver waits for this list, and a read that fails installs nothing:
  // the page used to sit with no scene and no rail, and no word of why.
  vi.mocked(api.listScenes).mockRejectedValue(new ApiError(500, "list down"));
  renderCampaign();
  expect(await screen.findByText(/The scene list could not be read: list down/)).toBeInTheDocument();
});

// See above: an empty campaign has no play view, so it has no first send.
// `ScenesView.test.tsx` covers creating the first scene.

test("a scene list arriving after a campaign switch does not strand the view", async () => {
  // A's list is slow; the reader moves to B, whose list lands first. A's late
  // answer must be dropped, not installed: it is labelled with the campaign it
  // was asked about, so installing it leaves the view holding a list nothing
  // will ever ask about again — the resolver waits for the current campaign's
  // rows and B's scene never opens.
  let landA: (v: SceneMeta[]) => void = () => {};
  vi.mocked(api.listScenes).mockImplementation((c) =>
    c === "run" ? new Promise((res) => { landA = res; })
                : Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "", date: "" }]));
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("Run One");

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));
  await screen.findByText("transcript of b1");

  // run's list finally answers, naming a scene "other" does not have
  landA([{ id: "s1", title: "Old", model: "", created: "", updated: "", date: "" }]);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/b1");
  expect(screen.getByText("transcript of b1")).toBeInTheDocument();
  expect(screen.queryByText(/· Old$/)).toBeNull();
});

// --- cross-campaign scoping of the async paths (codex review on #299) -------
// Scene ids repeat freely between campaigns, so every one of these guards has
// to compare the campaign too — an id-only test passes on the wrong scene.

test("a mutation relist landing after a campaign switch does not strand the view", async () => {
  // A rename's relist belongs to the campaign that asked for it. Installed
  // into the campaign the reader moved to, it labels B's rail with A's name —
  // and the resolver, which only acts on a list belonging to the current
  // campaign, then returns early forever: every later rail click and URL
  // change is dead until a reload.
  let landRelist: (v: SceneMeta[]) => void = () => {};
  vi.mocked(api.listScenes).mockImplementation((c) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "", date: "" }])
      : vi.mocked(api.renameScene).mock.calls.length
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  // the reader leaves while run's relist is still open
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));
  await screen.findByText("transcript of b1");

  landRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" }]);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  // the scene list is still B's, and B is still navigable
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const rows = await screen.findAllByRole("option");
  const labels = rows.map((r) => r.querySelector(".palette-label")?.textContent);
  expect(labels).not.toContain("Renamed");
  expect(labels).toContain("B one");
});

test("a rename finishing after a campaign switch does not drag the reader back", async () => {
  // Both campaigns have an "s1" — every campaign numbers its scenes from 001 —
  // so the "is the renamed scene still on screen" test passes on B's s1 unless
  // it compares the campaign as well, and replaces B's URL with A's scene.
  let landRename: (v: Awaited<ReturnType<typeof api.renameScene>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);   // both campaigns: s1
  vi.mocked(api.getScene).mockImplementation(async (c, sid) => fixture({
    meta: {}, messages: [{ role: "assistant", content: `${c}/${sid}` }],
  }));
  vi.mocked(api.renameScene).mockImplementation(() => new Promise((res) => { landRename = res; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("run/s1");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s1"));
  await screen.findByText("other/s1");

  landRename(fixture({ id: "s1-renamed" }));   // run's rename, answered after the move
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/s1");
  expect(screen.getByText("other/s1")).toBeInTheDocument();
});

test("a scene read that fails after the reader moved on does not raise a banner", async () => {
  // The rejection carries no window token, so an unscoped handler blames
  // whatever is on screen when it lands — and the switch that retired the read
  // has already cleared the errors that belonged to it, so the banner sticks.
  let failFirst: (e: unknown) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene)
    .mockImplementationOnce(() => new Promise((_res, rej) => { failFirst = rej; }))
    .mockImplementation(async (_c, sid) => fixture({
      meta: {}, messages: [{ role: "assistant", content: `transcript of ${sid}` }] }));
  renderCampaign();
  await waitFor(() => expect(api.getScene).toHaveBeenCalled());

  await openScene(/The Saltmarch Gate/);
  await screen.findByText("transcript of s2");

  failFirst(Object.assign(new Error("boom"), { detail: "transcript is not valid UTF-8" }));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.queryByText(/transcript is not valid UTF-8/)).toBeNull();
  expect(screen.getByText("transcript of s2")).toBeInTheDocument();
});

test("a turn finishing after a campaign switch does not install its transcript under the new one", async () => {
  // The most expensive version of the id-collision hazard. `runStream`'s
  // finally refreshes the scene the TURN owns, and every guard on that path
  // compares scene ids — which repeat, since each campaign numbers from 001.
  // So campaign A's refresh for "s1" passes while the reader is on B's "s1",
  // and installs A's posts under B's URL; an edit then addresses B's file with
  // A's message indices.
  //
  // The resolver cannot repair this one: `setActiveId` is handed the value it
  // already holds, React bails out of the render, and the effect never re-runs.
  // The refresh has to refuse to apply itself (codex review, P1).
  let finishTurn: () => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);   // both campaigns: s1
  vi.mocked(api.getScene).mockImplementation(async (c, sid) => fixture({
    meta: {}, total: 1, messages: [{ role: "assistant", content: `${c}/${sid}` }],
  }));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) =>
      new Promise<void>((res) => { finishTurn = () => { onEvent({ done: true }); res(); }; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("run/s1");

  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "we begin" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalled());

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s1"));
  await screen.findByText("other/s1");

  finishTurn();   // run's turn lands after the reader moved on
  await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });

  expect(screen.getByText("other/s1")).toBeInTheDocument();
  expect(screen.queryByText("run/s1")).toBeNull();
});

test("a scene created just before a campaign switch does not drag the reader back", async () => {
  // The rows are guarded and the navigation was not: `goToScene` carries the
  // captured campaign, so a switch during the relist sent the reader back to
  // the campaign they had just left.
  let landRelist: (v: SceneMeta[]) => void = () => {};
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "", date: "" }])
      : nth(c) > 1
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));          // creates s9 in "run"
  fireEvent.click(screen.getByText("switch campaign"));    // …then leaves
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));

  landRelist([...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/b1");
});

test("an older relist cannot restore a row a newer one removed", async () => {
  // Nothing serializes scene mutations — `renamesInFlight` blocks turns, not
  // deletions — so a rename's relist can still be open when the reader deletes
  // another row. Response order is not request order, and the older answer
  // landing last used to put the deleted row back. The list now decides which
  // sid the URL may name, so that row is a ghost leading to a 404 read.
  let landRenameRelist: (v: SceneMeta[]) => void = () => {};
  let relists = 0;
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) => {
    if (nth(c) === 1) return Promise.resolve(TWO_SCENES);
    relists += 1;
    // the rename's relist (first) hangs; the delete's (second) answers at once
    if (relists === 1) return new Promise((res) => { landRenameRelist = res; });
    return Promise.resolve([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "2", date: "" }]);
  });
  transcriptsPerScene();
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign("/campaigns/run/scenes/s1");
  await screen.findByText("transcript of s1");

  fireEvent.click(await screen.findByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(relists).toBe(1));

  // s2 is deleted while the rename's relist is still open. Delete belongs to
  // the scene on screen now, so this opens it first.
  await openScene(/The Saltmarch Gate/);
  fireEvent.click(screen.getByRole("button", { name: /delete scene/i }));
  await waitFor(() => expect(
    screen.queryByRole("heading", { name: /The Saltmarch Gate/ })).toBeNull());

  // the rename's older answer lands last, still carrying the deleted scene
  landRenameRelist([
    { id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "2", date: "" },
    TWO_SCENES[1],
  ]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  expect(screen.queryByRole("heading", { name: /The Saltmarch Gate/ })).toBeNull();
});

// Same retirement: there is no first send without a scene to send into.

test("a mutation relist in the old campaign cannot retire the new campaign's list", async () => {
  // The stranding the sequence guard exists to prevent, reachable THROUGH it:
  // a rename started in A resolves — and issues its relist — only after B's
  // mount read is already in flight. A single global counter hands A's later
  // request the newer number, so B's answer is retired by it while A's own is
  // refused for being the wrong campaign. Nothing installs, `sceneListCid`
  // never becomes B, and the resolver is disabled until reload.
  let landRenamePut: (v: Awaited<ReturnType<typeof api.renameScene>>) => void = () => {};
  let landRenameRelist: (v: SceneMeta[]) => void = () => {};
  let landMountB: (v: SceneMeta[]) => void = () => {};
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) => {
    if (c === "other") return new Promise((res) => { landMountB = res; });
    if (nth(c) > 1) return new Promise((res) => { landRenameRelist = res; });
    return Promise.resolve(ONE_SCENE);
  });
  transcriptsPerScene();
  // the PUT hangs, so the handler that resumes still carries run's `cid`
  vi.mocked(api.renameScene).mockImplementation(() => new Promise((res) => { landRenamePut = res; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  // started in run, while run is still the campaign on screen
  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "Renamed"));

  // the reader leaves; B's mount read is issued and still open
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("other"));

  // only NOW does run's rename land, issuing its relist after B's read
  landRenamePut(fixture({ id: "s1-renamed" }));
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });
  landRenameRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  // B's list finally answers — it must still install, and B still be navigable
  landMountB([{ id: "b1", title: "B one", model: "", created: "", updated: "", date: "" }]);
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));
  await screen.findByText("transcript of b1");
});
