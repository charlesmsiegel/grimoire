import { fireEvent, render, screen } from "@testing-library/react";
import { FocusProvider, FocusRestore, useFocus } from "./focus";

function Probe() {
  const { focus, setFocus } = useFocus();
  return (
    <>
      <span data-testid="state">{focus ? "on" : "off"}</span>
      <button onClick={() => setFocus(true)}>enter</button>
    </>
  );
}

const wrapped = () => (
  <FocusProvider><FocusRestore /><Probe /></FocusProvider>
);

beforeEach(() => localStorage.clear());

test("focus is off by default, and the restore pill only exists once it is on", () => {
  render(wrapped());
  expect(screen.getByTestId("state")).toHaveTextContent("off");
  // Unrendered, not hidden: in focus mode this is the FIRST tab stop, and a
  // control that is always in the tree would be one more thing between the
  // reader and the composer at every other moment.
  expect(screen.queryByRole("button", { name: /leave focus mode/i })).toBeNull();

  fireEvent.click(screen.getByText("enter"));
  expect(screen.getByTestId("state")).toHaveTextContent("on");
  fireEvent.click(screen.getByRole("button", { name: /leave focus mode/i }));
  expect(screen.getByTestId("state")).toHaveTextContent("off");
});

test("the preference survives a reload", () => {
  const first = render(wrapped());
  fireEvent.click(screen.getByText("enter"));
  first.unmount();

  render(wrapped());
  expect(screen.getByTestId("state")).toHaveTextContent("on");
  expect(screen.getByRole("button", { name: /leave focus mode/i })).toBeInTheDocument();
});

test("a component outside the provider reads 'not in focus mode' rather than throwing", () => {
  // Every route and every editor is rendered bare in its own test; reading a
  // display preference must not require the shell around it.
  render(<Probe />);
  expect(screen.getByTestId("state")).toHaveTextContent("off");
});

test("storage that refuses to answer is not a blank screen", () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });
  render(wrapped());
  expect(screen.getByTestId("state")).toHaveTextContent("off");
  // Still togglable for this session — it just will not be remembered.
  fireEvent.click(screen.getByText("enter"));
  expect(screen.getByTestId("state")).toHaveTextContent("on");
  get.mockRestore();
  set.mockRestore();
});
