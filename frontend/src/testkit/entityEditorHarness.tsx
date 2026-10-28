import type { ComponentProps } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { render } from "@testing-library/react";
import { EntityEditor } from "../components/EntityEditor";
import { ApiError, api } from "../api/client";
import type { EntityDetail } from "../api/types";
import { fixture } from "./fixture";
import { artRead } from "./recordArt";

// Spies the address `useNavigate` actually lands the router on -- the create
// paths (instantiate, the sheet wizard) fix a bug where the record was opened
// with the URL still naming the section root, so these are read back rather
// than assumed.
export const navigationProbe = { path: "" };
export function PathSpy() {
  const location = useLocation(); navigationProbe.path = location.pathname + location.search;
  return null;
}

export const fail = (status: number, detail: string, kind?: string,
              body?: Record<string, unknown>) =>
  new (ApiError)(status, detail, kind, body);

export function installEntityMocks() {
  vi.mocked(api.previewAdoptStAll).mockResolvedValue({ pending: 1 });
  vi.clearAllMocks();
  vi.mocked(api.readRecordArt).mockImplementation(async (target) => artRead(target, []));
  vi.mocked(api.getGroupState).mockResolvedValue({ goals: "", resources: "", focus: "", public_perception: "", secrets: "", updated: "" });
  vi.mocked(api.promoteRecordArt).mockResolvedValue({ ok: true });
  vi.mocked(api.setRecordArtDescription).mockResolvedValue({ ok: true });

  vi.mocked(api.listEntities).mockResolvedValue([]);
  vi.mocked(api.createEntity).mockResolvedValue({ id: "e1" });
  vi.mocked(api.updateEntity).mockResolvedValue({ ok: true });
  vi.mocked(api.deleteEntity).mockResolvedValue({ ok: true });
  vi.mocked(api.reclassifyEntity).mockResolvedValue({ id: "salt", campaigns: [] });
  vi.mocked(api.readEntity).mockResolvedValue({ meta: { id: "salt", name: "Salt", keys: "pact" }, body: "x", rev: "r1" });
  vi.mocked(api.previewAdoptSt).mockResolvedValue({ fields: {}, unmapped: [] });
  vi.mocked(api.adoptSt).mockResolvedValue({ applied: {} });
  vi.mocked(api.adoptStAll).mockResolvedValue({ applied: [], skipped: [] });
  vi.mocked(api.listCharacters).mockResolvedValue(fixture([{ id: "tanaka", name: "Tanaka" }]));
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listClimates).mockResolvedValue({ climates: [
    { id: "temperate-interior", name: "Temperate interior", builtin: true, custom: false },
    { id: "saltmarch-fog", name: "Saltmarch fog", builtin: false, custom: true },
  ] });
  vi.mocked(api.listEntityImages).mockResolvedValue([]);
  vi.mocked(api.setEntityImageDescription).mockResolvedValue({ ok: true });
  vi.mocked(api.getSheet).mockResolvedValue({ sheet: null });
  vi.mocked(api.readModuleContent).mockRejectedValue(new Error("not found"));
  vi.mocked(api.instantiateContent).mockResolvedValue({ id: "e1" });
}

export type Props = ComponentProps<typeof EntityEditor>;

/** The generic harness the bulk of this suite renders through now that
 *  selection is a prop rather than internal click state. `sectionPath` and
 *  `recordHref` are dummy placeholders here on purpose -- these tests assert
 *  on what the editor does with a record already selected (or with a route
 *  change simulated via `rerender`), never on where its links point. The two
 *  tests about the links themselves supply their own values below. A bare
 *  `MemoryRouter` is enough: nothing here reads the URL back, so there is no
 *  need for the `Routes`/`useParams` machinery a real click-driven navigation
 *  would require. */
export function Wrap(props: Partial<Props> & { kind: Props["kind"]; wid: string }) {
  return (
    <MemoryRouter>
      <PathSpy />
      <EntityEditor sectionPath="/section" newHref="/section?new=1" isNew recordHref={(r) => `/section/${r}`} {...props} />
    </MemoryRouter>
  );
}

export const DEFAULTS = {
  wid: "realm", scope: { kind: "world" as const, id: "realm" },
  sectionPath: "/worlds/realm/items",
  // The shared defaults open the blank form on the root, as the editor did
  // before the root became the browse grid (#437). A test about the grid
  // says `isNew={false}`.
  isNew: true,
  newHref: "/worlds/realm/items?new=1",
  recordHref: (r: string) => `/worlds/realm/items/${r}`,
};
export const editorWith = (p: Partial<Props> & { kind: Props["kind"] }) => (
  <MemoryRouter><EntityEditor {...DEFAULTS} {...p} /></MemoryRouter>);
export const renderEditor = (p: Partial<Props> & { kind: Props["kind"] }) => render(editorWith(p));

/** A promise plus the handle to settle it later, so a test can hold one
 *  scope's listing open while another lands. */
export function deferred<T = EntityDetail>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
