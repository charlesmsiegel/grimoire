import { renderWorld, renderCampaign, editField, fieldBlock, lastLocation } from "../testkit/characterPage";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";

test("a failed greeting read does not claim there are zero playable greetings", async () => {
  vi.mocked(api.listGreetings).mockRejectedValue(new Error("Greetings unavailable"));
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: "Greetings" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Greetings unavailable");
  expect(screen.queryByText(/No playable greetings/)).toBeNull();
});

test("campaign Greetings counts and displays the effective playable records", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([{
    id: "tide-watch", name: "Tide Watch", character: "mara", version: "default", present: ["mara", "seraphine"],
  }]));
  await renderCampaign();
  fireEvent.click(await screen.findByRole("tab", { name: "Greetings 1" }));
  expect(screen.getByRole("link", { name: "Tide Watch" })).toHaveAttribute("href", "/campaigns/run/world/greetings/tide-watch");
  expect(screen.queryByText("You are early.")).toBeNull();
});

test("card greeting import sources remain editable on Card", async () => {
  await renderWorld();
  await screen.findByText("You are early.");
  expect(screen.getByText("Rain on the shutters.")).toBeTruthy();
});

test("the greetings tab counts and links world greetings featuring the character", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue([
    {
      id: "tide-watch", name: "Tide Watch", character: "mara", version: "default",
      present: ["mara", "seraphine"], requires_tags: [], predecessor_join: "all",
      location: "gate", phase: "", sequence: null, optional: false,
    },
    {
      id: "mara-alone", name: "Mara Alone", character: "mara", version: "default",
      present: ["mara"], requires_tags: [], predecessor_join: "all",
      location: "gate", phase: "", sequence: null, optional: false,
    },
  ]);

  await renderWorld();
  const tab = await screen.findByRole("tab", { name: "Greetings 1" });
  fireEvent.click(tab);

  expect(screen.getByRole("link", { name: "Tide Watch" }))
    .toHaveAttribute("href", "/worlds/realm/greetings/tide-watch");
  expect(screen.queryByRole("link", { name: "Mara Alone" })).toBeNull();
  expect(screen.queryByText("You are early.")).toBeNull();
  expect(screen.queryByText("Rain on the shutters.")).toBeNull();
  expect(screen.queryByRole("tab", { name: "Card" })).toBeTruthy();
  fireEvent.click(screen.getByRole("link", { name: "Tide Watch" }));
  await waitFor(() => expect(lastLocation)
    .toBe("/worlds/realm/greetings/tide-watch"));
});

test("a card with openings but no playable records explains import", async () => {
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: /Greetings/ }));
  expect(screen.getByRole("tab", { name: "Greetings 0" })).toBeTruthy();
  await screen.findByText(/No playable greetings/i);
});

test.each([
  { scope: "world" as const, renderPage: renderWorld, owner: "realm" },
  { scope: "campaign" as const, renderPage: renderCampaign, owner: "run" },
])("$scope greetings display headings and rendered bodies with a rule between entries", async ({ scope, renderPage, owner }) => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "gate", name: "At the Gate", character: "seraphine", version: "default", present: ["seraphine"] },
    { id: "watch", name: "Tide Watch", character: "seraphine", version: "default", present: ["seraphine"] },
    { id: "veiled", name: "Behind the Veil", character: "seraphine", version: "veiled", present: ["seraphine"] },
  ]));
  vi.mocked(api.readGreeting).mockImplementation(async (_scope, id) => fixture({
    body: id === "gate" ? "The **tide** is rising.\n\n![Saltmarch](https://example.test/map.png)" : "Rain on the shutters.",
  }));
  await renderPage();
  expect(api.readGreeting).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("tab", { name: "Greetings 2" }));
  const first = await screen.findByRole("heading", { name: "At the Gate", level: 3 });
  const second = screen.getByRole("heading", { name: "Tide Watch", level: 3 });
  expect(within(first).getByRole("link")).toHaveAttribute("href",
    scope === "world" ? "/worlds/realm/greetings/gate" : "/campaigns/run/world/greetings/gate");
  const text = screen.getByText("tide", { selector: "strong" });
  expect(screen.getByRole("img", { name: "Saltmarch" })).toHaveAttribute("src", "https://example.test/map.png");
  expect(screen.getByText("Rain on the shutters.")).toBeTruthy();
  const separator = screen.getByRole("separator");
  expect(first.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(text.compareDocumentPosition(separator) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(separator.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(api.readGreeting).toHaveBeenCalledTimes(2);
  expect(api.readGreeting).toHaveBeenCalledWith({ kind: scope, id: owner }, "gate");
  expect(api.readGreeting).toHaveBeenCalledWith({ kind: scope, id: owner }, "watch");
});

test("a failed greeting body leaves other greetings readable", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "gate", name: "At the Gate", character: "seraphine", version: "default", present: ["seraphine"] },
    { id: "watch", name: "Tide Watch", character: "seraphine", version: "default", present: ["seraphine"] },
  ]));
  vi.mocked(api.readGreeting).mockImplementation(async (_scope, id) => {
    if (id === "gate") throw new Error("Greeting text unavailable");
    return fixture({ body: "The watch begins." });
  });
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: "Greetings 2" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Greeting text unavailable");
  expect(screen.getByRole("heading", { name: "At the Gate" })).toBeTruthy();
  expect(screen.getByText("The watch begins.")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Greetings 2" })).toBeTruthy();
});

test("an empty greeting body finishes loading", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "gate", name: "At the Gate", character: "seraphine", version: "default", present: ["seraphine"] },
  ]));
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: "Greetings 1" }));
  await screen.findByRole("heading", { name: "At the Gate" });
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("a late greeting body cannot appear after switching versions", async () => {
  let finish!: (value: Awaited<ReturnType<typeof api.readGreeting>>) => void;
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "gate", name: "At the Gate", character: "seraphine", version: "default", present: ["seraphine"] },
    { id: "veiled", name: "Behind the Veil", character: "seraphine", version: "veiled", present: ["seraphine"] },
  ]));
  vi.mocked(api.readGreeting).mockImplementation((_scope, id) => id === "gate"
    ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(fixture({ body: "The veil lifts." })));
  await renderWorld();
  fireEvent.click(screen.getByRole("tab", { name: "Greetings 1" }));
  expect(await screen.findByRole("status")).toHaveTextContent(/Loading/);
  fireEvent.click(screen.getByRole("button", { name: "veiled" }));
  await screen.findByText("The veil lifts.");
  await act(async () => { finish(fixture({ body: "Retired greeting text." })); });
  expect(screen.queryByText("Retired greeting text.")).toBeNull();
  expect(screen.queryByRole("heading", { name: "At the Gate" })).toBeNull();
  expect(screen.getByRole("heading", { name: "Behind the Veil" })).toBeTruthy();
  expect(screen.getByText("The veil lifts.")).toBeTruthy();
});

test.each([
  { scope: "world", renderPage: renderWorld, base: "/worlds/realm" },
  { scope: "campaign", renderPage: renderCampaign, base: "/campaigns/run/world" },
])("$scope greetings follow the selected version for source and supporting cast", async ({ renderPage, base }) => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "gate", name: "At the Gate", character: "seraphine", version: "default", present: ["seraphine"] },
    { id: "veiled", name: "Behind the Veil", character: "seraphine", version: "veiled", present: ["seraphine"] },
    { id: "watch", name: "Tide Watch", character: "mara", version: "veiled", present: ["mara", "seraphine"] },
    { id: "rain", name: "Rainfall", character: "", version: "", present: ["seraphine"] },
    { id: "alone", name: "Mara Alone", character: "mara", version: "default", present: ["mara"] },
  ]));
  await renderPage();
  const reads = vi.mocked(api.listGreetings).mock.calls.length;
  fireEvent.click(screen.getByRole("tab", { name: "Greetings 3" }));
  const list = within(screen.getByRole("region", { name: "Playable greetings" }));
  expect(list.getAllByRole("link").map(link => link.textContent)).toEqual(["At the Gate", "Tide Watch", "Rainfall"]);
  expect(list.getByRole("link", { name: "At the Gate" })).toHaveAttribute("href", `${base}/greetings/gate`);

  fireEvent.click(screen.getByRole("button", { name: "veiled" }));
  await screen.findByRole("tab", { name: "Greetings 1" });
  expect(list.getAllByRole("link").map(link => link.textContent)).toEqual(["Behind the Veil"]);
  expect(list.getByRole("link", { name: "Behind the Veil" })).toHaveAttribute("href", `${base}/greetings/veiled`);

  fireEvent.click(screen.getByRole("button", { name: "default" }));
  await screen.findByRole("tab", { name: "Greetings 3" });
  expect(list.getAllByRole("link").map(link => link.textContent)).toEqual(["At the Gate", "Tide Watch", "Rainfall"]);
  expect(api.listGreetings).toHaveBeenCalledTimes(reads);
});

test.each([
  { scope: "world", renderPage: renderWorld, base: "/worlds/realm" },
  { scope: "campaign", renderPage: renderCampaign, base: "/campaigns/run/world" },
])("$scope direct version links include source greetings with incomplete cast", async ({ renderPage, base }) => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "veiled", name: "Behind the Veil", character: "seraphine", version: "veiled", present: [] },
  ]));
  await renderPage(`${base}/characters/seraphine?v=veiled`);
  fireEvent.click(screen.getByRole("tab", { name: "Greetings 1" }));
  expect(screen.getByRole("link", { name: "Behind the Veil" })).toHaveAttribute("href", `${base}/greetings/veiled`);
  fireEvent.click(screen.getByRole("button", { name: "default" }));
  await screen.findByRole("tab", { name: "Greetings 0" });
  expect(screen.queryByRole("link", { name: "Behind the Veil" })).toBeNull();
  expect(screen.getByText(/No playable greetings/)).toBeTruthy();
});

test("editing the first message writes it back to the card", async () => {
  await renderWorld();
  const box = await editField("First message");
  fireEvent.change(box, { target: { value: "You are late." } });
  fireEvent.click(within(fieldBlock("First message")).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(vi.mocked(api.updateVersion).mock.calls[0][3].data.first_mes).toBe("You are late.");
});


test("+ Add greeting writes only once there is something to write", async () => {
  await renderWorld();
  fireEvent.click(await screen.findByRole("button", { name: "+ Add greeting" }));
  const box = await screen.findByRole("textbox", { name: "Alternate greeting 2" });
  // A blank placeholder saved to make the row exist would not survive the round
  // trip: `buildCard` drops empty greetings on the way out.
  expect(api.updateVersion).not.toHaveBeenCalled();
  fireEvent.change(box, { target: { value: "The council chamber." } });
  fireEvent.click(within(fieldBlock("Alternate greeting 2"))
    .getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateVersion).toHaveBeenCalled());
  expect(vi.mocked(api.updateVersion).mock.calls[0][3].data.alternate_greetings)
    .toEqual(["Rain on the shutters.", "The council chamber."]);
});

test("cancelling a new greeting leaves the card alone", async () => {
  await renderWorld();
  fireEvent.click(await screen.findByRole("button", { name: "+ Add greeting" }));
  await screen.findByRole("textbox", { name: "Alternate greeting 2" });
  fireEvent.click(within(fieldBlock("Alternate greeting 2"))
    .getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.queryByRole("textbox", { name: "Alternate greeting 2" })).toBeNull());
  expect(api.updateVersion).not.toHaveBeenCalled();
});
