import { render, screen, fireEvent } from "@testing-library/react";
import { useState } from "react";
import { PromptLayoutEditor } from "./PromptLayoutEditor";
import type { PromptLayoutSection } from "../api/client";

const SECTIONS: PromptLayoutSection[] = [
  { id: "global_system_prompt", label: "", default_label: "Global system prompt",
    tier: "lock-in", enabled: true },
  { id: "world_info", label: "", default_label: "World info",
    tier: "spotlight", enabled: true },
  { id: "weather", label: "", default_label: "Weather",
    tier: "spotlight", enabled: true },
];

/** The panel is presentational — ConfigView owns the rows. This host stands in
 *  for that owner so the tests drive it the way the page does. */
function Host({ onReset = () => {}, initial = SECTIONS }: {
  onReset?: () => void; initial?: PromptLayoutSection[];
}) {
  const [rows, setRows] = useState<PromptLayoutSection[] | null>(initial);
  return (
    <>
      <PromptLayoutEditor rows={rows} failed={false} busy={false}
                          onChange={setRows} onReset={onReset} />
      <output data-testid="state">{JSON.stringify(rows)}</output>
    </>
  );
}

const rowIds = () =>
  screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id"));
const state = (): PromptLayoutSection[] =>
  JSON.parse(screen.getByTestId("state").textContent!);

test("lists the sections in the order it is given", () => {
  render(<Host />);
  expect(rowIds()).toEqual(["global_system_prompt", "world_info", "weather"]);
});

test("an unset label shows blank with the default as its placeholder", () => {
  render(<Host />);
  const input = screen.getByLabelText("Label for World info") as HTMLInputElement;
  expect(input.value).toBe("");
  expect(input.placeholder).toBe("World info");
});

test("moving a row down swaps it with the next and reports the new order", () => {
  render(<Host />);
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(rowIds()).toEqual(["world_info", "global_system_prompt", "weather"]);
  expect(state().map((s) => s.id))
    .toEqual(["world_info", "global_system_prompt", "weather"]);
});

test("moving a row up swaps it with the previous", () => {
  render(<Host />);
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ up$/i })[2]);
  expect(rowIds()).toEqual(["global_system_prompt", "weather", "world_info"]);
});

test("the first row cannot move up and the last cannot move down", () => {
  render(<Host />);
  expect(screen.getAllByRole("button", { name: /^move .+ up$/i })[0]).toBeDisabled();
  expect(screen.getAllByRole("button", { name: /^move .+ down$/i })[2]).toBeDisabled();
});

test("unchecking a section reports it disabled without removing its row", () => {
  render(<Host />);
  fireEvent.click(screen.getByRole("checkbox", { name: /include weather/i }));
  expect(state().find((s) => s.id === "weather")!.enabled).toBe(false);
  expect(rowIds()).toContain("weather");
});

test("a typed label is reported, and clearing it goes back to blank", () => {
  render(<Host />);
  const input = screen.getByLabelText("Label for World info");
  fireEvent.change(input, { target: { value: "Lore" } });
  expect(state().find((s) => s.id === "world_info")!.label).toBe("Lore");
  fireEvent.change(input, { target: { value: "" } });
  expect(state().find((s) => s.id === "world_info")!.label).toBe("");
});

test("the label input stops at the length the store keeps", () => {
  render(<Host />);
  expect(screen.getByLabelText("Label for World info"))
    .toHaveAttribute("maxLength", "60");
});

test("reset is its own action, because only the server knows the catalog order", () => {
  const onReset = vi.fn();
  render(<Host onReset={onReset} />);
  fireEvent.click(screen.getByRole("button", { name: /reset/i }));
  expect(onReset).toHaveBeenCalled();
});

test("it carries no Save of its own — the page owns that", () => {
  render(<Host />);
  expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
});

test("it says a label renames the inspector row and not the prompt", () => {
  /** The honest half of the feature: each template emits its own heading, so a
   *  label edit never reaches the model. Asserted so a refactor cannot quietly
   *  drop the sentence and leave the control looking like it edits the prompt. */
  render(<Host />);
  expect(screen.getByText(/renames the row in the scene inspector/i)).toBeInTheDocument();
  expect(screen.getByText(/not the heading the model reads/i)).toBeInTheDocument();
});

test("a failed load leaves an explanation rather than an empty panel", () => {
  render(<PromptLayoutEditor rows={null} failed busy={false}
                             onChange={() => {}} onReset={() => {}} />);
  expect(screen.getByText(/could not load the prompt layout/i)).toBeInTheDocument();
});

test("no rows yet reads as loading, not as an empty layout", () => {
  render(<PromptLayoutEditor rows={null} failed={false} busy={false}
                             onChange={() => {}} onReset={() => {}} />);
  expect(screen.getByText(/loading/i)).toBeInTheDocument();
});
