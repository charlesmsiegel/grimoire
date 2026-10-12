import { act, renderHook } from "@testing-library/react";
import { useImageViewer } from "./useImageViewer";
import type { ImageDescriptor } from "./types";
const images: ImageDescriptor[] = [{ key: "Mara", label: "Mara", alt: "Mara", originalUrl: "/api/Mara", thumbnailUrl: "/api/Mara?w=160", version: "1" }];
test("owner changes close the sequence", () => {
  const { result, rerender } = renderHook(({ owner }) => useImageViewer(owner), { initialProps: { owner: "Realm/Mara" } });
  act(() => result.current.open(images, "Mara", document.body));
  expect(result.current.viewerProps?.initialKey).toBe("Mara");
  rerender({ owner: "Realm/Seraphine" });
  expect(result.current.viewerProps).toBeNull();
  rerender({ owner: "Realm/Mara" });
  expect(result.current.viewerProps).toBeNull();
});


test("a removed-image notice survives closing and retires when another owner is selected", () => {
  const { result, rerender } = renderHook(({ owner }) => useImageViewer(owner), { initialProps: { owner: "Realm" } });
  act(() => result.current.close("unavailable"));
  expect(result.current.notice).toBe("This image is no longer available.");
  expect(result.current.viewerProps).toBeNull();
  rerender({ owner: "Saltmarch" });
  expect(result.current.notice).toBeNull();
});
