import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { RateFields, complete, emptyRates, entryOf, formOf, hasBase, perMillion,
         type RateForm } from "./RateFields";

function Host({ start }: { start: RateForm }) {
  const [value, setValue] = useState(start);
  return <RateFields value={value} onChange={setValue} idPrefix="t" subject="vendor/m" />;
}

test("RateFields shows the per-million figure", () => {
  render(<Host start={{ ...emptyRates(), prompt_usd_per_1k: "0.5" }} />);

  // Four boxes, in the pricing table's order.
  const boxes = screen.getAllByRole("spinbutton");
  expect(boxes.map((b) => b.getAttribute("aria-label"))).toEqual([
    "Input rate for vendor/m", "Output rate for vendor/m",
    "Cache read rate for vendor/m", "Cache write rate for vendor/m",
  ]);
  expect(boxes[0]).toHaveAttribute("id", "t-prompt_usd_per_1k");
  expect(screen.getByText("$500/M")).toBeInTheDocument();
  // An empty box names its unit rather than a figure.
  expect(screen.getAllByText("$ per 1K")).toHaveLength(3);

  fireEvent.change(boxes[1], { target: { value: "1.5" } });
  expect(screen.getByText("$1,500/M")).toBeInTheDocument();
});

test("a zero rate is a price, and an absent one stays absent", () => {
  expect(perMillion("0")).toBe("$0/M");
  expect(perMillion("")).toBe("");
  const form = formOf({ prompt_usd_per_1k: 0, completion_usd_per_1k: 0 });
  expect(form).toEqual({ ...emptyRates(), prompt_usd_per_1k: "0", completion_usd_per_1k: "0" });
  expect(complete(form)).toBe(true);
  // Unset boxes are left out, never sent as null.
  expect(entryOf(form)).toEqual({ prompt_usd_per_1k: 0, completion_usd_per_1k: 0 });
  expect(complete({ ...emptyRates(), cache_read_usd_per_1k: "0.1" })).toBe(false);
  // `hasBase` asks only whether both base boxes were filled at all: a negative
  // one is the store's to refuse, not a missing rate.
  expect(hasBase({ ...emptyRates(), prompt_usd_per_1k: "-1", completion_usd_per_1k: "0" }))
    .toBe(true);
  expect(hasBase({ ...emptyRates(), prompt_usd_per_1k: "0.1" })).toBe(false);
  // A filled box that is not a rate is sent as typed, for the store to name.
  expect(entryOf({ ...emptyRates(), cache_write_usd_per_1k: "-1" }))
    .toEqual({ cache_write_usd_per_1k: -1 });
});
