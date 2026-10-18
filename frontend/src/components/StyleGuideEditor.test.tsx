import { MemoryRouter, useLocation } from "react-router-dom";
import { render as renderRTL, act, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { StyleGuideEditor } from "./StyleGuideEditor";

function Address() { return <output aria-label="Address">{useLocation().search}</output>; }
const render = (element: React.ReactElement, entry = "/") => renderRTL(
  <MemoryRouter initialEntries={[entry]}>{element}<Address /></MemoryRouter>);

vi.mock("../api/client", () => ({
  api: {
    listStyles: vi.fn(), readStyle: vi.fn(), createStyle: vi.fn(),
    updateStyle: vi.fn(), deleteStyle: vi.fn(), duplicateStyle: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listStyles).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: ["horror"], built_in: true },
    { id: "cozy-mystery", name: "Cozy Mystery", description: "Gentle.", tags: ["cozy"], built_in: false },
  ]);
  vi.mocked(api.readStyle).mockImplementation((sid) => Promise.resolve(
    sid === "gothic-horror"
      ? { meta: { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: ["horror"], built_in: true },
          body: "Atmosphere first." }
      : { meta: { id: "cozy-mystery", name: "Cozy Mystery", description: "Gentle.", tags: ["cozy"], built_in: false },
          body: "Keep it warm." }));
  vi.mocked(api.createStyle).mockResolvedValue({ id: "new-style" });
  vi.mocked(api.updateStyle).mockResolvedValue({ ok: true });
  vi.mocked(api.deleteStyle).mockResolvedValue({ ok: true });
  vi.mocked(api.duplicateStyle).mockResolvedValue({ id: "gothic-horror-copy" });
});

test("clicking a style shows a read-only view; built-in shows Duplicate not Edit", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gothic Horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror"));
  expect(screen.getByText("Atmosphere first.")).toBeInTheDocument();
  expect(container.querySelector("textarea")).toBeNull();
  expect(screen.getByRole("button", { name: /duplicate to customize/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
});

test("a custom style shows Edit, which reveals the form", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cozy Mystery"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("cozy-mystery"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});

test("+ New opens the form directly and creates a style", async () => {
  render(<StyleGuideEditor />);
  await screen.findByRole("link", { name: /new style guide/i });
  fireEvent.click(screen.getByRole("link", { name: /new style guide/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Space Opera" } });
  fireEvent.change(screen.getByLabelText("Guide text"), { target: { value: "Big, bold, cosmic." } });
  fireEvent.click(screen.getByRole("button", { name: /create style guide/i }));
  await waitFor(() => expect(api.createStyle).toHaveBeenCalledWith(
    expect.objectContaining({ name: "Space Opera", body: "Big, bold, cosmic." })));
});

test("duplicating a built-in style opens the new copy for editing", async () => {
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gothic Horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror"));
  vi.mocked(api.readStyle).mockResolvedValueOnce({
    meta: { id: "gothic-horror-copy", name: "Gothic Horror (copy)", description: "Dread.", tags: ["horror"], built_in: false },
    body: "Atmosphere first.",
  });
  fireEvent.click(screen.getByRole("button", { name: /duplicate to customize/i }));
  await waitFor(() => expect(api.duplicateStyle).toHaveBeenCalledWith("gothic-horror"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("gothic-horror-copy"));
});

test("deleting a custom style removes it", async () => {
  const original = window.confirm;
  window.confirm = () => true;
  const { container } = render(<StyleGuideEditor />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Cozy Mystery"));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("cozy-mystery"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteStyle).toHaveBeenCalledWith("cozy-mystery"));
  window.confirm = original;
});


test("the root browses without a blank draft and New can be cancelled without a write", async () => {
  render(<StyleGuideEditor />);
  expect(await screen.findByText(/Choose a style guide to read/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "+ New style guide" }));
  expect(await screen.findByLabelText("Name")).toHaveValue("");
  expect(screen.getByLabelText("Address")).toHaveTextContent("?new=1");
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  expect(await screen.findByText(/Choose a style guide to read/)).toBeInTheDocument();
  expect(api.createStyle).not.toHaveBeenCalled();
});

test("a bookmarked record opens read-only", async () => {
  render(<StyleGuideEditor />, "/?id=cozy-mystery");
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledWith("cozy-mystery"));
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
});


test("a Cancel reload refusal preserves the style draft and reports the failure", async () => {
  render(<StyleGuideEditor />, "/?id=cozy-mystery");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  vi.mocked(api.readStyle).mockRejectedValueOnce({ detail: "Style reload refused" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("Style reload refused")).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
});

test("a Cancel reload cannot replace another addressed style", async () => {
  render(<StyleGuideEditor />, "/?id=cozy-mystery");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  let release: (v: Awaited<ReturnType<typeof api.readStyle>>) => void = () => {};
  vi.mocked(api.readStyle).mockReturnValueOnce(new Promise((r) => { release = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("link", { name: /Gothic Horror/ }));
  await screen.findByText("Atmosphere first.");
  await act(async () => release({ meta: { id: "cozy-mystery", name: "Realm stale", description: "", tags: [], built_in: false }, body: "Old cancel response." }));
  expect(screen.getByText("Atmosphere first.")).toBeInTheDocument();
  expect(screen.queryByText("Old cancel response.")).toBeNull();
});

test.each([false, true])("review regression: a retired style create cannot reclaim a fresh form, refused=%s", async (refused) => {
  let finish: () => void = () => {};
  vi.mocked(api.createStyle).mockImplementationOnce(() => new Promise((resolve, reject) => {
    finish = () => refused ? reject(new Error("Old style refused")) : resolve({ id: "realm" });
  }));
  render(<StyleGuideEditor />, "/?new=1");
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: "Create style guide" }));
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("link", { name: "+ New style guide" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Saltmarch" } });
  await act(async () => finish());
  expect(screen.getByLabelText("Name")).toHaveValue("Saltmarch");
  expect(screen.getByLabelText("Address")).toHaveTextContent("?new=1");
  expect(screen.queryByText(/Old style refused/)).not.toBeInTheDocument();
});

test("review regression: a refused style Cancel preserves the draft after its older save commits", async () => {
  let finish: () => void = () => {};
  vi.mocked(api.updateStyle).mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve({ ok: true }); }));
  render(<StyleGuideEditor />, "/?id=cozy-mystery");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save style guide" }));
  vi.mocked(api.readStyle).mockRejectedValueOnce({ detail: "Cancel style refused" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByText("Cancel style refused");
  await act(async () => finish());
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
  expect(screen.getByText("Cancel style refused")).toBeInTheDocument();
});

test("review regression: an owned style follow-up read cannot navigate after another selection", async () => {
  render(<StyleGuideEditor />, "/?id=cozy-mystery");
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  let release: (value: Awaited<ReturnType<typeof api.readStyle>>) => void = () => {};
  vi.mocked(api.readStyle).mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Save style guide" }));
  await waitFor(() => expect(api.readStyle).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("link", { name: /Gothic Horror/ }));
  await screen.findByText("Atmosphere first.");
  await act(async () => release({ meta: { id: "cozy-mystery", name: "Realm stale", description: "", tags: [], built_in: false }, body: "Old save body" }));
  expect(screen.getByText("Atmosphere first.")).toBeInTheDocument();
  expect(screen.getByLabelText("Address")).toHaveTextContent("id=gothic-horror");
});
