import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { SceneMeta, ShellPayload } from "../../api/types";
import { fixture } from "../../testkit/fixture";
import NothingToWrapUp from "./NothingToWrapUp";
import { ShellPayloadProvider } from "../../shell/ShellPayloadContext";

const SCENES = fixture<SceneMeta[]>([
  { id: "s1", title: "The Tide Comes In" },
  { id: "s2", title: "The Long Quay" },
]);

function page(pending: { sid: string; proposals: number }[] | null) {
  const payload = pending === null ? null : fixture<ShellPayload>({
    campaigns: 1, todo: 0,
    campaign: { id: "run", name: "A Run", scenes: 2, open: [], ledger_open: 0, sheets: null,
                unreviewed: 0, pending, images_undescribed: 0 },
  });
  render(
    <ShellPayloadProvider value={{ status: payload ? "ready" : "loading", payload,
                                   retry: () => {}, cid: "run" }}>
      <MemoryRouter>
        <NothingToWrapUp cid="run" campaignName="A Run" sid="s1"
                         sceneTitle="The Tide Comes In" scenes={SCENES} />
      </MemoryRouter>
    </ShellPayloadProvider>,
  );
}

const column = () => within(screen.getByRole("complementary", { name: /waiting reviews/i }));

test("says the scene holds nothing, and offers the scene and the way back", () => {
  page([]);
  expect(screen.getByRole("heading", { name: /nothing to wrap up/i })).toBeInTheDocument();
  expect(screen.getByText(/The Tide Comes In is not holding a review/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /open the scene/i }))
    .toHaveAttribute("href", "/campaigns/run/scenes/s1");
  expect(screen.getByRole("link", { name: /back to the campaign/i }))
    .toHaveAttribute("href", "/campaigns/run");
});

test("its column names the scenes that ARE waiting, each a link to its own wrap-up", () => {
  // It used to have no column at all: a blank strip beside the page, and an
  // empty sheet on a phone.
  page([{ sid: "s2", proposals: 4 }]);
  const row = column().getByRole("link", { name: /The Long Quay/ });
  expect(row).toHaveAttribute("href", "/campaigns/run/scenes/s2/wrap-up");
  expect(within(row).getByLabelText("4 proposals")).toBeInTheDocument();
});

test("with nothing waiting anywhere the column says so", () => {
  page([]);
  expect(column().getByText(/no scene in this campaign is/i)).toBeInTheDocument();
});

test("the current scene stays in the waiting list until the shell confirms it is absent", () => {
  page([{ sid: "s1", proposals: 4 }]);
  const row = column().getByRole("link", { name: /The Tide Comes In/ });
  expect(row).toHaveAttribute("href", "/campaigns/run/scenes/s1/wrap-up");
  expect(within(row).getByLabelText("4 proposals")).toBeInTheDocument();
  expect(column().queryByText(/no scene in this campaign is/i)).not.toBeInTheDocument();
});

test("before the shell has answered it claims nothing either way", () => {
  page(null);
  expect(column().getByText(/checking/i)).toBeInTheDocument();
  expect(column().queryByText(/no scene in this campaign is/i)).toBeNull();
});

test("a failed waiting-review read offers retry instead of continuing to check", () => {
  const retry = vi.fn();
  render(
    <ShellPayloadProvider value={{ status: "failed", payload: null, retry, cid: "run" }}>
      <MemoryRouter>
        <NothingToWrapUp cid="run" campaignName="Saltmarch" sid="s1"
                         sceneTitle="The Tide Comes In" scenes={SCENES} />
      </MemoryRouter>
    </ShellPayloadProvider>,
  );
  expect(column().getByText(/could not be read/i)).toBeInTheDocument();
  expect(column().queryByText(/checking/i)).not.toBeInTheDocument();
  retry.mockClear(); // Arrival also demands a shell read.
  fireEvent.click(column().getByRole("button", { name: /try again/i }));
  expect(retry).toHaveBeenCalledTimes(1);
});
