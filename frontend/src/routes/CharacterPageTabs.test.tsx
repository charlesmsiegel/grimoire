import { renderWorld, renderCampaign, editField, fieldBlock, lastLocation } from "../testkit/characterPage";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { api, type ExpressionCollection } from "../api/client";
import { fixture } from "../testkit/fixture";

const pack = { label: "Armor", clothing_aliases: [], fallback_expression: "neutral", source: "inherited" as const,
  presentation: { scale: 1, offset_x: 0, offset_y: 0, anchor: "bottom-center" as const }, expressions: {} };
const collection: ExpressionCollection = { format: 1, revision: null, effective_revision: "one", default_outfit: "armor",
  outfits: { armor: pack, everyday: { ...pack, source: "local" } }, standard_labels: [],
  hidden_outfits: ["formal"], hidden_expressions: {}, warnings: [] };

test("Expressions counts effective packs before opening and updates after adding an outfit", async () => {
  vi.mocked(api.getExpressions).mockResolvedValue(collection);
  vi.mocked(api.mutateExpressions).mockImplementation(async () => {
    const result = { ...collection, outfits: { ...collection.outfits, formal: pack } };
    vi.mocked(api.getExpressions).mockResolvedValue(result);
    return result;
  });
  await renderWorld();
  fireEvent.click(await screen.findByRole("tab", { name: "Expressions 2" }));
  expect(screen.getByRole("combobox", { name: "Outfit" }).children).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Edit expressions" }));
  fireEvent.change(screen.getByRole("textbox", { name: "New outfit ID" }), { target: { value: "formal" } });
  fireEvent.click(screen.getByRole("button", { name: "Add outfit" }));
  expect(await screen.findByRole("tab", { name: "Expressions 3" })).toBeTruthy();
  expect(screen.getByRole("combobox", { name: "Outfit" }).children).toHaveLength(3);
});

test("Expressions shows zero for an empty collection and omits the count on failure", async () => {
  vi.mocked(api.getExpressions).mockImplementation(async target => {
    if (target.version_id === "veiled") throw new Error("Unavailable");
    return { ...collection, outfits: {}, default_outfit: null };
  });
  await renderWorld();
  expect(await screen.findByRole("tab", { name: "Expressions 0" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "veiled" }));
  fireEvent.click(await screen.findByRole("tab", { name: "Expressions" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Unavailable");
});

test("a late expression read cannot replace the selected version's count", async () => {
  let resolve!: (value: ExpressionCollection) => void;
  vi.mocked(api.getExpressions).mockImplementation(target => target.version_id === "default"
    ? new Promise(done => { resolve = done; }) : Promise.resolve({ ...collection, outfits: { armor: pack } }));
  await renderWorld();
  fireEvent.click(screen.getByRole("button", { name: "veiled" }));
  expect(await screen.findByRole("tab", { name: "Expressions 1" })).toBeTruthy();
  await act(async () => { resolve(collection); });
  expect(screen.getByRole("tab", { name: "Expressions 1" })).toBeTruthy();
});

test("an imported pack with a failed reload hides the stale count until recovery", async () => {
  const imported = { ...collection, outfits: { ...collection.outfits, formal: pack } };
  vi.mocked(api.getExpressions).mockResolvedValueOnce(collection)
    .mockRejectedValueOnce(new Error("Reload unavailable")).mockResolvedValue(imported);
  vi.mocked(api.previewExpressionPack).mockResolvedValue({ token: "preview", expires_at: 1, effective_revision: "one",
    issues: [], images: [], metadata: { format: 1, outfits: { formal: { label: "Formal" } } } });
  vi.mocked(api.publishExpressionPack).mockResolvedValue(imported);
  await renderWorld();
  fireEvent.click(await screen.findByRole("tab", { name: "Expressions 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Import expression pack" }));
  fireEvent.change(screen.getByLabelText("Expression ZIP"), { target: { files: [new File(["zip"], "outfits.zip")] } });
  fireEvent.click(await screen.findByRole("button", { name: "Import outfits" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Reload unavailable");
  expect(screen.getByRole("tab", { name: "Expressions" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reload library" }));
  expect(await screen.findByRole("tab", { name: "Expressions 3" })).toBeTruthy();
});

test("a retired publishing import refreshes the count without closing a new import dialog", async () => {
  const imported = { ...collection, outfits: { ...collection.outfits, formal: pack } };
  let stored = collection;
  let finish!: () => void;
  vi.mocked(api.getExpressions).mockImplementation(async () => stored);
  vi.mocked(api.previewExpressionPack).mockResolvedValue({ token: "preview", expires_at: 1, effective_revision: "one",
    issues: [], images: [], metadata: { format: 1, outfits: { formal: { label: "Formal" } } } });
  vi.mocked(api.publishExpressionPack).mockImplementation(() => new Promise(resolve => {
    finish = () => { stored = imported; resolve(imported); };
  }));
  await renderWorld();
  fireEvent.click(await screen.findByRole("tab", { name: "Expressions 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Import expression pack" }));
  fireEvent.change(screen.getByLabelText("Expression ZIP"), { target: { files: [new File(["zip"], "first.zip")] } });
  fireEvent.click(await screen.findByRole("button", { name: "Import outfits" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("button", { name: "Import expression pack" }));
  fireEvent.change(screen.getByLabelText("Expression ZIP"), { target: { files: [new File(["zip"], "second.zip")] } });
  await screen.findByRole("button", { name: "Import outfits" });
  await act(async () => { finish(); });
  expect(await screen.findByRole("tab", { name: "Expressions 3" })).toBeTruthy();
  expect(screen.getByLabelText<HTMLInputElement>("Expression ZIP").files?.[0].name).toBe("second.zip");
  expect(screen.getByRole("button", { name: "Import outfits" })).toBeEnabled();
});

test.each(["tabs", "versions", "back to the original version"])("a publishing import updates only its owner after closing its dialog and switching %s", async destination => {
  const imported = { ...collection, outfits: { ...collection.outfits, formal: pack } };
  let stored = collection;
  let finish!: (value: ExpressionCollection) => void;
  vi.mocked(api.getExpressions).mockImplementation(async target =>
    target.version_id === "default" ? stored : { ...collection, outfits: { armor: pack } });
  vi.mocked(api.previewExpressionPack).mockResolvedValue({ token: "preview", expires_at: 1, effective_revision: "one",
    issues: [], images: [], metadata: { format: 1, outfits: { formal: { label: "Formal" } } } });
  vi.mocked(api.publishExpressionPack).mockImplementation(() => new Promise(resolve => {
    finish = value => { stored = value; resolve(value); };
  }));
  await renderWorld();
  fireEvent.click(await screen.findByRole("tab", { name: "Expressions 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Import expression pack" }));
  fireEvent.change(screen.getByLabelText("Expression ZIP"), { target: { files: [new File(["zip"], "outfits.zip")] } });
  fireEvent.click(await screen.findByRole("button", { name: "Import outfits" }));
  const close = screen.getByRole("button", { name: "Close" });
  expect(close).toBeEnabled();
  fireEvent.click(close);
  fireEvent.click(destination === "tabs"
    ? screen.getByRole("tab", { name: "Card" }) : screen.getByRole("button", { name: "veiled" }));
  if (destination === "back to the original version") {
    await screen.findByRole("tab", { name: "Expressions 1" });
    fireEvent.click(screen.getByRole("button", { name: "default" }));
    await screen.findByRole("tab", { name: "Expressions 2" });
  }
  await act(async () => { finish(imported); });
  const count = destination === "versions" ? 1 : 3;
  fireEvent.click(await screen.findByRole("tab", { name: `Expressions ${count}` }));
  expect(screen.getByRole("combobox", { name: "Outfit" }).children).toHaveLength(count);
});

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
