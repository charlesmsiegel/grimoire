import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { ColumnSection, PageShell, PlainShell } from "./PageShell";

function Shelf({ label = "Context" }: { label?: string }) {
  return (
    <PageShell column={<ColumnSection label="Worlds" count={2}>
                         <button>rows</button>
                       </ColumnSection>}
               footer={<span>pinned</span>} columnLabel={label}>
      <h1>Main</h1>
    </PageShell>
  );
}

test("the column, its pinned footer and main are all present and named", () => {
  render(<MemoryRouter><Shelf label="Worlds" /></MemoryRouter>);
  const column = screen.getByRole("complementary", { name: "Worlds" });
  expect(column).toHaveTextContent("Worlds");
  expect(column).toHaveTextContent("2");
  // The footer is outside the scrolling half, which is the whole point of the
  // structure: a long column must not push it out of the shell.
  expect(column.querySelector(".column-pinned")).toHaveTextContent("pinned");
  expect(column.querySelector(".column-scroll")).not.toHaveTextContent("pinned");
  expect(screen.getByRole("main")).toHaveTextContent("Main");
});

test("a page with no footer renders no pinned block at all", () => {
  render(
    <MemoryRouter>
      <PageShell column={<span>rows</span>}><span>main</span></PageShell>
    </MemoryRouter>,
  );
  expect(screen.getByRole("complementary").querySelector(".column-pinned")).toBeNull();
});

test("a route that only changes its params still opens main at the top", () => {
  // Two routes rendering different components remount main, and a fresh node
  // starts at zero. A route whose component stays put and whose param moves --
  // /worlds/a to /worlds/b -- keeps the same scroll port, and without the
  // reset the next record opens at the offset the last one was left at.
  function Jump() {
    const navigate = useNavigate();
    return <button onClick={() => navigate("/w/b")}>go</button>;
  }
  render(
    <MemoryRouter initialEntries={["/w/a"]}>
      <Routes>
        <Route path="/w/:wid" element={
          <PageShell column={<span>rows</span>}><Jump /></PageShell>} />
      </Routes>
    </MemoryRouter>,
  );
  const main = screen.getByRole("main");
  const scrollTo = vi.fn();
  main.scrollTo = scrollTo as any;

  fireEvent.click(screen.getByText("go"));
  expect(screen.getByRole("main")).toBe(main);   // same port, not a remount
  expect(scrollTo).toHaveBeenCalledWith(0, 0);
});

test("PlainShell is main with no column — the wizards' shape", () => {
  render(<MemoryRouter><PlainShell><h1>Welcome</h1></PlainShell></MemoryRouter>);
  expect(screen.getByRole("main")).toHaveTextContent("Welcome");
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
});

// ---- 375px: the column is the page, main is a push (4h) ----

/** jsdom reports 1024 unless a test says otherwise. Set it before render — the
 *  shell reads the width once on mount and then listens. */
function atWidth(px: number) {
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: px, configurable: true, writable: true });
  return () => Object.defineProperty(window, "innerWidth",
    { value: orig, configurable: true, writable: true });
}

test("at desktop width there is no index control at all", () => {
  // Rendered, not CSS-hidden: a control that does nothing here should not be
  // in the tab order here.
  const restore = atWidth(1200);
  try {
    render(<MemoryRouter><Shelf /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /context|worlds/i })).not.toBeInTheDocument();
  } finally { restore(); }
});

test("on a phone main is what you land on, and the column arrives on demand", () => {
  // A deep link is a request for content; answering it with an index would be
  // ignoring it.
  const restore = atWidth(375);
  try {
    render(<MemoryRouter><Shelf label="Worlds" /></MemoryRouter>);
    expect(screen.getByRole("main")).toHaveTextContent("Main");
    expect(document.querySelector(".shell")).not.toHaveClass("show-column");

    fireEvent.click(screen.getByRole("button", { name: /worlds/i }));
    expect(document.querySelector(".shell")).toHaveClass("show-column");
    expect(screen.getByRole("button", { name: /close/i })).toBeInTheDocument();
  } finally { restore(); }
});

test("picking something that navigates puts the column away again", () => {
  // This is what makes it a push rather than a tab switch: a row that navigates
  // hands you the thing you asked for.
  const restore = atWidth(375);
  try {
    function Jump() {
      const navigate = useNavigate();
      return <button onClick={() => navigate("/w/b")}>go</button>;
    }
    render(
      <MemoryRouter initialEntries={["/w/a"]}>
        <Routes>
          <Route path="/w/:wid" element={
            <PageShell column={<Jump />} columnLabel="Worlds"><span>main</span></PageShell>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /worlds/i }));
    expect(document.querySelector(".shell")).toHaveClass("show-column");

    fireEvent.click(screen.getByText("go"));
    expect(document.querySelector(".shell")).not.toHaveClass("show-column");
  } finally { restore(); }
});

test("a filter that does not navigate leaves the column up", () => {
  // You can see it working behind the column and will probably pick another.
  const restore = atWidth(375);
  try {
    render(<MemoryRouter><Shelf label="Worlds" /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /worlds/i }));
    fireEvent.click(screen.getByText("rows"));
    expect(document.querySelector(".shell")).toHaveClass("show-column");
  } finally { restore(); }
});

test("rotating into a wide viewport gives both panes back", () => {
  const restore = atWidth(375);
  try {
    render(<MemoryRouter><Shelf label="Worlds" /></MemoryRouter>);
    expect(document.querySelector(".shell")).toHaveClass("phone");

    Object.defineProperty(window, "innerWidth", { value: 1200, configurable: true, writable: true });
    fireEvent(window, new Event("resize"));
    expect(document.querySelector(".shell")).not.toHaveClass("phone");
    expect(screen.queryByRole("button", { name: /^worlds$/i })).not.toBeInTheDocument();
  } finally { restore(); }
});
