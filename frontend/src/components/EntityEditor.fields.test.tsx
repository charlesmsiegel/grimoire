import type { EntityDetail } from "../api/types";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { Wrap, deferred, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

test("typed fields render in the form and are sent on create", async () => {
  render(<Wrap wid="w" kind="items" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Salt Knife" } });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "weapon" } });
  fireEvent.change(screen.getByLabelText("Rarity"), { target: { value: "rare" } });
  fireEvent.click(screen.getByRole("button", { name: /create item/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "items", {
      name: "Salt Knife", body: "", keys: "", owners: "", secrecy: "public",
      fields: { item_type: "weapon", rarity: "rare" },
    }),
  );
});

test("typed field values show as chips in the detail sidebar", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "marsh-wyrm", name: "Marsh Wyrm" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "marsh-wyrm", name: "Marsh Wyrm", creature_type: "wyrm", threat: "apex" }, body: "old" }));
  const { container } = render(<Wrap wid="w" kind="creatures" selected="marsh-wyrm" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/Type: wyrm/)).toBeInTheDocument();
  expect(within(side).getByText(/Threat: apex/)).toBeInTheDocument();
});

test("a single-valued ref field offers only its own kinds, and sends the pick", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.listPCs).mockResolvedValue(fixture([{ id: "winifred", name: "Winifred" }]));
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [{ id: "saltmarch", name: "Saltmarch" }] : []));
  render(<Wrap wid="w" kind="groups" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "The Watch" } });
  const leader = await screen.findByRole("radiogroup", { name: "Leader" });
  const hq = screen.getByRole("radiogroup", { name: "Headquarters" });
  // Leader takes people; headquarters takes places. Neither offers the other's.
  expect(within(leader).getByLabelText("Mara")).toBeInTheDocument();
  expect(within(leader).getByLabelText("Winifred")).toBeInTheDocument();
  expect(within(leader).queryByLabelText("Saltmarch")).toBeNull();
  expect(within(hq).getByLabelText("Saltmarch")).toBeInTheDocument();
  expect(within(hq).queryByLabelText("Mara")).toBeNull();
  fireEvent.click(within(leader).getByLabelText("Mara"));
  fireEvent.click(screen.getByRole("button", { name: /create group/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "groups",
      expect.objectContaining({ fields: { leader: "characters:mara" } })));
});

test("a single-valued ref field can be cleared back to none", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "characters:mara" }, body: "x", rev: "r1" });
  render(<Wrap wid="w" kind="groups" selected="watch" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  const leader = await screen.findByRole("radiogroup", { name: "Leader" });
  expect(within(leader).getByLabelText("Mara")).toBeChecked();
  fireEvent.click(within(leader).getByLabelText("None"));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "groups", "watch",
      expect.objectContaining({ fields: expect.objectContaining({ leader: "" }) })));
});

test("a multi-valued ref field is a checkbox picker and sends a comma-joined list", async () => {
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations"
      ? [{ id: "saltmarch", name: "Saltmarch" }, { id: "realm", name: "Realm" }] : []));
  render(<Wrap wid="w" kind="creatures" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Marsh Wyrm" } });
  const habitat = await screen.findByRole("group", { name: "Habitat" });
  fireEvent.click(within(habitat).getByLabelText("Saltmarch"));
  fireEvent.click(within(habitat).getByLabelText("Realm"));
  fireEvent.click(screen.getByRole("button", { name: /create creature/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "creatures",
      expect.objectContaining({
        fields: expect.objectContaining({ habitat: "locations:saltmarch, locations:realm" }),
      })));
});

test("a ref shows in the sidebar as a chip that navigates to the record", async () => {
  const onOpenOwner = vi.fn();
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "characters:mara" }, body: "x", rev: "r1" });
  const { container } = render(<Wrap wid="w" kind="groups" selected="watch" onOpenOwner={onOpenOwner} />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Leader")).toBeInTheDocument();
  fireEvent.click(within(side).getByRole("button", { name: /Mara/ }));
  expect(onOpenOwner).toHaveBeenCalledWith("characters:mara");
});

test("a ref whose record is gone renders as a dangling chip rather than disappearing", async () => {
  // The decided behaviour (#222): a delete does not scrub the refs that name
  // the record, so the reader has to say the holder is missing.
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "characters:mara" }, body: "x", rev: "r1" });
  const { container } = render(<Wrap wid="w" kind="groups" selected="watch" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const chip = within(side).getByText("characters:mara");
  expect(chip).toHaveClass("dangling");
  expect(chip.getAttribute("title")).toMatch(/no longer/i);
});

test("a ref field with no candidates says so instead of rendering an empty picker", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([]);
  render(<Wrap wid="w" kind="groups" />);
  // Leader and Members each offer their own actor picker (spec 07).
  expect(await screen.findAllByText(/No characters or PCs yet/i)).toHaveLength(2);
});

test("kinds with no ref fields fetch no candidate lists", async () => {
  render(<Wrap wid="w" kind="locations" />);
  await waitFor(() => expect(api.listEntities).toHaveBeenCalled());
  expect(api.listCharacters).not.toHaveBeenCalled();
  expect(api.listPCs).not.toHaveBeenCalled();
});

test("actors are listed only once something draws them", async () => {
  // Closed Activation, no known_by on the record: no listing. Opening the
  // disclosure is what asks.
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine" }, body: "b", rev: "r1" });
  render(<Wrap wid="w" kind="creatures" selected="seraphine" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  expect(api.listCharacters).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Activation"));
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledTimes(1));
  expect(api.listPCs).toHaveBeenCalledTimes(1);
});

test("a dangling ref is visible in the form and can be cleared", async () => {
  // Without a row of its own the field would look unset while still saving the
  // old ref — and the one thing you could not do is remove it.
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "winifred", name: "Winifred" }]));
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "characters:mara" }, body: "x", rev: "r1" });
  render(<Wrap wid="w" kind="groups" selected="watch" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  const leader = await screen.findByRole("radiogroup", { name: "Leader" });
  expect(within(leader).getByLabelText("characters:mara")).toBeChecked();
  expect(within(leader).getByLabelText("None")).not.toBeChecked();
  fireEvent.click(within(leader).getByLabelText("Winifred"));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "groups", "watch",
      expect.objectContaining({
        fields: expect.objectContaining({ leader: "characters:winifred" }) })));
});

test("an untouched dangling ref is left alone rather than scrubbed", async () => {
  // A save sends only what the user changed, so an untouched field is not in
  // the patch at all and the stored value stays exactly as it was found. That
  // is what keeps a value the save boundary would REFUSE — a legacy free-text
  // `leader`, say — from making the record uneditable.
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "characters:mara" }, body: "x", rev: "r1" });
  render(<Wrap wid="w" kind="groups" selected="watch" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalled());
  const sent = vi.mocked(api.updateEntity).mock.calls[0][3];
  expect(sent.fields).toBeUndefined();
});

test("a legacy free-text value in a newly-claimed key does not block an unrelated edit", async () => {
  // `holder` was unrecognised frontmatter before ref fields existed, so a
  // hand-written `holder: Mara` is out there. Resending it on every save would
  // have the backend refuse it and make the record uneditable.
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "items" ? [{ id: "knife", name: "Salt Knife" }] : []));
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "knife", name: "Salt Knife", holder: "Mara" }, body: "sharp", rev: "r1" });
  render(<Wrap wid="w" kind="items" selected="knife" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "very sharp" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalled());
  const sent = vi.mocked(api.updateEntity).mock.calls[0][3];
  expect(sent.body).toBe("very sharp");
  expect(sent.fields?.holder).toBeUndefined();   // never resent, so never refused
});

test("changing one field sends only that field", async () => {
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "items" ? [{ id: "knife", name: "Salt Knife" }] : []));
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "knife", name: "Salt Knife", item_type: "weapon", rarity: "rare" },
    body: "sharp", rev: "r1" });
  render(<Wrap wid="w" kind="items" selected="knife" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Rarity"), { target: { value: "common" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalled());
  expect(vi.mocked(api.updateEntity).mock.calls[0][3].fields).toEqual({ rarity: "common" });
});

test("a scope change clears the ref candidates before the new ones arrive", async () => {
  // Navigating between worlds can keep this editor mounted. Until the new
  // scope's listing lands the picker must offer NOTHING — offering the old
  // scope's records saves a ref into a scope where it names nothing, and
  // existence is deliberately not validated server-side.
  const w2 = deferred<any[]>();
  vi.mocked(api.listEntities).mockImplementation((s, kind) => {
    if (kind !== "locations") return Promise.resolve([]);
    return s.id === "w1" ? Promise.resolve([{ id: "saltmarch", name: "Saltmarch" }]) : w2.promise;
  });
  const { rerender } = render(
    <Wrap wid="w1" scope={{ kind: "world", id: "w1" }} kind="creatures" />);
  expect(within(await screen.findByRole("group", { name: "Habitat" }))
    .getByLabelText("Saltmarch")).toBeInTheDocument();
  // w2's listing is still in flight at this point, deliberately.
  rerender(<Wrap wid="w2" scope={{ kind: "world", id: "w2" }} kind="creatures" />);
  expect(within(screen.getByRole("group", { name: "Habitat" }))
    .queryByLabelText("Saltmarch")).toBeNull();
  w2.resolve([{ id: "realm", name: "Realm" }]);
  expect(await within(screen.getByRole("group", { name: "Habitat" }))
    .findByLabelText("Realm")).toBeInTheDocument();
});

test("a slow listing from the previous scope cannot land on top of the current one", async () => {
  // The other half: w1's request resolves SECOND. Without a request token it
  // overwrites w2's options and the stale candidates stick permanently.
  const w1 = deferred<any[]>();
  vi.mocked(api.listEntities).mockImplementation((s, kind) => {
    if (kind !== "locations") return Promise.resolve([]);
    return s.id === "w1" ? w1.promise : Promise.resolve([{ id: "realm", name: "Realm" }]);
  });
  const { rerender } = render(
    <Wrap wid="w1" scope={{ kind: "world", id: "w1" }} kind="creatures" />);
  rerender(<Wrap wid="w2" scope={{ kind: "world", id: "w2" }} kind="creatures" />);
  const habitat = await screen.findByRole("group", { name: "Habitat" });
  expect(await within(habitat).findByLabelText("Realm")).toBeInTheDocument();
  w1.resolve([{ id: "saltmarch", name: "Saltmarch" }]);
  await waitFor(() => expect(within(habitat).getByLabelText("Realm")).toBeInTheDocument());
  expect(within(habitat).queryByLabelText("Saltmarch")).toBeNull();
});

test("a record whose id carries the list delimiter is not offered as a candidate", async () => {
  // `<kind>:<id>` in a comma-separated list, so `locations:salt,march` parses
  // as two refs and could never be saved. slugify cannot make one; an imported
  // file can. Mirrors entity_schema.referenceable.
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations"
      ? [{ id: "salt,march", name: "Salt March" }, { id: "realm", name: "Realm" }] : []));
  render(<Wrap wid="w" kind="creatures" />);
  const habitat = await screen.findByRole("group", { name: "Habitat" });
  expect(within(habitat).getByLabelText("Realm")).toBeInTheDocument();
  expect(within(habitat).queryByLabelText("Salt March")).toBeNull();
});

test("same-named candidates across kinds are told apart by kind", async () => {
  // Names are not unique — only ids are — and `holder` offers four kinds at
  // once, so two radios both reading "Mara" would be a control where you
  // cannot tell which relationship you are storing.
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "groups" ? [{ id: "mara-company", name: "Mara" }]
      : kind === "locations" ? [{ id: "saltmarch", name: "Saltmarch" }] : []));
  render(<Wrap wid="w" kind="items" />);
  const holder = await screen.findByRole("radiogroup", { name: "Held by" });
  expect(within(holder).getByLabelText("Mara (character)")).toBeInTheDocument();
  expect(within(holder).getByLabelText("Mara (group)")).toBeInTheDocument();
  // ...and an unambiguous one keeps its plain name
  expect(within(holder).getByLabelText("Saltmarch")).toBeInTheDocument();
});

test("same-named candidates of the SAME kind fall back to the ref", async () => {
  // Two characters really can share a name, and then the kind settles nothing.
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([
    { id: "mara", name: "Mara" }, { id: "mara-2", name: "Mara" }]));
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([]);
  render(<Wrap wid="w" kind="items" />);
  const holder = await screen.findByRole("radiogroup", { name: "Held by" });
  expect(within(holder).getByLabelText("Mara (characters:mara)")).toBeInTheDocument();
  expect(within(holder).getByLabelText("Mara (characters:mara-2)")).toBeInTheDocument();
});

test("one kind failing to load does not empty the whole picker", async () => {
  vi.mocked(api.listCharacters).mockRejectedValue(new Error("network"));
  vi.mocked(api.listPCs).mockResolvedValue(fixture([{ id: "winifred", name: "Winifred" }]));
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [{ id: "saltmarch", name: "Saltmarch" }] : []));
  render(<Wrap wid="w" kind="items" />);
  const holder = await screen.findByRole("radiogroup", { name: "Held by" });
  expect(within(holder).getByLabelText("Winifred")).toBeInTheDocument();
  expect(within(holder).getByLabelText("Saltmarch")).toBeInTheDocument();
});

test("a failed candidate load does not report existing refs as deleted", async () => {
  // The harmful version: a dropped request becomes a claim about the store,
  // and the reader may act on it by clearing a relationship that is fine.
  vi.mocked(api.listCharacters).mockRejectedValue(new Error("network"));
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "items" ? [{ id: "knife", name: "Salt Knife" }] : []));
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "knife", name: "Salt Knife", holder: "characters:mara" }, body: "x", rev: "r1" });
  const { container } = render(<Wrap wid="w" kind="items" selected="knife" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const chip = within(side).getByText("characters:mara");
  expect(chip.getAttribute("title")).toMatch(/could not load/i);
  expect(chip.getAttribute("title")).not.toMatch(/no longer exists/i);
});

test("a ref really is reported as deleted once the lists have loaded", async () => {
  // The other side of the same coin — the load succeeded, so "gone" is a claim
  // this editor is entitled to make.
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "items" ? [{ id: "knife", name: "Salt Knife" }] : []));
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "knife", name: "Salt Knife", holder: "characters:mara" }, body: "x", rev: "r1" });
  const { container } = render(<Wrap wid="w" kind="items" selected="knife" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("characters:mara").getAttribute("title"))
    .toMatch(/no longer exists/i);
});

test("an empty picker whose listing failed says so instead of claiming the store is empty", async () => {
  // A single-kind field is the sharp case: its one listing failing left the
  // picker reading "No locations yet." — a request outage presented as an
  // empty library, with no error and nothing to retry.
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    kind === "locations" ? Promise.reject(new Error("network")) : Promise.resolve([]));
  render(<Wrap wid="w" kind="creatures" />);
  expect(await screen.findByText(/could not load the list of records/i)).toBeInTheDocument();
  expect(screen.queryByText(/No locations yet/i)).toBeNull();
});

test("an empty picker whose listing succeeded still says the store is empty", async () => {
  // The claim is legitimate when a listing actually arrived, so the fix must
  // not turn every empty picker into a fake error.
  vi.mocked(api.listEntities).mockResolvedValue([]);
  render(<Wrap wid="w" kind="creatures" />);
  expect(await screen.findByText(/No locations yet/i)).toBeInTheDocument();
});

test("a record whose id carries a line separator is not offered as a candidate", async () => {
  // Every separator `parse_frontmatter`'s splitlines recognises, not just \n.
  const seps = ["\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", " ", " "];
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations"
      ? [...seps.map((c, i) => ({ id: `a${c}b${i}`, name: `Bad ${i}` })),
         { id: "realm", name: "Realm" }]
      : []));
  render(<Wrap wid="w" kind="creatures" />);
  const habitat = await screen.findByRole("group", { name: "Habitat" });
  expect(within(habitat).getByLabelText("Realm")).toBeInTheDocument();
  seps.forEach((_c, i) =>
    expect(within(habitat).queryByLabelText(`Bad ${i}`)).toBeNull());
});

test("a sidebar ref of a kind the field cannot name reads as unresolved", async () => {
  // `leader` takes people; `headquarters` takes places. Resolving against the
  // whole union let a legacy `leader: locations:realm` resolve off the
  // locations fetched for headquarters and render as an ordinary clickable
  // chip — the read view calling a malformed relationship valid, while the
  // form one click away correctly showed it unresolved.
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "groups" ? [{ id: "watch", name: "The Watch" }]
      : kind === "locations" ? [{ id: "realm", name: "Realm" }] : []));
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "locations:realm",
            headquarters: "locations:realm" },
    body: "x", rev: "r1" });
  const { container } = render(<Wrap wid="w" kind="groups" selected="watch" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  // ...the same value, under two fields, told apart by what each may name
  const leader = within(side).getByRole("heading", { name: "Leader" })
    .parentElement as HTMLElement;
  const hq = within(side).getByRole("heading", { name: "Headquarters" })
    .parentElement as HTMLElement;
  expect(within(leader).getByText("locations:realm")).toHaveClass("dangling");
  expect(within(hq).getByRole("button", { name: /Realm/ })).toBeInTheDocument();
});

test("a record read still in flight when the scope changes does not land under the new scope", async () => {
  // The same exposure PCEditor.select had: this editor stays mounted across a
  // world-to-world navigation, and the SAME id staying selected across it is
  // now the intended behaviour (it is re-read under the new scope) -- but a
  // slow read the OLD scope started must still never land, no matter how late
  // it resolves.
  let resolveW1!: (v: EntityDetail) => void;
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.readEntity)
    .mockImplementationOnce(() => new Promise((r) => { resolveW1 = r; }))
    .mockResolvedValue({ meta: { id: "salt", name: "Salt" }, body: "new scope body", rev: "r2" });
  const { rerender } = render(
    <Wrap wid="w1" scope={{ kind: "world", id: "w1" }} kind="lore" selected="salt" />);
  rerender(<Wrap wid="w2" scope={{ kind: "world", id: "w2" }} kind="lore" selected="salt" />);
  await waitFor(() =>
    expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w2" }, "lore"));
  await screen.findByText("new scope body");
  resolveW1({ meta: { id: "salt", name: "Salt" }, body: "old scope body", rev: "r1" });
  await waitFor(() => expect(api.listEntities).toHaveBeenCalled());
  expect(screen.queryByText("old scope body")).toBeNull();
});

// ---- choice and number widgets (#221) --------------------------------------

test("a choice field is a picker over its source's options and sends the chosen id", async () => {
  render(<Wrap wid="w" kind="locations" />);
  const picker = await screen.findByLabelText("Climate");
  expect(picker.tagName).toBe("SELECT");
  await waitFor(() => expect(within(picker).getByText("Saltmarch fog")).toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Fogbank" } });
  fireEvent.change(picker, { target: { value: "saltmarch-fog" } });
  fireEvent.click(screen.getByRole("button", { name: /create location/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "locations", {
      name: "Fogbank", body: "", keys: "", owners: "", secrecy: "public",
      fields: { climate: "saltmarch-fog" },
    }),
  );
});

test("a number field is a bounded number input", async () => {
  render(<Wrap wid="w" kind="locations" />);
  const input = await screen.findByLabelText<HTMLInputElement>("Weather persistence");
  expect(input.type).toBe("number");
  expect(input.min).toBe("0");
  expect(input.max).toBe("1");
  // and a text field stays a text box
  expect(screen.getByLabelText<HTMLInputElement>("Weather zone").type).toBe("text");
});

test("a stored choice value outside the options is shown, not blanked", async () => {
  // A climate since deleted, or a hand-edited file. The picker keeps it as a
  // row of its own so the value is visible and clearable -- silently showing
  // "(none)" would save a clear the user never asked for.
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "fogbank", name: "Fogbank" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "fogbank", name: "Fogbank", climate: "temperate-costal" }, body: "grey" }));
  render(<Wrap wid="w" kind="locations" selected="fogbank" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  const picker = await screen.findByLabelText<HTMLSelectElement>("Climate");
  await waitFor(() => expect(within(picker).getByText("Saltmarch fog")).toBeInTheDocument());
  expect(picker.value).toBe("temperate-costal");
  expect(within(picker).getByText(/temperate-costal \(not an option\)/)).toBeInTheDocument();
});

test("a stored choice value is not called 'not an option' while the list has not arrived", async () => {
  // A request outage is not a fact about the value: until the source answers
  // (and for good if it fails) the value is simply shown, still selected.
  vi.mocked(api.listClimates).mockRejectedValue(new Error("offline"));
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "fogbank", name: "Fogbank" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "fogbank", name: "Fogbank", climate: "temperate-interior" }, body: "grey" }));
  render(<Wrap wid="w" kind="locations" selected="fogbank" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  const picker = await screen.findByLabelText<HTMLSelectElement>("Climate");
  expect(picker.value).toBe("temperate-interior");
  expect(within(picker).getByText("temperate-interior")).toBeInTheDocument();
  expect(within(picker).queryByText(/not an option/)).toBeNull();
  // ...and the failure is said under the control, not left as an empty picker
  expect(await screen.findByText(/Could not load the choices/)).toBeInTheDocument();
});

test("a stored number field value the number input cannot show gets a text box", async () => {
  // A hand-written `wet` is sanitized to blank by <input type="number">, which
  // would make the field look unfilled while it still holds the value. The
  // text box shows it, and the boundary will say what is wrong on save.
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "fogbank", name: "Fogbank" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "fogbank", name: "Fogbank", persistence: "wet" }, body: "grey" }));
  render(<Wrap wid="w" kind="locations" selected="fogbank" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  const input = await screen.findByLabelText<HTMLInputElement>("Weather persistence");
  expect(input.type).toBe("text");
  expect(input.value).toBe("wet");
  // Once the value parses, the same element becomes a number input (React
  // changes the attribute, not the node, so the caret stays) with the value kept.
  fireEvent.change(input, { target: { value: "0.4" } });
  const after = screen.getByLabelText<HTMLInputElement>("Weather persistence");
  expect(after.type).toBe("number");
  expect(after.value).toBe("0.4");
});

test("the sidebar names a chosen option by its label", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "fogbank", name: "Fogbank" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "fogbank", name: "Fogbank", climate: "temperate-interior", persistence: "0.4" },
    body: "grey" }));
  const { container } = render(<Wrap wid="w" kind="locations" selected="fogbank" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/Climate: Temperate interior/)).toBeInTheDocument();
  expect(within(side).getByText(/Weather persistence: 0.4/)).toBeInTheDocument();
});

// ---- selecting from the route (world-section-routes, task 4) --------------

