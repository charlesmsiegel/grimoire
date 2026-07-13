import { render, screen, fireEvent } from "@testing-library/react";
import { FieldWidget, DerivedBadge } from "./SheetWidgets";
import type { ModuleField } from "../api/client";

const dots: ModuleField = { key: "vigor", label: "Vigor", type: "dots", max: 5 };
const track: ModuleField = { key: "health", label: "Health", type: "track", max: 7 };
const res: ModuleField = { key: "essence", label: "Essence", type: "resource", max: 10 };
const num: ModuleField = { key: "strength", label: "Strength", type: "number", min: 1, max: 20 };
const list: ModuleField = { key: "gear", label: "Gear", type: "list" };
const dotsHuge: ModuleField = { key: "xp", label: "Experience", type: "dots", max: 100 };
const dotsAtCap: ModuleField = { key: "resolve", label: "Resolve", type: "dots", max: 40 };

test("dots view renders max pips with value filled, no buttons", () => {
  const { container } = render(<FieldWidget def={dots} value={3} mode="view" />);
  expect(container.querySelectorAll(".pip").length).toBe(5);
  expect(container.querySelectorAll(".pip.on").length).toBe(3);
  expect(container.querySelectorAll("button").length).toBe(0);
});

test("dots edit: click sets value; clicking current decrements", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={dots} value={3} mode="edit" onChange={onChange} />);
  fireEvent.click(screen.getByLabelText("Vigor 5"));
  expect(onChange).toHaveBeenCalledWith(5);
  fireEvent.click(screen.getByLabelText("Vigor 3"));
  expect(onChange).toHaveBeenCalledWith(2);
});

test("track edit clicking box 1 at value 1 reaches 0", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={track} value={1} mode="edit" onChange={onChange} />);
  fireEvent.click(screen.getByLabelText("Health 1"));
  expect(onChange).toHaveBeenCalledWith(0);
});

test("resource view shows bar and current/max text", () => {
  const { container } = render(
    <FieldWidget def={res} value={{ current: 6, max: 10 }} mode="view" />);
  expect(screen.getByText("6 / 10")).toBeInTheDocument();
  const fill = container.querySelector(".resource-fill") as HTMLElement;
  expect(fill.style.width).toBe("60%");
});

test("resource edit exposes paired inputs", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={res} value={{ current: 6, max: 10 }} mode="edit" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("Essence current"), { target: { value: "4" } });
  expect(onChange).toHaveBeenCalledWith({ current: 4, max: 10 });
});

test("number renders stat cell in grid mode", () => {
  const { container } = render(<FieldWidget def={num} value={14} mode="view" grid />);
  expect(container.querySelector(".stat-cell")).toBeInTheDocument();
  expect(screen.getByText("14")).toBeInTheDocument();
});

test("number edit in grid mode is an input", () => {
  const onChange = vi.fn();
  render(<FieldWidget def={num} value={14} mode="edit" grid onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("Strength"), { target: { value: "15" } });
  expect(onChange).toHaveBeenCalledWith(15);
});

test("list view renders bullets; edit emits raw string", () => {
  const { rerender } = render(<FieldWidget def={list} value={["rope", "lantern"]} mode="view" />);
  expect(screen.getByText("rope")).toBeInTheDocument();
  const onChange = vi.fn();
  rerender(<FieldWidget def={list} value={"rope\n"} mode="edit" onChange={onChange} />);
  const ta = screen.getByLabelText("Gear") as HTMLTextAreaElement;
  expect(ta.value).toBe("rope\n");
  fireEvent.change(ta, { target: { value: "rope\nlan" } });
  expect(onChange).toHaveBeenCalledWith("rope\nlan");
});

test("dots with max beyond the pip cap falls back to the number widget", () => {
  const { container } = render(<FieldWidget def={dotsHuge} value={42} mode="view" />);
  expect(container.querySelectorAll(".pip").length).toBe(0);
  expect(screen.getByText("42")).toBeInTheDocument();

  const onChange = vi.fn();
  render(<FieldWidget def={dotsHuge} value={42} mode="edit" onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("Experience"), { target: { value: "50" } });
  expect(onChange).toHaveBeenCalledWith(50);
});

test("dots at the pip cap still renders pips", () => {
  const { container } = render(<FieldWidget def={dotsAtCap} value={10} mode="view" />);
  expect(container.querySelectorAll(".pip").length).toBe(40);
});

test("derived badge shows name and value, em-dash when undefined", () => {
  render(<DerivedBadge name="sight_pool" value={6} />);
  expect(screen.getByText("sight_pool")).toBeInTheDocument();
  expect(screen.getByText("6")).toBeInTheDocument();
  render(<DerivedBadge name="ghost" value={undefined} />);
  expect(screen.getByText("—")).toBeInTheDocument();
});
