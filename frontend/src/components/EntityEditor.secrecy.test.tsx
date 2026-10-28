import { asSecrecy } from "./entityEditor/presentation";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { Wrap, renderEditor, installEntityMocks } from "../testkit/entityEditorHarness";

vi.mock("../api/client", async () => (await import("../testkit/entityEditorMocks")).entityApiMock());
beforeEach(installEntityMocks);

test("creates an entry with a chosen secrecy level", async () => {
  render(<Wrap wid="w" kind="lore" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "The Twist" } });
  fireEvent.click(screen.getByRole("radio", { name: "Secret" }));
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore",
      expect.objectContaining({ name: "The Twist", secrecy: "secret" })),
  );
});

test("the secrecy picker offers all three levels and starts on public", async () => {
  render(<Wrap wid="w" kind="lore" />);
  await screen.findByLabelText("Name");
  const group = screen.getByRole("radiogroup", { name: "Secrecy" });
  expect(within(group).getAllByRole("radio").map((r) => r.getAttribute("value")))
    .toEqual(["public", "secret", "gm-only"]);
  expect(within(group).getByRole("radio", { name: "Public" })).toBeChecked();
});

test("the detail sidebar badges a secret entry and the form opens on its level", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: "secret" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "twist", name: "Twist", secrecy: "secret" }, body: "the harbourmaster did it" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="twist" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Secrecy")).toBeInTheDocument();
  expect(within(side).getByText("Secret")).toBeInTheDocument();
  // Edit opens the form already on the stored level, so a save can't downgrade it
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByRole("radio", { name: "Secret" })).toBeChecked();
});

test("an unmarked entry reads as public in the sidebar", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({ meta: { id: "salt", name: "Salt" }, body: "x" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Public")).toBeInTheDocument();
});

test("the rail badges non-public rows only", async () => {
  vi.mocked(api.listEntities).mockImplementation((_s, kind) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "a", name: "Open" },
      { id: "b", name: "Twist", secrecy: "secret" },
      { id: "c", name: "Note", secrecy: "gm-only" },
    ]));
  const { container } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  await screen.findByText("Twist");
  const rail = container.querySelector(".entity-grid") as HTMLElement;
  expect(within(rail).getByText("Secret")).toBeInTheDocument();
  expect(within(rail).getByText("GM-only")).toBeInTheDocument();
  expect(within(rail).queryByText("Public")).toBeNull();
});

test("switching a viewed entry back to public sends 'public', clearing the level", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: "gm-only" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "twist", name: "Twist", secrecy: "gm-only" }, body: "x" }));
  render(<Wrap wid="w" kind="lore" selected="twist" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("radio", { name: "Public" }));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "twist",
      expect.objectContaining({ secrecy: "public" })),
  );
});

test("'+ New' after viewing a secret entry does not inherit its level", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: "secret" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "twist", name: "Twist", secrecy: "secret" }, body: "x" }));
  const { rerender } = render(<Wrap wid="w" kind="lore" selected="twist" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  rerender(<Wrap wid="w" kind="lore" selected={null} />);
  expect(screen.getByRole("radio", { name: "Public" })).toBeChecked();
});

test("a hand-edited secrecy value is read the way the backend reads it", async () => {
  // `store.entities.normalize_secrecy` trims and lowercases, because frontmatter
  // is hand-editable. Matching only the canonical spelling here badged the entry
  // Public and made the next save send "public" — a valid level, so the route
  // accepted it and the entry was silently published.
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "twist", name: "Twist", secrecy: " Secret " }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "twist", name: "Twist", secrecy: " Secret " }, body: "x" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="twist" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Secret")).toBeInTheDocument();
  expect(within(side).queryByText("Public")).toBeNull();

  // and an unrelated edit must not downgrade it on the way back out
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByRole("radio", { name: "Secret" })).toBeChecked();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "twist",
      expect.objectContaining({ body: "edited", secrecy: "secret" })),
  );
});

test("an unrecognised secrecy value still reads as public", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "typo", name: "Typo", secrecy: "sercet" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "typo", name: "Typo", secrecy: "sercet" }, body: "x" }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="typo" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Public")).toBeInTheDocument();
});

// ---- per-record prompt cost (#51) ----

test("rail rows carry the record's token count", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([
    { id: "salt", name: "Salt", tokens: 1240 },
    { id: "brine", name: "Brine", tokens: 7 },
  ]);
  const { container } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  await screen.findByText("Salt");
  const counts = Array.from(container.querySelectorAll(".row-tokens")).map((n) => n.textContent);
  expect(counts).toEqual(["1,240 tokens", "7 tokens"]);
  // The visible and accessible labels both carry the unit.
  expect(screen.getByRole("link", { name: /Salt 1,240 tokens/ })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Brine 7 tokens/ })).toBeInTheDocument();
});

test("a zero-token record still shows its count, in the rail and the detail", async () => {
  // 0 is falsy: a `{e.tokens && ...}` guard would silently blank an empty
  // record's badge, which reads identically to the field being absent.
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "stub", name: "Stub", tokens: 0 }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "stub", name: "Stub" }, body: "", tokens: 0 }));
  const browse = render(<Wrap wid="w" kind="lore" isNew={false} />);
  await within(browse.container.querySelector(".entity-grid") as HTMLElement).findByText("Stub");
  expect(browse.container.querySelector(".row-tokens")!.textContent).toBe("0 tokens");
  browse.unmount();
  const { container } = render(<Wrap wid="w" kind="lore" selected="stub" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".token-badge")!.textContent).toBe("0 tokens");
});

test("a row with no token count renders no badge", async () => {
  // a payload from before the field existed must not render "undefined" or 0
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  const { container } = render(<Wrap isNew={false} wid="w" kind="lore" />);
  await screen.findByText("Salt");
  expect(container.querySelector(".row-tokens")).toBeNull();
});

test("the detail header and sidebar report the cost, keyed as per-activation", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 42 }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "salt", name: "Salt", keys: "pact" }, body: "Binds", tokens: 42 }));
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".detail-main h3 .token-badge")!.textContent).toBe("42 tokens");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText("Context cost")).toBeInTheDocument();
  expect(within(side).getByText("42 tokens")).toBeInTheDocument();
  expect(within(side).getByText(/these keys activate/i)).toBeInTheDocument();
});

test("a keyless lore entry is described as always-on, a keyless location as the setting", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 3 }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "salt", name: "Salt" }, body: "Binds", tokens: 3 }));
  const lore = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const loreSide = lore.container.querySelector(".detail-sidebar") as HTMLElement;
  // "always-on" alone also matches the Keys hint above it, so match the sentence
  expect(within(loreSide).getByText(/charged on every turn/i)).toBeInTheDocument();
  expect(within(loreSide).getByText("3 tokens")).toBeInTheDocument();  // singular only at 1
  lore.unmount();

  // a keyless LOCATION never joins world info; it is charged as the setting
  const loc = render(<Wrap wid="w" kind="locations" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const locSide = loc.container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(locSide).getByText(/current setting/i)).toBeInTheDocument();
  expect(within(locSide).queryByText(/charged on every turn/i)).toBeNull();
});

test("the header badge clears when the form is reset for a new record", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 42 }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "salt", name: "Salt" }, body: "Binds", tokens: 42 }));
  const { container, rerender } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(container.querySelector(".token-badge")).not.toBeNull());
  rerender(<Wrap wid="w" kind="lore" selected={null} />);
  expect(container.querySelector(".token-badge")).toBeNull();
});

test("a GM-only record's count is marked as never charged, not as a live cost", async () => {
  // #49 drops a gm-only body before every activation rule, so the number is
  // what it WOULD cost. Showing it plain would read as a standing charge.
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "vault", name: "Vault", tokens: 88, secrecy: "gm-only" }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "vault", name: "Vault", secrecy: "gm-only" }, body: "Behind the seawall.", tokens: 88 }));
  const browse = render(<Wrap wid="w" kind="lore" isNew={false} />);
  // by role, not by text: the same mock feeds `loreOwnerOptions`, so "Vault"
  // is also an owner checkbox label further down the form
  await screen.findByRole("link", { name: /Vault.*88 tokens/ });
  expect(browse.container.querySelector(".row-tokens")!.className).toContain("never-charged");
  // Struck-through text does not announce as struck, so the name has to say it.
  // The row also carries #49's own GM-only tag, hence the gap in the middle --
  // asserted loosely so that tag's wording stays that feature's business.
  expect(screen.getByRole("link", { name: /Vault.*88 tokens, never charged/ })).toBeInTheDocument();
  browse.unmount();

  const { container } = render(<Wrap wid="w" kind="lore" selected="vault" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".token-badge")!.className).toContain("never-charged");
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/never charged/i)).toBeInTheDocument();
});

test("a public record's count carries no never-charged marking", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "salt", name: "Salt", tokens: 88 }]);
  vi.mocked(api.readEntity).mockResolvedValue(fixture({
    meta: { id: "salt", name: "Salt" }, body: "Binds", tokens: 88 }));
  const browse = render(<Wrap wid="w" kind="lore" isNew={false} />);
  await screen.findByRole("link", { name: /Salt.*88 tokens/ });
  expect(browse.container.querySelector(".row-tokens")!.className).not.toContain("never-charged");
  browse.unmount();
  const { container } = render(<Wrap wid="w" kind="lore" selected="salt" />);
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  expect(container.querySelector(".token-badge")!.className).not.toContain("never-charged");
});

// Hand-written JSON sometimes wraps the legacy scalar in singleton arrays.
// Editing prose must never turn a previously recognized private value public.
test.each([
  { value: " Secret ", label: "Secret", level: "secret" },
  { value: [" GM-ONLY "], label: "GM-only", level: "gm-only" },
  { value: [["secret"]], label: "Secret", level: "secret" },
  { value: {}, label: "Public", level: "public" },
  { value: ["secret", "public"], label: "Public", level: "public" },
  { value: [{ secrecy: "secret" }], label: "Public", level: "public" },
  { value: null, label: "Public", level: "public" },
])("legacy secrecy $value retains its audience when prose is edited", async ({ value, label, level }) => {
  const record = fixture<Awaited<ReturnType<typeof api.readEntity>>>({
    meta: { id: "salt", name: "Salt" }, body: "x", rev: "r1",
  });
  // Deliberately malformed historical wire input at the runtime boundary.
  Object.assign(record.meta, { secrecy: value });
  vi.mocked(api.readEntity).mockResolvedValue(record);
  renderEditor({ kind: "lore", selected: "salt" });
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.getByRole("radio", { name: label })).toBeChecked();
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "edited" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateEntity).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, "lore", "salt", expect.objectContaining({ secrecy: level, body: "edited" })));
});

test("cyclic and object secrecy inputs fall back safely without invoking object coercion", () => {
  const cycle: unknown[] = []; cycle.push(cycle);
  const coercion = vi.fn(() => "secret");
  expect(asSecrecy(cycle)).toBe("public");
  expect(asSecrecy({ toString: coercion })).toBe("public");
  expect(coercion).not.toHaveBeenCalled();
});
