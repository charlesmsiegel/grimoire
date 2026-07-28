import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WeatherWidget } from "./WeatherWidget";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getSceneWeather: vi.fn(),
    setWeatherOverride: vi.fn(),
    clearWeather: vi.fn(),
    resumeWeather: vi.fn(),
    deleteWeatherOverride: vi.fn(),
  } };
});

const BASE = {
  weather: { condition: "overcast", temperature: "cold", wind: "breeze" },
  source: { condition: "procedural", temperature: "procedural", wind: "procedural" },
  procedural: { condition: "overcast", temperature: "cold", wind: "breeze" },
  stack: [],
  climate: "temperate-interior",
  season: "winter",
  location: "saltmarch-docks",
  native: "2026-06-14T09:00",
  ordinal: 3698906,  // ...T09:00 is morning, position 1 of 5
  tables: {
    condition: ["clear", "overcast", "light rain"],
    temperature: ["freezing", "cold", "mild"],
    wind: ["calm", "breeze", "gale"],
  },
};

beforeEach(() => {
  // Call history is not auto-cleared here, and two of these tests assert on
  // "was not called" / "the first call" — both of which read a previous test's
  // calls without this.
  vi.clearAllMocks();
  vi.mocked(api.getSceneWeather).mockResolvedValue(BASE as never);
  vi.mocked(api.setWeatherOverride).mockResolvedValue({ id: "ovr-1" } as never);
  vi.mocked(api.clearWeather).mockResolvedValue({ cleared: 1 });
  vi.mocked(api.resumeWeather).mockResolvedValue({ resumed: 1 });
});

test("reads as one line of the three axes", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  expect(await screen.findByText(/overcast, cold, wind breeze/)).toBeInTheDocument();
});

test("renders nothing when there is no location or moment", async () => {
  // Matches how the neighbouring When and Location widgets degrade — no
  // placeholder, no error.
  vi.mocked(api.getSceneWeather).mockResolvedValue(
    { weather: null, location: null, native: null } as never);
  const { container } = render(<WeatherWidget cid="c" sid="s" />);
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});

test("marks an authored axis and shows its note on hover", async () => {
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { ...BASE.weather, condition: "blizzard" },
    source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-14", to: null,
              condition: "blizzard", note: "the Wintertide storm" }],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  const chip = await screen.findByTitle("the Wintertide storm");
  expect(chip).toHaveTextContent("Condition set");
});

test("the popover offers the active season's entries per axis", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  const select = await screen.findByLabelText("Condition");
  expect([...select.querySelectorAll("option")].map((o) => o.textContent))
    .toEqual(["leave to chance", "clear", "overcast", "light rain"]);
});

test("an authored value off the table is still offered, marked as authored", async () => {
  // Overrides are deliberately not confined to the table, and the extractor
  // writes whatever the narration invented. Without this the select shows
  // blank and saving another axis quietly discards this one.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { ...BASE.weather, condition: "blizzard" },
    source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-14",
              to: null, condition: "blizzard" }],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  const select = await screen.findByLabelText("Condition");
  expect([...select.querySelectorAll("option")].map((o) => o.textContent))
    .toContain("blizzard (authored)");
  expect(select).toHaveValue("blizzard");
});

test("saving an axis writes an override for the chosen duration", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(await screen.findByLabelText("Condition"), { target: { value: "light rain" } });
  fireEvent.change(screen.getByLabelText("For"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "the squall" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalledWith("c",
    // 4, not 5: the moment is the morning block, so "the rest of today" is
    // the four blocks left rather than a whole day starting here.
    expect.objectContaining({ location: "saltmarch-docks", start: "2026-06-14T09:00",
                              blocks: 4, condition: "light rain", note: "the squall" })));
});

test("leave to chance clears that axis rather than omitting it", async () => {
  // A user selecting it on an overridden axis means "stop overriding this";
  // omitting would let the setting appear to do nothing.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { ...BASE.weather, condition: "blizzard" },
    source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-14",
              to: null, condition: "blizzard" }],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(await screen.findByLabelText("Condition"), { target: { value: "__chance__" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.clearWeather).toHaveBeenCalledWith("c",
    expect.objectContaining({ axes: ["condition"], blocks: 1 })));
  expect(api.setWeatherOverride).not.toHaveBeenCalled();
});

test("Clear override appears only when something is authored", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  expect(screen.queryByRole("button", { name: "Clear override" })).not.toBeInTheDocument();
});

test("Clear override clears all three axes through the same operation", async () => {
  // Deleting only the winners would promote a shadowed span, so the sky would
  // change rather than return to generated.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE, source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-14",
              to: null, condition: "blizzard" }],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Clear override" }));
  await waitFor(() => expect(api.clearWeather).toHaveBeenCalledWith("c",
    expect.objectContaining({ location: "saltmarch-docks", blocks: 1 })));
  expect(vi.mocked(api.clearWeather).mock.calls[0][1].axes).toBeUndefined();
});

test("a suppressed axis offers Resume inheriting", async () => {
  // Without it the clear is a one-way door: the axis looks generated, so
  // Clear override does not appear, and setting a value only writes another
  // local exception rather than restoring the campaign-wide one.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-14",
              to: null, suppress: ["condition"] }],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Resume inheriting" }));
  await waitFor(() => expect(api.resumeWeather).toHaveBeenCalledWith("c",
    expect.objectContaining({ axes: ["condition"] })));
});

test("a save failure surfaces the reason and keeps the popover open", async () => {
  vi.mocked(api.setWeatherOverride).mockRejectedValue({ detail: "unparseable moment" });
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(await screen.findByLabelText("Condition"), { target: { value: "clear" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("unparseable moment")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});


test("the rest of today counts the blocks actually left, not a whole day", async () => {
  // A fixed 5 would span a whole day *starting here*: at 09:00 the current
  // block is morning and only four remain, so five would also override the
  // following day's dawn.
  vi.mocked(api.getSceneWeather).mockResolvedValue({ ...BASE, ordinal: 5 * 739781 + 1 } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(await screen.findByLabelText("Condition"), { target: { value: "clear" } });
  fireEvent.change(screen.getByLabelText("For"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalledWith("c",
    expect.objectContaining({ blocks: 4 })));
});

test("changing only the note re-writes the authored axis", async () => {
  // Every draft axis still equals the resolved value, so a value comparison
  // alone would issue no request and the edit would vanish silently.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { ...BASE.weather, condition: "blizzard" },
    source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-14",
              to: null, condition: "blizzard", note: "old note" }],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "the Wintertide storm" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalledWith("c",
    expect.objectContaining({ condition: "blizzard", note: "the Wintertide storm" })));
});

test("pinning a currently procedural value still writes an override", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  // Re-select the value already showing: the user means "pin this".
  fireEvent.change(await screen.findByLabelText("Condition"), { target: { value: "overcast" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalledWith("c",
    expect.objectContaining({ condition: "overcast" })));
});

test("an untouched popover saves nothing", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(api.setWeatherOverride).not.toHaveBeenCalled();
  expect(api.clearWeather).not.toHaveBeenCalled();
});

test("a metadata-only save does not localize an inherited axis", async () => {
  // Axes can come from different spans, and the PUT is location-scoped:
  // including an inherited _default wind would copy it into a new local
  // override and quietly stop campaign-wide wind changes reaching here.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { condition: "blizzard", temperature: "cold", wind: "gale" },
    source: { condition: "manual", temperature: "procedural", wind: "manual" },
    stack: [
      { id: "local", location: "saltmarch-docks", from: "2026-06-14", to: null,
        condition: "blizzard", note: "old note" },
      { id: "inherited", location: "_default", from: "2026-06-14", to: null, wind: "gale" },
    ],
  } as never);
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "the Wintertide storm" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalled());
  const body = vi.mocked(api.setWeatherOverride).mock.calls[0][1] as Record<string, unknown>;
  expect(body.condition).toBe("blizzard");
  expect(body.wind).toBeUndefined();  // still inherited from _default
});

test("a note-only edit replaces the span instead of layering a duplicate", async () => {
  // A create-shaped PUT would leave the original standing, so the old note
  // returns in the next block and any shorter duration lets it resume.
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { ...BASE.weather, condition: "blizzard" },
    source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-10", to: null,
              condition: "blizzard", note: "old note" }],
  } as never);
  vi.mocked(api.deleteWeatherOverride).mockResolvedValue({ ok: true });
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: "the Wintertide storm" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalled());
  expect(api.deleteWeatherOverride).toHaveBeenCalledWith("c", "saltmarch-docks", "ovr-1");
  const body = vi.mocked(api.setWeatherOverride).mock.calls[0][1] as Record<string, unknown>;
  // Its own bounds are kept: the duration select was not touched, so an
  // open-ended override stays open-ended rather than becoming one block.
  expect(body.start).toBe("2026-06-10");
  expect(body.end).toBeNull();
  expect(body.blocks).toBeUndefined();
  expect(body.note).toBe("the Wintertide storm");
});

test("changing the duration of an existing span re-bounds it", async () => {
  vi.mocked(api.getSceneWeather).mockResolvedValue({
    ...BASE,
    weather: { ...BASE.weather, condition: "blizzard" },
    source: { ...BASE.source, condition: "manual" },
    stack: [{ id: "ovr-1", location: "saltmarch-docks", from: "2026-06-10", to: null,
              condition: "blizzard" }],
  } as never);
  vi.mocked(api.deleteWeatherOverride).mockResolvedValue({ ok: true });
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(screen.getByLabelText("For"), { target: { value: "0" } });  // this block
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalled());
  const body = vi.mocked(api.setWeatherOverride).mock.calls[0][1] as Record<string, unknown>;
  expect(body.blocks).toBe(1);
  expect(api.deleteWeatherOverride).toHaveBeenCalled();
});

test("a brand-new override is created, not treated as a replacement", async () => {
  render(<WeatherWidget cid="c" sid="s" />);
  fireEvent.click(await screen.findByRole("button", { name: /Weather/ }));
  fireEvent.change(await screen.findByLabelText("Condition"), { target: { value: "light rain" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setWeatherOverride).toHaveBeenCalled());
  expect(api.deleteWeatherOverride).not.toHaveBeenCalled();
  const body = vi.mocked(api.setWeatherOverride).mock.calls[0][1] as Record<string, unknown>;
  expect(body.start).toBe("2026-06-14T09:00");
});
