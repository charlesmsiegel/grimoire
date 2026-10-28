import type { ComponentProps } from "react";
import { MemoryRouter } from "react-router-dom";
import { render } from "@testing-library/react";
import { GreetingEditor } from "../components/GreetingEditor";
import { ApiError, api } from "../api/client";
import type { Greeting } from "../api/types";
import { fixture } from "./fixture";

export type Props = ComponentProps<typeof GreetingEditor>;
export const fail = (status: number, detail: string, kind?: string,
                     body?: Record<string, unknown>) => new ApiError(status, detail, kind, body);

export function installGreetingMocks() {
  vi.clearAllMocks();
  vi.mocked(api.getGreetingSubjects).mockResolvedValue({});
  vi.mocked(api.setImageSubjects).mockResolvedValue({ ok: true });
  vi.mocked(api.listUntaggedImages).mockResolvedValue([]);
  vi.mocked(api.listGreetings).mockResolvedValue([]);
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]);
  vi.mocked(api.listTags).mockResolvedValue({ vip: "VIP" });
  vi.mocked(api.listEntities).mockResolvedValue([
    { id: "counting-house", name: "The Counting House" },
    { id: "the-quay", name: "The Quay" },
  ]);
  vi.mocked(api.createGreeting).mockResolvedValue({ id: "open" });
  vi.mocked(api.setEdges).mockResolvedValue({ ok: true });
  vi.mocked(api.importGreetings).mockResolvedValue({ greetings: ["g1"] });
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  vi.mocked(api.updateGreeting).mockResolvedValue({ ok: true });
}

/** The generic harness the bulk of this suite renders through now that
 *  selection is a prop rather than internal click state -- the same shape as
 *  `EntityEditor`'s `Wrap`. `sectionPath` and `recordHref` are dummy
 *  placeholders here on purpose: these tests assert on what the editor does
 *  with a record already selected (or with a route change simulated via
 *  `rerender`), never on where its links point. The two tests about the
 *  links themselves render through `renderGreetings` below instead, which
 *  carries real values. */
export function Wrap(props: Partial<Props> & { scope: Props["scope"]; wid: string }) {
  return (
    <MemoryRouter>
      <GreetingEditor isNew sectionPath="/section" recordHref={(r) => `/section/${r}`} {...props} />
    </MemoryRouter>
  );
}

export const GREETINGS_DEFAULTS = {
  isNew: true,
  wid: "realm", scope: { kind: "world" as const, id: "realm" },
  sectionPath: "/worlds/realm/greetings",
  recordHref: (r: string) => `/worlds/realm/greetings/${r}`,
};

export const greetingsWith = (p: Partial<Props> = {}) => (
  <MemoryRouter><GreetingEditor {...GREETINGS_DEFAULTS} {...p} /></MemoryRouter>);
export const renderGreetings = (p: Partial<Props> = {}) => render(greetingsWith(p));

export const greetingFixture = (id: string, name: string) => ({
  meta: { id, name, character: "", version: "", present: [] as string[], requires_tags: [] as string[],
          predecessor_join: "all" as const },
  body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
});

/** A promise plus the handle to settle it later, so a test can hold one
 *  scope's read open while another lands. */
export function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

export function greeting(id: string, name: string, character: string, present: string[], mark?: Greeting["mark"]) {
  return { id, name, character, version: "default", present, requires_tags: [],
           predecessor_join: "all" as const, ...(mark ? { mark } : {}) };
}
export const IMG_BODY = "scene ![M](/api/worlds/w/greetings/open/images/embed-aaa111bbb222)";

export function mockOpenWithImage(subjects: Record<string, string[]> = {}) {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
    body: IMG_BODY, edges: { leads_to: [], excludes: [] }, predecessors: [],
  }));
  vi.mocked(api.getGreetingSubjects).mockResolvedValue(subjects);
}

