import { fireEvent, render, screen } from "@testing-library/react";
import CitedRow from "./CitedRow";
import type { Citation } from "../../api/client";

const CITED: Citation = {
  quote: "I'd rather the mud than his company.",
  speaker: "Sister Aud",
  certainty: 0.92,
  authority: "self",
  band: "high",
  scene: "009--the-priory-door",
  scene_title: "The Priory Door",
  recorded: "2026-08-13T10:00:00Z",
};

const hovered: string[] = [];
const jumped: string[] = [];

function renderRow(citation?: Citation) {
  hovered.length = 0; jumped.length = 0;
  return render(
    <CitedRow label="Standing" value="Guarded. Will not be alone with the Reeve."
              citation={citation}
              onHoverQuote={(q) => hovered.push(q)}
              onGoToTurn={(q) => jumped.push(q)} />,
  );
}

const marker = () => screen.getByRole("button", { name: /^Standing:/ });

test("a cited row is marked with a filled marker in the accent", () => {
  renderRow(CITED);
  expect(marker()).toHaveClass("cited");
  expect(marker()).toHaveTextContent("◆");
});

test("an uncited row says so rather than being hidden", () => {
  // "We do not know why this is here" is itself worth showing, and it is the
  // honest answer for every row the later absorb phases wrote.
  renderRow(undefined);
  expect(marker()).toHaveClass("uncited");
  expect(marker()).toHaveTextContent("◇");
  expect(marker()).toHaveAccessibleName(/no citation on file/i);
});

test("a citation under the low band is marked in the alert colour", () => {
  // The point of the screen: a line the model was unsure of is visible before
  // it is quoted back at you in ten scenes' time.
  renderRow({ ...CITED, band: "low", certainty: 0.4 });
  expect(marker()).toHaveClass("low");
  expect(marker()).toHaveAccessibleName(/below the low certainty band/i);
});

test("hovering opens the popover with the quote, speaker and certainty", () => {
  renderRow(CITED);
  fireEvent.mouseEnter(screen.getByText(/Guarded/).closest(".cited-row") as HTMLElement);
  expect(screen.getByText(/rather the mud/)).toBeInTheDocument();
  expect(screen.getByText("Sister Aud")).toBeInTheDocument();
  expect(screen.getByText(/CERTAINTY 0.92/)).toBeInTheDocument();
  expect(screen.getByLabelText("certainty 0.92 of 1")).toBeInTheDocument();
  // Labelled, not the filename it is stored under.
  expect(screen.getByText("The Priory Door")).toBeInTheDocument();
});

test("the certainty meter lights fifths, not a raw fraction", () => {
  renderRow({ ...CITED, certainty: 0.4 });
  fireEvent.focus(marker());
  const meter = screen.getByLabelText("certainty 0.40 of 1");
  expect(meter.querySelectorAll(".seg")).toHaveLength(5);
  expect(meter.querySelectorAll(".seg.on")).toHaveLength(2);
});

test("a citation the model did not rate says unrated, not zero", () => {
  // Declining to rate itself is not the same claim as rating itself hopeless.
  renderRow({ ...CITED, certainty: null });
  fireEvent.focus(marker());
  expect(screen.getByText(/CERTAINTY UNRATED/)).toBeInTheDocument();
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("a citation with no speaker says unattributed rather than nothing", () => {
  renderRow({ ...CITED, speaker: "" });
  fireEvent.focus(marker());
  expect(screen.getByText("Unattributed")).toBeInTheDocument();
});

test("keyboard focus opens it too — the marker is the only route to the quote", () => {
  renderRow(CITED);
  fireEvent.focus(marker());
  expect(screen.getByText(/rather the mud/)).toBeInTheDocument();
  fireEvent.blur(marker());
  expect(screen.queryByText(/rather the mud/)).not.toBeInTheDocument();
});

test("an uncited row opens nothing on hover", () => {
  renderRow(undefined);
  fireEvent.mouseEnter(screen.getByText(/Guarded/).closest(".cited-row") as HTMLElement);
  expect(screen.queryByRole("note")).not.toBeInTheDocument();
  expect(hovered).toEqual([]);
});

test("opening publishes the quote so the transcript can light it, and closing clears it", () => {
  renderRow(CITED);
  fireEvent.focus(marker());
  expect(hovered).toEqual([CITED.quote]);
  fireEvent.blur(marker());
  expect(hovered).toEqual([CITED.quote, ""]);
});

test("Go to turn hands back the quote", () => {
  renderRow(CITED);
  fireEvent.focus(marker());
  // mouseDown, not click: the row's own mouseleave closes the popover, and a
  // pointer that leaves the row on its way here would never land the click.
  fireEvent.mouseDown(screen.getByRole("button", { name: /go to turn/i }));
  expect(jumped).toEqual([CITED.quote]);
});

test("with nowhere to jump to, the popover offers no jump", () => {
  render(<CitedRow label="Standing" value="x" citation={CITED} />);
  fireEvent.focus(screen.getByRole("button", { name: /^Standing:/ }));
  expect(screen.queryByRole("button", { name: /go to turn/i })).not.toBeInTheDocument();
});
