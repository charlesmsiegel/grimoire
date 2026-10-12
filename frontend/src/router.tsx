import { createBrowserRouter, type RouteObject } from "react-router-dom";
import App from "./App";
import { ArtNavigationProvider } from "./components/art/ArtNavigationProvider";

// App's descendant Routes retain every route and redirect. The data host only
// supplies real history blocking; RunRegistryProvider remains above this host.
export const appRoutes: RouteObject[] = [
  { path: "*", element: <ArtNavigationProvider><App /></ArtNavigationProvider> },
];
export function createAppRouter() { return createBrowserRouter(appRoutes); }
