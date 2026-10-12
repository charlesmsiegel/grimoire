import { api } from "./client";

afterEach(() => { vi.unstubAllGlobals(); });

test.each(["world", "campaign"] as const)("membership encodes %s scope names", async (kind) => {
  const mocked = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ groups: [], by_actor: {}, digest: "" }) });
  vi.stubGlobal("fetch", mocked);
  const scope = { kind, id: "realm#1" };
  await api.getMembership(scope);
  expect(mocked.mock.calls[0][0]).toBe(`/api/${kind === "world" ? "worlds" : "campaigns"}/realm%231/membership`);
});

test.each(["world", "campaign"] as const)("actor membership encodes %s scope and actor separately", async (kind) => {
  const mocked = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ groups: [], actor: "characters:mara#1" }) });
  vi.stubGlobal("fetch", mocked);
  await api.getActorMembership({ kind, id: "realm#1" }, "characters:mara#1");
  expect(mocked.mock.calls[0][0]).toBe(`/api/${kind === "world" ? "worlds" : "campaigns"}/realm%231/membership?actor=characters%3Amara%231`);
});
