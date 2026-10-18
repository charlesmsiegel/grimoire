import { fireEvent, render, screen } from "@testing-library/react";
import { SceneMenu } from "./SceneMenu";

function setup() {
  const toggle = vi.fn();
  const onNewScene = vi.fn();
  render(
    <>
      <p>the transcript</p>
      <SceneMenu onNewScene={onNewScene} panels={[
        { label: "Calendar", open: false, toggle },
        { label: "Cover", open: true, toggle: vi.fn() },
      ]} />
    </>);
  const summary = screen.getByText("Scene ⋯");
  const menu = summary.closest("details") as HTMLDetailsElement;
  /** Open it the way a click on the summary does: the element's own state,
   *  and the toggle event the menu follows it by. */
  const openMenu = () => {
    menu.open = true;
    fireEvent(menu, new Event("toggle"));
  };
  return { toggle, onNewScene, summary, menu, openMenu };
}

test("each panel is a row that says whether it is showing", () => {
  setup();
  expect(screen.getByRole("button", { name: "Calendar" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("button", { name: "Cover ✓" })).toHaveAttribute("aria-pressed", "true");
});

test("a pick runs and shuts the menu", () => {
  const { toggle, onNewScene, menu, openMenu } = setup();
  openMenu();
  fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
  expect(toggle).toHaveBeenCalledTimes(1);
  expect(menu.open).toBe(false);
  openMenu();
  fireEvent.click(screen.getByRole("button", { name: "+ New scene" }));
  expect(onNewScene).toHaveBeenCalledTimes(1);
  expect(menu.open).toBe(false);
});

test("Escape shuts an open menu and hands focus back to its summary", () => {
  const { menu, summary, openMenu } = setup();
  openMenu();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(menu.open).toBe(false);
  expect(summary).toHaveFocus();
});

test("a press outside shuts it, and a press inside does not", () => {
  const { menu, openMenu } = setup();
  openMenu();
  fireEvent.pointerDown(screen.getByRole("button", { name: "Calendar" }));
  expect(menu.open).toBe(true);
  fireEvent.pointerDown(screen.getByText("the transcript"));
  expect(menu.open).toBe(false);
});
