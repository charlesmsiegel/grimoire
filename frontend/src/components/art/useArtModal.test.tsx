import { useState } from "react";
import { createPortal } from "react-dom";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ImageViewer } from "./ImageViewer";
import { useArtModal } from "./useArtModal";

test("Back unmounts nested art dialogs without leaving the next route inert or locked", async () => {
  function Picker() {
    const modal = useArtModal(), [open, setOpen] = useState(false);
    return createPortal(<div ref={modal.root} role="dialog" aria-label="Use existing art">
      <button onClick={() => setOpen(true)}>View Saltmarch</button>
      {open && <ImageViewer images={[{ key: "map", label: "Saltmarch", alt: "Saltmarch",
        originalUrl: "/api/map", thumbnailUrl: "/api/map", version: "1" }]}
        initialKey="map" onClose={() => setOpen(false)} />}
    </div>, modal.portal);
  }
  const NativeRequest = globalThis.Request;
  class RouteRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  }
  vi.stubGlobal("Request", RouteRequest);
  const overflow = document.body.style.overflow;
  document.body.style.overflow = "auto";
  const router = createMemoryRouter([{ path: "/before", element: <button>Realm</button> },
    { path: "/art", element: <Picker /> }], { initialEntries: ["/before", "/art"], initialIndex: 1 });
  const view = render(<RouterProvider router={router} />);
  try {
    fireEvent.click(screen.getByRole("button", { name: "View Saltmarch" }));
    expect(screen.getByRole("dialog", { name: "Image viewer" })).toBeInTheDocument();
    expect(view.container).toHaveAttribute("inert");
    await act(async () => { await router.navigate(-1); });
    expect(screen.getByRole("button", { name: "Realm" })).toBeInTheDocument();
    expect(view.container).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("auto");
  } finally {
    view.unmount(); router.dispose(); document.body.style.overflow = overflow; vi.unstubAllGlobals();
  }
});


test.each(["hidden", "disabled", "display"])("closing a viewer skips a connected %s opener", (state) => {
  const controls = render(<div><button>View Mara art</button><button>View art</button></div>);
  const opener = screen.getByRole("button", { name: "View Mara art" });
  const fallback = screen.getByRole("button", { name: "View art" });
  const viewer = render(<ImageViewer images={[{ key: "Mara", label: "Mara", alt: "Mara",
    originalUrl: "/api/art", thumbnailUrl: "/api/art", version: "1" }]}
    initialKey="Mara" opener={opener} fallbackFocus={fallback} onClose={vi.fn()} />);
  if (state === "hidden") opener.hidden = true;
  else if (state === "disabled") opener.setAttribute("disabled", "");
  else opener.style.display = "none";
  viewer.unmount();
  expect(fallback).toHaveFocus();
  controls.unmount();
});


test.each(["removed", "hidden"])("fallback focus preserves the page's scroller when the opener is %s", state => {
  const controls = render(<div data-testid="scroll-owner" style={{ overflow: "auto" }}>
    <h1 tabIndex={-1}>Realm</h1><button>View Mara art</button>
  </div>);
  const scroller = screen.getByTestId("scroll-owner"), fallback = screen.getByRole("heading", { name: "Realm" });
  const opener = screen.getByRole("button", { name: "View Mara art" });
  scroller.scrollTop = 640; scroller.scrollLeft = 28;
  const focus = fallback.focus.bind(fallback);
  // jsdom does not implement the browser's scroll-on-focus default.
  const restored = vi.spyOn(fallback, "focus").mockImplementation(options => {
    if (!options?.preventScroll) { scroller.scrollTop = 0; scroller.scrollLeft = 0; }
    focus(options);
  });
  const viewer = render(<ImageViewer images={[{ key: "Mara", label: "Mara", alt: "Mara",
    originalUrl: "/api/art", thumbnailUrl: "/api/art", version: "1" }]}
    initialKey="Mara" opener={opener} fallbackFocus={fallback} onClose={vi.fn()} />);
  try {
    if (state === "removed") opener.remove(); else opener.style.display = "none";
    viewer.unmount();
    expect(fallback).toHaveFocus();
    expect(scroller.scrollTop).toBe(640); expect(scroller.scrollLeft).toBe(28);
    expect(restored).toHaveBeenCalledWith({ preventScroll: true });
  } finally { viewer.unmount(); restored.mockRestore(); controls.unmount(); }
});
