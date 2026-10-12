import { useEffect } from "react";
import { act, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, Link, Routes, Route } from "react-router-dom";
import { appRoutes } from "./router";
import { RunRegistryProvider, useRunRegistry } from "./runs/RunRegistryProvider";

vi.mock("./App", () => ({ default: function AppStub() {
  return <Routes><Route path="/worlds/:wid/*" element={<><span>Realm</span><Link to="/campaigns">Campaigns</Link></>} />
    <Route path="/campaigns" element={<span>Campaigns route</span>} /></Routes>;
} }));
test("data-router host preserves descendant routes and the run registry above navigation", async () => {
  const held: unknown[] = [];
  function Observe() { const registry = useRunRegistry(); useEffect(() => { held.push(registry); }); return null; }
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/worlds/Realm/items/Saltmarch?tab=art"] });
  render(<RunRegistryProvider><Observe /><RouterProvider router={router} /></RunRegistryProvider>);
  expect(screen.getByText("Realm")).toBeVisible();
  // A route-host test needs no Request signal: the same bridge is exercised
  // against actual Back/Link behavior by ArtNavigationProvider.test.tsx.
  const NativeRequest = globalThis.Request;
  class TestRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) { super(input, { ...init, signal: undefined }); }
  }
  vi.stubGlobal("Request", TestRequest);
  try { await act(() => router.navigate("/campaigns")); } finally { vi.unstubAllGlobals(); }
  expect(screen.getByText("Campaigns route")).toBeVisible();
  expect(new Set(held).size).toBe(1);
});
