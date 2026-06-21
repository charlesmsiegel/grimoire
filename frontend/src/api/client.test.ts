import { api } from "./client";

function sseResponse(chunks: string[]) {
  let i = 0;
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () =>
            i < chunks.length
              ? { value: new TextEncoder().encode(chunks[i++]), done: false }
              : { value: undefined, done: true },
        };
      },
    },
  };
}

test("renameConversation PUTs the new title", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: "c1", title: "New" }) });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.renameConversation("c1", "New");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/conversations/c1",
    expect.objectContaining({ method: "PUT", body: JSON.stringify({ title: "New" }) }),
  );
});

test("deleteConversation issues DELETE", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.deleteConversation("c1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/conversations/c1",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("retry posts to the retry endpoint and forwards SSE events", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(sseResponse(['data: {"delta":"hi"}\n\n', 'data: {"done":true}\n\n']));
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const events: unknown[] = [];
  await api.retry("c1", (e) => events.push(e));

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/conversations/c1/retry",
    expect.objectContaining({ method: "POST" }),
  );
  expect(events).toEqual([{ delta: "hi" }, { done: true }]);
});
