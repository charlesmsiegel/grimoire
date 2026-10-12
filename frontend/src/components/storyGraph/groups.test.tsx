import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { StoryGraph } from "../../api/types";
import { graphFixture } from "../../testkit/storyGraph";
import { layout } from "./layout";
import { indexGraph, PRESETS, visible } from "./model";
import { NodeDetail } from "./NodeDetail";

function groups(): StoryGraph {
  const g = graphFixture();
  g.nodes.push(
    { id: "groups:salt-circle", kind: "group", label: "Salt Circle", group_type: "circle", secrecy: "gm-only" },
    { id: "groups:realm", kind: "group", label: "Salt Circle", group_type: "", secrecy: "public" },
  );
  g.edges.push(
    { id: "m", kind: "member_of", from: "characters:mara", to: "groups:salt-circle", source: "structural", relation: null, candidate_id: null },
    { id: "l", kind: "leads", from: "characters:mara", to: "groups:salt-circle", source: "structural", relation: null, candidate_id: null },
    { id: "w", kind: "member_of", from: "characters:winifred", to: "groups:realm", source: "structural", relation: null, candidate_id: null },
  );
  return g;
}

test("Groups default off and Cast exposes both roles with closed endpoints", () => {
  const g = groups(), ix = indexGraph(g);
  expect(visible(g, ix, "story", PRESETS.story.show, null).nodes.some((n) => n.kind === "group")).toBe(false);
  const cast = visible(g, ix, "cast", PRESETS.cast.show, null);
  expect(cast.nodes.filter((n) => n.kind === "group")).toHaveLength(2);
  expect(cast.edges.filter((e) => e.to === "groups:salt-circle").map((e) => e.kind)).toEqual(["member_of", "leads"]);
  const noActors = visible(g, ix, "cast", { ...PRESETS.cast.show, actors: false }, null);
  expect(noActors.edges.some((e) => e.kind === "member_of" || e.kind === "leads")).toBe(false);
  // Do not dump: with no visible member or leader, no group is drawn.
  expect(noActors.nodes.some((n) => n.kind === "group")).toBe(false);
});

test.each(["play", "calendar"] as const)("%s places groups below locations in name/ref order", (axis) => {
  const g = groups(), ix = indexGraph(g);
  const view = visible(g, ix, "cast", { ...PRESETS.cast.show, locations: true }, null);
  const lay = layout(g, ix, view, axis);
  const at = (id: string) => lay.nodes.find((p) => p.id === id)!;
  expect(lay.nodes).toHaveLength(view.nodes.length);
  expect(at("groups:realm").y).toBeGreaterThan(at("locations:saltmarch-harbour").y);
  expect(at("groups:salt-circle").y).toBeGreaterThan(at("groups:realm").y);
});

test("group detail exposes marked secrecy and independent projected roles", () => {
  const g = groups(), ix = indexGraph(g), onSelect = vi.fn();
  const node = g.nodes.find((n) => n.id === "groups:salt-circle")!;
  render(<MemoryRouter><NodeDetail cid="saltmarch" graph={g} ix={ix} node={node} arc={null} onSelect={onSelect} onArc={vi.fn()} /></MemoryRouter>);
  expect(screen.getByText("circle")).toBeInTheDocument();
  expect(screen.getByText("GM only")).toBeInTheDocument();
  for (const heading of ["Members", "Leader"]) {
    const section = screen.getByRole("heading", { name: heading }).parentElement!;
    fireEvent.click(within(section).getByRole("button", { name: "Mara" }));
  }
  expect(onSelect).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("link", { name: "Open group" })).toHaveAttribute("href", "/campaigns/saltmarch/world/groups/salt-circle");
});
