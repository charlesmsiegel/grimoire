import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// The header's theme toggle persists on click, so it reaches the api client.
vi.mock("../api/client", () => ({
  api: { putConfig: vi.fn().mockResolvedValue({ theme: "dark" }) },
}));

import AppHeader from "./AppHeader";
import type { ProviderHealth } from "../api/types";
import { ThemeProvider } from "../theme/ThemeProvider";
import { ShellStatusProvider, usePublishSceneModel } from "./ShellStatus";

/** What the provider last actually did (#146). `null` is "nothing has been
 *  recorded", which is the state the dot is green-but-unproven in — so a test
 *  about ROUTING passes it and says nothing about health, and the one test
 *  that wants the word "connected" supplies the outcome that earns it. */
const WORKED: ProviderHealth = { state: "ok", kind: "", detail: "", at: "" };

/** A page that knows which model its turns run on, standing in for CampaignView. */
function Publisher({ model, ready }: { model: string | null; ready?: boolean | null }) {
  usePublishSceneModel(model, ready ?? null);
  return null;
}

function renderHeader(published?: string | null, ready: boolean | null = null,
                      health: ProviderHealth | null = null) {
  return render(
    <MemoryRouter>
      <ThemeProvider initial="light">
      <ShellStatusProvider>
        {published !== undefined && <Publisher model={published} ready={ready} />}
        <AppHeader model="vendor/active" connection="OpenRouter" ready health={health}
                   railDrawer={false} onOpenRail={() => {}} />
      </ShellStatusProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

test("names the active connection's model when no page has said otherwise", async () => {
  renderHeader();
  expect(await screen.findByText("VENDOR/ACTIVE")).toBeInTheDocument();
});

test("a routed scene model replaces it, rather than sitting beside it", async () => {
  // #142: the header exists to name what the next turn costs. A campaign whose
  // scene turns are routed elsewhere makes the active connection's model the
  // wrong answer, and showing both would be two answers to one question.
  renderHeader("vendor/cheap");
  expect(await screen.findByText("VENDOR/CHEAP")).toBeInTheDocument();
  expect(screen.queryByText("VENDOR/ACTIVE")).not.toBeInTheDocument();
});

test("leaving the campaign restores the global model", async () => {
  const { rerender } = renderHeader("vendor/cheap");
  expect(await screen.findByText("VENDOR/CHEAP")).toBeInTheDocument();

  // The publisher unmounting is what a navigation away looks like; its cleanup
  // clears the value, or the header would keep naming a campaign's model on
  // the Configuration page.
  rerender(
    <MemoryRouter>
      <ThemeProvider initial="light">
        <ShellStatusProvider>
          <AppHeader model="vendor/active" connection="OpenRouter" ready health={null}
                     railDrawer={false} onOpenRail={() => {}} />
        </ShellStatusProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("VENDOR/ACTIVE")).toBeInTheDocument();
});

test("a page that resolved nothing leaves the global model alone", async () => {
  // A failed routing read publishes null, which must read as "no opinion"
  // rather than as "no model" -- the header would otherwise go blank on a
  // hiccup that changes nothing about what the turn will use.
  renderHeader(null);
  expect(await screen.findByText("VENDOR/ACTIVE")).toBeInTheDocument();
});

test("the dot reports the routed connection, not the active one", async () => {
  // A campaign routing its scene turns at a keyless connection 409s every send.
  // A green dot beside that describes a connection this page is not using.
  renderHeader("vendor/cheap", false);
  expect(await screen.findByTitle(/not ready/)).toBeInTheDocument();
});

test("a page with no opinion about readiness leaves the global verdict alone", async () => {
  // The health outcome is supplied so the verdict has something to say beyond
  // "not checked yet" (#146): what is under test is that `ready` still governs
  // when the page publishes no opinion, and a green-but-unproven dot would
  // pass whether it did or not.
  renderHeader("vendor/cheap", null, WORKED);
  expect(await screen.findByTitle(/OpenRouter, connected/)).toBeInTheDocument();
});
