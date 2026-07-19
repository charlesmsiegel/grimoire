import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ModelCombobox from "./ModelCombobox";

const MODELS = [
  { id: "anthropic/claude", name: "Claude", context: 200000, prompt: "0.00001", completion: "0.00002" },
  { id: "google/gemini", name: "Gemini", context: 1048576, prompt: "0", completion: "0" },
];

function Harness({ initial = "", models = MODELS }: { initial?: string; models?: typeof MODELS }) {
  const [v, setV] = useState(initial);
  return <ModelCombobox value={v} onChange={setV} models={models} />;
}

test("focusing with an empty query shows the full list", async () => {
  render(<Harness />);
  fireEvent.focus(screen.getByRole("textbox"));
  expect(await screen.findByText("anthropic/claude")).toBeInTheDocument();
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("typing filters by id", async () => {
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("anthropic/claude");
  fireEvent.change(input, { target: { value: "google" } });
  await waitFor(() => expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("typing filters by name", async () => {
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("anthropic/claude");
  fireEvent.change(input, { target: { value: "Gemini" } });
  await waitFor(() => expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("rows show the model context limit", async () => {
  render(<Harness />);
  fireEvent.focus(screen.getByRole("textbox"));
  expect(await screen.findByText("200K ctx")).toBeInTheDocument();
  expect(screen.getByText("1M ctx")).toBeInTheDocument();
});

test("typing 'free' filters by the price label", async () => {
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("anthropic/claude");
  fireEvent.change(input, { target: { value: "free" } });
  await waitFor(() => expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("selecting a row sets the model id", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} models={MODELS} />);
  fireEvent.focus(screen.getByRole("textbox"));
  fireEvent.mouseDown(await screen.findByText("google/gemini"));
  expect(onChange).toHaveBeenCalledWith("google/gemini");
});

test("free-text typing passes through even for an unlisted id", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} models={MODELS} />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("google/gemini");
  fireEvent.change(input, { target: { value: "my/custom-model" } });
  expect(onChange).toHaveBeenCalledWith("my/custom-model");
});

test("an error prop shows a note and still allows free-text typing", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} models={[]} error />);
  expect(await screen.findByText(/couldn.t load model list/i)).toBeInTheDocument();
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "still/works" } });
  expect(onChange).toHaveBeenCalledWith("still/works");
});

test("a model with unknown pricing/context shows no chips and is not matched by 'free'", async () => {
  const models = [
    { id: "custom/model", name: "Custom", context: null, prompt: null, completion: null },
    ...MODELS,
  ];
  render(<Harness models={models} />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  const row = (await screen.findByText("custom/model")).closest("li")!;
  expect(row.textContent).not.toMatch(/ctx/);
  fireEvent.change(input, { target: { value: "free" } });
  await waitFor(() => expect(screen.queryByText("custom/model")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});
