import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
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
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/* ABOVE the router, not inside it. A provider under `BrowserRouter`
        remounts on navigation, and surviving navigation is the one thing this
        exists to do -- so mounted there it would compile, pass its own tests,
        and lose every unresolved send the moment the player opened another
        scene. */}
    <RunRegistryProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </RunRegistryProvider>
  </React.StrictMode>,
);
