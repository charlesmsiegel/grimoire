import { useEffect, useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, Link, RouterProvider, useLocation, useNavigate } from "react-router-dom";
import { ArtNavigationProvider, useArtNavigation } from "./ArtNavigationProvider";

// Same Request/signal bridge as WorldView's existing data-router Back test:
// jsdom's signal is not the Node signal accepted by its native Request.
const NativeRequest = globalThis.Request;
class TestRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...rest } = init ?? {};
    super(input, rest);
    if (signal) Object.defineProperty(this, "signal", { value: signal });
  }
}
beforeEach(() => vi.stubGlobal("Request", TestRequest));
afterEach(() => vi.unstubAllGlobals());
function Editor() {
  const navigation = useArtNavigation(), location = useLocation(), navigate = useNavigate();
  const [text, setText] = useState(""), [tab, setTab] = useState("Art");
  useEffect(() => navigation.registerDraftGuard(() => !text || window.confirm("Discard unsaved art?")), [navigation, text]);
  return <><output>{location.pathname + location.search}</output>
    <textarea aria-label="Description" value={text} onChange={(e) => setText(e.target.value)} />
    <Link to="/worlds/Realm/items/Saltmarch">Leave</Link>
    <button onClick={() => navigation.requestArtTransition(() => setTab("Details"))}>Details</button>
    <button onClick={() => navigation.requestArtTransition(() => navigate("/worlds/Realm/pcs/Mara?tab=art"))}>Art destination</button>
    <span>{tab}</span></>;
}
function setup() {
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Editor /></ArtNavigationProvider> }],
    { initialEntries: ["/worlds/Realm/locations/Saltmarch", "/worlds/Realm/characters/Seraphine?tab=art&v=era"], initialIndex: 1 });
  render(<RouterProvider router={router} />);
  return router;
}
test("cancelled Link, Back, and local switch retain URL and draft", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const router = setup();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved description" } });
  fireEvent.click(screen.getByRole("link"));
  await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
  expect(router.state.location.pathname).toContain("/characters/Seraphine");
  expect(router.state.location.search).toContain("tab=art");
  await act(() => router.navigate(-1));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
  expect(router.state.location.pathname).toContain("/characters/Seraphine");
  expect(screen.getByRole("textbox")).toHaveValue("Unsaved description");
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(screen.getByText("Art")).toBeVisible();
  confirm.mockRestore();
});
test("confirmed discard navigates once and clean panels are not blocked", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const router = setup();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved crop" } });
  fireEvent.click(screen.getByRole("link"));
  await waitFor(() => expect(router.state.location.pathname).toContain("/items/"));
  expect(confirm).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "" } });
  await act(() => router.navigate(-1));
  expect(confirm).toHaveBeenCalledOnce();
  confirm.mockRestore();
});

test("approved local transition and its route update prompt only once", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const router = setup();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Unsaved description" } });
  fireEvent.click(screen.getByRole("button", { name: "Art destination" }));
  await waitFor(() => expect(router.state.location.pathname).toContain("/pcs/Mara"));
  expect(confirm).toHaveBeenCalledOnce();
  confirm.mockRestore();
});
