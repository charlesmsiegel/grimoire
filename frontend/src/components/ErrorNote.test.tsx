import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "../api/client";
import { errMsg, isOffline } from "./errMsg";
import { ErrorNote } from "./ErrorNote";

function show(err: unknown, at = "/campaigns/run") {
  render(
    <MemoryRouter initialEntries={[at]}>
      <div className="banner"><ErrorNote err={err} /></div>
    </MemoryRouter>);
}

test("an ApiError tagged network is offline; the same error untagged is not", () => {
  expect(isOffline(new ApiError(502, "connection reset", "network"))).toBe(true);
  expect(isOffline(new ApiError(502, "connection reset"))).toBe(false);
});

test("a stream error frame carries its kind as a plain object", () => {
  expect(isOffline({ detail: "connection reset", kind: "network" })).toBe(true);
});

test("missing_key is NOT offline — an unconfigured key is a different fix", () => {
  expect(isOffline({ detail: "No LLM connection selected", kind: "missing_key" })).toBe(false);
});

test("a rejection that is not an object at all does not throw", () => {
  expect(isOffline("boom")).toBe(false);
  expect(isOffline(null)).toBe(false);
  expect(isOffline(undefined)).toBe(false);
});

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

test("errMsg still reads a plain string rejection", () => {
  expect(errMsg("boom")).toBe("boom");
});

test("on Connections itself the note keeps its words and drops the link", () => {
  // The catalog fetch on that page raises this note too, and a link to the
  // page you are reading is noise.
  show(new ApiError(502, "connection refused", "network"), "/connections");
  expect(screen.getByText(/Couldn’t reach the model provider/)).toBeInTheDocument();
  expect(screen.queryByRole("link")).toBeNull();
});
