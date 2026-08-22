import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AppHeader from "./AppHeader";
import { ShellStatusProvider, usePublishSceneModel } from "./ShellStatus";

/** A page that knows which model its turns run on, standing in for CampaignView. */
function Publisher({ model, ready }: { model: string | null; ready?: boolean | null }) {
  usePublishSceneModel(model, ready ?? null);
  return null;
}

function renderHeader(published?: string | null, ready: boolean | null = null) {
  return render(
    <MemoryRouter>
      <ShellStatusProvider>
        {published !== undefined && <Publisher model={published} ready={ready} />}
        <AppHeader model="vendor/active" connection="OpenRouter" ready />
      </ShellStatusProvider>
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
      <ShellStatusProvider>
        <AppHeader model="vendor/active" connection="OpenRouter" ready />
      </ShellStatusProvider>
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
  renderHeader("vendor/cheap");
  expect(await screen.findByTitle(/OpenRouter · connected/)).toBeInTheDocument();
});
