import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: { getCalendarMonths: vi.fn() } };
});

const HARPTOS_1492 = [
  { key: "Hammer", name: "Hammer", days: 30 },
  { key: "Midwinter", name: "Midwinter", days: 1 },
  { key: "Mirtul", name: "Mirtul", days: 30 },
];

test("year entry loads months; picking month+day emits the native date", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  const onChange = vi.fn();
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                             onChange={onChange} ariaLabel="Scene date" />);
  expect(screen.getByLabelText("Scene date month")).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Scene date year"), "1492");
  await waitFor(() => expect(api.getCalendarMonths).toHaveBeenCalledWith(
    { kind: "campaign", id: "c1" }, 1492));
  await userEvent.selectOptions(await screen.findByLabelText("Scene date month"), "Mirtul");
  await userEvent.selectOptions(screen.getByLabelText("Scene date day"), "5");
  expect(onChange).toHaveBeenLastCalledWith("1492-Mirtul-05");
});

test("festival pseudo-months offer a single day", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  const onChange = vi.fn();
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                             onChange={onChange} ariaLabel="Scene date" />);
  await userEvent.type(screen.getByLabelText("Scene date year"), "1492");
  await userEvent.selectOptions(await screen.findByLabelText("Scene date month"), "Midwinter");
  const day = screen.getByLabelText("Scene date day") as HTMLSelectElement;
  expect([...day.options].map(o => o.value)).toEqual(["", "1"]);
});

test("an external value change re-syncs the fields", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  const { rerender } = render(
    <CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                        onChange={() => {}} ariaLabel="Scene date" />);
  rerender(
    <CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value="1492-Mirtul-05"
                        onChange={() => {}} ariaLabel="Scene date" />);
  expect(screen.getByLabelText("Scene date year")).toHaveValue(1492);
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date month")).toHaveValue("Mirtul"));
  expect(screen.getByLabelText("Scene date day")).toHaveValue("5");
});

test("a prefill arriving mid-edit does not clobber typed input", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  const onChange = vi.fn();
  const { rerender } = render(
    <CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                        onChange={onChange} ariaLabel="Scene date" />);
  await userEvent.type(screen.getByLabelText("Scene date year"), "20");
  rerender(
    <CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value="2026-06-29"
                        onChange={onChange} ariaLabel="Scene date" />);
  expect(screen.getByLabelText("Scene date year")).toHaveValue(20);
  expect(onChange).not.toHaveBeenCalledWith("2026-06-29");
});

test("a year change that shortens the month clears a day that no longer fits", async () => {
  const CHESHVAN_30 = [{ key: "Cheshvan", name: "Cheshvan", days: 30 }];
  const CHESHVAN_29 = [{ key: "Cheshvan", name: "Cheshvan", days: 29 }];
  (api.getCalendarMonths as any).mockImplementation(
    (_scope: unknown, year: number) =>
      Promise.resolve({ months: year === 5785 ? CHESHVAN_30 : CHESHVAN_29 }));
  const onChange = vi.fn();
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value=""
                             onChange={onChange} ariaLabel="Scene date" />);
  await userEvent.type(screen.getByLabelText("Scene date year"), "5785");
  await userEvent.selectOptions(await screen.findByLabelText("Scene date month"), "Cheshvan");
  await userEvent.selectOptions(screen.getByLabelText("Scene date day"), "30");
  expect(onChange).toHaveBeenLastCalledWith("5785-Cheshvan-30");

  const yearInput = screen.getByLabelText("Scene date year");
  await userEvent.clear(yearInput);
  await userEvent.type(yearInput, "5786");
  const day = screen.getByLabelText("Scene date day") as HTMLSelectElement;
  await waitFor(() =>
    expect([...day.options].map(o => o.value)).toEqual(
      ["", ...Array.from({ length: 29 }, (_, i) => String(i + 1))]));
  expect(day).toHaveValue("");
  expect(onChange).toHaveBeenLastCalledWith("");
});

test("an existing value pre-fills the controls", async () => {
  (api.getCalendarMonths as any).mockResolvedValue({ months: HARPTOS_1492 });
  render(<CalendarDatePicker scope={{ kind: "campaign", id: "c1" }} value="1492-Mirtul-05"
                             onChange={() => {}} ariaLabel="Scene date" />);
  expect(screen.getByLabelText("Scene date year")).toHaveValue(1492);
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date month")).toHaveValue("Mirtul"));
  expect(screen.getByLabelText("Scene date day")).toHaveValue("5");
});
