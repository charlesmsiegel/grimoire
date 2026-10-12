import { act, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { ArtNavigationProvider } from "../art/ArtNavigationProvider";
import { CharacterArtRelated, type CharacterArtRelatedProps } from "./CharacterArtRelated";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { ...actual.api, getImageUsage: vi.fn() } };
});
function props(): CharacterArtRelatedProps {
  return { scope: { kind: "campaign", id: "Saltmarch" }, wid: "Realm", record: "Seraphine", version: "veiled",
    appearances: [{ gid: "opening", greeting_name: "Saltmarch", name: "gallery_1", url: "/g/art", thumb: "/g/thumb" },
      { gid: "remote", greeting_name: "Realm", name: "https://example.test/art.png", url: "https://example.test/art.png", copyable: false }],
    baseVersions: [{ id: "main", name: "main", images: ["avatar"], image_v: { avatar: "world" }, image_descriptions: { avatar: "A portrait" } }],
    manage: true, busy: false, fallbackFocus: null, onCopy: vi.fn(), onLocalize: vi.fn(),
    localizeProg: null, localizeMsg: null, greetingHref: (gid) => "/greetings/" + gid };
}
test("greeting source viewing and copyability remain separate from record art", () => {
  const p = props();
  render(<MemoryRouter><CharacterArtRelated {...p} /></MemoryRouter>);
  const local = screen.getByRole("img", { name: "Saltmarch art" }).closest(".shelf-tile")!;
  expect(within(local as HTMLElement).getByRole("link", { name: "Saltmarch art" })).toHaveAttribute("href", "/g/art");
  fireEvent.click(within(local as HTMLElement).getByRole("button", { name: "Add to gallery" }));
  expect(p.onCopy).toHaveBeenCalledWith(p.appearances[0], "gallery");
  expect(screen.getByRole("link", { name: "Saltmarch" })).toHaveAttribute("href", "/greetings/opening");
  const remote = screen.getByRole("img", { name: "Realm art" }).closest(".shelf-tile")!;
  expect(within(remote as HTMLElement).queryByRole("button")).not.toBeInTheDocument();
});
test("other world versions use the common viewer without mutation controls", () => {
  const p = props();
  render(<MemoryRouter><CharacterArtRelated {...p} /></MemoryRouter>);
  const section = screen.getByRole("region", { name: "Other world versions" });
  expect(within(section).getAllByRole("button")).toHaveLength(1);
  fireEvent.click(within(section).getByRole("button", { name: "View avatar · main" }));
  expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", "/api/worlds/Realm/characters/Seraphine/versions/main/images/avatar?v=world");
});
test("localization keeps its progress and belongs to explicit management", () => {
  const p = { ...props(), scope: { kind: "world" as const, id: "Realm" }, localizeProg: { done: 2, total: 3 }, localizeMsg: "Localized 2 images" };
  const view = render(<MemoryRouter><CharacterArtRelated {...p} /></MemoryRouter>);
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "2");
  expect(screen.getByRole("button", { name: "Localizing…" })).toBeDisabled();
  view.rerender(<MemoryRouter><CharacterArtRelated {...p} manage={false} localizeProg={null} /></MemoryRouter>);
  expect(screen.queryByRole("button", { name: "Localize images" })).not.toBeInTheDocument();
  expect(screen.getByText("Localized 2 images")).toBeInTheDocument();
});

test("other-version art uses the supplied draft guard and cannot open while busy", () => {
  const p = props(), guard = vi.fn();
  const view = render(<MemoryRouter><CharacterArtRelated {...p} onView={guard} /></MemoryRouter>);
  fireEvent.click(within(screen.getByRole("region", { name: "Other world versions" })).getByRole("button"));
  expect(guard).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  view.rerender(<MemoryRouter><CharacterArtRelated {...p} busy onView={guard} /></MemoryRouter>);
  expect(within(screen.getByRole("region", { name: "Other world versions" })).getByRole("button")).toBeDisabled();
});


test("related-art refresh replaces viewer metadata and reports a removed selection", async () => {
  const p = props();
  const view = render(<MemoryRouter><CharacterArtRelated {...p} /></MemoryRouter>);
  fireEvent.click(within(screen.getByRole("region", { name: "Other world versions" })).getByRole("button"));
  view.rerender(<MemoryRouter><CharacterArtRelated {...p} baseVersions={[{ ...p.baseVersions[0], image_v: { avatar: "fresh" } }]} /></MemoryRouter>);
  expect(screen.getByTestId("viewer-image")).toHaveAttribute("src", expect.stringContaining("v=fresh"));
  view.rerender(<MemoryRouter><CharacterArtRelated {...p} baseVersions={[]} /></MemoryRouter>);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(screen.getByRole("status")).toHaveTextContent("This image is no longer available.");
});

test("accepted same-record Back retires the related-version viewer", async () => {
  let navigate!: ReturnType<typeof useNavigate>;
  const p = props();
  function Surface() {
    navigate = useNavigate();
    return <CharacterArtRelated {...p} />;
  }
  render(<MemoryRouter initialEntries={["/?tab=art", "/?tab=art&v=veiled"]}>
    <ArtNavigationProvider><Surface /></ArtNavigationProvider>
  </MemoryRouter>);
  fireEvent.click(within(screen.getByRole("region", { name: "Other world versions" })).getByRole("button"));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  act(() => navigate(-1));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
