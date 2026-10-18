import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { RunRegistryProvider } from "./runs/RunRegistryProvider";
import "@fontsource/cinzel/600.css";
import "@fontsource/cinzel/700.css";
import "@fontsource/cinzel/900.css";
import "@fontsource/spectral/300.css";
import "@fontsource/spectral/300-italic.css";
import "@fontsource/spectral/400.css";
import "@fontsource/spectral/400-italic.css";
import "@fontsource/spectral/500.css";
import "@fontsource/space-mono/400.css";
import "@fontsource/space-mono/700.css";
import { createAppRouter } from "./router";
import "./index.css";

const router = createAppRouter();

const root = document.getElementById("root");
if (!root) throw new Error("The app root element is missing.");
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    {/* ABOVE the router, not inside it. A provider under the router host
        remounts on navigation, and surviving navigation is the one thing this
        exists to do -- so mounted there it would compile, pass its own tests,
        and lose every unresolved send the moment the player opened another
        scene. */}
    <RunRegistryProvider>
      {/* Retain v6's synchronous routing on React 18. A scene rename updates
          its URL and cached identity together; a deferred router commit lets
          the resolver see the old URL beside the new list and leave the scene.
          Both memory and data hosts pin this in CampaignView.test.tsx. */}
      <RouterProvider router={router} useTransitions={false} />
    </RunRegistryProvider>
  </React.StrictMode>,
);
