import type { EntityDetail } from "../api/types";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { Wrap, deferred, fail, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

const salt = (meta: Record<string, unknown> = {}): EntityDetail => ({
  meta: { id: "salt", name: "Salt", keys: "pact", ...meta }, body: "Binds", rev: "r1" });

/** The sidebar's `.side-section` headed `heading`, or null when none is. */
const sideSection = (side: HTMLElement, heading: string) =>
  Array.from(side.querySelectorAll<HTMLElement>(".side-section"))
    .find((sec) => sec.querySelector("h4")?.textContent === heading) ?? null;

test("activation settings show as chips in the read-only view", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ priority: "250", sticky: "2" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByRole("heading", { name: "Priority" });
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(sideSection(side, "Priority")!).getByText("250")).toHaveClass("chip", "on");
  expect(within(sideSection(side, "Sticky")!).getByText("2 posts")).toHaveClass("chip", "on");
  // No aggregate heading: each field is a section of its own.
  expect(within(side).queryByText("Activation")).toBeNull();
  expect(container.querySelector("textarea")).toBeNull();
  // Only what is set: nothing here says cooldown, and keep is off.
  expect(within(side).queryByText(/Cooldown/)).toBeNull();
  expect(within(side).queryByText("Keep under budget")).toBeNull();
});

test("each set activation field and known_by is a side-section of its own", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.readEntity).mockResolvedValue(
    salt({ sticky: "3", priority: "40", known_by: "characters:mara" }));
  const onOpenOwner = vi.fn();
  const { container } = render(
    <Wrap wid="w" kind="lore" selected="salt" onOpenOwner={onOpenOwner} />);
  await screen.findByRole("button", { name: "Mara" });
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const sticky = sideSection(side, "Sticky");
  const priority = sideSection(side, "Priority");
  const knownBy = sideSection(side, "Known by");
  for (const sec of [sticky, priority, knownBy]) {
    expect(sec).not.toBeNull();
    expect(sec!.querySelectorAll("h4")).toHaveLength(1);
  }
  expect(new Set([sticky, priority, knownBy]).size).toBe(3);
  expect(within(sticky!).getByText("3 posts")).toHaveClass("chip", "on");
  expect(within(priority!).getByText("40")).toHaveClass("chip", "on");
  // Known by keeps its navigating chips, and only there.
  fireEvent.click(within(knownBy!).getByRole("button", { name: "Mara" }));
  expect(onOpenOwner).toHaveBeenCalledWith("characters:mara");
  expect(within(sticky!).queryByRole("button")).toBeNull();
});

test("a record with no activation settings has no activation sections", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt());
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByText("Binds");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const headings = Array.from(side.querySelectorAll("h4")).map((h) => h.textContent);
  for (const label of ["Activation", "Secondary keys", "Key logic", "Scan depth", "Sticky",
                       "Cooldown", "Priority", "Keep under budget", "Recursion", "Known by"]) {
    expect(headings).not.toContain(label);
  }
});

test("keep shows as Keep under budget only when it is on", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ keep: "true", scan_depth: "4" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByRole("heading", { name: "Keep under budget" });
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(sideSection(side, "Keep under budget")!).getByText("yes")).toHaveClass("chip", "on");
  expect(within(sideSection(side, "Scan depth")!).getByText("4 posts")).toBeInTheDocument();
});

test("Edit reveals the Activation disclosure and saves changed fields only", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ priority: "250", sticky: "2" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  const details = container.querySelector("details.activation-settings") as HTMLElement;
  expect(within(details).getByText("Activation")).toBeInTheDocument();
  expect(within(details).getByLabelText("Sticky")).toHaveValue(2);
  fireEvent.change(within(details).getByLabelText("Priority"), { target: { value: "300" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "lore", "salt",
    expect.objectContaining({ fields: { priority: "300" } })));
});

test("clearing keep sends a blank, never false", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ keep: "true" }));
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByLabelText("Keep under budget"));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "lore", "salt",
    expect.objectContaining({ fields: { keep: "" } })));
});

test("a save the server refuses as an invalid activation value shows the reason", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt());
  vi.mocked(api.updateEntity).mockRejectedValueOnce(
    fail(400, "invalid values for lore: sticky"));
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByText("Activation"));
  fireEvent.change(await screen.findByLabelText("Sticky"), { target: { value: "99" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await screen.findByText("invalid values for lore: sticky");
});

test("every kind carries the Activation disclosure", async () => {
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "saltmarch-key", name: "Saltmarch Key", sticky: "1" }, body: "b", rev: "r1" });
  const { container } = render(<Wrap wid="w" kind="items" selected="saltmarch-key" />);
  await screen.findByText("1 post");
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("details.activation-settings")).not.toBeNull();
});

test("known_by renders as chips that open the actor", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.readEntity).mockResolvedValue(
    salt({ known_by: "characters:mara, characters:gone" }));
  const onOpenOwner = vi.fn();
  const { container } = render(
    <Wrap wid="w" kind="lore" selected="salt" onOpenOwner={onOpenOwner} />);
  fireEvent.click(await screen.findByRole("button", { name: "Mara" }));
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(onOpenOwner).toHaveBeenCalledWith("characters:mara");
  const missing = await within(side).findByText("missing: characters:gone");
  expect(missing.tagName).toBe("SPAN");
  expect(missing).toHaveClass("chip", "missing");
});

test("the Known by picker offers characters and PCs and stores refs", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.listPCs).mockResolvedValue(fixture([{ id: "winifred", name: "Winifred" }]));
  // A kind with no actor picker of its own, so "Mara" can only be this one.
  vi.mocked(api.readEntity).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine" }, body: "b", rev: "r1" });
  render(<Wrap wid="w" kind="creatures" selected="seraphine" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  // Closed, and not drawn: a record that sets nothing here opens it shut.
  expect(screen.queryByLabelText("Mara")).toBeNull();
  fireEvent.click(screen.getByText("Activation"));
  fireEvent.click(await screen.findByLabelText("Mara"));
  fireEvent.click(screen.getByLabelText("Winifred"));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "creatures", "seraphine",
    expect.objectContaining({ fields: { known_by: "characters:mara, pcs:winifred" } })));
});

test("adopt banner applies imported settings", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ st_extensions: '{"sticky":2}' }));
  vi.mocked(api.previewAdoptSt).mockResolvedValue(
    { fields: { sticky: "2" }, unmapped: ["probability"] });
  vi.mocked(api.adoptSt).mockResolvedValue({ applied: { sticky: "2" } });
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  const banner = await screen.findByRole("status", { name: /imported settings/i });
  expect(within(banner).getByText("sticky")).toBeInTheDocument();
  expect(within(banner).getByText(/probability/)).toBeInTheDocument();
  expect(api.previewAdoptSt).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt");
  const reads = vi.mocked(api.readEntity).mock.calls.length;
  fireEvent.click(within(banner).getByRole("button", { name: /^apply$/i }));
  await waitFor(() => expect(api.adoptSt).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "lore", "salt"));
  await waitFor(() => expect(vi.mocked(api.readEntity).mock.calls.length).toBeGreaterThan(reads));
});

test("no adopt banner when the preview has nothing to add, or there is no stash", async () => {
  // A stash that is fully adopted already: the preview says nothing is pending.
  vi.mocked(api.readEntity).mockResolvedValue(salt({ st_extensions: '{"sticky":2}' }));
  const { unmount } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.previewAdoptSt).toHaveBeenCalled());
  expect(screen.queryByRole("status", { name: /imported settings/i })).toBeNull();
  unmount();
  // No stash: the preview is never asked for.
  vi.mocked(api.previewAdoptSt).mockClear();
  vi.mocked(api.readEntity).mockResolvedValue(salt());
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByText("Binds");
  expect(api.previewAdoptSt).not.toHaveBeenCalled();
});

test("Apply imported settings to all sits beside + New on the lore rail only", async () => {
  vi.mocked(api.adoptStAll).mockResolvedValue({
    applied: [{ kind: "lore", id: "a", fields: { sticky: "2" } },
              { kind: "items", id: "b", fields: { priority: "5" } }],
    skipped: [{ kind: "lore", id: "c", reason: "unreadable st_extensions" }] });
  const { unmount } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  const before = vi.mocked(api.listEntities).mock.calls.length;
  fireEvent.click(await screen.findByRole("button", { name: "Apply imported settings to all" }));
  await screen.findByText("Applied to 2 entries; 1 skipped");
  expect(api.adoptStAll).toHaveBeenCalledWith({ kind: "world", id: "w" });
  expect(vi.mocked(api.listEntities).mock.calls.length).toBeGreaterThan(before);
  unmount();
  render(<Wrap isNew={false} wid="w" kind="items" />);
  await screen.findByText("No items yet.");
  expect(screen.queryByRole("button", { name: "Apply imported settings to all" })).toBeNull();
});

test("chips say post for one and posts for the rest", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ cooldown: "0", sticky: "1", scan_depth: "3" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByText("1 post");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(sideSection(side, "Sticky")!).getByText("1 post")).toBeInTheDocument();
  expect(within(sideSection(side, "Scan depth")!).getByText("3 posts")).toBeInTheDocument();
  // "0" is a value that is set: it reads as the number it is.
  expect(within(sideSection(side, "Cooldown")!).getByText("0 posts")).toBeInTheDocument();
});

test("the activation counts step by one", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ sticky: "2" }));
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  expect(await screen.findByLabelText("Sticky")).toHaveAttribute("step", "1");
});

test("adopt-all is disabled over an open edit and never discards it", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.readEntity).mockResolvedValue(salt());
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "unsaved words" } });
  const all = screen.getByRole("button", { name: "Apply imported settings to all" });
  expect(all).toBeDisabled();
  expect(all).toHaveAttribute("title", expect.stringMatching(/save or cancel/i));
  fireEvent.click(all);
  expect(api.adoptStAll).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Body")).toHaveValue("unsaved words");
});

test("a library with no pending imported settings offers no bulk Apply", async () => {
  vi.mocked(api.previewAdoptStAll).mockResolvedValue({ pending: 0 });
  render(<Wrap isNew={false} wid="w" kind="lore" />);
  await waitFor(() => expect(api.previewAdoptStAll).toHaveBeenCalled());
  expect(screen.queryByRole("button", { name: "Apply imported settings to all" })).not.toBeInTheDocument();
});

test("adopt-all over a viewed record applies and re-reads it", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.readEntity).mockResolvedValue(salt());
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByRole("button", { name: /^edit$/i });
  const reads = vi.mocked(api.readEntity).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Apply imported settings to all" }));
  await screen.findByText("Applied to 0 entries; 0 skipped");
  expect(vi.mocked(api.readEntity).mock.calls.length).toBeGreaterThan(reads);
});

test("a second click while an adopt is in flight does nothing", async () => {
  let land!: (v: { applied: []; skipped: [] }) => void;
  vi.mocked(api.adoptStAll).mockReturnValue(new Promise((r) => { land = r; }));
  render(<Wrap isNew={false} wid="w" kind="lore" />);
  const all = await screen.findByRole("button", { name: "Apply imported settings to all" });
  fireEvent.click(all);
  await waitFor(() => expect(all).toBeDisabled());
  fireEvent.click(all);
  expect(api.adoptStAll).toHaveBeenCalledTimes(1);
  land({ applied: [], skipped: [] });
  await screen.findByText("Applied to 0 entries; 0 skipped");
  await waitFor(() => expect(screen.getByRole("button", { name: "Apply imported settings to all" })).toBeEnabled());
});

test.each([false, true])("the adoption preview keeps bulk Apply pending, refused=%s", async (refused) => {
  const preview = deferred<Awaited<ReturnType<typeof api.previewAdoptStAll>>>();
  vi.mocked(api.previewAdoptStAll).mockResolvedValueOnce({ pending: 1 }).mockReturnValueOnce(preview.promise);
  vi.mocked(api.adoptStAll).mockResolvedValue({ applied: [{ kind: "lore", id: "salt", fields: { sticky: "2" } }], skipped: [] });
  render(<Wrap isNew={false} wid="w" kind="lore" />);
  const all = await screen.findByRole("button", { name: "Apply imported settings to all" });
  fireEvent.click(all);
  await screen.findByText("Applied to 1 entry; 0 skipped");
  await waitFor(() => expect(api.previewAdoptStAll).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("button", { name: "Apply imported settings to all" })).not.toBeInTheDocument();
  expect(api.adoptStAll).toHaveBeenCalledTimes(1);
  await act(async () => {
    if (refused) preview.reject(new Error("Preview unavailable"));
    else preview.resolve({ pending: 0 });
  });
  expect(screen.queryByRole("button", { name: "Apply imported settings to all" })).not.toBeInTheDocument();
  expect(screen.getByText("Applied to 1 entry; 0 skipped")).toBeInTheDocument();
  expect(api.adoptStAll).toHaveBeenCalledTimes(1);
});

test("a successful bulk adoption retires its offer when the list refresh fails", async () => {
  const listing = deferred<Awaited<ReturnType<typeof api.listEntities>>>();
  vi.mocked(api.adoptStAll).mockResolvedValue({ applied: [{ kind: "lore", id: "salt", fields: { sticky: "2" } }], skipped: [] });
  const { rerender } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  const all = await screen.findByRole("button", { name: "Apply imported settings to all" });
  vi.mocked(api.listEntities).mockReturnValueOnce(listing.promise);
  fireEvent.click(all);
  await screen.findByText("Applied to 1 entry; 0 skipped");
  await act(async () => { listing.reject(new Error("List refresh unavailable")); });
  expect(await screen.findByText(/List refresh unavailable/)).toBeInTheDocument();
  expect(screen.getByText("Applied to 1 entry; 0 skipped")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Apply imported settings to all" })).not.toBeInTheDocument();
  expect(api.adoptStAll).toHaveBeenCalledTimes(1);
  expect(api.previewAdoptStAll).toHaveBeenCalledTimes(1);

  // Returning to the library may offer a sweep again only once a fresh
  // preview has confirmed something pending, never from the retired count.
  const confirmation = deferred<Awaited<ReturnType<typeof api.previewAdoptStAll>>>();
  vi.mocked(api.previewAdoptStAll).mockReturnValueOnce(confirmation.promise);
  rerender(<Wrap isNew={false} wid="w" kind="items" />);
  await screen.findByText("No items yet.");
  rerender(<Wrap isNew={false} wid="w" kind="lore" />);
  await waitFor(() => expect(api.previewAdoptStAll).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("button", { name: "Apply imported settings to all" })).not.toBeInTheDocument();
  await act(async () => { confirmation.resolve({ pending: 1 }); });
  expect(await screen.findByRole("button", { name: "Apply imported settings to all" })).toBeEnabled();
  expect(api.adoptStAll).toHaveBeenCalledTimes(1);
});

test("the per-entry Apply waits on its request too", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ st_extensions: '{"sticky":2}' }));
  vi.mocked(api.previewAdoptSt).mockResolvedValue({ fields: { sticky: "2" }, unmapped: [] });
  let land!: (v: { applied: Record<string, string> }) => void;
  vi.mocked(api.adoptSt).mockReturnValue(new Promise((r) => { land = r; }));
  render(<Wrap wid="w" kind="lore" selected="salt" />);
  const banner = await screen.findByRole("status", { name: /imported settings/i });
  const apply = within(banner).getByRole("button", { name: /^apply$/i });
  fireEvent.click(apply);
  await waitFor(() => expect(apply).toBeDisabled());
  fireEvent.click(apply);
  expect(api.adoptSt).toHaveBeenCalledTimes(1);
  land({ applied: { sticky: "2" } });
  // The mocked preview still reports a pending field, so the banner is back.
  await waitFor(() => expect(
    screen.getByRole("button", { name: /^apply$/i })).not.toBeDisabled());
});

describe("a per-entry adopt that fails", () => {
  function twoRecords() {
    vi.mocked(api.listEntities).mockResolvedValue([
      { id: "salt", name: "Salt" }, { id: "seraphine", name: "Seraphine" }]);
    vi.mocked(api.readEntity).mockImplementation((_s, _k, id) =>
      Promise.resolve({ meta: { id, name: id, st_extensions: '{"sticky":2}' },
                        body: `body of ${id}`, rev: "r1" }));
    vi.mocked(api.previewAdoptSt).mockResolvedValue({ fields: { sticky: "2" }, unmapped: [] });
    let fail!: (e: Error) => void;
    vi.mocked(api.adoptSt).mockReturnValue(new Promise((_r, j) => { fail = j; }));
    return (e: Error) => fail(e);
  }

  test("says so on the record it was for", async () => {
    const fail = twoRecords();
    render(<Wrap wid="w" kind="lore" selected="salt" />);
    const banner = await screen.findByRole("status", { name: /imported settings/i });
    fireEvent.click(within(banner).getByRole("button", { name: /^apply$/i }));
    await waitFor(() => expect(api.adoptSt).toHaveBeenCalled());
    fail(new Error("adopt refused"));
    expect(await screen.findByText(/adopt refused/)).toBeInTheDocument();
  });

  test("says nothing on a record opened since", async () => {
    const fail = twoRecords();
    const { rerender } = render(<Wrap wid="w" kind="lore" selected="salt" />);
    const banner = await screen.findByRole("status", { name: /imported settings/i });
    fireEvent.click(within(banner).getByRole("button", { name: /^apply$/i }));
    await waitFor(() => expect(api.adoptSt).toHaveBeenCalled());
    rerender(<Wrap wid="w" kind="lore" selected="seraphine" />);
    await screen.findByText("body of seraphine");
    fail(new Error("adopt refused"));
    await screen.findByText("body of seraphine");
    expect(screen.queryByText(/adopt refused/)).toBeNull();
  });
});

test("the adopt-all status line goes when another record is opened", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([
    { id: "salt", name: "Salt" }, { id: "seraphine", name: "Seraphine" }]);
  vi.mocked(api.readEntity).mockImplementation((_s, _k, id) =>
    Promise.resolve({ meta: { id, name: id }, body: `body of ${id}`, rev: "r1" }));
  const { rerender } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByText("body of salt");
  fireEvent.click(screen.getByRole("button", { name: "Apply imported settings to all" }));
  await screen.findByText("Applied to 0 entries; 0 skipped");
  rerender(<Wrap wid="w" kind="lore" selected="seraphine" />);
  await screen.findByText("body of seraphine");
  expect(screen.queryByText("Applied to 0 entries; 0 skipped")).toBeNull();
});

test("the adopt-all status line goes when the scope changes", async () => {
  const { rerender } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  fireEvent.click(await screen.findByRole("button", { name: "Apply imported settings to all" }));
  await screen.findByText("Applied to 0 entries; 0 skipped");
  rerender(<Wrap isNew={false} wid="other" kind="lore" scope={{ kind: "world", id: "other" }} />);
  await waitFor(() => expect(screen.queryByText("Applied to 0 entries; 0 skipped")).toBeNull());
});

test("a record the campaign only inherits points at the world instead of offering Apply", async () => {
  vi.mocked(api.readEntity).mockResolvedValue(salt({ st_extensions: '{"sticky":2}' }));
  vi.mocked(api.previewAdoptSt).mockResolvedValue(
    { fields: { sticky: "2" }, unmapped: [], inherited: true });
  render(<Wrap wid="w" scope={{ kind: "campaign", id: "c1" }} kind="lore" selected="salt" />);
  const banner = await screen.findByRole("status", { name: /imported settings/i });
  expect(within(banner).getByText(
    "Imported SillyTavern settings are available on the world's copy of this entry.")).toBeInTheDocument();
  expect(within(banner).queryByRole("button")).toBeNull();
  expect(screen.queryByRole("button", { name: /^apply$/i })).toBeNull();
});

test("a Known-by chip is a label, not an inert button, when nothing can open the actor", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "mara", name: "Mara" }]));
  vi.mocked(api.readEntity).mockResolvedValue(salt({ known_by: "characters:mara" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await screen.findByText("Binds");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  const chip = await within(side).findByText("Mara");
  expect(chip.closest(".chip")?.tagName).toBe("SPAN");
  expect(chip.closest(".chip")).toHaveClass("chip", "on");
  expect(within(side).queryByRole("button", { name: "Mara" })).toBeNull();
});

describe("an adopt that completes after the scope has changed", () => {
  // The rail row, not the owner picker that lists the same lore.
  const ROW = { selector: ".row-name" };
  const listings = (s: { id: string }) => Promise.resolve(
    s.id === "w1" ? [{ id: "salt", name: "Salt" }] : [{ id: "hall", name: "Realm Hall" }]);

  test("per-entry Apply leaves the new scope's rail and screen alone", async () => {
    vi.mocked(api.listEntities).mockImplementation(listings);
    vi.mocked(api.readEntity).mockImplementation((s, _k, id) =>
      Promise.resolve(s.id === "w1"
        ? salt({ st_extensions: '{"sticky":2}' })
        : { meta: { id, name: "Realm Hall" }, body: "A hall.", rev: "r2" }));
    vi.mocked(api.previewAdoptSt).mockResolvedValue({ fields: { sticky: "2" }, unmapped: [] });
    const landed = deferred<{ applied: Record<string, string> }>();
    vi.mocked(api.adoptSt).mockReturnValue(landed.promise);
    const { rerender } = render(
      <Wrap wid="w1" scope={{ kind: "world", id: "w1" }} kind="lore" selected="salt" />);
    const banner = await screen.findByRole("status", { name: /imported settings/i });
    fireEvent.click(within(banner).getByRole("button", { name: /^apply$/i }));
    await waitFor(() => expect(api.adoptSt).toHaveBeenCalled());

    rerender(<Wrap isNew={false} wid="w2" scope={{ kind: "world", id: "w2" }} kind="lore" />);
    await screen.findByText("Realm Hall", ROW);
    const reads = vi.mocked(api.readEntity).mock.calls.length;
    landed.resolve({ applied: { sticky: "2" } });
    await new Promise((r) => setTimeout(r, 0));

    // The old scope's listing did not replace the rail, and its record was
    // neither re-read nor drawn over the new screen.
    expect(screen.getByText("Realm Hall", ROW)).toBeInTheDocument();
    expect(screen.queryByText("Salt", ROW)).toBeNull();
    expect(vi.mocked(api.readEntity).mock.calls.length).toBe(reads);
    expect(screen.queryByText("Binds")).toBeNull();
    expect(screen.queryByRole("status", { name: /imported settings/i })).toBeNull();
  });

  test("a per-entry failure from the old scope is not shown in the new one", async () => {
    vi.mocked(api.listEntities).mockImplementation(listings);
    vi.mocked(api.readEntity).mockResolvedValue(salt({ st_extensions: '{"sticky":2}' }));
    vi.mocked(api.previewAdoptSt).mockResolvedValue({ fields: { sticky: "2" }, unmapped: [] });
    const landed = deferred<never>();
    vi.mocked(api.adoptSt).mockReturnValue(landed.promise);
    const { rerender } = render(
      <Wrap wid="w1" scope={{ kind: "world", id: "w1" }} kind="lore" selected="salt" />);
    const banner = await screen.findByRole("status", { name: /imported settings/i });
    fireEvent.click(within(banner).getByRole("button", { name: /^apply$/i }));
    await waitFor(() => expect(api.adoptSt).toHaveBeenCalled());
    rerender(<Wrap isNew={false} wid="w2" scope={{ kind: "world", id: "w2" }} kind="lore" />);
    await screen.findByText("Realm Hall", ROW);
    landed.reject(new Error("boom"));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/boom/)).toBeNull();
  });

  test("adopt-all leaves the new scope's rail, note and busy state alone", async () => {
    vi.mocked(api.listEntities).mockImplementation(listings);
    const landed = deferred<{ applied: never[]; skipped: never[] }>();
    vi.mocked(api.adoptStAll).mockReturnValue(landed.promise);
    const { rerender } = render(
      <Wrap isNew={false} wid="w1" scope={{ kind: "world", id: "w1" }} kind="lore" />);
    fireEvent.click(await screen.findByRole("button", { name: "Apply imported settings to all" }));
    await waitFor(() => expect(api.adoptStAll).toHaveBeenCalled());

    rerender(<Wrap isNew={false} wid="w2" scope={{ kind: "world", id: "w2" }} kind="lore" />);
    await screen.findByText("Realm Hall", ROW);
    // The old request no longer holds the new scope's button.
    await waitFor(() => expect(
      screen.getByRole("button", { name: "Apply imported settings to all" })).not.toBeDisabled());
    landed.resolve({ applied: [], skipped: [] });
    await new Promise((r) => setTimeout(r, 0));

    expect(screen.getByText("Realm Hall", ROW)).toBeInTheDocument();
    expect(screen.queryByText("Salt", ROW)).toBeNull();
    expect(screen.queryByText(/^Applied to/)).toBeNull();
  });
});

