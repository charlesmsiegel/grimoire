import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "../api/client";
import { ErrorNote } from "./ErrorNote";

// What the reader SEES. Which rejections count as offline is `isOffline`'s
// own question, and is pinned in `api/errors.test.ts`.

function show(err: unknown, at = "/campaigns/run") {
  render(
    <MemoryRouter initialEntries={[at]}>
      <div className="banner"><ErrorNote err={err} /></div>
    </MemoryRouter>);
}

test("a network failure offers the local-connection recovery, not just the error", () => {
  show(new ApiError(502, "connection reset", "network"));
  expect(screen.getByText(/Couldn’t reach the model provider/)).toBeInTheDocument();
  // The provider's own words survive: `network` covers a local endpoint that
  // is not running, and that reader needs the address that was refused.
  expect(screen.getByText(/connection reset/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Connections/ })).toHaveAttribute("href", "/connections");
});

test("any other failure renders its detail and nothing else", () => {
  show(new ApiError(409, "No LLM connection selected", "missing_key"));
  expect(screen.getByText("No LLM connection selected")).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});

test("a composed sentence renders unchanged", () => {
  // Half the raisers of these banners never caught anything -- they write a
  // sentence out of what they found ("Could not import — 2 files failed").
  // Those go through the same component, and a version of it that only knew
  // how to read `detail` off an object would blank every one of them.
  show("Could not import — 2 files failed.");
  expect(screen.getByText("Could not import — 2 files failed.")).toBeInTheDocument();
});

test("a failure with an empty detail says something rather than nothing", () => {
  // `errorText`'s own rule, and the reason this reads through it rather than
  // taking `detail` itself: a backend answering `{"detail": ""}` used to give
  // an error box with nothing in it. An earlier draft of this change routed
  // every banner through a helper that did not have that rule, which put the
  // blank box back in the one editor that had been spared it.
  show(new ApiError(500, ""));
  expect(screen.getByText(/Error/)).toBeInTheDocument();
});

test("the note names the recovery, not merely that something broke", () => {
  // The point of #210 is that a local model connection keeps play going. A
  // note that only said "you are offline" would pass every other assertion
  // here and be worth nothing.
  show({ detail: "connection reset", kind: "network" });
  expect(screen.getByText(/local model connection/)).toBeInTheDocument();
  expect(screen.getByText(/library is on this machine/)).toBeInTheDocument();
});

test("on Connections itself the note keeps its words and drops the link", () => {
  // The catalog fetch on that page raises this note too, and a link to the
  // page you are reading is noise.
  show(new ApiError(502, "connection refused", "network"), "/connections");
  expect(screen.getByText(/Couldn’t reach the model provider/)).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});
