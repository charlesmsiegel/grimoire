import { fireEvent, render, screen } from "@testing-library/react";
import { ComposerMeta } from "./ComposerMeta";

type Props = React.ComponentProps<typeof ComposerMeta>;

function setup(over: Partial<Props> = {}) {
  const props: Props = {
    directing: false, pcless: false, onDirect: vi.fn(), held: null,
    targets: { words: "", paragraphs: "", wordsDefault: "150", paragraphsDefault: "2" },
    onTarget: vi.fn(), onClearTargets: vi.fn(),
    order: null, orderOpen: false, onOrder: vi.fn(),
    dossierOpen: false, notice: null, inspectorOpen: false, onInspector: vi.fn(),
    ...over,
  };
  render(<ComposerMeta {...props} />);
  return props;
}

test("Speak and Direct say which is on, and Speak is refused where nobody can speak", () => {
  const { onDirect } = setup({ pcless: true, directing: true });
  expect(screen.getByRole("button", { name: "Direct" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Speak" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  expect(onDirect).toHaveBeenCalledWith(true);
  expect(screen.getByText("steers the reply · never posted")).toBeInTheDocument();
});

test("the boxes show what the reply is budgeted at, and the figure is not said twice", () => {
  setup();
  expect(screen.getByLabelText("Next reply words")).toHaveAttribute("placeholder", "150");
  expect(screen.getByLabelText("Next reply paragraphs")).toHaveAttribute("placeholder", "2");
  // The strip used to repeat the placeholders as "150 words · 2 paragraphs"
  // beside them, which is most of why it wrapped.
  expect(screen.queryByText(/150 words/)).toBeNull();
  expect(screen.queryByText("one-shot")).toBeNull();
});

test("a one-shot target is badged as one, and ✕ takes it back", () => {
  const { onTarget, onClearTargets } = setup({
    targets: { words: "300", paragraphs: "", wordsDefault: "150", paragraphsDefault: "2" } });
  expect(screen.getByLabelText("Next reply words")).toHaveValue(300);
  expect(screen.getByText("one-shot")).toBeInTheDocument();
  // ✕, not the "?" it was: a question mark reads as help.
  const cancel = screen.getByRole("button", { name: "Cancel the one-shot response targets" });
  expect(cancel).toHaveTextContent("✕");
  fireEvent.click(cancel);
  expect(onClearTargets).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Next reply paragraphs"), { target: { value: "3" } });
  expect(onTarget).toHaveBeenCalledWith("response_continuation_paragraphs", "3");
});

test("a held draft and the notices are named on the strip", () => {
  setup({ held: "note", dossierOpen: true, notice: "Roll failed: no dice" });
  expect(screen.getByText("note held · clear the box to get it back")).toBeInTheDocument();
  expect(screen.getByText("Still your turn · dossier open")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Roll failed: no dice");
  // Whatever gives way to an ellipsis keeps the whole sentence in its tooltip.
  expect(screen.getByRole("status")).toHaveAttribute("title", "Roll failed: no dice");
});

test("Order shows only with a group, and both links say whether they are open", () => {
  const { onOrder, onInspector } = setup({ order: "Directed", orderOpen: true, inspectorOpen: true });
  fireEvent.click(screen.getByRole("button", { name: "Order: Directed" }));
  expect(onOrder).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Order: Directed" })).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(screen.getByRole("button", { name: "Hide what the model saw" }));
  expect(onInspector).toHaveBeenCalled();
});

test("with no group there is no Order", () => {
  setup();
  expect(screen.queryByRole("button", { name: /^Order:/ })).toBeNull();
  expect(screen.getByRole("button", { name: "What the model saw →" }))
    .toHaveAttribute("aria-expanded", "false");
});
