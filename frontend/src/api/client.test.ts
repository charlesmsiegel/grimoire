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
