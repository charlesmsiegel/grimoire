import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ModelCombobox from "./ModelCombobox";

vi.mock("../api/models", async (orig) => {
  const actual = await orig<typeof import("../api/models")>();
  return { ...actual, getModels: vi.fn() };
});
import { getModels } from "../api/models";
const mockFetchModels = getModels as unknown as ReturnType<typeof vi.fn>;

const MODELS = [
  { id: "anthropic/claude", name: "Claude", context: 200000, prompt: "0.00001", completion: "0.00002" },
  { id: "google/gemini", name: "Gemini", context: 1048576, prompt: "0", completion: "0" },
];

function Harness({ initial = "" }: { initial?: string }) {
  const [v, setV] = useState(initial);
  return <ModelCombobox value={v} onChange={setV} />;
}

beforeEach(() => {
  mockFetchModels.mockReset();
  mockFetchModels.mockResolvedValue(MODELS);
});

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
  render(<ModelCombobox value="" onChange={onChange} />);
  fireEvent.focus(screen.getByRole("textbox"));
  fireEvent.mouseDown(await screen.findByText("google/gemini"));
  expect(onChange).toHaveBeenCalledWith("google/gemini");
});

test("free-text typing passes through even for an unlisted id", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("google/gemini");
  fireEvent.change(input, { target: { value: "my/custom-model" } });
  expect(onChange).toHaveBeenCalledWith("my/custom-model");
});

test("a fetch failure degrades to a usable text input with a note", async () => {
  mockFetchModels.mockRejectedValue(new Error("offline"));
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} />);
  expect(await screen.findByText(/couldn.t load model list/i)).toBeInTheDocument();
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "still/works" } });
  expect(onChange).toHaveBeenCalledWith("still/works");
});
