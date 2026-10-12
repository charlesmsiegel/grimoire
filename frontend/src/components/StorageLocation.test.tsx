import { cleanup, render, screen } from "@testing-library/react";
import { StorageLocation } from "./StorageLocation";
import { api } from "../api/client";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: { getDataDir: vi.fn(), putDataDir: vi.fn() },
}));

afterEach(cleanup);

// Spec 03 asks the sharing-boundary text to name every residual a delete
// leaves, and to say the per-device compiled cache need not be synced.
test("the storage hint states the cache's sharing boundary and residuals", async () => {
  vi.mocked(api.getDataDir).mockResolvedValue(
    { data_dir: "/library", default: "/library", is_default: true, source: "default", exists: true });
  const { container } = render(<StorageLocation />);
  await screen.findByDisplayValue("/library");
  const text = container.textContent ?? "";
  expect(text).toContain("Derived text, thumbnails, and embeddings share this folder too.");
  expect(text).toContain("Dormant caches can remain for 90 days.");
  expect(text).toContain("Thumbnails and text-keyed vectors remain until you clear their folders");
  // Only the per-device databases may skip sync: the purge marker and the
  // delete intents beside them are how other devices learn of a delete.
  expect(text).toContain(".cache/compiled/*.sqlite");
  expect(text).toContain("keep the rest of .cache/compiled/ syncing");
});
