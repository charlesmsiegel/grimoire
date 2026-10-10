import { act, renderHook, waitFor } from "@testing-library/react";
import { api } from "../../api/client";
import { useModelList } from "./useModelList";

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return { ...actual, api: { readConnectionCapabilities: vi.fn() } };
});

beforeEach(() => { vi.clearAllMocks(); });

test("a re-ask that clears the list clears a failed read's error too", async () => {
  vi.mocked(api.readConnectionCapabilities).mockRejectedValueOnce(new Error("offline"));
  const { result } = renderHook(() => useModelList("saltmarch", ["generate"], ""));
  await waitFor(() => expect(result.current.failed).not.toBeNull());
  // The re-ask (a test landing) stays in flight: the stale error must not.
  vi.mocked(api.readConnectionCapabilities).mockReturnValue(new Promise(() => {}));
  act(() => { result.current.reask(true); });
  expect(result.current.failed).toBeNull();
  expect(result.current.listed).toBeNull();
});
