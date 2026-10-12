import { act, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, type ActorMembershipResponse } from "../api/client";
import { ActorGroups } from "./ActorGroups";

vi.mock("../api/client", () => ({ api: { getActorMembership: vi.fn() } }));

const scope = { kind: "world", id: "realm" } as const;
const answer: ActorMembershipResponse = { actor: "characters:seraphine", groups: [
  { ref: "groups:salt-circle", name: "Salt Circle", secrecy: "public", roles: ["leader"] },
] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => { vi.resetAllMocks(); });

test.each(["leader", "both"])("%s gives one read-only leads chip", async (roles) => {
  vi.mocked(api.getActorMembership).mockResolvedValue({ ...answer, groups: [
    { ...answer.groups[0], roles: roles === "leader" ? ["leader"] : ["leader", "member"] },
  ] });
  render(<MemoryRouter><ActorGroups scope={scope} actor={answer.actor} /></MemoryRouter>);
  expect(await screen.findAllByRole("link", { name: "Salt Circle (leads)" })).toHaveLength(1);
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});

test.each(["empty", "failure"])("%s hides Groups quietly", async (state) => {
  if (state === "empty") vi.mocked(api.getActorMembership).mockResolvedValue({ actor: answer.actor, groups: [] });
  else vi.mocked(api.getActorMembership).mockRejectedValue(new Error("unavailable"));
  await act(async () => { render(<MemoryRouter><ActorGroups scope={scope} actor={answer.actor} /></MemoryRouter>); });
  expect(screen.queryByText("Groups")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a changed scope synchronously hides old chips until its own answer arrives", async () => {
  vi.mocked(api.getActorMembership).mockResolvedValueOnce(answer);
  const next = deferred<ActorMembershipResponse>();
  vi.mocked(api.getActorMembership).mockReturnValueOnce(next.promise);
  const view = render(<MemoryRouter><ActorGroups scope={scope} actor={answer.actor} /></MemoryRouter>);
  await screen.findByRole("link", { name: "Salt Circle (leads)" });
  view.rerender(<MemoryRouter><ActorGroups scope={{ kind: "campaign", id: "saltmarch" }} actor={answer.actor} /></MemoryRouter>);
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  await act(async () => { next.resolve(answer); });
  expect(screen.getByRole("link")).toHaveAttribute("href", "/campaigns/saltmarch/world/groups/salt-circle");
});

test.each(["success", "failure"])("late old %s cannot replace the new actor's section", async (outcome) => {
  const old = deferred<ActorMembershipResponse>();
  vi.mocked(api.getActorMembership).mockReturnValueOnce(old.promise);
  vi.mocked(api.getActorMembership).mockResolvedValueOnce({ actor: "pcs:mara", groups: [
    { ref: "groups:saltmarch", name: "Saltmarch", secrecy: "public", roles: ["member"] },
  ] });
  const view = render(<MemoryRouter><ActorGroups scope={scope} actor={answer.actor} /></MemoryRouter>);
  view.rerender(<MemoryRouter><ActorGroups scope={{ kind: "campaign", id: "saltmarch" }} actor="pcs:mara" /></MemoryRouter>);
  await screen.findByRole("link", { name: "Saltmarch" });
  await act(async () => { if (outcome === "success") old.resolve(answer); else old.reject(new Error("unavailable")); });
  expect(screen.getByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByText("Salt Circle (leads)")).not.toBeInTheDocument();
});
